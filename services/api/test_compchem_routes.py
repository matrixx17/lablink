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
from database import Base  # noqa: E402

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
# need it for these tests.
from compchem_models import (  # noqa: E402
    Project, Campaign, Run, RunInput, RunOutput, RunMetric, RunLineage,
    Molecule, MoleculeProperty, AssayResult, AuditEvent,
)
_cc_tables = [
    t.__table__ for t in (
        Project, Campaign, Molecule, Run, RunInput, RunOutput,
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
        "parser_name": "autodock_vina",
        "artifact_role": "metric_source",
        "parsed": {
            "software_name": "AutoDock Vina",
            "software_version": "1.2.5",
            "run_kind": "docking",
            "termination_status": "normal",
            "method": "Vina",
            "metrics": [
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
    assert body["metrics_count"] == 4
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
    assert len(body["metrics"]) == 4
    assert body["qc"] is not None  # server_qc must be persisted
    assert isinstance(body["audit_events"], list)
    assert len(body["audit_events"]) >= 1
    # Hash-chain field is present
    assert all(len(a["record_hash"]) == 64 for a in body["audit_events"])


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


# --- Cross-org isolation --------------------------------------------------

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


def _run_all():
    fns_in_order = [
        test_create_campaign_creates_project_and_campaign,
        test_create_campaign_duplicate_returns_409,
        test_get_campaign_includes_counts,
        test_get_campaign_404_for_unknown,
        test_ingest_run_persists_run_metrics_molecule_and_qc,
        test_ingest_run_dedups_molecule_on_repeat,
        test_get_campaign_counts_update_after_ingest,
        test_list_campaign_molecules_includes_top_metrics,
        test_get_molecule_detail_has_all_runs,
        test_get_run_detail_includes_qc_and_audit,
        test_export_campaign_csv_returns_flat_rows,
        test_export_campaign_unknown_format_400,
        test_verify_audit_chain_returns_pass,
        test_verify_audit_chain_detects_tamper,
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
