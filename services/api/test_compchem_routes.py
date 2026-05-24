"""
End-to-end tests for the comp-chem API routes.

Strategy:
  - Spin up an in-process SQLite DB (file-backed so it survives across the
    multiple sessions FastAPI creates per request).
  - Patch SessionLocal / engine in `database` BEFORE compchem_models is
    imported, so the ORM binds to the test DB.
  - Use FastAPI's TestClient — no docker, no postgres, no network.

This catches integration bugs the unit tests miss: route wiring, Pydantic
serialisation, FK constraints, audit chain continuity across endpoints.

Run with:
    cd services/api && python test_compchem_routes.py

Note: SQLite doesn't fully support all PostgreSQL features. Specifically:
  - JSONB columns degrade to JSON (fine for tests)
  - ARRAY columns aren't supported (we don't use any in comp-chem models)
  - The campaign-event-count subquery uses ->>'campaign_id' which is JSONB-
    specific; on SQLite that route is skipped in the test suite.
"""

from __future__ import annotations

import os
import sys
import tempfile

# --- DB patching ----------------------------------------------------------
# Must happen BEFORE we import anything that touches database.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMPDB.close()
os.environ["LABLINK_TEST_DB"] = _TMPDB.name
os.environ["DEMO_RESET_SECRET"] = "test-demo-reset-secret"

import database  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_test_engine = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    future=True,
)
database.engine = _test_engine
database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine, future=True)

# Now safe to import the rest
import compchem_models  # noqa: E402,F401  (registers tables on Base)
from database import Base, ApiKey  # noqa: E402

# Patch JSONB → JSON for SQLite compatibility.
# Two-step: (1) register a SQLite type compiler that renders JSONB as JSON,
# (2) ensure column instances bind their bind/result processors via the JSON
# implementation when running against SQLite.
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler  # noqa: E402

def _visit_JSONB(self, type_, **kw):  # type: ignore[no-untyped-def]
    return "JSON"

SQLiteTypeCompiler.visit_JSONB = _visit_JSONB  # type: ignore[attr-defined]

# JSONB's bind/result processors call json.dumps/loads, which works fine on
# SQLite columns typed as JSON, so we don't need to intercept those.

# Only create the comp-chem tables. Importing database.py registers the
# lab-instrument webhook table which uses ARRAY (postgres-only); we don't
# need it for these tests. ApiKey is included because Bearer org tokens share
# the existing auth verifier.
from compchem_models import (  # noqa: E402
    Organization, OrgCredential, OrgUser, Project, Campaign, DockingGrid, Run, RunInput, RunOutput, RunMetric, RunLineage,
    Molecule, MoleculeProperty, AssayResult, AuditEvent,
)
_cc_tables = [
    t.__table__ for t in (
        ApiKey, Organization, OrgCredential, OrgUser, Project, Campaign, DockingGrid, Molecule, Run, RunInput, RunOutput,
        RunMetric, RunLineage, MoleculeProperty, AssayResult, AuditEvent,
    )
]
Base.metadata.create_all(bind=_test_engine, tables=_cc_tables)

from fastapi.testclient import TestClient  # noqa: E402
from compchem_routes import router as compchem_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402

app = FastAPI()
app.include_router(compchem_router)
client = TestClient(app)


# --- Tests ----------------------------------------------------------------

ORG = "acme-pharma"


def _q(p):
    return {**p, "org_id": ORG} if "org_id" not in p else p


