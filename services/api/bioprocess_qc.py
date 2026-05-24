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
