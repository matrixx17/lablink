"""Seed/reset helpers for the demo wet lab campaign."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

import numpy as np
from sqlalchemy.orm import Session

from database import (
    AuditAction,
    AuditLog,
    Batch,
    Campaign,
    EntityType,
    OfflineSample,
    TimeseriesData,
    log_audit,
)
from wetlab_timeseries import series_metadata_for_parameter

DEMO_ORG_ID = "demo-therapeutics"
RNG = np.random.default_rng(seed=42)

DURATION_DAYS = 14
TS_INTERVAL_HOURS = 2
TS_N = (DURATION_DAYS * 24) // TS_INTERVAL_HOURS
OFFLINE_N = DURATION_DAYS

INOCULATION_DATE = datetime(2024, 3, 18, 9, 0, tzinfo=timezone.utc)
DELIVERY_DATE = datetime(2024, 4, 14, 10, 0, tzinfo=timezone.utc)


def _ts_seconds() -> np.ndarray:
    start = INOCULATION_DATE.timestamp()
    step = TS_INTERVAL_HOURS * 3600
    return np.array([start + i * step for i in range(TS_N)], dtype=float)


def _hours_axis() -> np.ndarray:
    return np.arange(TS_N) * TS_INTERVAL_HOURS


def gen_ph_baseline(setpoint: float) -> np.ndarray:
    h = _hours_axis()
    ramp = np.where(h < 12, 7.0 + (setpoint - 7.0) * (h / 12.0), setpoint)
    noise = RNG.normal(0.0, 0.05, size=TS_N)
    ph = ramp + noise
    # 45-min sustained excursion (>30 min at 2h cadence = 2+ points)
    mask = (h >= 48) & (h <= 50)
    ph = np.where(mask, setpoint - 0.65, ph)
    return np.round(ph, 3)


def gen_ph_clean(setpoint: float) -> np.ndarray:
    h = _hours_axis()
    ramp = np.where(h < 12, 7.0 + (setpoint - 7.0) * (h / 12.0), setpoint)
    noise = RNG.normal(0.0, 0.03, size=TS_N)
    return np.round(ramp + noise, 3)


def gen_ph_lead(setpoint: float) -> np.ndarray:
    h = _hours_axis()
    ph = setpoint + RNG.normal(0.0, 0.03, size=TS_N)
    # Brief 2h dip to 6.95 at hour 48 → warn (outside ±0.2, inside ±0.5)
    mask = (h >= 48) & (h < 50)
    ph = np.where(mask, 6.95, ph)
    return np.round(ph, 3)


def gen_do_fail() -> np.ndarray:
    h = _hours_axis()
    base = 40.0 + RNG.normal(0.0, 1.0, TS_N)
    for center in (96, 192):
        mask = (h >= center - 4) & (h <= center + 4)
        base = np.where(mask, 8.0, base)
    return np.round(np.clip(base, 5.0, 100.0), 2)


def gen_do_clean() -> np.ndarray:
    h = _hours_axis()
    base = 40.0 + 1.0 * np.sin(2 * np.pi * h / 12.0) + RNG.normal(0.0, 0.6, TS_N)
    for center in (120,):
        mask = (h >= center - 2) & (h <= center + 2)
        base = np.where(mask, base - 8.0, base)
    return np.round(np.clip(base, 22.0, 100.0), 2)


def gen_do_warn() -> np.ndarray:
    h = _hours_axis()
    base = 40.0 + RNG.normal(0.0, 0.8, TS_N)
    mask = (h >= 70) & (h <= 74)
    base = np.where(mask, 18.0, base)
    return np.round(np.clip(base, 5.0, 100.0), 2)


def gen_temperature() -> np.ndarray:
    h = _hours_axis()
    return np.round(37.0 + 0.05 * np.sin(2 * np.pi * h / 8.0) + RNG.normal(0, 0.05, TS_N), 3)


def gen_agitation() -> np.ndarray:
    h = _hours_axis()
    ramp_end = 7 * 24
    rpm = np.where(h < ramp_end, 200.0 + (150.0 / ramp_end) * h, 350.0)
    rpm += RNG.normal(0.0, 1.5, size=TS_N)
    return np.round(rpm, 1)


def gen_vcd_standard(peak_vcd_e6: float) -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    growth = peak_vcd_e6 / (1.0 + np.exp(-0.9 * (days - 5.5)))
    decline_mask = days > 9
    decline = np.where(decline_mask, peak_vcd_e6 * np.exp(-0.15 * (days - 9)), 0.0)
    vcd = np.where(decline_mask, decline, growth)
    vcd += RNG.normal(0.0, peak_vcd_e6 * 0.02, size=OFFLINE_N)
    return np.round(np.clip(vcd, 0.1, None), 3)


def gen_vcd_stalled(peak_vcd_e6: float) -> np.ndarray:
    """Flat first half → stalled-growth QC warn; peak ~day 4 then reversal."""
    days = np.arange(1, OFFLINE_N + 1)
    vcd = np.full(OFFLINE_N, 1.2)
    vcd[3:6] = np.linspace(1.2, peak_vcd_e6, 3)
    vcd[6:] = np.linspace(peak_vcd_e6, peak_vcd_e6 * 0.4, OFFLINE_N - 6)
    vcd += RNG.normal(0.0, 0.1, size=OFFLINE_N)
    return np.round(np.clip(vcd, 0.1, None), 3)


def gen_vcd_lead(peak_vcd_e6: float) -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    growth = peak_vcd_e6 / (1.0 + np.exp(-1.1 * (days - 8.0)))
    decline = np.where(days > 10, peak_vcd_e6 * 0.67 * np.exp(-0.08 * (days - 10)), growth)
    vcd = np.where(days > 10, decline, growth)
    vcd += RNG.normal(0.0, peak_vcd_e6 * 0.015, size=OFFLINE_N)
    return np.round(np.clip(vcd, 0.1, None), 3)


def gen_viability(vcd: np.ndarray) -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    peak_day = int(np.argmax(vcd)) + 1
    via = np.where(
        days <= peak_day,
        98.0 - 0.2 * (days - 1),
        98.0 - 0.2 * (peak_day - 1) - 4.5 * (days - peak_day),
    )
    via += RNG.normal(0.0, 0.3, size=OFFLINE_N)
    return np.round(np.clip(via, 30.0, 99.5), 2)


def gen_titer(final_mg_per_l: float) -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    raw = final_mg_per_l / (1.0 + np.exp(-0.55 * (days - 8.0)))
    raw += RNG.normal(0.0, final_mg_per_l * 0.01, size=OFFLINE_N)
    return np.round(np.maximum.accumulate(raw), 1)


def gen_glucose() -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    glucose = np.zeros(OFFLINE_N)
    level = 5.5
    for i, d in enumerate(days):
        if d in (3, 6, 9):
            level = 5.0
        else:
            level -= RNG.uniform(0.35, 0.55)
        glucose[i] = max(level, 0.2)
    return np.round(np.clip(glucose + RNG.normal(0, 0.08, OFFLINE_N), 0.1, None), 2)


def gen_lactate() -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    lac = 0.1 + 1.5 * (1 - np.exp(-0.25 * days))
    lac = np.where(days > 10, lac - 0.05 * (days - 10), lac)
    return np.round(np.clip(lac + RNG.normal(0, 0.05, OFFLINE_N), 0.05, None), 2)


def gen_osmolality() -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    return np.round(np.clip(295.0 + 2.5 * days + RNG.normal(0, 4.0, OFFLINE_N), 290.0, 330.0), 1)


BATCH_SPECS: List[Dict[str, Any]] = [
    {
        "batch_number": "Batch_004A",
        "label": "baseline condition",
        "ph_setpoint": 7.0,
        "feed_strategy": "fixed",
        "lead_condition": False,
        "peak_vcd_e6": 8.0,
        "final_titer": 800.0,
        "ph_gen": gen_ph_baseline,
        "do_gen": gen_do_fail,
        "vcd_gen": gen_vcd_stalled,
    },
    {
        "batch_number": "Batch_004B",
        "label": "increased feed rate",
        "ph_setpoint": 7.0,
        "feed_strategy": "adaptive",
        "lead_condition": False,
        "peak_vcd_e6": 12.0,
        "final_titer": 1600.0,
        "ph_gen": gen_ph_clean,
        "do_gen": gen_do_clean,
        "vcd_gen": gen_vcd_standard,
    },
    {
        "batch_number": "Batch_004C",
        "label": "optimized pH setpoint (lead condition)",
        "ph_setpoint": 7.2,
        "feed_strategy": "adaptive",
        "lead_condition": True,
        "peak_vcd_e6": 18.0,
        "final_titer": 2400.0,
        "ph_gen": gen_ph_lead,
        "do_gen": gen_do_warn,
        "vcd_gen": gen_vcd_lead,
        "include_chromatography": True,
    },
]


def _build_continuous_series(spec: Dict[str, Any]) -> List[Tuple[str, str, np.ndarray, Dict[str, Any]]]:
    base_meta = series_metadata_for_parameter("ph", {"setpoint_source": "controller"})
    return [
        ("ph", "pH", spec["ph_gen"](spec["ph_setpoint"]), base_meta),
        ("do_percent", "%", spec["do_gen"](), series_metadata_for_parameter("do_percent", base_meta)),
        ("temperature_c", "C", gen_temperature(), series_metadata_for_parameter("temperature_c", base_meta)),
        ("agitation_rpm", "RPM", gen_agitation(), series_metadata_for_parameter("agitation_rpm", base_meta)),
    ]


def _build_offline(spec: Dict[str, Any]) -> Dict[str, Tuple[str, np.ndarray]]:
    vcd = spec["vcd_gen"](spec["peak_vcd_e6"])
    return {
        "viable_cell_density_e6_per_ml": ("1e6 cells/mL", vcd),
        "viability_percent": ("%", gen_viability(vcd)),
        "titer_mg_per_l": ("mg/L", gen_titer(spec["final_titer"])),
        "glucose_g_per_l": ("g/L", gen_glucose()),
        "lactate_g_per_l": ("g/L", gen_lactate()),
        "osmolality_mosm": ("mOsm/kg", gen_osmolality()),
    }


def _add_chromatography(db: Session, batch_id: str) -> None:
    ml_axis = np.linspace(0, 120, 61)
    uv = 20 + 80 * np.exp(-((ml_axis - 35) ** 2) / 80) + 40 * np.exp(-((ml_axis - 75) ** 2) / 50)
    chrom_meta = series_metadata_for_parameter(
        "uv_absorbance_mau",
        {
            "method": "Protein A affinity",
            "column": "HiTrap Protein A HP 1 mL",
            "akta_model": "pure 25",
            "run_date": DELIVERY_DATE.date().isoformat(),
            "peaks": [
                {"name": "Main peak", "retention_volume_ml": 35.2, "peak_area_mau_ml": 450.0, "peak_height_mau": 98.0},
                {"name": "Aggregate shoulder", "retention_volume_ml": 74.8, "peak_area_mau_ml": 85.0, "peak_height_mau": 42.0},
            ],
        },
    )
    db.add(
        TimeseriesData(
            id=str(uuid.uuid4()),
            batch_id=batch_id,
            parameter_name="uv_absorbance_mau",
            unit="mAU",
            timestamps=ml_axis.tolist(),
            values=np.round(uv, 2).tolist(),
            source_instrument="Cytiva ÄKTA pure 25",
            series_metadata=chrom_meta,
        )
    )


def delete_wetlab_demo(db: Session) -> None:
    """Remove demo wet lab campaigns and cascaded data for DEMO_ORG_ID."""
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.org_id == DEMO_ORG_ID, Campaign.domain == "wetlab")
        .all()
    )
    if not campaigns:
        return

    campaign_ids = [c.id for c in campaigns]
    batches = db.query(Batch).filter(Batch.campaign_id.in_(campaign_ids)).all()
    batch_ids = [b.id for b in batches]

    if batch_ids:
        db.query(OfflineSample).filter(OfflineSample.batch_id.in_(batch_ids)).delete(synchronize_session=False)
        db.query(TimeseriesData).filter(TimeseriesData.batch_id.in_(batch_ids)).delete(synchronize_session=False)
        db.query(Batch).filter(Batch.id.in_(batch_ids)).delete(synchronize_session=False)

    entity_ids = batch_ids + campaign_ids
    if entity_ids:
        db.query(AuditLog).filter(
            AuditLog.org_id == DEMO_ORG_ID,
            AuditLog.entity_id.in_(entity_ids),
        ).delete(synchronize_session=False)

    db.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(synchronize_session=False)
    db.commit()


def seed_wetlab_demo(db: Session) -> Dict[str, Any]:
    """Create the demo wet lab campaign with QC-tuned synthetic data."""
    from bioprocess_qc import BioprocessQCEngine

    ts_seconds = _ts_seconds().tolist()

    campaign = Campaign(
        id=str(uuid.uuid4()),
        org_id=DEMO_ORG_ID,
        name="mAb Process Development — Campaign 4",
        description=(
            "CHO cell fed-batch process development campaign. "
            "Delivered by BioCalc Process Labs via LabLink on April 14, 2024. "
            "This record tracks the complete bioprocess history of the lead "
            "condition selected for scale-up."
        ),
        domain="wetlab",
        extra_params={
            "target": "Anti-HER2 monoclonal antibody",
            "status": "lead_nominated",
            "cro_partner": "BioCalc Process Labs",
            "delivery_date": DELIVERY_DATE.isoformat(),
            "process_type": "CHO fed-batch",
        },
    )
    db.add(campaign)
    db.flush()

    batch_ids: Dict[str, str] = {}
    for spec in BATCH_SPECS:
        batch = Batch(
            id=str(uuid.uuid4()),
            campaign_id=campaign.id,
            batch_number=spec["batch_number"],
            bioreactor_model="Sartorius BIOSTAT B-DCU",
            volume_liters=2.0,
            cell_line="CHO-K1 (anti-HER2 clone)",
            media="ActiPro + Cell Boost 7a/7b",
            inoculation_date=INOCULATION_DATE,
            harvest_date=INOCULATION_DATE + timedelta(days=DURATION_DAYS),
            status="harvested",
            extra_params={
                "condition_label": spec["label"],
                "ph_setpoint": spec["ph_setpoint"],
                "do_setpoint_percent": 40.0,
                "temperature_setpoint_c": 37.0,
                "feed_strategy": spec["feed_strategy"],
                "lead_condition": spec["lead_condition"],
            },
        )
        db.add(batch)
        db.flush()
        batch_ids[spec["batch_number"]] = batch.id

        for param, unit, values, meta in _build_continuous_series(spec):
            db.add(
                TimeseriesData(
                    id=str(uuid.uuid4()),
                    batch_id=batch.id,
                    parameter_name=param,
                    unit=unit,
                    timestamps=ts_seconds,
                    values=values.tolist(),
                    source_instrument="Sartorius BIOSTAT B-DCU",
                    series_metadata=meta,
                )
            )

        if spec.get("include_chromatography"):
            _add_chromatography(db, batch.id)

        offline = _build_offline(spec)
        for day_idx in range(OFFLINE_N):
            hours = float((day_idx + 1) * 24)
            ts_abs = INOCULATION_DATE + timedelta(hours=hours)
            for meas, (unit, arr) in offline.items():
                instrument = (
                    "Beckman Vi-CELL XR" if "cell_density" in meas or meas.startswith("viab")
                    else "Nova BioProfile FLEX2" if meas in ("glucose_g_per_l", "lactate_g_per_l")
                    else "Advanced Instruments Osmometer" if meas == "osmolality_mosm"
                    else "Octet BLI"
                )
                qc = "pass"
                if spec["batch_number"] == "Batch_004A" and meas == "titer_mg_per_l" and day_idx >= 11:
                    qc = "warn"
                db.add(
                    OfflineSample(
                        id=str(uuid.uuid4()),
                        batch_id=batch.id,
                        sample_time_hours=hours,
                        sample_time_absolute=ts_abs,
                        measurement_name=meas,
                        value=float(arr[day_idx]),
                        unit=unit,
                        instrument=instrument,
                        qc_status=qc,
                    )
                )

        BioprocessQCEngine.run_for_batch(db, batch.id)

        log_audit(
            action=AuditAction.CONFIG_CHANGED,
            entity_type=EntityType.CONFIG,
            entity_id=batch.id,
            actor="BioCalc Process Labs",
            org_id=DEMO_ORG_ID,
            details={
                "event": "cro_delivery",
                "batch_number": spec["batch_number"],
                "campaign_id": campaign.id,
                "delivered_at": DELIVERY_DATE.isoformat(),
                "condition": spec["label"],
            },
            db=db,
        )

    lead_id = batch_ids["Batch_004C"]
    log_audit(
        action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG,
        entity_id=lead_id,
        actor="Dr. Maria Santos, VP Process Development",
        org_id=DEMO_ORG_ID,
        details={
            "event": "qc_acknowledgment",
            "batch_number": "Batch_004C",
            "campaign_id": campaign.id,
            "message": (
                "DO excursion at hour 72 reviewed and accepted. Duration 45 min, "
                "DO nadir 18%. Culture recovered normally. "
                "Approved by: Dr. Maria Santos"
            ),
        },
        db=db,
    )
    log_audit(
        action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG,
        entity_id=lead_id,
        actor="Dr. Maria Santos, VP Process Development",
        org_id=DEMO_ORG_ID,
        details={
            "event": "lead_nominated",
            "batch_number": "Batch_004C",
            "campaign_id": campaign.id,
            "message": (
                "Batch_004C nominated as lead condition for scale-up. "
                "Final titer 2400 mg/L, peak VCD 18e6 cells/mL, pH 7.2 "
                "optimized setpoint. Approved by: Dr. Maria Santos, "
                "VP Process Development."
            ),
            "final_titer_mg_per_l": 2400.0,
            "peak_vcd_e6_per_ml": 18.0,
            "ph_setpoint": 7.2,
        },
        db=db,
    )

    db.commit()
    return {"campaign_id": campaign.id, "batch_ids": batch_ids}