def test_create_campaign_creates_project_and_campaign():
    r = client.post(
        "/api/v1/campaigns",
        params={"org_id": ORG},
        json={
            "org_id": ORG,
            "project_name": "EGFR-program-2026",
            "name": "lead_opt_round_3",
            "campaign_type": "lead_optimization",
            "target_metric": "best_binding_affinity",
            "target_metric_unit": "kcal/mol",
            "target_metric_threshold": -8.0,
            "project": {
                "name": "EGFR-program-2026",
                "target_name": "EGFR",
                "target_uniprot": "P00533",
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "lead_opt_round_3"
    assert body["project_name"] == "EGFR-program-2026"
    assert body["run_count"] == 0
    assert body["molecule_count"] == 0
    assert body["target_metric_threshold"] == -8.0
    # cache for later tests
    global _CAMPAIGN_ID
    _CAMPAIGN_ID = body["id"]


def test_create_campaign_duplicate_returns_409():
    r = client.post(
        "/api/v1/campaigns",
        params={"org_id": ORG},
        json={
            "org_id": ORG,
            "project_name": "EGFR-program-2026",
            "name": "lead_opt_round_3",
        },
    )
    assert r.status_code == 409, r.text


def test_get_campaign_includes_counts():
    r = client.get(f"/api/v1/campaigns/{_CAMPAIGN_ID}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == _CAMPAIGN_ID
    assert body["run_count"] == 0


def test_get_campaign_404_for_unknown():
    r = client.get("/api/v1/campaigns/99999", params={"org_id": ORG})
    assert r.status_code == 404


def test_demo_login_and_reset_seed_demo_org():
    r = client.post("/api/v1/demo/login")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org_id"] == "demo-therapeutics"
    assert body["email"] == "demo@lablink.io"
    assert body["demo_mode"] is True

    r = client.get("/api/v1/orgs/demo-therapeutics", params={"org_id": "demo-therapeutics"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Demo Therapeutics"
    assert r.json()["demo_mode"] is True

    r = client.post("/demo/reset", headers={"X-Demo-Reset-Secret": "wrong"})
    assert r.status_code == 401

    r = client.post("/demo/reset", headers={"X-Demo-Reset-Secret": "test-demo-reset-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["reset_at"]

    r = client.get("/api/v1/campaigns", params={"org_id": "demo-therapeutics"})
    assert r.status_code == 200, r.text
    campaigns = r.json()
    assert len(campaigns) >= 1
    campaign = campaigns[0]
    assert campaign["status"] == "lead_nominated"
    assert campaign["lead_molecule_id"] is not None
    assert campaign["run_count"] == 16
    assert "Bio Labs" in campaign["description"]

    db = database.SessionLocal()
    from compchem_models import AuditEvent, Campaign, Molecule, Run
    demo_campaign = db.query(Campaign).filter(Campaign.org_id == "demo-therapeutics").first()
    assert demo_campaign is not None
    assert demo_campaign.extra_metadata["delivery_date"] == "2026-05-22"
    lead = db.query(Molecule).filter(Molecule.id == demo_campaign.lead_molecule_id).first()
    assert lead is not None
    assert lead.name == "AC-007"
    assert lead.external_id == "mol_001"
    assert db.query(Run).filter(Run.campaign_id == demo_campaign.id, Run.run_kind == "docking").count() == 10
    assert db.query(Run).filter(Run.campaign_id == demo_campaign.id, Run.run_kind == "molecular_dynamics").count() == 4
    assert db.query(Run).filter(Run.campaign_id == demo_campaign.id, Run.run_kind == "dft").count() == 2
    assert db.query(AuditEvent).filter(AuditEvent.org_id == "demo-therapeutics", AuditEvent.action == "cro_delivery").count() == 1
    lead_event = db.query(AuditEvent).filter(AuditEvent.org_id == "demo-therapeutics", AuditEvent.action == "lead_nominated").first()
    assert lead_event is not None
    assert lead_event.actor == "dr_john_doe"
    assert "AC-007 nominated as lead candidate" in lead_event.details["message"]
    db.close()


def test_issue_and_list_cro_upload_credential():
    r = client.post(
        f"/api/v1/orgs/{ORG}/credentials",
        json={
            "credential_type": "cro_upload",
            "campaign_id": _CAMPAIGN_ID,
            "label": "Round 3 CRO",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["credential_type"] == "cro_upload"
    assert body["token"].startswith("cro_")
    assert body["label"] == "Round 3 CRO"

    global _CRO_CREDENTIAL_ID, _CRO_TOKEN
    _CRO_CREDENTIAL_ID = body["id"]
    _CRO_TOKEN = body["token"]

    r = client.get(f"/api/v1/orgs/{ORG}/credentials")
    assert r.status_code == 200, r.text
    listed = r.json()
    assert any(item["id"] == _CRO_CREDENTIAL_ID for item in listed)
    assert all("credential_value" not in item and "token" not in item for item in listed)


def test_create_and_list_docking_grid():
    r = client.post(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/grids",
        params={"org_id": ORG},
        json={
            "campaign_id": _CAMPAIGN_ID,
            "name": "active_site_tight",
            "receptor_pdb_s3_key": "data/acme/egfr_receptor.pdb",
            "receptor_pdb_hash": "a" * 64,
            "software": "AutoDock Vina",
            "software_version": "1.2.5",
            "box_center_x": 10.0,
            "box_center_y": 11.0,
            "box_center_z": 12.0,
            "box_size_x": 20.0,
            "box_size_y": 20.0,
            "box_size_z": 20.0,
            "exhaustiveness": 16,
            "extra_params": {"num_modes": 9},
            "notes": "EGFR ATP pocket",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "active_site_tight"
    assert body["campaign_id"] == _CAMPAIGN_ID
    assert body["id"]

    global _GRID_ID
    _GRID_ID = body["id"]

    r = client.get(f"/api/v1/campaigns/{_CAMPAIGN_ID}/grids", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    grids = r.json()
    assert any(g["id"] == _GRID_ID for g in grids)


def test_get_docking_grid():
    r = client.get(f"/api/v1/grids/{_GRID_ID}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "active_site_tight"


def test_cro_upload_credential_scopes_run_ingest():
    manifest = {
        "campaign_id": _CAMPAIGN_ID + 999,
        "filename": "bad_scope.log",
        "file_hash": "b" * 64,
        "parsed": {"run_kind": "docking", "termination_status": "normal", "metrics": []},
    }
    r = client.post(
        "/api/v1/runs/ingest",
        json=manifest,
        headers={"Authorization": f"Bearer {_CRO_TOKEN}"},
    )
    assert r.status_code == 403

    manifest["campaign_id"] = _CAMPAIGN_ID
    manifest["filename"] = "cro_upload.log"
    manifest["inferred_from_path"] = True
    manifest["inferred_context"] = {"campaign_name": "lead_opt_round_3", "run_type": "vina"}
    r = client.post(
        "/api/v1/runs/ingest",
        json=manifest,
        headers={"Authorization": f"Bearer {_CRO_TOKEN}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["campaign_id"] == _CAMPAIGN_ID
    global _CRO_RUN_ID
    _CRO_RUN_ID = body["run_id"]

    r = client.get(f"/api/v1/runs/{_CRO_RUN_ID}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    assert r.json()["was_inferred"] is True


def test_ingest_run_persists_run_metrics_molecule_and_qc():
    manifest = {
        "org_id": ORG,
        "project": "EGFR-program-2026",
        "campaign": "lead_opt_round_3",
        "molecule_smiles": "Cc1ccc(cc1)C(=O)Nc2ccncc2",
        "molecule_name": "LL-042",
        "filename": "dock_LL042_out.pdbqt",
        "s3_key": "data/acme/dock_LL042_out.pdbqt",
        "file_size_bytes": 612,
        "file_hash": "abcdef" * 10 + "1234",  # 64 chars
        "grid_id": _GRID_ID,
        "parser_name": "autodock_vina",
        "artifact_role": "metric_source",
        "parsed": {
            "software_name": "AutoDock Vina",
            "software_version": "1.2.5",
            "run_kind": "docking",
            "termination_status": "normal",
            "method": "Vina",
            "metrics": [
                {"name": "docking_score_top", "value": -9.2, "unit": "kcal/mol"},
                {"name": "best_binding_affinity", "value": -9.2, "unit": "kcal/mol"},
                {"name": "pose_affinity_rank_1", "value": -9.2, "unit": "kcal/mol",
                 "metadata": {"rank": 1, "rmsd_lb_A": 0.0}},
                {"name": "pose_affinity_rank_2", "value": -8.7, "unit": "kcal/mol",
                 "metadata": {"rank": 2}},
                {"name": "pose_affinity_rank_3", "value": -8.3, "unit": "kcal/mol",
                 "metadata": {"rank": 3}},
            ],
            "metadata": {"n_poses": 3},
        },
        "client_qc": {"overall_status": "warn"},
    }
    r = client.post("/api/v1/runs/ingest", json=manifest)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] > 0
    assert body["campaign_id"] == _CAMPAIGN_ID
    assert body["molecule_id"] is not None
    assert body["molecule_created"] is True
    assert body["metrics_count"] == 5
    assert body["qc"] is not None
    assert body["qc"]["overall_status"] in ("pass", "warn", "fail")

    global _RUN_ID, _MOLECULE_ID
    _RUN_ID = body["run_id"]
    _MOLECULE_ID = body["molecule_id"]


def test_ingest_run_dedups_molecule_on_repeat():
    """Second ingest of the same SMILES should reuse the molecule."""
    manifest = {
        "org_id": ORG,
        "project": "EGFR-program-2026",
        "campaign": "lead_opt_round_3",
        "molecule_smiles": "Cc1ccc(cc1)C(=O)Nc2ccncc2",  # same as before
        "filename": "dock_LL042_rerun.pdbqt",
        "s3_key": "data/acme/dock_LL042_rerun.pdbqt",
        "file_size_bytes": 700,
        "file_hash": "f" * 64,
        "parser_name": "autodock_vina",
        "artifact_role": "metric_source",
        "parsed": {
            "software_name": "AutoDock Vina",
            "software_version": "1.2.5",
            "run_kind": "docking",
            "termination_status": "normal",
            "metrics": [
                {"name": "best_binding_affinity", "value": -9.5, "unit": "kcal/mol"},
                {"name": "pose_affinity_rank_1", "value": -9.5, "unit": "kcal/mol"},
                {"name": "pose_affinity_rank_2", "value": -9.0, "unit": "kcal/mol"},
                {"name": "pose_affinity_rank_3", "value": -8.8, "unit": "kcal/mol"},
            ],
        },
    }
    r = client.post("/api/v1/runs/ingest", json=manifest)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["molecule_id"] == _MOLECULE_ID
    assert body["molecule_created"] is False


def test_get_campaign_counts_update_after_ingest():
    r = client.get(f"/api/v1/campaigns/{_CAMPAIGN_ID}", params={"org_id": ORG})
    body = r.json()
    assert body["run_count"] >= 2
    assert body["molecule_count"] == 1


def test_list_campaign_molecules_includes_top_metrics():
    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/molecules",
        params={"org_id": ORG},
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    item = items[0]
    assert item["id"] == _MOLECULE_ID
    assert item["run_count"] >= 2
    # Best best_binding_affinity should be the lower (more negative) of the two
    best = next((m for m in item["top_metrics"]
                 if m["metric_name"] == "best_binding_affinity"), None)
    assert best is not None
    assert best["best_value"] == -9.5
    assert best["unit"] == "kcal/mol"
    assert best["run_id"] > 0


def test_list_campaign_molecules_include_metrics_shape():
    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/molecules",
        params={"org_id": ORG, "include_metrics": "true"},
    )
    assert r.status_code == 200, r.text
    item = r.json()[0]
    assert item["smiles"] == item["canonical_smiles"]
    assert item["qc_status"] in ("pass", "warn", "fail", None)
    assert item["metrics"]["docking_score_top"] == -9.2
    assert item["metrics"]["best_binding_affinity"] == -9.5


def test_get_molecule_detail_has_all_runs():
    r = client.get(f"/api/v1/molecules/{_MOLECULE_ID}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == _MOLECULE_ID
    assert body["canonical_smiles"]
    assert len(body["runs"]) >= 2
    # Each run summary carries metric_count
    assert all(r["metric_count"] >= 1 for r in body["runs"])
    # Assay results: should have entries for both runs' metrics
    assert len(body["assay_results"]) >= 2


def test_get_run_detail_includes_qc_and_audit():
    r = client.get(f"/api/v1/runs/{_RUN_ID}", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == _RUN_ID
    assert body["software_name"] == "AutoDock Vina"
    assert len(body["metrics"]) == 5
    assert body["grid_id"] == _GRID_ID
    assert body["qc"] is not None  # server_qc must be persisted
    assert isinstance(body["audit_events"], list)
    assert len(body["audit_events"]) >= 1
    # Hash-chain field is present
    assert all(len(a["record_hash"]) == 64 for a in body["audit_events"])


def test_list_grid_runs_includes_top_docking_score():
    r = client.get(f"/api/v1/grids/{_GRID_ID}/runs", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    runs = r.json()
    assert any(run["id"] == _RUN_ID and run["top_docking_score"] == -9.2 for run in runs)


def test_patch_run_grid_associates_grid():
    r = client.patch(
        f"/api/v1/runs/{_RUN_ID}/grid",
        params={"org_id": ORG},
        json={"grid_id": _GRID_ID},
    )
    assert r.status_code == 200, r.text
    assert r.json()["grid_id"] == _GRID_ID


def test_campaign_methods_json_and_text():
    r = client.get(f"/api/v1/campaigns/{_CAMPAIGN_ID}/methods", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["campaign_id"] == str(_CAMPAIGN_ID)
    assert body["campaign_name"] == "lead_opt_round_3"
    assert "docking" in body["paragraphs"]
    assert "Molecular docking was performed using AutoDock Vina 1.2.5" in body["full_text"]
    assert "(10, 11, 12) Å" in body["full_text"]
    assert "20 × 20 × 20 Å" in body["full_text"]
    assert body["run_counts"]["docking"] >= 1
    assert body["software_versions"]["AutoDock Vina"] == ["1.2.5"]
    assert "scoring_function" in body["missing_fields"]

    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/methods",
        params={"org_id": ORG, "format": "text"},
    )
    assert r.status_code == 200, r.text
    assert "text/plain" in r.headers.get("content-type", "")
    assert "Molecular docking was performed" in r.text


def test_campaign_config_template_download():
    r = client.get(f"/api/v1/campaigns/{_CAMPAIGN_ID}/config-template", params={"org_id": ORG})
    assert r.status_code == 200, r.text
    assert "application/x-yaml" in r.headers.get("content-type", "")
    assert 'attachment; filename="lablink_campaign.yaml"' in r.headers.get("content-disposition", "")
    assert "# Generated for: lead_opt_round_3" in r.text
    assert f'campaign_id: "{_CAMPAIGN_ID}"' in r.text
    assert 'org_token: "PASTE_YOUR_TOKEN_HERE"' in r.text


def test_export_campaign_csv_returns_flat_rows():
    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/export",
        params={"org_id": ORG, "format": "csv"},
    )
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers.get("content-type", "")
    body = r.text
    lines = body.strip().split("\n")
    # Header + at least 2 metric rows (one per run, best_binding_affinity = 1 row each)
    assert lines[0].startswith("assay_id,metric_name,value,unit")
    assert len(lines) >= 3
    # Confirm canonical SMILES is in the output
    assert "Cc1ccc" in body or "Nc2ccncc2" in body


def test_export_campaign_unknown_format_400():
    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/export",
        params={"org_id": ORG, "format": "xml"},
    )
    assert r.status_code == 400


def test_verify_audit_chain_returns_pass():
    r = client.post(
        f"/api/v1/audit/verify/{_CAMPAIGN_ID}",
        params={"org_id": ORG},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("pass", "fail")
    assert body["valid"] is True
    assert body["record_count"] >= 4  # project, campaign, molecule, runs…
    assert body["errors"] == []


def test_verify_audit_chain_detects_tamper():
    """Mutate one audit row and confirm verification flips to fail."""
    db = database.SessionLocal()
    from compchem_models import AuditEvent
    target = db.query(AuditEvent).filter(AuditEvent.org_id == ORG).first()
    assert target is not None
    # Capture values before mutating, while still bound to the session
    target_id = target.id
    original_actor = target.actor
    target.actor = "TAMPERED"
    db.commit()
    db.close()

    r = client.post(
        f"/api/v1/audit/verify/{_CAMPAIGN_ID}",
        params={"org_id": ORG},
    )
    body = r.json()
    assert body["valid"] is False
    assert body["status"] == "fail"
    assert len(body["errors"]) >= 1

    # Restore so subsequent tests aren't poisoned
    db = database.SessionLocal()
    t = db.query(AuditEvent).filter(AuditEvent.id == target_id).first()
    t.actor = original_actor
    db.commit()
    db.close()


def test_revoke_cro_upload_credential_hides_from_active_list():
    r = client.delete(f"/api/v1/credentials/{_CRO_CREDENTIAL_ID}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked"

    r = client.get(f"/api/v1/orgs/{ORG}/credentials")
    assert r.status_code == 200, r.text
    assert all(item["id"] != _CRO_CREDENTIAL_ID for item in r.json())


# --- Cross-org isolation --------------------------------------------------

def test_export_bco_has_required_domains_and_valid_etag():
    """
    Validate the IEEE 2791-2020 BCO export:
      - HTTP 200 + application/json
      - all 10 required top-level fields present
      - etag matches SHA-256 of the body with etag zeroed
      - download=true sets Content-Disposition
    """
    import hashlib
    import json as _json

    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/export/bco",
        params={"org_id": ORG},
    )
    assert r.status_code == 200, r.text
    assert "application/json" in r.headers.get("content-type", "")
    bco = r.json()

    required = {
        "object_id", "spec_version", "etag",
        "provenance_domain", "usability_domain", "description_domain",
        "execution_domain", "io_domain", "parametric_domain", "error_domain",
    }
    missing = required - set(bco.keys())
    assert not missing, f"BCO missing required fields: {missing}"

    # Spot-check spec_version and object_id shape
    assert bco["spec_version"] == "https://w3id.org/ieee/ieee-2791-schema/"
    assert bco["object_id"] == f"lablink/{_CAMPAIGN_ID}/bco/v1"

    # Provenance has the expected sub-fields
    prov = bco["provenance_domain"]
    for key in ("name", "version", "created", "modified", "contributors",
                "license", "embargo", "review"):
        assert key in prov, f"provenance_domain missing {key}"
    assert prov["license"] == "restricted"

    # Recompute etag exactly as the server does: zero the field, canonical JSON
    etag_received = bco["etag"]
    body_for_hash = dict(bco)
    body_for_hash["etag"] = ""
    canonical = _json.dumps(body_for_hash, sort_keys=True, separators=(",", ":"), default=str)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert etag_received == expected, (
        f"etag mismatch: got {etag_received!r}, expected {expected!r}"
    )

    # error_domain is the two empty buckets we specified
    assert bco["error_domain"] == {"empirical_error": {}, "algorithmic_error": {}}

    # download=true should set Content-Disposition
    r2 = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/export/bco",
        params={"org_id": ORG, "download": "true"},
    )
    assert r2.status_code == 200
    cd = r2.headers.get("content-disposition", "")
    assert "attachment" in cd and "BCO.json" in cd


def test_export_evidence_book_zip_contents_and_checksums():
    """
    Validate the Evidence Book ZIP export:
      - HTTP 200, application/zip, attachment filename
      - contains every required member (PDF, CSVs, BCO, verification.json)
      - every file_checksums entry in verification.json matches a fresh
        SHA-256 of that zip member
      - audit_log.csv hash inside the PDF matches the recomputed hash
      - audit_chain_status is "verified"
    """
    import hashlib
    import io as _io
    import json as _json
    import zipfile as _zip

    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}/export/evidence-book",
        params={"org_id": ORG},
    )
    assert r.status_code == 200, r.text
    assert "application/zip" in r.headers.get("content-type", "")
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and "EvidenceBook_" in cd and cd.endswith('.zip"')
    assert r.headers.get("x-evidence-book-sha256")

    zf = _zip.ZipFile(_io.BytesIO(r.content))
    names = set(zf.namelist())

    required = {
        "summary.pdf",
        "runs.csv",
        "molecules.csv",
        "metrics.csv",
        "audit_log.csv",
        "campaign_bco.json",
        "verification.json",
    }
    missing = required - names
    assert not missing, f"Evidence book missing files: {missing}; got {names}"

    verification = _json.loads(zf.read("verification.json"))
    assert verification["schema"] == "lablink.evidence-book.verification/v1"
    assert verification["campaign_id"] == _CAMPAIGN_ID
    assert verification["audit_chain_status"] == "verified", verification

    # Recompute every checksum and compare
    for name, entry in verification["file_checksums"].items():
        assert name in names, f"verification.json references missing {name}"
        recomputed = hashlib.sha256(zf.read(name)).hexdigest()
        assert recomputed == entry["sha256"], (
            f"checksum mismatch for {name}: "
            f"recomputed={recomputed}, claimed={entry['sha256']}"
        )

    # verification.json should NOT list itself in file_checksums
    assert "verification.json" not in verification["file_checksums"]

    # The PDF must mention the audit_log.csv SHA-256 (PDF stores text as
    # latin1; SHA-256 hex is ASCII so substring check on raw bytes works.)
    audit_sha = verification["file_checksums"]["audit_log.csv"]["sha256"]
    pdf_bytes = zf.read("summary.pdf")
    assert audit_sha.encode("ascii") in pdf_bytes, (
        f"audit_log.csv SHA-256 {audit_sha} not found inside summary.pdf"
    )

    # BCO is the same dict the BCO endpoint returns; spot-check spec_version
    bco = _json.loads(zf.read("campaign_bco.json"))
    assert bco["spec_version"] == "https://w3id.org/ieee/ieee-2791-schema/"


def test_other_org_cannot_see_campaign():
    r = client.get(
        f"/api/v1/campaigns/{_CAMPAIGN_ID}",
        params={"org_id": "other-org"},
    )
    # AUTH_REQUIRED is false in tests, so require_org_access passes silently;
    # the campaign filter on org_id is what enforces isolation
    assert r.status_code == 404


# --- Runner ---------------------------------------------------------------

_CAMPAIGN_ID = -1
_RUN_ID = -1
_MOLECULE_ID = -1
_GRID_ID = ""
_CRO_CREDENTIAL_ID = ""
_CRO_TOKEN = ""
_CRO_RUN_ID = -1


def _run_all():
    fns_in_order = [
        test_create_campaign_creates_project_and_campaign,
        test_create_campaign_duplicate_returns_409,
        test_get_campaign_includes_counts,
        test_get_campaign_404_for_unknown,
        test_demo_login_and_reset_seed_demo_org,
        test_issue_and_list_cro_upload_credential,
        test_create_and_list_docking_grid,
        test_get_docking_grid,
        test_cro_upload_credential_scopes_run_ingest,
        test_ingest_run_persists_run_metrics_molecule_and_qc,
        test_ingest_run_dedups_molecule_on_repeat,
        test_get_campaign_counts_update_after_ingest,
        test_list_campaign_molecules_includes_top_metrics,
        test_list_campaign_molecules_include_metrics_shape,
        test_get_molecule_detail_has_all_runs,
        test_get_run_detail_includes_qc_and_audit,
        test_list_grid_runs_includes_top_docking_score,
        test_patch_run_grid_associates_grid,
        test_campaign_methods_json_and_text,
        test_campaign_config_template_download,
        test_export_campaign_csv_returns_flat_rows,
        test_export_campaign_unknown_format_400,
        test_verify_audit_chain_returns_pass,
        test_verify_audit_chain_detects_tamper,
        test_export_bco_has_required_domains_and_valid_etag,
        test_export_evidence_book_zip_contents_and_checksums,
        test_revoke_cro_upload_credential_hides_from_active_list,
        test_other_org_cannot_see_campaign,
    ]
    failed = 0
    for fn in fns_in_order:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns_in_order) - failed}/{len(fns_in_order)} passed")
    try:
        os.unlink(_TMPDB.name)
    except OSError:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
