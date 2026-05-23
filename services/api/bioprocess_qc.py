"""
Bioprocess-domain QC rules layered on the generic QC engine.

Translates statistical anomalies into process-scientist actionable findings.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from qc import qc_summary, QCStatus

BIOPROCESS_INSTRUMENTS = {
    "sartorius_biostat", "sartorius_ambr", "eppendorf_bioflo", "cytiva_bioreactor",
    "nova_bioprofile", "beckman_vicell", "bioprocess_offline", "agilent_chemstation",
}

FIELD_ALIASES = {
    "vcd": ["vcd", "viable cells", "viable cell density", "viable cells/ml"],
    "viability": ["viability", "viability (%)"],
    "ph": ["ph", "pH"],
    "do": ["do", "do [%]", "dissolved oxygen", "po2", "pO2"],
    "titer": ["titer", "titer (g/l)", "concentration"],
    "glucose": ["glucose", "glucose (g/l)"],
    "temperature": ["temperature", "temp", "temperature (c)"],
}


def _find_field(stats: Dict[str, Any], canonical: str) -> Optional[str]:
    aliases = FIELD_ALIASES.get(canonical, [canonical])
    for key in stats:
        kl = key.lower()
        for alias in aliases:
            if alias.lower() in kl or kl == alias.lower():
                return key
    return None


def _values(stats: Dict[str, Any], field: str) -> List[float]:
    raw = stats.get(field, {}).get("values", [])
    out = []
    for v in raw:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def _times(stats: Dict[str, Any], time_field: Optional[str]) -> List[float]:
    if not time_field:
        return list(range(len(_values(stats, list(stats.keys())[0])))) if stats else []
    return _values(stats, time_field)


def check_vcd_growth_profile(vcd_values: List[float], times: List[float]) -> List[Dict[str, Any]]:
    """Expect growth phase then plateau/decline — flag flat-line or early crash."""
    findings = []
    if len(vcd_values) < 5:
        return findings

    arr = np.array(vcd_values)
    peak_idx = int(np.argmax(arr))
    peak_frac = peak_idx / max(len(arr) - 1, 1)

    if peak_frac < 0.2 and arr[-1] < arr[0] * 0.5:
        findings.append({
            "rule": "vcd_early_crash",
            "severity": "fail",
            "message": "Viable cell density peaked in first 20% of run then fell below 50% of start.",
            "peak_time_fraction": round(peak_frac, 2),
        })
    elif peak_frac > 0.85:
        findings.append({
            "rule": "vcd_no_decline",
            "severity": "warn",
            "message": "VCD still rising at end of run — expected plateau/decline not observed.",
        })

    mid = len(arr) // 2
    if mid > 2 and np.std(arr[:mid]) < 0.01 * max(np.mean(arr[:mid]), 1e-6):
        findings.append({
            "rule": "vcd_stalled_growth",
            "severity": "warn",
            "message": "Flat viable cell density in first half of run (stalled growth).",
        })

    return findings


def check_do_ph_setpoint_excursion(
    values: List[float],
    times: List[float],
    setpoint: float,
    field_label: str,
    tolerance: float = 0.5,
    min_duration_h: float = 0.1,
) -> List[Dict[str, Any]]:
    """Flag sustained excursions below/above setpoint (e.g. DO crash)."""
    findings = []
    if len(values) < 3:
        return findings

    below = [i for i, v in enumerate(values) if v < setpoint - tolerance]
    if not below:
        return findings

    # Longest consecutive excursion
    longest = 1
    current = 1
    start_idx = below[0]
    best_start = start_idx
    best_len = 1

    for j in range(1, len(below)):
        if below[j] == below[j - 1] + 1:
            current += 1
            if current > longest:
                longest = current
                best_start = start_idx
                best_len = current
        else:
            start_idx = below[j]
            current = 1

    if len(times) >= 2:
        dt = abs(times[min(best_start + best_len, len(times) - 1)] - times[best_start])
    else:
        dt = best_len

    if dt >= min_duration_h or best_len >= 10:
        findings.append({
            "rule": f"{field_label}_setpoint_excursion",
            "severity": "fail" if field_label == "do" else "warn",
            "message": (
                f"{field_label.upper()} below setpoint {setpoint} for ~{round(dt, 2)} h "
                f"(tolerance ±{tolerance})."
            ),
            "setpoint": setpoint,
            "duration_h": round(dt, 3),
            "min_value": round(min(values[i] for i in below), 3),
        })

    return findings


def check_titer_trajectory(titer_values: List[float], times: List[float]) -> List[Dict[str, Any]]:
    """Offline titer should generally increase over the culture."""
    findings = []
    if len(titer_values) < 3:
        return findings

    arr = np.array(titer_values)
    if arr[-1] < arr[0] * 0.9:
        findings.append({
            "rule": "titer_declining",
            "severity": "warn",
            "message": "Titer decreased from first to last sample — unexpected for production run.",
        })
    elif arr[-1] <= arr[0] * 1.05:
        findings.append({
            "rule": "titer_flat",
            "severity": "warn",
            "message": "Titer did not meaningfully increase across offline samples.",
        })

    return findings


def bioprocess_qc_summary(
    stats: Dict[str, Any],
    instrument: Optional[str] = None,
    historical_baselines: Optional[Dict[str, Dict[str, Any]]] = None,
    setpoints: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Run generic QC then apply bioprocess domain rules.
    """
    setpoints = setpoints or {"ph": 7.0, "do": 30.0}

    vcd_field = _find_field(stats, "vcd")
    ph_field = _find_field(stats, "ph")
    do_field = _find_field(stats, "do")
    titer_field = _find_field(stats, "titer")
    time_field = _find_field(stats, "time") or next(
        (k for k in stats if "time" in k.lower()), None
    )

    monotonic = {}
    if titer_field:
        monotonic[titer_field] = "increasing"
    if vcd_field:
        monotonic[vcd_field] = "increasing"

    base = qc_summary(
        stats=stats,
        historical_baselines=historical_baselines,
        monotonic_fields=monotonic,
    )

    domain_findings: List[Dict[str, Any]] = []
    times = _times(stats, time_field) if time_field else []

    if vcd_field:
        domain_findings.extend(
            check_vcd_growth_profile(_values(stats, vcd_field), times or list(range(len(_values(stats, vcd_field)))))
        )

    if do_field:
        domain_findings.extend(
            check_do_ph_setpoint_excursion(
                _values(stats, do_field),
                times or list(range(len(_values(stats, do_field)))),
                setpoint=setpoints.get("do", 30.0),
                field_label="do",
                tolerance=5.0,
            )
        )

    if ph_field:
        domain_findings.extend(
            check_do_ph_setpoint_excursion(
                _values(stats, ph_field),
                times or list(range(len(_values(stats, ph_field)))),
                setpoint=setpoints.get("ph", 7.0),
                field_label="ph",
                tolerance=0.3,
            )
        )

    if titer_field:
        domain_findings.extend(
            check_titer_trajectory(_values(stats, titer_field), times)
        )

    base["domain_findings"] = domain_findings
    base["qc_mode"] = "bioprocess"

    if domain_findings:
        severities = [f.get("severity") for f in domain_findings]
        if "fail" in severities:
            base["overall_status"] = QCStatus.FAIL.value
        elif base["overall_status"] == QCStatus.PASS.value and "warn" in severities:
            base["overall_status"] = QCStatus.WARN.value

        domain_msgs = "; ".join(f["message"] for f in domain_findings[:3])
        base["summary"] = f"{base['summary']} Bioprocess: {domain_msgs}"

    return base


