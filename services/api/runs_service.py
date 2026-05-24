"""
Run lifecycle: create/link runs, persist measurement series, align timelines.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from database import (
    RunRecord, RunStatus, FileRecord, MeasurementSeries, DataKind,
    AuditAction, EntityType, log_audit,
)
from assay_qc import assay_qc_summary
from bioprocess_qc import bioprocess_qc_summary, is_bioprocess_instrument
from qc import qc_summary
from timeseries_align import (
    align_run_series,
    extract_series_from_stats,
    extract_series_from_points,
)
from baselines import get_baselines_for_qc


def get_or_create_run(
    db: Session,
    org_id: str,
    external_run_id: str,
    batch_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    bioreactor_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RunRecord:
    run = (
        db.query(RunRecord)
        .filter(RunRecord.org_id == org_id, RunRecord.external_run_id == external_run_id)
        .first()
    )
    if run:
        if batch_id and not run.batch_id:
            run.batch_id = batch_id
        if campaign_id and not run.campaign_id:
            run.campaign_id = campaign_id
        if bioreactor_id and not run.bioreactor_id:
            run.bioreactor_id = bioreactor_id
        db.commit()
        return run

    run = RunRecord(
        org_id=org_id,
        external_run_id=external_run_id,
        batch_id=batch_id,
        campaign_id=campaign_id,
        bioreactor_id=bioreactor_id,
        status=RunStatus.ACTIVE.value,
        started_at=datetime.now(timezone.utc),
        run_metadata=metadata or {},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        log_audit(
            action=AuditAction.RUN_CREATED,
            entity_type=EntityType.RUN,
            entity_id=str(run.id),
            actor="api",
            org_id=org_id,
            details={"external_run_id": external_run_id},
            db=db,
        )
    except Exception:
        pass

    return run


def persist_measurement_series(
    db: Session,
    org_id: str,
    run_id: int,
    file_id: int,
    stats: Dict[str, Any],
    schema_mapping: Dict[str, Any],
    data_kind: str,
    series_points: Optional[List[Dict[str, Any]]] = None,
    time_column: Optional[str] = None,
) -> int:
    """Store queryable time series from stats or series_points. Returns count inserted."""
    mapping = schema_mapping.get("mapping", {})
    count = 0

    if series_points:
        grouped = extract_series_from_points(series_points)
        for field, (times, values) in grouped.items():
            if not values:
                continue
            canonical = mapping.get(field, field)
            db.add(MeasurementSeries(
                org_id=org_id,
                run_id=run_id,
                file_id=file_id,
                field_name=field,
                canonical_field=canonical,
                data_kind=data_kind,
                time_values=times,
                values=values,
                point_count=len(values),
            ))
            count += 1
        db.commit()
        return count

    extracted = extract_series_from_stats(stats, time_field=time_column)
    for field, (times, values) in extracted.items():
        if not values:
            continue
        canonical = mapping.get(field, field)
        db.add(MeasurementSeries(
            org_id=org_id,
            run_id=run_id,
            file_id=file_id,
            field_name=field,
            canonical_field=canonical,
            data_kind=data_kind,
            time_values=times,
            values=values,
            point_count=len(values),
        ))
        count += 1

    db.commit()
    return count


def rebuild_run_alignment(db: Session, run_id: int) -> Dict[str, Any]:
    """Recompute alignment from all series on a run."""
    series = (
        db.query(MeasurementSeries)
        .filter(MeasurementSeries.run_id == run_id)
        .all()
    )

    continuous: Dict[str, Tuple[List[float], List[float]]] = {}
    discrete: Dict[str, Tuple[List[float], List[float]]] = {}

    for s in series:
        key = s.canonical_field or s.field_name
        pair = (list(s.time_values or []), list(s.values or []))
        if s.data_kind == DataKind.DISCRETE_OFFLINE.value:
            discrete[key] = pair
        else:
            continuous[key] = pair

    alignment = align_run_series(continuous, discrete)
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run:
        run.alignment = alignment
        run.updated_at = datetime.now(timezone.utc)
        db.commit()

    return alignment


def run_qc_for_manifest(
    stats: Dict[str, Any],
    org_id: str,
    instrument: Optional[str],
    db: Session,
    parsed_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    instrument_key = instrument or "unknown"
    try:
        historical = get_baselines_for_qc(org_id, instrument_key, db)
    except Exception:
        historical = None

    if is_bioprocess_instrument(instrument):
        return bioprocess_qc_summary(stats, instrument=instrument, historical_baselines=historical)

    assay_format = (parsed_metadata or {}).get("assay_format")
    if assay_format and assay_format != "unknown":
        return assay_qc_summary(
            stats=stats,
            assay_format=assay_format,
            precomputed_findings=(parsed_metadata or {}).get("assay_qc"),
            historical_baselines=historical,
        )

    return qc_summary(stats=stats, historical_baselines=historical)


def aggregate_run_stats(db: Session, run_id: int) -> Dict[str, Any]:
    """Merge all series on a run into stats dict for run-level QC."""
    series = (
        db.query(MeasurementSeries)
        .filter(MeasurementSeries.run_id == run_id)
        .all()
    )
    stats: Dict[str, Any] = {}
    for s in series:
        key = s.canonical_field or s.field_name
        if key in stats:
            key = f"{key}_{s.id}"
        numeric_vals = [float(v) for v in (s.values or []) if v is not None]
        stats[key] = {
            "values": list(s.values or []),
            "mean": float(sum(numeric_vals) / len(numeric_vals)) if numeric_vals else None,
            "n": len(numeric_vals),
        }
        stats[f"_time_{s.id}"] = {"values": list(s.time_values or [])}
    return stats


def update_run_qc(
    db: Session,
    run_id: int,
    org_id: str,
    instrument: Optional[str],
    parsed_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stats = aggregate_run_stats(db, run_id)
    if not stats:
        return {"overall_status": "unknown", "summary": "No measurement series on run."}

    qc = run_qc_for_manifest(stats, org_id, instrument, db, parsed_metadata=parsed_metadata)
    run = db.query(RunRecord).filter(RunRecord.id == run_id).first()
    if run:
        run.qc = qc
        db.commit()
    return qc
