"""Persistent demo session store

Revision ID: 013_demo_sessions
Revises: 012_cc_audit_extra_data
Create Date: 2026-05-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "013_demo_sessions"
down_revision: Union[str, None] = "012_cc_audit_extra_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(conn) -> set:
    return set(inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    if "demo_sessions" in _tables(conn):
        return
    op.create_table(
        "demo_sessions",
        sa.Column("token", sa.String(length=64), primary_key=True),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_demo_sessions_expires_at", "demo_sessions", ["expires_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if "demo_sessions" not in _tables(conn):
        return
    op.drop_index("ix_demo_sessions_expires_at", table_name="demo_sessions")
    op.drop_table("demo_sessions")
