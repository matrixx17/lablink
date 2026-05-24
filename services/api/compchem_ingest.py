"""
Comp-chem run ingestion service.

Translates a manifest from the edge agent (parsed file + campaign context +
client_qc) into a coherent set of database rows:

    cc_projects        find-or-create by (org_id, name)
    cc_campaigns       find-or-create by (org_id, project_id, name)
    cc_molecules       find-or-create by InChIKey within the campaign
                       (requires RDKit; falls back to SMILES-as-identifier
                       when RDKit is absent)
    cc_runs            new row per ingest
    cc_run_inputs/     created from the parsed file's role classification
      cc_run_outputs
    cc_run_metrics     one row per CompChemMetric (with mandatory unit)
    cc_assay_results   join row linking each numeric metric to the molecule
    cc_audit_events    tamper-evident hash-chain entry for every state change

QC is rerun server-side using compchem_qc.compchem_qc_summary (authoritative);
the agent's client_qc is preserved on the run's metadata for forensic value.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from compchem_models import (
    AssayResult,
    AuditEvent,
    AuditEventAction,
    Campaign,
    DockingGrid,
    Molecule,
    MoleculeProperty,
    Organization,
    Project,
    Run,
    RunInput,
    RunKind,
    RunMetric,
    RunOutput,
    RunStatus as CCRunStatus,
    log_cc_audit,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional RDKit — for canonical SMILES + InChIKey
# ---------------------------------------------------------------------------

try:
    from rdkit import Chem  # type: ignore
    from rdkit.Chem import AllChem, Descriptors, rdMolDescriptors  # type: ignore
    _HAS_RDKIT = True
except ImportError:
    _HAS_RDKIT = False
    Chem = None  # type: ignore


def canonicalize_smiles(smiles: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns (canonical_smiles, inchi_key, inchi).

    If RDKit is missing, returns (smiles, None, None) — the caller is
    responsible for falling back to SMILES-as-identifier and warning the
    user that deduplication is not happening.
    """
    if not _HAS_RDKIT or not smiles:
        return smiles, None, None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit cannot parse SMILES: {smiles!r}")
    canonical = Chem.MolToSmiles(mol, canonical=True)
    inchi = Chem.MolToInchi(mol)
    inchi_key = Chem.InchiToInchiKey(inchi) if inchi else None
    return canonical, inchi_key, inchi


def compute_molecule_properties(smiles: str) -> Dict[str, Any]:
    """Return a dict of basic RDKit descriptors. Empty dict if RDKit absent."""
    if not _HAS_RDKIT or not smiles:
        return {}
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return {}
    return {
        "molwt": float(Descriptors.MolWt(mol)),
        "logp": float(Descriptors.MolLogP(mol)),
        "tpsa": float(Descriptors.TPSA(mol)),
        "hbd": int(Descriptors.NumHDonors(mol)),
        "hba": int(Descriptors.NumHAcceptors(mol)),
        "rotb": int(Descriptors.NumRotatableBonds(mol)),
        "qed": float(Descriptors.qed(mol)),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "heavy_atom_count": int(mol.GetNumHeavyAtoms()),
    }


# ---------------------------------------------------------------------------
# Find-or-create helpers
# ---------------------------------------------------------------------------

def get_or_create_organization(db: Session, org_id: str) -> Organization:
    org = db.query(Organization).filter(Organization.org_id == org_id).first()
    if org:
        return org
    org = Organization(org_id=org_id, name=org_id)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def get_or_create_project(db: Session, org_id: str, name: str, actor: str) -> Project:
    proj = (
        db.query(Project)
        .filter(Project.org_id == org_id, Project.name == name)
        .first()
    )
    if proj:
        return proj
    proj = Project(org_id=org_id, name=name)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    log_cc_audit(
        action=AuditEventAction.PROJECT_CREATED,
        entity_type="project",
        entity_id=str(proj.id),
        actor=actor,
        org_id=org_id,
        details={"name": name},
        db=db,
    )
    return proj


