"""
DFT / semi-empirical output parsers — Gaussian and ORCA.

cclib (https://cclib.github.io/) does the heavy lifting for both: it
parses log/output files from a dozen QM programs into a uniform data
structure. We use cclib when available and fall back to lightweight
regex scanning if the import fails — so the agent still functions in
minimal-dependency environments and still uploads raw bytes.
"""

import os
import re
from datetime import datetime
from typing import List

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)

try:
    import cclib  # type: ignore
    _HAS_CCLIB = True
except ImportError:
    _HAS_CCLIB = False


# Hartree → kcal/mol (used to normalise final energies for cross-job comparison
# only when the source unit is explicit; cclib already returns energies in eV)
_EV_TO_KCAL_MOL = 23.0605


def _parse_with_cclib(file_path: str) -> dict:
    """Run cclib on a log file. Returns flattened dict, or {} if it fails."""
    if not _HAS_CCLIB:
        return {}
    try:
        data = cclib.io.ccread(file_path)
        if data is None:
            return {}
        out: dict = {}
        # Final SCF energy (eV) — cclib normalises to eV
        if hasattr(data, "scfenergies") and len(data.scfenergies) > 0:
            out["final_energy_eV"] = float(data.scfenergies[-1])
            out["scf_cycles"] = int(len(data.scfenergies))
        if hasattr(data, "metadata") and isinstance(data.metadata, dict):
            md = data.metadata
            for key in ("methods", "functional", "basis_set", "package_version",
                        "package", "wall_time", "cpu_time"):
                val = md.get(key)
                if val is not None:
                    out[key] = val
        if hasattr(data, "natom"):
            out["n_atoms"] = int(data.natom)
        if hasattr(data, "charge"):
            out["charge"] = int(data.charge)
        if hasattr(data, "mult"):
            out["multiplicity"] = int(data.mult)
        if hasattr(data, "optdone"):
            out["geometry_converged"] = bool(data.optdone)
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Gaussian
# ---------------------------------------------------------------------------

_GAUSSIAN_HEADER_PATTERNS = [
    re.compile(r"\bGaussian\s+\d+\b", re.IGNORECASE),
    re.compile(r"Entering Gaussian System"),
    re.compile(r"^\s*\*+\s*Gaussian\b", re.MULTILINE),
]

_GAUSSIAN_VERSION_RE = re.compile(r"Gaussian\s+(\d+),\s+Revision\s+([A-Za-z0-9\.]+)")
_GAUSSIAN_METHOD_RE = re.compile(r"#\s*([A-Za-z0-9\-\(\),/\s]+?)(?:\s*$|\n)", re.MULTILINE)
_GAUSSIAN_NORMAL_RE = re.compile(r"Normal termination of Gaussian", re.IGNORECASE)
_GAUSSIAN_ERROR_RE = re.compile(r"Error termination", re.IGNORECASE)


