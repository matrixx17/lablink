"""
Align discrete offline samples onto continuous bioreactor time axis.
"""

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _normalize_time_to_hours(times: List[float], unit: str = "h") -> List[float]:
    if not times:
        return []
    arr = np.array(times, dtype=float)
    if unit == "min":
        return (arr / 60.0).tolist()
    if unit == "s":
        return (arr / 3600.0).tolist()
    return arr.tolist()


def extract_series_from_stats(
    stats: Dict[str, Any],
    time_field: Optional[str] = None,
) -> Dict[str, Tuple[List[float], List[float]]]:
    """Build field -> (times_h, values) from manifest stats."""
    result: Dict[str, Tuple[List[float], List[float]]] = {}
    tf = time_field
    if tf is None:
        for k in stats:
            if "time" in k.lower():
                tf = k
                break

    times: List[float] = []
    if tf and tf in stats:
        times = [
            float(v) for v in stats[tf].get("values", [])
            if v is not None
        ]

    for field, data in stats.items():
        if field == tf:
            continue
        vals = [
            float(v) for v in data.get("values", [])
            if v is not None
        ]
        if not vals:
            continue
        t_axis = times if len(times) == len(vals) else list(range(len(vals)))
        result[field] = (_normalize_time_to_hours(t_axis), vals)

    return result


def extract_series_from_points(
    series_points: List[Dict[str, Any]],
) -> Dict[str, Tuple[List[float], List[float]]]:
    """Aggregate series_points into per-field time series."""
    buckets: Dict[str, List[Tuple[float, float]]] = {}
    for pt in series_points:
        field = pt.get("field")
        if not field:
            continue
        try:
            t = float(pt.get("t", 0))
            v = float(pt.get("value"))
        except (TypeError, ValueError):
            continue
        buckets.setdefault(field, []).append((t, v))

    out: Dict[str, Tuple[List[float], List[float]]] = {}
    for field, pairs in buckets.items():
        pairs.sort(key=lambda x: x[0])
        out[field] = (
            _normalize_time_to_hours([p[0] for p in pairs]),
            [p[1] for p in pairs],
        )
    return out


def align_run_series(
    continuous: Dict[str, Tuple[List[float], List[float]]],
    discrete: Dict[str, Tuple[List[float], List[float]]],
    run_start_h: float = 0.0,
) -> Dict[str, Any]:
    """
    Place discrete offline measurements on the continuous run timeline.

    Returns alignment metadata plus merged overlay series for dashboard.
    """
    if not continuous and not discrete:
        return {"aligned_fields": [], "overlays": {}, "notes": "no series to align"}

    # Reference timeline from longest continuous series
    ref_times: List[float] = []
    for times, _ in continuous.values():
        if len(times) > len(ref_times):
            ref_times = times

    if not ref_times and discrete:
        ref_times = sorted({t for times, _ in discrete.values() for t in times})

    overlays: Dict[str, Any] = {}
    aligned_fields: List[Dict[str, Any]] = []

    for field, (d_times, d_vals) in discrete.items():
        aligned = []
        for t, v in zip(d_times, d_vals):
            aligned.append({
                "time_h": round(t + run_start_h, 4),
                "value": v,
                "source": "discrete_offline",
            })
            if ref_times:
                idx = int(np.argmin([abs(rt - t) for rt in ref_times]))
                overlays.setdefault(field, []).append({
                    "discrete_time_h": t,
                    "nearest_continuous_time_h": ref_times[idx],
                    "delta_h": round(ref_times[idx] - t, 4),
                    "value": v,
                })

        aligned_fields.append({
            "field": field,
            "point_count": len(d_vals),
            "time_range_h": [min(d_times), max(d_times)] if len(d_times) > 0 else [],
            "samples": aligned,
        })

    return {
        "reference_timeline_h": {
            "start": ref_times[0] if ref_times else None,
            "end": ref_times[-1] if ref_times else None,
            "point_count": len(ref_times),
        },
        "continuous_fields": list(continuous.keys()),
        "discrete_fields": list(discrete.keys()),
        "aligned_fields": aligned_fields,
        "overlays": overlays,
        "run_start_h": run_start_h,
    }
