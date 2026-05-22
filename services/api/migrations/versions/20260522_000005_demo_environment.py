"""Add demo environment support

Revision ID: 007_demo_environment
Revises: 006_hosted_credentials
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "007_demo_environment"
down_revision: Union[str, None] = "006_hosted_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(conn, table_name: str) -> set:
    return {col["name"] for col in inspect(conn).get_columns(table_name)}


def upgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())

    org_columns = _columns(conn, "cc_organizations")
    if "demo_mode" not in org_columns:
        op.add_column(
            "cc_organizations",
            sa.Column("demo_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.alter_column("cc_organizations", "demo_mode", server_default=None)

    if "org_users" not in tables:
        op.create_table(
            "org_users",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["org_id"], ["cc_organizations.org_id"], ondelete="CASCADE"),
            sa.UniqueConstraint("org_id", "email", name="uq_org_users_org_email"),
        )
        op.create_index("ix_org_users_org_id", "org_users", ["org_id"])
        op.create_index("ix_org_users_email", "org_users", ["email"])


def downgrade() -> None:
    conn = op.get_bind()
    tables = set(inspect(conn).get_table_names())
    if "org_users" in tables:
        op.drop_index("ix_org_users_email", table_name="org_users")
        op.drop_index("ix_org_users_org_id", table_name="org_users")
        op.drop_table("org_users")

    org_columns = _columns(conn, "cc_organizations")
    if "demo_mode" in org_columns:
        op.drop_column("cc_organizations", "demo_mode")
