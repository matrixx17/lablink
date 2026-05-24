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
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    import yaml  # PyYAML; already in services/api requirements
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger("lablink-agent.campaign_context")

CONFIG_FILENAME = ".lablink.yaml"

RUN_TYPE_COMPONENTS = {"gromacs", "vina", "autodock", "gaussian", "orca", "openmm", "rdkit"}
RUN_KIND_BY_HINT = {
    "gromacs": "molecular_dynamics",
    "openmm": "molecular_dynamics",
    "vina": "docking",
    "autodock": "docking",
    "gaussian": "dft",
    "orca": "dft",
    "rdkit": "property_prediction",
}


@dataclass
class PathInference:
    campaign_name: Optional[str] = None
    molecule_label: Optional[str] = None
    run_type: Optional[str] = None
    run_index: Optional[int] = None

    @property
    def has_context(self) -> bool:
        return any((self.campaign_name, self.molecule_label, self.run_type, self.run_index is not None))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign_name": self.campaign_name,
            "molecule_label": self.molecule_label,
            "run_type": self.run_type,
            "run_index": self.run_index,
        }


def infer_from_path(file_path: str, watch_root: Optional[str] = None) -> PathInference:
    """
    Infer campaign/molecule/run hints from a directory layout.

    Rules are intentionally simple and auditable:
      campaigns/{name}, molecules/{smiles_or_label}, known software component,
      and run_12/run12.
    """
    abs_path = os.path.abspath(file_path)
    if watch_root:
        try:
            rel = os.path.relpath(abs_path, os.path.abspath(watch_root))
        except ValueError:
            rel = abs_path
    else:
        rel = abs_path
    parts = [p for p in rel.replace("\\", "/").split("/") if p and p != "."]
    lowered = [p.lower() for p in parts]

    inferred = PathInference()
    for idx, part in enumerate(lowered[:-1]):
        if part == "campaigns" and idx + 1 < len(parts):
            inferred.campaign_name = parts[idx + 1]
            break

    for idx, part in enumerate(lowered[:-1]):
        if part == "molecules" and idx + 1 < len(parts):
            inferred.molecule_label = parts[idx + 1]
            break

    for part in lowered:
        if part in RUN_TYPE_COMPONENTS:
            inferred.run_type = part
            break

    for part in lowered:
        match = re.fullmatch(r"run_?(\d+)", part)
        if match:
            inferred.run_index = int(match.group(1))
            break

    return inferred


@dataclass
class CampaignContext:
    """Per-file campaign context resolved from .lablink.yaml."""
    org_id: Optional[str] = None
    project: str = ""
    campaign: str = ""
    campaign_id: Optional[int] = None
    org_token: Optional[str] = None
    molecule_smiles: Optional[str] = None
    molecule_name: Optional[str] = None
    molecule_external_id: Optional[str] = None
    molecule_label: Optional[str] = None
    grid_id: Optional[str] = None
    grid_name: Optional[str] = None
    inferred_from_path: bool = False
    inferred_context: Dict[str, Any] = field(default_factory=dict)
    run_metadata: Dict[str, Any] = field(default_factory=dict)
    run_defaults: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None
    config_path: Optional[str] = None  # absolute path of the .lablink.yaml that produced this
    config_mtime: Optional[float] = None

    def merge_into_manifest(self, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """Apply context fields onto a manifest dict (non-destructive)."""
        if self.org_id:
            manifest.setdefault("org_id", self.org_id)
        if self.project:
            manifest.setdefault("project", self.project)
        if self.campaign:
            manifest.setdefault("campaign", self.campaign)
        if self.campaign_id is not None and not manifest.get("campaign_id"):
            manifest["campaign_id"] = self.campaign_id
        if self.molecule_smiles and not manifest.get("molecule_smiles"):
            manifest["molecule_smiles"] = self.molecule_smiles
        if self.molecule_name and not manifest.get("molecule_name"):
            manifest["molecule_name"] = self.molecule_name
        if self.molecule_external_id and not manifest.get("molecule_external_id"):
            manifest["molecule_external_id"] = self.molecule_external_id
        if self.molecule_label and not manifest.get("molecule_name"):
            manifest["molecule_name"] = self.molecule_label
        if self.grid_id and not manifest.get("grid_id"):
            manifest["grid_id"] = self.grid_id
        if self.grid_name and not manifest.get("grid_name"):
            manifest["grid_name"] = self.grid_name
        if self.notes and not manifest.get("notes"):
            manifest["notes"] = self.notes
        if self.run_metadata and not manifest.get("run_metadata"):
            manifest["run_metadata"] = self.run_metadata
        if self.config_path:
            manifest.setdefault("context_source", self.config_path)
        if self.inferred_from_path:
            manifest["inferred_from_path"] = True
            manifest["inferred_context"] = self.inferred_context
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

        # New minimal hosted config requires only campaign_id + org_token.
        # Legacy configs with org_id/project/campaign are still accepted so
        # existing local fixtures and self-hosted users do not break.
        org_id = raw.get("org_id")
        project = raw.get("project")
        campaign = raw.get("campaign")
        campaign_id = raw.get("campaign_id")
        org_token = raw.get("org_token")
        has_legacy_context = bool(org_id and project and campaign)
        missing = []
        if not has_legacy_context:
            if not campaign_id:
                missing.append("campaign_id")
            if not org_token:
                missing.append("org_token")
        if missing:
            logger.error(
                "%s missing required keys: %s. File will be uploaded with unknown context.",
                config_path,
                ", ".join(missing),
            )
            return None
        if has_legacy_context and (not campaign_id or not org_token):
            logger.warning(
                "%s uses legacy org_id/project/campaign context. New hosted configs only require campaign_id and org_token.",
                config_path,
            )

        run_defaults = raw.get("run") or {}
        if not isinstance(run_defaults, dict):
            logger.warning("%s: 'run' must be a mapping; ignoring", config_path)
            run_defaults = {}
        if raw.get("run_type") and not run_defaults.get("run_kind"):
            run_defaults["run_kind"] = raw.get("run_type")
        for top_level_default in ("software_name", "software_version", "forcefield", "compute_environment"):
            if raw.get(top_level_default) and not run_defaults.get(top_level_default):
                run_defaults[top_level_default] = raw.get(top_level_default)
        for optional_key in ("molecule_smiles", "run", "software_name", "software_version"):
            if optional_key not in raw:
                logger.warning("%s missing optional key %s; proceeding with parser/path inference", config_path, optional_key)

        return CampaignContext(
            org_id=str(org_id) if org_id is not None else None,
            project=str(project or ""),
            campaign=str(campaign or ""),
            campaign_id=int(campaign_id) if campaign_id is not None else None,
            org_token=str(org_token) if org_token else None,
            molecule_smiles=raw.get("molecule_smiles"),
            molecule_name=raw.get("molecule_name"),
            molecule_external_id=raw.get("molecule_external_id"),
            grid_id=str(raw["grid_id"]) if raw.get("grid_id") is not None else None,
            grid_name=raw.get("grid_name"),
            run_metadata=raw.get("run_metadata") or {},
            run_defaults={k: v for k, v in run_defaults.items() if v is not None},
            notes=raw.get("notes"),
            config_path=config_path,
            config_mtime=mtime,
        )
