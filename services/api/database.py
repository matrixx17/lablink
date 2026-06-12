
import os
import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, Dict, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, JSON, Text,
    CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, Boolean, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import sessionmaker, declarative_base, Session

DATABASE_URL = os.getenv("DATABASE_URL") or (
    "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=os.getenv("POSTGRES_USER", "postgres"),
        pw=os.getenv("POSTGRES_PASSWORD", "postgres"),
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        db=os.getenv("POSTGRES_DB", "lablink"),
    )
)

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class RunStatus(PyEnum):
    ACTIVE = "active"
    COMPLETE = "complete"
    ARCHIVED = "archived"


class DataKind(PyEnum):
    CONTINUOUS = "continuous"
    DISCRETE_OFFLINE = "discrete_offline"


class RunRecord(Base):
    """
    First-class bioprocess run (batch/campaign) aggregating multiple instrument files.
    """
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    external_run_id = Column(String(256), nullable=False, index=True)
    batch_id = Column(String(256), nullable=True)
    campaign_id = Column(String(256), nullable=True)
    bioreactor_id = Column(String(256), nullable=True)
    product = Column(String(256), nullable=True)
    status = Column(String(32), nullable=False, default=RunStatus.ACTIVE.value)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    run_metadata = Column(JSONB, nullable=True)
    qc = Column(JSONB, nullable=True)
    alignment = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "external_run_id", name="uq_run_org_external"),
        Index("ix_runs_org_status", "org_id", "status"),
    )


class FileRecord(Base):
    __tablename__ = "files"
    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), index=True)
    run_id = Column(Integer, nullable=True, index=True)
    filename = Column(String(512))
    s3_key = Column(String(1024))
    sample_id = Column(String(256), nullable=True)
    instrument = Column(String(256), nullable=True)
    data_kind = Column(String(32), nullable=True, default=DataKind.CONTINUOUS.value)
    schema_guess = Column(JSON)
    qc = Column(JSON)


class MeasurementSeries(Base):
    """Queryable time-series measurements attached to a run."""
    __tablename__ = "measurement_series"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    run_id = Column(Integer, nullable=False, index=True)
    file_id = Column(Integer, nullable=True, index=True)
    field_name = Column(String(256), nullable=False)
    canonical_field = Column(String(256), nullable=True)
    data_kind = Column(String(32), nullable=False, default=DataKind.CONTINUOUS.value)
    time_unit = Column(String(32), nullable=False, default="h")
    time_values = Column(JSONB, nullable=False)  # list[float]
    values = Column(JSONB, nullable=False)  # list[float], aligned with time_values
    point_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_series_run_field", "run_id", "field_name"),
    )


class ApiKey(Base):
    """Organization API keys for authenticated access."""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    key_prefix = Column(String(16), nullable=False)
    key_hash = Column(String(64), nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class WebhookEvent(PyEnum):
    """Event types that can trigger webhooks."""
    FILE_INGESTED = "file.ingested"
    FILE_PROCESSED = "file.processed"
    QC_COMPLETED = "qc.completed"
    QC_ANOMALY_DETECTED = "qc.anomaly_detected"
    SCHEMA_MAPPED = "schema.mapped"


class WebhookSubscription(Base):
    """
    Webhook subscription for notifying external systems of events.

    Subscribers register a URL endpoint to receive POST requests
    when specific events occur within their organization.
    """
    __tablename__ = "webhook_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    events = Column(ARRAY(String), nullable=False)  # List of WebhookEvent values
    secret = Column(String(256), nullable=False)  # For HMAC signing
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    failure_count = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index('ix_webhook_subscriptions_org_active', 'org_id', 'active'),
    )


class AuditAction(PyEnum):
    """Enumeration of auditable actions for 21 CFR Part 11 compliance."""
    RUN_CREATED = "run_created"
    RUN_COMPLETED = "run_completed"
    FILE_INGESTED = "file_ingested"
    SCHEMA_MAPPED = "schema_mapped"
    QC_COMPLETED = "qc_completed"
    QC_ANOMALY_FLAGGED = "qc_anomaly_flagged"
    FILE_ACCESSED = "file_accessed"
    CONFIG_CHANGED = "config_changed"
    PRESIGN_GENERATED = "presign_generated"
    WEBHOOK_REGISTERED = "webhook_registered"
    WEBHOOK_DELETED = "webhook_deleted"
    WEBHOOK_TRIGGERED = "webhook_triggered"
    BASELINE_UPDATED = "baseline_updated"
    BASELINE_RESET = "baseline_reset"
    CAMPAIGN_APPROVED = "campaign_approved"


