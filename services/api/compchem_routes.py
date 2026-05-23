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
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from auth import resolve_auth, require_org_access, verify_api_key
from compchem_bco import build_bco
from compchem_ingest import ingest_run_manifest
from demo_seed import (
    DEMO_ADMIN_EMAIL,
    DEMO_ADMIN_PASSWORD,
    DEMO_ORG_ID,
    DEMO_ORG_NAME,
    reset_demo_environment,
)
from compchem_models import (
    AssayResult,
    AuditEvent,
    AuditEventAction,
    Campaign,
    DockingGrid,
    Molecule,
    MoleculeProperty,
    Organization,
    OrgCredential,
    OrgUser,
    Project,
    Run,
    RunInput,
    RunLineage,
    RunMetric,
    RunOutput,
    log_cc_audit,
    verify_cc_audit_chain,
)
from database import SessionLocal
from storage import s3, S3_BUCKET

try:
    from cryptography.fernet import Fernet  # type: ignore
except Exception:  # pragma: no cover - optional dependency in some local dev envs
    Fernet = None  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Comp-Chem"])

LABLINK_MODE = os.getenv("LABLINK_MODE", "hosted").lower()
CRO_UPLOAD_CREDENTIAL_TYPE = "cro_upload"
_CREDENTIAL_SECRET = (
    os.getenv("LABLINK_CREDENTIAL_SECRET")
    or os.getenv("SECRET_KEY")
    or "lablink-dev-credential-secret"
)
_FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(_CREDENTIAL_SECRET.encode("utf-8")).digest())
_FERNET = Fernet(_FERNET_KEY) if Fernet else None


def _is_hosted_mode() -> bool:
    return LABLINK_MODE != "self_hosted"


