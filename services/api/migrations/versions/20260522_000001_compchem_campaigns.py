"""Computational chemistry campaign data model — Layer 0

Revision ID: 003_compchem
Revises: 002_bioprocess
Create Date: 2026-05-22

Tables created:
  cc_projects             — drug target / program
  cc_campaigns            — lead-opt or screening campaign (primary object)
  cc_runs                 — single simulation / calculation job
  cc_run_inputs           — input artifacts (ligand files, configs, forcefields)
  cc_run_outputs          — output artifacts (trajectories, result files, logs)
  cc_run_metrics          — extracted scalar results with mandatory units
  cc_run_lineage          — parent→child run dependency DAG
  cc_molecules            — chemical entities, deduplicated by InChIKey
  cc_molecule_properties  — computed/experimental properties (MW, LogP, TPSA…)
  cc_assay_results        — links RunMetrics to molecules they characterise
  cc_audit_events         — tamper-evident hash-chain audit log
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "003_compchem"
down_revision: Union[str, None] = "002_bioprocess"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names(conn) -> set:
    return set(inspect(conn).get_table_names())


def upgrade() -> None:
    conn = op.get_bind()
    tables = _table_names(conn)

    # ------------------------------------------------------------------
    # cc_projects
    # ------------------------------------------------------------------
    if "cc_projects" not in tables:
        op.create_table(
            "cc_projects",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("target_name", sa.String(256), nullable=True),
            sa.Column("target_uniprot", sa.String(16), nullable=True),
            sa.Column("indication", sa.String(256), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("org_id", "name", name="uq_cc_project_org_name"),
        )
        op.create_index("ix_cc_projects_id", "cc_projects", ["id"])
        op.create_index("ix_cc_projects_org_id", "cc_projects", ["org_id"])
        op.create_index("ix_cc_projects_org_status", "cc_projects", ["org_id", "status"])

    # ------------------------------------------------------------------
    # cc_campaigns
    # ------------------------------------------------------------------
    if "cc_campaigns" not in tables:
        op.create_table(
            "cc_campaigns",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("campaign_type", sa.String(64), nullable=False, server_default="lead_optimization"),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("hypothesis", sa.Text(), nullable=True),
            sa.Column("target_metric", sa.String(128), nullable=True),
            sa.Column("target_metric_unit", sa.String(32), nullable=True),
            sa.Column("target_metric_threshold", sa.Float(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["project_id"], ["cc_projects.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("org_id", "project_id", "name", name="uq_cc_campaign_org_project_name"),
        )
        op.create_index("ix_cc_campaigns_id", "cc_campaigns", ["id"])
        op.create_index("ix_cc_campaigns_org_id", "cc_campaigns", ["org_id"])
        op.create_index("ix_cc_campaigns_project", "cc_campaigns", ["project_id"])
        op.create_index("ix_cc_campaigns_org_status", "cc_campaigns", ["org_id", "status"])

    # ------------------------------------------------------------------
    # cc_molecules  (created before cc_runs — runs have an FK to molecules)
    # ------------------------------------------------------------------
    if "cc_molecules" not in tables:
        op.create_table(
            "cc_molecules",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("inchi_key", sa.String(27), nullable=False),
            sa.Column("canonical_smiles", sa.Text(), nullable=False),
            sa.Column("inchi", sa.Text(), nullable=True),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("external_id", sa.String(256), nullable=True),
            sa.Column("smiles_provided", sa.Text(), nullable=True),
            sa.Column("mol_block", sa.Text(), nullable=True),
            sa.Column("molecular_weight", sa.Float(), nullable=True),
            sa.Column("formula", sa.String(128), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["cc_campaigns.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("org_id", "campaign_id", "inchi_key",
                                name="uq_cc_molecule_org_campaign_inchikey"),
        )
        op.create_index("ix_cc_molecules_id", "cc_molecules", ["id"])
        op.create_index("ix_cc_molecules_org_id", "cc_molecules", ["org_id"])
        op.create_index("ix_cc_molecules_inchi_key", "cc_molecules", ["inchi_key"])
        op.create_index("ix_cc_molecules_campaign", "cc_molecules", ["campaign_id"])
        op.create_index("ix_cc_molecules_org_inchikey", "cc_molecules", ["org_id", "inchi_key"])

    # ------------------------------------------------------------------
    # cc_runs
    # ------------------------------------------------------------------
    if "cc_runs" not in tables:
        op.create_table(
            "cc_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("campaign_id", sa.Integer(), nullable=False),
            sa.Column("molecule_id", sa.Integer(), nullable=True),
            sa.Column("external_run_id", sa.String(256), nullable=True),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("run_kind", sa.String(64), nullable=False, server_default="other"),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("software_name", sa.String(128), nullable=True),
            sa.Column("software_version", sa.String(64), nullable=True),
            sa.Column("forcefield", sa.String(128), nullable=True),
            sa.Column("config_hash", sa.String(64), nullable=True),
            sa.Column("cli_args", sa.Text(), nullable=True),
            sa.Column("compute_environment", sa.String(32), nullable=True, server_default="local"),
            sa.Column("compute_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("wall_time_s", sa.Float(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["campaign_id"], ["cc_campaigns.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["molecule_id"], ["cc_molecules.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_cc_runs_id", "cc_runs", ["id"])
        op.create_index("ix_cc_runs_org_id", "cc_runs", ["org_id"])
        op.create_index("ix_cc_runs_campaign_id", "cc_runs", ["campaign_id"])
        op.create_index("ix_cc_runs_molecule_id", "cc_runs", ["molecule_id"])
        op.create_index("ix_cc_runs_external_run_id", "cc_runs", ["external_run_id"])
        op.create_index("ix_cc_runs_campaign_status", "cc_runs", ["campaign_id", "status"])
        op.create_index("ix_cc_runs_org_kind", "cc_runs", ["org_id", "run_kind"])

    # ------------------------------------------------------------------
    # cc_run_inputs
    # ------------------------------------------------------------------
    if "cc_run_inputs" not in tables:
        op.create_table(
            "cc_run_inputs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("input_kind", sa.String(64), nullable=False, server_default="other"),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("s3_key", sa.String(1024), nullable=True),
            sa.Column("file_hash", sa.String(64), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["run_id"], ["cc_runs.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_cc_run_inputs_id", "cc_run_inputs", ["id"])
        op.create_index("ix_cc_run_inputs_run", "cc_run_inputs", ["run_id"])
        op.create_index("ix_cc_run_inputs_org_id", "cc_run_inputs", ["org_id"])

    # ------------------------------------------------------------------
    # cc_run_outputs
    # ------------------------------------------------------------------
    if "cc_run_outputs" not in tables:
        op.create_table(
            "cc_run_outputs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("output_kind", sa.String(64), nullable=False, server_default="other"),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("s3_key", sa.String(1024), nullable=True),
            sa.Column("file_hash", sa.String(64), nullable=True),
            sa.Column("file_size_bytes", sa.Integer(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["run_id"], ["cc_runs.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_cc_run_outputs_id", "cc_run_outputs", ["id"])
        op.create_index("ix_cc_run_outputs_run", "cc_run_outputs", ["run_id"])
        op.create_index("ix_cc_run_outputs_org_id", "cc_run_outputs", ["org_id"])

    # ------------------------------------------------------------------
    # cc_run_metrics
    # ------------------------------------------------------------------
    if "cc_run_metrics" not in tables:
        op.create_table(
            "cc_run_metrics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("run_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("molecule_id", sa.Integer(), nullable=True),
            sa.Column("metric_name", sa.String(128), nullable=False),
            sa.Column("value", sa.Float(), nullable=False),
            sa.Column("unit", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("stderr", sa.Float(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["run_id"], ["cc_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["molecule_id"], ["cc_molecules.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_cc_run_metrics_id", "cc_run_metrics", ["id"])
        op.create_index("ix_cc_run_metrics_run_id", "cc_run_metrics", ["run_id"])
        op.create_index("ix_cc_run_metrics_org_id", "cc_run_metrics", ["org_id"])
        op.create_index("ix_cc_run_metrics_run_name", "cc_run_metrics", ["run_id", "metric_name"])
        op.create_index("ix_cc_run_metrics_molecule", "cc_run_metrics", ["molecule_id"])

    # ------------------------------------------------------------------
    # cc_run_lineage
    # ------------------------------------------------------------------
    if "cc_run_lineage" not in tables:
        op.create_table(
            "cc_run_lineage",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("parent_run_id", sa.Integer(), nullable=False),
            sa.Column("child_run_id", sa.Integer(), nullable=False),
            sa.Column("relationship", sa.String(64), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["parent_run_id"], ["cc_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["child_run_id"], ["cc_runs.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("parent_run_id", "child_run_id", name="uq_cc_lineage_parent_child"),
        )
        op.create_index("ix_cc_run_lineage_id", "cc_run_lineage", ["id"])
        op.create_index("ix_cc_run_lineage_parent", "cc_run_lineage", ["parent_run_id"])
        op.create_index("ix_cc_lineage_child", "cc_run_lineage", ["child_run_id"])

    # ------------------------------------------------------------------
    # cc_molecule_properties
    # ------------------------------------------------------------------
    if "cc_molecule_properties" not in tables:
        op.create_table(
            "cc_molecule_properties",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("molecule_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("property_name", sa.String(128), nullable=False),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("value_text", sa.String(512), nullable=True),
            sa.Column("unit", sa.String(32), nullable=True),
            sa.Column("property_source", sa.String(128), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["molecule_id"], ["cc_molecules.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("molecule_id", "property_name", "property_source",
                                name="uq_cc_mol_prop_mol_name_source"),
        )
        op.create_index("ix_cc_mol_props_id", "cc_molecule_properties", ["id"])
        op.create_index("ix_cc_mol_props_molecule", "cc_molecule_properties", ["molecule_id"])
        op.create_index("ix_cc_mol_props_org_id", "cc_molecule_properties", ["org_id"])
        op.create_index("ix_cc_mol_props_name", "cc_molecule_properties", ["property_name"])

    # ------------------------------------------------------------------
    # cc_assay_results
    # ------------------------------------------------------------------
    if "cc_assay_results" not in tables:
        op.create_table(
            "cc_assay_results",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("molecule_id", sa.Integer(), nullable=False),
            sa.Column("run_metric_id", sa.Integer(), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("metric_name", sa.String(128), nullable=False),
            sa.Column("value", sa.Float(), nullable=False),
            sa.Column("unit", sa.String(32), nullable=False),
            sa.Column("passes_threshold", sa.Boolean(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["molecule_id"], ["cc_molecules.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_metric_id"], ["cc_run_metrics.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("molecule_id", "run_metric_id", name="uq_cc_assay_mol_metric"),
        )
        op.create_index("ix_cc_assay_results_id", "cc_assay_results", ["id"])
        op.create_index("ix_cc_assay_results_molecule", "cc_assay_results", ["molecule_id"])
        op.create_index("ix_cc_assay_results_org_id", "cc_assay_results", ["org_id"])
        op.create_index("ix_cc_assay_results_metric_name", "cc_assay_results", ["metric_name"])

    # ------------------------------------------------------------------
    # cc_audit_events
    # ------------------------------------------------------------------
    if "cc_audit_events" not in tables:
        op.create_table(
            "cc_audit_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("org_id", sa.String(128), nullable=False),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("entity_type", sa.String(64), nullable=False),
            sa.Column("entity_id", sa.String(512), nullable=False),
            sa.Column("actor", sa.String(256), nullable=False),
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("previous_hash", sa.String(64), nullable=True),
            sa.Column("record_hash", sa.String(64), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_cc_audit_events_id", "cc_audit_events", ["id"])
        op.create_index("ix_cc_audit_events_timestamp", "cc_audit_events", ["timestamp"])
        op.create_index("ix_cc_audit_events_org_id", "cc_audit_events", ["org_id"])
        op.create_index("ix_cc_audit_org_timestamp", "cc_audit_events", ["org_id", "timestamp"])
        op.create_index("ix_cc_audit_org_action", "cc_audit_events", ["org_id", "action"])
        op.create_index("ix_cc_audit_entity", "cc_audit_events", ["entity_type", "entity_id"])


def downgrade() -> None:
    # Drop in reverse FK dependency order
    op.drop_index("ix_cc_audit_entity", table_name="cc_audit_events")
    op.drop_index("ix_cc_audit_org_action", table_name="cc_audit_events")
    op.drop_index("ix_cc_audit_org_timestamp", table_name="cc_audit_events")
    op.drop_index("ix_cc_audit_events_org_id", table_name="cc_audit_events")
    op.drop_index("ix_cc_audit_events_timestamp", table_name="cc_audit_events")
    op.drop_index("ix_cc_audit_events_id", table_name="cc_audit_events")
    op.drop_table("cc_audit_events")

    op.drop_index("ix_cc_assay_results_metric_name", table_name="cc_assay_results")
    op.drop_index("ix_cc_assay_results_org_id", table_name="cc_assay_results")
    op.drop_index("ix_cc_assay_results_molecule", table_name="cc_assay_results")
    op.drop_index("ix_cc_assay_results_id", table_name="cc_assay_results")
    op.drop_table("cc_assay_results")

    op.drop_index("ix_cc_mol_props_name", table_name="cc_molecule_properties")
    op.drop_index("ix_cc_mol_props_org_id", table_name="cc_molecule_properties")
    op.drop_index("ix_cc_mol_props_molecule", table_name="cc_molecule_properties")
    op.drop_index("ix_cc_mol_props_id", table_name="cc_molecule_properties")
    op.drop_table("cc_molecule_properties")

    op.drop_index("ix_cc_run_lineage_parent", table_name="cc_run_lineage")
    op.drop_index("ix_cc_lineage_child", table_name="cc_run_lineage")
    op.drop_index("ix_cc_run_lineage_id", table_name="cc_run_lineage")
    op.drop_table("cc_run_lineage")

    op.drop_index("ix_cc_run_metrics_molecule", table_name="cc_run_metrics")
    op.drop_index("ix_cc_run_metrics_run_name", table_name="cc_run_metrics")
    op.drop_index("ix_cc_run_metrics_org_id", table_name="cc_run_metrics")
    op.drop_index("ix_cc_run_metrics_run_id", table_name="cc_run_metrics")
    op.drop_index("ix_cc_run_metrics_id", table_name="cc_run_metrics")
    op.drop_table("cc_run_metrics")

    op.drop_index("ix_cc_run_outputs_org_id", table_name="cc_run_outputs")
    op.drop_index("ix_cc_run_outputs_run", table_name="cc_run_outputs")
    op.drop_index("ix_cc_run_outputs_id", table_name="cc_run_outputs")
    op.drop_table("cc_run_outputs")

    op.drop_index("ix_cc_run_inputs_org_id", table_name="cc_run_inputs")
    op.drop_index("ix_cc_run_inputs_run", table_name="cc_run_inputs")
    op.drop_index("ix_cc_run_inputs_id", table_name="cc_run_inputs")
    op.drop_table("cc_run_inputs")

    op.drop_index("ix_cc_runs_org_kind", table_name="cc_runs")
    op.drop_index("ix_cc_runs_campaign_status", table_name="cc_runs")
    op.drop_index("ix_cc_runs_external_run_id", table_name="cc_runs")
    op.drop_index("ix_cc_runs_molecule_id", table_name="cc_runs")
    op.drop_index("ix_cc_runs_campaign_id", table_name="cc_runs")
    op.drop_index("ix_cc_runs_org_id", table_name="cc_runs")
    op.drop_index("ix_cc_runs_id", table_name="cc_runs")
    op.drop_table("cc_runs")

    op.drop_index("ix_cc_molecules_org_inchikey", table_name="cc_molecules")
    op.drop_index("ix_cc_molecules_campaign", table_name="cc_molecules")
    op.drop_index("ix_cc_molecules_inchi_key", table_name="cc_molecules")
    op.drop_index("ix_cc_molecules_org_id", table_name="cc_molecules")
    op.drop_index("ix_cc_molecules_id", table_name="cc_molecules")
    op.drop_table("cc_molecules")

    op.drop_index("ix_cc_campaigns_org_status", table_name="cc_campaigns")
    op.drop_index("ix_cc_campaigns_project", table_name="cc_campaigns")
    op.drop_index("ix_cc_campaigns_org_id", table_name="cc_campaigns")
    op.drop_index("ix_cc_campaigns_id", table_name="cc_campaigns")
    op.drop_table("cc_campaigns")

    op.drop_index("ix_cc_projects_org_status", table_name="cc_projects")
    op.drop_index("ix_cc_projects_org_id", table_name="cc_projects")
    op.drop_index("ix_cc_projects_id", table_name="cc_projects")
    op.drop_table("cc_projects")
