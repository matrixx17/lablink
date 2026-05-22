"""
AutoDock Vina and Gnina docking output parsers.

Vina output formats:
  - .pdbqt              Output pose file with REMARK VINA RESULT lines containing
                        affinity (kcal/mol) and RMSD.
  - .log / .out         Text log with a score table per mode.
  - .sdf                Vina occasionally writes SDF with score in tags.

Gnina is a Vina fork with CNN scoring. Output looks like Vina's log but with
extra columns: affinity (kcal/mol), CNNscore, CNNaffinity (pK_d).
"""

import os
import re
from typing import List

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)


_VINA_REMARK_RE = re.compile(
    r"REMARK\s+VINA\s+RESULT:\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)"
)
# Vina log score row: "   1     -9.2      0.000      0.000"
_VINA_LOG_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$",
    re.MULTILINE,
)
_VINA_VERSION_RE = re.compile(r"AutoDock Vina\s+v?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
_VINA_HEADER_RE = re.compile(r"AutoDock Vina", re.IGNORECASE)
_VINA_DONE_RE = re.compile(r"Writing output|Refining results", re.IGNORECASE)

_GNINA_HEADER_RE = re.compile(r"\bgnina\b", re.IGNORECASE)
_GNINA_VERSION_RE = re.compile(r"gnina\s+v?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
# Gnina log row: "   1   -9.2   0.000   0.7321   5.842"
_GNINA_LOG_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$",
    re.MULTILINE,
)


class VinaParser(CompChemParser):
    name = "autodock_vina"
    software_name = "AutoDock Vina"
    run_kinds = [RunKind.DOCKING]

    def supported_extensions(self) -> List[str]:
        return [".pdbqt", ".log", ".out"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdbqt":
            head = self._read_head(file_path, max_bytes=4096)
            return "REMARK VINA RESULT" in head
        if ext in (".log", ".out"):
            head = self._read_head(file_path, max_bytes=4096)
            # Disambiguate: gnina logs also mention "Vina"; check gnina first
            if _GNINA_HEADER_RE.search(head):
                return False
            return bool(_VINA_HEADER_RE.search(head))
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        ext = os.path.splitext(file_path)[1].lower()
        result = CompChemParsedResult(
            software_name="AutoDock Vina",
            software_version=None,
            run_kind=RunKind.DOCKING,
            method="Vina",
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        if ext == ".pdbqt":
            self._parse_pdbqt(file_path, result)
        else:
            self._parse_log(file_path, result)

        return result

    def _parse_pdbqt(self, path: str, result: CompChemParsedResult) -> None:
        # Read whole file (small)
        text = self._read_head(path, max_bytes=512 * 1024)
        scores: List[float] = []
        rmsd_lb: List[float] = []
        for m in _VINA_REMARK_RE.finditer(text):
            scores.append(float(m.group(1)))
            rmsd_lb.append(float(m.group(2)))
        if not scores:
            result.parse_warnings.append("No VINA RESULT lines found in pdbqt")
            result.termination_status = TerminationStatus.UNKNOWN
            return

        result.termination_status = TerminationStatus.NORMAL
        result.metadata["n_poses"] = len(scores)
        result.metadata["score_distribution"] = {
            "min": min(scores), "max": max(scores),
            "mean": sum(scores) / len(scores),
        }
        result.metrics.append(CompChemMetric(
            name="best_binding_affinity",
            value=min(scores),  # Vina reports negative; "best" = most negative
            unit="kcal/mol",
        ))
        for i, (s, r) in enumerate(zip(scores[:10], rmsd_lb[:10]), start=1):
            result.metrics.append(CompChemMetric(
                name=f"pose_affinity_rank_{i}",
                value=s,
                unit="kcal/mol",
                metadata={"rank": i, "rmsd_lb_A": r},
            ))

    def _parse_log(self, path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(path, max_bytes=16384)
        tail = self._read_tail(path, max_bytes=16384)
        full = head + "\n" + tail

        m = _VINA_VERSION_RE.search(head)
        if m:
            result.software_version = m.group(1)

        scores: List[float] = []
        for row in _VINA_LOG_ROW_RE.finditer(full):
            try:
                scores.append(float(row.group(2)))
            except ValueError:
                continue

        if scores:
            result.termination_status = TerminationStatus.NORMAL
            result.metadata["n_poses"] = len(scores)
            result.metrics.append(CompChemMetric(
                name="best_binding_affinity",
                value=min(scores),
                unit="kcal/mol",
            ))
            for i, s in enumerate(scores[:10], start=1):
                result.metrics.append(CompChemMetric(
                    name=f"pose_affinity_rank_{i}",
                    value=s,
                    unit="kcal/mol",
                    metadata={"rank": i},
                ))
        else:
            result.parse_warnings.append("Vina log present but no score table parsed")
            result.termination_status = TerminationStatus.PARTIAL


class GninaParser(CompChemParser):
    name = "gnina"
    software_name = "Gnina"
    run_kinds = [RunKind.DOCKING]

    def supported_extensions(self) -> List[str]:
        return [".log", ".out", ".sdf"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".log", ".out"):
            head = self._read_head(file_path, max_bytes=8192)
            return bool(_GNINA_HEADER_RE.search(head))
        if ext == ".sdf":
            head = self._read_head(file_path, max_bytes=8192)
            return "CNNscore" in head or "minimizedAffinity" in head
        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        result = CompChemParsedResult(
            software_name="Gnina",
            software_version=None,
            run_kind=RunKind.DOCKING,
            method="Gnina CNN",
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".sdf":
            self._parse_sdf(file_path, result)
        else:
            self._parse_log(file_path, result)
        return result

    def _parse_log(self, path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(path, max_bytes=16384)
        tail = self._read_tail(path, max_bytes=16384)
        full = head + "\n" + tail

        m = _GNINA_VERSION_RE.search(head)
        if m:
            result.software_version = m.group(1)

        affinities: List[float] = []
        cnn_scores: List[float] = []
        cnn_affinities: List[float] = []
        for row in _GNINA_LOG_ROW_RE.finditer(full):
            try:
                affinities.append(float(row.group(2)))
                cnn_scores.append(float(row.group(4)))
                cnn_affinities.append(float(row.group(5)))
            except (ValueError, IndexError):
                continue

        if affinities:
            result.termination_status = TerminationStatus.NORMAL
            result.metadata["n_poses"] = len(affinities)
            result.metrics.append(CompChemMetric(
                name="best_binding_affinity",
                value=min(affinities),
                unit="kcal/mol",
            ))
            if cnn_scores:
                result.metrics.append(CompChemMetric(
                    name="best_cnn_score",
                    value=max(cnn_scores),  # CNN score: higher is better
                    unit="dimensionless",
                ))
            if cnn_affinities:
                result.metrics.append(CompChemMetric(
                    name="best_cnn_affinity",
                    value=max(cnn_affinities),  # pK_d, higher is better
                    unit="pK_d",
                ))
        else:
            result.parse_warnings.append("Gnina log present but no score table parsed")
            result.termination_status = TerminationStatus.PARTIAL

    def _parse_sdf(self, path: str, result: CompChemParsedResult) -> None:
        # Lightweight SDF tag scan; we don't pull the full structure
        text = self._read_head(path, max_bytes=512 * 1024)
        cnn_re = re.compile(r"> <CNNscore>\s*\n\s*([-\d.]+)")
        aff_re = re.compile(r"> <minimizedAffinity>\s*\n\s*([-\d.]+)")
        cnn_scores = [float(m.group(1)) for m in cnn_re.finditer(text)]
        affinities = [float(m.group(1)) for m in aff_re.finditer(text)]
        if affinities:
            result.termination_status = TerminationStatus.NORMAL
            result.metadata["n_poses"] = len(affinities)
            result.metrics.append(CompChemMetric(
                name="best_binding_affinity",
                value=min(affinities),
                unit="kcal/mol",
            ))
        if cnn_scores:
            result.metrics.append(CompChemMetric(
                name="best_cnn_score",
                value=max(cnn_scores),
                unit="dimensionless",
            ))
