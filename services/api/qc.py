"""
Quality Control module for lab data validation.

Provides comprehensive QC checks including:
- Z-score anomaly detection
- Historical drift detection
- Monotonicity and discontinuity checks
- Completeness validation
- Range validation
"""

from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import numpy as np


class QCStatus(Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class AnomalyType(Enum):
    ZSCORE = "zscore"
    DRIFT = "drift"
    DISCONTINUITY = "discontinuity"
    RANGE = "range"
    MISSING = "missing"
    MONOTONICITY = "monotonicity"


def _safe_float(value: Any) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        f = float(value)
        if np.isnan(f) or np.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _compute_stats(values: List[Any]) -> Dict[str, Any]:
    """Compute statistics for a list of values, handling nulls."""
    numeric_values = [_safe_float(v) for v in values]
    valid_values = [v for v in numeric_values if v is not None]

    n_total = len(values)
    n_valid = len(valid_values)
    n_null = n_total - n_valid
    null_pct = (n_null / n_total * 100) if n_total > 0 else 0.0

    if n_valid == 0:
        return {
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "n": n_total,
            "n_valid": n_valid,
            "null_pct": null_pct,
        }

    arr = np.array(valid_values)
    return {
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr, ddof=1)) if n_valid > 1 else 0.0,
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "n": n_total,
        "n_valid": n_valid,
        "null_pct": round(null_pct, 2),
    }


def _check_zscore_anomalies(
    values: List[Any],
    mean: float,
    std: float,
    threshold: float = 3.0
) -> List[Dict[str, Any]]:
    """Detect values that deviate more than threshold std from mean."""
    anomalies = []

    if std <= 0 or mean is None:
        return anomalies

    for i, v in enumerate(values):
        val = _safe_float(v)
        if val is None:
            continue

        zscore = abs(val - mean) / std
        if zscore > threshold:
            anomalies.append({
                "type": AnomalyType.ZSCORE.value,
                "details": {
                    "index": i,
                    "value": val,
                    "zscore": round(zscore, 3),
                    "threshold": threshold,
                }
            })

    return anomalies


def _check_drift(
    current_mean: float,
    current_std: float,
    current_n: int,
    historical: Dict[str, Any],
    threshold_std: float = 2.0
) -> Optional[Dict[str, Any]]:
    """
    Check if current batch mean has drifted from historical baseline.

    Uses a two-sample z-test approach to detect significant drift.
    """
    if current_mean is None or current_n == 0:
        return None

    hist_mean = historical.get("mean")
    hist_std = historical.get("std")
    hist_n = historical.get("n_samples", 0)

    if hist_mean is None or hist_std is None or hist_std <= 0 or hist_n == 0:
        return None

    # Standard error of the difference between means
    se_diff = np.sqrt((hist_std ** 2 / hist_n) + (current_std ** 2 / current_n)) if current_n > 0 else hist_std

    if se_diff <= 0:
        return None

    drift_magnitude = current_mean - hist_mean
    drift_zscore = abs(drift_magnitude) / se_diff

    if drift_zscore > threshold_std:
        return {
            "type": AnomalyType.DRIFT.value,
            "details": {
                "current_mean": round(current_mean, 4),
                "historical_mean": round(hist_mean, 4),
                "drift_magnitude": round(drift_magnitude, 4),
                "drift_zscore": round(drift_zscore, 3),
                "threshold": threshold_std,
                "direction": "increase" if drift_magnitude > 0 else "decrease",
            }
        }

    return None


