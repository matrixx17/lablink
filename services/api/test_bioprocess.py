"""Tests for bioprocess pivot: parsers, QC, alignment."""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers import parse_file, detect_format
from bioprocess_qc import bioprocess_qc_summary, check_vcd_growth_profile
from timeseries_align import align_run_series, extract_series_from_stats


FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures")


@pytest.fixture(autouse=True)
def _restore_global_test_state():
    """Keep this module's SQLite/env patches from leaking into other suites."""
    import database as db_module

    original_engine = db_module.engine
    original_session_local = db_module.SessionLocal
    original_demo_secret = os.environ.get("DEMO_RESET_SECRET")
    yield
    db_module.engine = original_engine
    db_module.SessionLocal = original_session_local
    if original_demo_secret is None:
        os.environ.pop("DEMO_RESET_SECRET", None)
    else:
        os.environ["DEMO_RESET_SECRET"] = original_demo_secret


def test_sartorius_biostat_detection():
    path = os.path.join(FIXTURES, "biostat_run.csv")
    assert detect_format(path) == "sartorius_biostat"


def test_biostat_parse_series_points():
    path = os.path.join(FIXTURES, "biostat_run.csv")
    result = parse_file(path)
    assert result.instrument == "sartorius_biostat"
    assert result.data_kind == "continuous"
    assert len(result.series_points) > 0
    assert "Viable cells" in result.headers or any("viable" in h.lower() for h in result.headers)


def test_offline_titer_parser():
    path = os.path.join(FIXTURES, "offline_titer.csv")
    assert detect_format(path) == "bioprocess_offline"
    result = parse_file(path)
    assert result.data_kind == "discrete_offline"


def test_bioprocess_qc_vcd_crash_rule():
    findings = check_vcd_growth_profile(
        [15, 12, 10, 2, 1, 0.5],
        [0, 12, 24, 36, 48, 60],
    )
    assert any(f["rule"] == "vcd_early_crash" for f in findings)


def test_timeseries_alignment():
    continuous = {
        "do": ([0, 12, 24, 48], [45, 40, 35, 30]),
    }
    discrete = {
        "titer": ([24, 48, 72], [0.1, 0.5, 1.0]),
    }
    aligned = align_run_series(continuous, discrete)
    assert "titer" in str(aligned.get("discrete_fields", []))
    assert len(aligned.get("aligned_fields", [])) >= 1


def test_extract_series_from_stats():
    stats = {
        "Time [h]": {"values": [0, 24, 48]},
        "Titer": {"values": [0.1, 0.5, 1.0]},
    }
    extracted = extract_series_from_stats(stats, time_field="Time [h]")
    assert "Titer" in extracted