class EntityType(PyEnum):
    """Types of entities that can be audited."""
    RUN = "run"
    FILE = "file"
    CONFIG = "config"
    USER = "user"
    WEBHOOK = "webhook"
    BASELINE = "baseline"
    CAMPAIGN = "campaign"


class AuditLog(Base):
    """
    Tamper-evident audit log for 21 CFR Part 11 compliance.

    Each record contains a hash of its contents and a reference to the
    previous record's hash, forming a verifiable chain.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    org_id = Column(String(128), nullable=False, index=True)
    action = Column(Enum(AuditAction, values_callable=lambda x: [e.value for e in x]), nullable=False, index=True)
    entity_type = Column(Enum(EntityType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    entity_id = Column(String(512), nullable=False)
    actor = Column(String(256), nullable=False)
    details = Column(JSONB, nullable=True)
    previous_hash = Column(String(64), nullable=True)  # SHA256 hex = 64 chars
    record_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index('ix_audit_logs_org_timestamp', 'org_id', 'timestamp'),
        Index('ix_audit_logs_org_action', 'org_id', 'action'),
    )


class Baseline(Base):
    """
    Historical baselines for drift detection.

    Stores running statistics (mean, std, n) per org/instrument/field
    combination using Welford's online algorithm for incremental updates.
    """
    __tablename__ = "baselines"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    instrument = Column(String(256), nullable=False, index=True)
    field_name = Column(String(256), nullable=False)  # Canonical field name

    # Running statistics (Welford's algorithm state)
    mean = Column(Float, nullable=False, default=0.0)
    std = Column(Float, nullable=False, default=0.0)
    n_samples = Column(Integer, nullable=False, default=0)

    # For Welford's algorithm: we need M2 (sum of squared differences)
    # std = sqrt(M2 / (n - 1)) for sample std
    m2 = Column(Float, nullable=False, default=0.0)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    last_updated = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        # Unique constraint: one baseline per org/instrument/field
        UniqueConstraint('org_id', 'instrument', 'field_name', name='uq_baseline_org_inst_field'),
        Index('ix_baselines_org_instrument', 'org_id', 'instrument'),
    )


class Campaign(Base):
    """
    Standalone campaign table for grouping wet lab batches (and, optionally,
    future cross-domain work). The `domain` column distinguishes wet lab
    campaigns from comp-chem campaigns (which live in cc_campaigns on the
    comp-chem branch).
    """
    __tablename__ = "campaigns"

    id = Column(String(36), primary_key=True)
    org_id = Column(String(128), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    domain = Column(String(20), nullable=False, default="compchem")
    extra_params = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class User(Base):
    """Authenticated user identity resolved from the current auth actor."""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(String(128), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    username = Column(String(255), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_users_org_email"),
        UniqueConstraint("org_id", "username", name="uq_users_org_username"),
    )


class CampaignApproval(Base):
    """Part 11-style campaign sign-off record."""
    __tablename__ = "campaign_approvals"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(
        String(36),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    approved_by_user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approved_by_name = Column(String(255), nullable=False)
    approval_meaning = Column(String(50), nullable=False)
    comments = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "approval_meaning IN ('author', 'reviewer', 'approver')",
            name="ck_campaign_approvals_meaning",
        ),
        Index("ix_campaign_approvals_campaign_id", "campaign_id"),
    )


class Batch(Base):
    __tablename__ = "batches"

    id = Column(String(36), primary_key=True)
    campaign_id = Column(String(36), nullable=False, index=True)
    batch_number = Column(String(100), nullable=True)
    bioreactor_model = Column(String(255), nullable=True)
    volume_liters = Column(Float, nullable=True)
    cell_line = Column(String(255), nullable=True)
    media = Column(String(255), nullable=True)
    inoculation_date = Column(DateTime(timezone=True), nullable=True)
    harvest_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), nullable=False, default="active")
    extra_params = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class TimeseriesData(Base):
    __tablename__ = "timeseries_data"

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), nullable=False, index=True)
    parameter_name = Column(String(100), nullable=False)
    unit = Column(String(50), nullable=True)
    timestamps = Column(ARRAY(Float), nullable=True)
    values = Column(ARRAY(Float), nullable=True)
    source_instrument = Column(String(255), nullable=True)
    # Parser context stored in DB column "metadata" (avoid SQLAlchemy attr name).
    series_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class OfflineSample(Base):
    __tablename__ = "offline_samples"

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), nullable=False, index=True)
    sample_time_hours = Column(Float, nullable=True)
    sample_time_absolute = Column(DateTime(timezone=True), nullable=True)
    measurement_name = Column(String(100), nullable=False)
    value = Column(Float, nullable=True)
    unit = Column(String(50), nullable=True)
    instrument = Column(String(255), nullable=True)
    qc_status = Column(String(20), nullable=False, default="pending")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def compute_record_hash(
    timestamp: datetime,
    org_id: str,
    action: AuditAction,
    entity_type: EntityType,
    entity_id: str,
    actor: str,
    details: Optional[Dict[str, Any]],
    previous_hash: Optional[str]
) -> str:
    """
    Compute SHA256 hash of audit record contents.

    The hash includes all meaningful fields plus the previous record's hash,
    creating a tamper-evident chain.
    """
    payload = {
        "timestamp": timestamp.isoformat(),
        "org_id": org_id,
        "action": action.value,
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "actor": actor,
        "details": details,
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def log_audit(
    action: AuditAction,
    entity_type: EntityType,
    entity_id: str,
    actor: str,
    org_id: str,
    details: Optional[Dict[str, Any]],
    db: Session
) -> AuditLog:
    """
    Create a new audit log entry with hash chaining.

    Args:
        action: The action being audited
        entity_type: Type of entity (file, config, user)
        entity_id: Identifier for the entity
        actor: Who/what performed the action (e.g., "edge-agent", "api", user ID)
        org_id: Organization identifier
        details: Additional context as JSON
        db: Database session

    Returns:
        The created AuditLog record
    """
    # Fetch the most recent audit record's hash for this org
    previous_record = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org_id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    previous_hash = previous_record.record_hash if previous_record else None

    # Create timestamp
    timestamp = datetime.now(timezone.utc)

    # Compute hash for this record
    record_hash = compute_record_hash(
        timestamp=timestamp,
        org_id=org_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
        previous_hash=previous_hash,
    )

    # Create and insert the audit record
    audit_record = AuditLog(
        timestamp=timestamp,
        org_id=org_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
        previous_hash=previous_hash,
        record_hash=record_hash,
    )

    db.add(audit_record)
    db.commit()
    db.refresh(audit_record)

    return audit_record


def verify_audit_chain(org_id: str, db: Session) -> Dict[str, Any]:
    """
    Verify the integrity of the audit chain for an organization.

    Returns:
        Dict with verification results including any broken links
    """
    records = (
        db.query(AuditLog)
        .filter(AuditLog.org_id == org_id)
        .order_by(AuditLog.id.asc())
        .all()
    )

    if not records:
        return {"valid": True, "record_count": 0, "errors": []}

    errors = []
    previous_hash = None

    for record in records:
        # Verify previous_hash matches
        if record.previous_hash != previous_hash:
            errors.append({
                "record_id": record.id,
                "error": "previous_hash mismatch",
                "expected": previous_hash,
                "actual": record.previous_hash,
            })

        # Recompute and verify record hash
        expected_hash = compute_record_hash(
            timestamp=record.timestamp,
            org_id=record.org_id,
            action=record.action,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            actor=record.actor,
            details=record.details,
            previous_hash=record.previous_hash,
        )

        if record.record_hash != expected_hash:
            errors.append({
                "record_id": record.id,
                "error": "record_hash mismatch",
                "expected": expected_hash,
                "actual": record.record_hash,
            })

        previous_hash = record.record_hash

    return {
        "valid": len(errors) == 0,
        "record_count": len(records),
        "errors": errors,
    }


def init_db():
    Base.metadata.create_all(bind=engine)
