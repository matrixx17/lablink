"""Seed a realistic wet lab campaign (mAb Process Development) into the
demo org, alongside the existing comp-chem demo data.

Usage:
    python scripts/seed_demo_wetlab.py

Creates one Campaign, three Batches (004A/B/C — C is the lead condition),
14 days of continuous TimeSeries (pH, DO, temp, agitation) at 2h cadence,
and offline samples at 24h cadence (VCD, viability, titer, glucose,
lactate, osmolality). Logs cro_delivery + lead_nominated audit events.

Synthetic values are generated with numpy — sin waves + noise + biology-
shaped curves so the resulting traces look like real bioreactor data.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import numpy as np

# Allow running as `python scripts/seed_demo_wetlab.py` from repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "services", "api"))

from database import (  # noqa: E402
    SessionLocal,
    Campaign,
    Batch,
    TimeseriesData,
    OfflineSample,
    AuditAction,
    EntityType,
    log_audit,
)


DEMO_ORG_ID = "demo-therapeutics"
RNG = np.random.default_rng(seed=42)

# 14 days, sampled every 2 hours
DURATION_DAYS = 14
TS_INTERVAL_HOURS = 2
TS_N = (DURATION_DAYS * 24) // TS_INTERVAL_HOURS  # 168
OFFLINE_N = DURATION_DAYS  # one sample per 24h, day 1..14

INOCULATION_DATE = datetime(2024, 3, 18, 9, 0, tzinfo=timezone.utc)
DELIVERY_DATE = datetime(2024, 4, 14, 10, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Synthetic data generators
# --------------------------------------------------------------------------

def _ts_seconds() -> np.ndarray:
    """Unix-timestamp array for the 168-point continuous trace."""
    start = INOCULATION_DATE.timestamp()
    step = TS_INTERVAL_HOURS * 3600
    return np.array([start + i * step for i in range(TS_N)], dtype=float)


def _hours_axis() -> np.ndarray:
    return np.arange(TS_N) * TS_INTERVAL_HOURS


def gen_ph(setpoint: float) -> np.ndarray:
    h = _hours_axis()
    # Initial drift from 7.0 to setpoint over first 12h, then controlled with
    # a slow oscillation and Gaussian noise.
    ramp = np.where(h < 12, 7.0 + (setpoint - 7.0) * (h / 12.0), setpoint)
    osc = 0.02 * np.sin(2 * np.pi * h / 36.0)  # ~36h diurnal-ish wobble
    noise = RNG.normal(0.0, 0.05, size=TS_N)
    return np.round(ramp + osc + noise, 3)


def gen_do(excursion: bool) -> np.ndarray:
    """40% setpoint; if excursion=True, inject dips to ~25% on days 4 and 8."""
    h = _hours_axis()
    base = 40.0 + 1.5 * np.sin(2 * np.pi * h / 12.0)  # 12h breathing
    noise = RNG.normal(0.0, 0.8, size=TS_N)
    do = base + noise
    if excursion:
        for day in (4, 8):
            # 6-hour dip centered on that day
            center = day * 24
            mask = (h >= center - 3) & (h <= center + 3)
            do = np.where(mask, do - 15.0 * np.exp(-((h - center) ** 2) / 4.0), do)
    return np.round(np.clip(do, 5.0, 100.0), 2)


def gen_temperature() -> np.ndarray:
    h = _hours_axis()
    return np.round(37.0 + 0.05 * np.sin(2 * np.pi * h / 8.0) + RNG.normal(0, 0.05, TS_N), 3)


def gen_agitation() -> np.ndarray:
    """200 RPM, ramped linearly to 350 RPM by day 7, then held."""
    h = _hours_axis()
    ramp_end = 7 * 24
    rpm = np.where(h < ramp_end, 200.0 + (150.0 / ramp_end) * h, 350.0)
    rpm += RNG.normal(0.0, 1.5, size=TS_N)
    return np.round(rpm, 1)


def gen_vcd(peak_vcd_e6: float) -> np.ndarray:
    """Sigmoid growth → plateau → decline. Returns one value per day."""
    days = np.arange(1, OFFLINE_N + 1)
    # Logistic up to ~day 9, then exponential decline
    growth = peak_vcd_e6 / (1.0 + np.exp(-0.9 * (days - 5.5)))
    decline_mask = days > 9
    decline = np.where(
        decline_mask, peak_vcd_e6 * np.exp(-0.15 * (days - 9)), 0.0
    )
    vcd = np.where(decline_mask, decline, growth)
    vcd += RNG.normal(0.0, peak_vcd_e6 * 0.02, size=OFFLINE_N)
    return np.round(np.clip(vcd, 0.1, None), 3)


def gen_viability(vcd: np.ndarray) -> np.ndarray:
    """Starts 98%, declines after peak VCD."""
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
    """Monotonically increasing, accelerates after day 4."""
    days = np.arange(1, OFFLINE_N + 1)
    # Smooth sigmoid reaching ~final by day 14
    raw = final_mg_per_l / (1.0 + np.exp(-0.55 * (days - 8.0)))
    # Force monotonicity after adding small noise
    raw += RNG.normal(0.0, final_mg_per_l * 0.01, size=OFFLINE_N)
    titer = np.maximum.accumulate(raw)
    return np.round(np.clip(titer, 0.0, None), 1)


def gen_glucose() -> np.ndarray:
    """Decreases, with feeds at days 3, 6, 9 bumping back up to ~5 g/L."""
    days = np.arange(1, OFFLINE_N + 1)
    glucose = np.zeros(OFFLINE_N)
    level = 5.5
    for i, d in enumerate(days):
        if d in (3, 6, 9):
            level = 5.0  # feed
        else:
            level -= RNG.uniform(0.35, 0.55)
        glucose[i] = max(level, 0.2)
    glucose += RNG.normal(0.0, 0.08, size=OFFLINE_N)
    return np.round(np.clip(glucose, 0.1, None), 2)


def gen_lactate() -> np.ndarray:
    """Increases through growth phase, plateaus, slight reuptake late."""
    days = np.arange(1, OFFLINE_N + 1)
    lac = 0.1 + 1.5 * (1 - np.exp(-0.25 * days))
    lac = np.where(days > 10, lac - 0.05 * (days - 10), lac)
    lac += RNG.normal(0.0, 0.05, size=OFFLINE_N)
    return np.round(np.clip(lac, 0.05, None), 2)


def gen_osmolality() -> np.ndarray:
    days = np.arange(1, OFFLINE_N + 1)
    osm = 295.0 + 2.5 * days + RNG.normal(0.0, 4.0, size=OFFLINE_N)
    return np.round(np.clip(osm, 290.0, 330.0), 1)


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------

BATCH_SPECS: List[Dict] = [
    {
        "batch_number": "Batch_004A",
        "label": "baseline condition",
        "ph_setpoint": 7.0,
        "do_excursion": True,
        "peak_vcd_e6": 8.0,
        "final_titer": 800.0,
    },
    {
        "batch_number": "Batch_004B",
        "label": "increased feed rate",
        "ph_setpoint": 7.0,
        "do_excursion": False,
        "peak_vcd_e6": 12.0,
        "final_titer": 1200.0,
    },
    {
        "batch_number": "Batch_004C",
        "label": "optimized pH setpoint (lead condition)",
        "ph_setpoint": 7.2,
        "do_excursion": False,
        "peak_vcd_e6": 18.0,
        "final_titer": 2400.0,
    },
]


def _build_continuous_series(spec: Dict) -> List[Tuple[str, str, np.ndarray]]:
    """Return list of (parameter_name, unit, values_array)."""
    return [
        ("ph", "pH", gen_ph(spec["ph_setpoint"])),
        ("do_percent", "%", gen_do(spec["do_excursion"])),
        ("temperature_c", "C", gen_temperature()),
        ("agitation_rpm", "RPM", gen_agitation()),
    ]


def _build_offline(spec: Dict) -> Dict[str, Tuple[str, np.ndarray]]:
    vcd = gen_vcd(spec["peak_vcd_e6"])
    return {
        "viable_cell_density_e6_per_ml": ("1e6 cells/mL", vcd),
        "viability_percent": ("%", gen_viability(vcd)),
        "titer_mg_per_l": ("mg/L", gen_titer(spec["final_titer"])),
        "glucose_g_per_l": ("g/L", gen_glucose()),
        "lactate_g_per_l": ("g/L", gen_lactate()),
        "osmolality_mosm": ("mOsm/kg", gen_osmolality()),
    }


def seed() -> Dict[str, str]:
    db = SessionLocal()
    try:
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
                },
            )
            db.add(batch)
            db.flush()
            batch_ids[spec["batch_number"]] = batch.id

            # Continuous time-series
            for param, unit, values in _build_continuous_series(spec):
                db.add(
                    TimeseriesData(
                        id=str(uuid.uuid4()),
                        batch_id=batch.id,
                        parameter_name=param,
                        unit=unit,
                        timestamps=ts_seconds,
                        values=values.tolist(),
                        source_instrument="Sartorius BIOSTAT B-DCU",
                    )
                )

            # Offline samples (24h cadence, days 1..14)
            offline = _build_offline(spec)
            for day_idx in range(OFFLINE_N):
                hours = float((day_idx + 1) * 24)
                ts_abs = INOCULATION_DATE + timedelta(hours=hours)
                for meas, (unit, arr) in offline.items():
                    instrument = (
                        "Beckman Vi-CELL XR" if meas.startswith("viab") or "cell_density" in meas
                        else "Nova BioProfile FLEX2" if meas in ("glucose_g_per_l", "lactate_g_per_l")
                        else "Advanced Instruments Osmometer" if meas == "osmolality_mosm"
                        else "Octet BLI"  # titer
                    )
                    # DO excursions are flagged on continuous data, but mark a
                    # couple of offline points on 004A as warn for realism.
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

            # CRO delivery audit event for every batch.
            # AuditAction enum doesn't yet include cro_delivery / lead_nominated,
            # so we tag the semantic name inside details["event"].
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

        # Lead nomination — Batch_004C
        lead_id = batch_ids["Batch_004C"]
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
        return {
            "campaign_id": campaign.id,
            "batches": batch_ids,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    print("Seeded wet lab demo campaign:")
    print(f"  campaign_id: {result['campaign_id']}")
    for name, bid in result["batches"].items():
        print(f"  {name}: {bid}")