class GaussianParser(CompChemParser):
    name = "gaussian"
    software_name = "Gaussian"
    run_kinds = [RunKind.DFT, RunKind.SEMI_EMPIRICAL]

    def supported_extensions(self) -> List[str]:
        return [".log", ".out"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions():
            return False
        head = self._read_head(file_path, max_bytes=8192)
        return any(p.search(head) for p in _GAUSSIAN_HEADER_PATTERNS)

    def parse(self, file_path: str) -> CompChemParsedResult:
        head = self._read_head(file_path, max_bytes=32768)
        tail = self._read_tail(file_path, max_bytes=16384)

        result = CompChemParsedResult(
            software_name="Gaussian",
            software_version=None,
            run_kind=RunKind.DFT,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        # Version
        m = _GAUSSIAN_VERSION_RE.search(head)
        if m:
            result.software_version = f"{m.group(1)}.{m.group(2)}"

        # Method line (first # directive)
        m = _GAUSSIAN_METHOD_RE.search(head)
        if m:
            route = m.group(1).strip()
            result.cli_args = route
            # Heuristic: first token is method, last slash-separated token is basis
            tokens = route.split()
            if tokens:
                method_token = tokens[0]
                result.method = method_token
                if "/" in method_token:
                    method, basis = method_token.rsplit("/", 1)
                    result.method = method
                    result.basis_set = basis

        # Termination status — look at tail
        if _GAUSSIAN_NORMAL_RE.search(tail):
            result.termination_status = TerminationStatus.NORMAL
        elif _GAUSSIAN_ERROR_RE.search(tail):
            result.termination_status = TerminationStatus.CRASHED
            err_line = next(
                (line.strip() for line in tail.splitlines() if "Error termination" in line),
                None,
            )
            result.error_message = err_line

        # Extract metrics via cclib if available
        cc_data = _parse_with_cclib(file_path)
        if cc_data:
            if "final_energy_eV" in cc_data:
                ev = cc_data["final_energy_eV"]
                result.metrics.append(CompChemMetric(
                    name="final_energy",
                    value=ev,
                    unit="eV",
                ))
                result.metrics.append(CompChemMetric(
                    name="final_energy",
                    value=ev * _EV_TO_KCAL_MOL,
                    unit="kcal/mol",
                ))
            if cc_data.get("functional") and not result.method:
                result.method = str(cc_data["functional"])
            if cc_data.get("basis_set") and not result.basis_set:
                result.basis_set = str(cc_data["basis_set"])
            if cc_data.get("package_version") and not result.software_version:
                result.software_version = str(cc_data["package_version"])
            if "geometry_converged" in cc_data:
                if not cc_data["geometry_converged"] and \
                        result.termination_status == TerminationStatus.UNKNOWN:
                    result.termination_status = TerminationStatus.UNCONVERGED
            for key in ("scf_cycles", "n_atoms", "charge", "multiplicity"):
                if key in cc_data:
                    result.metadata[key] = cc_data[key]
        else:
            result.parse_warnings.append(
                "cclib not available — only basic metadata extracted from Gaussian log"
            )

        return result


# ---------------------------------------------------------------------------
# ORCA
# ---------------------------------------------------------------------------

_ORCA_HEADER_PATTERNS = [
    re.compile(r"\* O\s*R\s*C\s*A \*", re.IGNORECASE),
    re.compile(r"Program Version\s+\d+\.\d+", re.IGNORECASE),
]

_ORCA_VERSION_RE = re.compile(r"Program Version\s+(\d+\.\d+\.\d+|\d+\.\d+)", re.IGNORECASE)
_ORCA_METHOD_RE = re.compile(r"^\s*!\s*(.+?)$", re.MULTILINE)
_ORCA_NORMAL_RE = re.compile(r"ORCA TERMINATED NORMALLY", re.IGNORECASE)
_ORCA_FINAL_E_RE = re.compile(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)")


class ORCAParser(CompChemParser):
    name = "orca"
    software_name = "ORCA"
    run_kinds = [RunKind.DFT]

    def supported_extensions(self) -> List[str]:
        return [".log", ".out"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions():
            return False
        head = self._read_head(file_path, max_bytes=8192)
        # ORCA banner is distinctive — must not match Gaussian
        if any(p.search(head) for p in _ORCA_HEADER_PATTERNS):
            return "Gaussian" not in head[:2048]
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        head = self._read_head(file_path, max_bytes=32768)
        tail = self._read_tail(file_path, max_bytes=16384)

        result = CompChemParsedResult(
            software_name="ORCA",
            software_version=None,
            run_kind=RunKind.DFT,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        m = _ORCA_VERSION_RE.search(head)
        if m:
            result.software_version = m.group(1)

        # ORCA "!" lines specify the method
        m = _ORCA_METHOD_RE.search(head)
        if m:
            directive = m.group(1).strip()
            result.cli_args = directive
            tokens = directive.split()
            if tokens:
                result.method = tokens[0]
                # Basis is usually a separate token like "def2-TZVP"
                for tok in tokens[1:]:
                    if "/" in tok or tok.lower().startswith(("def2", "cc-", "aug-")):
                        result.basis_set = tok
                        break

        if _ORCA_NORMAL_RE.search(tail):
            result.termination_status = TerminationStatus.NORMAL
        elif "ABORTING THE RUN" in tail or "aborting the run" in tail:
            result.termination_status = TerminationStatus.CRASHED

        # Final energy — ORCA prints in Hartree
        m = _ORCA_FINAL_E_RE.search(tail) or _ORCA_FINAL_E_RE.search(head)
        if m:
            hartree = float(m.group(1))
            result.metrics.append(CompChemMetric(
                name="final_energy",
                value=hartree,
                unit="Hartree",
            ))
            result.metrics.append(CompChemMetric(
                name="final_energy",
                value=hartree * 627.5094740631,  # Hartree -> kcal/mol
                unit="kcal/mol",
            ))

        cc_data = _parse_with_cclib(file_path)
        if cc_data:
            for key in ("scf_cycles", "n_atoms", "charge", "multiplicity"):
                if key in cc_data:
                    result.metadata[key] = cc_data[key]
            if "geometry_converged" in cc_data and not cc_data["geometry_converged"] \
                    and result.termination_status == TerminationStatus.UNKNOWN:
                result.termination_status = TerminationStatus.UNCONVERGED

        return result
