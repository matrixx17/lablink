"""API routes for bioprocess runs, dashboard data, auth, and compliance."""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import resolve_auth, require_org_access, create_api_key
from compliance import soc2_readiness_checklist
from database import RunRecord, MeasurementSeries, FileRecord, AuditLog, RunStatus
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
