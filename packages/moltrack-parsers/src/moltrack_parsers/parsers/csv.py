from __future__ import annotations

import csv
from pathlib import Path

from moltrack_parsers.models import FileType, MetricValue, base_result


def detect(filepath: str, head: bytes) -> bool:
    return Path(filepath).suffix.lower() in {".csv", ".tsv"}


def parse(filepath: str):
    result = base_result(filepath, FileType.CSV, "generic_csv")
    delimiter = "\t" if Path(filepath).suffix.lower() == ".tsv" else ","
    with open(filepath, newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        rows = list(reader)
    result.raw_metadata["row_count"] = len(rows)
    result.raw_metadata["columns"] = reader.fieldnames or []

    if rows:
        for col in reader.fieldnames or []:
            values = []
            for row in rows:
                try:
                    values.append(float(row[col]))
                except (TypeError, ValueError):
                    pass
            if values:
                result.extracted_metrics[f"{col}_mean"] = MetricValue(
                    value=sum(values) / len(values),
                    unit="unknown",
                    metadata={"n": len(values), "min": min(values), "max": max(values)},
                )
    return result
