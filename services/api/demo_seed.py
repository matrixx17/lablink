"""Seed/reset helpers for the public comp-chem demo environment."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.orm import Session

from compchem_ingest import ingest_run_manifest
from compchem_models import (
    AssayResult,
    AuditEvent,
    Campaign,
    DockingGrid,
    Molecule,
    MoleculeProperty,
    OrgCredential,
    Organization,
    OrgUser,
    Project,
    Run,
    RunInput,
    RunLineage,
    RunMetric,
    RunOutput,
    compute_audit_hash,
)

DEMO_ORG_ID = "demo-therapeutics"
DEMO_ORG_NAME = "Demo Therapeutics"
DEMO_ADMIN_EMAIL = "demo@lablink.io"
DEMO_ADMIN_PASSWORD = "LabLinkDemo"
DEMO_CONTEXT = (
    "Demo Therapeutics engaged CRO partner Bio Labs to run a virtual screening campaign "
    "against EGFR kinase. Bio delivered results via LabLink on May 22, 2026. This record "
    "shows the complete computational history of how compound AC-007 was selected as the "
    "lead candidate."
)

# A handful of representative delivery artifacts surfaced as file_received audit
# events. Hashes are deterministic so the BCO/Evidence-Book outputs stay byte-stable
# between demo resets; the S3 objects themselves are not created (the
# verify-delivery route falls back to demo_mode=true when fetch fails).
DEMO_DELIVERY_FILES = (
    ("dock_AC-007_out.pdbqt", "delivery_round3_dock_AC-007"),
    ("dock_AC-014_out.pdbqt", "delivery_round3_dock_AC-014"),
    ("delivery_manifest_round3.json", "delivery_round3_manifest"),
)

DEMO_APPROVALS_METADATA = [
    {
        "name": "Dr. John Doe",
        "role": "author",
        "date": "2026-05-22T14:30:00+00:00",
        "comments": "Authored the round-3 lead-optimization analysis; AC-007 carries the strongest combined docking + MD + DFT signal.",
    },
    {
        "name": "Dr. Priya Raman",
        "role": "reviewer",
        "date": "2026-05-22T16:05:00+00:00",
        "comments": "Reviewed all 10 docking poses and the MD/DFT follow-ups. Methods and parameter logs are complete; concur with AC-007 nomination.",
    },
]


def hash_demo_password(password: str) -> str:
    """PBKDF2 hash for the demo admin password."""
    salt = os.getenv("DEMO_PASSWORD_SALT", "lablink-demo-salt").encode("utf-8")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return "pbkdf2_sha256$120000$" + digest.hex()


def reset_demo_environment(db: Session) -> Dict[str, Any]:
    """Delete demo org data and recreate the curated demo dataset."""
    _delete_demo_data(db)
    _seed_demo_data(db)
    reset_at = datetime.now(timezone.utc).isoformat()
    return {"status": "ok", "reset_at": reset_at}


def _delete_demo_data(db: Session) -> None:
    org_id = DEMO_ORG_ID
    run_ids = [rid for (rid,) in db.query(Run.id).filter(Run.org_id == org_id).all()]
    molecule_ids = [mid for (mid,) in db.query(Molecule.id).filter(Molecule.org_id == org_id).all()]
    campaign_ids = [cid for (cid,) in db.query(Campaign.id).filter(Campaign.org_id == org_id).all()]
    project_ids = [pid for (pid,) in db.query(Project.id).filter(Project.org_id == org_id).all()]

    db.query(Campaign).filter(Campaign.org_id == org_id).update(
        {Campaign.lead_molecule_id: None},
        synchronize_session=False,
    )
    db.commit()

    if molecule_ids:
        db.query(AssayResult).filter(AssayResult.molecule_id.in_(molecule_ids)).delete(synchronize_session=False)
        db.query(MoleculeProperty).filter(MoleculeProperty.molecule_id.in_(molecule_ids)).delete(synchronize_session=False)
    if run_ids:
        db.query(RunLineage).filter(
            (RunLineage.parent_run_id.in_(run_ids)) | (RunLineage.child_run_id.in_(run_ids))
        ).delete(synchronize_session=False)
        db.query(RunMetric).filter(RunMetric.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(RunInput).filter(RunInput.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(RunOutput).filter(RunOutput.run_id.in_(run_ids)).delete(synchronize_session=False)
    db.query(AuditEvent).filter(AuditEvent.org_id == org_id).delete(synchronize_session=False)
    db.query(Run).filter(Run.org_id == org_id).delete(synchronize_session=False)
    db.query(Molecule).filter(Molecule.org_id == org_id).delete(synchronize_session=False)
    if campaign_ids:
        db.query(DockingGrid).filter(DockingGrid.campaign_id.in_(campaign_ids)).delete(synchronize_session=False)
    db.query(Campaign).filter(Campaign.org_id == org_id).delete(synchronize_session=False)
    db.query(Project).filter(Project.org_id == org_id).delete(synchronize_session=False)
    db.query(OrgCredential).filter(OrgCredential.org_id == org_id).delete(synchronize_session=False)
    db.query(OrgUser).filter(OrgUser.org_id == org_id).delete(synchronize_session=False)
    db.commit()


def _seed_demo_data(db: Session) -> None:
    org = db.query(Organization).filter(Organization.org_id == DEMO_ORG_ID).first()
    if not org:
        org = Organization(org_id=DEMO_ORG_ID)
        db.add(org)
    org.name = DEMO_ORG_NAME
    org.demo_mode = True
    db.commit()

    db.add(OrgUser(
        id=str(uuid.uuid4()),
        org_id=DEMO_ORG_ID,
        email=DEMO_ADMIN_EMAIL,
        password_hash=hash_demo_password(DEMO_ADMIN_PASSWORD),
        is_admin=True,
    ))
    db.commit()

    project = Project(
        org_id=DEMO_ORG_ID,
        name="EGFR antiviral discovery",
        description="Demo computational chemistry program for mutated viral-protein interaction modeling.",
        target_name="EGFR mutant panel",
        target_uniprot="P00533",
        indication="Antiviral lead optimization",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    campaign = Campaign(
        org_id=DEMO_ORG_ID,
        project_id=project.id,
        name="lead_opt_round_3",
        description=DEMO_CONTEXT,
        campaign_type="lead_optimization",
        hypothesis="Substituted amide analogs improve docking score while preserving drug-like properties.",
        target_metric="best_binding_affinity",
        target_metric_unit="kcal/mol",
        target_metric_threshold=-8.0,
        status="lead_nominated",
        extra_metadata={
            "delivery_date": "2026-05-22",
            "cro_partner": "Bio Labs",
            "delivery_credential": "cro_upload_bl_egfr_001",
            "extra_params": {"delivery_date": "2026-05-22"},
            "approvals": DEMO_APPROVALS_METADATA,
            "is_approved": True,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    grid = DockingGrid(
        id=str(uuid.uuid4()),
        campaign_id=campaign.id,
        name="EGFR ATP pocket tight grid",
        receptor_pdb_s3_key="demo/receptors/egfr_mutant_atp_site.pdb",
        receptor_pdb_hash="a" * 64,
        software="AutoDock Vina",
        software_version="1.2.5",
        box_center_x=10.0,
        box_center_y=11.0,
        box_center_z=12.0,
        box_size_x=20.0,
        box_size_y=20.0,
        box_size_z=20.0,
        exhaustiveness=16,
        extra_params={"num_modes": 9, "scoring_function": "vina"},
        notes="ATP-site grid used for Round 3 docking demo.",
    )
    db.add(grid)
    db.commit()

    compounds = [
        ("AC-007", "mol_001", "Cc1ccc(cc1)C(=O)Nc2ccncc2", -9.2, 0.72),
        ("AC-014", "mol_002", "COc1ccc(cc1)C(=O)Nc2ccncc2", -8.6, 0.69),
        ("AC-021", "mol_003", "Cc1ccc(cc1)C(=O)Nc2ncccc2F", -7.8, 0.61),
        ("AC-033", "mol_004", "CCOc1ccc(cc1)C(=O)Nc2ccncc2", -8.9, 0.76),
        ("AC-041", "mol_005", "Cc1ccccc1C(=O)Nc2ccncc2", -8.4, 0.64),
        ("AC-052", "mol_006", "CC(C)c1ccc(cc1)C(=O)Nc2ccncc2", -8.1, 0.58),
        ("AC-068", "mol_007", "Fc1ccc(cc1)C(=O)Nc2ccncc2", -7.4, 0.55),
        ("AC-079", "mol_008", "Clc1ccc(cc1)C(=O)Nc2ccncc2", -8.0, 0.57),
        ("AC-083", "mol_009", "CCn1cccc1C(=O)Nc2ccncc2", -7.1, 0.49),
        ("AC-099", "mol_010", "Cn1cccc1C(=O)Nc2ccncc2", -7.6, 0.52),
    ]

    docking_results = []
    for idx, (name, external_id, smiles, score, qed) in enumerate(compounds, start=1):
        manifest = _dock_manifest(campaign, grid, idx, name, external_id, smiles, score, qed)
        result = ingest_run_manifest(db=db, manifest=manifest, actor="cro_upload_bl_egfr_001")
        docking_results.append((result, name, external_id, smiles, score))
        _add_run_complete_event(
            db=db,
            campaign=campaign,
            run_id=result["run_id"],
            actor="cro_upload_bl_egfr_001",
            message=f"Docking run completed for {name} and delivered by Bio Labs.",
        )

    for offset_seconds, (filename, key_suffix) in enumerate(DEMO_DELIVERY_FILES):
        s3_key = f"demo/{DEMO_ORG_ID}/{campaign.name}/{filename}"
        original_hash = hashlib.sha256(f"{campaign.id}:{key_suffix}".encode("utf-8")).hexdigest()
        _add_audit_event(
            db=db,
            org_id=DEMO_ORG_ID,
            action="file_received",
            entity_type="file",
            entity_id=s3_key,
            actor="cro_upload_bl_egfr_001",
            details={
                "campaign_id": campaign.id,
                "filename": filename,
                "delivered_by": "Bio Labs",
            },
            extra_data={
                "s3_key": s3_key,
                "original_hash": original_hash,
                "filename": filename,
            },
            timestamp=datetime(2026, 5, 22, 14, 55, offset_seconds, tzinfo=timezone.utc),
        )

    _add_audit_event(
        db=db,
        org_id=DEMO_ORG_ID,
        action="cro_delivery",
        entity_type="campaign",
        entity_id=str(campaign.id),
        actor="cro_upload_bl_egfr_001",
        details={
            "message": (
                "Campaign results delivered by Bio Labs via LabLink secure upload. "
                "Credential: cro_upload_bl_egfr_001. 10 compounds, 10 docking runs. "
                "Verified: all file hashes match delivery manifest."
            ),
            "delivered_by": "Bio Labs",
            "delivery_date": "2026-05-22",
            "compound_count": 10,
            "docking_run_count": 10,
            "verified": True,
        },
        timestamp=datetime(2026, 5, 22, 15, 0, tzinfo=timezone.utc),
    )

    md_inputs = [
        ("AC-007", "mol_001", "Cc1ccc(cc1)C(=O)Nc2ccncc2", 1.8, -11820.4),
        ("AC-014", "mol_002", "COc1ccc(cc1)C(=O)Nc2ccncc2", 2.4, -11690.1),
        ("AC-033", "mol_004", "CCOc1ccc(cc1)C(=O)Nc2ccncc2", 2.1, -11710.9),
        ("AC-041", "mol_005", "Cc1ccccc1C(=O)Nc2ccncc2", 3.7, -11340.5),
    ]
    md_results = {}
    for idx, (name, external_id, smiles, rmsd, potential_energy) in enumerate(md_inputs, start=1):
        result = ingest_run_manifest(
            db=db,
            manifest=_md_manifest(campaign, idx, name, external_id, smiles, rmsd, potential_energy),
            actor="demo_computational_team",
        )
        _add_run_complete_event(
            db=db,
            campaign=campaign,
            run_id=result["run_id"],
            actor="demo_computational_team",
            message=f"Internal 100 ns MD follow-up completed for {name}.",
        )
        md_results[external_id] = result["run_id"]

    dft_inputs = [
        ("AC-007", "mol_001", "Cc1ccc(cc1)C(=O)Nc2ccncc2", 4.21, -734.128),
        ("AC-033", "mol_004", "CCOc1ccc(cc1)C(=O)Nc2ccncc2", 3.62, -742.912),
    ]
    dft_results = {}
    for idx, (name, external_id, smiles, gap, energy) in enumerate(dft_inputs, start=1):
        result = ingest_run_manifest(
            db=db,
            manifest=_dft_manifest(campaign, idx, name, external_id, smiles, gap, energy),
            actor="demo_computational_team",
        )
        _add_run_complete_event(
            db=db,
            campaign=campaign,
            run_id=result["run_id"],
            actor="demo_computational_team",
            message=f"Internal DFT electronic-profile calculation completed for {name}.",
        )
        dft_results[external_id] = result["run_id"]

    docking_by_external_id = {external_id: result["run_id"] for result, _, external_id, _, _ in docking_results}
    for external_id, md_run_id in md_results.items():
        dock_run_id = docking_by_external_id.get(external_id)
        if dock_run_id:
            db.add(RunLineage(
                parent_run_id=dock_run_id,
                child_run_id=md_run_id,
                relationship="dock_to_md",
                extra_metadata={"molecule_external_id": external_id},
            ))
    for external_id, dft_run_id in dft_results.items():
        md_run_id = md_results.get(external_id)
        if md_run_id:
            db.add(RunLineage(
                parent_run_id=md_run_id,
                child_run_id=dft_run_id,
                relationship="md_to_dft",
                extra_metadata={"molecule_external_id": external_id},
            ))
    db.commit()

    for approval in DEMO_APPROVALS_METADATA:
        _add_audit_event(
            db=db,
            org_id=DEMO_ORG_ID,
            action="campaign_approved",
            entity_type="campaign",
            entity_id=str(campaign.id),
            actor=approval["name"],
            details={
                "campaign_id": campaign.id,
                "approval_meaning": approval["role"],
                "approved_by_name": approval["name"],
                "comments": approval["comments"],
                "message": f"{approval['name']} signed off as {approval['role']}.",
            },
            timestamp=datetime.fromisoformat(approval["date"]),
        )

    lead = (
        db.query(Molecule)
        .filter(Molecule.org_id == DEMO_ORG_ID, Molecule.campaign_id == campaign.id, Molecule.external_id == "mol_001")
        .first()
    )
    if lead:
        campaign.lead_molecule_id = lead.id
        campaign.status = "lead_nominated"
        db.commit()
        _add_audit_event(
            db=db,
            org_id=DEMO_ORG_ID,
            action="lead_nominated",
            entity_type="molecule",
            entity_id=str(lead.id),
            actor="dr_john_doe",
            details={
                "message": (
                    "AC-007 nominated as lead candidate based on docking score (-9.2 kcal/mol), "
                    "MD stability (RMSD 1.8Å), and favorable DFT electronic profile "
                    "(HOMO-LUMO gap 4.21 eV). Approved by: Dr. John Doe, Head of "
                    "Computational Chemistry."
                ),
                "compound_id": "mol_001",
                "compound_name": "AC-007",
                "approved_by": "Dr. John Doe, Head of Computational Chemistry",
                "docking_score_kcal_mol": -9.2,
                "md_rmsd_A": 1.8,
                "homo_lumo_gap_eV": 4.21,
                "campaign_id": campaign.id,
            },
            timestamp=datetime(2026, 5, 22, 14, 23, tzinfo=timezone.utc),
        )


def _dock_manifest(
    campaign: Campaign,
    grid: DockingGrid,
    index: int,
    molecule_name: str,
    molecule_external_id: str,
    smiles: str,
    score: float,
    qed: float,
) -> Dict[str, Any]:
    return {
        "org_id": DEMO_ORG_ID,
        "campaign_id": campaign.id,
        "project": "EGFR antiviral discovery",
        "campaign": campaign.name,
        "molecule_smiles": smiles,
        "molecule_name": molecule_name,
        "molecule_external_id": molecule_external_id,
        "filename": f"dock_{molecule_name}_out.pdbqt",
        "s3_key": f"demo/{DEMO_ORG_ID}/{campaign.name}/dock_{molecule_name}_out.pdbqt",
        "file_size_bytes": 1200 + index,
        "file_hash": hashlib.sha256(f"{campaign.id}:{molecule_name}:{score}".encode("utf-8")).hexdigest(),
        "grid_id": grid.id,
        "parser_name": "autodock_vina",
        "artifact_role": "metric_source",
        "compute_environment": "hpc_slurm",
        "cluster_name": "demo-cluster",
        "run_metadata": {
            "delivered_by": "Bio Labs",
            "delivery_credential": "cro_upload_bl_egfr_001",
            "delivery_date": "2026-05-22",
            "scoring_function": "vina",
            "exhaustiveness": 16,
            "num_modes": 9,
        },
        "parsed": {
            "source_file": f"dock_{molecule_name}_out.pdbqt",
            "software_name": "AutoDock Vina",
            "software_version": "1.2.5",
            "run_kind": "docking",
            "termination_status": "normal",
            "forcefield": "AMBER ff19SB",
            "metrics": [
                {"name": "docking_score_top", "value": score, "unit": "kcal/mol"},
                {"name": "best_binding_affinity", "value": score, "unit": "kcal/mol"},
                {"name": "pose_affinity_rank_1", "value": score, "unit": "kcal/mol", "metadata": {"rank": 1}},
                {"name": "pose_affinity_rank_2", "value": score + 0.4, "unit": "kcal/mol", "metadata": {"rank": 2}},
                {"name": "qed", "value": qed, "unit": "dimensionless"},
            ],
            "metadata": {
                "n_poses": 3,
                "grid_name": grid.name,
                "extra_params": {
                    "delivered_by": "Bio Labs",
                    "delivery_credential": "cro_upload_bl_egfr_001",
                },
            },
        },
        "client_qc": {"overall_status": "pass"},
    }


def _md_manifest(
    campaign: Campaign,
    index: int,
    molecule_name: str,
    molecule_external_id: str,
    smiles: str,
    rmsd: float,
    potential_energy: float,
) -> Dict[str, Any]:
    return {
        "org_id": DEMO_ORG_ID,
        "campaign_id": campaign.id,
        "project": "EGFR antiviral discovery",
        "campaign": campaign.name,
        "molecule_smiles": smiles,
        "molecule_name": molecule_name,
        "molecule_external_id": molecule_external_id,
        "filename": f"md_{molecule_name}_100ns.log",
        "s3_key": f"demo/{DEMO_ORG_ID}/{campaign.name}/md_{molecule_name}_100ns.log",
        "file_size_bytes": 2400 + index,
        "file_hash": hashlib.sha256(f"md:{campaign.id}:{molecule_name}:{rmsd}".encode("utf-8")).hexdigest(),
        "parser_name": "gromacs",
        "artifact_role": "metric_source",
        "compute_environment": "hpc_slurm",
        "cluster_name": "demo-cluster",
        "run_metadata": {
            "timestep_fs": 2,
            "n_steps": 50_000_000,
            "total_time_ns": 100,
            "ensemble": "NPT",
            "temperature_k": 300,
            "pressure_bar": 1,
        },
        "parsed": {
            "source_file": f"md_{molecule_name}_100ns.log",
            "software_name": "GROMACS",
            "software_version": "2024.1",
            "run_kind": "molecular_dynamics",
            "termination_status": "normal",
            "forcefield": "AMBER ff19SB",
            "metrics": [
                {"name": "md_rmsd", "value": rmsd, "unit": "Å"},
                {"name": "mean_potential_energy", "value": potential_energy, "unit": "kJ/mol"},
            ],
            "metadata": {"simulation_ns": 100, "equilibration_ns": 10},
        },
        "client_qc": {"overall_status": "pass" if rmsd < 3.0 else "warn"},
    }


def _dft_manifest(
    campaign: Campaign,
    index: int,
    molecule_name: str,
    molecule_external_id: str,
    smiles: str,
    homo_lumo_gap: float,
    final_energy_hartree: float,
) -> Dict[str, Any]:
    return {
        "org_id": DEMO_ORG_ID,
        "campaign_id": campaign.id,
        "project": "EGFR antiviral discovery",
        "campaign": campaign.name,
        "molecule_smiles": smiles,
        "molecule_name": molecule_name,
        "molecule_external_id": molecule_external_id,
        "filename": f"dft_{molecule_name}_b3lyp.log",
        "s3_key": f"demo/{DEMO_ORG_ID}/{campaign.name}/dft_{molecule_name}_b3lyp.log",
        "file_size_bytes": 1800 + index,
        "file_hash": hashlib.sha256(f"dft:{campaign.id}:{molecule_name}:{homo_lumo_gap}".encode("utf-8")).hexdigest(),
        "parser_name": "gaussian_orca",
        "artifact_role": "metric_source",
        "compute_environment": "hpc_slurm",
        "cluster_name": "demo-cluster",
        "run_metadata": {
            "functional": "B3LYP",
            "basis_set": "6-31G*",
            "solvent_model": "PCM",
            "dispersion_correction": "D3BJ",
        },
        "parsed": {
            "source_file": f"dft_{molecule_name}_b3lyp.log",
            "software_name": "ORCA",
            "software_version": "5.0.4",
            "run_kind": "dft",
            "termination_status": "normal",
            "method": "B3LYP/6-31G*",
            "metrics": [
                {"name": "homo_lumo_gap", "value": homo_lumo_gap, "unit": "eV"},
                {"name": "final_energy", "value": final_energy_hartree, "unit": "Hartree"},
            ],
            "metadata": {"scf_cycles": 82, "imaginary_frequencies": 0},
        },
        "client_qc": {"overall_status": "pass"},
    }


def _add_audit_event(
    db: Session,
    org_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    details: Dict[str, Any],
    timestamp: datetime,
    extra_data: Dict[str, Any] | None = None,
) -> AuditEvent:
    previous_record = (
        db.query(AuditEvent)
        .filter(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.id.desc())
        .first()
    )
    previous_hash = previous_record.record_hash if previous_record else None
    record_hash = compute_audit_hash(
        timestamp=timestamp,
        org_id=org_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        details=details,
        previous_hash=previous_hash,
    )
    event = AuditEvent(
        timestamp=timestamp,
        org_id=org_id,
        action=action,
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


def _add_run_complete_event(
    db: Session,
    campaign: Campaign,
    run_id: int,
    actor: str,
    message: str,
) -> AuditEvent:
    return _add_audit_event(
        db=db,
        org_id=DEMO_ORG_ID,
        action="run_complete",
        entity_type="run",
        entity_id=str(run_id),
        actor=actor,
        details={"campaign_id": campaign.id, "message": message, "run_id": run_id},
        timestamp=datetime(2026, 5, 22, 16, 0, tzinfo=timezone.utc),
    )
