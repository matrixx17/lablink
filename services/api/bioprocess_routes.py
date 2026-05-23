"""API routes for bioprocess runs, dashboard data, auth, and compliance."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import resolve_auth, require_org_access, create_api_key
from compliance import soc2_readiness_checklist
from database import (
    RunRecord, MeasurementSeries, FileRecord, AuditLog, RunStatus,
    Campaign, Batch, TimeseriesData, OfflineSample,
)
from evidence_book import build_evidence_book, EvidenceBookTooLarge
from runs_service import get_or_create_run, rebuild_run_alignment, update_run_qc
from transform import transform_run_to_asm

router = APIRouter(tags=["Bioprocess"])


def get_db():
    from database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class RunCreate(BaseModel):
    org_id: str = "default-org"
    external_run_id: str
    batch_id: Optional[str] = None
    campaign_id: Optional[str] = None
    bioreactor_id: Optional[str] = None
    product: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class RunOut(BaseModel):
    id: int
    org_id: str
    external_run_id: str
    batch_id: Optional[str]
    campaign_id: Optional[str]
    bioreactor_id: Optional[str]
    status: str
    qc: Optional[Dict[str, Any]]
    alignment: Optional[Dict[str, Any]]
    file_count: int = 0


class SeriesOut(BaseModel):
    id: int
    field_name: str
    canonical_field: Optional[str]
    data_kind: str
    time_values: List[float]
    values: List[float]
    point_count: int


class ApiKeyCreate(BaseModel):
    org_id: str
    name: str = "default"


class ApiKeyOut(BaseModel):
    id: int
    org_id: str
    name: str
    key_prefix: str
    api_key: Optional[str] = None


@router.post("/api/v1/runs", response_model=RunOut)
def create_run(
    body: RunCreate,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    require_org_access(body.org_id, org_id)
    run = get_or_create_run(
        db, body.org_id, body.external_run_id,
        batch_id=body.batch_id, campaign_id=body.campaign_id,
        bioreactor_id=body.bioreactor_id, metadata=body.metadata,
    )
    if body.product:
        run.product = body.product
        db.commit()
    fc = db.query(FileRecord).filter(FileRecord.run_id == run.id).count()
    return RunOut(
        id=run.id, org_id=run.org_id, external_run_id=run.external_run_id,
        batch_id=run.batch_id, campaign_id=run.campaign_id,
        bioreactor_id=run.bioreactor_id, status=run.status,
        qc=run.qc, alignment=run.alignment, file_count=fc,
    )


@router.get("/api/v1/runs", response_model=List[RunOut])
def list_runs(
    org_id: str = Query("default-org"),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    q = db.query(RunRecord).filter(RunRecord.org_id == org_id)
    if status:
        q = q.filter(RunRecord.status == status)
    runs = q.order_by(RunRecord.id.desc()).limit(100).all()
    out = []
    for run in runs:
        fc = db.query(FileRecord).filter(FileRecord.run_id == run.id).count()
        out.append(RunOut(
            id=run.id, org_id=run.org_id, external_run_id=run.external_run_id,
            batch_id=run.batch_id, campaign_id=run.campaign_id,
            bioreactor_id=run.bioreactor_id, status=run.status,
            qc=run.qc, alignment=run.alignment, file_count=fc,
        ))
    return out


@router.get("/api/v1/runs/{run_id}", response_model=RunOut)
def get_run(
    run_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    run = db.query(RunRecord).filter(RunRecord.id == run_id, RunRecord.org_id == org_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    fc = db.query(FileRecord).filter(FileRecord.run_id == run.id).count()
    return RunOut(
        id=run.id, org_id=run.org_id, external_run_id=run.external_run_id,
        batch_id=run.batch_id, campaign_id=run.campaign_id,
        bioreactor_id=run.bioreactor_id, status=run.status,
        qc=run.qc, alignment=run.alignment, file_count=fc,
    )


@router.get("/api/v1/runs/{run_id}/series", response_model=List[SeriesOut])
def get_run_series(
    run_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    rows = (
        db.query(MeasurementSeries)
        .filter(MeasurementSeries.run_id == run_id, MeasurementSeries.org_id == org_id)
        .all()
    )
    return [
        SeriesOut(
            id=s.id, field_name=s.field_name, canonical_field=s.canonical_field,
            data_kind=s.data_kind, time_values=s.time_values or [],
            values=s.values or [], point_count=s.point_count,
        )
        for s in rows
    ]


@router.post("/api/v1/runs/{run_id}/align")
def align_run(
    run_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    run = db.query(RunRecord).filter(RunRecord.id == run_id, RunRecord.org_id == org_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    alignment = rebuild_run_alignment(db, run_id)
    update_run_qc(db, run_id, org_id, None)
    return {"run_id": run_id, "alignment": alignment}


@router.get("/api/v1/runs/{run_id}/normalized")
def get_run_normalized(
    run_id: int,
    format: str = Query("asm", description="asm (default) or lablink"),
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    run = db.query(RunRecord).filter(RunRecord.id == run_id, RunRecord.org_id == org_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    if format != "asm":
        raise HTTPException(400, f"Unsupported format '{format}' for run-level export. Only 'asm' is supported.")
    return transform_run_to_asm(db, run)


@router.get("/api/v1/runs/{run_id}/audit")
def get_run_audit(
    run_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    a_org, _ = auth
    require_org_access(org_id, a_org)
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org_id)
        .order_by(AuditLog.id.desc())
        .limit(200)
        .all()
    )
    rid = str(run_id)
    return [
        {
            "id": l.id,
            "timestamp": l.timestamp.isoformat(),
            "action": l.action.value,
            "entity_id": l.entity_id,
            "details": l.details,
        }
        for l in logs
        if l.entity_id == rid or (l.details and l.details.get("run_id") == run_id)
    ]


@router.post("/api/v1/auth/keys", response_model=ApiKeyOut)
def issue_api_key(body: ApiKeyCreate, db: Session = Depends(get_db)):
    record, raw = create_api_key(body.org_id, body.name, db)
    return ApiKeyOut(
        id=record.id, org_id=record.org_id, name=record.name,
        key_prefix=record.key_prefix, api_key=raw,
    )


@router.get("/api/v1/compliance/soc2-readiness")
def soc2_readiness():
    return soc2_readiness_checklist()


# ---------------------------------------------------------------------------
# Wet lab: campaigns / batches / timeseries / offline samples
# ---------------------------------------------------------------------------

class BatchOut(BaseModel):
    id: str
    campaign_id: str
    batch_number: Optional[str]
    bioreactor_model: Optional[str]
    volume_liters: Optional[float]
    cell_line: Optional[str]
    media: Optional[str]
    inoculation_date: Optional[str]
    harvest_date: Optional[str]
    status: str
    extra_params: Optional[Dict[str, Any]]


class CampaignOut(BaseModel):
    id: str
    org_id: str
    name: str
    description: Optional[str]
    domain: str
    extra_params: Optional[Dict[str, Any]]
    created_at: Optional[str]
    batch_count: int = 0


class TimeseriesOut(BaseModel):
    id: str
    batch_id: str
    parameter_name: str
    unit: Optional[str]
    timestamps: List[float]
    values: List[float]
    source_instrument: Optional[str]
    inoculation_unix: Optional[float] = None


class OfflineSampleOut(BaseModel):
    id: str
    batch_id: str
    sample_time_hours: Optional[float]
    sample_time_absolute: Optional[str]
    measurement_name: str
    value: Optional[float]
    unit: Optional[str]
    instrument: Optional[str]
    qc_status: str


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _batch_to_out(b: Batch) -> BatchOut:
    return BatchOut(
        id=b.id,
        campaign_id=b.campaign_id,
        batch_number=b.batch_number,
        bioreactor_model=b.bioreactor_model,
        volume_liters=b.volume_liters,
        cell_line=b.cell_line,
        media=b.media,
        inoculation_date=_iso(b.inoculation_date),
        harvest_date=_iso(b.harvest_date),
        status=b.status,
        extra_params=b.extra_params,
    )


@router.get("/api/v1/campaigns", response_model=List[CampaignOut])
def list_campaigns(
    domain: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    q = db.query(Campaign).filter(Campaign.org_id == org_id)
    if domain:
        q = q.filter(Campaign.domain == domain)
    rows = q.order_by(Campaign.created_at.desc()).all()
    out: List[CampaignOut] = []
    for c in rows:
        bc = db.query(Batch).filter(Batch.campaign_id == c.id).count()
        out.append(CampaignOut(
            id=c.id, org_id=c.org_id, name=c.name, description=c.description,
            domain=c.domain, extra_params=c.extra_params,
            created_at=_iso(c.created_at), batch_count=bc,
        ))
    return out


@router.get("/api/v1/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    require_org_access(org_id, c.org_id)
    batch_count = db.query(Batch).filter(Batch.campaign_id == campaign_id).count()
    return CampaignOut(
        id=c.id, org_id=c.org_id, name=c.name, description=c.description,
        domain=c.domain, extra_params=c.extra_params,
        created_at=_iso(c.created_at), batch_count=batch_count,
    )


@router.get("/api/v1/campaigns/{campaign_id}/batches", response_model=List[BatchOut])
def list_campaign_batches(
    campaign_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    require_org_access(org_id, c.org_id)
    rows = (
        db.query(Batch)
        .filter(Batch.campaign_id == campaign_id)
        .order_by(Batch.batch_number)
        .all()
    )
    return [_batch_to_out(b) for b in rows]


@router.get("/api/v1/batches/{batch_id}", response_model=BatchOut)
def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    b = db.query(Batch).filter(Batch.id == batch_id).first()
    if not b:
        raise HTTPException(404, "Batch not found")
    c = db.query(Campaign).filter(Campaign.id == b.campaign_id).first()
    if c is not None:
        require_org_access(org_id, c.org_id)
    return _batch_to_out(b)


@router.get("/api/v1/batches/{batch_id}/timeseries", response_model=List[TimeseriesOut])
def list_batch_timeseries(
    batch_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    b = db.query(Batch).filter(Batch.id == batch_id).first()
    if not b:
        raise HTTPException(404, "Batch not found")
    c = db.query(Campaign).filter(Campaign.id == b.campaign_id).first()
    if c is not None:
        require_org_access(org_id, c.org_id)
    inoculation_unix = b.inoculation_date.timestamp() if b.inoculation_date else None
    rows = (
        db.query(TimeseriesData)
        .filter(TimeseriesData.batch_id == batch_id)
        .order_by(TimeseriesData.parameter_name)
        .all()
    )
    return [
        TimeseriesOut(
            id=r.id, batch_id=r.batch_id, parameter_name=r.parameter_name,
            unit=r.unit, timestamps=list(r.timestamps or []),
            values=list(r.values or []),
            source_instrument=r.source_instrument,
            inoculation_unix=inoculation_unix,
        )
        for r in rows
    ]


@router.get("/api/v1/batches/{batch_id}/samples", response_model=List[OfflineSampleOut])
def list_batch_samples(
    batch_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    org_id, _ = auth
    b = db.query(Batch).filter(Batch.id == batch_id).first()
    if not b:
        raise HTTPException(404, "Batch not found")
    c = db.query(Campaign).filter(Campaign.id == b.campaign_id).first()
    if c is not None:
        require_org_access(org_id, c.org_id)
    rows = (
        db.query(OfflineSample)
        .filter(OfflineSample.batch_id == batch_id)
        .order_by(OfflineSample.sample_time_hours, OfflineSample.measurement_name)
        .all()
    )
    return [
        OfflineSampleOut(
            id=r.id, batch_id=r.batch_id,
            sample_time_hours=r.sample_time_hours,
            sample_time_absolute=_iso(r.sample_time_absolute),
            measurement_name=r.measurement_name,
            value=r.value, unit=r.unit, instrument=r.instrument,
            qc_status=r.qc_status,
        )
        for r in rows
    ]


@router.get("/api/v1/campaigns/{campaign_id}/export/evidence-book")
def export_campaign_evidence_book(
    campaign_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Bundle the wet lab campaign as a downloadable Evidence Book ZIP:
    summary.pdf, batches.csv, offline_samples.csv, timeseries_summary.csv,
    audit_log.csv, provenance.json (wet-lab-native; NOT a BCO), and
    verification.json with SHA-256 of every other file plus the
    campaign-scoped audit chain status.

    Built in memory; no temp files. Refuses with HTTP 413 if total
    timeseries points exceed the cap — use /batches/{id}/timeseries for
    raw streams.
    """
    org_id, _ = auth
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    require_org_access(org_id, c.org_id)

    try:
        zip_bytes, zip_sha256 = build_evidence_book(db, c, c.org_id)
    except EvidenceBookTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))

    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in c.name
    )
    date_part = datetime.now(timezone.utc).date().isoformat()
    filename = f"{safe_name}_EvidenceBook_{date_part}.zip"

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Evidence-Book-Sha256": zip_sha256,
            "X-Evidence-Book-Bytes": str(len(zip_bytes)),
        },
    )


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "dashboard")


@router.get("/dashboard")
@router.get("/dashboard/")
def dashboard_index():
    path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.isfile(path):
        raise HTTPException(404, "Dashboard not found")
    return FileResponse(path)


@router.get("/dashboard/{path:path}")
def dashboard_assets(path: str):
    full = os.path.normpath(os.path.join(STATIC_DIR, path))
    if not full.startswith(os.path.normpath(STATIC_DIR)) or not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full)