def _check_monotonicity(
    values: List[Any],
    expected_direction: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Check for monotonicity violations and discontinuities.

    Args:
        values: List of values (typically time-series)
        expected_direction: "increasing", "decreasing", or None (auto-detect)

    Returns:
        List of anomalies for reversals and discontinuities
    """
    anomalies = []

    # Extract valid numeric values with indices
    indexed_values = []
    for i, v in enumerate(values):
        val = _safe_float(v)
        if val is not None:
            indexed_values.append((i, val))

    if len(indexed_values) < 3:
        return anomalies

    # Compute differences
    diffs = []
    for j in range(1, len(indexed_values)):
        prev_idx, prev_val = indexed_values[j - 1]
        curr_idx, curr_val = indexed_values[j]
        diffs.append((curr_idx, curr_val - prev_val))

    if not diffs:
        return anomalies

    # Auto-detect expected direction if not specified
    if expected_direction is None:
        positive_diffs = sum(1 for _, d in diffs if d > 0)
        negative_diffs = sum(1 for _, d in diffs if d < 0)
        if positive_diffs > negative_diffs * 2:
            expected_direction = "increasing"
        elif negative_diffs > positive_diffs * 2:
            expected_direction = "decreasing"
        else:
            # Not clearly monotonic, skip this check
            return anomalies

    # Check for reversals
    for idx, diff in diffs:
        if expected_direction == "increasing" and diff < 0:
            anomalies.append({
                "type": AnomalyType.MONOTONICITY.value,
                "details": {
                    "index": idx,
                    "expected": "increasing",
                    "actual_change": round(diff, 4),
                }
            })
        elif expected_direction == "decreasing" and diff > 0:
            anomalies.append({
                "type": AnomalyType.MONOTONICITY.value,
                "details": {
                    "index": idx,
                    "expected": "decreasing",
                    "actual_change": round(diff, 4),
                }
            })

    # Check for discontinuities (jumps > 3x typical step size)
    abs_diffs = [abs(d) for _, d in diffs]
    if len(abs_diffs) >= 3:
        median_step = float(np.median(abs_diffs))
        if median_step > 0:
            for idx, diff in diffs:
                if abs(diff) > 3 * median_step:
                    anomalies.append({
                        "type": AnomalyType.DISCONTINUITY.value,
                        "details": {
                            "index": idx,
                            "jump_magnitude": round(abs(diff), 4),
                            "typical_step": round(median_step, 4),
                            "ratio": round(abs(diff) / median_step, 2),
                        }
                    })

    return anomalies


def _check_completeness(
    values: List[Any],
    null_pct: float,
    expected_n: Optional[int] = None,
    null_threshold_pct: float = 10.0
) -> List[Dict[str, Any]]:
    """Check for data completeness issues."""
    anomalies = []

    # Check null percentage
    if null_pct > null_threshold_pct:
        anomalies.append({
            "type": AnomalyType.MISSING.value,
            "details": {
                "null_pct": null_pct,
                "threshold_pct": null_threshold_pct,
                "issue": "high_null_rate",
            }
        })

    # Check expected count
    if expected_n is not None and len(values) < expected_n:
        anomalies.append({
            "type": AnomalyType.MISSING.value,
            "details": {
                "actual_count": len(values),
                "expected_count": expected_n,
                "missing_count": expected_n - len(values),
                "issue": "insufficient_data_points",
            }
        })

    return anomalies


def _check_range(
    values: List[Any],
    expected_range: Dict[str, float]
) -> List[Dict[str, Any]]:
    """Check if values fall within expected instrument range."""
    anomalies = []

    range_min = expected_range.get("min")
    range_max = expected_range.get("max")

    if range_min is None and range_max is None:
        return anomalies

    for i, v in enumerate(values):
        val = _safe_float(v)
        if val is None:
            continue

        if range_min is not None and val < range_min:
            anomalies.append({
                "type": AnomalyType.RANGE.value,
                "details": {
                    "index": i,
                    "value": val,
                    "expected_min": range_min,
                    "issue": "below_range",
                }
            })
        elif range_max is not None and val > range_max:
            anomalies.append({
                "type": AnomalyType.RANGE.value,
                "details": {
                    "index": i,
                    "value": val,
                    "expected_max": range_max,
                    "issue": "above_range",
                }
            })

    return anomalies


def _determine_field_status(anomalies: List[Dict[str, Any]]) -> QCStatus:
    """Determine overall status for a field based on its anomalies."""
    if not anomalies:
        return QCStatus.PASS

    # Fail conditions: drift, high missing rate, multiple anomalies
    fail_types = {AnomalyType.DRIFT.value, AnomalyType.MISSING.value}

    for anomaly in anomalies:
        if anomaly["type"] in fail_types:
            return QCStatus.FAIL

    # Multiple z-score or range anomalies -> fail
    zscore_count = sum(1 for a in anomalies if a["type"] == AnomalyType.ZSCORE.value)
    range_count = sum(1 for a in anomalies if a["type"] == AnomalyType.RANGE.value)

    if zscore_count > 3 or range_count > 3:
        return QCStatus.FAIL

    # Any anomalies -> warn
    return QCStatus.WARN


def _generate_summary(qc_flags: Dict[str, Any], overall_status: QCStatus) -> str:
    """Generate human-readable summary of QC results."""
    if overall_status == QCStatus.PASS:
        return "All QC checks passed. No anomalies detected."

    issues = []
    for field, data in qc_flags.items():
        anomalies = data.get("anomalies", [])
        if anomalies:
            types = set(a["type"] for a in anomalies)
            issues.append(f"{field}: {', '.join(types)}")

    status_word = "warnings" if overall_status == QCStatus.WARN else "failures"
    return f"QC {status_word} detected. Issues: {'; '.join(issues)}"


def qc_summary(
    stats: Dict[str, Any],
    historical_baselines: Optional[Dict[str, Dict[str, Any]]] = None,
    expected_ranges: Optional[Dict[str, Dict[str, float]]] = None,
    expected_counts: Optional[Dict[str, int]] = None,
    monotonic_fields: Optional[Dict[str, str]] = None,
    zscore_threshold: float = 3.0,
    drift_threshold: float = 2.0,
    null_threshold_pct: float = 10.0,
) -> Dict[str, Any]:
    """
    Perform comprehensive QC analysis on lab data.

    Args:
        stats: Dict of field_name -> {"values": [...], "mean": x, "std": y}
               (mean/std optional, will be computed if missing)
        historical_baselines: Optional dict of field_name -> {"mean": x, "std": y, "n_samples": n}
        expected_ranges: Optional dict of field_name -> {"min": x, "max": y}
        expected_counts: Optional dict of field_name -> expected number of data points
        monotonic_fields: Optional dict of field_name -> "increasing" | "decreasing"
        zscore_threshold: Threshold for z-score anomaly detection (default: 3.0)
        drift_threshold: Threshold for drift detection in std units (default: 2.0)
        null_threshold_pct: Threshold for null percentage warning (default: 10.0)

    Returns:
        Dict with qc_flags, overall_status, and summary
    """
    historical_baselines = historical_baselines or {}
    expected_ranges = expected_ranges or {}
    expected_counts = expected_counts or {}
    monotonic_fields = monotonic_fields or {}

    qc_flags = {}
    field_statuses = []

    for field_name, field_data in stats.items():
        anomalies = []

        # Get values list
        values = field_data.get("values", [])

        # Compute or use provided stats
        if "mean" in field_data and "std" in field_data:
            computed_stats = {
                "mean": _safe_float(field_data.get("mean")),
                "std": _safe_float(field_data.get("std")),
                "n": len(values),
                "n_valid": len([v for v in values if _safe_float(v) is not None]),
                "null_pct": 0.0,
            }
            # Recompute null_pct
            n_valid = computed_stats["n_valid"]
            n_total = computed_stats["n"]
            computed_stats["null_pct"] = round((n_total - n_valid) / n_total * 100, 2) if n_total > 0 else 0.0
        else:
            computed_stats = _compute_stats(values)

        mean = computed_stats.get("mean")
        std = computed_stats.get("std")
        n_valid = computed_stats.get("n_valid", 0)
        null_pct = computed_stats.get("null_pct", 0.0)

        # 1. Z-score anomalies
        if mean is not None and std is not None and std > 0:
            zscore_anomalies = _check_zscore_anomalies(values, mean, std, zscore_threshold)
            anomalies.extend(zscore_anomalies)

        # 2. Historical drift detection
        if field_name in historical_baselines and mean is not None:
            drift_anomaly = _check_drift(
                current_mean=mean,
                current_std=std or 0.0,
                current_n=n_valid,
                historical=historical_baselines[field_name],
                threshold_std=drift_threshold,
            )
            if drift_anomaly:
                anomalies.append(drift_anomaly)

        # 3. Monotonicity and discontinuity checks
        if field_name in monotonic_fields or len(values) >= 10:
            direction = monotonic_fields.get(field_name)
            mono_anomalies = _check_monotonicity(values, direction)
            anomalies.extend(mono_anomalies)

        # 4. Completeness checks
        expected_n = expected_counts.get(field_name)
        completeness_anomalies = _check_completeness(
            values, null_pct, expected_n, null_threshold_pct
        )
        anomalies.extend(completeness_anomalies)

        # 5. Range validation
        if field_name in expected_ranges:
            range_anomalies = _check_range(values, expected_ranges[field_name])
            anomalies.extend(range_anomalies)

        # Determine field status
        field_status = _determine_field_status(anomalies)
        field_statuses.append(field_status)

        qc_flags[field_name] = {
            "anomalies": anomalies,
            "stats": {
                "mean": round(mean, 4) if mean is not None else None,
                "std": round(std, 4) if std is not None else None,
                "n": computed_stats.get("n", 0),
                "null_pct": null_pct,
            },
            "status": field_status.value,
        }

    # Determine overall status
    if QCStatus.FAIL in field_statuses:
        overall_status = QCStatus.FAIL
    elif QCStatus.WARN in field_statuses:
        overall_status = QCStatus.WARN
    else:
        overall_status = QCStatus.PASS

    summary = _generate_summary(qc_flags, overall_status)

    return {
        "qc_flags": qc_flags,
        "overall_status": overall_status.value,
        "summary": summary,
    }
