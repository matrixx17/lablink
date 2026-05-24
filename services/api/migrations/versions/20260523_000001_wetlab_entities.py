"""Wet lab entities: campaigns, batches, timeseries_data, offline_samples

Revision ID: 009_wetlab
Revises: 008_campaign_lead_molecule
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "009_wetlab"
down_revision: Union[str, None] = "008_campaign_lead_molecule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(conn) -> set:
    return set(inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)

    if "campaigns" not in tables:
        op.create_table(
            "campaigns",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("org_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "domain",
                sa.String(length=20),
                nullable=False,
                server_default="compchem",
            ),
            sa.Column("extra_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "domain IN ('compchem', 'wetlab')",
                name="ck_campaigns_domain",
            ),
        )
        op.create_index("ix_campaigns_org_id", "campaigns", ["org_id"])
    else:
        # Campaigns table already exists from another source — just ensure the
        # `domain` column is present.
        existing_cols = {c["name"] for c in inspect(conn).get_columns("campaigns")}
        if "domain" not in existing_cols:
            op.add_column(
                "campaigns",
                sa.Column(
                    "domain",
                    sa.String(length=20),
                    nullable=False,
                    server_default="compchem",
                ),
            )
            op.create_check_constraint(
                "ck_campaigns_domain",
                "campaigns",
                "domain IN ('compchem', 'wetlab')",
            )

    if "batches" not in tables:
        op.create_table(
            "batches",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("campaign_id", sa.String(length=36), nullable=False),
            sa.Column("batch_number", sa.String(length=100), nullable=True),
            sa.Column("bioreactor_model", sa.String(length=255), nullable=True),
            sa.Column("volume_liters", sa.Float(), nullable=True),
            sa.Column("cell_line", sa.String(length=255), nullable=True),
            sa.Column("media", sa.String(length=255), nullable=True),
            sa.Column("inoculation_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("harvest_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default="active",
            ),
            sa.Column("extra_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["campaign_id"], ["campaigns.id"], name="fk_batches_campaign"
            ),
        )
        op.create_index("ix_batches_campaign_id", "batches", ["campaign_id"])

    if "timeseries_data" not in tables:
        op.create_table(
            "timeseries_data",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("parameter_name", sa.String(length=100), nullable=False),
            sa.Column("unit", sa.String(length=50), nullable=True),
            sa.Column("timestamps", postgresql.ARRAY(sa.Float()), nullable=True),
            sa.Column("values", postgresql.ARRAY(sa.Float()), nullable=True),
            sa.Column("source_instrument", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["batch_id"], ["batches.id"], name="fk_timeseries_batch"
            ),
        )
        op.create_index("ix_timeseries_data_batch_id", "timeseries_data", ["batch_id"])

    if "offline_samples" not in tables:
        op.create_table(
            "offline_samples",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("batch_id", sa.String(length=36), nullable=False),
            sa.Column("sample_time_hours", sa.Float(), nullable=True),
            sa.Column("sample_time_absolute", sa.DateTime(timezone=True), nullable=True),
            sa.Column("measurement_name", sa.String(length=100), nullable=False),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=50), nullable=True),
            sa.Column("instrument", sa.String(length=255), nullable=True),
            sa.Column(
                "qc_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["batch_id"], ["batches.id"], name="fk_offline_samples_batch"
            ),
        )
        op.create_index(
            "ix_offline_samples_batch_id", "offline_samples", ["batch_id"]
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)

    if "offline_samples" in tables:
        op.drop_index("ix_offline_samples_batch_id", table_name="offline_samples")
        op.drop_table("offline_samples")

    if "timeseries_data" in tables:
        op.drop_index("ix_timeseries_data_batch_id", table_name="timeseries_data")
        op.drop_table("timeseries_data")

    if "batches" in tables:
        op.drop_index("ix_batches_campaign_id", table_name="batches")
        op.drop_table("batches")

    if "campaigns" in tables:
        op.drop_index("ix_campaigns_org_id", table_name="campaigns")
        op.drop_table("campaigns")
