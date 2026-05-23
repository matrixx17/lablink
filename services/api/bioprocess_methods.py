"""
Wet lab methods-section generator.

Produces a publication-ready paragraph set for a wet lab campaign by
mining its batches' bioreactor metadata, offline-sample parameter
presence, and any chromatography (ÄKTA) timeseries that carry the
`metadata.x_axis == "ml"` marker.

Same shape as the comp-chem `compchem_methods` module so the dashboard
can render either with a single UI page.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from database import Batch, Campaign, OfflineSample, TimeseriesData

# Tone of voice: present tense, terse, no marketing.


def generate_methods(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Build the methods doc for a wet lab campaign.

    Returns:
        {
          "campaign_id": str,
          "generated_at": iso-8601,
          "domain": "wetlab",
          "paragraphs": {bioreactor, offline, chromatography},
          "full_text": "\n\n".join(non-empty paragraphs),
          "missing_fields": [str, ...],
          "instrument_summary": {bioreactor_models: [...], cell_lines: [...], ...},
        }
    """
    batches = (
        db.query(Batch)
        .filter(Batch.campaign_id == campaign.id)
        .order_by(Batch.batch_number)
        .all()
    )
    batch_ids = [b.id for b in batches]

    offline_samples: List[OfflineSample] = []
    timeseries: List[TimeseriesData] = []
    if batch_ids:
        offline_samples = (
            db.query(OfflineSample).filter(OfflineSample.batch_id.in_(batch_ids)).all()
        )
        timeseries = (
            db.query(TimeseriesData).filter(TimeseriesData.batch_id.in_(batch_ids)).all()
        )

    missing: List[str] = []

    bioreactor_para = _bioreactor_paragraph(batches, timeseries, missing)
    offline_para = _offline_paragraph(offline_samples, missing)
    chrom_para = _chromatography_paragraph(timeseries, missing)

    paragraphs = {
        "bioreactor": bioreactor_para,
        "offline": offline_para,
        "chromatography": chrom_para,
    }
    # Drop any empty paragraphs from the full-text concatenation.
    full_text = "\n\n".join(p for p in paragraphs.values() if p)

    return {
        "campaign_id": str(campaign.id),
        "campaign_name": campaign.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain": "wetlab",
        "paragraphs": paragraphs,
        "full_text": full_text,
        "missing_fields": missing,
        "instrument_summary": _instrument_summary(batches, offline_samples, timeseries),
    }


# ---------------------------------------------------------------------- helpers


def _mode(values: List[Any]) -> Optional[Any]:
    """Most common non-null value or None."""
    cleaned = [v for v in values if v is not None and v != ""]
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def _placeholder(field: str, missing: List[str]) -> str:
    if field not in missing:
        missing.append(field)
    return "[not recorded]"


def _continuous_mode_for(
    timeseries: List[TimeseriesData], parameter: str,
) -> Optional[float]:
    """Estimate the controller setpoint for `parameter` as the mode of its
    values across all batches at 0.05 precision."""
    # NB: ARRAY columns may come back as JSON strings on SQLite; tolerate.
    import json as _json
    all_vals: List[float] = []
    for r in timeseries:
        if r.parameter_name != parameter:
            continue
        vals = r.values
        if isinstance(vals, str):
            try:
                vals = _json.loads(vals)
            except Exception:
                continue
        if isinstance(vals, list) and vals and isinstance(vals[0], str) and len(vals[0]) == 1:
            try:
                vals = _json.loads("".join(vals))
            except Exception:
                continue
        for v in vals or []:
            try:
                all_vals.append(float(v))
            except (TypeError, ValueError):
                continue
    if not all_vals:
        return None
    rounded = [round(v / 0.05) * 0.05 for v in all_vals]
    return float(Counter(rounded).most_common(1)[0][0])


# ---------------------------------------------------------------------- paragraphs


def _bioreactor_paragraph(
    batches: List[Batch],
    timeseries: List[TimeseriesData],
    missing: List[str],
) -> str:
    if not batches:
        return ""

    volume_mode = _mode([b.volume_liters for b in batches])
    bioreactor_mode = _mode([b.bioreactor_model for b in batches])
    cell_mode = _mode([b.cell_line for b in batches])
    media_mode = _mode([b.media for b in batches])

    # Run duration: median of (harvest - inoculation) in days
    durations: List[float] = []
    for b in batches:
        if b.harvest_date and b.inoculation_date:
            delta = b.harvest_date - b.inoculation_date
            durations.append(delta.total_seconds() / 86400.0)
    duration_str = (
        f"{int(round(sum(durations) / len(durations)))}" if durations else None
    )

    # Setpoints from continuous data (mode estimator)
    ph_setpoint = _continuous_mode_for(timeseries, "ph")
    do_setpoint = _continuous_mode_for(timeseries, "do_percent")
    temp_setpoint = _continuous_mode_for(timeseries, "temperature_c")

    # Also accept explicit extra_params.ph_setpoint when present
    if ph_setpoint is None:
        ep = [(b.extra_params or {}).get("ph_setpoint") for b in batches]
        ph_setpoint = _mode([v for v in ep if v is not None])

    def fmt(v: Optional[float], digits: int = 1) -> str:
        return f"{v:.{digits}f}" if v is not None else None  # type: ignore[return-value]

    bioreactor_str = bioreactor_mode or _placeholder("bioreactor_model", missing)
    volume_str = f"{volume_mode:g}" if volume_mode is not None else _placeholder(
        "volume_liters", missing
    )
    cell_str = cell_mode or _placeholder("cell_line", missing)
    media_str = media_mode or _placeholder("media", missing)
    duration_str = duration_str or _placeholder("run_duration", missing)
    ph_str = fmt(ph_setpoint, 2) if ph_setpoint is not None else _placeholder(
        "ph_setpoint", missing
    )
    do_str = fmt(do_setpoint, 0) if do_setpoint is not None else _placeholder(
        "do_setpoint", missing
    )
    temp_str = fmt(temp_setpoint, 1) if temp_setpoint is not None else _placeholder(
        "temperature_setpoint", missing
    )

    return (
        f"Fed-batch cell culture was performed in a {volume_str} L {bioreactor_str} "
        f"bioreactor. {cell_str} cells were cultured in {media_str} medium for "
        f"{duration_str} days. pH was controlled to {ph_str} ± 0.05, dissolved "
        f"oxygen was maintained above {do_str}% air saturation, and temperature "
        f"was held at {temp_str}°C. A total of {len(batches)} independent "
        f"bioreactor runs were performed."
    )


