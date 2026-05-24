"""Campaign approval workflow

Revision ID: 011_campaign_approvals
Revises: 010_timeseries_metadata
Create Date: 2026-05-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "011_campaign_approvals"
down_revision: Union[str, None] = "010_timeseries_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(conn) -> set:
    return set(inspect(conn).get_table_names())


def _enum_values(conn, enum_name: str) -> set:
    if conn.dialect.name != "postgresql":
        return set()
    rows = conn.execute(sa.text(
        """
        SELECT enumlabel
        FROM pg_enum
        JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
        WHERE pg_type.typname = :enum_name
        """
    ), {"enum_name": enum_name}).fetchall()
    return {row[0] for row in rows}


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)

    if conn.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        audit_values = _enum_values(conn, "auditaction")
        if "campaign_approved" not in audit_values:
            op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'campaign_approved'")
        entity_values = _enum_values(conn, "entitytype")
        if "campaign" not in entity_values:
            op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'campaign'")

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column(
                "id",
                sa.String(length=36),
                server_default=sa.text("gen_random_uuid()::text") if conn.dialect.name == "postgresql" else None,
                nullable=False,
            ),
            sa.Column("org_id", sa.String(length=128), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("username", sa.String(length=255), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id", "email", name="uq_users_org_email"),
            sa.UniqueConstraint("org_id", "username", name="uq_users_org_username"),
        )
        op.create_index("ix_users_org_id", "users", ["org_id"])
        op.create_index("ix_users_email", "users", ["email"])
        op.create_index("ix_users_username", "users", ["username"])

    tables = _table_names(conn)
    if "campaign_approvals" not in tables:
        op.create_table(
            "campaign_approvals",
            sa.Column(
                "id",
                sa.String(length=36),
                server_default=sa.text("gen_random_uuid()::text") if conn.dialect.name == "postgresql" else None,
                nullable=False,
            ),
            sa.Column("campaign_id", sa.String(length=36), nullable=False),
            sa.Column("approved_by_user_id", sa.String(length=36), nullable=False),
            sa.Column("approved_by_name", sa.String(length=255), nullable=False),
            sa.Column("approval_meaning", sa.String(length=50), nullable=False),
            sa.Column("comments", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_campaign_approvals_campaign"),
            sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], name="fk_campaign_approvals_user"),
            sa.CheckConstraint(
                "approval_meaning IN ('author', 'reviewer', 'approver')",
                name="ck_campaign_approvals_meaning",
            ),
        )
        op.create_index("ix_campaign_approvals_campaign_id", "campaign_approvals", ["campaign_id"])


def downgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)
    if "campaign_approvals" in tables:
        op.drop_index("ix_campaign_approvals_campaign_id", table_name="campaign_approvals")
        op.drop_table("campaign_approvals")
    if "users" in tables:
        op.drop_index("ix_users_username", table_name="users")
        op.drop_index("ix_users_email", table_name="users")
        op.drop_index("ix_users_org_id", table_name="users")
        op.drop_table("users")
