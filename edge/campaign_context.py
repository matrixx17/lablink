"""
Campaign context discovery for the comp-chem edge agent.

The agent needs to know which campaign / molecule / run a file belongs to
*before* it can post a meaningful manifest. For MVP we implement the
config-file approach only — it is the least magical, the most auditable,
and the only one that survives a directory move without breaking.

Convention: a scientist drops a `.lablink.yaml` into a project directory.
The agent discovers the closest ancestor `.lablink.yaml` for every file
event and uses its contents as campaign context.

Example .lablink.yaml:

    org_id: acme-pharma
    project: EGFR-program-2026
    campaign: lead_opt_round_3
    # Optional: a molecule SMILES applied to every file under this dir.
    # For multi-molecule runs, the parser fills molecule_smiles per-result
    # instead and this is left out.
    molecule_smiles: "Cc1ccc(cc1)C(=O)Nc2ccncc2"
    molecule_name: "LL-042"
    # Optional default run metadata
    run:
      run_kind: docking          # docking | molecular_dynamics | dft | ...
      software_name: AutoDock Vina
      forcefield: AMBER ff19SB
      compute_environment: hpc_slurm
      cluster_name: discovery
    # Optional: override file routing (skip the watch's default upload)
    notes: "Round 3 — narrow series around mol-019 scaffold"

Discovery rules:
  - Walk up from the file's directory until a .lablink.yaml is found,
    stopping at the watch root.
  - Cache results per directory (campaign config rarely changes during
    a run, but the YAML is re-read if its mtime changes).
  - If no .lablink.yaml is found, the file is moved to .unclassified/
    rather than uploaded — context is mandatory for comp-chem. This is
    deliberate: silently uploading uncontextualised files is exactly
    the failure mode we built campaigns to prevent.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    import yaml  # PyYAML; already in services/api requirements
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger("lablink-agent.campaign_context")

CONFIG_FILENAME = ".lablink.yaml"


@dataclass
class CampaignContext:
    """Per-file campaign context resolved from .lablink.yaml."""
    org_id: str
    project: str
    campaign: str
    campaign_id: Optional[int] = None
    molecule_smiles: Optional[str] = None
    molecule_name: Optional[str] = None
    molecule_external_id: Optional[str] = None
    run_metadata: Dict[str, Any] = field(default_factory=dict)
    run_defaults: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None
    config_path: Optional[str] = None  # absolute path of the .lablink.yaml that produced this
    config_mtime: Optional[float] = None

    def merge_into_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Apply context fields onto a manifest dict (non-destructive)."""
        manifest.setdefault("org_id", self.org_id)
        manifest.setdefault("project", self.project)
        manifest.setdefault("campaign", self.campaign)
        if self.campaign_id is not None and not manifest.get("campaign_id"):
            manifest["campaign_id"] = self.campaign_id
        if self.molecule_smiles and not manifest.get("molecule_smiles"):
            manifest["molecule_smiles"] = self.molecule_smiles
        if self.molecule_name and not manifest.get("molecule_name"):
            manifest["molecule_name"] = self.molecule_name
        if self.molecule_external_id and not manifest.get("molecule_external_id"):
            manifest["molecule_external_id"] = self.molecule_external_id
        if self.notes and not manifest.get("notes"):
            manifest["notes"] = self.notes
        if self.run_metadata and not manifest.get("run_metadata"):
            manifest["run_metadata"] = self.run_metadata
        if self.config_path:
            manifest.setdefault("context_source", self.config_path)
        # Run defaults: fill in software_name, forcefield, compute_environment, etc.
        for key, val in self.run_defaults.items():
            if val is not None and not manifest.get(key):
                manifest[key] = val
        return manifest


class CampaignContextResolver:
    """
    Walks up the directory tree from a file path looking for .lablink.yaml,
    caches results, and re-reads if the config's mtime changes.

    Thread-safe — the watchdog observer may dispatch events from multiple
    threads.
    """

    def __init__(self, watch_root: str):
        self.watch_root = os.path.abspath(watch_root)
        self._cache: Dict[str, CampaignContext] = {}
        self._lock = threading.Lock()

    def resolve(self, file_path: str) -> Optional[CampaignContext]:
        """Return the closest-ancestor CampaignContext, or None if none found."""
        config_path = self._find_config(file_path)
        if not config_path:
            return None
        return self._load_cached(config_path)

    # ------------------------------------------------------------------

    def _find_config(self, file_path: str) -> Optional[str]:
        """Walk up from file_path's directory until we find .lablink.yaml
        or pass the watch root."""
        abs_file = os.path.abspath(file_path)
        if not abs_file.startswith(self.watch_root):
            # File outside the watch root — refuse to associate
            return None

        d = os.path.dirname(abs_file)
        while True:
            candidate = os.path.join(d, CONFIG_FILENAME)
            if os.path.isfile(candidate):
                return candidate
            if os.path.abspath(d) == self.watch_root:
                return None
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent

    def _load_cached(self, config_path: str) -> Optional[CampaignContext]:
        with self._lock:
            try:
                mtime = os.path.getmtime(config_path)
            except OSError:
                self._cache.pop(config_path, None)
                return None

            cached = self._cache.get(config_path)
            if cached and cached.config_mtime == mtime:
                return cached

            ctx = self._load(config_path, mtime)
            if ctx:
                self._cache[config_path] = ctx
            return ctx

    @staticmethod
    def _load(config_path: str, mtime: float) -> Optional[CampaignContext]:
        if yaml is None:
            logger.error(
                "PyYAML is not installed; cannot parse %s. "
                "Install with: pip install PyYAML",
                config_path,
            )
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            logger.error("Failed to read %s: %s", config_path, e)
            return None

        if not isinstance(raw, dict):
            logger.error("%s: top-level must be a mapping, got %s", config_path, type(raw).__name__)
            return None

        # Required fields. campaign_id is accepted as an explicit DB target,
        # but project/campaign names remain useful for human-readable context
        # and for first-run bootstrap when no ID exists yet.
        org_id = raw.get("org_id")
        project = raw.get("project")
        campaign = raw.get("campaign")
        missing = [k for k, v in (
            ("org_id", org_id),
        ) if not v]
        if not raw.get("campaign_id"):
            missing.extend([k for k, v in (("project", project), ("campaign", campaign)) if not v])
        if missing:
            logger.error(
                "%s missing required keys: %s. File will be uploaded with "
                "unknown context.",
                config_path,
                ", ".join(missing),
            )
            return None

        run_defaults = raw.get("run") or {}
        if not isinstance(run_defaults, dict):
            logger.warning("%s: 'run' must be a mapping; ignoring", config_path)
            run_defaults = {}

        return CampaignContext(
            org_id=str(org_id),
            project=str(project or ""),
            campaign=str(campaign or ""),
            campaign_id=int(raw["campaign_id"]) if raw.get("campaign_id") is not None else None,
            molecule_smiles=raw.get("molecule_smiles"),
            molecule_name=raw.get("molecule_name"),
            molecule_external_id=raw.get("molecule_external_id"),
            run_metadata=raw.get("run_metadata") or {},
            run_defaults={k: v for k, v in run_defaults.items() if v is not None},
            notes=raw.get("notes"),
            config_path=config_path,
            config_mtime=mtime,
        )
