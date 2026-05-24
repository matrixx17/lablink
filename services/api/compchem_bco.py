"""
IEEE 2791-2020 BioCompute Object (BCO) export for comp-chem campaigns.

Spec reference: https://w3id.org/ieee/ieee-2791-schema/

A BCO is a structured provenance document that captures a computational
pipeline end-to-end: who ran it, what software stack, what inputs/outputs,
what parameters, and how to reproduce. We assemble one per campaign by
folding together the campaign metadata, its runs (one pipeline step per
run_kind), molecules (io_domain inputs), metrics (io_domain outputs), and
the existing comp-chem audit chain (provenance_domain.contributors).

The etag is SHA-256 of the canonical JSON of the document with the etag
field cleared — recompute on verify by zeroing etag and re-hashing.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from compchem_models import (
    AuditEvent,
    Campaign,
    Molecule,
    Project,
    Run,
    RunMetric,
)

BCO_SPEC_VERSION = "https://w3id.org/ieee/ieee-2791-schema/"

# Mapping run_kind -> (step_number, name, description, io uris). The step
# number is stable so that downstream readers can rely on ordering.
_RUN_KIND_STEPS: Dict[str, Dict[str, Any]] = {
    "docking": {
        "step_number": 1,
        "name": "Molecular Docking",
        "description": "Virtual screening via molecular docking",
        "input_uri": "lablink://molecules/input_ligands",
        "output_uri": "lablink://runs/docking_results",
    },
    "md": {
        "step_number": 2,
        "name": "Molecular Dynamics Simulation",
        "description": "All-atom MD simulation of ligand-target complex",
        "input_uri": "lablink://runs/docking_results",
        "output_uri": "lablink://runs/md_trajectories",
    },
    # The DB stores RunKind.MOLECULAR_DYNAMICS = "molecular_dynamics"; alias it
    # to the same MD step so external readers see one canonical MD step.
    "molecular_dynamics": {
        "step_number": 2,
        "name": "Molecular Dynamics Simulation",
        "description": "All-atom MD simulation of ligand-target complex",
        "input_uri": "lablink://runs/docking_results",
        "output_uri": "lablink://runs/md_trajectories",
    },
    "dft": {
        "step_number": 3,
        "name": "Quantum Mechanical Calculation",
        "description": "Density functional theory calculation",
        "input_uri": "lablink://runs/md_endpoints",
        "output_uri": "lablink://runs/dft_results",
    },
}

# Parameter names we surface in parametric_domain. Anything else stays in
# the run's extra_metadata.
_PARAM_KEYS = {"forcefield", "timestep_fs", "ensemble", "functional", "basis_set", "exhaustiveness"}


def _iso(ts: Optional[datetime]) -> Optional[str]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def compute_etag(bco: Dict[str, Any]) -> str:
    """SHA-256 over canonical JSON with the etag field zeroed."""
    body = dict(bco)
    body["etag"] = ""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _collect_contributors(db: Session, org_id: str, campaign_id: int) -> List[Dict[str, Any]]:
    """Unique actors who touched this campaign, oldest-first."""
    seen: "OrderedDict[str, None]" = OrderedDict()
    cid_str = str(campaign_id)

    # Primary filter: events that directly name the campaign as entity, plus
    # events whose details JSON references campaign_id. The second branch uses
    # a JSONB-specific operator that works on Postgres; on SQLite the same
    # query is silently a no-match, so we run a second query that scans the
    # campaign's runs and molecules to backfill contributors.
    rows = (
        db.query(AuditEvent.timestamp, AuditEvent.actor)
        .filter(
            AuditEvent.org_id == org_id,
            (
                (AuditEvent.entity_id == cid_str)
                | (AuditEvent.details["campaign_id"].astext == cid_str)
            ),
        )
        .order_by(AuditEvent.timestamp.asc())
        .all()
    )

    # Backfill: include actors from runs / molecules that belong to this
    # campaign, by joining audit events to those entity IDs.
    child_ids: List[str] = []
    child_ids += [str(rid) for (rid,) in db.query(Run.id).filter(
        Run.campaign_id == campaign_id, Run.org_id == org_id
    ).all()]
    child_ids += [str(mid) for (mid,) in db.query(Molecule.id).filter(
        Molecule.campaign_id == campaign_id, Molecule.org_id == org_id
    ).all()]
    if child_ids:
        extra = (
            db.query(AuditEvent.timestamp, AuditEvent.actor)
            .filter(
                AuditEvent.org_id == org_id,
                AuditEvent.entity_type.in_(("run", "molecule")),
                AuditEvent.entity_id.in_(child_ids),
            )
            .order_by(AuditEvent.timestamp.asc())
            .all()
        )
        rows = list(rows) + list(extra)
        rows.sort(key=lambda r: (r[0] or datetime.min.replace(tzinfo=timezone.utc)))

    for _, actor in rows:
        if actor and actor not in seen:
            seen[actor] = None

    return [
        {"contribution": ["authoredBy"], "name": actor}
        for actor in seen.keys()
    ]


def _provenance_domain(
    campaign: Campaign, contributors: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "name": campaign.name,
        "version": "1.0.0",
        "created": _iso(campaign.created_at),
        "modified": _iso(campaign.updated_at),
        "contributors": contributors,
        "license": "restricted",
        "derived_from": None,
        "obsolete_after": None,
        "embargo": {},
        "review": [],
    }


def _usability_domain(campaign: Campaign, project: Project) -> List[str]:
    if campaign.description and campaign.description.strip():
        return [campaign.description.strip()]
    target = (campaign.extra_metadata or {}).get("target") if campaign.extra_metadata else None
    target = target or project.name or "unspecified target"
    return [
        f"Computational chemistry campaign: {campaign.name}. "
        f"Target: {target}. "
        f"Generated by LabLink on {_today_iso()}."
    ]


def _pipeline_steps(runs: List[Run]) -> List[Dict[str, Any]]:
    """
    One pipeline step per unique step_number present. Run kinds that share
    a step (e.g. "md" and "molecular_dynamics") collapse to one entry.
    """
    by_step: Dict[int, Dict[str, Any]] = {}
    for r in runs:
        spec = _RUN_KIND_STEPS.get(r.run_kind)
        if spec is None:
            spec = {
                "step_number": 99,
                "name": (r.run_kind or "unknown").title(),
                "description": f"{(r.run_kind or 'unknown').title()} runs",
                "input_uri": f"lablink://runs/{r.run_kind or 'unknown'}_inputs",
                "output_uri": f"lablink://runs/{r.run_kind or 'unknown'}_outputs",
            }
        bucket = by_step.setdefault(spec["step_number"], {"spec": spec, "runs": []})
        bucket["runs"].append(r)

    steps = []
    for step_number, bucket in by_step.items():
        spec = bucket["spec"]
        version = _mode([r.software_version for r in bucket["runs"] if r.software_version])
        steps.append({
            "step_number": step_number,
            "name": spec["name"],
            "description": spec["description"],
            "version": version or "",
            "input_list": [{"uri": spec["input_uri"]}],
            "output_list": [{"uri": spec["output_uri"]}],
        })

    steps.sort(key=lambda s: s["step_number"])
    return steps


def _mode(values: List[str]) -> Optional[str]:
    if not values:
        return None
    counter = Counter(values)
    return counter.most_common(1)[0][0]


def _description_domain(
    campaign: Campaign, project: Project, runs: List[Run]
) -> Dict[str, Any]:
    target = (campaign.extra_metadata or {}).get("target") if campaign.extra_metadata else None
    keywords = ["computational chemistry", "drug discovery"]
    if target:
        keywords.append(target)
    keywords.append("CADD")

    platform: List[str] = []
    seen = set()
    for r in runs:
        if r.software_name and r.software_name not in seen:
            platform.append(r.software_name)
            seen.add(r.software_name)

    return {
        "keywords": keywords,
        "platform": platform,
        "pipeline_steps": _pipeline_steps(runs),
    }


def _execution_domain(
    campaign: Campaign, runs: List[Run]
) -> Dict[str, Any]:
    prereqs = []
    seen: set = set()
    for r in runs:
        if not r.software_name:
            continue
        key = (r.software_name, r.software_version or "")
        if key in seen:
            continue
        seen.add(key)
        prereqs.append({
            "name": r.software_name,
            "version": r.software_version or "",
            "uri": {"uri": ""},
        })

    return {
        "script": [f"lablink://campaigns/{campaign.id}/export/bco"],
        "script_driver": "LabLink Provenance Engine v1.0",
        "software_prerequisites": prereqs,
        "environment_variables": {},
        "external_data_endpoints": [
            {"name": "LabLink API", "url": f"lablink://campaigns/{campaign.id}"}
        ],
    }


def _io_domain(
    db: Session, campaign: Campaign
) -> Dict[str, Any]:
    molecules = (
        db.query(Molecule)
        .filter(Molecule.campaign_id == campaign.id, Molecule.org_id == campaign.org_id)
        .order_by(Molecule.id.asc())
        .all()
    )

    input_subdomain = [
        {
            "uri": f"lablink://molecules/{m.id}",
            "access_time": _iso(m.created_at),
        }
        for m in molecules
    ]

    # For each molecule, find the most recent run that produced a metric for
    # it and use that run's created_at as the access_time.
    output_subdomain: List[Dict[str, Any]] = []
    for m in molecules:
        latest = (
            db.query(Run.created_at)
            .join(RunMetric, RunMetric.run_id == Run.id)
            .filter(RunMetric.molecule_id == m.id, Run.org_id == campaign.org_id)
            .order_by(Run.created_at.desc())
            .first()
        )
        access_time = _iso(latest[0]) if latest else _iso(m.created_at)
        output_subdomain.append({
            "uri": f"lablink://molecules/{m.id}/metrics",
            "access_time": access_time,
        })

    return {
        "input_subdomain": input_subdomain,
        "output_subdomain": output_subdomain,
    }


def _parametric_domain(runs: List[Run]) -> List[Dict[str, Any]]:
    """
    Surface (param_name, value) pairs from each run's extra_metadata, scoped
    to a known whitelist of comp-chem parameters. step number comes from
    _RUN_KIND_STEPS so the same (param, value) under different run kinds
    appears once per step.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in runs:
        spec = _RUN_KIND_STEPS.get(r.run_kind)
        step = spec["step_number"] if spec else 99

        # forcefield is a first-class column on Run; everything else lives in
        # extra_metadata.
        candidates: List[Tuple[str, Any]] = []
        if r.forcefield:
            candidates.append(("forcefield", r.forcefield))
        meta = r.extra_metadata or {}
        for key in _PARAM_KEYS:
            if key == "forcefield":
                continue  # already handled
            if key in meta and meta[key] is not None:
                candidates.append((key, meta[key]))

        for param_name, value in candidates:
            key = (param_name, str(value), step)
            if key in seen:
                continue
            seen.add(key)
            out.append({"param": param_name, "value": str(value), "step": step})

    out.sort(key=lambda p: (p["step"], p["param"]))
    return out


def build_bco(
    db: Session, campaign: Campaign, project: Project
) -> Dict[str, Any]:
    """Assemble the full BCO document for a campaign."""
    runs = (
        db.query(Run)
        .filter(Run.campaign_id == campaign.id, Run.org_id == campaign.org_id)
        .order_by(Run.created_at.asc())
        .all()
    )
    contributors = _collect_contributors(db, campaign.org_id, campaign.id)

    bco: Dict[str, Any] = {
        "object_id": f"lablink/{campaign.id}/bco/v1",
        "spec_version": BCO_SPEC_VERSION,
        "etag": "",  # placeholder, filled in below
        "provenance_domain": _provenance_domain(campaign, contributors),
        "usability_domain": _usability_domain(campaign, project),
        "description_domain": _description_domain(campaign, project, runs),
        "execution_domain": _execution_domain(campaign, runs),
        "io_domain": _io_domain(db, campaign),
        "parametric_domain": _parametric_domain(runs),
        "error_domain": {
            "empirical_error": {},
            "algorithmic_error": {},
        },
    }
    bco["etag"] = compute_etag(bco)
    return bco
