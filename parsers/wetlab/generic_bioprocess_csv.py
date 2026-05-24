"""
Generic bioprocess controller CSV parser — fallback for un-vendored
bioreactor exports.

Detection signal: header row contains ≥2 of the canonical bioreactor
parameters (pH, DO, temperature, agitation, feed, volume, VCD). Sits
in the registry AFTER the vendor-specific parsers (Sartorius / Cytiva /
Eppendorf / Nova / Vi-CELL) and BEFORE `GenericCSVParser`, so it only
fires when the vendor parsers don't match.

What it does that the plain `GenericCSVParser` doesn't:
- Skip multi-row metadata preambles (1–10 rows of "Method:" / "Date:"
  / blank) until a row with ≥3 numeric/timestamp cells.
- Identify the time column by header alias (`time`, `elapsed`, `t [h]`,
  `datetime`, `date`).
- Strip bracketed units from headers and remap to canonical snake_case
  parameter names via `canonicalize_parameter`.
- Convert timestamps to hours-since-first.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..base import BaseParser, ParsedResult
from .canonical_names import (
    canonicalize_parameter,
    derive_unit_for,
    is_time_column,
)


_BIOPROCESS_HINT_RE = re.compile(
    r"\b(ph|do|dissolved oxygen|temp|temperature|agit|agitation|rpm|feed|volume|vcd|viable)\b",
    re.IGNORECASE,
)


class GenericBioprocessCsvParser(BaseParser):
    name = "generic_bioprocess_csv"
    description = "Generic bioreactor controller CSV (canonical-name fallback)"
    vendor = "generic"

    # -----------------------------------------------------------------
    # detect
    # -----------------------------------------------------------------

    def detect(self, file_path: str) -> bool:
        if not os.path.isfile(file_path):
            return False
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_extensions():
            return False
        try:
            content = self._read_file_safely(file_path)
        except Exception:
            return False

        headers, _ = self._find_header_row(content)
        if not headers or len(headers) < 3:
            return False

        # Must have a time column AND ≥2 bioprocess parameter columns.
        time_present = any(is_time_column(h) for h in headers)
        if not time_present:
            return False

        canonical_hits = sum(1 for h in headers if canonicalize_parameter(h) is not None)
        # Subtract 0 for time since we matched it separately.
        return canonical_hits >= 2

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".txt"]

    # -----------------------------------------------------------------
    # parse
    # -----------------------------------------------------------------

    def parse(self, file_path: str) -> ParsedResult:
        content = self._read_file_safely(file_path)
        headers, header_line_idx = self._find_header_row(content)
        delimiter = self._detect_delimiter(content, header_line_idx)

        if not headers:
            return self._empty_result(file_path, "no header row identified")

        body = "\n".join(content.splitlines()[header_line_idx:])
        try:
            df = pd.read_csv(io.StringIO(body), sep=delimiter)
        except Exception as e:
            return self._empty_result(file_path, f"pandas read_csv failed: {e}")

        if df.empty:
            return self._empty_result(file_path, "empty data body")

        # Find the time column (raw header) and the parameter columns.
        time_col_raw = next((h for h in df.columns if is_time_column(str(h))), None)

        # Build a rename map: raw header -> canonical_name (only for matched cols)
        renames: Dict[str, str] = {}
        col_units: Dict[str, Optional[str]] = {}
        for c in df.columns:
            if c == time_col_raw:
                continue
            canonical = canonicalize_parameter(str(c))
            if canonical is None:
                continue
            renames[c] = canonical
            col_units[canonical] = derive_unit_for(canonical, str(c))

        # Convert the time column to hours-since-first (float).
        df_renamed = df.rename(columns=renames)
        time_col_canonical = "time_hours"
        if time_col_raw is not None:
            df_renamed[time_col_canonical] = self._to_hours_since_first(df[time_col_raw])
            if time_col_raw in df_renamed.columns and time_col_raw != time_col_canonical:
                df_renamed = df_renamed.drop(columns=[time_col_raw])
        else:
            df_renamed[time_col_canonical] = list(range(len(df_renamed)))

        # Build series_points
        series_points: List[Dict[str, Any]] = []
        param_cols = [c for c in df_renamed.columns if c != time_col_canonical and c in col_units]
        for idx, row in df_renamed.iterrows():
            try:
                t = float(row[time_col_canonical])
            except (TypeError, ValueError):
                continue
            for col in param_cols:
                val = row[col]
                if pd.isna(val):
                    continue
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                series_points.append({
                    "t": t,
                    "field": col,
                    "value": fval,
                    "unit": col_units.get(col),
                })

        raw_stats = self.compute_column_stats(df_renamed[param_cols + [time_col_canonical]])

        warnings: List[str] = []
        if not param_cols:
            warnings.append("no canonical bioprocess parameters detected")
        if time_col_raw is None:
            warnings.append("no time column found; using row index as t")

        return ParsedResult(
            instrument="generic_bioprocess",
            format_version="1.0",
            timestamp=None,
            metadata={
                "canonical_parameters": param_cols,
                "raw_to_canonical": renames,
            },
            headers=list(df_renamed.columns),
            data=df_renamed,
            raw_stats=raw_stats,
            data_kind="continuous",
            time_column=time_col_canonical,
            series_points=series_points,
            parse_warnings=warnings,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

    # -----------------------------------------------------------------
    # internals
    # -----------------------------------------------------------------

    def _find_header_row(self, content: str) -> Tuple[List[str], int]:
        """
        Scan the first ~12 lines for a row that looks like a header:
        - has ≥3 non-empty cells
        - at least one cell looks like a bioprocess parameter
        - the *next* row (if any) parses to mostly numeric/time-like cells

        Returns (headers, line_index). Empty list if no plausible header.
        """
        lines = content.splitlines()
        for i in range(min(len(lines), 12)):
            line = lines[i].strip()
            if not line:
                continue
            delim = self._detect_delimiter_line(line)
            try:
                cells = next(csv.reader([line], delimiter=delim))
            except Exception:
                continue
            cells = [c.strip() for c in cells]
            if len(cells) < 3:
                continue
            if not any(_BIOPROCESS_HINT_RE.search(c) or is_time_column(c) for c in cells):
                continue
            # Looks plausible — confirm the next non-blank row is mostly numeric
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines):
                return cells, i
            try:
                next_cells = next(csv.reader([lines[j]], delimiter=delim))
            except Exception:
                return cells, i
            numeric = 0
            for nc in next_cells:
                nc = nc.strip()
                if not nc:
                    continue
                try:
                    float(nc)
                    numeric += 1
                except ValueError:
                    if re.match(r"^\d{1,2}[:/]\d", nc) or re.match(r"^\d{4}-\d{2}-\d{2}", nc):
                        numeric += 1
            if numeric >= max(2, len(next_cells) // 2):
                return cells, i
        return [], 0

    def _detect_delimiter_line(self, line: str) -> str:
        if line.count("\t") >= 2:
            return "\t"
        if line.count(";") > line.count(","):
            return ";"
        return ","

    def _detect_delimiter(self, content: str, header_line_idx: int) -> str:
        lines = content.splitlines()
        if header_line_idx >= len(lines):
            return ","
        return self._detect_delimiter_line(lines[header_line_idx])

    def _to_hours_since_first(self, series: pd.Series) -> List[float]:
        """
        Convert a column to hours-since-first. Handles:
        - numeric hours already
        - numeric minutes (heuristic: max > 100 and unit suffix `min`)
        - parseable datetime strings
        """
        # Try numeric first
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.notna().sum() >= len(series) * 0.8:
            first = numeric.dropna().iloc[0]
            return [(float(x) - float(first)) if pd.notna(x) else float("nan") for x in numeric]

        # Try datetime
        dt = pd.to_datetime(series, errors="coerce")
        if dt.notna().sum() >= len(series) * 0.8:
            first = dt.dropna().iloc[0]
            return [
                ((x - first).total_seconds() / 3600.0) if pd.notna(x) else float("nan")
                for x in dt
            ]

        # Give up — return row index
        return list(range(len(series)))

    def _empty_result(self, file_path: str, reason: str) -> ParsedResult:
        return ParsedResult(
            instrument="generic_bioprocess",
            format_version="1.0",
            timestamp=None,
            metadata={"parse_error": reason},
            parse_warnings=[reason],
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
        )