def is_bioprocess_instrument(instrument: Optional[str]) -> bool:
    if not instrument:
        return False
    key = instrument.lower().replace("-", "_")
    return key in BIOPROCESS_INSTRUMENTS or "bioprocess" in key or "biostat" in key


# ============================================================================
# Batch-level QC engine
# ----------------------------------------------------------------------------
# A higher-level interface that loads a Batch's continuous + offline data
# from the DB, runs the 10 checks defined in the wet lab spec, and returns
# a list of `QCResult` objects suitable for direct JSON return. The
# existing `bioprocess_qc_summary` stays as-is for the file-level ingest
# path.
# ============================================================================

from dataclasses import dataclass, asdict
from typing import Iterable
from collections import Counter


@dataclass
class QCResult:
    """One QC outcome for the batch-level engine.

    Keep this serialisable as a dict via `asdict()` for the API endpoint.
    """
    check_name: str
    status: str            # "pass" | "warn" | "fail"
    message: str
    numeric_value: Optional[float] = None
    timepoint_h: Optional[float] = None
    parameter: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Severity ordering: worst wins for the batch-level rollup.
_SEVERITY_RANK = {"pass": 0, "warn": 1, "fail": 2}


def _worst(statuses: Iterable[str]) -> str:
    best = "pass"
    for s in statuses:
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK.get(best, 0):
            best = s
    return best


