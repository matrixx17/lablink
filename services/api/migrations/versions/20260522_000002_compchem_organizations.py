"""Add comp-chem organizations table

Revision ID: 004_compchem_orgs
Revises: 003_compchem
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "004_compchem_orgs"
down_revision: Union[str, None] = "003_compchem"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "cc_organizations" not in tables:
        op.create_table(
            "cc_organizations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id", name="uq_cc_organizations_org_id"),
        )
        op.create_index("ix_cc_organizations_id", "cc_organizations", ["id"])
        op.create_index("ix_cc_organizations_org_id", "cc_organizations", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_cc_organizations_org_id", table_name="cc_organizations")
    op.drop_index("ix_cc_organizations_id", table_name="cc_organizations")
    op.drop_table("cc_organizations")
