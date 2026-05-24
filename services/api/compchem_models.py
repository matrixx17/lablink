"""
Computational chemistry data model — Layer 0.

Hierarchy:
    Organization
    └── Project  (drug target or program)
        └── Campaign  (lead-opt or screening campaign against a target)
            ├── Run  (single simulation / calculation job)
            │   ├── RunInput   (files, parameters, forcefield, software version)
            │   ├── RunOutput  (trajectory, result files, logs)
            │   ├── RunMetric  (extracted scalar — docking score, ΔG, RMSD, …)
            │   └── AuditEvent (tamper-evident state change log)
            └── Molecule  (chemical entity, deduplicated by InChIKey)
                ├── MoleculeProperty  (computed: MW, LogP, TPSA, …)
                └── AssayResult       (links RunMetrics that characterise this molecule)

Design decisions:
- canonical_smiles stored as computed by RDKit at ingest; inchi_key is the
  deduplication index — never trust user-provided SMILES as canonical.
- All metric values carry an explicit unit string; mixed units (kcal/mol vs
  kJ/mol) are caught at query time, not silently averaged.
- Run.config_hash is a SHA256 of the full parameter set — reproducibility
  check without storing every config file inline.
- RunLineage tracks parent→child dependencies (e.g. docking pose → MD input)
  as a self-referential adjacency list on Run.
- AuditEvent reuses the same SHA256 hash-chain approach from the existing
  audit_logs table so the chain can be verified identically.
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from database import Base, engine  # reuse existing engine + Base

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ProjectStatus(PyEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class CampaignType(PyEnum):
    LEAD_OPTIMIZATION = "lead_optimization"
    HIT_IDENTIFICATION = "hit_identification"
    VIRTUAL_SCREENING = "virtual_screening"
    ADMET_PROFILING = "admet_profiling"
    FRAGMENT_GROWING = "fragment_growing"
    OTHER = "other"


class CampaignStatus(PyEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    LEAD_NOMINATED = "lead_nominated"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class RunStatus(PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunKind(PyEnum):
    """Broad category of computation."""
    DOCKING = "docking"
    MOLECULAR_DYNAMICS = "molecular_dynamics"
    FREE_ENERGY = "free_energy"          # FEP, RBFE, ABFE
    DFT = "dft"
    SEMI_EMPIRICAL = "semi_empirical"
    MMGBSA = "mmgbsa"
    MMPBSA = "mmpbsa"
    CONFORMER_GENERATION = "conformer_generation"
    PROPERTY_PREDICTION = "property_prediction"
    PHARMACOPHORE = "pharmacophore"
    OTHER = "other"


class InputKind(PyEnum):
    LIGAND_FILE = "ligand_file"          # SDF, MOL2, SMILES
    RECEPTOR_FILE = "receptor_file"       # PDB, MOE
    FORCEFIELD = "forcefield"
    CONFIG_FILE = "config_file"
    PARAMETER_SET = "parameter_set"
    REFERENCE_STRUCTURE = "reference_structure"
    OTHER = "other"


class OutputKind(PyEnum):
    TRAJECTORY = "trajectory"            # DCD, XTC, TRR
    RESULT_FILE = "result_file"          # docking poses SDF, score CSV
    LOG_FILE = "log_file"
    CHECKPOINT = "checkpoint"
    ENERGY_FILE = "energy_file"
    VISUALIZATION = "visualization"
    OTHER = "other"


class ComputeEnvironment(PyEnum):
    LOCAL = "local"
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    HPC_SLURM = "hpc_slurm"
    HPC_PBS = "hpc_pbs"
    HPC_LSF = "hpc_lsf"
    KUBERNETES = "kubernetes"
    OTHER = "other"


class AuditEventAction(PyEnum):
    """Comp-chem specific audit actions."""
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    CAMPAIGN_CREATED = "campaign_created"
    CAMPAIGN_UPDATED = "campaign_updated"
    CAMPAIGN_COMPLETED = "campaign_completed"
    RUN_SUBMITTED = "run_submitted"
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"
    MOLECULE_REGISTERED = "molecule_registered"
    MOLECULE_UPDATED = "molecule_updated"
    METRIC_RECORDED = "metric_recorded"
    ASSAY_RESULT_LINKED = "assay_result_linked"
    FILE_RECEIVED = "file_received"
    FILE_UPLOADED = "file_uploaded"
    FILE_ACCESSED = "file_accessed"
    CONFIG_CHANGED = "config_changed"
    CRO_DELIVERY = "cro_delivery"
    LEAD_NOMINATED = "lead_nominated"


# ---------------------------------------------------------------------------
# Core hierarchy: Organization → Project → Campaign
# ---------------------------------------------------------------------------

class Organization(Base):
    """Tenant / company that owns projects, campaigns, and audit events."""
    __tablename__ = "cc_organizations"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(256), nullable=True)
    demo_mode = Column(Boolean, nullable=False, default=False)
    extra_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class OrgCredential(Base):
    """Scoped upload credential issued for a CRO or external collaborator."""
    __tablename__ = "org_credentials"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(
        String(128),
        ForeignKey("cc_organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_type = Column(String(50), nullable=False, index=True)
    credential_value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    label = Column(String(255), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_org_credentials_org_type", "org_id", "credential_type"),
    )


class OrgUser(Base):
    """Minimal dashboard user record for demo/admin identity bootstrapping."""
    __tablename__ = "org_users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id = Column(
        String(128),
        ForeignKey("cc_organizations.org_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(255), nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "email", name="uq_org_users_org_email"),
    )


class Project(Base):
    """
    A drug target or research program.

    Projects group campaigns sharing the same biological target
    (e.g. EGFR kinase inhibitor programme).
    """
    __tablename__ = "cc_projects"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    target_name = Column(String(256), nullable=True)   # e.g. "EGFR", "JAK2"
    target_uniprot = Column(String(16), nullable=True) # e.g. "P00533"
    indication = Column(String(256), nullable=True)    # therapeutic area
    status = Column(String(32), nullable=False, default=ProjectStatus.ACTIVE.value)
    extra_metadata = Column("metadata", JSONB, nullable=True)            # arbitrary key/value

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_cc_project_org_name"),
        Index("ix_cc_projects_org_status", "org_id", "status"),
    )


class Campaign(Base):
    """
    A lead-optimisation or screening campaign within a project.

    This is the primary queryable object. A campaign accumulates runs
    and molecules over its lifetime and is the unit of IND / partner
    data room export.
    """
    __tablename__ = "cc_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("cc_projects.id", ondelete="RESTRICT"), nullable=False, index=True)
    lead_molecule_id = Column(Integer, ForeignKey("cc_molecules.id", ondelete="SET NULL"), nullable=True, index=True)

    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    campaign_type = Column(String(64), nullable=False, default=CampaignType.LEAD_OPTIMIZATION.value)
    status = Column(String(32), nullable=False, default=CampaignStatus.ACTIVE.value)

    # Scientific context
    hypothesis = Column(Text, nullable=True)           # what we're testing
    target_metric = Column(String(128), nullable=True) # primary optimisation goal, e.g. "docking_score"
    target_metric_unit = Column(String(32), nullable=True)
    target_metric_threshold = Column(Float, nullable=True)  # pass/fail threshold

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("org_id", "project_id", "name", name="uq_cc_campaign_org_project_name"),
        Index("ix_cc_campaigns_project", "project_id"),
        Index("ix_cc_campaigns_org_status", "org_id", "status"),
    )


class DockingGrid(Base):
    """
    A receptor/grid definition used for docking runs within a campaign.

    The primary key is a UUID string so grid IDs can be safely copied into
    .lablink.yaml files and used by edge agents without exposing sequential IDs.
    """
    __tablename__ = "docking_grids"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    campaign_id = Column(Integer, ForeignKey("cc_campaigns.id", ondelete="CASCADE"), nullable=False)

    name = Column(String(255), nullable=False)
    receptor_pdb_s3_key = Column(String(1024), nullable=True)
    receptor_pdb_hash = Column(String(64), nullable=True)
    software = Column(String(100), nullable=False)
    software_version = Column(String(50), nullable=True)

    box_center_x = Column(Float, nullable=True)
    box_center_y = Column(Float, nullable=True)
    box_center_z = Column(Float, nullable=True)
    box_size_x = Column(Float, nullable=True)
    box_size_y = Column(Float, nullable=True)
    box_size_z = Column(Float, nullable=True)

    exhaustiveness = Column(Integer, nullable=True)
    extra_params = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("campaign_id", "name", name="uq_docking_grid_campaign_name"),
        Index("ix_docking_grids_campaign_id", "campaign_id"),
    )


# ---------------------------------------------------------------------------
# Run and its sub-entities
# ---------------------------------------------------------------------------

class Run(Base):
    """
    A single simulation or calculation job within a campaign.

    software_name + software_version + config_hash form the reproducibility
    fingerprint. config_hash is SHA256(canonical JSON of all parameters).
    """
    __tablename__ = "cc_runs"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("cc_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Optional: the molecule this run characterises (null for multi-molecule jobs)
    molecule_id = Column(Integer, ForeignKey("cc_molecules.id", ondelete="SET NULL"), nullable=True, index=True)
    grid_id = Column(String(36), ForeignKey("docking_grids.id", ondelete="SET NULL"), nullable=True)

    external_run_id = Column(String(256), nullable=True, index=True)  # job ID on HPC / cloud scheduler
    name = Column(String(256), nullable=True)
    run_kind = Column(String(64), nullable=False, default=RunKind.OTHER.value)
    status = Column(String(32), nullable=False, default=RunStatus.PENDING.value)
    was_inferred = Column(Boolean, nullable=False, default=False)

    # Reproducibility fields — first-class, not buried in metadata
    software_name = Column(String(128), nullable=True)     # e.g. "AutoDock Vina", "GROMACS"
    software_version = Column(String(64), nullable=True)   # e.g. "1.2.5", "2023.1"
    forcefield = Column(String(128), nullable=True)        # e.g. "AMBER ff19SB", "CHARMM36m"
    config_hash = Column(String(64), nullable=True)        # SHA256 of full parameter set
    cli_args = Column(Text, nullable=True)                 # raw command line or script call
    compute_environment = Column(String(32), nullable=True, default=ComputeEnvironment.LOCAL.value)
    compute_details = Column(JSONB, nullable=True)         # instance type, CPU/GPU count, cluster name

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    wall_time_s = Column(Float, nullable=True)             # seconds, for cost tracking

    error_message = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_cc_runs_campaign_status", "campaign_id", "status"),
        Index("ix_cc_runs_org_kind", "org_id", "run_kind"),
        Index("ix_cc_runs_grid_id", "grid_id"),
    )


class RunInput(Base):
    """
    An input artifact for a run (ligand file, receptor, forcefield, config).

    s3_key points to the raw file in object storage.
    file_hash (SHA256) enables deduplication and tamper detection.
    """
    __tablename__ = "cc_run_inputs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("cc_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)

    input_kind = Column(String(64), nullable=False, default=InputKind.OTHER.value)
    filename = Column(String(512), nullable=False)
    s3_key = Column(String(1024), nullable=True)
    file_hash = Column(String(64), nullable=True)   # SHA256 of file contents
    file_size_bytes = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_cc_run_inputs_run", "run_id"),
    )


class RunOutput(Base):
    """
    An output artifact from a run (trajectory, result file, log).
    """
    __tablename__ = "cc_run_outputs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("cc_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)

    output_kind = Column(String(64), nullable=False, default=OutputKind.OTHER.value)
    filename = Column(String(512), nullable=False)
    s3_key = Column(String(1024), nullable=True)
    file_hash = Column(String(64), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_cc_run_outputs_run", "run_id"),
    )


class RunMetric(Base):
    """
    An extracted scalar result from a run.

    Examples: docking_score (-9.2, kcal/mol), rmsd (1.4, Å),
              delta_g (-8.1, kcal/mol), pIC50 (7.3, dimensionless).

    unit is mandatory — never let kcal/mol and kJ/mol coexist silently.
    confidence and stderr support FEP/alchemical results.
    """
    __tablename__ = "cc_run_metrics"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("cc_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)

    # Optional: the molecule this metric characterises (may differ from run.molecule_id
    # for multi-ligand docking jobs where each pose has its own metric)
    molecule_id = Column(Integer, ForeignKey("cc_molecules.id", ondelete="SET NULL"), nullable=True, index=True)

    metric_name = Column(String(128), nullable=False)  # e.g. "docking_score", "delta_g_bind"
    value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=False)           # e.g. "kcal/mol", "Å", "dimensionless"
    confidence = Column(Float, nullable=True)           # 0–1, model confidence or experimental uncertainty flag
    stderr = Column(Float, nullable=True)               # standard error for FEP results
    extra_metadata = Column("metadata", JSONB, nullable=True)             # pose rank, frame index, etc.

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_cc_run_metrics_run_name", "run_id", "metric_name"),
        Index("ix_cc_run_metrics_molecule", "molecule_id"),
    )


class RunLineage(Base):
    """
    Parent → child dependency between runs.

    Captures workflow provenance: e.g. a docking run (parent) whose
    top-ranked pose feeds an MD simulation (child). Forms a DAG.
    """
    __tablename__ = "cc_run_lineage"

    id = Column(Integer, primary_key=True, index=True)
    parent_run_id = Column(Integer, ForeignKey("cc_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    child_run_id = Column(Integer, ForeignKey("cc_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship = Column(String(64), nullable=True)   # e.g. "pose_to_md", "md_to_fep"
    extra_metadata = Column("metadata", JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("parent_run_id", "child_run_id", name="uq_cc_lineage_parent_child"),
        Index("ix_cc_lineage_child", "child_run_id"),
    )


# ---------------------------------------------------------------------------
# Molecule and its sub-entities
# ---------------------------------------------------------------------------

class Molecule(Base):
    """
    A chemical entity, deduplicated by InChIKey.

    canonical_smiles is always computed via RDKit at ingest — never stored
    as-provided, because two representations of the same molecule must map
    to the same row. inchi_key is the unique key within an org.

    Molecules are campaign-scoped so different campaigns can track the same
    compound independently (different series, different scaffolds).
    """
    __tablename__ = "cc_molecules"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("cc_campaigns.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Primary identifiers
    inchi_key = Column(String(27), nullable=False)     # standard 27-char InChIKey
    canonical_smiles = Column(Text, nullable=False)    # RDKit-canonicalised
    inchi = Column(Text, nullable=True)
    name = Column(String(256), nullable=True)          # internal compound ID or common name
    external_id = Column(String(256), nullable=True)   # CAS, ChEMBL ID, registry number

    # Structure variants
    smiles_provided = Column(Text, nullable=True)      # original SMILES as submitted (for audit)
    mol_block = Column(Text, nullable=True)            # MDL Molfile / SDF block

    # Quick-access computed properties (mirrors MoleculeProperty rows for hot path)
    molecular_weight = Column(Float, nullable=True)
    formula = Column(String(128), nullable=True)

    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        # Uniqueness: one molecule per (org, campaign, InChIKey) —
        # same compound can appear in multiple campaigns independently
        UniqueConstraint("org_id", "campaign_id", "inchi_key", name="uq_cc_molecule_org_campaign_inchikey"),
        Index("ix_cc_molecules_inchi_key", "inchi_key"),
        Index("ix_cc_molecules_campaign", "campaign_id"),
        Index("ix_cc_molecules_org_inchikey", "org_id", "inchi_key"),
    )


class MoleculeProperty(Base):
    """
    A computed or experimentally measured property of a molecule.

    Separating from Molecule allows arbitrary property expansion without
    schema changes. property_source tracks whether value came from RDKit,
    an ML model, or wet-lab measurement.
    """
    __tablename__ = "cc_molecule_properties"

    id = Column(Integer, primary_key=True, index=True)
    molecule_id = Column(Integer, ForeignKey("cc_molecules.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)

    property_name = Column(String(128), nullable=False)   # e.g. "logP", "TPSA", "HBD", "pIC50"
    value = Column(Float, nullable=True)
    value_text = Column(String(512), nullable=True)        # for non-numeric properties
    unit = Column(String(32), nullable=True)
    property_source = Column(String(128), nullable=True)   # "rdkit", "chemprop", "wet_lab", model name
    confidence = Column(Float, nullable=True)
    extra_metadata = Column("metadata", JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        # One value per (molecule, property name, source) — allows multiple
        # sources for the same property (rdkit logP vs experimental logP)
        UniqueConstraint("molecule_id", "property_name", "property_source",
                         name="uq_cc_mol_prop_mol_name_source"),
        Index("ix_cc_mol_props_molecule", "molecule_id"),
        Index("ix_cc_mol_props_name", "property_name"),
    )


class AssayResult(Base):
    """
    Links a RunMetric to the molecule it characterises.

    This is the join table that answers "show me every computed ΔG for
    scaffold X across all runs in this campaign." AssayResults are the
    accumulation point — a molecule may have dozens over its lifetime.
    """
    __tablename__ = "cc_assay_results"

    id = Column(Integer, primary_key=True, index=True)
    molecule_id = Column(Integer, ForeignKey("cc_molecules.id", ondelete="CASCADE"), nullable=False, index=True)
    run_metric_id = Column(Integer, ForeignKey("cc_run_metrics.id", ondelete="CASCADE"), nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)

    # Denormalised for fast querying without joining through run_metrics every time
    metric_name = Column(String(128), nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=False)

    # Optional pass/fail flag relative to campaign threshold
    passes_threshold = Column(Boolean, nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("molecule_id", "run_metric_id", name="uq_cc_assay_mol_metric"),
        Index("ix_cc_assay_results_molecule", "molecule_id"),
        Index("ix_cc_assay_results_metric_name", "metric_name"),
    )


# ---------------------------------------------------------------------------
# Tamper-evident audit log (same hash-chain pattern as existing audit_logs)
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    """
    Tamper-evident audit log for comp-chem campaign events.

    Uses the same SHA256 hash-chain approach as audit_logs (existing table)
    so chain verification works identically. Kept as a separate table to
    avoid polluting the lab-instrument audit chain with comp-chem events,
    and to allow independent chain verification per domain.
    """
    __tablename__ = "cc_audit_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                       nullable=False, index=True)
    org_id = Column(String(128), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)   # AuditEventAction value
    entity_type = Column(String(64), nullable=False)           # "project", "campaign", "run", "molecule"
    entity_id = Column(String(512), nullable=False)
    actor = Column(String(256), nullable=False)                # user ID or "api", "system"
    details = Column(JSONB, nullable=True)
    extra_data = Column(JSONB, nullable=True)
    previous_hash = Column(String(64), nullable=True)
    record_hash = Column(String(64), nullable=False)

    __table_args__ = (
        Index("ix_cc_audit_org_timestamp", "org_id", "timestamp"),
        Index("ix_cc_audit_org_action", "org_id", "action"),
        Index("ix_cc_audit_entity", "entity_type", "entity_id"),
    )


# ---------------------------------------------------------------------------
# Audit helpers (mirrors database.py pattern exactly)
# ---------------------------------------------------------------------------

def _canonical_timestamp(ts: datetime) -> str:
    """
    Canonical UTC string for audit hashing.

    Reasons we don't just call isoformat():
      - SQLite drops tzinfo on round-trip from DateTime(timezone=True), so a
        round-tripped record would produce a different isoformat() than the
        pre-insert value, breaking the chain.
      - Different drivers vary in microsecond precision.

    We normalise to UTC, strip tzinfo, and emit ISO with millisecond
    precision — stable across all dialects.
    """
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    # Truncate to milliseconds to avoid driver-specific microsecond drift
    ms = ts.microsecond // 1000 * 1000
    ts = ts.replace(microsecond=ms)
    return ts.isoformat(timespec="milliseconds")


def compute_audit_hash(
    timestamp: datetime,
    org_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    details: Optional[Dict[str, Any]],
    previous_hash: Optional[str],
) -> str:
    payload = {
        "timestamp": _canonical_timestamp(timestamp),
        "org_id": org_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor": actor,
        "details": details,
        "previous_hash": previous_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def log_cc_audit(
    action: AuditEventAction,
    entity_type: str,
    entity_id: str,
    actor: str,
    org_id: str,
    details: Optional[Dict[str, Any]],
    db: Session,
    extra_data: Optional[Dict[str, Any]] = None,
) -> AuditEvent:
    previous_record = (
        db.query(AuditEvent)
        .filter(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.id.desc())
        .first()
    )
    previous_hash = previous_record.record_hash if previous_record else None
    timestamp = datetime.now(timezone.utc)

    record_hash = compute_audit_hash(
        timestamp=timestamp,
        org_id=org_id,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
        previous_hash=previous_hash,
    )

    event = AuditEvent(
        timestamp=timestamp,
        org_id=org_id,
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
        extra_data=extra_data,
        previous_hash=previous_hash,
        record_hash=record_hash,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def verify_cc_audit_chain(org_id: str, db: Session) -> Dict[str, Any]:
    records = (
        db.query(AuditEvent)
        .filter(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.id.asc())
        .all()
    )
    if not records:
        return {"valid": True, "record_count": 0, "errors": []}

    errors = []
    previous_hash = None
    for record in records:
        if record.previous_hash != previous_hash:
            errors.append({
                "record_id": record.id,
                "error": "previous_hash mismatch",
                "expected": previous_hash,
                "actual": record.previous_hash,
            })
        expected = compute_audit_hash(
            timestamp=record.timestamp,
            org_id=record.org_id,
            action=record.action,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            actor=record.actor,
            details=record.details,
            previous_hash=record.previous_hash,
        )
        if record.record_hash != expected:
            errors.append({
                "record_id": record.id,
                "error": "record_hash mismatch",
                "expected": expected,
                "actual": record.record_hash,
            })
        previous_hash = record.record_hash

    return {
        "valid": len(errors) == 0,
        "record_count": len(records),
        "errors": errors,
    }
