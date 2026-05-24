"""Add metadata JSONB to timeseries_data for chromatography context

Revision ID: 010_timeseries_metadata
Revises: 009_wetlab
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "010_timeseries_metadata"
down_revision: Union[str, None] = "009_wetlab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(conn, table: str) -> set:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "timeseries_data" not in tables:
        return
    cols = _column_names(conn, "timeseries_data")
    if "metadata" not in cols:
        op.add_column(
            "timeseries_data",
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "timeseries_data" not in tables:
        return
    cols = _column_names(conn, "timeseries_data")
    if "metadata" in cols:
        op.drop_column("timeseries_data", "metadata")