def _credential_token_hash(raw_token: str) -> str:
    return hmac.new(
        _CREDENTIAL_SECRET.encode("utf-8"),
        raw_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _encode_credential_value(payload: Dict[str, Any]) -> str:
    """
    Store credential metadata as an encrypted blob when cryptography is
    available. The token itself is never stored, only its HMAC hash.
    """
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if _FERNET:
        return "fernet1." + _FERNET.encrypt(raw).decode("ascii")
    return "llenc1." + base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_credential_value(value: str) -> Dict[str, Any]:
    if value.startswith("fernet1."):
        if not _FERNET:
            raise ValueError("Encrypted credential cannot be decoded without cryptography")
        raw = _FERNET.decrypt(value.split(".", 1)[1].encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    if not value.startswith("llenc1."):
        return json.loads(value)
    raw = base64.urlsafe_b64decode(value.split(".", 1)[1].encode("ascii"))
    return json.loads(raw.decode("utf-8"))


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
    lead_molecule_id: Optional[int] = None
    project_name: str
    target_name: Optional[str] = None
    name: str
    description: Optional[str]
    campaign_type: str
    status: str
    metadata: Optional[Dict[str, Any]] = None
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


class OrgCredentialCreate(BaseModel):
    credential_type: str = Field(CRO_UPLOAD_CREDENTIAL_TYPE, pattern="^cro_upload$")
    campaign_id: int
    label: Optional[str] = Field(None, max_length=255)
    expires_at: Optional[datetime] = None


class OrgCredentialIssued(BaseModel):
    id: str
    org_id: str
    credential_type: str
    label: Optional[str]
    created_at: datetime
    expires_at: Optional[datetime]
    token: str = Field(..., description="Returned once. Store in the CRO uploader as a Bearer token.")


class OrgCredentialListItem(BaseModel):
    id: str
    org_id: str
    credential_type: str
    label: Optional[str]
    created_at: datetime
    expires_at: Optional[datetime]


class OrgInfo(BaseModel):
    org_id: str
    name: Optional[str]
    demo_mode: bool = False


class DemoLoginResponse(BaseModel):
    org_id: str
    org_name: str
    email: str
    demo_mode: bool


class DockingGridCreate(BaseModel):
    campaign_id: int
    name: str = Field(..., max_length=255)
    receptor_pdb_s3_key: Optional[str] = Field(None, max_length=1024)
    receptor_pdb_hash: Optional[str] = Field(None, min_length=64, max_length=64)
    software: str = Field(..., max_length=100)
    software_version: Optional[str] = Field(None, max_length=50)
    box_center_x: Optional[float] = None
    box_center_y: Optional[float] = None
    box_center_z: Optional[float] = None
    box_size_x: Optional[float] = None
    box_size_y: Optional[float] = None
    box_size_z: Optional[float] = None
    exhaustiveness: Optional[int] = None
    extra_params: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


class DockingGridRead(DockingGridCreate):
    id: str
    created_at: datetime


class DockingGridSummary(BaseModel):
    id: str
    name: str
    software: str
    box_center_x: Optional[float] = None
    box_center_y: Optional[float] = None
    box_center_z: Optional[float] = None
    box_size_x: Optional[float] = None
    box_size_y: Optional[float] = None
    box_size_z: Optional[float] = None


class DockingGridRunSummary(BaseModel):
    id: int
    software_name: Optional[str]
    created_at: datetime
    top_docking_score: Optional[float] = None


class RunGridUpdate(BaseModel):
    grid_id: str


class MoleculeTopMetric(BaseModel):
    metric_name: str
    best_value: float
    unit: str
    run_id: int


class MoleculeListItem(BaseModel):
    id: int
    inchi_key: str
    canonical_smiles: str
    smiles: Optional[str] = None
    name: Optional[str]
    external_id: Optional[str]
    molecular_weight: Optional[float]
    formula: Optional[str]
    qc_status: Optional[str] = None
    metrics: Dict[str, float] = {}
    run_count: int
    top_metrics: List[MoleculeTopMetric] = []


class RunSummary(BaseModel):
    id: int
    run_kind: str
    status: str
    was_inferred: bool = False
    software_name: Optional[str]
    software_version: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    wall_time_s: Optional[float]
    metric_count: int
    qc_status: Optional[str] = None
    parameters: Dict[str, Any] = {}
    metrics: List[Dict[str, Any]] = []
    audit_events: List[Dict[str, Any]] = []


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
    is_campaign_lead: bool = False
    lead_nomination: Optional[Dict[str, Any]] = None
    lineage: List[Dict[str, Any]] = []


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
    grid_id: Optional[str] = None
    external_run_id: Optional[str]
    name: Optional[str]
    run_kind: str
    status: str
    was_inferred: bool = False
    software_name: Optional[str]
    software_version: Optional[str]
    forcefield: Optional[str]
    config_hash: Optional[str] = None
    cli_args: Optional[str]
    compute_environment: Optional[str]
    compute_details: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    wall_time_s: Optional[float]
    error_message: Optional[str]
    inputs: List[RunInputOut] = []
    outputs: List[RunOutputOut] = []
    metrics: List[RunMetricOut] = []
    qc: Optional[Dict[str, Any]] = None
    client_qc: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    audit_events: List[Dict[str, Any]] = []


class CampaignRunOut(BaseModel):
    id: int
    molecule_id: Optional[int]
    molecule_name: Optional[str]
    molecule_external_id: Optional[str]
    run_kind: str
    status: str
    qc_status: Optional[str]
    was_inferred: bool = False
    software_name: Optional[str]
    software_version: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: Optional[datetime] = None
    actor: Optional[str] = None
    wall_time_s: Optional[float]
    metric_count: int


class SarPoint(BaseModel):
    molecule_id: int
    molecule_name: Optional[str]
    molecule_external_id: Optional[str]
    canonical_smiles: str
    run_id: int
    run_status: str
    qc_status: Optional[str]
    x: float
    y: float
    x_metric: str
    y_metric: str
    x_unit: str
    y_unit: str


class SarResponse(BaseModel):
    metric_names: List[str]
    points: List[SarPoint]


class MethodsResponse(BaseModel):
    campaign_id: str
    campaign_name: str
    generated_at: str
    missing_fields: List[str]
    paragraphs: Dict[str, str]
    full_text: str
    software_versions: Dict[str, List[str]]
    run_counts: Dict[str, int]


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _active_org_credentials(db: Session, org_id: str, credential_type: str = CRO_UPLOAD_CREDENTIAL_TYPE) -> List[OrgCredential]:
    return (
        db.query(OrgCredential)
        .filter(
            OrgCredential.org_id == org_id,
            OrgCredential.credential_type == credential_type,
            OrgCredential.revoked_at.is_(None),
        )
        .all()
    )


def _credential_is_active(record: OrgCredential) -> bool:
    if record.revoked_at is not None:
        return False
    if record.expires_at is None:
        return True
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _find_cro_credential(db: Session, token: str) -> Optional[tuple[OrgCredential, Dict[str, Any]]]:
    token_hash = _credential_token_hash(token)
    records = (
        db.query(OrgCredential)
        .filter(
            OrgCredential.credential_type == CRO_UPLOAD_CREDENTIAL_TYPE,
            OrgCredential.revoked_at.is_(None),
        )
        .all()
    )
    for record in records:
        if not _credential_is_active(record):
            continue
        try:
            payload = _decode_credential_value(record.credential_value)
        except Exception:
            logger.warning("Could not decode credential %s", record.id)
            continue
        if payload.get("token_hash") == token_hash:
            return record, payload
    return None


def _resolve_ingest_auth(
    manifest: Dict[str, Any],
    db: Session,
    fallback_auth: tuple,
    authorization: Optional[str],
) -> tuple[str, str]:
    """
    Ingest accepts:
      - normal auth resolved by resolve_auth (X-API-Key or dev org_id), or
      - Authorization: Bearer <org-api-key>, or
      - Authorization: Bearer <CRO upload credential>.
    """
    token = _bearer_token(authorization)
    if not token:
        return fallback_auth

    api_key_record = verify_api_key(token, db)
    if api_key_record:
        return api_key_record.org_id, f"api-key:{api_key_record.name}"

    credential = _find_cro_credential(db, token)
    if not credential:
        raise HTTPException(status_code=401, detail="Invalid Bearer token")

    record, payload = credential
    manifest_campaign_id = manifest.get("campaign_id")
    if manifest_campaign_id is None:
        raise HTTPException(status_code=403, detail="CRO upload credential requires campaign_id in manifest")
    if str(manifest_campaign_id) != str(payload.get("campaign_id")):
        raise HTTPException(status_code=403, detail="CRO upload credential is scoped to a different campaign")
    if manifest.get("org_id") is not None and str(manifest.get("org_id")) != str(payload.get("org_id") or record.org_id):
        raise HTTPException(status_code=403, detail="CRO upload credential is scoped to a different org")

    return str(payload.get("org_id") or record.org_id), f"cro-upload:{record.id}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/api/v1/orgs/{org_id}/credentials", response_model=OrgCredentialIssued)
def issue_org_credential(
    org_id: str,
    body: OrgCredentialCreate,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Issue a scoped CRO upload credential for a campaign in hosted mode."""
    if not _is_hosted_mode():
        raise HTTPException(status_code=403, detail="Credential issuance is only available in hosted mode")
    a_org, actor = auth
    require_org_access(org_id, a_org)

    org = db.query(Organization).filter(Organization.org_id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    campaign = db.query(Campaign).filter(Campaign.id == body.campaign_id, Campaign.org_id == org_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found for org")

    raw_token = f"cro_{secrets.token_urlsafe(32)}"
    credential_payload = {
        "campaign_id": str(body.campaign_id),
        "org_id": org_id,
        "token_hash": _credential_token_hash(raw_token),
    }
    credential = OrgCredential(
        org_id=org_id,
        credential_type=body.credential_type,
        credential_value=_encode_credential_value(credential_payload),
        label=body.label or f"CRO upload for {campaign.name}",
        expires_at=body.expires_at,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    log_cc_audit(
        action=AuditEventAction.CONFIG_CHANGED,
        entity_type="org_credential",
        entity_id=credential.id,
        actor=actor,
        org_id=org_id,
        details={
            "credential_type": credential.credential_type,
            "campaign_id": body.campaign_id,
            "label": credential.label,
            "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        },
        db=db,
    )

    return OrgCredentialIssued(
        id=credential.id,
        org_id=credential.org_id,
        credential_type=credential.credential_type,
        label=credential.label,
        created_at=credential.created_at,
        expires_at=credential.expires_at,
        token=raw_token,
    )


@router.get("/api/v1/orgs/{org_id}/credentials", response_model=List[OrgCredentialListItem])
def list_org_credentials(
    org_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """List active credentials. Secret credential payloads are never returned."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    return [
        OrgCredentialListItem(
            id=record.id,
            org_id=record.org_id,
            credential_type=record.credential_type,
            label=record.label,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )
        for record in _active_org_credentials(db, org_id)
        if _credential_is_active(record)
    ]


@router.delete("/api/v1/credentials/{credential_id}")
def revoke_credential(
    credential_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Revoke a scoped credential without exposing its stored secret payload."""
    record = db.query(OrgCredential).filter(OrgCredential.id == credential_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Credential not found")
    a_org, actor = auth
    require_org_access(record.org_id, a_org)
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()
    log_cc_audit(
        action=AuditEventAction.CONFIG_CHANGED,
        entity_type="org_credential",
        entity_id=record.id,
        actor=actor,
        org_id=record.org_id,
        details={"revoked": True, "credential_type": record.credential_type},
        db=db,
    )
    return {"status": "revoked", "id": credential_id}


@router.get("/api/v1/orgs/{org_id}", response_model=OrgInfo)
def get_org_info(
    org_id: str,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Org metadata used by the dashboard shell, including demo-mode banner state."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    org = db.query(Organization).filter(Organization.org_id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return OrgInfo(org_id=org.org_id, name=org.name, demo_mode=bool(org.demo_mode))


@router.post("/api/v1/demo/login", response_model=DemoLoginResponse)
def demo_login(db: Session = Depends(get_db)):
    """
    Public demo login bootstrap. This is intentionally not general auth; it
    ensures the demo org/admin exists and returns the org route target.
    """
    org = db.query(Organization).filter(Organization.org_id == DEMO_ORG_ID).first()
    user = db.query(OrgUser).filter(OrgUser.org_id == DEMO_ORG_ID, OrgUser.email == DEMO_ADMIN_EMAIL).first()
    if not org or not user:
        reset_demo_environment(db)
        org = db.query(Organization).filter(Organization.org_id == DEMO_ORG_ID).first()
    return DemoLoginResponse(
        org_id=DEMO_ORG_ID,
        org_name=org.name if org else DEMO_ORG_NAME,
        email=DEMO_ADMIN_EMAIL,
        demo_mode=True,
    )


def _demo_secret_matches(provided: Optional[str]) -> bool:
    expected = os.getenv("DEMO_RESET_SECRET", "")
    return bool(expected and provided and hmac.compare_digest(provided, expected))


@router.post("/demo/reset")
@router.post("/api/v1/demo/reset")
def reset_demo(
    x_demo_reset_secret: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Reset the public demo dataset in-process."""
    bearer = _bearer_token(authorization)
    if not (_demo_secret_matches(x_demo_reset_secret) or _demo_secret_matches(bearer)):
        raise HTTPException(status_code=401, detail="Valid DEMO_RESET_SECRET required")
    return reset_demo_environment(db)


@router.get("/api/v1/campaigns", response_model=List[CampaignOut])
def list_campaigns(
    org_id: str = Query("default-org"),
    status: Optional[str] = None,
    project_name: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """List campaigns for the dashboard landing page."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    if org_id == DEMO_ORG_ID and not db.query(Organization).filter(Organization.org_id == DEMO_ORG_ID).first():
        reset_demo_environment(db)

    q = (
        db.query(Campaign, Project.name.label("project_name"))
        .join(Project, Project.id == Campaign.project_id)
        .filter(Campaign.org_id == org_id)
    )
    if status:
        q = q.filter(Campaign.status == status)
    if project_name:
        q = q.filter(Project.name == project_name)

    rows = q.order_by(Campaign.started_at.desc().nullslast(), Campaign.id.desc()).all()
    return [
        _campaign_to_out(db, campaign, project_name=pname)
        for campaign, pname in rows
    ]


@router.post("/api/v1/campaigns", response_model=CampaignOut)
def create_campaign(
    body: CampaignIn,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Create a campaign (and parent project if it doesn't exist)."""
    org_id, actor = auth
    require_org_access(body.org_id, org_id)

    org = db.query(Organization).filter(Organization.org_id == body.org_id).first()
    if org is None:
        db.add(Organization(org_id=body.org_id, name=body.org_id))
        db.commit()

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


@router.get("/api/v1/campaigns/{campaign_id}/runs", response_model=List[CampaignRunOut])
def list_campaign_runs(
    campaign_id: int,
    org_id: str = Query("default-org"),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """List all runs in a campaign with molecule and QC summaries."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)

    q = (
        db.query(Run, Molecule)
        .outerjoin(Molecule, Molecule.id == Run.molecule_id)
        .filter(Run.org_id == org_id, Run.campaign_id == campaign_id)
    )
    if status:
        q = q.filter(Run.status == status)

    runs = q.order_by(Run.created_at.desc(), Run.id.desc()).all()
    run_ids = [r.id for r, _ in runs]
    metric_counts = dict(
        db.query(RunMetric.run_id, func.count(RunMetric.id))
        .filter(RunMetric.run_id.in_(run_ids))
        .group_by(RunMetric.run_id)
        .all()
    ) if run_ids else {}
    run_actors = {
        int(event.entity_id): event.actor
        for event in db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            AuditEvent.entity_type == "run",
            AuditEvent.entity_id.in_([str(rid) for rid in run_ids]),
            AuditEvent.action == "run_submitted",
        )
        .all()
    } if run_ids else {}

    out: List[CampaignRunOut] = []
    for run, mol in runs:
        server_qc = (run.extra_metadata or {}).get("server_qc") or {}
        out.append(CampaignRunOut(
            id=run.id,
            molecule_id=run.molecule_id,
            molecule_name=mol.name if mol else None,
            molecule_external_id=mol.external_id if mol else None,
            run_kind=run.run_kind,
            status=run.status,
            qc_status=server_qc.get("overall_status"),
            was_inferred=bool(run.was_inferred),
            software_name=run.software_name,
            software_version=run.software_version,
            started_at=run.started_at,
            completed_at=run.completed_at,
            created_at=run.created_at,
            actor=run_actors.get(run.id),
            wall_time_s=run.wall_time_s,
            metric_count=int(metric_counts.get(run.id, 0)),
        ))
    return out


@router.get("/api/v1/campaigns/{campaign_id}/sar", response_model=SarResponse)
def get_campaign_sar(
    campaign_id: int,
    org_id: str = Query("default-org"),
    x_metric: Optional[str] = Query(None),
    y_metric: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    SAR scatter data: one point per molecule/run containing selected metric pair.

    If no axes are provided, the first two available metrics are selected.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)

    metric_names = [
        name for (name,) in (
            db.query(AssayResult.metric_name)
            .join(Molecule, Molecule.id == AssayResult.molecule_id)
            .filter(Molecule.campaign_id == campaign_id, AssayResult.org_id == org_id)
            .distinct()
            .order_by(AssayResult.metric_name.asc())
            .all()
        )
    ]
    if not metric_names:
        return SarResponse(metric_names=[], points=[])

    x_name = x_metric or metric_names[0]
    y_name = y_metric or (metric_names[1] if len(metric_names) > 1 else metric_names[0])

    rows = (
        db.query(
            Molecule.id.label("molecule_id"),
            Molecule.name,
            Molecule.external_id,
            Molecule.canonical_smiles,
            Run.id.label("run_id"),
            Run.status.label("run_status"),
            Run.extra_metadata,
            AssayResult.metric_name,
            AssayResult.value,
            AssayResult.unit,
        )
        .join(RunMetric, RunMetric.id == AssayResult.run_metric_id)
        .join(Run, Run.id == RunMetric.run_id)
        .join(Molecule, Molecule.id == AssayResult.molecule_id)
        .filter(
            Molecule.campaign_id == campaign_id,
            AssayResult.org_id == org_id,
            AssayResult.metric_name.in_([x_name, y_name]),
        )
        .all()
    )

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row.molecule_id, row.run_id)
        entry = grouped.setdefault(key, {
            "molecule_id": row.molecule_id,
            "molecule_name": row.name,
            "molecule_external_id": row.external_id,
            "canonical_smiles": row.canonical_smiles,
            "run_id": row.run_id,
            "run_status": row.run_status,
            "qc_status": ((row.extra_metadata or {}).get("server_qc") or {}).get("overall_status"),
            "metrics": {},
        })
        entry["metrics"][row.metric_name] = {"value": row.value, "unit": row.unit}

    points: List[SarPoint] = []
    for entry in grouped.values():
        metrics = entry["metrics"]
        if x_name not in metrics or y_name not in metrics:
            continue
        points.append(SarPoint(
            molecule_id=entry["molecule_id"],
            molecule_name=entry["molecule_name"],
            molecule_external_id=entry["molecule_external_id"],
            canonical_smiles=entry["canonical_smiles"],
            run_id=entry["run_id"],
            run_status=entry["run_status"],
            qc_status=entry["qc_status"],
            x=float(metrics[x_name]["value"]),
            y=float(metrics[y_name]["value"]),
            x_metric=x_name,
            y_metric=y_name,
            x_unit=metrics[x_name]["unit"],
            y_unit=metrics[y_name]["unit"],
        ))

    return SarResponse(metric_names=metric_names, points=points)


@router.get("/api/v1/campaigns/{campaign_id}/methods")
def get_campaign_methods(
    campaign_id: int,
    org_id: str = Query("default-org"),
    format: str = Query("json", pattern="^(json|text)$"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Generate a deterministic journal-methods draft from structured run data.

    No LLM calls are made; this is pure template interpolation from the
    campaign's runs, run metadata, metrics, and associated docking grids.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    campaign, _ = _load_campaign(db, campaign_id, org_id)
    methods = _build_methods_section(db, campaign)
    if format == "text":
        return Response(content=methods["full_text"], media_type="text/plain")
    return methods


@router.get("/api/v1/campaigns/{campaign_id}/config-template")
def get_campaign_config_template(
    campaign_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Download a starter .lablink.yaml for this campaign."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    campaign, _ = _load_campaign(db, campaign_id, org_id)
    content = f'''# LabLink Campaign Configuration
# Generated for: {campaign.name}
# Hand this file to your CRO or drop it in your output directory.

campaign_id: "{campaign.id}"
org_token: "PASTE_YOUR_TOKEN_HERE"

# The molecule being studied (SMILES string)
# You can override this per-run using the --molecule CLI flag
molecule_smiles: ""

# Run type: one of md, docking, dft, property, other
run_type: ""

# Optional: associate with a specific docking grid
# grid_id: ""

# Optional: override software detection
# software_name: ""
# software_version: ""
'''
    return Response(
        content=content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="lablink_campaign.yaml"'},
    )


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


@router.post("/api/v1/campaigns/{campaign_id}/grids", response_model=DockingGridRead)
def create_docking_grid(
    campaign_id: int,
    body: DockingGridCreate,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Create a docking grid definition scoped to a campaign."""
    a_org, actor = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)
    if body.campaign_id != campaign_id:
        raise HTTPException(400, "body.campaign_id must match path campaign_id")

    existing = (
        db.query(DockingGrid)
        .filter(DockingGrid.campaign_id == campaign_id, DockingGrid.name == body.name)
        .first()
    )
    if existing:
        raise HTTPException(409, "Docking grid name already exists for this campaign")

    grid = DockingGrid(**body.model_dump())
    db.add(grid)
    db.commit()
    db.refresh(grid)
    log_cc_audit(
        action=AuditEventAction.CONFIG_CHANGED,
        entity_type="docking_grid",
        entity_id=str(grid.id),
        actor=actor,
        org_id=org_id,
        details={"campaign_id": campaign_id, "name": grid.name, "software": grid.software},
        db=db,
    )
    return _grid_to_read(grid)


@router.get("/api/v1/campaigns/{campaign_id}/grids", response_model=List[DockingGridRead])
def list_docking_grids(
    campaign_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """List docking grid definitions for a campaign."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)
    grids = (
        db.query(DockingGrid)
        .filter(DockingGrid.campaign_id == campaign_id)
        .order_by(DockingGrid.created_at.desc(), DockingGrid.name.asc())
        .all()
    )
    return [_grid_to_read(g) for g in grids]


@router.get("/api/v1/grids/{grid_id}", response_model=DockingGridRead)
def get_docking_grid(
    grid_id: str,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Get one docking grid definition."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    grid = _load_grid(db, grid_id, org_id)
    return _grid_to_read(grid)


@router.get("/api/v1/grids/{grid_id}/runs", response_model=List[DockingGridRunSummary])
def list_grid_runs(
    grid_id: str,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """List runs associated with a docking grid, newest first."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_grid(db, grid_id, org_id)
    runs = (
        db.query(Run)
        .filter(Run.grid_id == grid_id, Run.org_id == org_id)
        .order_by(Run.created_at.desc())
        .all()
    )
    out: List[DockingGridRunSummary] = []
    for run in runs:
        top_score = (
            db.query(RunMetric.value)
            .filter(
                RunMetric.run_id == run.id,
                RunMetric.metric_name == "docking_score_top",
            )
            .order_by(RunMetric.id.desc())
            .first()
        )
        out.append(DockingGridRunSummary(
            id=run.id,
            software_name=run.software_name,
            created_at=run.created_at,
            top_docking_score=float(top_score[0]) if top_score else None,
        ))
    return out


@router.post("/api/v1/runs/ingest", response_model=RunIngestResponse)
def ingest_run(
    manifest: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
    authorization: Optional[str] = Header(None),
):
    """
    Receive a parsed-file manifest from the edge agent, persist the run,
    run server-side QC, write audit log, return run_id.

    Manifest shape: see edge/compchem_agent.py — `org_id`, `project`,
    `campaign` are required; `molecule_smiles` is required for per-molecule
    runs; `parsed` carries the full CompChemParsedResult.to_manifest().
    """
    a_org, actor = _resolve_ingest_auth(manifest, db, auth, authorization)
    org_id_param = manifest.get("org_id") or a_org
    manifest["org_id"] = org_id_param
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
    include_metrics: bool = Query(False),
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

    flat_metrics: Dict[int, Dict[str, float]] = {}
    qc_by_molecule: Dict[int, str] = {}
    if include_metrics:
        for mid, entries in grouped.items():
            flat_metrics[mid] = {entry["metric_name"]: entry["best_value"] for entry in entries}
        for prop in (
            db.query(MoleculeProperty)
            .filter(MoleculeProperty.molecule_id.in_(mol_ids), MoleculeProperty.value.isnot(None))
            .all()
        ):
            flat_metrics.setdefault(prop.molecule_id, {})[prop.property_name] = float(prop.value)
        for m in mols:
            if m.molecular_weight is not None:
                flat_metrics.setdefault(m.id, {}).setdefault("mw", float(m.molecular_weight))

        statuses: Dict[int, List[str]] = {}
        for run in db.query(Run).filter(Run.molecule_id.in_(mol_ids)).all():
            meta = run.extra_metadata or {}
            qc = meta.get("server_qc") if isinstance(meta, dict) else None
            status = qc.get("overall_status") if isinstance(qc, dict) else None
            if status:
                statuses.setdefault(run.molecule_id, []).append(str(status))
        qc_by_molecule = {mid: _worst_qc_status(vals) for mid, vals in statuses.items()}

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
            smiles=m.canonical_smiles if include_metrics else None,
            name=m.name,
            external_id=m.external_id,
            molecular_weight=m.molecular_weight,
            formula=m.formula,
            qc_status=qc_by_molecule.get(m.id) if include_metrics else None,
            metrics=flat_metrics.get(m.id, {}) if include_metrics else {},
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

    run_metrics: Dict[int, List[Dict[str, Any]]] = {}
    if all_run_ids:
        for metric in db.query(RunMetric).filter(RunMetric.run_id.in_(all_run_ids)).order_by(RunMetric.id.asc()).all():
            run_metrics.setdefault(metric.run_id, []).append({
                "id": metric.id,
                "name": metric.metric_name,
                "value": metric.value,
                "unit": metric.unit,
                "metadata": metric.extra_metadata,
            })

    run_audit: Dict[int, List[Dict[str, Any]]] = {}
    if all_run_ids:
        for event in (
            db.query(AuditEvent)
            .filter(
                AuditEvent.org_id == org_id,
                AuditEvent.entity_type == "run",
                AuditEvent.entity_id.in_([str(rid) for rid in all_run_ids]),
            )
            .order_by(AuditEvent.id.asc())
            .all()
        ):
            try:
                rid = int(event.entity_id)
            except ValueError:
                continue
            run_audit.setdefault(rid, []).append({
                "id": event.id,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "action": event.action,
                "actor": event.actor,
                "details": event.details,
            })

    runs_out = []
    for r in runs_q:
        meta = r.extra_metadata or {}
        server_qc = meta.get("server_qc") if isinstance(meta, dict) else None
        parsed_meta = meta.get("parsed_metadata") if isinstance(meta, dict) else None
        run_metadata = meta.get("run_metadata") if isinstance(meta, dict) else None
        parameters = {}
        if isinstance(parsed_meta, dict):
            parameters.update(parsed_meta)
        if isinstance(run_metadata, dict):
            parameters.update(run_metadata)
        if r.compute_details:
            parameters["compute_details"] = r.compute_details
        runs_out.append(RunSummary(
            id=r.id,
            run_kind=r.run_kind,
            status=r.status,
            was_inferred=bool(r.was_inferred),
            software_name=r.software_name,
            software_version=r.software_version,
            started_at=r.started_at,
            completed_at=r.completed_at,
            wall_time_s=r.wall_time_s,
            metric_count=int(metric_counts.get(r.id, 0)),
            qc_status=server_qc.get("overall_status") if isinstance(server_qc, dict) else None,
            parameters=parameters,
            metrics=run_metrics.get(r.id, []),
            audit_events=run_audit.get(r.id, []),
        ))

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

    campaign = db.query(Campaign).filter(Campaign.id == mol.campaign_id, Campaign.org_id == org_id).first()
    lead_event = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            AuditEvent.action == "lead_nominated",
            AuditEvent.entity_type == "molecule",
            AuditEvent.entity_id == str(mol.id),
        )
        .order_by(AuditEvent.id.desc())
        .first()
    )
    lineage = [
        {
            "parent_run_id": row.parent_run_id,
            "child_run_id": row.child_run_id,
            "relationship": row.relationship,
            "metadata": row.extra_metadata,
        }
        for row in db.query(RunLineage)
        .filter(
            (RunLineage.parent_run_id.in_(all_run_ids)) |
            (RunLineage.child_run_id.in_(all_run_ids))
        )
        .all()
    ] if all_run_ids else []

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
        is_campaign_lead=bool(campaign and campaign.lead_molecule_id == mol.id),
        lead_nomination={
            "timestamp": lead_event.timestamp.isoformat() if lead_event and lead_event.timestamp else None,
            "actor": lead_event.actor,
            "details": lead_event.details,
        } if lead_event else None,
        lineage=lineage,
    )


@router.get("/api/v1/molecules/{molecule_id}/structure.svg")
def get_molecule_structure_svg(
    molecule_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Render a molecule as SVG using server-side RDKit."""
    a_org, _ = auth
    require_org_access(org_id, a_org)

    mol_row = (
        db.query(Molecule)
        .filter(Molecule.id == molecule_id, Molecule.org_id == org_id)
        .first()
    )
    if not mol_row:
        raise HTTPException(404, "Molecule not found")

    try:
        from rdkit import Chem  # type: ignore
        from rdkit.Chem import rdDepictor  # type: ignore
        from rdkit.Chem.Draw import rdMolDraw2D  # type: ignore
    except ImportError:
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='360' height='220'>"
            "<rect width='100%' height='100%' fill='#f8fafc'/>"
            "<text x='24' y='110' font-size='14' fill='#64748b'>RDKit unavailable</text>"
            "</svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")

    mol = Chem.MolFromSmiles(mol_row.canonical_smiles)
    if mol is None:
        raise HTTPException(422, "Stored SMILES could not be parsed by RDKit")
    rdDepictor.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(360, 240)
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    return Response(content=drawer.GetDrawingText(), media_type="image/svg+xml")


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
        grid_id=str(run.grid_id) if run.grid_id else None,
        external_run_id=run.external_run_id,
        name=run.name,
        run_kind=run.run_kind,
        status=run.status,
        was_inferred=bool(run.was_inferred),
        software_name=run.software_name,
        software_version=run.software_version,
        forcefield=run.forcefield,
        config_hash=run.config_hash,
        cli_args=run.cli_args,
        compute_environment=run.compute_environment,
        compute_details=run.compute_details,
        started_at=run.started_at,
        completed_at=run.completed_at,
        wall_time_s=run.wall_time_s,
        error_message=run.error_message,
        inputs=inputs,
        outputs=outputs,
        metrics=metrics,
        qc=run_meta.get("server_qc"),
        client_qc=run_meta.get("client_qc"),
        metadata=run_meta,
        audit_events=audit_out,
    )


@router.patch("/api/v1/runs/{run_id}/grid", response_model=RunDetailOut)
def update_run_grid(
    run_id: int,
    body: RunGridUpdate,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Associate an existing run with a docking grid."""
    a_org, actor = auth
    require_org_access(org_id, a_org)
    run = db.query(Run).filter(Run.id == run_id, Run.org_id == org_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    grid = _load_grid(db, body.grid_id, org_id)
    if grid.campaign_id != run.campaign_id:
        raise HTTPException(400, "Grid must belong to the same campaign as the run")

    run.grid_id = grid.id
    db.commit()
    log_cc_audit(
        action=AuditEventAction.CONFIG_CHANGED,
        entity_type="run",
        entity_id=str(run.id),
        actor=actor,
        org_id=org_id,
        details={"campaign_id": run.campaign_id, "grid_id": grid.id, "grid_name": grid.name},
        db=db,
    )
    db.refresh(run)
    return get_run(run_id=run_id, org_id=org_id, db=db, auth=auth)


@router.get("/api/v1/artifacts/{artifact_type}/{artifact_id}/download")
def get_artifact_download_url(
    artifact_type: str,
    artifact_id: int,
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Return a short-lived presigned URL for a raw input/output artifact."""
    a_org, _ = auth
    require_org_access(org_id, a_org)

    if artifact_type == "input":
        artifact = db.query(RunInput).filter(RunInput.id == artifact_id, RunInput.org_id == org_id).first()
    elif artifact_type == "output":
        artifact = db.query(RunOutput).filter(RunOutput.id == artifact_id, RunOutput.org_id == org_id).first()
    else:
        raise HTTPException(400, "artifact_type must be 'input' or 'output'")

    if not artifact:
        raise HTTPException(404, "Artifact not found")
    if not artifact.s3_key:
        raise HTTPException(404, "Artifact has no stored object key")

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": artifact.s3_key},
        ExpiresIn=900,
    )
    return {"url": url, "expires_in_seconds": 900, "filename": artifact.filename}


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


@router.get("/api/v1/campaigns/{campaign_id}/audit")
def list_campaign_audit_events(
    campaign_id: int,
    org_id: str = Query("default-org"),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """Human-readable campaign audit feed for the dashboard."""
    a_org, _ = auth
    require_org_access(org_id, a_org)
    _load_campaign(db, campaign_id, org_id)

    cid_str = str(campaign_id)
    rows = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            (
                ((AuditEvent.entity_type == "campaign") & (AuditEvent.entity_id == cid_str))
                | (AuditEvent.details["campaign_id"].astext == cid_str)
            ),
        )
        .order_by(AuditEvent.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            "action": a.action,
            "entity_type": a.entity_type,
            "entity_id": a.entity_id,
            "actor": a.actor,
            "details": a.details,
            "previous_hash": a.previous_hash,
            "record_hash": a.record_hash,
        }
        for a in rows
    ]


@router.get("/api/v1/campaigns/{campaign_id}/export/bco")
def export_campaign_bco(
    campaign_id: int,
    download: bool = Query(False, description="If true, send as attachment"),
    org_id: str = Query("default-org"),
    db: Session = Depends(get_db),
    auth: tuple = Depends(resolve_auth),
):
    """
    Export the campaign as an IEEE 2791-2020 BioCompute Object.

    The etag is SHA-256 of the canonical JSON of the document with the etag
    field zeroed; recompute on verify by zeroing etag and re-hashing.
    """
    a_org, _ = auth
    require_org_access(org_id, a_org)
    campaign, project = _load_campaign(db, campaign_id, org_id)

    bco = build_bco(db, campaign, project)
    headers: Dict[str, str] = {}
    if download:
        safe_name = "".join(
            c if c.isalnum() or c in ("-", "_") else "_" for c in campaign.name
        )
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}_BCO.json"'
    return Response(
        content=json.dumps(bco, indent=2, default=str),
        media_type="application/json",
        headers=headers,
    )


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


def _load_grid(db: Session, grid_id: str, org_id: str) -> DockingGrid:
    grid = (
        db.query(DockingGrid)
        .join(Campaign, Campaign.id == DockingGrid.campaign_id)
        .filter(DockingGrid.id == grid_id, Campaign.org_id == org_id)
        .first()
    )
    if not grid:
        raise HTTPException(404, "Docking grid not found")
    return grid


def _grid_to_read(grid: DockingGrid) -> DockingGridRead:
    return DockingGridRead(
        id=str(grid.id),
        campaign_id=grid.campaign_id,
        name=grid.name,
        receptor_pdb_s3_key=grid.receptor_pdb_s3_key,
        receptor_pdb_hash=grid.receptor_pdb_hash,
        software=grid.software,
        software_version=grid.software_version,
        box_center_x=grid.box_center_x,
        box_center_y=grid.box_center_y,
        box_center_z=grid.box_center_z,
        box_size_x=grid.box_size_x,
        box_size_y=grid.box_size_y,
        box_size_z=grid.box_size_z,
        exhaustiveness=grid.exhaustiveness,
        extra_params=grid.extra_params,
        created_at=grid.created_at,
        notes=grid.notes,
    )


def _build_methods_section(db: Session, campaign: Campaign) -> Dict[str, Any]:
    runs = (
        db.query(Run)
        .filter(Run.campaign_id == campaign.id, Run.org_id == campaign.org_id)
        .order_by(Run.created_at.asc(), Run.id.asc())
        .all()
    )
    grouped: Dict[str, List[Run]] = {k: [] for k in ("md", "docking", "dft", "property", "other")}
    for run in runs:
        grouped[_methods_run_type(run.run_kind)].append(run)

    grids_by_id: Dict[str, DockingGrid] = {}
    grid_ids = sorted({r.grid_id for r in runs if r.grid_id})
    if grid_ids:
        grids_by_id = {
            g.id: g for g in db.query(DockingGrid).filter(DockingGrid.id.in_(grid_ids)).all()
        }

    metrics_by_run: Dict[int, List[RunMetric]] = {}
    if runs:
        for metric in db.query(RunMetric).filter(RunMetric.run_id.in_([r.id for r in runs])).all():
            metrics_by_run.setdefault(metric.run_id, []).append(metric)

    missing_fields: List[str] = []
    paragraphs: Dict[str, str] = {}
    run_counts: Dict[str, int] = {k: len(v) for k, v in grouped.items() if v}
    software_versions = _software_versions(runs)

    if grouped["md"]:
        paragraphs["md"] = _methods_md_paragraph(grouped["md"], missing_fields)
    if grouped["docking"]:
        paragraphs["docking"] = _methods_docking_paragraph(
            grouped["docking"], grids_by_id, metrics_by_run, missing_fields,
        )
    if grouped["dft"]:
        paragraphs["dft"] = _methods_dft_paragraph(grouped["dft"], missing_fields)
    if grouped["property"]:
        paragraphs["property"] = _methods_property_paragraph(grouped["property"], metrics_by_run)
    if grouped["other"]:
        paragraphs["other"] = (
            f"Additional computational runs were recorded for {len(grouped['other'])} jobs "
            "that were not classified as molecular dynamics, docking, quantum mechanical, "
            "or molecular property calculations."
        )

    full_text = "\n\n".join(
        paragraphs[k] for k in ("md", "docking", "dft", "property", "other") if k in paragraphs
    )
    return {
        "campaign_id": str(campaign.id),
        "campaign_name": campaign.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "missing_fields": _unique_ordered(missing_fields),
        "paragraphs": paragraphs,
        "full_text": full_text,
        "software_versions": software_versions,
        "run_counts": run_counts,
    }


def _methods_run_type(run_kind: Optional[str]) -> str:
    value = (run_kind or "other").lower()
    if value in ("md", "molecular_dynamics", "free_energy"):
        return "md"
    if value == "docking":
        return "docking"
    if value in ("dft", "semi_empirical"):
        return "dft"
    if value in ("property", "property_prediction", "admet_profiling"):
        return "property"
    return "other"


def _software_versions(runs: List[Run]) -> Dict[str, List[str]]:
    versions: Dict[str, set] = {}
    for run in runs:
        if not run.software_name:
            continue
        versions.setdefault(run.software_name, set()).add(run.software_version or "[not recorded]")
    return {name: sorted(vals) for name, vals in sorted(versions.items())}


def _run_params(run: Run) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if isinstance(run.compute_details, dict):
        params.update(run.compute_details)
    meta = run.extra_metadata if isinstance(run.extra_metadata, dict) else {}
    for key in ("parsed_metadata", "run_metadata", "extra_params"):
        if isinstance(meta.get(key), dict):
            params.update(meta[key])
    if isinstance(meta.get("metadata"), dict):
        params.update(meta["metadata"])
    if run.forcefield and not params.get("forcefield"):
        params["forcefield"] = run.forcefield
    return params


def _first_recorded(
    runs: List[Run],
    field: str,
    missing_fields: List[str],
    run_attr: Optional[str] = None,
) -> Any:
    for run in runs:
        if run_attr:
            value = getattr(run, run_attr, None)
            if value not in (None, ""):
                return value
        params = _run_params(run)
        value = params.get(field)
        if value not in (None, ""):
            return value
    missing_fields.append(field)
    return "[not recorded]"


def _methods_md_paragraph(runs: List[Run], missing_fields: List[str]) -> str:
    timestep = _first_recorded(runs, "timestep_fs", missing_fields)
    n_steps = _first_recorded(runs, "n_steps", missing_fields)
    total_time = _first_recorded(runs, "total_time_ns", missing_fields)
    if total_time == "[not recorded]" and timestep != "[not recorded]" and n_steps != "[not recorded]":
        try:
            total_time = float(timestep) * float(n_steps) / 1_000_000
            # Remove the total_time_ns missing marker because it was derived.
            if "total_time_ns" in missing_fields:
                missing_fields.remove("total_time_ns")
        except (TypeError, ValueError):
            pass

    return (
        "Molecular dynamics simulations were performed using "
        f"{_first_recorded(runs, 'software_name', missing_fields, 'software_name')} "
        f"{_first_recorded(runs, 'software_version', missing_fields, 'software_version')}. "
        f"The {_first_recorded(runs, 'forcefield', missing_fields)} force field was used for all "
        "simulations. Production runs were carried out in the "
        f"{_first_recorded(runs, 'ensemble', missing_fields)} ensemble at "
        f"{_first_recorded(runs, 'temperature_k', missing_fields)} K and "
        f"{_first_recorded(runs, 'pressure_bar', missing_fields)} bar with a {timestep} fs "
        f"timestep for {_format_methods_value(total_time)} ns. A total of {len(runs)} independent "
        "simulations were performed."
    )


def _methods_docking_paragraph(
    runs: List[Run],
    grids_by_id: Dict[str, DockingGrid],
    metrics_by_run: Dict[int, List[RunMetric]],
    missing_fields: List[str],
) -> str:
    grid = next((grids_by_id.get(r.grid_id) for r in runs if r.grid_id and grids_by_id.get(r.grid_id)), None)
    params = _run_params(runs[0])
    if grid and isinstance(grid.extra_params, dict):
        params = {**params, **grid.extra_params}

    def val(name: str, grid_attr: Optional[str] = None) -> Any:
        if grid_attr and grid is not None:
            grid_value = getattr(grid, grid_attr, None)
            if grid_value not in (None, ""):
                return grid_value
        for run in runs:
            run_params = _run_params(run)
            value = run_params.get(name)
            if value not in (None, ""):
                return value
        if name in params and params[name] not in (None, ""):
            return params[name]
        missing_fields.append(name)
        return "[not recorded]"

    n_poses = val("n_poses")
    if n_poses == "[not recorded]":
        pose_counts = [
            len([m for m in metrics_by_run.get(run.id, []) if "pose" in (m.metric_name or "").lower()])
            for run in runs
        ]
        pose_counts = [c for c in pose_counts if c > 0]
        if pose_counts:
            n_poses = max(pose_counts)
            if "n_poses" in missing_fields:
                missing_fields.remove("n_poses")

    molecule_count = len({r.molecule_id for r in runs if r.molecule_id})
    return (
        "Molecular docking was performed using "
        f"{_first_recorded(runs, 'software_name', missing_fields, 'software_name')} "
        f"{_first_recorded(runs, 'software_version', missing_fields, 'software_version')} with the "
        f"{val('scoring_function')} scoring function. Docking grids were centered at "
        f"({_format_methods_value(val('box_center_x', 'box_center_x'))}, "
        f"{_format_methods_value(val('box_center_y', 'box_center_y'))}, "
        f"{_format_methods_value(val('box_center_z', 'box_center_z'))}) Å with dimensions "
        f"{_format_methods_value(val('box_size_x', 'box_size_x'))} × "
        f"{_format_methods_value(val('box_size_y', 'box_size_y'))} × "
        f"{_format_methods_value(val('box_size_z', 'box_size_z'))} Å. Exhaustiveness was set to "
        f"{_format_methods_value(val('exhaustiveness', 'exhaustiveness'))}. The top "
        f"{_format_methods_value(n_poses)} poses were retained for each ligand. A total of "
        f"{len(runs)} docking runs were performed across {molecule_count} compounds."
    )


def _methods_dft_paragraph(runs: List[Run], missing_fields: List[str]) -> str:
    functional = _first_recorded(runs, "functional", missing_fields)
    basis_set = _first_recorded(runs, "basis_set", missing_fields)
    solvent_model = _optional_first_recorded(runs, "solvent_model")
    dispersion = _optional_first_recorded(runs, "dispersion_correction")
    solvent_line = (
        f"Solvation was treated using the {solvent_model} implicit solvent model."
        if solvent_model else ""
    )
    dispersion_line = (
        f"Grimme's {dispersion} dispersion correction was applied."
        if dispersion else ""
    )
    middle = " ".join(part for part in (solvent_line, dispersion_line) if part)
    if middle:
        middle += " "
    return (
        "Quantum mechanical calculations were performed using "
        f"{_first_recorded(runs, 'software_name', missing_fields, 'software_name')} "
        f"{_first_recorded(runs, 'software_version', missing_fields, 'software_version')}. "
        "Geometry optimizations and single-point energy calculations were carried out at the "
        f"{functional}/{basis_set} level of theory. {middle}A total of {len(runs)} "
        "calculations were performed."
    )


def _methods_property_paragraph(
    runs: List[Run],
    metrics_by_run: Dict[int, List[RunMetric]],
) -> str:
    metric_names = sorted({
        metric.metric_name
        for run in runs
        for metric in metrics_by_run.get(run.id, [])
        if metric.metric_name
    })
    molecule_count = len({r.molecule_id for r in runs if r.molecule_id})
    metric_text = ", ".join(metric_names) if metric_names else "[not recorded]"
    return (
        f"Molecular properties including {metric_text} were computed for all "
        f"{molecule_count} compounds using RDKit."
    )


def _optional_first_recorded(runs: List[Run], field: str) -> Optional[Any]:
    for run in runs:
        value = _run_params(run).get(field)
        if value not in (None, ""):
            return value
    return None


def _format_methods_value(value: Any) -> str:
    if value == "[not recorded]":
        return value
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _unique_ordered(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _worst_qc_status(statuses: List[str]) -> str:
    order = {"pass": 0, "warn": 1, "fail": 2}
    normalised = [s.lower() for s in statuses if s]
    if not normalised:
        return "unknown"
    return max(normalised, key=lambda s: order.get(s, -1))


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
        lead_molecule_id=campaign.lead_molecule_id,
        project_name=project_name,
        target_name=(
            db.query(Project.target_name)
            .filter(Project.id == campaign.project_id)
            .scalar()
        ),
        name=campaign.name,
        description=campaign.description,
        campaign_type=campaign.campaign_type,
        status=campaign.status,
        metadata=campaign.extra_metadata,
        target_metric=campaign.target_metric,
        target_metric_unit=campaign.target_metric_unit,
        target_metric_threshold=campaign.target_metric_threshold,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        run_count=int(run_count),
        molecule_count=int(molecule_count),
    )