# ---------------------------------------------------------------------------
# Internal helpers shared by checks
# ---------------------------------------------------------------------------

def _series_to_arrays(values: List[float], timestamps_unix: List[float]) -> tuple:
    """
    Convert wall-clock unix timestamps to hours-since-first, drop NaNs.
    Tolerates JSON-string payloads (SQLite stores ARRAY as a JSON string).
    """
    if not values or not timestamps_unix:
        return [], []
    # SQLite ARRAY -> JSON: the driver may return a string (single column
    # value) OR a list of characters (when SQLAlchemy iterates the string
    # via the ARRAY adapter). Handle both by joining and json.loads.
    import json as _json

    def _coerce(seq):
        if isinstance(seq, str):
            try:
                return _json.loads(seq)
            except Exception:
                return []
        # Detect "list of characters that form a JSON array" — first non-
        # empty element is a single character like '[' or '{'.
        if isinstance(seq, list) and seq and isinstance(seq[0], str) and len(seq[0]) == 1:
            joined = "".join(seq)
            try:
                return _json.loads(joined)
            except Exception:
                return []
        return seq

    values = _coerce(values)
    timestamps_unix = _coerce(timestamps_unix)
    if not values or not timestamps_unix:
        return [], []

    paired = []
    for t, v in zip(timestamps_unix, values):
        if v is None or t is None:
            continue
        try:
            t_f = float(t)
            v_f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isnan(v_f) or np.isnan(t_f):
            continue
        paired.append((t_f, v_f))
    if not paired:
        return [], []
    paired.sort(key=lambda p: p[0])
    t0 = paired[0][0]
    return (
        [v for _, v in paired],
        [(t - t0) / 3600.0 for t, _ in paired],
    )


def _estimate_setpoint(values: List[float], precision: float) -> Optional[float]:
    """Estimate a control setpoint as the most common value at the given precision."""
    if not values:
        return None
    rounded = [round(v / precision) * precision for v in values]
    most_common, _ = Counter(rounded).most_common(1)[0]
    return float(most_common)


def _duration_minutes(hours: List[float], i_start: int, i_end: int) -> float:
    """Convert an [i_start..i_end] index range into minutes."""
    if i_end < i_start or i_end >= len(hours):
        return 0.0
    return (hours[i_end] - hours[i_start]) * 60.0