def test_evidence_book_export_zip_and_checksums(tmp_path):
    """
    End-to-end Evidence Book build against an in-memory SQLite DB.

    Asserts: the returned zip contains every required file, every
    SHA-256 in verification.json matches a fresh hash of that zip member,
    the audit_log.csv SHA-256 is greppable inside summary.pdf,
    audit_chain_status is "verified", and provenance.json (NOT a BCO) is
    present.
    """
    import hashlib
    import io as _io
    import json as _json
    import uuid
    import zipfile as _zip
    from datetime import datetime, timedelta, timezone

    import json as _json
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    # Patch JSONB / ARRAY for SQLite. Mirrors test_compchem_routes.py pattern.
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import (
        Base, Campaign, Batch, OfflineSample, TimeseriesData,
        AuditLog, AuditAction, EntityType, compute_record_hash,
    )

    db_path = tmp_path / "evidence_book.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

    # Create only the wet lab tables (skip webhooks ARRAY edge case).
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples",
                      "timeseries_data", "users", "campaign_approvals",
                      "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)

    # Point evidence_book's session at our engine
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    org = "demo-therapeutics"

    campaign = Campaign(
        id=str(uuid.uuid4()), org_id=org,
        name="Test Campaign 4",
        description="Test bioprocess campaign for Evidence Book export",
        domain="wetlab",
        extra_params={"target": "Anti-HER2 mAb", "process_type": "CHO fed-batch"},
    )
    session.add(campaign)
    session.flush()

    inoculation = datetime(2024, 3, 18, 9, 0, tzinfo=timezone.utc)
    batch = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_TEST_A", bioreactor_model="Sartorius BIOSTAT B-DCU",
        volume_liters=2.0, cell_line="CHO-K1", media="ActiPro",
        inoculation_date=inoculation,
        harvest_date=inoculation + timedelta(days=14),
        status="harvested",
        extra_params={"condition_label": "baseline", "ph_setpoint": 7.0},
    )
    session.add(batch)
    session.flush()

    # One offline VCD sample, one titer sample, so peak_vcd and final_titer
    # both populate.
    for h, meas, val, unit in [
        (24.0, "viable_cell_density_e6_per_ml", 2.5, "1e6 cells/mL"),
        (240.0, "viable_cell_density_e6_per_ml", 8.0, "1e6 cells/mL"),
        (336.0, "titer_mg_per_l", 800.0, "mg/L"),
    ]:
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=inoculation + timedelta(hours=h),
            measurement_name=meas, value=val, unit=unit,
            instrument="Octet BLI" if "titer" in meas else "Beckman Vi-CELL",
            qc_status="pass",
        ))

    # NOTE: TimeseriesData uses Postgres ARRAY(Float) which SQLite cannot
    # bind directly. The Evidence Book exporter handles an empty timeseries
    # set gracefully — that's the case exercised here. The full timeseries
    # path is exercised against the real Postgres in dev/CI.

    # Audit log entry whose record_hash IS the canonical hash of its own
    # fields (otherwise verify will report mismatch).
    ts = datetime(2024, 4, 14, 10, 0, tzinfo=timezone.utc)
    rec_hash = compute_record_hash(
        timestamp=ts, org_id=org,
        action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG,
        entity_id=batch.id,
        actor="BioCalc Process Labs",
        details={"event": "cro_delivery", "campaign_id": campaign.id},
        previous_hash=None,
    )
    session.add(AuditLog(
        timestamp=ts, org_id=org,
        action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG,
        entity_id=batch.id,
        actor="BioCalc Process Labs",
        details={"event": "cro_delivery", "campaign_id": campaign.id},
        previous_hash=None,
        record_hash=rec_hash,
    ))
    session.commit()

    # SQLite's DateTime(timezone=True) strips tzinfo on round-trip, which
    # changes the canonical isoformat() the audit hasher uses. Re-hash the
    # row using the round-tripped timestamp so the chain verifies. (This is
    # a latent bug on the wet lab branch's compute_record_hash that doesn't
    # affect Postgres; track separately.)
    saved = session.query(AuditLog).first()
    saved.record_hash = compute_record_hash(
        timestamp=saved.timestamp,
        org_id=saved.org_id,
        action=saved.action,
        entity_type=saved.entity_type,
        entity_id=saved.entity_id,
        actor=saved.actor,
        details=saved.details,
        previous_hash=saved.previous_hash,
    )
    session.commit()

    # ---- Exercise the export ----
    from evidence_book_wetlab import build_evidence_book
    zip_bytes, zip_sha = build_evidence_book(session, campaign, org)
    session.close()

    assert isinstance(zip_bytes, bytes) and len(zip_bytes) > 100
    assert zip_sha == hashlib.sha256(zip_bytes).hexdigest()

    zf = _zip.ZipFile(_io.BytesIO(zip_bytes))
    names = set(zf.namelist())
    required = {
        "summary.pdf", "batches.csv", "offline_samples.csv",
        "timeseries_summary.csv", "audit_log.csv",
        "provenance.json", "verification.json",
    }
    assert required.issubset(names), f"missing: {required - names}"

    verification = _json.loads(zf.read("verification.json"))
    assert verification["schema"] == "lablink.evidence-book.verification/v1"
    assert verification["campaign_id"] == campaign.id
    assert verification["audit_chain_status"] == "verified", verification
    assert verification["approvals"] == []
    assert verification["is_approved"] is False

    for name, entry in verification["file_checksums"].items():
        recomputed = hashlib.sha256(zf.read(name)).hexdigest()
        assert recomputed == entry["sha256"], f"{name} hash drift"

    audit_sha = verification["file_checksums"]["audit_log.csv"]["sha256"]
    pdf_bytes = zf.read("summary.pdf")
    assert audit_sha.encode("ascii") in pdf_bytes, \
        f"audit_log SHA-256 {audit_sha} not found inside summary.pdf"

    provenance = _json.loads(zf.read("provenance.json"))
    assert provenance["schema"] == "lablink.wetlab.provenance/v1"
    assert provenance["campaign"]["id"] == campaign.id
    assert len(provenance["batches"]) == 1
    assert "BioCalc Process Labs" in provenance["contributors"]

    # campaign_bco.json must NOT be present on wet lab branch
    assert "campaign_bco.json" not in names