def _offline_paragraph(
    samples: List[OfflineSample], missing: List[str],
) -> str:
    if not samples:
        return ""

    present_params = {s.measurement_name for s in samples}
    # Pick a representative instrument per measurement.
    instrument_by_param: Dict[str, Optional[str]] = {}
    for s in samples:
        if s.instrument and s.measurement_name not in instrument_by_param:
            instrument_by_param[s.measurement_name] = s.instrument

    sentences: List[str] = []

    if "vcd_e6_per_ml" in present_params or "viable_cell_density_e6_per_ml" in present_params:
        inst = (
            instrument_by_param.get("vcd_e6_per_ml")
            or instrument_by_param.get("viable_cell_density_e6_per_ml")
            or _placeholder("cell_counter_instrument", missing)
        )
        sentences.append(
            f"Cell density and viability were measured every 24 hours using a "
            f"{inst} automated cell counter."
        )

    if "titer_mg_per_l" in present_params:
        method = instrument_by_param.get("titer_mg_per_l") or _placeholder(
            "titer_method", missing
        )
        sentences.append(
            f"Product titer was quantified by {method} at 24-hour intervals."
        )

    if "glucose_g_per_l" in present_params and "lactate_g_per_l" in present_params:
        analyser = (
            instrument_by_param.get("glucose_g_per_l")
            or instrument_by_param.get("lactate_g_per_l")
            or _placeholder("metabolite_analyser", missing)
        )
        sentences.append(
            f"Glucose and lactate concentrations were measured using a {analyser} "
            f"biochemistry analyzer."
        )
    elif "glucose_g_per_l" in present_params:
        analyser = instrument_by_param.get("glucose_g_per_l") or _placeholder(
            "metabolite_analyser", missing
        )
        sentences.append(
            f"Glucose concentration was measured using a {analyser} analyzer."
        )

    if "osmolality_mosm" in present_params:
        inst = instrument_by_param.get("osmolality_mosm") or _placeholder(
            "osmometer", missing
        )
        sentences.append(f"Osmolality was measured using a {inst} osmometer.")

    return " ".join(sentences)


def _chromatography_paragraph(
    timeseries: List[TimeseriesData], missing: List[str],
) -> str:
    """Emit when any row carries chromatography metadata (x_axis=ml) or the
    canonical uv_absorbance_mau parameter from ÄKTA ingest."""
    akta_rows = [
        r for r in timeseries
        if r.parameter_name == "uv_absorbance_mau"
        or (r.series_metadata or {}).get("x_axis") == "ml"
    ]
    if not akta_rows:
        return ""

    methods = Counter()
    columns = Counter()
    akta_models = Counter()
    peak_counts: List[int] = []

    for r in akta_rows:
        meta = r.series_metadata or {}
        if meta.get("method"):
            methods[str(meta["method"])] += 1
        if meta.get("column"):
            columns[str(meta["column"])] += 1
        if meta.get("akta_model"):
            akta_models[str(meta["akta_model"])] += 1
        elif r.source_instrument and "ÄKTA" in r.source_instrument:
            akta_models[r.source_instrument.replace("Cytiva ", "")] += 1
        peaks = meta.get("peaks")
        if isinstance(peaks, list) and peaks:
            peak_counts.append(len(peaks))

    column_type = (
        columns.most_common(1)[0][0] if columns
        else _placeholder("column_type", missing)
    )
    akta_model = (
        akta_models.most_common(1)[0][0] if akta_models
        else _placeholder("akta_model", missing)
    )
    method_name = methods.most_common(1)[0][0] if methods else column_type

    if peak_counts:
        avg_peaks = int(round(sum(peak_counts) / len(peak_counts)))
        peak_count_str = str(avg_peaks)
    else:
        peak_count_str = _placeholder("peak_count", missing)

    return (
        f"Protein purification was performed by {method_name} chromatography "
        f"({column_type}) using an ÄKTA {akta_model} system. "
        f"{peak_count_str} chromatographic peaks were identified per run."
    )


def _instrument_summary(
    batches: List[Batch],
    samples: List[OfflineSample],
    timeseries: List[TimeseriesData],
) -> Dict[str, Any]:
    return {
        "bioreactor_models": sorted({b.bioreactor_model for b in batches if b.bioreactor_model}),
        "cell_lines": sorted({b.cell_line for b in batches if b.cell_line}),
        "media": sorted({b.media for b in batches if b.media}),
        "offline_instruments": sorted({s.instrument for s in samples if s.instrument}),
        "continuous_parameters": sorted({r.parameter_name for r in timeseries}),
        "offline_parameters": sorted({s.measurement_name for s in samples}),
        "has_chromatography": any(
            r.parameter_name == "uv_absorbance_mau"
            or (r.series_metadata or {}).get("x_axis") == "ml"
            for r in timeseries
        ),
    }
