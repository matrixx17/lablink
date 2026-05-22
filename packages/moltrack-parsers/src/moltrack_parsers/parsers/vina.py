from __future__ import annotations

import re
from pathlib import Path

from moltrack_parsers.models import FileType, MetricValue, ParseResult, base_result

_VERSION_RE = re.compile(r"AutoDock Vina\s+v?(\d+\.\d+(?:\.\d+)?)", re.I)
_ROW_RE = re.compile(r"^\s*(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", re.M)


def detect(filepath: str, head: bytes) -> bool:
    return Path(filepath).suffix.lower() in {".log", ".out", ".txt"} and b"AutoDock Vina" in head


def parse(filepath: str) -> ParseResult:
    text = Path(filepath).read_text(errors="ignore")
    result = base_result(filepath, FileType.VINA_LOG, "AutoDock Vina")
    version = _VERSION_RE.search(text)
    if version:
        result.software_version = version.group(1)

    scores = []
    for row in _ROW_RE.finditer(text):
        rank = int(row.group(1))
        score = float(row.group(2))
        scores.append(score)
        result.extracted_metrics[f"pose_affinity_rank_{rank}"] = MetricValue(
            value=score,
            unit="kcal/mol",
            metadata={"rank": rank, "rmsd_lb_A": float(row.group(3)), "rmsd_ub_A": float(row.group(4))},
        )

    if scores:
        result.extracted_metrics["best_binding_affinity"] = MetricValue(min(scores), "kcal/mol")
        result.raw_metadata["score_distribution"] = {
            "min": min(scores),
            "max": max(scores),
            "mean": sum(scores) / len(scores),
            "n": len(scores),
        }
    return result
