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

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from database import Batch, Campaign, OfflineSample, TimeseriesData

# Tone of voice: present tense, terse, no marketing.


def generate_wetlab_methods(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Build the methods doc for a wet lab campaign.

    The response intentionally mirrors the comp-chem methods export contract so
    the shared dashboard page can render either domain.
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

    missing_fields: List[str] = []
    paragraph_order = ("bioreactor", "cell_analysis", "titer", "metabolites", "chromatography")
    paragraphs = {
        "bioreactor": _bioreactor_paragraph(batches, timeseries, missing_fields),
        "cell_analysis": _cell_analysis_paragraph(offline_samples, missing_fields),
        "titer": _titer_paragraph(offline_samples, missing_fields),
        "metabolites": _metabolites_paragraph(batches, offline_samples, missing_fields),
        "chromatography": _chromatography_paragraph(timeseries, missing_fields),
    }
    full_text = "\n\n".join(paragraphs[key] for key in paragraph_order if paragraphs.get(key))

    return {
        "campaign_id": str(campaign.id),
        "campaign_name": campaign.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain": "wetlab",
        "paragraphs": paragraphs,
        "full_text": full_text,
        "missing_fields": _unique_ordered(missing_fields),
        "software_versions": _equipment_versions(batches, offline_samples, timeseries),
        "run_counts": {
            "batches": len(batches),
            "total_offline_samples": len(offline_samples),
            "continuous_timepoints": _continuous_timepoint_count(timeseries),
        },
    }


# ---------------------------------------------------------------------- helpers


def generate_methods(db: Session, campaign: Campaign) -> Dict[str, Any]:
    """Backward-compatible alias for existing wet lab callers."""
    return generate_wetlab_methods(db, campaign)


def _mode(values: Iterable[Any]) -> Optional[Any]:
    """Most common non-null value or None."""
    cleaned = [v for v in values if v is not None and v != ""]
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def _placeholder(field: str, missing: List[str]) -> str:
    if field not in missing:
        missing.append(field)
    return "[not recorded]"


def _coerce_array(value: Any) -> List[Any]:
    """Tolerate PostgreSQL ARRAY plus SQLite JSON/string test fixtures."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    if isinstance(value, list) and value and isinstance(value[0], str) and len(value[0]) == 1:
        try:
            parsed = json.loads("".join(value))
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return value
    return value if isinstance(value, list) else []


def _float_values(value: Any) -> List[float]:
    out: List[float] = []
    for item in _coerce_array(value):
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _continuous_mode_for(timeseries: List[TimeseriesData], parameter: str) -> Optional[float]:
    """Estimate a controller setpoint as the mode at 0.05 precision."""
    all_vals: List[float] = []
    for r in timeseries:
        if r.parameter_name != parameter:
            continue
        all_vals.extend(_float_values(r.values))
    if not all_vals:
        return None
    rounded = [round(v / 0.05) * 0.05 for v in all_vals]
    return float(Counter(rounded).most_common(1)[0][0])


def _extra_mode(batches: List[Batch], keys: Iterable[str]) -> Optional[Any]:
    for key in keys:
        value = _mode([(b.extra_params or {}).get(key) for b in batches])
        if value not in (None, ""):
            return value
    return None


def _sample_interval_hours(samples: List[OfflineSample], measurement_names: Iterable[str]) -> Optional[float]:
    names = set(measurement_names)
    by_batch: DefaultDict[str, List[float]] = defaultdict(list)
    for sample in samples:
        if sample.measurement_name not in names or sample.sample_time_hours is None:
            continue
        by_batch[sample.batch_id].append(float(sample.sample_time_hours))

    intervals: List[float] = []
    for hours in by_batch.values():
        ordered = sorted(set(hours))
        intervals.extend(
            round(ordered[i] - ordered[i - 1], 2)
            for i in range(1, len(ordered))
            if ordered[i] > ordered[i - 1]
        )
    return _mode(intervals)


def _fmt_number(value: Any, digits: int = 1) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num.is_integer():
        return str(int(num))
    return f"{num:.{digits}f}".rstrip("0").rstrip(".")


def _fmt_interval(value: Optional[float], missing: List[str], field: str) -> str:
    if value is None:
        return _placeholder(field, missing)
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))}-hour"
    return f"{_fmt_number(value, 1)}-hour"


def _measurement_groups(samples: List[OfflineSample]) -> Dict[str, List[OfflineSample]]:
    grouped: Dict[str, List[OfflineSample]] = defaultdict(list)
    for sample in samples:
        grouped[sample.measurement_name].append(sample)
    return grouped


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

    durations: List[float] = []
    for b in batches:
        if b.harvest_date and b.inoculation_date:
            delta = b.harvest_date - b.inoculation_date
            durations.append(delta.total_seconds() / 86400.0)
    duration_str = (
        _fmt_number(statistics.median(durations), 1) if durations else None
    )

    ph_setpoint = _extra_mode(batches, ("ph_setpoint",)) or _continuous_mode_for(timeseries, "ph")
    do_setpoint = (
        _extra_mode(batches, ("do_setpoint_percent", "do_setpoint"))
        or _continuous_mode_for(timeseries, "do_percent")
    )
    temp_setpoint = (
        _extra_mode(batches, ("temperature_setpoint_c", "temperature_c"))
        or _continuous_mode_for(timeseries, "temperature_c")
    )
    feed_strategy = _extra_mode(batches, ("feed_strategy", "feed"))

    bioreactor_str = bioreactor_mode or _placeholder("bioreactor_model", missing)
    volume_str = f"{volume_mode:g}" if volume_mode is not None else _placeholder(
        "volume_liters", missing
    )
    cell_str = cell_mode or _placeholder("cell_line", missing)
    media_str = media_mode or _placeholder("media", missing)
    duration_str = duration_str or _placeholder("run_duration", missing)
    ph_str = _fmt_number(ph_setpoint, 2) if ph_setpoint is not None else _placeholder(
        "ph_setpoint", missing
    )
    do_str = _fmt_number(do_setpoint, 0) if do_setpoint is not None else _placeholder(
        "do_setpoint", missing
    )
    temp_str = _fmt_number(temp_setpoint, 1) if temp_setpoint is not None else _placeholder(
        "temperature_setpoint", missing
    )
    feed_str = f" Feeding used a {feed_strategy} strategy." if feed_strategy else ""

    return (
        f"Fed-batch cell culture was performed in a {volume_str} L {bioreactor_str} "
        f"bioreactor. {cell_str} cells were cultured in {media_str} medium for "
        f"{duration_str} days. pH was controlled to {ph_str} ± 0.05, dissolved "
        f"oxygen was maintained above {do_str}% air saturation, and temperature "
        f"was held at {temp_str}°C.{feed_str} A total of {len(batches)} independent "
        f"bioreactor runs were performed."
    )


def _cell_analysis_paragraph(samples: List[OfflineSample], missing: List[str]) -> str:
    if not samples:
        return ""

    groups = _measurement_groups(samples)
    cell_measurements = ("vcd_e6_per_ml", "viable_cell_density_e6_per_ml", "viability_percent")
    cell_samples = [s for name in cell_measurements for s in groups.get(name, [])]
    if not cell_samples:
        return ""

    inst = _mode([s.instrument for s in cell_samples]) or _placeholder("cell_counter_instrument", missing)
    interval = _fmt_interval(_sample_interval_hours(samples, cell_measurements), missing, "cell_analysis_interval")
    return (
        f"Viable cell density and viability were measured at {interval} intervals "
        f"using a {inst} automated cell counter."
    )


def _titer_paragraph(samples: List[OfflineSample], missing: List[str]) -> str:
    titer_samples = [s for s in samples if s.measurement_name == "titer_mg_per_l"]
    if not titer_samples:
        return ""

    method = _mode([s.instrument for s in titer_samples]) or _placeholder("titer_method", missing)
    interval = _fmt_interval(_sample_interval_hours(samples, ("titer_mg_per_l",)), missing, "titer_interval")
    final_values: List[float] = []
    by_batch: DefaultDict[str, List[OfflineSample]] = defaultdict(list)
    for sample in titer_samples:
        by_batch[sample.batch_id].append(sample)
    for batch_samples in by_batch.values():
        ordered = sorted(batch_samples, key=lambda s: s.sample_time_hours or -1.0)
        if ordered and ordered[-1].value is not None:
            final_values.append(float(ordered[-1].value))

    if final_values:
        if len(final_values) == 1:
            titer_text = f"The final recorded titer was {_fmt_number(final_values[0], 0)} mg/L."
        else:
            titer_text = (
                f"Final recorded titers ranged from {_fmt_number(min(final_values), 0)} to "
                f"{_fmt_number(max(final_values), 0)} mg/L."
            )
    else:
        titer_text = "Final recorded titer was [not recorded]."
        _placeholder("final_titer", missing)

    return f"Product titer was quantified by {method} at {interval} intervals. {titer_text}"


def _metabolites_paragraph(
    batches: List[Batch],
    samples: List[OfflineSample],
    missing: List[str],
) -> str:
    metabolite_names = ("glucose_g_per_l", "lactate_g_per_l", "osmolality_mosm")
    metabolite_samples = [s for s in samples if s.measurement_name in metabolite_names]
    if not metabolite_samples:
        return ""

    analyzer = _mode([
        s.instrument for s in metabolite_samples
        if s.measurement_name in ("glucose_g_per_l", "lactate_g_per_l")
    ]) or _mode([s.instrument for s in metabolite_samples]) or _placeholder("metabolite_analyzer", missing)
    interval = _fmt_interval(_sample_interval_hours(samples, metabolite_names), missing, "metabolite_interval")
    measured = sorted({
        "glucose" if s.measurement_name == "glucose_g_per_l"
        else "lactate" if s.measurement_name == "lactate_g_per_l"
        else "osmolality"
        for s in metabolite_samples
    })
    if len(measured) == 1:
        measured_text = measured[0]
        verb = "was"
    elif len(measured) == 2:
        measured_text = " and ".join(measured)
        verb = "were"
    else:
        measured_text = ", ".join(measured[:-1]) + f", and {measured[-1]}"
        verb = "were"
    threshold = _extra_mode(
        batches,
        (
            "glucose_feed_threshold_g_per_l",
            "feed_threshold_g_per_l",
            "feed_threshold",
            "glucose_threshold_g_per_l",
        ),
    )
    threshold_text = (
        f"Glucose-triggered feeding used a {_fmt_number(threshold, 2)} g/L threshold."
        if threshold not in (None, "")
        else f"Glucose-triggered feeding threshold was {_placeholder('feed_threshold', missing)}."
    )
    return (
        f"{measured_text.capitalize()} {verb} measured at {interval} intervals using a "
        f"{analyzer} biochemistry analyzer. {threshold_text}"
    )


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


def _equipment_versions(
    batches: List[Batch],
    samples: List[OfflineSample],
    timeseries: List[TimeseriesData],
) -> Dict[str, List[str]]:
    equipment: Dict[str, set] = {}

    def add(category: str, value: Optional[str]) -> None:
        if value:
            equipment.setdefault(category, set()).add(value)

    for value in {b.bioreactor_model for b in batches if b.bioreactor_model}:
        add("Bioreactor", value)

    for sample in samples:
        name = sample.measurement_name
        if name in ("vcd_e6_per_ml", "viable_cell_density_e6_per_ml", "viability_percent"):
            add("Cell analysis", sample.instrument)
        elif name == "titer_mg_per_l":
            add("Titer", sample.instrument)
        elif name in ("glucose_g_per_l", "lactate_g_per_l"):
            add("Metabolites", sample.instrument)
        elif name == "osmolality_mosm":
            add("Osmolality", sample.instrument)

    for row in timeseries:
        if row.parameter_name == "uv_absorbance_mau" or (row.series_metadata or {}).get("x_axis") == "ml":
            add("Chromatography", row.source_instrument)
        else:
            add("Continuous controller", row.source_instrument)

    return {name: sorted(values) for name, values in sorted(equipment.items())}


def _continuous_timepoint_count(timeseries: List[TimeseriesData]) -> int:
    total = 0
    for row in timeseries:
        total += len(_coerce_array(row.timestamps))
    return total


def _unique_ordered(values: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
