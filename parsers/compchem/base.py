"""
Base abstractions for comp-chem parsers.

A comp-chem parser is conceptually different from a lab-instrument parser:
  - The result is a *run record* (software, version, metrics, termination
    status) rather than a tabular dataset.
  - The "metric" output (docking score, ΔG, final energy) is a small set of
    scalars with mandatory units, not a time-series.
  - Termination status (normal vs crashed vs unconverged) is first-class —
    a comp-chem result that didn't converge is still scientifically
    meaningful but must not be silently treated as a passing run.

CompChemParsedResult maps directly onto the cc_run_metrics / cc_run schema
defined in services/api/compchem_models.py.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class RunKind(Enum):
    DOCKING = "docking"
    MOLECULAR_DYNAMICS = "molecular_dynamics"
    FREE_ENERGY = "free_energy"
    DFT = "dft"
    SEMI_EMPIRICAL = "semi_empirical"
    MMGBSA = "mmgbsa"
    MMPBSA = "mmpbsa"
    CONFORMER_GENERATION = "conformer_generation"
    PROPERTY_PREDICTION = "property_prediction"
    PHARMACOPHORE = "pharmacophore"
    OTHER = "other"


class TerminationStatus(Enum):
    NORMAL = "normal"            # Completed cleanly (e.g. "Normal termination" in Gaussian)
    UNCONVERGED = "unconverged"  # Job ran but did not converge
    CRASHED = "crashed"          # Job died mid-run (segfault, OOM, walltime)
    PARTIAL = "partial"          # Some frames/poses but truncated
    UNKNOWN = "unknown"           # Couldn't determine


@dataclass
class CompChemMetric:
    """A single extracted scalar — maps to one row in cc_run_metrics."""
    name: str                    # e.g. "docking_score", "final_energy", "delta_g_bind"
    value: float
    unit: str                    # MANDATORY — never empty. "kcal/mol", "Hartree", "Å", "dimensionless"
    confidence: Optional[float] = None
    stderr: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class CompChemParsedResult:
    """
    Standardised result from parsing a comp-chem output file.

    Maps onto the cc_runs / cc_run_metrics tables. The agent populates
    the additional run-context fields (campaign_id, molecule_id) from
    the .lablink.yaml before posting the manifest.
    """
    # --- Run identification ---
    software_name: str               # e.g. "GROMACS", "AutoDock Vina", "Gaussian"
    software_version: Optional[str]  # e.g. "2023.3", "1.2.5", "16.A.03"
    run_kind: RunKind = RunKind.OTHER

    # --- Reproducibility / provenance ---
    forcefield: Optional[str] = None      # e.g. "AMBER ff19SB", "CHARMM36m"
    method: Optional[str] = None          # DFT method or docking algorithm, e.g. "B3LYP", "Vina"
    basis_set: Optional[str] = None       # for QM jobs, e.g. "6-31G(d)", "def2-TZVP"
    cli_args: Optional[str] = None         # raw command line if recoverable from log header

    # --- Outcome ---
    termination_status: TerminationStatus = TerminationStatus.UNKNOWN
    error_message: Optional[str] = None

    # --- Timing ---
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    wall_time_s: Optional[float] = None

    # --- Extracted scalars (becomes cc_run_metrics rows) ---
    metrics: List[CompChemMetric] = field(default_factory=list)

    # --- Run-kind specific metadata (free-form JSONB on cc_runs.metadata) ---
    # MD: n_frames, n_atoms, timestep_ps, total_time_ps
    # Docking: n_poses, score_distribution
    # DFT: scf_cycles, convergence_threshold
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --- Source bookkeeping ---
    source_file: str = ""
    file_size_bytes: int = 0
    file_hash: Optional[str] = None  # SHA256 hex, populated by agent before upload
    parse_warnings: List[str] = field(default_factory=list)

    def to_manifest(self) -> Dict[str, Any]:
        """Serialize to JSON-friendly dict for the API manifest payload."""
        return {
            "software_name": self.software_name,
            "software_version": self.software_version,
            "run_kind": self.run_kind.value,
            "forcefield": self.forcefield,
            "method": self.method,
            "basis_set": self.basis_set,
            "cli_args": self.cli_args,
            "termination_status": self.termination_status.value,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "wall_time_s": self.wall_time_s,
            "metrics": [
                {
                    "name": m.name,
                    "value": m.value,
                    "unit": m.unit,
                    "confidence": m.confidence,
                    "stderr": m.stderr,
                    "metadata": m.metadata,
                }
                for m in self.metrics
            ],
            "metadata": self.metadata,
            "source_file": self.source_file,
            "file_size_bytes": self.file_size_bytes,
            "file_hash": self.file_hash,
            "parse_warnings": self.parse_warnings,
        }


class CompChemParser(ABC):
    """Abstract base for comp-chem parsers."""

    name: str = "base"
    software_name: str = "unknown"
    run_kinds: List[RunKind] = [RunKind.OTHER]

    @abstractmethod
    def detect(self, file_path: str) -> bool:
        """Fast detection — read header/magic bytes only, never parse the full file."""
        ...

    @abstractmethod
    def parse(self, file_path: str) -> CompChemParsedResult:
        """Parse the file and return a structured result. Never raises on
        malformed content — populate parse_warnings and termination_status
        instead so the raw bytes still get uploaded."""
        ...

    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """List of file extensions this parser claims, lowercase with dot."""
        ...

    # --- Helpers shared by concrete parsers ---

    @staticmethod
    def _read_head(file_path: str, max_bytes: int = 16384) -> str:
        """Read the first chunk of a text file with encoding fallback."""
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                with open(file_path, "r", encoding=enc, errors="replace") as f:
                    return f.read(max_bytes)
            except (UnicodeDecodeError, OSError):
                continue
        return ""

    @staticmethod
    def _read_tail(file_path: str, max_bytes: int = 16384) -> str:
        """Read the last chunk of a text file (where termination markers live)."""
        try:
            size = max(0, __import__("os").path.getsize(file_path))
            offset = max(0, size - max_bytes)
            with open(file_path, "rb") as f:
                f.seek(offset)
                raw = f.read()
            for enc in ("utf-8", "latin-1", "cp1252"):
                try:
                    return raw.decode(enc, errors="replace")
                except UnicodeDecodeError:
                    continue
        except OSError:
            pass
        return ""
