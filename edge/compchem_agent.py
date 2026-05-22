"""
LabLink AI — Comp-Chem Edge Agent

Watches directories for computational chemistry output (GROMACS, OpenMM,
Vina/Gnina, Glide, Gaussian/ORCA, RDKit property tables) and posts a
campaign-aware manifest to the central API.

Key differences from the bioprocess `agent.py`:

  - Campaign context is mandatory. The agent discovers the closest
    `.lablink.yaml` ancestor for every new file. Files without context
    are moved to `.unclassified/` rather than uploaded — silently
    ingesting uncontextualised files is exactly the failure mode the
    campaign-centric model was designed to prevent.

  - File hash (SHA256) is computed before upload as the tamper-evidence
    anchor. The hash is sent in the manifest and (Layer 2) recorded on
    the corresponding cc_run_inputs / cc_run_outputs row.

  - Manifest payload targets the comp-chem API surface
    (POST /api/v1/cc/events — built in Layer 2). For now we POST to a
    placeholder endpoint and log the full payload so the integration is
    testable end-to-end without the API routes existing yet.

  - File ↔ run classification: the parser's run_kind + filename suffixes
    decide whether the file is a RunInput (e.g. .tpr, .mdp), a RunOutput
    (.xtc, .log, .pdbqt), or a RunMetric source (StateDataReporter CSV,
    Gaussian .log). The agent encodes its best guess in the manifest;
    the API can override.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Allow importing parsers/ from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers.compchem import (  # noqa: E402
    CompChemParsedResult,
    RunKind,
    TerminationStatus,
    detect_compchem_format,
    parse_compchem_file,
)
from edge.campaign_context import (  # noqa: E402
    CampaignContext,
    CampaignContextResolver,
    CONFIG_FILENAME,
)

# QC runs server-side normally, but we also run it client-side so a failed
# QC can be logged at the watch source — useful when an HPC submits a
# malformed run and the scientist is debugging on their laptop. We import
# lazily because services/api isn't always on the agent's sys.path.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "services", "api"))
try:
    from compchem_qc import compchem_qc_summary  # type: ignore  # noqa: E402
    _AGENT_QC_AVAILABLE = True
except ImportError:
    _AGENT_QC_AVAILABLE = False
    compchem_qc_summary = None  # type: ignore


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lablink-compchem-agent")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# File extensions the comp-chem agent will consider. Wider than the bioprocess
# agent — covers binary trajectories, QM logs, docking outputs, property tables.
DEFAULT_EXTENSIONS = {
    # Logs / text outputs
    ".log", ".out", ".txt",
    # Trajectories and structures
    ".xtc", ".trr", ".dcd", ".gro", ".pdb", ".pdbqt", ".tpr", ".edr",
    ".h5", ".hdf5",
    # Tables / structure-data files
    ".csv", ".tsv", ".sdf", ".mol2",
    # Schrödinger
    ".mae", ".maegz",
}


class AgentConfig:
    def __init__(
        self,
        watch_folder: str,
        api_base: str = "http://localhost:8000",
        org_id_override: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        request_timeout: int = 60,
        max_failures: int = 5,
        dry_run: bool = False,
    ):
        self.watch_folder = os.path.abspath(watch_folder)
        self.api_base = api_base.rstrip("/")
        self.org_id_override = org_id_override
        self.api_key = api_key or os.environ.get("LABLINK_API_KEY")
        self.max_retries = max_retries
        self.request_timeout = request_timeout
        self.max_failures = max_failures
        self.dry_run = dry_run

        self.unclassified_folder = os.path.join(self.watch_folder, ".unclassified")
        self.failed_folder = os.path.join(self.watch_folder, ".failed")
        self.processed_folder = os.path.join(self.watch_folder, ".processed")


# ---------------------------------------------------------------------------
# HTTP session with retry
# ---------------------------------------------------------------------------

def create_http_session(config: AgentConfig) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=config.max_retries,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if config.api_key:
        session.headers["X-API-Key"] = config.api_key
    return session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """SHA256 of file contents, streamed in chunks (no memory blow-up on
    multi-GB trajectories)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def classify_artifact_role(parsed: CompChemParsedResult, filename: str) -> str:
    """
    Best-effort guess of whether this file is an input, output, or metric source.

    Returns one of: "input", "output", "metric_source".
    The API can override based on the campaign's expectations.
    """
    ext = os.path.splitext(filename)[1].lower()

    # GROMACS run inputs
    if ext in (".tpr", ".mdp", ".top", ".itp"):
        return "input"

    # Coordinate / topology snapshots can be either, but treated as inputs by
    # default unless they have an OpenMM stamp (output PDB)
    if ext == ".pdb":
        if parsed.software_name == "OpenMM":
            return "output"
        return "input"

    # Trajectories are outputs
    if ext in (".xtc", ".trr", ".dcd", ".h5", ".hdf5", ".edr"):
        return "output"

    # Docking poses
    if ext in (".pdbqt", ".sdf", ".mol2", ".mae", ".maegz"):
        return "output" if parsed.run_kind == RunKind.DOCKING else "input"

    # Logs and StateDataReporter CSVs are both metric sources
    if ext in (".log", ".out", ".csv", ".tsv"):
        return "metric_source"

    return "output"


