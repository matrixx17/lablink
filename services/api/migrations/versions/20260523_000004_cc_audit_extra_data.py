"""Add extra_data JSONB to comp-chem audit events

Revision ID: 012_cc_audit_extra_data
Revises: 011_campaign_approvals
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "012_cc_audit_extra_data"
down_revision: Union[str, None] = "011_campaign_approvals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(conn, table: str) -> set:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "cc_audit_events" not in tables:
        return
    cols = _column_names(conn, "cc_audit_events")
    if "extra_data" not in cols:
        op.add_column(
            "cc_audit_events",
            sa.Column("extra_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "cc_audit_events" not in tables:
        return
    cols = _column_names(conn, "cc_audit_events")
    if "extra_data" in cols:
        op.drop_column("cc_audit_events", "extra_data")
