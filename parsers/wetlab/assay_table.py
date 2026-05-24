"""
Fallback parser for generic wet lab assay tables.

This sits after the more specific wet lab parsers and before GenericCSVParser,
so unclassified CSV/Excel assay files get assay metadata and QC attached while
ordinary tabular files still fall through to the generic parser.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from ..base import BaseParser, ParsedResult
from .assay_format import detect_assay_format, detect_plate_metadata
from .assay_qc import AssayQCEngine


class AssayTableParser(BaseParser):
    name = "assay_table"
    description = "Generic wet lab assay CSV/Excel table"
    vendor = "generic"

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".txt", ".xlsx", ".xls"]

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions():
            return False
        try:
            df = self._read_table(file_path, nrows=100)
        except Exception:
            return False
        return detect_assay_format(df, [str(c) for c in df.columns]) != "unknown"

    def parse(self, file_path: str) -> ParsedResult:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        try:
            df = self._read_table(file_path)
        except Exception as e:
            raise ValueError(f"Failed to parse assay table: {e}")

        df.columns = self._clean_column_names(df.columns)
        headers = [str(c) for c in df.columns]
        assay_format = detect_assay_format(df, headers)
        metadata: Dict[str, Any] = {
            "assay_format": assay_format,
            **detect_plate_metadata(df, headers),
        }
        if assay_format != "unknown":
            metadata["assay_qc"] = [
                result.to_dict()
                for result in AssayQCEngine().run(df, assay_format)
            ]

        return ParsedResult(
            instrument="assay_table",
            format_version="1.0",
            timestamp=self._file_timestamp(file_path),
            metadata=metadata,
            headers=headers,
            data=df,
            raw_stats=self.compute_column_stats(df),
            parse_warnings=[],
            source_file=os.path.basename(file_path),
            file_size_bytes=os.path.getsize(file_path),
        )

    def _read_table(self, file_path: str, nrows: int | None = None) -> pd.DataFrame:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(file_path, nrows=nrows)
        sep = "\t" if ext == ".tsv" else None
        return pd.read_csv(file_path, sep=sep, engine="python", nrows=nrows)

    def _clean_column_names(self, columns: pd.Index) -> List[str]:
        cleaned: List[str] = []
        seen: Dict[str, int] = {}
        for col in columns:
            name = str(col).strip()
            name = " ".join(name.split()) or "unnamed"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            cleaned.append(name)
        return cleaned

    def _file_timestamp(self, file_path: str) -> datetime | None:
        try:
            return datetime.fromtimestamp(os.path.getmtime(file_path))
        except Exception:
            return None
