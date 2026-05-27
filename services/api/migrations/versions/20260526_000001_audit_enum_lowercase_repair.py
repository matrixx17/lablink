"""Repair auditaction/entitytype enums to canonical lowercase values

Some databases were originally bootstrapped via SQLAlchemy ``create_all()``
before the ``AuditLog`` model used ``values_callable``. Those engines stored
the enum *names* (``CONFIG_CHANGED``) as the Postgres labels, while the
application now persists the enum *values* (``config_changed``). Later
migrations only added a handful of lowercase labels (``run_created``,
``run_completed``, ``campaign``...), leaving the original labels uppercase.

This migration ensures every canonical lowercase value exists, so inserts that
write values like ``config_changed`` / ``config`` succeed. It is idempotent:
on a fresh migration-built database every label already exists and each
statement is a no-op.

Revision ID: 015_audit_enum_lowercase_repair
Revises: 014_demo_share_codes
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "015_audit_enum_lowercase_repair"
down_revision: Union[str, None] = "014_demo_share_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Canonical lowercase values, mirroring AuditAction.value / EntityType.value
# in services/api/database.py.
AUDITACTION_VALUES = [
    "run_created",
    "run_completed",
    "file_ingested",
    "schema_mapped",
    "qc_completed",
    "qc_anomaly_flagged",
    "file_accessed",
    "config_changed",
    "presign_generated",
    "webhook_registered",
    "webhook_deleted",
    "webhook_triggered",
    "baseline_updated",
    "baseline_reset",
    "campaign_approved",
]

ENTITYTYPE_VALUES = [
    "run",
    "file",
    "config",
    "user",
    "webhook",
    "baseline",
    "campaign",
]


def _enum_values(conn, enum_name: str) -> set:
    rows = conn.execute(
        sa.text(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = :enum_name
            """
        ),
        {"enum_name": enum_name},
    ).fetchall()
    return {row[0] for row in rows}


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    existing = _enum_values(conn, "auditaction")
    for value in AUDITACTION_VALUES:
        if value not in existing:
            op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")

    existing = _enum_values(conn, "entitytype")
    for value in ENTITYTYPE_VALUES:
        if value not in existing:
            op.execute(f"ALTER TYPE entitytype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop enum values; the added labels are harmless. No-op.
    pass
