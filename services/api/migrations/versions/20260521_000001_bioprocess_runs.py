"""Bioprocess runs, measurement series, API keys

Revision ID: 002_bioprocess
Revises: 001_initial
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "002_bioprocess"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(conn) -> set:
    return set(inspect(conn).get_table_names())


def _column_names(conn, table: str) -> set:
    return {c["name"] for c in inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)

    if "runs" not in tables:
        op.create_table(
            "runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(length=128), nullable=False),
            sa.Column("external_run_id", sa.String(length=256), nullable=False),
            sa.Column("batch_id", sa.String(length=256), nullable=True),
            sa.Column("campaign_id", sa.String(length=256), nullable=True),
            sa.Column("bioreactor_id", sa.String(length=256), nullable=True),
            sa.Column("product", sa.String(length=256), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("run_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("qc", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("alignment", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id", "external_run_id", name="uq_run_org_external"),
        )
        op.create_index("ix_runs_id", "runs", ["id"], unique=False)
        op.create_index("ix_runs_org_id", "runs", ["org_id"], unique=False)
        op.create_index("ix_runs_org_status", "runs", ["org_id", "status"], unique=False)

    if "files" in tables:
        file_cols = _column_names(conn, "files")
        if "run_id" not in file_cols:
            op.add_column("files", sa.Column("run_id", sa.Integer(), nullable=True))
        if "data_kind" not in file_cols:
            op.add_column("files", sa.Column("data_kind", sa.String(length=32), nullable=True))
        indexes = {idx["name"] for idx in inspect(conn).get_indexes("files")}
        if "ix_files_run_id" not in indexes:
            op.create_index("ix_files_run_id", "files", ["run_id"], unique=False)

    if "measurement_series" not in tables:
        op.create_table(
            "measurement_series",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(length=128), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("file_id", sa.Integer(), nullable=True),
            sa.Column("field_name", sa.String(length=256), nullable=False),
            sa.Column("canonical_field", sa.String(length=256), nullable=True),
            sa.Column("data_kind", sa.String(length=32), nullable=False),
            sa.Column("time_unit", sa.String(length=32), nullable=False),
            sa.Column("time_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("point_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_measurement_series_id", "measurement_series", ["id"], unique=False)
        op.create_index("ix_measurement_series_run_id", "measurement_series", ["run_id"], unique=False)
        op.create_index("ix_series_run_field", "measurement_series", ["run_id", "field_name"], unique=False)

    if "api_keys" not in tables:
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("key_prefix", sa.String(length=16), nullable=False),
            sa.Column("key_hash", sa.String(length=64), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_api_keys_org_id", "api_keys", ["org_id"], unique=False)

    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'run_created'")
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'run_completed'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'run'")


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_index("ix_series_run_field", table_name="measurement_series")
    op.drop_index("ix_measurement_series_run_id", table_name="measurement_series")
    op.drop_index("ix_measurement_series_id", table_name="measurement_series")
    op.drop_table("measurement_series")
    op.drop_index("ix_files_run_id", table_name="files")
    op.drop_column("files", "data_kind")
    op.drop_column("files", "run_id")
    op.drop_index("ix_runs_org_status", table_name="runs")
    op.drop_index("ix_runs_org_id", table_name="runs")
    op.drop_index("ix_runs_id", table_name="runs")
    op.drop_table("runs")