def test_bioprocess_qc_engine_detects_broken_batch(tmp_path):
    """
    Build an in-memory SQLite batch with deliberately-bad data and assert
    the BioprocessQCEngine returns the expected QCResult statuses:

      - pH series stuck 0.6 below setpoint for 45 min  -> ph_excursion FAIL
      - DO crashes to 5% for 10 min                    -> do_crash FAIL
      - VCD never doubles inoculation density          -> vcd_growth_curve_shape FAIL
      - Glucose drops to 0 mid-run                     -> glucose_depletion FAIL

    And overall rollup -> batch.extra_params['qc_status'] == 'fail'.

    Inserts TimeseriesData via raw SQL to dodge SQLite's inability to bind
    Python lists to ARRAY(Float) columns.
    """
    import json as _json
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch, OfflineSample
    from bioprocess_qc import BioprocessQCEngine

    db_path = tmp_path / "qc_engine.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples",
                      "timeseries_data", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    org = "demo-therapeutics"
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id=org, name="Broken Campaign",
        domain="wetlab", extra_params={},
    )
    session.add(campaign)
    session.flush()

    inoculation = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    batch = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_BROKEN", bioreactor_model="Sartorius BIOSTAT",
        volume_liters=2.0, cell_line="CHO", media="ActiPro",
        inoculation_date=inoculation,
        harvest_date=inoculation + timedelta(days=14),
        status="harvested",
        extra_params={"condition_label": "broken", "ph_setpoint": 7.0},
    )
    session.add(batch)
    session.flush()

    # ---- TimeseriesData (raw insert to bypass ARRAY binding) -------------
    # 14 days × 5 minute cadence = 4032 points. Keep it small: 2-hour cadence.
    step_seconds = 2 * 3600
    n_points = (14 * 24 // 2) + 1
    t_unix = [inoculation.timestamp() + i * step_seconds for i in range(n_points)]

    # pH: setpoint 7.0; for hours 40-90 hold pH at 6.4 (-0.6 from setpoint)
    ph_vals = []
    for i in range(n_points):
        hour = i * 2
        if 40 <= hour <= 90:
            ph_vals.append(6.4)
        else:
            ph_vals.append(7.00 + (0.01 if i % 2 == 0 else -0.01))
    # DO: mostly 40%, but hours 70-72 drop to 5% (3-point window > 5 min)
    do_vals = []
    for i in range(n_points):
        hour = i * 2
        do_vals.append(5.0 if 70 <= hour <= 74 else 40.0)

    insert_sql = text("""
        INSERT INTO timeseries_data
            (id, batch_id, parameter_name, unit, timestamps, "values",
             source_instrument, created_at)
        VALUES
            (:id, :batch_id, :param, :unit, :timestamps, :values,
             :source, :created_at)
    """)
    now_iso = datetime.now(timezone.utc).isoformat()
    for param, unit, vals in (
        ("ph", "pH", ph_vals),
        ("do_percent", "%", do_vals),
    ):
        session.execute(insert_sql, {
            "id": str(uuid.uuid4()),
            "batch_id": batch.id,
            "param": param,
            "unit": unit,
            "timestamps": _json.dumps(t_unix),
            "values": _json.dumps(vals),
            "source": "Sartorius BIOSTAT",
            "created_at": now_iso,
        })

    # ---- Offline samples: VCD never doubles inoculum + glucose depletion -
    inoculum = 2.0  # 2e6 cells/mL
    for day in range(1, 15):
        h = float(day * 24)
        ts_abs = inoculation + timedelta(hours=h)
        # VCD never exceeds 1.5x inoculum -> failed culture
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=ts_abs,
            measurement_name="vcd_e6_per_ml",
            value=inoculum * (1.0 + 0.05 * day), unit="1e6 cells/mL",
            instrument="Vi-CELL", qc_status="pass",
        ))
        # Glucose drops to 0 at day 6 (mid-run)
        glc = max(0.0, 5.0 - day)
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=ts_abs,
            measurement_name="glucose_g_per_l",
            value=glc, unit="g/L",
            instrument="Nova BioProfile", qc_status="pass",
        ))
    session.commit()

    # ---- Run engine ------------------------------------------------------
    results = BioprocessQCEngine.run_for_batch(session, batch.id)
    results_by_name = {r.check_name: r for r in results}

    # Spot-check the failures we set up
    assert "ph_excursion" in results_by_name, results_by_name
    assert results_by_name["ph_excursion"].status == "fail", \
        results_by_name["ph_excursion"]
    assert results_by_name["do_crash"].status == "fail", \
        results_by_name["do_crash"]
    assert results_by_name["vcd_growth_curve_shape"].status == "fail", \
        results_by_name["vcd_growth_curve_shape"]
    assert results_by_name["glucose_depletion"].status == "fail", \
        results_by_name["glucose_depletion"]

    # Overall rollup persisted
    session.refresh(batch)
    assert (batch.extra_params or {}).get("qc_status") == "fail"
    assert isinstance((batch.extra_params or {}).get("qc_results"), list)


