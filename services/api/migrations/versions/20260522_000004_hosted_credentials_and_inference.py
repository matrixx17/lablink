"""Add hosted credentials and run inference marker

Revision ID: 006_hosted_credentials
Revises: 005_docking_grids
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "006_hosted_credentials"
down_revision: Union[str, None] = "005_docking_grids"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table_name: str) -> set:
    return {col["name"] for col in inspect(conn).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    if conn.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if "org_credentials" not in tables:
        id_type = postgresql.UUID(as_uuid=False) if conn.dialect.name == "postgresql" else sa.String(36)
        id_default = sa.text("gen_random_uuid()") if conn.dialect.name == "postgresql" else None
        op.create_table(
            "org_credentials",
            sa.Column("id", id_type, nullable=False, server_default=id_default),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("credential_type", sa.String(50), nullable=False),
            sa.Column("credential_value", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("label", sa.String(255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["org_id"], ["cc_organizations.org_id"], ondelete="CASCADE"),
        )
        op.create_index("ix_org_credentials_org_id", "org_credentials", ["org_id"])
        op.create_index("ix_org_credentials_credential_type", "org_credentials", ["credential_type"])
        op.create_index("ix_org_credentials_org_type", "org_credentials", ["org_id", "credential_type"])

    run_columns = _columns(conn, "cc_runs")
    if "was_inferred" not in run_columns:
        op.add_column(
            "cc_runs",
            sa.Column("was_inferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.alter_column("cc_runs", "was_inferred", server_default=None)


def downgrade() -> None:
    conn = op.get_bind()
    run_columns = _columns(conn, "cc_runs")
    if "was_inferred" in run_columns:
        op.drop_column("cc_runs", "was_inferred")

    tables = set(inspect(conn).get_table_names())
    if "org_credentials" in tables:
        op.drop_index("ix_org_credentials_org_type", table_name="org_credentials")
        op.drop_index("ix_org_credentials_credential_type", table_name="org_credentials")
        op.drop_index("ix_org_credentials_org_id", table_name="org_credentials")
        op.drop_table("org_credentials")
