"""Tracked demo share codes

Revision ID: 014_demo_share_codes
Revises: 013_demo_sessions
Create Date: 2026-05-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "014_demo_share_codes"
down_revision: Union[str, None] = "013_demo_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables(conn) -> set:
    return set(inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    if "demo_share_codes" in _tables(conn):
        return
    op.create_table(
        "demo_share_codes",
        sa.Column("short_code", sa.String(length=24), primary_key=True),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("opened_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_demo_share_codes_created_at", "demo_share_codes", ["created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if "demo_share_codes" not in _tables(conn):
        return
    op.drop_index("ix_demo_share_codes_created_at", table_name="demo_share_codes")
    op.drop_table("demo_share_codes")