def test_generate_wetlab_methods(tmp_path):
    """Methods generator emits shared-schema wet lab paragraphs and
    lists missing-fields when something can't be inferred."""
    import json as _json
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch, OfflineSample
    from bioprocess_methods import generate_wetlab_methods

    db_path = tmp_path / "methods.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples",
                      "timeseries_data", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    s = SessionLocal()
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id="demo-therapeutics",
        name="Methods Test Campaign", domain="wetlab",
        extra_params={"target": "Anti-HER2 mAb"},
    )
    s.add(campaign); s.flush()

    inoc = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    for n in ("Batch_M_001", "Batch_M_002"):
        b = Batch(
            id=str(uuid.uuid4()), campaign_id=campaign.id,
            batch_number=n, bioreactor_model="Sartorius BIOSTAT B-DCU",
            volume_liters=2.0, cell_line="CHO-K1", media="ActiPro",
            inoculation_date=inoc,
            harvest_date=inoc + timedelta(days=14),
            status="harvested",
            extra_params={"ph_setpoint": 7.0},
        )
        s.add(b); s.flush()
        s.execute(
            text("""
                INSERT INTO timeseries_data
                    (id, batch_id, parameter_name, unit, timestamps, "values",
                     source_instrument, created_at)
                VALUES
                    (:id, :batch_id, :param, :unit, :timestamps, :values,
                     :source, :created_at)
            """),
            {
                "id": str(uuid.uuid4()), "batch_id": b.id,
                "param": "ph", "unit": "pH",
                "timestamps": _json.dumps([0.0, 3600.0]),
                "values": _json.dumps([7.0, 7.02]),
                "source": "Sartorius BIOSTAT B-DCU",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        # one VCD + one titer + one metabolite sample per batch
        s.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=b.id,
            sample_time_hours=72.0,
            sample_time_absolute=inoc + timedelta(hours=72),
            measurement_name="vcd_e6_per_ml", value=12.0,
            unit="1e6 cells/mL", instrument="Beckman Vi-CELL XR",
            qc_status="pass",
        ))
        s.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=b.id,
            sample_time_hours=336.0,
            sample_time_absolute=inoc + timedelta(hours=336),
            measurement_name="titer_mg_per_l", value=2000.0,
            unit="mg/L", instrument="Octet BLI", qc_status="pass",
        ))
        s.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=b.id,
            sample_time_hours=72.0,
            sample_time_absolute=inoc + timedelta(hours=72),
            measurement_name="glucose_g_per_l", value=3.2,
            unit="g/L", instrument="Nova BioProfile FLEX2", qc_status="pass",
        ))
    s.commit()

    result = generate_wetlab_methods(s, campaign)
    assert result["domain"] == "wetlab"
    paragraphs = result["paragraphs"]
    assert "Sartorius BIOSTAT B-DCU" in paragraphs["bioreactor"]
    assert "CHO-K1" in paragraphs["bioreactor"]
    assert "2 L" in paragraphs["bioreactor"]
    assert "Beckman Vi-CELL XR" in paragraphs["cell_analysis"]
    assert "Octet BLI" in paragraphs["titer"]
    assert "Nova BioProfile FLEX2" in paragraphs["metabolites"]
    assert paragraphs["chromatography"] == ""  # no ÄKTA data
    assert set(paragraphs) == {"bioreactor", "cell_analysis", "titer", "metabolites", "chromatography"}
    assert result["run_counts"]["batches"] == 2
    assert result["run_counts"]["total_offline_samples"] == 6
    assert result["run_counts"]["continuous_timepoints"] == 4
    assert result["software_versions"]["Bioreactor"] == ["Sartorius BIOSTAT B-DCU"]
    assert result["software_versions"]["Cell analysis"] == ["Beckman Vi-CELL XR"]
    # Temperature setpoint not in timeseries — should be marked missing
    assert "temperature_setpoint" in result["missing_fields"]
    # Full text concatenates non-empty paragraphs with a blank-line separator
    assert "\n\n" in result["full_text"]
    assert "Sartorius" in result["full_text"]
    assert "Octet BLI" in result["full_text"]


