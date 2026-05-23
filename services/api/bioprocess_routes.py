"""API routes for bioprocess runs, dashboard data, auth, and compliance."""

import hmac
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import resolve_auth, require_org_access, create_api_key
from compliance import soc2_readiness_checklist
from database import (
    RunRecord, MeasurementSeries, FileRecord, AuditLog, RunStatus,
    Campaign, Batch, TimeseriesData, OfflineSample,
)
from evidence_book import build_evidence_book, build_batch_record, EvidenceBookTooLarge
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

class BatchSummaryMetrics(BaseModel):
    peak_vcd: Optional[float] = None
    final_titer: Optional[float] = None
    min_viability: Optional[float] = None
    run_duration_days: Optional[float] = None
    lead_condition: bool = False
    qc_status: Optional[str] = None


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
    summary_metrics: Optional[BatchSummaryMetrics] = None


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
    metadata: Optional[Dict[str, Any]] = None
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


@router.get("/api/v1/campaigns/{campaign_id}/methods")
def get_campaign_methods(
    campaign_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Render a publication-ready methods section for a wet lab campaign."""
    org_id, _ = auth
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    require_org_access(org_id, c.org_id)
    if c.domain != "wetlab":
        raise HTTPException(
            status_code=400,
            detail="Methods export is only available for wet lab campaigns.",
        )
    from bioprocess_methods import generate_methods
    return generate_methods(db, c)


@router.get("/api/v1/campaigns/{campaign_id}/batches", response_model=List[BatchOut])
def list_campaign_batches(
    campaign_id: str,
    include_metrics: bool = Query(
        False,
        description="If true, compute peak_vcd / final_titer / min_viability "
                    "/ run_duration_days / lead_condition per batch and return "
                    "in summary_metrics.",
    ),
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

    outs = [_batch_to_out(b) for b in rows]
    if not include_metrics:
        return outs

    # Compute per-batch summary metrics in one offline-samples query.
    if rows:
        batch_ids = [b.id for b in rows]
        samples = (
            db.query(OfflineSample)
            .filter(OfflineSample.batch_id.in_(batch_ids))
            .all()
        )
        by_batch: Dict[str, List[OfflineSample]] = {}
        for s in samples:
            by_batch.setdefault(s.batch_id, []).append(s)

        for b, out in zip(rows, outs):
            ssamples = by_batch.get(b.id) or []
            metrics = _compute_summary_metrics(b, ssamples)
            out.summary_metrics = metrics
    return outs


def _compute_summary_metrics(
    batch: "Batch", samples: List["OfflineSample"],
) -> BatchSummaryMetrics:
    """Derive peak_vcd / final_titer / min_viability / run_duration_days from
    the batch's offline samples + extra_params. Cheap (single linear pass)."""
    vcd_vals: List[float] = []
    titer_pairs: List[tuple] = []
    via_vals: List[float] = []
    sample_times: List[float] = []
    for s in samples:
        if s.value is None:
            continue
        v = float(s.value)
        if s.sample_time_hours is not None:
            sample_times.append(float(s.sample_time_hours))
        name = (s.measurement_name or "")
        if name in ("vcd_e6_per_ml", "viable_cell_density_e6_per_ml"):
            vcd_vals.append(v)
        elif name == "titer_mg_per_l":
            titer_pairs.append((s.sample_time_hours or 0.0, v))
        elif name == "viability_percent":
            via_vals.append(v)

    titer_pairs.sort(key=lambda p: p[0])
    final_titer = titer_pairs[-1][1] if titer_pairs else None
    duration_days = None
    if sample_times:
        duration_days = (max(sample_times) - min(sample_times)) / 24.0

    extra = batch.extra_params or {}
    return BatchSummaryMetrics(
        peak_vcd=max(vcd_vals) if vcd_vals else None,
        final_titer=final_titer,
        min_viability=min(via_vals) if via_vals else None,
        run_duration_days=duration_days,
        lead_condition=bool(extra.get("lead_condition")),
        qc_status=extra.get("qc_status"),
    )


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
            metadata=r.series_metadata,
            inoculation_unix=inoculation_unix,
        )
        for r in rows
    ]


class QCResultOut(BaseModel):
    check_name: str
    status: str  # pass | warn | fail
    message: str
    numeric_value: Optional[float] = None
    timepoint_h: Optional[float] = None
    parameter: Optional[str] = None


@router.get("/api/v1/batches/{batch_id}/qc", response_model=List[QCResultOut])
def get_batch_qc(
    batch_id: str,
    refresh: bool = Query(False, description="Recompute even if cached"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Return the wet-lab QC engine's results for a batch. Cached on
    `Batch.extra_params["qc_results"]` between runs; pass `?refresh=true`
    to force recomputation.
    """
    org_id, _ = auth
    b = db.query(Batch).filter(Batch.id == batch_id).first()
    if not b:
        raise HTTPException(404, "Batch not found")
    c = db.query(Campaign).filter(Campaign.id == b.campaign_id).first()
    if c is not None:
        require_org_access(org_id, c.org_id)

    cached = (b.extra_params or {}).get("qc_results")
    if cached and not refresh:
        return [QCResultOut(**r) for r in cached]

    from bioprocess_qc import BioprocessQCEngine
    results = BioprocessQCEngine.run_for_batch(db, batch_id)
    return [QCResultOut(**r.to_dict()) for r in results]


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
    format: str = Query(
        "evidence-book",
        description="evidence-book (default) or batch-record (wet lab only)",
    ),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Bundle the wet lab campaign as a downloadable ZIP.

    format=evidence-book (default): VDR-style Evidence Book.
    format=batch-record: pharmaceutical Batch Manufacturing Record (wet lab only).
    """
    org_id, _ = auth
    c = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    require_org_access(org_id, c.org_id)

    fmt = (format or "evidence-book").strip().lower()
    if fmt not in ("evidence-book", "batch-record"):
        raise HTTPException(400, f"Unsupported format '{format}'")

    if fmt == "batch-record" and c.domain != "wetlab":
        raise HTTPException(
            status_code=400,
            detail="Batch record export is only available for wet lab campaigns.",
        )

    try:
        if fmt == "batch-record":
            zip_bytes, zip_sha256 = build_batch_record(db, c, c.org_id)
            prefix = "BatchRecord"
        else:
            zip_bytes, zip_sha256 = build_evidence_book(db, c, c.org_id)
            prefix = "EvidenceBook"
    except EvidenceBookTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))

    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in c.name
    )
    date_part = datetime.now(timezone.utc).date().isoformat()
    filename = f"{safe_name}_{prefix}_{date_part}.zip"

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Sha256": zip_sha256,
            "X-Export-Bytes": str(len(zip_bytes)),
            "X-Export-Format": fmt,
        },
    )


def _demo_reset_secret_matches(provided: Optional[str]) -> bool:
    expected = os.getenv("DEMO_RESET_SECRET", "")
    return bool(expected and provided and hmac.compare_digest(provided, expected))


@router.post("/api/v1/demo/wetlab/reset")
def reset_wetlab_demo(
    x_demo_reset_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Delete and re-seed the demo wet lab campaign."""
    if not _demo_reset_secret_matches(x_demo_reset_secret):
        raise HTTPException(status_code=401, detail="Valid DEMO_RESET_SECRET required")

    from wetlab_seed import delete_wetlab_demo, seed_wetlab_demo

    delete_wetlab_demo(db)
    result = seed_wetlab_demo(db)
    reset_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "ok",
        "reset_at": reset_at,
        **result,
    }


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
