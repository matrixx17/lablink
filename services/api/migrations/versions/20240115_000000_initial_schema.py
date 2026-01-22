"""Initial schema creation

Revision ID: 001_initial
Revises:
Create Date: 2024-01-15 00:00:00.000000

This migration creates the initial database schema for LabLink AI:
- files: Stores processed file records with schema mapping and QC results
- webhook_subscriptions: Webhook endpoint registrations
- audit_logs: Tamper-evident audit trail for 21 CFR Part 11 compliance
- baselines: Historical statistics for drift detection
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial database schema."""

    # ----------------------------------------
    # Files table
    # ----------------------------------------
    op.create_table(
        'files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.String(length=128), nullable=True),
        sa.Column('filename', sa.String(length=512), nullable=True),
        sa.Column('s3_key', sa.String(length=1024), nullable=True),
        sa.Column('sample_id', sa.String(length=256), nullable=True),
        sa.Column('instrument', sa.String(length=256), nullable=True),
        sa.Column('schema_guess', sa.JSON(), nullable=True),
        sa.Column('qc', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_files_id', 'files', ['id'], unique=False)
    op.create_index('ix_files_org_id', 'files', ['org_id'], unique=False)

    # ----------------------------------------
    # Webhook Subscriptions table
    # ----------------------------------------
    op.create_table(
        'webhook_subscriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.String(length=128), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('events', postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column('secret', sa.String(length=256), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_count', sa.Integer(), nullable=False, default=0),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_webhook_subscriptions_id', 'webhook_subscriptions', ['id'], unique=False)
    op.create_index('ix_webhook_subscriptions_org_id', 'webhook_subscriptions', ['org_id'], unique=False)
    op.create_index('ix_webhook_subscriptions_org_active', 'webhook_subscriptions', ['org_id', 'active'], unique=False)

    # ----------------------------------------
    # Audit Logs table
    # ----------------------------------------
    # Create enum types
    op.execute("""
        CREATE TYPE auditaction AS ENUM (
            'file_ingested', 'schema_mapped', 'qc_completed', 'qc_anomaly_flagged',
            'file_accessed', 'config_changed', 'presign_generated',
            'webhook_registered', 'webhook_deleted', 'webhook_triggered',
            'baseline_updated', 'baseline_reset'
        )
    """)
    op.execute("""
        CREATE TYPE entitytype AS ENUM (
            'file', 'config', 'user', 'webhook', 'baseline'
        )
    """)

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('org_id', sa.String(length=128), nullable=False),
        sa.Column('action', postgresql.ENUM('file_ingested', 'schema_mapped', 'qc_completed',
                  'qc_anomaly_flagged', 'file_accessed', 'config_changed', 'presign_generated',
                  'webhook_registered', 'webhook_deleted', 'webhook_triggered',
                  'baseline_updated', 'baseline_reset', name='auditaction', create_type=False), nullable=False),
        sa.Column('entity_type', postgresql.ENUM('file', 'config', 'user', 'webhook', 'baseline',
                  name='entitytype', create_type=False), nullable=False),
        sa.Column('entity_id', sa.String(length=512), nullable=False),
        sa.Column('actor', sa.String(length=256), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('previous_hash', sa.String(length=64), nullable=True),
        sa.Column('record_hash', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_id', 'audit_logs', ['id'], unique=False)
    op.create_index('ix_audit_logs_timestamp', 'audit_logs', ['timestamp'], unique=False)
    op.create_index('ix_audit_logs_org_id', 'audit_logs', ['org_id'], unique=False)
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'], unique=False)
    op.create_index('ix_audit_logs_org_timestamp', 'audit_logs', ['org_id', 'timestamp'], unique=False)
    op.create_index('ix_audit_logs_org_action', 'audit_logs', ['org_id', 'action'], unique=False)

    # ----------------------------------------
    # Baselines table
    # ----------------------------------------
    op.create_table(
        'baselines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.String(length=128), nullable=False),
        sa.Column('instrument', sa.String(length=256), nullable=False),
        sa.Column('field_name', sa.String(length=256), nullable=False),
        sa.Column('mean', sa.Float(), nullable=False, default=0.0),
        sa.Column('std', sa.Float(), nullable=False, default=0.0),
        sa.Column('n_samples', sa.Integer(), nullable=False, default=0),
        sa.Column('m2', sa.Float(), nullable=False, default=0.0),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_updated', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'instrument', 'field_name', name='uq_baseline_org_inst_field')
    )
    op.create_index('ix_baselines_id', 'baselines', ['id'], unique=False)
    op.create_index('ix_baselines_org_id', 'baselines', ['org_id'], unique=False)
    op.create_index('ix_baselines_instrument', 'baselines', ['instrument'], unique=False)
    op.create_index('ix_baselines_org_instrument', 'baselines', ['org_id', 'instrument'], unique=False)


def downgrade() -> None:
    """Drop all tables and types."""
    op.drop_table('baselines')
    op.drop_table('audit_logs')
    op.drop_table('webhook_subscriptions')
    op.drop_table('files')

    # Drop enum types
    op.execute('DROP TYPE IF EXISTS auditaction')
    op.execute('DROP TYPE IF EXISTS entitytype')
