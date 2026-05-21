
import os
import hashlib
import json
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, Dict, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, JSON, Text,
    DateTime, Enum, Index, Boolean, Float, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import sessionmaker, declarative_base, Session

DB_USER = os.getenv("POSTGRES_USER", "postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", "postgres")
DB_NAME = os.getenv("POSTGRES_DB", "lablink")
DB_HOST = os.getenv("POSTGRES_HOST", "postgres")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

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


class EntityType(PyEnum):
    """Types of entities that can be audited."""
    RUN = "run"
    FILE = "file"
    CONFIG = "config"
    USER = "user"
    WEBHOOK = "webhook"
    BASELINE = "baseline"


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
    action = Column(Enum(AuditAction), nullable=False, index=True)
    entity_type = Column(Enum(EntityType), nullable=False)
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
