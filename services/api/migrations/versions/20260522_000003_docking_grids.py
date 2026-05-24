"""Add docking grids for comp-chem campaigns

Revision ID: 005_docking_grids
Revises: 004_compchem_orgs
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "005_docking_grids"
down_revision: Union[str, None] = "004_compchem_orgs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table_name: str) -> set:
    return {col["name"] for col in inspect(conn).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    if conn.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if "docking_grids" not in tables:
        op.create_table(
            "docking_grids",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=False),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("receptor_pdb_s3_key", sa.String(1024), nullable=True),
            sa.Column("receptor_pdb_hash", sa.CHAR(64), nullable=True),
            sa.Column("software", sa.String(100), nullable=False),
            sa.Column("software_version", sa.String(50), nullable=True),
            sa.Column("box_center_x", sa.Float(), nullable=True),
            sa.Column("box_center_y", sa.Float(), nullable=True),
            sa.Column("box_center_z", sa.Float(), nullable=True),
            sa.Column("box_size_x", sa.Float(), nullable=True),
            sa.Column("box_size_y", sa.Float(), nullable=True),
            sa.Column("box_size_z", sa.Float(), nullable=True),
            sa.Column("exhaustiveness", sa.Integer(), nullable=True),
            sa.Column("extra_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["cc_campaigns.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("campaign_id", "name", name="uq_docking_grid_campaign_name"),
        )
        op.create_index("ix_docking_grids_campaign_id", "docking_grids", ["campaign_id"])

    run_columns = _columns(conn, "cc_runs")
    if "grid_id" not in run_columns:
        op.add_column(
            "cc_runs",
            sa.Column("grid_id", postgresql.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            "fk_cc_runs_grid_id_docking_grids",
            "cc_runs",
            "docking_grids",
            ["grid_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_cc_runs_grid_id", "cc_runs", ["grid_id"])


def downgrade() -> None:
    conn = op.get_bind()
    run_columns = _columns(conn, "cc_runs")
    if "grid_id" in run_columns:
        op.drop_index("ix_cc_runs_grid_id", table_name="cc_runs")
        op.drop_constraint("fk_cc_runs_grid_id_docking_grids", "cc_runs", type_="foreignkey")
        op.drop_column("cc_runs", "grid_id")
    op.drop_index("ix_docking_grids_campaign_id", table_name="docking_grids")
    op.drop_table("docking_grids")
