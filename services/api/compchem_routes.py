"""
Comp-chem API routes — Layer 2.

Endpoints:
  POST   /api/v1/campaigns                       create / find-or-create
  GET    /api/v1/campaigns/{id}                  detail with run + molecule counts
  POST   /api/v1/runs/ingest                     receive parsed manifest, run QC,
                                                  persist, return run_id
  GET    /api/v1/campaigns/{id}/molecules        list with top metrics per molecule
  GET    /api/v1/molecules/{id}                  full detail with all runs
  GET    /api/v1/runs/{id}                       detail with QC + audit entries
  GET    /api/v1/campaigns/{id}/export           ML-ready flat export (csv|parquet)
  POST   /api/v1/audit/verify/{campaign_id}      recompute campaign hash chain

Mirrors the bioprocess_routes.py pattern: every route depends on resolve_auth
to get (org_id, actor); writes are audited via cc_audit_events. All data is
isolated by org_id.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import resolve_auth, require_org_access
from compchem_ingest import ingest_run_manifest
from compchem_models import (
    AssayResult,
    AuditEvent,
    AuditEventAction,
    Campaign,
    Molecule,
    MoleculeProperty,
    Project,
    Run,
    RunInput,
    RunMetric,
    RunOutput,
    log_cc_audit,
    verify_cc_audit_chain,
)
from database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Comp-Chem"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str = Field(..., description="Project name; unique within org")
    description: Optional[str] = None
    target_name: Optional[str] = Field(None, description="Biological target (e.g. EGFR)")
    target_uniprot: Optional[str] = Field(None, max_length=16)
    indication: Optional[str] = None


class CampaignIn(BaseModel):
    org_id: str = "default-org"
    project_name: str = Field(..., description="Project the campaign belongs to (auto-created if absent)")
    name: str = Field(..., description="Campaign name; unique within (org, project)")
    description: Optional[str] = None
    campaign_type: Optional[str] = Field(
        "lead_optimization",
        description="lead_optimization | hit_identification | virtual_screening | admet_profiling | fragment_growing | other",
    )
    hypothesis: Optional[str] = None
    target_metric: Optional[str] = None
    target_metric_unit: Optional[str] = None
    target_metric_threshold: Optional[float] = None
    project: Optional[ProjectIn] = Field(
        None,
        description="Optional project details to populate on first creation",
    )


class CampaignOut(BaseModel):
    id: int
    org_id: str
    project_id: int
    project_name: str
    name: str
    description: Optional[str]
    campaign_type: str
    status: str
    target_metric: Optional[str]
    target_metric_unit: Optional[str]
    target_metric_threshold: Optional[float]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    run_count: int = 0
    molecule_count: int = 0


class RunIngestResponse(BaseModel):
    run_id: int
    campaign_id: int
    project_id: int
    molecule_id: Optional[int] = None
    molecule_created: bool = False
    metrics_count: int = 0
    qc: Optional[Dict[str, Any]] = None


class MoleculeTopMetric(BaseModel):
    metric_name: str
    best_value: float
    unit: str
    run_id: int


class MoleculeListItem(BaseModel):
    id: int
    inchi_key: str
    canonical_smiles: str
    name: Optional[str]
    external_id: Optional[str]
    molecular_weight: Optional[float]
    formula: Optional[str]
    run_count: int
    top_metrics: List[MoleculeTopMetric] = []


class RunSummary(BaseModel):
    id: int
    run_kind: str
    status: str
    software_name: Optional[str]
    software_version: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    wall_time_s: Optional[float]
    metric_count: int


class MoleculeDetail(BaseModel):
    id: int
    campaign_id: int
    inchi_key: str
    canonical_smiles: str
    inchi: Optional[str]
    name: Optional[str]
    external_id: Optional[str]
    molecular_weight: Optional[float]
    formula: Optional[str]
    properties: Dict[str, Any] = {}
    runs: List[RunSummary] = []
    assay_results: List[Dict[str, Any]] = []


class RunMetricOut(BaseModel):
    id: int
    name: str
    value: float
    unit: str
    confidence: Optional[float]
    stderr: Optional[float]
    metadata: Optional[Dict[str, Any]] = None


class RunInputOut(BaseModel):
    id: int
    input_kind: str
    filename: str
    s3_key: Optional[str]
    file_hash: Optional[str]
    file_size_bytes: Optional[int]


class RunOutputOut(BaseModel):
    id: int
    output_kind: str
    filename: str
    s3_key: Optional[str]
    file_hash: Optional[str]
    file_size_bytes: Optional[int]


class RunDetailOut(BaseModel):
    id: int
    campaign_id: int
    molecule_id: Optional[int]
    external_run_id: Optional[str]
    name: Optional[str]
    run_kind: str
    status: str
    software_name: Optional[str]
    software_version: Optional[str]
    forcefield: Optional[str]
    cli_args: Optional[str]
    compute_environment: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    wall_time_s: Optional[float]
    error_message: Optional[str]
    inputs: List[RunInputOut] = []
    outputs: List[RunOutputOut] = []
    metrics: List[RunMetricOut] = []
    qc: Optional[Dict[str, Any]] = None
    client_qc: Optional[Dict[str, Any]] = None
    audit_events: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/v1/campaigns", response_model=CampaignOut)
def create_campaign(
    body: CampaignIn,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Create a campaign (and parent project if it doesn't exist)."""
    org_id, actor = auth
    require_org_access(body.org_id, org_id)

    # Project: get-or-create, applying any provided details
    project = (
        db.query(Project)
        .filter(Project.org_id == body.org_id, Project.name == body.project_name)
        .first()
    )
    if project is None:
        details = body.project or ProjectIn(name=body.project_name)
        project = Project(
            org_id=body.org_id,
            name=body.project_name,
            description=details.description,
            target_name=details.target_name,
            target_uniprot=details.target_uniprot,
            indication=details.indication,
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        log_cc_audit(
            action=AuditEventAction.PROJECT_CREATED,
            entity_type="project",
            entity_id=str(project.id),
            actor=actor,
            org_id=body.org_id,
            details={"name": body.project_name},
            db=db,
        )

    # Campaign: enforce uniqueness on (org, project, name)
    existing = (
        db.query(Campaign)
        .filter(
            Campaign.org_id == body.org_id,
            Campaign.project_id == project.id,
            Campaign.name == body.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Campaign '{body.name}' already exists in project '{body.project_name}'",
        )

    campaign = Campaign(
        org_id=body.org_id,
        project_id=project.id,
        name=body.name,
        description=body.description,
        campaign_type=body.campaign_type or "lead_optimization",
        hypothesis=body.hypothesis,
        target_metric=body.target_metric,
        target_metric_unit=body.target_metric_unit,
        target_metric_threshold=body.target_metric_threshold,
        started_at=datetime.now(timezone.utc),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    log_cc_audit(
        action=AuditEventAction.CAMPAIGN_CREATED,
        entity_type="campaign",
        entity_id=str(campaign.id),
        actor=actor,
        org_id=body.org_id,
        details={
            "name": body.name,
            "project_id": project.id,
            "campaign_type": campaign.campaign_type,
        },
        db=db,
    )

    return _campaign_to_out(db, campaign, project_name=project.name)


@router.get("/api/v1/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Campaign detail with run + molecule counts."""
    a_org, _ = auth
    require_org_access(org_id, a_org)

    campaign, project = _load_campaign(db, campaign_id, org_id)
    return _campaign_to_out(db, campaign, project_name=project.name)


@router.post("/api/v1/runs/ingest", response_model=RunIngestResponse)
def ingest_run(
    manifest: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Receive a parsed-file manifest from the edge agent, persist the run,
    run server-side QC, write audit log, return run_id.

    Manifest shape: see edge/compchem_agent.py — `org_id`, `project`,
    `campaign` are required; `molecule_smiles` is required for per-molecule
    runs; `parsed` carries the full CompChemParsedResult.to_manifest().
    """
    org_id_param = manifest.get("org_id")
    if not org_id_param:
        raise HTTPException(400, "manifest must include org_id")
    a_org, actor = auth
    require_org_access(org_id_param, a_org)

    try:
        result = ingest_run_manifest(db=db, manifest=manifest, actor=actor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Run ingestion failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    return RunIngestResponse(**result)


@router.get(
    "/api/v1/campaigns/{campaign_id}/molecules",
    response_model=List[MoleculeListItem],
)
def list_campaign_molecules(
    campaign_id: int,
    org_id: str = Query("default-org"),
    limit: int = Query(200, ge=1, le=2000),
    metric_name: Optional[str] = Query(
        None,
        description="If set, top_metrics will be limited to this metric only",
    ),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    List molecules in a campaign with each molecule's best metric per name.

    "Best" semantics: for now, the most-negative value wins (correct for
    docking scores and binding energies). Property-style metrics where
    higher-is-better will need a per-metric direction flag — out of scope
    for Layer 2.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)  # 404 if missing

    mols = (
        db.query(Molecule)
        .filter(Molecule.org_id == org_id, Molecule.campaign_id == campaign_id)
        .order_by(Molecule.id.asc())
        .limit(limit)
        .all()
    )
    if not mols:
        return []

    mol_ids = [m.id for m in mols]

    # Run counts in one query
    run_counts = dict(
        db.query(Run.molecule_id, func.count(Run.id))
        .filter(Run.molecule_id.in_(mol_ids))
        .group_by(Run.molecule_id)
        .all()
    )

    # Best metric per (molecule, metric_name) — fetch min(value) since
    # docking/binding semantics are "lower is better"
    metrics_q = (
        db.query(
            AssayResult.molecule_id,
            AssayResult.metric_name,
            func.min(AssayResult.value).label("best_value"),
            AssayResult.unit,
        )
        .filter(AssayResult.molecule_id.in_(mol_ids))
        .group_by(AssayResult.molecule_id, AssayResult.metric_name, AssayResult.unit)
    )
    if metric_name:
        metrics_q = metrics_q.filter(AssayResult.metric_name == metric_name)

    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for mid, mname, best, unit in metrics_q.all():
        grouped.setdefault(mid, []).append({"metric_name": mname, "best_value": float(best), "unit": unit})

    # Resolve run_id for each best value (separate small query — keeps the
    # group-by clean and the call site shapes the data we want)
    output: List[MoleculeListItem] = []
    for m in mols:
        tops: List[MoleculeTopMetric] = []
        for entry in grouped.get(m.id, []):
            run_id = (
                db.query(AssayResult.run_metric_id, RunMetric.run_id)
                .join(RunMetric, RunMetric.id == AssayResult.run_metric_id)
                .filter(
                    AssayResult.molecule_id == m.id,
                    AssayResult.metric_name == entry["metric_name"],
                    AssayResult.value == entry["best_value"],
                )
                .first()
            )
            tops.append(MoleculeTopMetric(
                metric_name=entry["metric_name"],
                best_value=entry["best_value"],
                unit=entry["unit"],
                run_id=int(run_id[1]) if run_id else 0,
            ))

        output.append(MoleculeListItem(
            id=m.id,
            inchi_key=m.inchi_key,
            canonical_smiles=m.canonical_smiles,
            name=m.name,
            external_id=m.external_id,
            molecular_weight=m.molecular_weight,
            formula=m.formula,
            run_count=int(run_counts.get(m.id, 0)),
            top_metrics=tops,
        ))
    return output


@router.get("/api/v1/molecules/{molecule_id}", response_model=MoleculeDetail)
def get_molecule(
    molecule_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Full molecule detail — all runs that touched this molecule, all assays."""
    a_org, _ = auth
    require_org_access(org_id, a_org)

    mol = (
        db.query(Molecule)
        .filter(Molecule.id == molecule_id, Molecule.org_id == org_id)
        .first()
    )
    if not mol:
        raise HTTPException(404, "Molecule not found")

    properties = {
        p.property_name: {
            "value": p.value,
            "unit": p.unit,
            "source": p.property_source,
        }
        for p in db.query(MoleculeProperty)
        .filter(MoleculeProperty.molecule_id == molecule_id)
        .all()
    }

    # Runs that targeted this molecule (including multi-ligand runs where the
    # molecule shows up in a metric but not on the run row)
    direct_run_ids = {
        r.id for r in db.query(Run).filter(Run.molecule_id == molecule_id).all()
    }
    metric_run_ids = {
        rid for (rid,) in db.query(RunMetric.run_id)
        .filter(RunMetric.molecule_id == molecule_id)
        .distinct()
        .all()
    }
    all_run_ids = direct_run_ids | metric_run_ids
    runs_q = db.query(Run).filter(Run.id.in_(all_run_ids)).order_by(Run.id.desc()).all() \
        if all_run_ids else []

    metric_counts = dict(
        db.query(RunMetric.run_id, func.count(RunMetric.id))
        .filter(RunMetric.run_id.in_(all_run_ids))
        .group_by(RunMetric.run_id)
        .all()
    ) if all_run_ids else {}

    runs_out = [
        RunSummary(
            id=r.id,
            run_kind=r.run_kind,
            status=r.status,
            software_name=r.software_name,
            software_version=r.software_version,
            started_at=r.started_at,
            completed_at=r.completed_at,
            wall_time_s=r.wall_time_s,
            metric_count=int(metric_counts.get(r.id, 0)),
        )
        for r in runs_q
    ]

    assays = [
        {
            "id": a.id,
            "metric_name": a.metric_name,
            "value": a.value,
            "unit": a.unit,
            "passes_threshold": a.passes_threshold,
            "run_metric_id": a.run_metric_id,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in db.query(AssayResult)
        .filter(AssayResult.molecule_id == molecule_id)
        .order_by(AssayResult.id.desc())
        .all()
    ]

    return MoleculeDetail(
        id=mol.id,
        campaign_id=mol.campaign_id,
        inchi_key=mol.inchi_key,
        canonical_smiles=mol.canonical_smiles,
        inchi=mol.inchi,
        name=mol.name,
        external_id=mol.external_id,
        molecular_weight=mol.molecular_weight,
        formula=mol.formula,
        properties=properties,
        runs=runs_out,
        assay_results=assays,
    )


@router.get("/api/v1/runs/{run_id}", response_model=RunDetailOut)
def get_run(
    run_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Full run detail with QC and audit log entries scoped to this run."""
    a_org, _ = auth
    require_org_access(org_id, a_org)

    run = db.query(Run).filter(Run.id == run_id, Run.org_id == org_id).first()
    if not run:
        raise HTTPException(404, "Run not found")

    inputs = [
        RunInputOut(
            id=ri.id, input_kind=ri.input_kind, filename=ri.filename,
            s3_key=ri.s3_key, file_hash=ri.file_hash,
            file_size_bytes=ri.file_size_bytes,
        )
        for ri in db.query(RunInput).filter(RunInput.run_id == run.id).all()
    ]
    outputs = [
        RunOutputOut(
            id=ro.id, output_kind=ro.output_kind, filename=ro.filename,
            s3_key=ro.s3_key, file_hash=ro.file_hash,
            file_size_bytes=ro.file_size_bytes,
        )
        for ro in db.query(RunOutput).filter(RunOutput.run_id == run.id).all()
    ]
    metrics = [
        RunMetricOut(
            id=m.id, name=m.metric_name, value=m.value, unit=m.unit,
            confidence=m.confidence, stderr=m.stderr,
            metadata=m.extra_metadata,
        )
        for m in db.query(RunMetric).filter(RunMetric.run_id == run.id).all()
    ]

    run_meta = run.extra_metadata or {}
    audit = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            AuditEvent.entity_type == "run",
            AuditEvent.entity_id == str(run.id),
        )
        .order_by(AuditEvent.id.asc())
        .all()
    )
    audit_out = [
        {
            "id": a.id,
            "timestamp": a.timestamp.isoformat(),
            "action": a.action,
            "actor": a.actor,
            "details": a.details,
            "record_hash": a.record_hash,
        }
        for a in audit
    ]

    return RunDetailOut(
        id=run.id,
        campaign_id=run.campaign_id,
        molecule_id=run.molecule_id,
        external_run_id=run.external_run_id,
        name=run.name,
        run_kind=run.run_kind,
        status=run.status,
        software_name=run.software_name,
        software_version=run.software_version,
        forcefield=run.forcefield,
        cli_args=run.cli_args,
        compute_environment=run.compute_environment,
        started_at=run.started_at,
        completed_at=run.completed_at,
        wall_time_s=run.wall_time_s,
        error_message=run.error_message,
        inputs=inputs,
        outputs=outputs,
        metrics=metrics,
        qc=run_meta.get("server_qc"),
        client_qc=run_meta.get("client_qc"),
        audit_events=audit_out,
    )


@router.get("/api/v1/campaigns/{campaign_id}/export")
def export_campaign(
    campaign_id: int,
    format: str = Query("csv", description="csv | parquet"),
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Flat ML-ready export of every assay result in a campaign joined with
    molecule + run context — one row per (molecule, metric, run).

    `format=parquet` requires pyarrow in the API container (already pulled
    in by pandas); falls back to CSV with a 501 message if unavailable.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    campaign, project = _load_campaign(db, campaign_id, org_id)

    # Pull the wide join
    rows = (
        db.query(
            AssayResult.id.label("assay_id"),
            AssayResult.metric_name,
            AssayResult.value,
            AssayResult.unit,
            AssayResult.passes_threshold,
            Molecule.id.label("molecule_id"),
            Molecule.inchi_key,
            Molecule.canonical_smiles,
            Molecule.name.label("molecule_name"),
            Molecule.external_id,
            Molecule.molecular_weight,
            Molecule.formula,
            RunMetric.id.label("run_metric_id"),
            RunMetric.confidence,
            RunMetric.stderr,
            Run.id.label("run_id"),
            Run.run_kind,
            Run.status.label("run_status"),
            Run.software_name,
            Run.software_version,
            Run.forcefield,
            Run.compute_environment,
            Run.completed_at,
        )
        .join(Molecule, Molecule.id == AssayResult.molecule_id)
        .join(RunMetric, RunMetric.id == AssayResult.run_metric_id)
        .join(Run, Run.id == RunMetric.run_id)
        .filter(Molecule.campaign_id == campaign_id, AssayResult.org_id == org_id)
        .order_by(AssayResult.id.asc())
        .all()
    )

    columns = [
        "assay_id", "metric_name", "value", "unit", "passes_threshold",
        "molecule_id", "inchi_key", "canonical_smiles", "molecule_name",
        "external_id", "molecular_weight", "formula",
        "run_metric_id", "confidence", "stderr",
        "run_id", "run_kind", "run_status",
        "software_name", "software_version", "forcefield",
        "compute_environment", "completed_at",
    ]
    data = [
        {
            col: (val.isoformat() if isinstance(val, datetime) else val)
            for col, val in zip(columns, row)
        }
        for row in rows
    ]

    fmt = format.lower()
    base_filename = f"campaign_{campaign_id}_{project.name}_{campaign.name}".replace(" ", "_")

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        for row in data:
            writer.writerow(row)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.csv"'},
        )

    if fmt == "parquet":
        try:
            import pandas as pd  # already in requirements
        except ImportError:
            raise HTTPException(501, "parquet export requires pandas + pyarrow")
        df = pd.DataFrame(data, columns=columns)
        buf = io.BytesIO()
        try:
            df.to_parquet(buf, engine="pyarrow", index=False)
        except (ImportError, ValueError) as e:
            raise HTTPException(
                501,
                f"parquet export needs pyarrow installed in the API container: {e}",
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{base_filename}.parquet"'},
        )

    raise HTTPException(400, f"Unsupported format '{format}'. Use 'csv' or 'parquet'.")


@router.post("/api/v1/audit/verify/{campaign_id}")
def verify_campaign_audit_chain(
    campaign_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Recompute the cc_audit_events hash chain for this org and report
    pass/fail. The chain is org-wide (not campaign-scoped) because hash
    chains MUST be sequential — checking only campaign-scoped events would
    leave gaps in the chain.

    We additionally report how many events on the chain reference this
    campaign so the caller can see audit coverage for the campaign of
    interest.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)  # 404 if missing

    chain_result = verify_cc_audit_chain(org_id=org_id, db=db)

    # Coverage: how many events name this campaign by entity_id or details
    cid_str = str(campaign_id)
    campaign_events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            (
                ((AuditEvent.entity_type == "campaign") & (AuditEvent.entity_id == cid_str))
                | (AuditEvent.details["campaign_id"].astext == cid_str)
            ),
        )
        .count()
    )
    chain_result["campaign_event_count"] = int(campaign_events)
    chain_result["status"] = "pass" if chain_result.get("valid") else "fail"
    return chain_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_campaign(db: Session, campaign_id: int, org_id: str):
    """Return (campaign, project) or raise 404."""
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.org_id == org_id)
        .first()
    )
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    project = db.query(Project).filter(Project.id == campaign.project_id).first()
    if not project:
        # Foreign key should prevent this, but defensive
        raise HTTPException(500, "Project for campaign not found")
    return campaign, project


def _campaign_to_out(db: Session, campaign: Campaign, project_name: str) -> CampaignOut:
    run_count = db.query(func.count(Run.id)).filter(
        Run.campaign_id == campaign.id
    ).scalar() or 0
    molecule_count = db.query(func.count(Molecule.id)).filter(
        Molecule.campaign_id == campaign.id
    ).scalar() or 0
    return CampaignOut(
        id=campaign.id,
        org_id=campaign.org_id,
        project_id=campaign.project_id,
        project_name=project_name,
        name=campaign.name,
        description=campaign.description,
        campaign_type=campaign.campaign_type,
        status=campaign.status,
        target_metric=campaign.target_metric,
        target_metric_unit=campaign.target_metric_unit,
        target_metric_threshold=campaign.target_metric_threshold,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        run_count=int(run_count),
        molecule_count=int(molecule_count),
    )