def test_bioprocess_qc_engine_passes_clean_batch(tmp_path):
    """A well-behaved batch should produce overall qc_status == 'pass'."""
    import json as _json
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch, OfflineSample
    from bioprocess_qc import BioprocessQCEngine

    db_path = tmp_path / "qc_engine_clean.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples",
                      "timeseries_data", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id="demo-therapeutics",
        name="Clean Campaign", domain="wetlab", extra_params={},
    )
    session.add(campaign)
    session.flush()

    inoculation = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    batch = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_CLEAN", bioreactor_model="Sartorius BIOSTAT",
        inoculation_date=inoculation,
        harvest_date=inoculation + timedelta(days=14),
        status="harvested", extra_params={"ph_setpoint": 7.0},
    )
    session.add(batch)
    session.flush()

    step_seconds = 2 * 3600
    n_points = (14 * 24 // 2) + 1
    t_unix = [inoculation.timestamp() + i * step_seconds for i in range(n_points)]

    ph_vals = [7.00 + (0.02 if i % 3 == 0 else -0.01) for i in range(n_points)]
    do_vals = [40.0 + (1.5 if i % 4 == 0 else -0.5) for i in range(n_points)]
    temp_vals = [37.0 + (0.05 if i % 5 == 0 else -0.05) for i in range(n_points)]

    insert_sql = text("""
        INSERT INTO timeseries_data
            (id, batch_id, parameter_name, unit, timestamps, "values",
             source_instrument, created_at)
        VALUES
            (:id, :batch_id, :param, :unit, :timestamps, :values, :source, :created_at)
    """)
    now_iso = datetime.now(timezone.utc).isoformat()
    for param, unit, vals in (
        ("ph", "pH", ph_vals),
        ("do_percent", "%", do_vals),
        ("temperature_c", "°C", temp_vals),
    ):
        session.execute(insert_sql, {
            "id": str(uuid.uuid4()), "batch_id": batch.id, "param": param,
            "unit": unit, "timestamps": _json.dumps(t_unix),
            "values": _json.dumps(vals),
            "source": "Sartorius BIOSTAT", "created_at": now_iso,
        })

    # Healthy growth: sigmoid 2e6 → 18e6
    for day in range(1, 15):
        h = float(day * 24)
        # Logistic
        vcd = 2.0 + 16.0 / (1.0 + 2.718281828 ** (-(day - 7) * 0.8))
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=inoculation + timedelta(hours=h),
            measurement_name="vcd_e6_per_ml", value=float(vcd),
            unit="1e6 cells/mL", instrument="Vi-CELL", qc_status="pass",
        ))
        # Viability above 70% until day 13
        via = 98.0 if day < 13 else 75.0
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=inoculation + timedelta(hours=h),
            measurement_name="viability_percent", value=via,
            unit="%", instrument="Vi-CELL", qc_status="pass",
        ))
        # Titer monotonically increasing
        titer = 200.0 * day
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=h, sample_time_absolute=inoculation + timedelta(hours=h),
            measurement_name="titer_mg_per_l", value=titer,
            unit="mg/L", instrument="Octet BLI", qc_status="pass",
        ))
    session.commit()

    results = BioprocessQCEngine.run_for_batch(session, batch.id)
    statuses = [r.status for r in results]
    assert "fail" not in statuses, [
        (r.check_name, r.status, r.message) for r in results if r.status != "pass"
    ]
    session.refresh(batch)
    overall = (batch.extra_params or {}).get("qc_status")
    assert overall in ("pass", "warn"), overall


