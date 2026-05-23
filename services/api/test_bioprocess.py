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

    from sqlalchemy import create_engine
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
                      "timeseries_data", "audit_logs")
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
    from evidence_book import build_evidence_book
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
