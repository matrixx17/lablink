"""
Bridge wet-lab parser output to TimeseriesData rows with series_metadata.

ÄKTA and other parsers stash chromatography context on ParsedResult.metadata
(x_axis, method, column, peaks). This module copies that onto every
TimeseriesData row for a batch so methods export, Evidence Book, and
batch-record PDFs can read it without re-parsing files.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from database import TimeseriesData

CHROMATOGRAPHY_PARAMETERS = frozenset({"uv_absorbance_mau", "buffer_b_percent"})


def series_metadata_for_parameter(
    parameter_name: str,
    file_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the JSONB metadata blob for one persisted parameter series."""
    meta = dict(file_metadata or {})
    if parameter_name in CHROMATOGRAPHY_PARAMETERS or meta.get("x_axis") == "ml":
        meta.setdefault("x_axis", "ml")
    else:
        meta.setdefault("x_axis", "hours")
    return meta


def group_series_points(
    series_points: List[Dict[str, Any]],
) -> Dict[str, Tuple[List[float], List[float], Optional[str]]]:
    """
    Group ParsedResult.series_points by canonical field name.

    Returns {parameter_name: (timestamps, values, unit)}.
    """
    buckets: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"t": [], "v": [], "unit": None}
    )
    for pt in series_points or []:
        field = pt.get("field") or pt.get("parameter")
        if not field:
            continue
        buckets[field]["t"].append(float(pt["t"]))
        buckets[field]["v"].append(float(pt["value"]))
        if pt.get("unit"):
            buckets[field]["unit"] = pt["unit"]
    return {
        name: (b["t"], b["v"], b["unit"])
        for name, b in buckets.items()
    }


def timeseries_rows_from_parse(
    batch_id: str,
    series_points: List[Dict[str, Any]],
    file_metadata: Optional[Dict[str, Any]] = None,
    *,
    source_instrument: Optional[str] = None,
    inoculation_unix: Optional[float] = None,
) -> List[TimeseriesData]:
    """
    Create TimeseriesData ORM rows from a parser's series_points list.

    When inoculation_unix is set and x_axis is hours, timestamps are stored
    as Unix seconds (controller export). ÄKTA rows keep ml on the t axis as-is.
    """
    rows: List[TimeseriesData] = []
    grouped = group_series_points(series_points)
    for param, (ts_raw, values, unit) in grouped.items():
        meta = series_metadata_for_parameter(param, file_metadata)
        if meta.get("x_axis") == "ml":
            timestamps = ts_raw
        elif inoculation_unix is not None:
            timestamps = [inoculation_unix + float(t) * 3600.0 for t in ts_raw]
        else:
            timestamps = ts_raw
        rows.append(
            TimeseriesData(
                id=str(uuid.uuid4()),
                batch_id=batch_id,
                parameter_name=param,
                unit=unit,
                timestamps=timestamps,
                values=values,
                source_instrument=source_instrument,
                series_metadata=meta,
            )
        )
    return rows
