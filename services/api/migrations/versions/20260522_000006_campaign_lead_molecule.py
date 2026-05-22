"""Add lead molecule pointer to comp-chem campaigns

Revision ID: 008_campaign_lead_molecule
Revises: 007_demo_environment
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "008_campaign_lead_molecule"
down_revision: Union[str, None] = "007_demo_environment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table_name: str) -> set:
    return {col["name"] for col in inspect(conn).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    campaign_columns = _columns(conn, "cc_campaigns")
    if "lead_molecule_id" not in campaign_columns:
        op.add_column("cc_campaigns", sa.Column("lead_molecule_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_cc_campaigns_lead_molecule_id",
            "cc_campaigns",
            "cc_molecules",
            ["lead_molecule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_cc_campaigns_lead_molecule_id", "cc_campaigns", ["lead_molecule_id"])


def downgrade() -> None:
    conn = op.get_bind()
    campaign_columns = _columns(conn, "cc_campaigns")
    if "lead_molecule_id" in campaign_columns:
        op.drop_index("ix_cc_campaigns_lead_molecule_id", table_name="cc_campaigns")
        op.drop_constraint("fk_cc_campaigns_lead_molecule_id", "cc_campaigns", type_="foreignkey")
        op.drop_column("cc_campaigns", "lead_molecule_id")