def _find_sustained_excursion(
    values: List[float],
    hours: List[float],
    predicate,
    min_minutes: float,
) -> Optional[tuple]:
    """
    Walk values; find first contiguous run where predicate(v) is True for
    ≥ min_minutes. Returns (start_index, end_index, duration_min, extreme_value)
    or None.
    """
    n = len(values)
    i = 0
    while i < n:
        if not predicate(values[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and predicate(values[j + 1]):
            j += 1
        dur = _duration_minutes(hours, i, j)
        if dur >= min_minutes:
            extreme = min(values[i:j + 1]) if predicate(values[i]) else values[i]
            return i, j, dur, extreme
        i = j + 1
    return None


# ---------------------------------------------------------------------------
# Continuous-data checks
# ---------------------------------------------------------------------------

def _check_ph_excursion(values: List[float], hours: List[float]) -> List[QCResult]:
    if not values:
        return []
    setpoint = _estimate_setpoint(values, precision=0.05) or float(np.median(values))
    results: List[QCResult] = []

    # Below setpoint
    below_warn = _find_sustained_excursion(
        values, hours, lambda v: v < setpoint - 0.2, min_minutes=30.0,
    )
    below_fail = _find_sustained_excursion(
        values, hours, lambda v: v < setpoint - 0.5, min_minutes=30.0,
    )
    # Above setpoint
    above_warn = _find_sustained_excursion(
        values, hours, lambda v: v > setpoint + 0.2, min_minutes=30.0,
    )
    above_fail = _find_sustained_excursion(
        values, hours, lambda v: v > setpoint + 0.5, min_minutes=30.0,
    )

    chosen = None
    status = "pass"
    if below_fail or above_fail:
        chosen = below_fail or above_fail
        status = "fail"
    elif below_warn or above_warn:
        chosen = below_warn or above_warn
        status = "warn"

    if chosen and status != "pass":
        i_start, _, dur, extreme = chosen
        results.append(QCResult(
            check_name="ph_excursion",
            status=status,
            message=f"pH excursion: {dur:.0f} min at pH {extreme:.2f}, setpoint {setpoint:.2f}",
            numeric_value=float(extreme),
            timepoint_h=float(hours[i_start]),
            parameter="ph",
        ))
    else:
        results.append(QCResult(
            check_name="ph_excursion", status="pass",
            message=f"pH controlled within ±0.2 of setpoint {setpoint:.2f}",
            parameter="ph",
        ))
    return results


def _check_do_crash(values: List[float], hours: List[float]) -> List[QCResult]:
    if not values:
        return []
    fail = _find_sustained_excursion(values, hours, lambda v: v < 10.0, min_minutes=5.0)
    warn = _find_sustained_excursion(values, hours, lambda v: v < 20.0, min_minutes=15.0)
    if fail:
        i_start, _, dur, nadir = fail
        return [QCResult(
            check_name="do_crash", status="fail",
            message=f"DO crash: {dur:.0f} min below 10% (nadir {nadir:.1f}%)",
            numeric_value=float(nadir), timepoint_h=float(hours[i_start]),
            parameter="do_percent",
        )]
    if warn:
        i_start, _, dur, nadir = warn
        return [QCResult(
            check_name="do_crash", status="warn",
            message=f"DO dip: {dur:.0f} min below 20% (nadir {nadir:.1f}%)",
            numeric_value=float(nadir), timepoint_h=float(hours[i_start]),
            parameter="do_percent",
        )]
    return [QCResult(
        check_name="do_crash", status="pass",
        message="DO held above 20% throughout the run",
        parameter="do_percent",
    )]


def _check_temperature_stability(values: List[float], hours: List[float]) -> List[QCResult]:
    if not values:
        return []
    setpoint = _estimate_setpoint(values, precision=0.1) or float(np.median(values))
    fail = _find_sustained_excursion(
        values, hours, lambda v: abs(v - setpoint) > 1.0, min_minutes=30.0,
    )
    warn = _find_sustained_excursion(
        values, hours, lambda v: abs(v - setpoint) > 0.5, min_minutes=30.0,
    )
    if fail:
        i, _, dur, extreme = fail
        return [QCResult(
            check_name="temperature_stability", status="fail",
            message=f"Temperature drift >1°C for {dur:.0f} min (extreme {extreme:.2f}°C, setpoint {setpoint:.2f}°C)",
            numeric_value=float(extreme), timepoint_h=float(hours[i]),
            parameter="temperature_c",
        )]
    if warn:
        i, _, dur, extreme = warn
        return [QCResult(
            check_name="temperature_stability", status="warn",
            message=f"Temperature drift >0.5°C for {dur:.0f} min (extreme {extreme:.2f}°C, setpoint {setpoint:.2f}°C)",
            numeric_value=float(extreme), timepoint_h=float(hours[i]),
            parameter="temperature_c",
        )]
    return [QCResult(
        check_name="temperature_stability", status="pass",
        message=f"Temperature held within ±0.5°C of setpoint {setpoint:.2f}°C",
        parameter="temperature_c",
    )]


def _check_agitation_ramp(values: List[float], hours: List[float]) -> List[QCResult]:
    if not values:
        return []
    # Anything below 50 RPM after the first 2 h is suspicious.
    after_2h = [(t, v) for t, v in zip(hours, values) if t >= 2.0]
    if not after_2h:
        return [QCResult(
            check_name="agitation_ramp", status="pass",
            message="Agitation profile within bounds (insufficient data after t=2h)",
            parameter="agitation_rpm",
        )]
    suspicious = next(((t, v) for t, v in after_2h if v < 50.0), None)
    if suspicious:
        t, v = suspicious
        return [QCResult(
            check_name="agitation_ramp", status="warn",
            message=f"Agitation dropped to {v:.0f} RPM at t={t:.1f}h (possible sensor dropout or stop)",
            numeric_value=float(v), timepoint_h=float(t),
            parameter="agitation_rpm",
        )]
    return [QCResult(
        check_name="agitation_ramp", status="pass",
        message="Agitation held above 50 RPM after t=2h",
        parameter="agitation_rpm",
    )]


def _check_data_completeness(
    parameter: str, values: List[float], hours: List[float],
) -> List[QCResult]:
    if len(hours) < 3:
        return [QCResult(
            check_name="data_completeness", status="warn",
            message=f"{parameter}: only {len(hours)} datapoints recorded",
            parameter=parameter,
        )]
    diffs = np.diff(hours)
    median_step = float(np.median(diffs))
    if median_step <= 0:
        return []
    gap_threshold = 3.0 * median_step
    expected = (hours[-1] - hours[0]) / median_step + 1
    actual = len(hours)
    missing_pct = max(0.0, (expected - actual) / expected) * 100.0
    big_gaps = int(np.sum(diffs > gap_threshold))
    missing_pct = max(missing_pct, big_gaps / max(actual, 1) * 100.0)

    if missing_pct > 20.0:
        status = "fail"
    elif missing_pct > 5.0:
        status = "warn"
    else:
        status = "pass"
    return [QCResult(
        check_name="data_completeness", status=status,
        message=f"{parameter}: {missing_pct:.1f}% missing datapoints (median step {median_step * 60:.1f} min)",
        numeric_value=float(missing_pct), parameter=parameter,
    )]


# ---------------------------------------------------------------------------
# Offline-sample checks
# ---------------------------------------------------------------------------

def _check_vcd_growth_curve_shape(
    vcd_values: List[float], times_h: List[float],
) -> List[QCResult]:
    """Wrap the existing check_vcd_growth_profile dict output as QCResult.

    Additionally fail outright if VCD never exceeds 2x the inoculation
    density — suggests a failed culture even if the legacy helper only
    flags a 'still rising' warning.
    """
    if not vcd_values:
        return []

    inoculum = vcd_values[0]
    peak = max(vcd_values)
    if inoculum > 0 and peak < 2.0 * inoculum:
        return [QCResult(
            check_name="vcd_growth_curve_shape", status="fail",
            message=(
                f"VCD never doubled inoculation density "
                f"(peak {peak:.1f} ≤ 2× inoculum {inoculum:.1f}). "
                "Failed culture."
            ),
            numeric_value=float(peak), parameter="vcd_e6_per_ml",
        )]

    findings = check_vcd_growth_profile(vcd_values, times_h)
    if not findings:
        return [QCResult(
            check_name="vcd_growth_curve_shape", status="pass",
            message=f"VCD growth profile healthy (peak {peak:.1f}×10⁶/mL)",
            parameter="vcd_e6_per_ml",
        )]
    worst = "warn"
    for f in findings:
        if f.get("severity") == "fail":
            worst = "fail"
            break
    f0 = findings[0]
    return [QCResult(
        check_name="vcd_growth_curve_shape", status=worst,
        message=f0.get("message", "VCD growth anomaly"),
        parameter="vcd_e6_per_ml",
    )]


def _check_viability_trajectory(
    viability: List[float], times_h: List[float],
) -> List[QCResult]:
    if not viability or not times_h:
        return []
    run_duration = max(times_h)
    cutoff = run_duration * 0.7
    final_window = run_duration * 0.8

    fail = next(
        ((t, v) for t, v in zip(times_h, viability) if v < 50.0 and t < final_window),
        None,
    )
    if fail:
        t, v = fail
        return [QCResult(
            check_name="viability_trajectory", status="fail",
            message=f"Viability dropped to {v:.1f}% at t={t:.1f}h (mid-run)",
            numeric_value=float(v), timepoint_h=float(t),
            parameter="viability_percent",
        )]
    warn = next(((t, v) for t, v in zip(times_h, viability) if v < 70.0 and t < cutoff), None)
    if warn:
        t, v = warn
        return [QCResult(
            check_name="viability_trajectory", status="warn",
            message=f"Viability dropped below 70% (to {v:.1f}%) at t={t:.1f}h, before 70% of run",
            numeric_value=float(v), timepoint_h=float(t),
            parameter="viability_percent",
        )]
    return [QCResult(
        check_name="viability_trajectory", status="pass",
        message=f"Viability held above 70% through {cutoff:.0f}h",
        parameter="viability_percent",
    )]


def _check_titer_monotonicity(
    titer: List[float], times_h: List[float],
) -> List[QCResult]:
    if len(titer) < 2:
        return []
    findings = check_titer_trajectory(titer, times_h)
    # Additionally scan for >10% / >30% drops between consecutive samples.
    worst = "pass"
    worst_msg = "Titer non-decreasing"
    for i in range(1, len(titer)):
        prev, cur = titer[i - 1], titer[i]
        if prev <= 0:
            continue
        drop = (prev - cur) / prev
        if drop > 0.3 and worst != "fail":
            worst = "fail"
            worst_msg = f"Titer dropped {drop * 100:.0f}% between {times_h[i-1]:.0f}h and {times_h[i]:.0f}h ({prev:.0f}→{cur:.0f} mg/L)"
        elif drop > 0.10 and worst == "pass":
            worst = "warn"
            worst_msg = f"Titer dipped {drop * 100:.0f}% between {times_h[i-1]:.0f}h and {times_h[i]:.0f}h"
    # If existing helper raised anything, escalate
    if findings and worst != "fail":
        worst = "warn"
        worst_msg = findings[0].get("message", worst_msg)
    return [QCResult(
        check_name="titer_monotonicity", status=worst, message=worst_msg,
        parameter="titer_mg_per_l",
    )]


def _check_glucose_depletion(
    glucose: List[float], times_h: List[float],
) -> List[QCResult]:
    if not glucose:
        return []
    run_duration = max(times_h) if times_h else 0.0
    final_window = run_duration * 0.9
    # Fail if glucose reaches 0 before the last 10% of the run.
    zero = next(
        ((t, v) for t, v in zip(times_h, glucose) if v <= 0.01 and t < final_window),
        None,
    )
    if zero:
        t, v = zero
        return [QCResult(
            check_name="glucose_depletion", status="fail",
            message=f"Glucose depleted to {v:.2f} g/L at t={t:.0f}h (before 90% of run)",
            numeric_value=float(v), timepoint_h=float(t),
            parameter="glucose_g_per_l",
        )]
    # Warn at <0.5 g/L anywhere
    low = next(((t, v) for t, v in zip(times_h, glucose) if v < 0.5), None)
    if low:
        t, v = low
        return [QCResult(
            check_name="glucose_depletion", status="warn",
            message=f"Glucose dropped to {v:.2f} g/L at t={t:.0f}h (starvation risk)",
            numeric_value=float(v), timepoint_h=float(t),
            parameter="glucose_g_per_l",
        )]
    return [QCResult(
        check_name="glucose_depletion", status="pass",
        message=f"Glucose held above 0.5 g/L (min {min(glucose):.2f})",
        parameter="glucose_g_per_l",
    )]


def _check_metabolic_consistency(
    glucose: List[float], lactate: List[float], times_h: List[float],
) -> List[QCResult]:
    """Lactate-to-glucose yield ratio (Ylac/glc). >1.2 sustained = Warburg-style stress."""
    if len(glucose) < 2 or len(lactate) < 2 or len(glucose) != len(lactate):
        return []
    ratios: List[float] = []
    for i in range(1, len(glucose)):
        dglc = glucose[i - 1] - glucose[i]      # consumed (positive when consumed)
        dlac = lactate[i] - lactate[i - 1]      # produced
        if dglc <= 0.05:                         # skip feed events or noise
            continue
        # Mass-to-mole: glucose 180 g/mol, lactate 90 g/mol → ratio of masses
        # × (180/90) = 2 gives mol/mol. Equivalent: (Δlac / Δglc) * 2.
        ratio = (dlac / dglc) * 2.0
        if ratio > 0:
            ratios.append(ratio)
    if not ratios:
        return [QCResult(
            check_name="metabolic_consistency", status="pass",
            message="Insufficient glucose-consumption windows for Ylac/glc",
            parameter="metabolic",
        )]
    mean_ratio = float(np.mean(ratios))
    if mean_ratio > 1.2:
        return [QCResult(
            check_name="metabolic_consistency", status="warn",
            message=f"Elevated lactate/glucose ratio: {mean_ratio:.2f} mol/mol (Warburg-style stress)",
            numeric_value=mean_ratio, parameter="metabolic",
        )]
    return [QCResult(
        check_name="metabolic_consistency", status="pass",
        message=f"Lactate/glucose ratio {mean_ratio:.2f} mol/mol (normal range)",
        numeric_value=mean_ratio, parameter="metabolic",
    )]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class BioprocessQCEngine:
    """
    Run the wet-lab QC suite against a batch's persisted data.

    Use:
        results = BioprocessQCEngine.run_for_batch(db, batch_id)

    `run_for_batch` also writes the overall worst-status into
    `batch.extra_params["qc_status"]` and the full result list into
    `batch.extra_params["qc_results"]` (list of dicts) so callers can
    retrieve them cheaply without re-running the engine.
    """

    @staticmethod
    def run_for_batch(db, batch_id: str) -> List[QCResult]:
        # Imports here so this module stays importable from the worker
        # tier without pulling SQLAlchemy at module load.
        from database import Batch, TimeseriesData, OfflineSample

        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return []

        results: List[QCResult] = []

        # ----- Continuous --------------------------------------------------
        ts_rows = (
            db.query(TimeseriesData)
            .filter(TimeseriesData.batch_id == batch_id)
            .all()
        )
        by_param: Dict[str, TimeseriesData] = {r.parameter_name: r for r in ts_rows}

        def _load(name: str):
            r = by_param.get(name)
            if r is None:
                return [], []
            vals = r.values
            ts = r.timestamps
            return _series_to_arrays(vals, ts)

        if "ph" in by_param:
            values, hours = _load("ph")
            results += _check_ph_excursion(values, hours)
            results += _check_data_completeness("ph", values, hours)
        if "do_percent" in by_param:
            values, hours = _load("do_percent")
            results += _check_do_crash(values, hours)
        if "temperature_c" in by_param:
            values, hours = _load("temperature_c")
            results += _check_temperature_stability(values, hours)
        if "agitation_rpm" in by_param:
            values, hours = _load("agitation_rpm")
            results += _check_agitation_ramp(values, hours)

        # ----- Offline --------------------------------------------------
        sample_rows = (
            db.query(OfflineSample)
            .filter(OfflineSample.batch_id == batch_id)
            .order_by(OfflineSample.sample_time_hours.asc())
            .all()
        )
        by_meas: Dict[str, List[OfflineSample]] = {}
        for s in sample_rows:
            by_meas.setdefault(s.measurement_name, []).append(s)

        def _vt(name: str) -> tuple:
            rows = by_meas.get(name) or []
            v = [r.value for r in rows if r.value is not None]
            t = [r.sample_time_hours or 0.0 for r in rows if r.value is not None]
            return v, t

        vcd_v, vcd_t = _vt("vcd_e6_per_ml")
        if not vcd_v:
            # try legacy name
            vcd_v, vcd_t = _vt("viable_cell_density_e6_per_ml")
        results += _check_vcd_growth_curve_shape(vcd_v, vcd_t)

        via_v, via_t = _vt("viability_percent")
        results += _check_viability_trajectory(via_v, via_t)

        tit_v, tit_t = _vt("titer_mg_per_l")
        results += _check_titer_monotonicity(tit_v, tit_t)

        glc_v, glc_t = _vt("glucose_g_per_l")
        results += _check_glucose_depletion(glc_v, glc_t)

        lac_v, lac_t = _vt("lactate_g_per_l")
        # Align: if glucose & lactate share timepoints, pass aligned arrays
        if glc_v and lac_v and glc_t == lac_t:
            results += _check_metabolic_consistency(glc_v, lac_v, glc_t)

        # ----- Persist rollup --------------------------------------------
        overall = _worst(r.status for r in results) if results else "pass"
        extra = dict(batch.extra_params or {})
        extra["qc_status"] = overall
        extra["qc_results"] = [r.to_dict() for r in results]
        batch.extra_params = extra
        try:
            db.commit()
        except Exception:
            db.rollback()

        return results

    @staticmethod
    def serialize_results(results: List[QCResult]) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in results]