def test_methods_chromatography_uses_series_metadata(tmp_path):
    """Chromatography paragraph reads ÄKTA metadata persisted on TimeseriesData."""
    import json as _json
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch
    from bioprocess_methods import generate_methods

    engine = create_engine(f"sqlite:///{tmp_path / 'chrom.db'}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples", "timeseries_data",
                      "users", "campaign_approvals", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    s = SessionLocal()
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id="demo-therapeutics",
        name="Chrom Campaign", domain="wetlab", extra_params={},
    )
    s.add(campaign)
    s.flush()
    batch = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_CHROM", bioreactor_model="ÄKTA pure 25",
        volume_liters=1.0, cell_line="CHO-K1", media="ActiPro",
        inoculation_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
        harvest_date=datetime(2024, 3, 15, tzinfo=timezone.utc),
        status="complete", extra_params={},
    )
    s.add(batch)
    s.flush()
    meta = {
        "x_axis": "ml",
        "method": "Protein A affinity",
        "column": "HiTrap Protein A HP 1 mL",
        "akta_model": "pure 25",
        "peaks": [
            {"retention_volume_ml": 35.2, "peak_area_mau_ml": 450},
            {"retention_volume_ml": 74.8, "peak_area_mau_ml": 85},
        ],
    }
    s.execute(
        text("""
            INSERT INTO timeseries_data
                (id, batch_id, parameter_name, unit, timestamps, "values",
                 source_instrument, metadata, created_at)
            VALUES
                (:id, :batch_id, :param, :unit, :timestamps, :values,
                 :source, :metadata, :created_at)
        """),
        {
            "id": str(uuid.uuid4()), "batch_id": batch.id,
            "param": "uv_absorbance_mau", "unit": "mAU",
            "timestamps": _json.dumps([10.0, 35.0, 75.0]),
            "values": _json.dumps([5.0, 98.0, 42.0]),
            "source": "Cytiva ÄKTA pure 25",
            "metadata": _json.dumps(meta),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    s.commit()

    result = generate_methods(s, campaign)
    chrom = result["paragraphs"]["chromatography"]
    assert "Protein A affinity" in chrom
    assert "HiTrap Protein A HP 1 mL" in chrom
    assert "pure 25" in chrom
    assert "2 chromatographic peaks" in chrom
    assert "akta_model" not in result["missing_fields"]


def test_batch_record_export_zip(tmp_path):
    """batch-record format returns expected zip members and checksums."""
    import hashlib
    import io as _io
    import json as _json
    import uuid
    import zipfile as _zip
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch, OfflineSample, compute_record_hash, AuditLog, AuditAction, EntityType
    from evidence_book_wetlab import build_batch_record, build_evidence_book

    engine = create_engine(f"sqlite:///{tmp_path / 'batch_record.db'}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples", "timeseries_data", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    org = "demo-therapeutics"
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id=org, name="Batch Record Test",
        domain="wetlab", extra_params={"target": "mAb"},
    )
    session.add(campaign)
    session.flush()
    inoc = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    batch = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_BR_001", bioreactor_model="Sartorius BIOSTAT",
        volume_liters=2.0, cell_line="CHO-K1", media="ActiPro",
        inoculation_date=inoc, harvest_date=inoc + timedelta(days=14),
        status="harvested",
        extra_params={"lead_condition": True, "qc_status": "pass", "feed_strategy": "adaptive"},
    )
    session.add(batch)
    session.flush()
    session.add(OfflineSample(
        id=str(uuid.uuid4()), batch_id=batch.id, sample_time_hours=336.0,
        sample_time_absolute=inoc + timedelta(hours=336),
        measurement_name="titer_mg_per_l", value=2000.0, unit="mg/L",
        instrument="Octet BLI", qc_status="pass",
    ))
    ts = datetime(2024, 4, 1, 10, 0, tzinfo=timezone.utc)
    rec_hash = compute_record_hash(
        timestamp=ts, org_id=org, action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG, entity_id=batch.id,
        actor="tester", details={"event": "cro_delivery", "campaign_id": campaign.id},
        previous_hash=None,
    )
    session.add(AuditLog(
        timestamp=ts, org_id=org, action=AuditAction.CONFIG_CHANGED,
        entity_type=EntityType.CONFIG, entity_id=batch.id, actor="tester",
        details={"event": "cro_delivery", "campaign_id": campaign.id},
        previous_hash=None, record_hash=rec_hash,
    ))
    session.commit()
    saved = session.query(AuditLog).first()
    saved.record_hash = compute_record_hash(
        timestamp=saved.timestamp, org_id=saved.org_id, action=saved.action,
        entity_type=saved.entity_type, entity_id=saved.entity_id,
        actor=saved.actor, details=saved.details, previous_hash=saved.previous_hash,
    )
    session.commit()

    br_bytes, _ = build_batch_record(session, campaign, org)
    zf = _zip.ZipFile(_io.BytesIO(br_bytes))
    br_names = set(zf.namelist())
    assert {
        "batch_record.pdf", "timeseries_data.csv",
        "offline_samples.csv", "batch_comparison.csv",
        "audit_log.csv", "verification.json",
    }.issubset(br_names)
    assert "summary.pdf" not in br_names
    assert "provenance.json" not in br_names
    verification = _json.loads(zf.read("verification.json"))
    assert verification.get("export_type") == "batch-record"
    for name, entry in verification["file_checksums"].items():
        assert hashlib.sha256(zf.read(name)).hexdigest() == entry["sha256"]

    eb_bytes, _ = build_evidence_book(session, campaign, org)
    eb_zf = _zip.ZipFile(_io.BytesIO(eb_bytes))
    assert "summary.pdf" in eb_zf.namelist()
    assert "batch_record_summary.pdf" not in eb_zf.namelist()
    session.close()


def test_campaign_methods_route_domain_guard():
    """Methods endpoint rejects non-wetlab campaigns."""
    from bioprocess_routes import get_campaign_methods
    from database import Campaign
    from unittest.mock import MagicMock

    campaign = Campaign(
        id="c1", org_id="demo-therapeutics", name="CompChem",
        domain="compchem", extra_params={},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = campaign

    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        get_campaign_methods("c1", db=db, auth=("demo-therapeutics", "test"))
    assert exc.value.status_code == 400


def test_campaign_approval_workflow_persists_audit_and_response(tmp_path):
    """Approving a wet lab campaign stores sign-off, audit row, and rollup."""
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import database as db_module
    from database import Base, Campaign, AuditLog
    from bioprocess_routes import (
        ApprovalCreate,
        approve_campaign,
        get_campaign,
        list_campaign_approvals,
    )

    db_path = tmp_path / "approval.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "users", "campaign_approvals", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    campaign = Campaign(
        id=str(uuid.uuid4()),
        org_id="demo-therapeutics",
        name="Approval Campaign",
        domain="wetlab",
        extra_params={},
    )
    session.add(campaign)
    session.commit()

    approval = approve_campaign(
        campaign_id=campaign.id,
        body=ApprovalCreate(
            approval_meaning="reviewer",
            comments="Reviewed process data and QC package.",
        ),
        db=session,
        auth=("demo-therapeutics", "reviewer@example.com"),
    )
    assert approval.approval_meaning == "reviewer"
    assert approval.approved_by_name == "reviewer@example.com"

    approvals = list_campaign_approvals(
        campaign_id=campaign.id,
        db=session,
        auth=("demo-therapeutics", "reviewer@example.com"),
    )
    assert len(approvals) == 1
    assert approvals[0].comments == "Reviewed process data and QC package."

    detail = get_campaign(
        campaign_id=campaign.id,
        db=session,
        auth=("demo-therapeutics", "reviewer@example.com"),
    )
    assert detail.is_approved is True
    assert detail.approval_count == 1
    assert detail.approvals[0].id == approval.id

    audit = session.query(AuditLog).one()
    assert audit.action.value == "campaign_approved"
    assert audit.entity_id == campaign.id
    assert audit.details["event"] == "campaign_approved"
    assert "approved as reviewer" in audit.details["message"]


def test_wetlab_parser_registry_contains_new_parsers():
    """The three new wet lab parsers are registered after vendor-specific
    parsers and before GenericCSVParser."""
    import sys, os as _os
    _REPO = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _REPO not in sys.path:
        sys.path.insert(0, _REPO)

    from parsers import list_supported_formats

    formats = list_supported_formats()
    # All three present
    for name in ("akta_csv", "generic_bioprocess_csv", "generic_offline_sample_csv"):
        assert name in formats, f"{name} missing from registry: {formats}"

    # Generic CSV is last (true fallback)
    assert formats[-1] == "generic_csv", formats
    # Vendor-specific parsers (e.g. sartorius_biostat) come before the
    # generic wet lab fallbacks
    sartorius_idx = formats.index("sartorius_biostat")
    generic_bio_idx = formats.index("generic_bioprocess_csv")
    assert sartorius_idx < generic_bio_idx, (
        f"sartorius_biostat ({sartorius_idx}) must come before "
        f"generic_bioprocess_csv ({generic_bio_idx})"
    )


def test_wetlab_demo_reset_requires_secret():
    """POST /api/v1/demo/wetlab/reset rejects requests without a matching
    DEMO_RESET_SECRET header."""
    import os as _os
    from unittest.mock import MagicMock
    import pytest
    from fastapi import HTTPException

    _os.environ["DEMO_RESET_SECRET"] = "expected-secret-xyz"
    try:
        from bioprocess_routes import reset_wetlab_demo

        db = MagicMock()

        # No header at all -> 401
        with pytest.raises(HTTPException) as exc:
            reset_wetlab_demo(x_demo_reset_secret=None, db=db)
        assert exc.value.status_code == 401

        # Wrong header -> 401
        with pytest.raises(HTTPException) as exc:
            reset_wetlab_demo(x_demo_reset_secret="nope", db=db)
        assert exc.value.status_code == 401

        # The MagicMock db means the success path would attempt the real
        # delete + seed — too expensive for a unit test. The auth guard
        # itself is what we're certifying here; the seed itself is
        # exercised by the other tests that consume seeded-shaped data.
    finally:
        _os.environ.pop("DEMO_RESET_SECRET", None)


def test_wetlab_seed_module_exposes_callable_seeder():
    """Smoke test: the seed module exports the expected entry points and
    the demo BATCH_SPECS produce the 004A / 004B / 004C narrative."""
    from wetlab_seed import (
        BATCH_SPECS,
        DEMO_ORG_ID,
        seed_wetlab_demo,
        delete_wetlab_demo,
    )

    assert callable(seed_wetlab_demo)
    assert callable(delete_wetlab_demo)
    assert DEMO_ORG_ID == "demo-therapeutics"

    # The three batches that drive the comparison story
    batch_numbers = {spec["batch_number"] for spec in BATCH_SPECS}
    assert batch_numbers == {"Batch_004A", "Batch_004B", "Batch_004C"}

    # 004C must be flagged as the lead condition and have the highest
    # configured final-titer
    lead = next(s for s in BATCH_SPECS if s["batch_number"] == "Batch_004C")
    assert lead.get("lead_condition") is True
    titers = {s["batch_number"]: s["final_titer"] for s in BATCH_SPECS}
    assert titers["Batch_004C"] > titers["Batch_004B"] > titers["Batch_004A"]


def test_include_metrics_query_param_populates_summary_metrics(tmp_path):
    """GET /api/v1/campaigns/{id}/batches?include_metrics=true returns
    summary_metrics with peak_vcd / final_titer / min_viability / duration /
    lead_condition derived from offline samples + extra_params."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.postgresql import JSONB, ARRAY  # noqa: F401
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = lambda self, t, **kw: "JSON"
    SQLiteTypeCompiler.visit_ARRAY = lambda self, t, **kw: "JSON"

    import database as db_module
    from database import Base, Campaign, Batch, OfflineSample
    from bioprocess_routes import list_campaign_batches

    db_path = tmp_path / "include_metrics.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in ("campaigns", "batches", "offline_samples",
                      "timeseries_data", "audit_logs")
    ]
    Base.metadata.create_all(engine, tables=tables)
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal

    session = SessionLocal()
    campaign = Campaign(
        id=str(uuid.uuid4()), org_id="demo-therapeutics",
        name="Inc Metrics Campaign", domain="wetlab", extra_params={},
    )
    session.add(campaign)
    session.flush()

    inoc = datetime(2024, 3, 1, 9, 0, tzinfo=timezone.utc)
    b1 = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_IM_A", bioreactor_model="Sartorius",
        inoculation_date=inoc, harvest_date=inoc + timedelta(days=14),
        status="harvested", extra_params={"lead_condition": False},
    )
    b2 = Batch(
        id=str(uuid.uuid4()), campaign_id=campaign.id,
        batch_number="Batch_IM_B", bioreactor_model="Sartorius",
        inoculation_date=inoc, harvest_date=inoc + timedelta(days=14),
        status="harvested", extra_params={"lead_condition": True},
    )
    session.add_all([b1, b2])
    session.flush()

    # Offline samples for both batches
    for batch, vcd_peak, titer_final, via_min in [
        (b1, 8.0, 800.0, 78.0),
        (b2, 18.0, 2400.0, 88.0),
    ]:
        for day, vcd_factor in [(1, 0.1), (5, 0.6), (10, 1.0)]:
            t = day * 24.0
            session.add(OfflineSample(
                id=str(uuid.uuid4()), batch_id=batch.id,
                sample_time_hours=t, sample_time_absolute=inoc + timedelta(hours=t),
                measurement_name="vcd_e6_per_ml",
                value=vcd_peak * vcd_factor, unit="1e6 cells/mL",
                instrument="Vi-CELL", qc_status="pass",
            ))
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=336.0, sample_time_absolute=inoc + timedelta(hours=336),
            measurement_name="titer_mg_per_l", value=titer_final,
            unit="mg/L", instrument="Octet", qc_status="pass",
        ))
        session.add(OfflineSample(
            id=str(uuid.uuid4()), batch_id=batch.id,
            sample_time_hours=336.0, sample_time_absolute=inoc + timedelta(hours=336),
            measurement_name="viability_percent", value=via_min,
            unit="%", instrument="Vi-CELL", qc_status="pass",
        ))
    session.commit()

    # Direct function call (mirrors the FastAPI dependency path).
    outs = list_campaign_batches(
        campaign_id=campaign.id, include_metrics=True,
        db=session, auth=("demo-therapeutics", "test"),
    )
    assert len(outs) == 2
    by_num = {o.batch_number: o for o in outs}
    a = by_num["Batch_IM_A"]
    b = by_num["Batch_IM_B"]
    assert a.summary_metrics is not None and b.summary_metrics is not None
    assert a.summary_metrics.peak_vcd == 8.0
    assert b.summary_metrics.peak_vcd == 18.0
    assert a.summary_metrics.final_titer == 800.0
    assert b.summary_metrics.final_titer == 2400.0
    assert a.summary_metrics.min_viability == 78.0
    assert b.summary_metrics.lead_condition is True
    assert a.summary_metrics.lead_condition is False
    # Duration in days
    assert a.summary_metrics.run_duration_days is not None
    assert abs(a.summary_metrics.run_duration_days - 13.0) < 0.1  # 336h - 24h = 312h = 13d

    # And: without include_metrics, summary_metrics is None
    outs_no = list_campaign_batches(
        campaign_id=campaign.id, include_metrics=False,
        db=session, auth=("demo-therapeutics", "test"),
    )
    assert all(o.summary_metrics is None for o in outs_no)
