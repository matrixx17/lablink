"""
ÄKTA chromatography CSV parser (Cytiva, formerly GE Healthcare UNICORN).

ÄKTA exports a CSV with ~20 metadata rows up top (Method:, Column:,
Run date:, etc.) followed by a chromatogram table whose first data row
typically contains column headers `ml`, `mAU`, `%B`, `Pressure (MPa)`,
`Temperature (°C)`. Optionally a peak table follows with `Peak Name`,
`Retention Volume (ml)`, `Area`, `Height`.

We emit:

- `metadata.method`, `metadata.column`, `metadata.run_date` when present
- `metadata.x_axis = "ml"` (chromatograms aren't time-based)
- `metadata.peaks` list when a peak table is detected
- `series_points` with `t = ml` and `field` ∈ {"uv_absorbance_mau",
  "buffer_b_percent"} (so downstream code that expects ParsedResult
  series can consume it without changes)
- `data_kind = "continuous"`
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..base import BaseParser, ParsedResult


# Signal phrases in the metadata block that indicate this is ÄKTA / UNICORN.
_AKTA_SIGNALS = ("UNICORN", "Cytiva", "GE Healthcare", "ÄKTA", "Akta")


class AktaCsvParser(BaseParser):
    name = "akta_csv"
    description = "Cytiva ÄKTA / UNICORN chromatography CSV export"
    vendor = "Cytiva"

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

        head = content[:4096]

        # Strong signal: vendor name in the header block
        for signal in _AKTA_SIGNALS:
            if signal in head:
                return True

        # Weak signal: header row contains BOTH `ml` and `mAU` columns
        # (no other lab CSV typically pairs these exact tokens)
        try:
            for line in head.splitlines()[:60]:
                lower = line.lower()
                if ",ml" in lower and ",mau" in lower:
                    return True
                if "\tml" in lower and "\tmau" in lower:
                    return True
        except Exception:
            return False

        return False

    def supported_extensions(self) -> List[str]:
        return [".csv", ".tsv", ".txt"]

    # -----------------------------------------------------------------
    # parse
    # -----------------------------------------------------------------

    def parse(self, file_path: str) -> ParsedResult:
        content = self._read_file_safely(file_path)
        delimiter = "\t" if "\t" in content[:1024] and "," not in content[:200] else ","

        lines = content.splitlines()
        metadata = self._parse_metadata_block(lines)
        data_start = self._find_data_start(lines, delimiter)

        df: Optional[pd.DataFrame] = None
        series_points: List[Dict[str, Any]] = []

        if data_start is not None:
            data_lines = lines[data_start:]
            # Strip blank rows mid-file and the peak table if present
            data_only, peak_lines = self._split_chromatogram_and_peaks(data_lines, delimiter)
            if data_only:
                try:
                    df = pd.read_csv(io.StringIO("\n".join(data_only)), sep=delimiter)
                except Exception:
                    df = None

            if peak_lines:
                metadata["peaks"] = self._parse_peak_table(peak_lines, delimiter)

        if df is not None and not df.empty:
            series_points = self._chromatogram_series(df)

        warnings: List[str] = []
        if df is None or df.empty:
            warnings.append("ÄKTA chromatogram body could not be parsed")

        # Run date / method / column come from metadata block
        run_dt = None
        if metadata.get("run_date"):
            try:
                run_dt = pd.to_datetime(metadata["run_date"]).to_pydatetime()
            except Exception:
                run_dt = None

        result = ParsedResult(
            instrument="akta",
            format_version=metadata.get("unicorn_version") or "unknown",
            timestamp=run_dt,
            metadata={**metadata, "x_axis": "ml"},
            headers=list(df.columns) if df is not None else [],
            data=df,
            raw_stats=self.compute_column_stats(df) if df is not None else {},
            data_kind="continuous",
            time_column=None,                   # x-axis is volume (ml), not time
            series_points=series_points,
            parse_warnings=warnings,
            source_file=file_path,
            file_size_bytes=os.path.getsize(file_path),
        )
        return result

    # -----------------------------------------------------------------
    # internals
    # -----------------------------------------------------------------

    def _parse_metadata_block(self, lines: List[str]) -> Dict[str, Any]:
        """Pull `Method:`, `Column:`, `Run date:` and similar key/value pairs."""
        out: Dict[str, Any] = {}
        # ÄKTA metadata rows look like: "Method:,my_method,,,"
        kv_re = re.compile(
            r"^\s*(method|column|run\s*date|date|sample|operator|unicorn\s*version)\s*:\s*[,\t]?\s*(.+?)\s*[,\t]?\s*$",
            re.IGNORECASE,
        )
        for line in lines[:40]:
            m = kv_re.match(line)
            if not m:
                continue
            key = re.sub(r"\s+", "_", m.group(1).strip().lower())
            value = m.group(2).strip().strip(",").strip()
            # Drop trailing CSV padding
            value = re.sub(r"[,\t]+$", "", value).strip()
            if not value:
                continue
            if key == "method":
                out["method"] = value
            elif key == "column":
                out["column"] = value
            elif key in ("run_date", "date"):
                out["run_date"] = value
            elif key == "sample":
                out["sample"] = value
            elif key == "operator":
                out["operator"] = value
            elif key == "unicorn_version":
                out["unicorn_version"] = value
        return out

    def _find_data_start(self, lines: List[str], delimiter: str) -> Optional[int]:
        """Return the line index where the chromatogram column-header row sits."""
        for i, line in enumerate(lines):
            cells = [c.strip().lower() for c in next(csv.reader([line], delimiter=delimiter), [])]
            if not cells:
                continue
            has_ml = any(c == "ml" or c.startswith("ml ") for c in cells)
            has_mau = any("mau" in c for c in cells)
            if has_ml and has_mau:
                return i
        return None

    def _split_chromatogram_and_peaks(
        self, lines: List[str], delimiter: str
    ) -> Tuple[List[str], List[str]]:
        """
        Some exports concatenate the chromatogram and a peak table; split on
        the first empty row or `Peak`-style header after the data block.
        """
        chromatogram: List[str] = []
        peaks: List[str] = []
        in_peaks = False
        peak_header_re = re.compile(r"\bpeak\b", re.IGNORECASE)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # blank row separates sections
                if chromatogram:
                    in_peaks = True
                continue
            if not in_peaks and peak_header_re.search(stripped) and (
                "retention" in stripped.lower() or "area" in stripped.lower()
            ):
                in_peaks = True
                peaks.append(line)
                continue
            (peaks if in_peaks else chromatogram).append(line)

        return chromatogram, peaks

    def _parse_peak_table(self, lines: List[str], delimiter: str) -> List[Dict[str, Any]]:
        """Parse the peak table into a list of dicts."""
        try:
            df = pd.read_csv(io.StringIO("\n".join(lines)), sep=delimiter)
        except Exception:
            return []
        if df.empty:
            return []

        # Normalise column names
        rename: Dict[str, str] = {}
        for c in df.columns:
            cl = str(c).strip().lower()
            if "retention" in cl and "volume" in cl:
                rename[c] = "retention_volume_ml"
            elif cl in ("peak name", "peak", "name"):
                rename[c] = "name"
            elif "area" in cl:
                rename[c] = "peak_area_mau_ml"
            elif "height" in cl:
                rename[c] = "peak_height_mau"
        df = df.rename(columns=rename)

        keep_cols = [c for c in ("name", "retention_volume_ml", "peak_area_mau_ml", "peak_height_mau") if c in df.columns]
        records = df[keep_cols].to_dict(orient="records") if keep_cols else df.to_dict(orient="records")

        # Drop empty rows
        cleaned: List[Dict[str, Any]] = []
        for rec in records:
            if all(pd.isna(v) or v in ("", None) for v in rec.values()):
                continue
            cleaned.append({k: (None if pd.isna(v) else v) for k, v in rec.items()})
        return cleaned

    def _chromatogram_series(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Convert the chromatogram into series_points keyed by 'ml'."""
        # Locate columns
        ml_col = None
        mau_col = None
        b_col = None
        for c in df.columns:
            cl = str(c).strip().lower()
            if cl == "ml" or cl.startswith("ml "):
                ml_col = c
            elif "mau" in cl:
                mau_col = c
            elif cl in ("%b", "buffer b", "buffer b %", "%b ", "%b\t"):
                b_col = c

        points: List[Dict[str, Any]] = []
        if ml_col is None:
            return points

        for _, row in df.iterrows():
            try:
                t = float(row[ml_col])
            except (TypeError, ValueError):
                continue
            if mau_col is not None:
                try:
                    v = float(row[mau_col])
                    if not pd.isna(v):
                        points.append({"t": t, "field": "uv_absorbance_mau", "value": v, "unit": "mAU"})
                except (TypeError, ValueError):
                    pass
            if b_col is not None:
                try:
                    v = float(row[b_col])
                    if not pd.isna(v):
                        points.append({"t": t, "field": "buffer_b_percent", "value": v, "unit": "%"})
                except (TypeError, ValueError):
                    pass
        return points
