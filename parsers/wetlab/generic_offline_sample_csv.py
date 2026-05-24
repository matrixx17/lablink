"""
Generic offline-sample CSV parser — fallback for un-vendored discrete
measurement exports.

Detection signal:
- CSV header contains "Viable" / "VCD" / "Titer" plus a time column
- and is "short" (< 50 rows) — heuristic for discrete sampling

Two shapes are handled:

  WIDE  (one row per timepoint):
    Sample Time (h), VCD (e6/mL), Viability (%), Glucose (g/L), Titer (mg/L)

  LONG  (one row per measurement):
    Sample Time (h), Parameter, Value, Unit

Returned `series_points` carry `data_kind="discrete_offline"`. Each
point: `{t, field=canonical_name, value, unit}`.
"""

from __future__ import annotations

import csv
import io
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..base import BaseParser, ParsedResult
from .canonical_names import (
    canonicalize_parameter,
    derive_unit_for,
    is_time_column,
)


# Markers that hint this is offline-shaped data
_OFFLINE_HINT = ("viable", "vcd", "titer", "viability", "glucose", "lactate")


class GenericOfflineSampleCsvParser(BaseParser):
    name = "generic_offline_sample_csv"
    description = "Generic offline-sample CSV (canonical-name fallback)"
    vendor = "generic"

    # Soft cap: anything longer than this is probably a continuous controller
    # export, not an offline sample sheet.
    MAX_ROWS_FOR_OFFLINE = 50

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

        lines = [l for l in content.splitlines() if l.strip()]
        if not lines:
            return False

        # Find a plausible header row in the first few lines
        header_line, _idx = self._find_header(lines)
        if not header_line:
            return False

        # Row count cap (header + data combined)
        if len(lines) > self.MAX_ROWS_FOR_OFFLINE + 4:
            return False

        cells = [c.strip().lower() for c in header_line]
        has_offline_term = any(any(h in c for h in _OFFLINE_HINT) for c in cells)
        has_time = any(is_time_column(c) for c in cells)
        # Long-format CSVs ("Parameter, Value, Unit") don't put offline
        # keywords in the *header* — the parameter name lives in the rows.
        # Accept either: wide (offline term in header) OR long (Parameter
        # + Value columns present alongside a time column).
        has_long_shape = (
            "parameter" in cells or "measurement" in cells or "analyte" in cells
        ) and ("value" in cells or "result" in cells)
        return has_time and (has_offline_term or has_long_shape)

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".txt"]

    # -----------------------------------------------------------------
    # parse
    # -----------------------------------------------------------------

    def parse(self, file_path: str) -> ParsedResult:
        content = self._read_file_safely(file_path)
        lines = content.splitlines()
        header_cells, header_idx = self._find_header(lines)
        if not header_cells:
            return self._empty_result(file_path, "no header detected")

        delimiter = self._detect_delimiter(lines[header_idx])
        body = "\n".join(lines[header_idx:])
        try:
            df = pd.read_csv(io.StringIO(body), sep=delimiter)
        except Exception as e:
            return self._empty_result(file_path, f"pandas read_csv failed: {e}")

        if df.empty:
            return self._empty_result(file_path, "empty data body")

        # Detect WIDE vs LONG
        col_strs = [str(c).strip() for c in df.columns]
        param_col = next(
            (c for c, cs in zip(df.columns, col_strs)
             if cs.lower() in ("parameter", "measurement", "analyte", "name")),
            None,
        )
        value_col = next(
            (c for c, cs in zip(df.columns, col_strs) if cs.lower() in ("value", "result")),
            None,
        )
        is_long = param_col is not None and value_col is not None

        time_col = next((c for c in df.columns if is_time_column(str(c))), None)

        series_points: List[Dict[str, Any]] = []
        if is_long:
            series_points = self._parse_long(df, time_col, param_col, value_col)
        else:
            series_points = self._parse_wide(df, time_col)

        # Summary stats per canonical parameter
        raw_stats: Dict[str, Dict[str, Any]] = {}
        by_field: Dict[str, List[float]] = {}
        for p in series_points:
            by_field.setdefault(p["field"], []).append(p["value"])
        for field, vals in by_field.items():
            if not vals:
                continue
            s = pd.Series(vals, dtype=float)
            raw_stats[field] = {
                "mean": float(s.mean()),
                "std": float(s.std(ddof=1)) if len(s) > 1 else 0.0,
                "min": float(s.min()),
                "max": float(s.max()),
                "n": int(len(s)),
                "null_count": 0,
                "values": vals,
            }

        return ParsedResult(
            instrument="generic_offline_sample",
            format_version="1.0",
            timestamp=None,
            metadata={
                "shape": "long" if is_long else "wide",
                "parameter_count": len(by_field),
            },
            headers=[str(c) for c in df.columns],
            data=df,
            raw_stats=raw_stats,
            data_kind="discrete_offline",
            time_column="time_hours" if series_points else None,
            series_points=series_points,
            parse_warnings=[] if series_points else ["no recognised measurement columns"],
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )

    # -----------------------------------------------------------------
    # internals
    # -----------------------------------------------------------------

    def _find_header(self, lines: List[str]) -> Tuple[List[str], int]:
        for i in range(min(len(lines), 8)):
            line = lines[i].strip()
            if not line:
                continue
            delim = self._detect_delimiter(line)
            try:
                cells = next(csv.reader([line], delimiter=delim))
            except Exception:
                continue
            cells = [c.strip() for c in cells]
            if len(cells) < 2:
                continue
            lower_cells = [c.lower() for c in cells]
            has_time = any(is_time_column(c) for c in lower_cells)
            has_hint = any(any(h in c for h in _OFFLINE_HINT) for c in lower_cells)
            has_long_shape = (
                ("parameter" in lower_cells or "measurement" in lower_cells
                 or "analyte" in lower_cells)
                and ("value" in lower_cells or "result" in lower_cells)
            )
            if has_time and (has_hint or has_long_shape):
                return cells, i
        return [], 0

    def _detect_delimiter(self, line: str) -> str:
        if line.count("\t") >= 2:
            return "\t"
        if line.count(";") > line.count(","):
            return ";"
        return ","

    def _parse_long(
        self,
        df: pd.DataFrame,
        time_col: Optional[Any],
        param_col: Any,
        value_col: Any,
    ) -> List[Dict[str, Any]]:
        unit_col = next(
            (c for c in df.columns if str(c).strip().lower() in ("unit", "units")),
            None,
        )
        points: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                t = float(row[time_col]) if time_col is not None else float("nan")
            except (TypeError, ValueError):
                continue
            param_raw = str(row[param_col]) if pd.notna(row[param_col]) else ""
            canonical = canonicalize_parameter(param_raw)
            if canonical is None:
                continue
            try:
                v = float(row[value_col])
            except (TypeError, ValueError):
                continue
            unit = None
            if unit_col is not None and pd.notna(row[unit_col]):
                unit = str(row[unit_col]).strip()
            points.append({
                "t": t,
                "field": canonical,
                "value": v,
                "unit": unit or derive_unit_for(canonical, param_raw),
            })
        return points

    def _parse_wide(
        self,
        df: pd.DataFrame,
        time_col: Optional[Any],
    ) -> List[Dict[str, Any]]:
        # Map each measurement column to a canonical name (skip the time col)
        canonical_map: Dict[Any, str] = {}
        unit_map: Dict[Any, Optional[str]] = {}
        for c in df.columns:
            if c == time_col:
                continue
            cn = canonicalize_parameter(str(c))
            if cn is None:
                continue
            canonical_map[c] = cn
            unit_map[c] = derive_unit_for(cn, str(c))

        points: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            t_val: Optional[float] = None
            if time_col is not None:
                try:
                    t_val = float(row[time_col])
                except (TypeError, ValueError):
                    t_val = None
            for raw_col, canonical in canonical_map.items():
                val = row[raw_col]
                if pd.isna(val):
                    continue
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                points.append({
                    "t": t_val if t_val is not None else 0.0,
                    "field": canonical,
                    "value": fval,
                    "unit": unit_map.get(raw_col),
                })
        return points

    def _empty_result(self, file_path: str, reason: str) -> ParsedResult:
        return ParsedResult(
            instrument="generic_offline_sample",
            format_version="1.0",
            timestamp=None,
            metadata={"parse_error": reason},
            data_kind="discrete_offline",
            parse_warnings=[reason],
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
        )
