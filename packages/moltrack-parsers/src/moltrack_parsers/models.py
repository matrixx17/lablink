from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class FileType(str, Enum):
    GROMACS_EDR = "gromacs_edr"
    DFT_LOG = "dft_log"
    VINA_LOG = "vina_log"
    SDF = "sdf"
    CSV = "csv"
    UNKNOWN = "unknown"


@dataclass
class MetricValue:
    value: float
    unit: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    file_type: FileType
    software_name: Optional[str]
    software_version: Optional[str]
    extracted_metrics: Dict[str, MetricValue] = field(default_factory=dict)
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    file_hash: str = ""
    source_file: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_type": self.file_type.value,
            "software_name": self.software_name,
            "software_version": self.software_version,
            "extracted_metrics": {
                key: {
                    "value": metric.value,
                    "unit": metric.unit,
                    "metadata": metric.metadata,
                }
                for key, metric in self.extracted_metrics.items()
            },
            "raw_metadata": self.raw_metadata,
            "file_hash": self.file_hash,
            "source_file": self.source_file,
        }


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def base_result(filepath: str, file_type: FileType, software_name: Optional[str]) -> ParseResult:
    return ParseResult(
        file_type=file_type,
        software_name=software_name,
        software_version=None,
        file_hash=sha256_file(filepath),
        source_file=str(Path(filepath)),
    )
