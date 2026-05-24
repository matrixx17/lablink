"""
Wet lab assay format detection for generic tabular CSVs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from .canonical_names import is_time_column


ASSAY_FORMATS = {
    "dose_response",
    "potency_summary",
    "hplc_purity",
    "bioprocess_offline",
    "unknown",
}

_PLATE_FORMATS = {96, 384, 1536}
_WELL_RE = re.compile(r"^\s*([A-Za-z]{1,2})\s*0*([0-9]{1,2})\s*$")


def normalize_header(header: object) -> str:
    """Normalize column headers for simple assay-format matching."""
    text = str(header or "").strip().lower()
    text = text.replace("µ", "u")
    text = text.replace("μ", "u")
    text = re.sub(r"\s+", " ", text)
    return text


def _compact(header: object) -> str:
    return re.sub(r"[^a-z0-9%]+", "", normalize_header(header))


def _matches(header: object, patterns: Iterable[str]) -> bool:
    normalized = normalize_header(header)
    compacted = _compact(header)
    for pattern in patterns:
        p_norm = normalize_header(pattern)
        p_compact = _compact(pattern)
        if p_norm and p_norm in normalized:
            return True
        if p_compact and p_compact in compacted:
            return True
    return False


def _has(headers: Iterable[object], patterns: Iterable[str]) -> bool:
    return any(_matches(header, patterns) for header in headers)


def _find_column(headers: Iterable[object], patterns: Iterable[str]) -> Optional[str]:
    for header in headers:
        if _matches(header, patterns):
            return str(header)
    return None


def _row_number(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        row = int(text)
        return row if row > 0 else None
    if not re.match(r"^[A-Za-z]{1,2}$", text):
        return None
    row = 0
    for char in text.upper():
        row = row * 26 + (ord(char) - ord("A") + 1)
    return row


def _column_number(value: Any) -> Optional[int]:
    try:
        col = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return col if col > 0 else None


def _plate_format_from_dimensions(max_row: Optional[int], max_col: Optional[int]) -> Optional[int]:
    if max_row is None or max_col is None:
        return None
    if max_row <= 8 and max_col <= 12:
        return 96
    if max_row <= 16 and max_col <= 24:
        return 384
    if max_row <= 32 and max_col <= 48:
        return 1536
    return None


def identify_plate_positions(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Return row/column/well positions when a plate layout is explicit."""
    if df is None or df.empty:
        return []

    headers = [str(c) for c in df.columns]
    well_col = _find_column(headers, ["well", "well_id", "well position"])
    row_col = _find_column(headers, ["row", "well_row"])
    col_col = _find_column(headers, ["column", "col", "well_column", "well_col"])

    positions: List[Dict[str, Any]] = []
    for idx, row in df.iterrows():
        row_num: Optional[int] = None
        col_num: Optional[int] = None

        if well_col and well_col in df.columns:
            match = _WELL_RE.match(str(row.get(well_col, "")))
            if match:
                row_num = _row_number(match.group(1))
                col_num = _column_number(match.group(2))

        if row_num is None and row_col and row_col in df.columns:
            row_num = _row_number(row.get(row_col))
        if col_num is None and col_col and col_col in df.columns:
            col_num = _column_number(row.get(col_col))

        if row_num is None or col_num is None:
            continue
        positions.append({
            "index": idx,
            "row": row_num,
            "column": col_num,
            "well": f"{chr(ord('A') + row_num - 1)}{col_num}",
        })
    return positions


def detect_plate_metadata(df: pd.DataFrame, headers: List[str]) -> Dict[str, Any]:
    """Detect common 96/384/1536-well tabular plate layouts."""
    columns = list(headers or [])
    if not columns and df is not None:
        columns = [str(c) for c in df.columns]

    row_count = int(len(df)) if df is not None else 0
    has_plate_headers = _has(columns, ["well", "row", "column", "plate"])
    is_plate_data = row_count in _PLATE_FORMATS or has_plate_headers
    if not is_plate_data:
        return {}

    positions = identify_plate_positions(df)
    max_row = max((p["row"] for p in positions), default=None)
    max_col = max((p["column"] for p in positions), default=None)
    plate_format = row_count if row_count in _PLATE_FORMATS else _plate_format_from_dimensions(max_row, max_col)

    return {
        "is_plate_data": True,
        "plate_format": plate_format,
        "plate_well_count": row_count,
    }


def detect_assay_format(df: pd.DataFrame, headers: List[str]) -> str:
    """
    Detect common wet lab assay tables after a generic CSV parse.

    Returns one of:
    dose_response, potency_summary, hplc_purity, bioprocess_offline, unknown.
    """
    columns = list(headers or [])
    if not columns and df is not None:
        columns = [str(c) for c in df.columns]

    has_concentration = _has(columns, ["concentration", "conc", "dose", "[um]", "[nm]"])
    has_response = _has(columns, ["response", "inhibition", "activity", "signal", "%inh", "viability"])
    if has_concentration and has_response:
        return "dose_response"

    has_compound = _has(columns, ["compound_id", "cmpd", "cpd", "name"])
    has_potency = _has(columns, ["ic50", "ec50", "ki", "kd"])
    if has_compound and has_potency:
        return "potency_summary"

    has_purity = _has(columns, ["purity", "%purity", "area%"])
    has_rt = _has(columns, ["rt", "retention_time", "ret_time"])
    if has_purity and has_rt:
        return "hplc_purity"

    has_viable = _has(columns, ["vcd", "viable", "viability"])
    has_time = any(is_time_column(normalize_header(c)) for c in columns)
    if has_viable and has_time:
        return "bioprocess_offline"

    return "unknown"