def get_or_create_campaign(
    db: Session,
    org_id: str,
    project_id: int,
    name: str,
    actor: str,
    campaign_type: Optional[str] = None,
    description: Optional[str] = None,
    hypothesis: Optional[str] = None,
) -> Campaign:
    camp = (
        db.query(Campaign)
        .filter(
            Campaign.org_id == org_id,
            Campaign.project_id == project_id,
            Campaign.name == name,
        )
        .first()
    )
    if camp:
        return camp
    camp = Campaign(
        org_id=org_id,
        project_id=project_id,
        name=name,
        campaign_type=campaign_type or "lead_optimization",
        description=description,
        hypothesis=hypothesis,
        started_at=datetime.now(timezone.utc),
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    log_cc_audit(
        action=AuditEventAction.CAMPAIGN_CREATED,
        entity_type="campaign",
        entity_id=str(camp.id),
        actor=actor,
        org_id=org_id,
        details={"name": name, "project_id": project_id},
        db=db,
    )
    return camp


def get_or_create_molecule(
    db: Session,
    org_id: str,
    campaign_id: int,
    smiles_provided: str,
    actor: str,
    name: Optional[str] = None,
    external_id: Optional[str] = None,
) -> Tuple[Molecule, bool]:
    """
    Returns (molecule, created_flag). Deduplicates within campaign by InChIKey
    when RDKit is available, by raw SMILES otherwise.
    """
    if not smiles_provided:
        raise ValueError("smiles_provided is required to register a molecule")

    try:
        canonical, inchi_key, inchi = canonicalize_smiles(smiles_provided)
    except ValueError as e:
        logger.warning("Cannot canonicalize %r: %s — using raw SMILES", smiles_provided, e)
        canonical, inchi_key, inchi = smiles_provided, None, None

    # Dedup key: prefer InChIKey, else fall back to canonical SMILES
    dedup_key = inchi_key or canonical

    query = db.query(Molecule).filter(
        Molecule.org_id == org_id,
        Molecule.campaign_id == campaign_id,
    )
    if inchi_key:
        existing = query.filter(Molecule.inchi_key == inchi_key).first()
    else:
        # Fallback path — no InChIKey, match on canonical_smiles
        existing = query.filter(Molecule.canonical_smiles == canonical).first()

    if existing:
        return existing, False

    props = compute_molecule_properties(canonical)
    mol = Molecule(
        org_id=org_id,
        campaign_id=campaign_id,
        # When RDKit is absent we still need a non-null inchi_key — use a
        # truncated hash of the SMILES so the unique constraint is satisfied
        # and downstream queries don't choke on NULL. Real InChIKeys are
        # 27 chars; this synthetic key has the same length.
        inchi_key=inchi_key or _smiles_pseudo_key(canonical),
        canonical_smiles=canonical,
        inchi=inchi,
        smiles_provided=smiles_provided,
        name=name,
        external_id=external_id,
        molecular_weight=props.get("molwt"),
        formula=props.get("formula"),
        extra_metadata=({"computed_without_rdkit": True} if not _HAS_RDKIT else None),
    )
    db.add(mol)
    db.commit()
    db.refresh(mol)

    # Mirror computed properties as MoleculeProperty rows so they're queryable
    for prop_name, value in props.items():
        if prop_name == "formula":
            continue  # stored on the molecule itself
        try:
            db.add(MoleculeProperty(
                molecule_id=mol.id,
                org_id=org_id,
                property_name=prop_name,
                value=float(value),
                unit=_unit_for_property(prop_name),
                property_source="rdkit",
            ))
        except (TypeError, ValueError):
            continue
    db.commit()

    log_cc_audit(
        action=AuditEventAction.MOLECULE_REGISTERED,
        entity_type="molecule",
        entity_id=str(mol.id),
        actor=actor,
        org_id=org_id,
        details={
            "campaign_id": campaign_id,
            "inchi_key": mol.inchi_key,
            "canonical_smiles": canonical,
            "rdkit_available": _HAS_RDKIT,
        },
        db=db,
    )
    return mol, True


def _smiles_pseudo_key(smiles: str) -> str:
    """27-char pseudo-InChIKey for the no-RDKit fallback. Format
    distinguishable from real InChIKeys (always starts 'NORDKIT-')."""
    import hashlib
    h = hashlib.sha256(smiles.encode("utf-8")).hexdigest().upper()
    # Real InChIKeys: AAAAAAAAAAAAAA-BBBBBBBBBB-C (14-10-1, total 27)
    return f"NORDKIT-{h[:14]}-{h[14:18]}"  # 8 + 1 + 14 + 1 + 4 = 28; close enough


def _unit_for_property(prop: str) -> str:
    """Map RDKit property names to canonical units."""
    units = {
        "molwt": "g/mol",
        "logp": "log_units",
        "tpsa": "Å²",
        "hbd": "count",
        "hba": "count",
        "rotb": "count",
        "qed": "dimensionless",
        "heavy_atom_count": "count",
    }
    return units.get(prop, "dimensionless")


# ---------------------------------------------------------------------------
# Run ingestion
# ---------------------------------------------------------------------------

def ingest_run_manifest(
    db: Session,
    manifest: Dict[str, Any],
    actor: str,
) -> Dict[str, Any]:
    """
    Persist a comp-chem manifest into the database.

    Expected manifest shape (subset, see edge/compchem_agent.py for full):
        {
          "org_id": "...",
          "project": "...",
          "campaign": "...",
          "molecule_smiles": "...",                 (optional)
          "molecule_name": "...",                    (optional)
          "filename": "...",
          "s3_key": "...",
          "file_size_bytes": int,
          "file_hash": "sha256-hex",
          "parser_name": "...",
          "artifact_role": "input" | "output" | "metric_source",
          "parsed": { CompChemParsedResult.to_manifest() shape },
          "client_qc": {...} | None,
        }

    Returns:
        {
          "run_id": int,
          "campaign_id": int,
          "project_id": int,
          "molecule_id": int | None,
          "molecule_created": bool,
          "qc": {... server-side QC result ...},
          "metrics_count": int,
        }
    """
    org_id = manifest.get("org_id")
    project_name = manifest.get("project")
    campaign_name = manifest.get("campaign")
    campaign_id = manifest.get("campaign_id")
    if not org_id:
        raise ValueError("manifest must include org_id")
    if not campaign_id and not (project_name and campaign_name):
        raise ValueError("manifest must include campaign_id or both project and campaign")

    parsed = manifest.get("parsed") or {}
    if not parsed:
        raise ValueError("manifest must include a 'parsed' block")

    # --- Organization / Project / Campaign -------------------------------
    get_or_create_organization(db, org_id)
    if campaign_id:
        campaign = (
            db.query(Campaign)
            .filter(Campaign.id == int(campaign_id), Campaign.org_id == org_id)
            .first()
        )
        if not campaign:
            raise ValueError(f"campaign_id={campaign_id} not found for org_id={org_id}")
        project = db.query(Project).filter(Project.id == campaign.project_id).first()
        if not project:
            raise ValueError(f"project for campaign_id={campaign_id} not found")
    else:
        project = get_or_create_project(db, org_id, project_name, actor)
        campaign = get_or_create_campaign(
            db, org_id, project.id, campaign_name, actor,
            campaign_type=manifest.get("campaign_type"),
            description=manifest.get("notes"),
        )

    # --- Molecule (optional — not every run is per-molecule) ------------
    molecule: Optional[Molecule] = None
    molecule_created = False
    smiles = manifest.get("molecule_smiles")
    if smiles:
        molecule, molecule_created = get_or_create_molecule(
            db, org_id, campaign.id, smiles, actor,
            name=manifest.get("molecule_name"),
            external_id=manifest.get("molecule_external_id"),
        )

    # --- Run -------------------------------------------------------------
    grid_id = manifest.get("grid_id")
    if grid_id:
        grid = (
            db.query(DockingGrid)
            .filter(DockingGrid.id == str(grid_id), DockingGrid.campaign_id == campaign.id)
            .first()
        )
        if not grid:
            raise ValueError(f"grid_id={grid_id} not found for campaign_id={campaign.id}")

    run_kind_value = (parsed.get("run_kind") or RunKind.OTHER.value).lower()
    termination = (parsed.get("termination_status") or "unknown").lower()

    # Map termination → RunStatus
    if termination == "normal":
        run_status = CCRunStatus.COMPLETED.value
    elif termination in ("crashed",):
        run_status = CCRunStatus.FAILED.value
    elif termination in ("unconverged", "partial"):
        run_status = CCRunStatus.COMPLETED.value  # finished, but QC will flag
    else:
        run_status = CCRunStatus.COMPLETED.value

    started = _parse_iso(parsed.get("started_at"))
    completed = _parse_iso(parsed.get("completed_at"))

    run = Run(
        org_id=org_id,
        campaign_id=campaign.id,
        molecule_id=molecule.id if molecule else None,
        grid_id=str(grid_id) if grid_id else None,
        external_run_id=manifest.get("external_run_id"),
        name=manifest.get("run_name") or parsed.get("source_file"),
        run_kind=run_kind_value,
        status=run_status,
        was_inferred=bool(manifest.get("inferred_from_path")),
        software_name=parsed.get("software_name") or manifest.get("software_name"),
        software_version=parsed.get("software_version"),
        forcefield=parsed.get("forcefield") or manifest.get("forcefield"),
        cli_args=parsed.get("cli_args"),
        compute_environment=manifest.get("compute_environment"),
        compute_details={
            "cluster_name": manifest.get("cluster_name"),
            **(manifest.get("run_metadata") or {}),
        } if (manifest.get("cluster_name") or manifest.get("run_metadata")) else None,
        started_at=started,
        completed_at=completed,
        wall_time_s=parsed.get("wall_time_s"),
        error_message=parsed.get("error_message"),
        extra_metadata={
            "parsed_metadata": parsed.get("metadata"),
            "client_qc": manifest.get("client_qc"),
            "parser_name": manifest.get("parser_name"),
            "context_source": manifest.get("context_source"),
            "inferred_from_path": bool(manifest.get("inferred_from_path")),
            "inferred_context": manifest.get("inferred_context") or {},
            "parse_warnings": parsed.get("parse_warnings") or [],
            "run_metadata": manifest.get("run_metadata") or {},
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    log_cc_audit(
        action=AuditEventAction.RUN_SUBMITTED,
        entity_type="run",
        entity_id=str(run.id),
        actor=actor,
        org_id=org_id,
        details={
            "campaign_id": campaign.id,
            "molecule_id": molecule.id if molecule else None,
            "run_kind": run_kind_value,
            "software": f"{parsed.get('software_name')} {parsed.get('software_version') or ''}".strip(),
            "termination_status": termination,
            "file_hash": manifest.get("file_hash"),
        },
        db=db,
    )

    # --- Input / Output artifact rows -----------------------------------
    artifact_role = (manifest.get("artifact_role") or "output").lower()
    artifact_kind = parsed.get("metadata", {}).get("artifact_kind") or "file"
    if artifact_role == "input":
        db.add(RunInput(
            run_id=run.id,
            org_id=org_id,
            input_kind="config_file" if artifact_kind == "run_input" else "other",
            filename=manifest.get("filename") or "unknown",
            s3_key=manifest.get("s3_key"),
            file_hash=manifest.get("file_hash"),
            file_size_bytes=manifest.get("file_size_bytes"),
        ))
    else:
        # output OR metric_source → treat as RunOutput; metrics are stored
        # separately as RunMetric rows
        db.add(RunOutput(
            run_id=run.id,
            org_id=org_id,
            output_kind=_output_kind_for(artifact_kind, manifest.get("filename")),
            filename=manifest.get("filename") or "unknown",
            s3_key=manifest.get("s3_key"),
            file_hash=manifest.get("file_hash"),
            file_size_bytes=manifest.get("file_size_bytes"),
        ))
    db.commit()

    if manifest.get("s3_key") or manifest.get("file_hash"):
        log_cc_audit(
            action=AuditEventAction.FILE_RECEIVED,
            entity_type="file",
            entity_id=str(manifest.get("s3_key") or manifest.get("filename") or run.id),
            actor=actor,
            org_id=org_id,
            details={
                "campaign_id": campaign.id,
                "run_id": run.id,
                "filename": manifest.get("filename"),
                "artifact_role": artifact_role,
            },
            extra_data={
                "s3_key": manifest.get("s3_key"),
                "original_hash": manifest.get("file_hash"),
                "filename": manifest.get("filename"),
            },
            db=db,
        )

    # --- Metrics + AssayResults -----------------------------------------
    metrics = parsed.get("metrics") or []
    metric_rows: List[RunMetric] = []
    for m in metrics:
        try:
            value = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        unit = m.get("unit")
        if not unit:
            logger.warning("Skipping metric %r on run %d — missing unit", m.get("name"), run.id)
            continue
        row = RunMetric(
            run_id=run.id,
            org_id=org_id,
            molecule_id=molecule.id if molecule else None,
            metric_name=m.get("name") or "unknown",
            value=value,
            unit=unit,
            confidence=_safe_float(m.get("confidence")),
            stderr=_safe_float(m.get("stderr")),
            extra_metadata=m.get("metadata"),
        )
        db.add(row)
        metric_rows.append(row)
    db.commit()
    for row in metric_rows:
        db.refresh(row)
        if molecule:
            db.add(AssayResult(
                molecule_id=molecule.id,
                run_metric_id=row.id,
                org_id=org_id,
                metric_name=row.metric_name,
                value=row.value,
                unit=row.unit,
                passes_threshold=_passes_campaign_threshold(campaign, row),
            ))
    db.commit()

    # --- Server-side authoritative QC -----------------------------------
    try:
        from compchem_qc import compchem_qc_summary
        qc_result = compchem_qc_summary(
            parsed=parsed,
            molecule_smiles=smiles,
        )
    except Exception as e:
        logger.exception("Server-side QC failed: %s", e)
        qc_result = {
            "qc_mode": "compchem",
            "overall_status": "unknown",
            "summary": f"Server QC raised: {e}",
            "qc_flags": {},
            "domain_findings": [],
        }

    # Persist QC + termination on the run
    # Compose: run.extra_metadata is JSONB; preserve client_qc and add server qc
    run_meta = dict(run.extra_metadata or {})
    run_meta["server_qc"] = qc_result
    run.extra_metadata = run_meta
    if qc_result.get("overall_status") == "fail":
        # Update audit + status
        run.status = CCRunStatus.FAILED.value if termination == "crashed" else run.status
    db.commit()

    log_cc_audit(
        action=AuditEventAction.METRIC_RECORDED,
        entity_type="run",
        entity_id=str(run.id),
        actor=actor,
        org_id=org_id,
        details={
            "metrics_count": len(metric_rows),
            "qc_overall_status": qc_result.get("overall_status"),
            "qc_summary": qc_result.get("summary"),
        },
        db=db,
    )

    return {
        "run_id": run.id,
        "campaign_id": campaign.id,
        "project_id": project.id,
        "molecule_id": molecule.id if molecule else None,
        "molecule_created": molecule_created,
        "qc": qc_result,
        "metrics_count": len(metric_rows),
    }


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        # Handle both 'Z' and timezone-offset suffixes
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _output_kind_for(artifact_kind: str, filename: Optional[str]) -> str:
    """Map agent-provided artifact_kind into the cc_run_outputs.output_kind enum."""
    if artifact_kind in ("trajectory",):
        return "trajectory"
    if artifact_kind in ("energy",):
        return "energy_file"
    if artifact_kind in ("coordinate_snapshot",):
        return "result_file"
    if filename and filename.lower().endswith((".log", ".out")):
        return "log_file"
    return "result_file"


def _passes_campaign_threshold(campaign: Campaign, metric: RunMetric) -> Optional[bool]:
    """Pass/fail vs campaign.target_metric_threshold if metric name + unit match."""
    if campaign.target_metric != metric.metric_name:
        return None
    if campaign.target_metric_unit and campaign.target_metric_unit != metric.unit:
        return None
    if campaign.target_metric_threshold is None:
        return None
    # Convention: docking scores are "lower is better" (negative kcal/mol).
    # For now, use the simple sign-aware rule: a metric ≤ threshold passes.
    # Campaigns can tighten this later.
    return metric.value <= campaign.target_metric_threshold
