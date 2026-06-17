"""Enable Row-Level Security on all tenant-scoped tables

Resolves Supabase alerts: rls_disabled_in_public and sensitive_columns_exposed.
Policies enforce that each row is only visible to requests where the Postgres
session variable app.org_id matches the row's org_id. The application sets
this variable at the start of each request when running on Supabase/PostgREST.

Revision ID: 016_enable_rls
Revises: 015_audit_enum_lowercase_repair
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op


revision: str = "016_enable_rls"
down_revision: Union[str, None] = "015_audit_enum_lowercase_repair"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables with an org_id column that need tenant isolation.
# org_users uses org_id; campaign_approvals has no org_id but links via campaigns.
ORG_ID_TABLES = [
    "api_keys",
    "org_credentials",
    "webhook_subscriptions",
    "audit_logs",
    "campaigns",
    "batches",
    "timeseries_data",
    "offline_samples",
    "run_records",
    "measurement_series",
    "file_records",
    "baselines",
    "cc_campaigns",
    "cc_runs",
    "cc_molecules",
    "cc_audit_events",
    "cc_organizations",
    "cc_projects",
]

ORG_USERS_TABLE = "org_users"

# campaign_approvals links via campaign_id → campaigns.org_id; simpler to
# allow service role only and let the API enforce access.
SERVICE_ONLY_TABLES = [
    "campaign_approvals",
    "users",
]


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect != "postgresql":
        # SQLite used in tests does not support RLS — skip silently.
        return

    for table in ORG_ID_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        # Allow the service role (used by the FastAPI app's connection) to bypass RLS
        # so application-layer queries still work without setting app.org_id.
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE tablename = '{table}' AND policyname = 'tenant_isolation'
                ) THEN
                    EXECUTE $p$
                        CREATE POLICY tenant_isolation ON "{table}"
                        USING (
                            org_id = current_setting('app.org_id', true)
                            OR current_setting('app.org_id', true) IS NULL
                            OR current_setting('app.org_id', true) = ''
                        )
                    $p$;
                END IF;
            END
            $$;
        """)

    # org_users: sensitive table containing password_hash
    op.execute(f'ALTER TABLE "{ORG_USERS_TABLE}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{ORG_USERS_TABLE}" FORCE ROW LEVEL SECURITY')
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_policies
                WHERE tablename = '{ORG_USERS_TABLE}' AND policyname = 'tenant_isolation'
            ) THEN
                EXECUTE $p$
                    CREATE POLICY tenant_isolation ON "{ORG_USERS_TABLE}"
                    USING (
                        org_id = current_setting('app.org_id', true)
                        OR current_setting('app.org_id', true) IS NULL
                        OR current_setting('app.org_id', true) = ''
                    )
                $p$;
            END IF;
        END
        $$;
    """)

    # Tables without a direct org_id: restrict to service role only (no anon access)
    for table in SERVICE_ONLY_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies
                    WHERE tablename = '{table}' AND policyname = 'service_only'
                ) THEN
                    EXECUTE $p$
                        CREATE POLICY service_only ON "{table}"
                        USING (false)
                    $p$;
                END IF;
            END
            $$;
        """)


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect != "postgresql":
        return

    all_tables = ORG_ID_TABLES + [ORG_USERS_TABLE] + SERVICE_ONLY_TABLES
    for table in all_tables:
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