def _coerce_for_json(obj: Any) -> Any:
    """Make dataclasses, datetimes, and enums JSON-serializable."""
    if is_dataclass(obj):
        return _coerce_for_json(asdict(obj))
    if isinstance(obj, dict):
        return {k: _coerce_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce_for_json(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    # Enums
    if hasattr(obj, "value") and obj.__class__.__module__ != "builtins":
        try:
            return obj.value
        except AttributeError:
            pass
    return obj


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------

class CompChemFileProcessor:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.session = create_http_session(config)
        self.resolver = CampaignContextResolver(config.watch_folder)
        self._failure_counts: Dict[str, int] = {}

        os.makedirs(config.watch_folder, exist_ok=True)
        os.makedirs(config.unclassified_folder, exist_ok=True)
        os.makedirs(config.failed_folder, exist_ok=True)

    def process_file(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        filename = os.path.basename(abs_path)

        # Always-skipped paths
        if any(seg in abs_path.replace("\\", "/").split("/")
               for seg in (".failed", ".unclassified", ".processed")):
            return False
        if filename == CONFIG_FILENAME:
            return False  # The yaml itself never gets uploaded

        attempts = self._failure_counts.get(abs_path, 0) + 1
        self._failure_counts[abs_path] = attempts

        try:
            # 1) Resolve campaign context. Mandatory.
            context = self.resolver.resolve(abs_path)
            if context is None:
                logger.warning(
                    "No %s found for %s — moving to .unclassified/",
                    CONFIG_FILENAME, filename,
                )
                self._move_to_unclassified(abs_path)
                return False

            # 2) Classify file
            parser_name = detect_compchem_format(abs_path)
            if parser_name:
                logger.info("[%s] %s -> parser=%s",
                            context.campaign, filename, parser_name)
            else:
                logger.info("[%s] %s -> parser=unknown (will upload raw)",
                            context.campaign, filename)

            # 3) Parse (extract metadata) — parser never raises; bad files
            # come back with termination_status=UNKNOWN
            parsed = parse_compchem_file(abs_path)

            # 4) Hash before upload — tamper-evidence anchor
            try:
                parsed.file_hash = sha256_file(abs_path)
            except OSError as e:
                logger.error("Failed to hash %s: %s", filename, e)
                self._handle_failure(abs_path, f"hash failed: {e}", attempts)
                return False

            # 4b) Run client-side QC (advisory). Server is authoritative;
            # this gives the scientist a fast local signal when something
            # is obviously wrong with their just-submitted run.
            qc_result: Optional[Dict[str, Any]] = None
            if _AGENT_QC_AVAILABLE:
                try:
                    qc_result = compchem_qc_summary(
                        parsed=parsed.to_manifest(),
                        molecule_smiles=context.molecule_smiles,
                    )
                    status = qc_result.get("overall_status", "unknown")
                    if status == "fail":
                        logger.warning(
                            "[%s] %s QC=FAIL — %s",
                            context.campaign, filename,
                            qc_result.get("summary", "(no summary)"),
                        )
                    elif status == "warn":
                        logger.info(
                            "[%s] %s QC=WARN — %s",
                            context.campaign, filename,
                            qc_result.get("summary", "(no summary)"),
                        )
                except Exception as e:
                    logger.warning("QC raised on %s: %s — continuing", filename, e)

            # 5) Upload raw bytes (presigned URL flow, same as existing agent)
            s3_key: Optional[str] = None
            if not self.config.dry_run:
                presign = self._get_presigned_url(filename, context.org_id)
                if presign is None:
                    self._handle_failure(abs_path, "presign failed", attempts)
                    return False
                url, fields, s3_key = presign
                if not self._upload_to_s3(abs_path, url, fields):
                    self._handle_failure(abs_path, "S3 upload failed", attempts)
                    return False
                logger.info("Uploaded %s -> %s", filename, s3_key)

            # 6) Build comp-chem manifest and POST
            manifest = self._build_manifest(
                parsed, context, s3_key, filename, parser_name, qc_result,
            )
            if not self.config.dry_run:
                if not self._post_manifest(manifest):
                    self._handle_failure(abs_path, "manifest post failed", attempts)
                    return False
            else:
                logger.info(
                    "DRY-RUN manifest for %s:\n%s",
                    filename,
                    json.dumps(manifest, indent=2),
                )

            self._failure_counts.pop(abs_path, None)
            logger.info("[%s] processed: %s", context.campaign, filename)
            return True

        except Exception as e:
            logger.exception("Unexpected error processing %s: %s", filename, e)
            self._handle_failure(abs_path, str(e), attempts)
            return False

    # ------------------------------------------------------------------

    def _build_manifest(
        self,
        parsed: CompChemParsedResult,
        context: CampaignContext,
        s3_key: Optional[str],
        filename: str,
        parser_name: Optional[str],
        qc_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        manifest: Dict[str, Any] = {
            # Where this file lives in object storage
            "s3_key": s3_key,
            "filename": filename,
            "file_size_bytes": parsed.file_size_bytes,
            "file_hash": parsed.file_hash,
            # Agent classification
            "parser_name": parser_name,
            "artifact_role": classify_artifact_role(parsed, filename),
            # Full parsed result (software, version, metrics, termination, etc.)
            "parsed": parsed.to_manifest(),
            # Client-side QC (advisory — server reruns authoritatively)
            "client_qc": qc_result,
            # Bookkeeping
            "agent_timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Layer in campaign context (org_id, project, campaign, molecule, run defaults)
        context.merge_into_manifest(manifest)

        # Org override flag — wins over yaml when set
        if self.config.org_id_override:
            manifest["org_id"] = self.config.org_id_override

        return _coerce_for_json(manifest)

    # ------------------------------------------------------------------

    def _get_presigned_url(
        self, filename: str, org_id: str,
    ) -> Optional[Tuple[str, dict, str]]:
        try:
            resp = self.session.post(
                f"{self.config.api_base}/api/v1/presign",
                json={"filename": filename, "org_id": org_id},
                timeout=self.config.request_timeout,
            )
            if resp.status_code != 200:
                logger.error("Presign failed: %s %s", resp.status_code, resp.text[:500])
                return None
            data = resp.json()
            return data["url"], data["fields"]["fields"], data["fields"]["key"]
        except Exception as e:
            logger.error("Presign error: %s", e)
            return None

    def _upload_to_s3(self, path: str, url: str, fields: dict) -> bool:
        try:
            with open(path, "rb") as f:
                resp = self.session.post(
                    url, data=fields, files={"file": f},
                    timeout=self.config.request_timeout * 4,
                )
            if resp.status_code not in (200, 201, 204):
                logger.error("Upload failed: %s %s", resp.status_code, resp.text[:500])
                return False
            return True
        except Exception as e:
            logger.error("Upload error: %s", e)
            return False

    def _post_manifest(self, manifest: Dict[str, Any]) -> bool:
        # Comp-chem manifest endpoint — wired up in Layer 2. For Layer 1
        # we expect HTTP 404 / 501 and treat it as "OK, endpoint not yet
        # implemented" so the agent can be exercised end-to-end now.
        try:
            resp = self.session.post(
                f"{self.config.api_base}/api/v1/cc/events",
                json=manifest,
                timeout=self.config.request_timeout,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code in (404, 501):
                logger.warning(
                    "Comp-chem events endpoint not yet implemented (%s). "
                    "Manifest accepted by Layer 1 logic; will succeed once "
                    "Layer 2 routes are deployed.",
                    resp.status_code,
                )
                return True
            logger.error("Manifest post failed: %s %s", resp.status_code, resp.text[:500])
            return False
        except Exception as e:
            logger.error("Manifest post error: %s", e)
            return False

    # ------------------------------------------------------------------

    def _move_to_unclassified(self, path: str) -> None:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = os.path.join(
                self.config.unclassified_folder,
                f"{timestamp}_{os.path.basename(path)}",
            )
            shutil.move(path, dest)
            meta = {
                "original_path": path,
                "reason": f"No {CONFIG_FILENAME} found in ancestor directories",
                "moved_at": datetime.now().isoformat(),
            }
            with open(f"{dest}.meta.json", "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            logger.error("Failed to move %s to .unclassified/: %s", path, e)

    def _handle_failure(self, path: str, error: str, attempts: int) -> None:
        filename = os.path.basename(path)
        if attempts >= self.config.max_failures:
            logger.error(
                "Max failures (%d) reached for %s — moving to dead letter queue",
                self.config.max_failures, filename,
            )
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = os.path.join(self.config.failed_folder, f"{timestamp}_{filename}")
                shutil.move(path, dest)
                meta = {
                    "original_path": path,
                    "failed_at": datetime.now().isoformat(),
                    "error": error,
                    "attempts": attempts,
                }
                with open(f"{dest}.meta.json", "w") as f:
                    json.dump(meta, f, indent=2)
            except Exception as e:
                logger.error("Failed to move %s to dead letter queue: %s", filename, e)
            self._failure_counts.pop(path, None)
        else:
            logger.warning(
                "Processing failed for %s (attempt %d/%d): %s",
                filename, attempts, self.config.max_failures, error,
            )


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------

class CompChemHandler(FileSystemEventHandler):
    def __init__(self, processor: CompChemFileProcessor, extensions: set):
        super().__init__()
        self.processor = processor
        self.extensions = {e.lower() if e.startswith(".") else f".{e.lower()}"
                           for e in extensions}

    def on_created(self, event):
        if event.is_directory:
            return
        path = event.src_path
        # Skip internal folders
        parts = path.replace("\\", "/").split("/")
        if any(p in (".failed", ".unclassified", ".processed") for p in parts):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in self.extensions and os.path.basename(path) != CONFIG_FILENAME:
            return
        # Short settle delay so the OS finishes flushing the file
        time.sleep(0.5)
        self.processor.process_file(path)

    def on_moved(self, event):
        # Renames count as creations for our purposes
        if event.is_directory:
            return
        self.on_created(type("E", (), {"src_path": event.dest_path, "is_directory": False})())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="LabLink AI — Comp-Chem Edge Agent. Watches directories "
                    "for GROMACS / OpenMM / Vina / Glide / DFT / RDKit outputs "
                    "and uploads them to a campaign-aware central API.",
    )
    parser.add_argument("--watch", required=True,
                        help="Root directory to watch (recursive).")
    parser.add_argument("--api", default="http://localhost:8000",
                        help="API base URL")
    parser.add_argument("--org",
                        help="Override org_id from .lablink.yaml (use sparingly)")
    parser.add_argument("--api-key", help="X-API-Key for authenticated mode "
                                          "(falls back to LABLINK_API_KEY env)")
    parser.add_argument("--extensions",
                        help="Comma-separated extra extensions to watch. "
                             "Defaults cover all known comp-chem outputs.")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-failures", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse + hash + log manifest, but skip the actual "
                             "upload. Useful for testing campaign context "
                             "wiring before the API is reachable.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)

    config = AgentConfig(
        watch_folder=args.watch,
        api_base=args.api,
        org_id_override=args.org,
        api_key=args.api_key,
        max_retries=args.max_retries,
        max_failures=args.max_failures,
        dry_run=args.dry_run,
    )

    extensions = set(DEFAULT_EXTENSIONS)
    if args.extensions:
        for e in args.extensions.split(","):
            e = e.strip()
            if e:
                extensions.add(e if e.startswith(".") else f".{e}")

    processor = CompChemFileProcessor(config)
    handler = CompChemHandler(processor, extensions)

    observer = Observer()
    observer.schedule(handler, config.watch_folder, recursive=True)
    observer.start()

    logger.info("=" * 60)
    logger.info("LabLink AI — Comp-Chem Edge Agent")
    logger.info("=" * 60)
    logger.info("Watch:        %s (recursive)", config.watch_folder)
    logger.info("API:          %s", config.api_base)
    logger.info("Auth:         %s",
                "X-API-Key set" if config.api_key else "anonymous-dev mode")
    logger.info("Extensions:   %s", ", ".join(sorted(extensions)))
    logger.info("Dry-run:      %s", config.dry_run)
    logger.info("Context file: %s (closest ancestor)", CONFIG_FILENAME)
    logger.info("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down…")
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
