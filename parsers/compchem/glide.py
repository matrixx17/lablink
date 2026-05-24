"""
Schrödinger Glide docking output parser.

Glide produces:
  - _pv.mae / _pv.maegz   Pose viewer files (compressed Maestro format).
                          We don't unpack .maegz here — that requires
                          Schrödinger's proprietary libs. We extract from
                          the accompanying _dock.log instead.
  - _dock.log             Docking log with score table per ligand pose.
  - .mae                  Single-structure Maestro file.

Detection priority: _dock.log files (highest signal), then .maegz / .mae
based on filename pattern.
"""

import os
import re
from typing import List, Optional

from .base import (
    CompChemMetric,
    CompChemParsedResult,
    CompChemParser,
    RunKind,
    TerminationStatus,
)


_GLIDE_LOG_HEADERS = [
    re.compile(r"Glide\s+(SP|XP|HTVS)", re.IGNORECASE),
    re.compile(r"Schr.dinger.*Glide", re.IGNORECASE),
    re.compile(r"glide_version", re.IGNORECASE),
]

_GLIDE_VERSION_RE = re.compile(r"Glide\s+(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
_GLIDE_PRECISION_RE = re.compile(r"Glide\s+(SP|XP|HTVS)", re.IGNORECASE)
_GLIDE_NORMAL_RE = re.compile(r"^\s*===+\s*$.*Glide.*successfully", re.MULTILINE | re.DOTALL | re.IGNORECASE)
_GLIDE_DONE_RE = re.compile(r"docking job completed", re.IGNORECASE)
_GLIDE_FAIL_RE = re.compile(r"FATAL.*ERROR|Docking job failed", re.IGNORECASE)

# Glide score table — header line is e.g.:
#   ===  Rank ===  Title === Lig#  === Score === DScore === GScore ===
# Followed by rows of pose results. The simplest reliable extraction is
# the "Best Glide score" line at the bottom.
_GLIDE_BEST_SCORE_RE = re.compile(
    r"Best Glide\s+(?:Score|GScore|XP score)?\s*[:=]?\s*(-?\d+\.\d+)",
    re.IGNORECASE,
)
_GLIDE_POSE_ROW_RE = re.compile(
    r"^\s*\d+\s+\S+\s+\d+\s+(-?\d+\.\d+)",  # rank, title, lig#, gscore
    re.MULTILINE,
)


class GlideParser(CompChemParser):
    name = "glide"
    software_name = "Glide"
    run_kinds = [RunKind.DOCKING]

    def supported_extensions(self) -> List[str]:
        return [".log", ".mae", ".maegz"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        base = os.path.basename(file_path).lower()
        ext = os.path.splitext(file_path)[1].lower()

        # Filename heuristics for pose-viewer / maegz output
        if base.endswith("_pv.maegz") or base.endswith("_pv.mae"):
            return True

        if ext == ".log":
            head = self._read_head(file_path, max_bytes=8192)
            return any(p.search(head) for p in _GLIDE_LOG_HEADERS)

        return False

    def parse(self, file_path: str) -> CompChemParsedResult:
        result = CompChemParsedResult(
            software_name="Glide",
            software_version=None,
            run_kind=RunKind.DOCKING,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

        ext = os.path.splitext(file_path)[1].lower()

        # Binary .mae / .maegz — minimal parse. Mark and look for sibling log.
        if ext in (".mae", ".maegz"):
            result.metadata["pose_viewer_file"] = True
            sibling_log = self._find_sibling_log(file_path)
            if sibling_log:
                result.parse_warnings.append(
                    f"Binary pose viewer; metrics taken from {os.path.basename(sibling_log)}"
                )
                self._populate_from_log(sibling_log, result)
            else:
                result.parse_warnings.append(
                    "Binary pose viewer; no _dock.log sibling found — metrics unavailable"
                )
                result.termination_status = TerminationStatus.UNKNOWN
            return result

        # Text log
        self._populate_from_log(file_path, result)
        return result

    def _populate_from_log(self, log_path: str, result: CompChemParsedResult) -> None:
        head = self._read_head(log_path, max_bytes=16384)
        tail = self._read_tail(log_path, max_bytes=32768)
        full_text = head + "\n" + tail

        m = _GLIDE_VERSION_RE.search(head)
        if m:
            result.software_version = m.group(1)

        m = _GLIDE_PRECISION_RE.search(head)
        if m:
            result.method = f"Glide {m.group(1).upper()}"

        # Termination
        if _GLIDE_FAIL_RE.search(full_text):
            result.termination_status = TerminationStatus.CRASHED
        elif _GLIDE_DONE_RE.search(tail) or _GLIDE_NORMAL_RE.search(tail):
            result.termination_status = TerminationStatus.NORMAL
        else:
            result.termination_status = TerminationStatus.PARTIAL

        # Best Glide score
        m = _GLIDE_BEST_SCORE_RE.search(tail) or _GLIDE_BEST_SCORE_RE.search(head)
        if m:
            score = float(m.group(1))
            result.metrics.append(CompChemMetric(
                name="best_glide_score",
                value=score,
                unit="kcal/mol",
            ))

        # Pose distribution — collect all per-pose gscores
        pose_scores: List[float] = []
        for row in _GLIDE_POSE_ROW_RE.finditer(full_text):
            try:
                pose_scores.append(float(row.group(1)))
            except ValueError:
                continue

        if pose_scores:
            result.metadata["n_poses"] = len(pose_scores)
            result.metadata["score_distribution"] = {
                "min": min(pose_scores),
                "max": max(pose_scores),
                "mean": sum(pose_scores) / len(pose_scores),
            }
            # Add top-N as individual metrics so the downstream system can
            # query each pose
            for i, s in enumerate(sorted(pose_scores)[:10], start=1):
                result.metrics.append(CompChemMetric(
                    name=f"pose_score_rank_{i}",
                    value=s,
                    unit="kcal/mol",
                    metadata={"rank": i},
                ))

    @staticmethod
    def _find_sibling_log(pose_path: str) -> Optional[str]:
        """Glide convention: foo_pv.maegz <-> foo_dock.log."""
        d = os.path.dirname(pose_path)
        base = os.path.basename(pose_path)
        stem = base
        for suffix in ("_pv.maegz", "_pv.mae"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        candidate = os.path.join(d, f"{stem}_dock.log")
        return candidate if os.path.isfile(candidate) else None
