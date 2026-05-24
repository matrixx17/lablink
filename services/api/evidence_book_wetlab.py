"""
Campaign Evidence Book ZIP export — wet lab branch.

Produces a self-contained, tamper-evident ZIP for due-diligence / regulatory
filing inclusion:

    {campaign_name}_EvidenceBook_{YYYY-MM-DD}.zip
      summary.pdf
      batches.csv (+ batches.parquet if pyarrow available)
      offline_samples.csv (+ .parquet)
      timeseries_summary.csv (+ .parquet)
      audit_log.csv
      provenance.json     (wet-lab-native; NOT a BCO — BCO is comp-chem only)
      verification.json

Two-pass build: content artifacts hashed first, PDF embeds the audit_log
SHA-256 on its audit page, verification.json written last. Everything in
memory via BytesIO. No temp files.

See `compchem_bco.py` + `evidence_book.py` on the comp-chem branch for the
parallel comp-chem implementation.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from database import (
    AuditAction,
    AuditLog,
    Batch,
    Campaign,
    EntityType,
    OfflineSample,
    TimeseriesData,
    compute_record_hash,
)

# Wet lab campaigns rarely exceed a handful of batches, but timeseries can be
# millions of points. Cap before we OOM the API container.
MAX_TIMESERIES_POINTS = 5_000_000

EVIDENCE_BOOK_SCHEMA = "lablink.evidence-book.verification/v1"
PROVENANCE_SCHEMA = "lablink.wetlab.provenance/v1"
EVIDENCE_GENERATOR = "LabLink Provenance Engine v1.0"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_evidence_book(
    db: Session,
    campaign: Campaign,
    org_id: str,
) -> Tuple[bytes, str]:
    """
    Build the Evidence Book ZIP for a wet lab campaign.

    Returns (zip_bytes, sha256_of_zip).
    """
    batches = (
        db.query(Batch)
        .filter(Batch.campaign_id == campaign.id)
        .order_by(Batch.batch_number)
        .all()
    )

    # Size guard — count total timeseries points before materialising.
    batch_ids = [b.id for b in batches]
    if batch_ids:
        ts_count = sum(
            len(row.timestamps or [])
            for row in db.query(TimeseriesData.timestamps).filter(
                TimeseriesData.batch_id.in_(batch_ids)
            ).all()
        )
        if ts_count > MAX_TIMESERIES_POINTS:
            raise EvidenceBookTooLarge(
                f"Campaign has {ts_count} timeseries points "
                f"(cap {MAX_TIMESERIES_POINTS}). Use the per-batch "
                "/timeseries endpoints to retrieve raw streams."
            )

    generated_at = datetime.now(timezone.utc)

    # ----- PASS 1: content artifacts ----------------------------------------
    batches_df = _collect_batches_dataframe(db, batches)
    offline_df = _collect_offline_dataframe(db, batches)
    ts_summary_df = _collect_timeseries_summary_dataframe(db, batches)
    audit_rows = _collect_campaign_audit(db, campaign, batches, org_id)

    audit_csv_bytes = _audit_rows_to_csv_bytes(audit_rows)
    batches_csv_bytes = _df_to_csv_bytes(batches_df)
    offline_csv_bytes = _df_to_csv_bytes(offline_df)
    ts_summary_csv_bytes = _df_to_csv_bytes(ts_summary_df)

    audit_chain_status = _verify_campaign_audit_chain(audit_rows)
    provenance = _build_provenance(
        campaign, batches, batches_df, audit_rows, audit_chain_status, generated_at
    )
    provenance_bytes = json.dumps(provenance, indent=2, default=str).encode("utf-8")

    files: Dict[str, bytes] = {
        "batches.csv": batches_csv_bytes,
        "offline_samples.csv": offline_csv_bytes,
        "timeseries_summary.csv": ts_summary_csv_bytes,
        "audit_log.csv": audit_csv_bytes,
        "provenance.json": provenance_bytes,
    }
    for name, df in (
        ("batches.parquet", batches_df),
        ("offline_samples.parquet", offline_df),
        ("timeseries_summary.parquet", ts_summary_df),
    ):
        parquet = _df_to_parquet_bytes(df)
        if parquet is not None:
            files[name] = parquet

    checksums = {name: _sha256(data) for name, data in files.items()}

    # ----- PASS 2: derived artifacts ----------------------------------------
    pdf_bytes = _build_pdf(
        campaign=campaign,
        batches=batches,
        batches_df=batches_df,
        offline_df=offline_df,
        ts_summary_df=ts_summary_df,
        audit_rows=audit_rows,
        audit_chain_status=audit_chain_status,
        audit_csv_sha256=checksums["audit_log.csv"],
        generated_at=generated_at,
    )
    files["summary.pdf"] = pdf_bytes
    checksums["summary.pdf"] = _sha256(pdf_bytes)

    root_hash = audit_rows[0].record_hash if audit_rows else None
    tip_hash = audit_rows[-1].record_hash if audit_rows else None

    verification: Dict[str, Any] = {
        "schema": EVIDENCE_BOOK_SCHEMA,
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "org_id": org_id,
        "export_timestamp": generated_at.isoformat(),
        "export_generated_by": EVIDENCE_GENERATOR,
        "audit_chain_status": (
            "verified" if audit_chain_status["valid"] else "failed"
        ),
        "audit_chain_detail": audit_chain_status,
        "total_events_verified": audit_chain_status["record_count"],
        "batches_exported": int(len(batches_df)),
        "offline_samples_exported": int(len(offline_df)),
        "timeseries_parameters_summarised": int(len(ts_summary_df)),
        "root_hash": root_hash,
        "tip_hash": tip_hash,
        "file_checksums": {
            name: {"sha256": digest, "bytes": len(files[name])}
            for name, digest in checksums.items()
        },
    }
    files["verification.json"] = json.dumps(verification, indent=2, default=str).encode("utf-8")

    fixed_mtime = max((b.created_at for b in batches if b.created_at), default=None) or generated_at
    zip_bytes = _assemble_zip(files, fixed_mtime=fixed_mtime)
    return zip_bytes, _sha256(zip_bytes)


class EvidenceBookTooLarge(Exception):
    pass


# ---------------------------------------------------------------------------
# Batch Record export (format=batch-record)
# ---------------------------------------------------------------------------

def build_batch_record(
    db: Session,
    campaign: Campaign,
    org_id: str,
) -> Tuple[bytes, str]:
    """
    Build the Batch Manufacturing Record ZIP for a wet lab campaign.

    Contents: batch_record_summary.pdf, timeseries_data.csv,
    offline_samples.csv, batch_comparison.csv, audit_log.csv,
    verification.json.
    """
    batches = (
        db.query(Batch)
        .filter(Batch.campaign_id == campaign.id)
        .order_by(Batch.batch_number)
        .all()
    )
    batch_ids = [b.id for b in batches]
    if batch_ids:
        ts_count = sum(
            len(row.timestamps or [])
            for row in db.query(TimeseriesData.timestamps).filter(
                TimeseriesData.batch_id.in_(batch_ids)
            ).all()
        )
        if ts_count > MAX_TIMESERIES_POINTS:
            raise EvidenceBookTooLarge(
                f"Campaign has {ts_count} timeseries points "
                f"(cap {MAX_TIMESERIES_POINTS})."
            )

    generated_at = datetime.now(timezone.utc)
    batch_map = {b.id: b for b in batches}

    comparison_df = _collect_batch_comparison(db, batches)
    offline_df = _collect_offline_dataframe(db, batches)
    ts_long_df = _collect_timeseries_long_dataframe(db, batches, batch_map)
    audit_rows = _collect_campaign_audit(db, campaign, batches, org_id)

    audit_csv_bytes = _audit_rows_to_csv_bytes(audit_rows)
    comparison_csv_bytes = _df_to_csv_bytes(comparison_df)
    offline_csv_bytes = _df_to_csv_bytes(offline_df)
    ts_csv_bytes = _df_to_csv_bytes(ts_long_df)

    audit_chain_status = _verify_campaign_audit_chain(audit_rows)

    files: Dict[str, bytes] = {
        "offline_samples.csv": offline_csv_bytes,
        "timeseries_data.csv": ts_csv_bytes,
        "batch_comparison.csv": comparison_csv_bytes,
        "audit_log.csv": audit_csv_bytes,
    }
    checksums = {name: _sha256(data) for name, data in files.items()}

    pdf_bytes = _build_batch_record_pdf(
        campaign=campaign,
        batches=batches,
        comparison_df=comparison_df,
        offline_df=offline_df,
        audit_rows=audit_rows,
        audit_chain_status=audit_chain_status,
        audit_csv_sha256=checksums["audit_log.csv"],
        generated_at=generated_at,
        db=db,
    )
    files["batch_record_summary.pdf"] = pdf_bytes
    checksums["batch_record_summary.pdf"] = _sha256(pdf_bytes)

    root_hash = audit_rows[0].record_hash if audit_rows else None
    tip_hash = audit_rows[-1].record_hash if audit_rows else None
    verification: Dict[str, Any] = {
        "schema": EVIDENCE_BOOK_SCHEMA,
        "export_type": "batch-record",
        "campaign_id": campaign.id,
        "campaign_name": campaign.name,
        "org_id": org_id,
        "export_timestamp": generated_at.isoformat(),
        "export_generated_by": EVIDENCE_GENERATOR,
        "audit_chain_status": (
            "verified" if audit_chain_status["valid"] else "failed"
        ),
        "audit_chain_detail": audit_chain_status,
        "total_events_verified": audit_chain_status["record_count"],
        "batches_exported": int(len(batches)),
        "root_hash": root_hash,
        "tip_hash": tip_hash,
        "file_checksums": {
            name: {"sha256": digest, "bytes": len(files[name])}
            for name, digest in checksums.items()
        },
    }
    files["verification.json"] = json.dumps(verification, indent=2, default=str).encode("utf-8")

    fixed_mtime = max((b.created_at for b in batches if b.created_at), default=None) or generated_at
    zip_bytes = _assemble_zip(files, fixed_mtime=fixed_mtime)
    return zip_bytes, _sha256(zip_bytes)


def _collect_batch_comparison(db: Session, batches: List[Batch]) -> pd.DataFrame:
    """One row per batch with summary metrics for batch-record export."""
    if not batches:
        return pd.DataFrame()
    batch_ids = [b.id for b in batches]
    samples = (
        db.query(OfflineSample)
        .filter(OfflineSample.batch_id.in_(batch_ids))
        .all()
    )
    by_batch: Dict[str, List[OfflineSample]] = {}
    for s in samples:
        by_batch.setdefault(s.batch_id, []).append(s)

    rows = []
    for b in batches:
        ssamples = by_batch.get(b.id) or []
        vcd_vals: List[float] = []
        titer_pairs: List[tuple] = []
        via_vals: List[float] = []
        for s in ssamples:
            if s.value is None:
                continue
            v = float(s.value)
            name = s.measurement_name or ""
            if name in ("vcd_e6_per_ml", "viable_cell_density_e6_per_ml"):
                vcd_vals.append(v)
            elif name == "titer_mg_per_l":
                titer_pairs.append((s.sample_time_hours or 0.0, v))
            elif name == "viability_percent":
                via_vals.append(v)
        titer_pairs.sort(key=lambda p: p[0])
        extra = b.extra_params or {}
        rows.append({
            "batch_number": b.batch_number,
            "condition_label": extra.get("condition_label"),
            "peak_vcd_e6_per_ml": max(vcd_vals) if vcd_vals else None,
            "final_titer_mg_per_l": titer_pairs[-1][1] if titer_pairs else None,
            "min_viability_percent": min(via_vals) if via_vals else None,
            "qc_status": extra.get("qc_status"),
            "lead_condition": bool(extra.get("lead_condition")),
            "ph_setpoint": extra.get("ph_setpoint"),
            "feed_strategy": extra.get("feed_strategy"),
            "status": b.status,
        })
    return pd.DataFrame(rows)


def _collect_timeseries_long_dataframe(
    db: Session,
    batches: List[Batch],
    batch_map: Dict[str, Batch],
) -> pd.DataFrame:
    """Long-format continuous data: batch_number, parameter, unit, time_hours, value."""
    if not batches:
        return pd.DataFrame()
    series = (
        db.query(TimeseriesData)
        .filter(TimeseriesData.batch_id.in_([b.id for b in batches]))
        .all()
    )
    rows: List[Dict[str, Any]] = []
    for s in series:
        b = batch_map.get(s.batch_id)
        batch_number = b.batch_number if b else s.batch_id
        inoc_unix = b.inoculation_date.timestamp() if b and b.inoculation_date else None
        meta = s.series_metadata or {}
        x_axis = meta.get("x_axis", "hours")
        ts_list = s.timestamps or []
        vals = s.values or []
        for t_raw, val in zip(ts_list, vals):
            if val is None:
                continue
            if x_axis == "ml":
                time_hours = float(t_raw)
                time_label = "elution_volume_ml"
            elif inoc_unix is not None:
                time_hours = (float(t_raw) - inoc_unix) / 3600.0
                time_label = "time_hours"
            else:
                time_hours = float(t_raw)
                time_label = "time_hours"
            rows.append({
                "batch_number": batch_number,
                "parameter_name": s.parameter_name,
                "unit": s.unit,
                time_label: round(time_hours, 4),
                "value": val,
                "source_instrument": s.source_instrument,
            })
    return pd.DataFrame(rows)


def _build_batch_record_pdf(
    *,
    campaign: Campaign,
    batches: List[Batch],
    comparison_df: pd.DataFrame,
    offline_df: pd.DataFrame,
    audit_rows: List[AuditLog],
    audit_chain_status: Dict[str, Any],
    audit_csv_sha256: str,
    generated_at: datetime,
    db: Session,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, PageBreak,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.85 * inch, bottomMargin=0.75 * inch,
        title=f"Batch Record — {campaign.name}",
        author=EVIDENCE_GENERATOR,
        compress=0,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=24, leading=30, spaceAfter=14,
    )
    eyebrow = ParagraphStyle(
        "eyebrow", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#6b6b6b"),
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13, leading=17, spaceBefore=10, spaceAfter=6,
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=9.5, leading=13,
    )
    flow: List[Any] = []

    flow.append(Paragraph("BATCH MANUFACTURING RECORD", eyebrow))
    flow.append(Paragraph(_xml_escape(campaign.name), title_style))
    flow.append(Paragraph(f"Generated at: {generated_at.isoformat()}", body))
    flow.append(Spacer(1, 0.2 * inch))
    flow.append(Paragraph(
        "This record compiles process parameters, offline analytics, and QC "
        "disposition for each production batch in the campaign.",
        body,
    ))
    flow.append(PageBreak())

    for b in batches:
        flow.append(Paragraph(_xml_escape(b.batch_number or b.id), h2))
        extra = b.extra_params or {}
        meta_rows = [
            ["Bioreactor", b.bioreactor_model or "—"],
            ["Volume (L)", _fmt_num(b.volume_liters)],
            ["Cell line", b.cell_line or "—"],
            ["Media", b.media or "—"],
            ["pH setpoint", _fmt_num(extra.get("ph_setpoint"), digits=2)],
            ["Feed strategy", extra.get("feed_strategy") or "—"],
            ["QC status", extra.get("qc_status") or "—"],
            ["Lead condition", "Yes" if extra.get("lead_condition") else "No"],
        ]
        mt = Table(meta_rows, colWidths=[1.6 * inch, 4.6 * inch])
        mt.setStyle(_table_style(header=False))
        flow.append(mt)
        flow.append(Spacer(1, 0.12 * inch))

        # Process parameters summary from timeseries
        ts_rows = (
            db.query(TimeseriesData)
            .filter(TimeseriesData.batch_id == b.id)
            .all()
        )
        if ts_rows:
            flow.append(Paragraph("Process parameters (continuous)", body))
            param_data = [["Parameter", "Unit", "Mean", "Min", "Max", "n"]]
            for s in ts_rows:
                if (s.series_metadata or {}).get("x_axis") == "ml":
                    continue
                vals = [v for v in (s.values or []) if v is not None]
                if not vals:
                    continue
                arr = pd.Series(vals, dtype=float)
                param_data.append([
                    s.parameter_name, s.unit or "",
                    _fmt_num(arr.mean()), _fmt_num(arr.min()),
                    _fmt_num(arr.max()), str(len(arr)),
                ])
            if len(param_data) > 1:
                pt = Table(param_data, repeatRows=1)
                pt.setStyle(_table_style(header=True))
                flow.append(pt)
                flow.append(Spacer(1, 0.1 * inch))

        # Offline samples for this batch
        batch_offline = offline_df[offline_df["batch_id"] == b.id] if not offline_df.empty and "batch_id" in offline_df.columns else pd.DataFrame()
        if not batch_offline.empty:
            flow.append(Paragraph("Offline measurements", body))
            off_data = [["Measurement", "Time (h)", "Value", "Unit", "QC"]]
            for _, row in batch_offline.iterrows():
                off_data.append([
                    str(row.get("measurement_name", "")),
                    _fmt_num(row.get("sample_time_hours"), digits=1),
                    _fmt_num(row.get("value")),
                    str(row.get("unit") or ""),
                    str(row.get("qc_status") or ""),
                ])
            ot = Table(off_data, repeatRows=1, colWidths=[1.5 * inch, 0.8 * inch, 0.8 * inch, 0.7 * inch, 0.6 * inch])
            ot.setStyle(_table_style(header=True))
            flow.append(ot)
            flow.append(Spacer(1, 0.1 * inch))

        qc_results = extra.get("qc_results") or []
        if qc_results:
            flow.append(Paragraph("QC results", body))
            qc_data = [["Check", "Status", "Message"]]
            for r in qc_results:
                if isinstance(r, dict):
                    qc_data.append([
                        r.get("check_name", ""),
                        r.get("status", ""),
                        _short(r.get("message"), 60),
                    ])
            qt = Table(qc_data, repeatRows=1, colWidths=[1.4 * inch, 0.7 * inch, 3.9 * inch])
            qt.setStyle(_table_style(header=True))
            flow.append(qt)

        flow.append(PageBreak())

    # Audit summary page
    flow.append(Paragraph("AUDIT TRAIL", eyebrow))
    chain_valid = audit_chain_status["valid"]
    status_word = "VERIFIED" if chain_valid else "FAILED"
    flow.append(Paragraph(
        f"Chain status: <b>{status_word}</b> — events: {audit_chain_status['record_count']}",
        body,
    ))
    flow.append(Paragraph(
        f"Full log in audit_log.csv (SHA-256: <font face='Courier'>{audit_csv_sha256}</font>)",
        body,
    ))

    doc.build(flow)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Content collectors
# ---------------------------------------------------------------------------

def _collect_batches_dataframe(db: Session, batches: List[Batch]) -> pd.DataFrame:
    """One row per batch with derived stats (peak VCD, final titer, duration)."""
    if not batches:
        return pd.DataFrame()
    batch_ids = [b.id for b in batches]

    # Aggregate offline samples per batch for peak VCD and final titer
    peaks: Dict[str, Dict[str, Any]] = {bid: {} for bid in batch_ids}
    samples = (
        db.query(OfflineSample)
        .filter(OfflineSample.batch_id.in_(batch_ids))
        .all()
    )
    for s in samples:
        bucket = peaks.setdefault(s.batch_id, {})
        meas = (s.measurement_name or "").lower()
        if "vcd" in meas or "viable_cell" in meas:
            cur = bucket.get("peak_vcd")
            if cur is None or (s.value is not None and s.value > cur):
                bucket["peak_vcd"] = s.value
        if "titer" in meas:
            # final titer = max sample_time_hours
            hours = s.sample_time_hours or 0
            existing_hours = bucket.get("_titer_hours", -1)
            if hours >= existing_hours:
                bucket["_titer_hours"] = hours
                bucket["final_titer"] = s.value

    rows = []
    for b in batches:
        p = peaks.get(b.id, {})
        duration_h = None
        if b.inoculation_date and b.harvest_date:
            duration_h = (b.harvest_date - b.inoculation_date).total_seconds() / 3600.0
        rows.append({
            "batch_id": b.id,
            "campaign_id": b.campaign_id,
            "batch_number": b.batch_number,
            "bioreactor_model": b.bioreactor_model,
            "volume_liters": b.volume_liters,
            "cell_line": b.cell_line,
            "media": b.media,
            "inoculation_date": _iso(b.inoculation_date),
            "harvest_date": _iso(b.harvest_date),
            "duration_h": duration_h,
            "status": b.status,
            "peak_vcd": p.get("peak_vcd"),
            "final_titer": p.get("final_titer"),
            "condition_label": (b.extra_params or {}).get("condition_label"),
            "ph_setpoint": (b.extra_params or {}).get("ph_setpoint"),
            "do_setpoint_percent": (b.extra_params or {}).get("do_setpoint_percent"),
            "created_at": _iso(b.created_at),
        })
    return pd.DataFrame(rows)


def _collect_offline_dataframe(db: Session, batches: List[Batch]) -> pd.DataFrame:
    if not batches:
        return pd.DataFrame()
    rows = (
        db.query(OfflineSample)
        .filter(OfflineSample.batch_id.in_([b.id for b in batches]))
        .order_by(OfflineSample.batch_id.asc(), OfflineSample.sample_time_hours.asc())
        .all()
    )
    data = []
    for s in rows:
        data.append({
            "sample_id": s.id,
            "batch_id": s.batch_id,
            "sample_time_hours": s.sample_time_hours,
            "sample_time_absolute": _iso(s.sample_time_absolute),
            "measurement_name": s.measurement_name,
            "value": s.value,
            "unit": s.unit,
            "instrument": s.instrument,
            "qc_status": s.qc_status,
            "created_at": _iso(s.created_at),
        })
    return pd.DataFrame(data)


def _collect_timeseries_summary_dataframe(
    db: Session, batches: List[Batch]
) -> pd.DataFrame:
    """Per-batch, per-parameter min/max/mean/std/n. Full series omitted —
    that goes in audit_log.csv-adjacent per-batch CSVs (not bundled here to
    keep the zip small; downstream consumers hit /batches/{id}/timeseries
    if they need the raw arrays)."""
    if not batches:
        return pd.DataFrame()
    series = (
        db.query(TimeseriesData)
        .filter(TimeseriesData.batch_id.in_([b.id for b in batches]))
        .all()
    )
    data = []
    for s in series:
        vals = [v for v in (s.values or []) if v is not None]
        if not vals:
            data.append({
                "batch_id": s.batch_id,
                "parameter_name": s.parameter_name,
                "unit": s.unit,
                "n": 0,
                "min": None,
                "max": None,
                "mean": None,
                "std": None,
                "source_instrument": s.source_instrument,
            })
            continue
        arr = pd.Series(vals, dtype=float)
        data.append({
            "batch_id": s.batch_id,
            "parameter_name": s.parameter_name,
            "unit": s.unit,
            "n": int(len(arr)),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "mean": float(arr.mean()),
            "std": float(arr.std()) if len(arr) > 1 else 0.0,
            "source_instrument": s.source_instrument,
        })
    return pd.DataFrame(data)


def _collect_campaign_audit(
    db: Session,
    campaign: Campaign,
    batches: List[Batch],
    org_id: str,
) -> List[AuditLog]:
    """Audit events touching this campaign or any of its batches.

    Wet lab audit_logs use enum columns and details JSONB; on Postgres we
    use the ->> operator, on SQLite we backfill via the batch IDs we
    already loaded (and the campaign ID itself)."""
    cid = str(campaign.id)
    batch_id_strs = [str(b.id) for b in batches]

    primary = (
        db.query(AuditLog)
        .filter(
            AuditLog.org_id == org_id,
            (
                (AuditLog.entity_id == cid)
                | (AuditLog.details["campaign_id"].astext == cid)
            ),
        )
        .order_by(AuditLog.id.asc())
        .all()
    )

    by_id: "OrderedDict[int, AuditLog]" = OrderedDict((r.id, r) for r in primary)

    if batch_id_strs:
        backfill = (
            db.query(AuditLog)
            .filter(
                AuditLog.org_id == org_id,
                AuditLog.entity_id.in_(batch_id_strs),
            )
            .order_by(AuditLog.id.asc())
            .all()
        )
        for r in backfill:
            by_id.setdefault(r.id, r)

    return sorted(by_id.values(), key=lambda r: r.id)


def _audit_rows_to_csv_bytes(rows: List[AuditLog]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "sequence_number", "id", "timestamp", "action", "entity_type",
        "entity_id", "actor", "details_json", "previous_hash", "record_hash",
    ])
    for i, r in enumerate(rows, start=1):
        w.writerow([
            i, r.id, _iso(r.timestamp),
            r.action.value if hasattr(r.action, "value") else str(r.action),
            r.entity_type.value if hasattr(r.entity_type, "value") else str(r.entity_type),
            r.entity_id, r.actor,
            json.dumps(r.details, sort_keys=True, default=str) if r.details else "",
            r.previous_hash or "",
            r.record_hash or "",
        ])
    return buf.getvalue().encode("utf-8")


def _verify_campaign_audit_chain(rows: List[AuditLog]) -> Dict[str, Any]:
    """Validate each row's record_hash recomputes from its own fields.

    Like the comp-chem variant we don't chain-walk because rows are
    campaign-scoped; previous_hash links point at the previous ORG-WIDE row,
    not the previous campaign row."""
    errors = []
    for r in rows:
        expected = compute_record_hash(
            timestamp=r.timestamp,
            org_id=r.org_id,
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            actor=r.actor,
            details=r.details,
            previous_hash=r.previous_hash,
        )
        if r.record_hash != expected:
            errors.append({
                "record_id": r.id,
                "error": "record_hash mismatch",
                "expected": expected,
                "actual": r.record_hash,
            })
    return {
        "valid": len(errors) == 0,
        "record_count": len(rows),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Wet-lab-native provenance (NOT a BCO; BCO is comp-chem-only)
# ---------------------------------------------------------------------------

def _build_provenance(
    campaign: Campaign,
    batches: List[Batch],
    batches_df: pd.DataFrame,
    audit_rows: List[AuditLog],
    audit_chain_status: Dict[str, Any],
    generated_at: datetime,
) -> Dict[str, Any]:
    contributors_seen: "OrderedDict[str, None]" = OrderedDict()
    for r in audit_rows:
        if r.actor and r.actor not in contributors_seen:
            contributors_seen[r.actor] = None
    contributors = list(contributors_seen.keys())

    batch_summary = []
    if not batches_df.empty:
        for _, row in batches_df.iterrows():
            batch_summary.append({
                "batch_id": row.get("batch_id"),
                "batch_number": row.get("batch_number"),
                "condition_label": row.get("condition_label"),
                "bioreactor_model": row.get("bioreactor_model"),
                "peak_vcd": _to_native(row.get("peak_vcd")),
                "final_titer": _to_native(row.get("final_titer")),
                "duration_h": _to_native(row.get("duration_h")),
                "status": row.get("status"),
            })

    return {
        "schema": PROVENANCE_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "generated_by": EVIDENCE_GENERATOR,
        "campaign": {
            "id": campaign.id,
            "org_id": campaign.org_id,
            "name": campaign.name,
            "description": campaign.description,
            "domain": campaign.domain,
            "extra_params": campaign.extra_params or {},
            "created_at": _iso(campaign.created_at),
        },
        "batches": batch_summary,
        "contributors": contributors,
        "audit": {
            "record_count": audit_chain_status["record_count"],
            "valid": audit_chain_status["valid"],
            "root_hash": audit_rows[0].record_hash if audit_rows else None,
            "tip_hash": audit_rows[-1].record_hash if audit_rows else None,
        },
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _build_pdf(
    *,
    campaign: Campaign,
    batches: List[Batch],
    batches_df: pd.DataFrame,
    offline_df: pd.DataFrame,
    ts_summary_df: pd.DataFrame,
    audit_rows: List[AuditLog],
    audit_chain_status: Dict[str, Any],
    audit_csv_sha256: str,
    generated_at: datetime,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buf = io.BytesIO()
    # compress=0 keeps the content stream uncompressed so external tools and
    # tests can grep for the audit_log.csv SHA-256 embedded on the audit page.
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.75 * inch,
        title=f"Evidence Book — {campaign.name}",
        author=EVIDENCE_GENERATOR,
        subject=f"audit_log.csv SHA-256: {audit_csv_sha256}",
        keywords=f"campaign:{campaign.id};audit_sha256:{audit_csv_sha256}",
        compress=0,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=28, leading=34, spaceAfter=18,
    )
    eyebrow = ParagraphStyle(
        "eyebrow", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#6b6b6b"),
        leading=12, spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=12,
        spaceAfter=8, textColor=colors.HexColor("#0d0d12"),
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=10, leading=14, spaceAfter=6,
    )
    mono = ParagraphStyle(
        "mono", parent=styles["BodyText"],
        fontName="Courier", fontSize=8, leading=11, textColor=colors.HexColor("#444"),
    )

    flow = []

    # ----- Cover -----------------------------------------------------------
    flow.append(Paragraph("CAMPAIGN EVIDENCE BOOK", eyebrow))
    flow.append(Paragraph(_xml_escape(campaign.name), title_style))
    target = (campaign.extra_params or {}).get("target")
    if target:
        flow.append(Paragraph(f"Target: <b>{_xml_escape(target)}</b>", body))
    flow.append(Spacer(1, 0.25 * inch))

    verification_hash = hashlib.sha256(
        f"{campaign.id}|{generated_at.isoformat()}".encode("utf-8")
    ).hexdigest()
    cover_table = Table([
        ["Generated by", EVIDENCE_GENERATOR],
        ["Generated at", generated_at.isoformat()],
        ["Campaign ID", str(campaign.id)],
        ["Organisation", campaign.org_id],
        ["Verification hash", verification_hash],
    ], colWidths=[1.6 * inch, 4.6 * inch])
    cover_table.setStyle(_table_style(header=False))
    flow.append(cover_table)
    flow.append(Spacer(1, 0.35 * inch))
    flow.append(Paragraph(
        "This document was automatically compiled from cryptographically "
        "verified records. The audit trail integrity has been verified at "
        "export time; see the Audit Trail section and "
        "<font face='Courier'>verification.json</font> for proofs.",
        body,
    ))
    flow.append(PageBreak())

    # ----- Campaign summary -----------------------------------------------
    flow.append(Paragraph("CAMPAIGN SUMMARY", eyebrow))
    flow.append(Paragraph("At a glance", h2))
    if campaign.description:
        flow.append(Paragraph(_xml_escape(campaign.description), body))
    flow.append(Spacer(1, 0.12 * inch))

    cro = (campaign.extra_params or {}).get("cro_partner")
    delivered = (campaign.extra_params or {}).get("delivery_date")
    status = (campaign.extra_params or {}).get("status") or "active"

    summary_rows = [
        ["Status", status],
        ["Domain", campaign.domain],
        ["Batch count", str(len(batches_df))],
        ["Total offline samples", str(len(offline_df))],
        ["Process type", (campaign.extra_params or {}).get("process_type") or "—"],
        ["CRO partner", cro or "—"],
        ["Delivered", delivered or "—"],
    ]
    if not batches_df.empty and "final_titer" in batches_df.columns:
        peak_titer = batches_df["final_titer"].dropna().max()
        if pd.notna(peak_titer):
            summary_rows.append(["Peak final titer (across batches)", f"{peak_titer:.0f} mg/L"])
    s_table = Table(summary_rows, colWidths=[2.0 * inch, 4.2 * inch])
    s_table.setStyle(_table_style(header=False))
    flow.append(s_table)
    flow.append(PageBreak())

    # ----- Batch table ----------------------------------------------------
    flow.append(Paragraph("BATCHES", eyebrow))
    flow.append(Paragraph(f"All {len(batches_df)} batches", h2))
    if not batches_df.empty:
        cols = ["batch_number", "condition_label", "peak_vcd", "final_titer", "duration_h", "status"]
        header = ["Batch", "Condition", "Peak VCD (×10⁶/mL)", "Final titer (mg/L)", "Duration (h)", "Status"]
        data = [header]
        for _, row in batches_df.iterrows():
            data.append([
                _short(row.get("batch_number"), 14),
                _short(row.get("condition_label"), 28),
                _fmt_num(row.get("peak_vcd")),
                _fmt_num(row.get("final_titer"), digits=0),
                _fmt_num(row.get("duration_h"), digits=0),
                _short(row.get("status"), 12),
            ])
        b_table = Table(data, repeatRows=1, colWidths=[
            0.9 * inch, 2.0 * inch, 1.2 * inch, 1.1 * inch, 0.8 * inch, 0.8 * inch,
        ])
        b_table.setStyle(_table_style(header=True))
        flow.append(b_table)
    else:
        flow.append(Paragraph("No batches recorded.", body))
    flow.append(PageBreak())

    # ----- Offline sample summary -----------------------------------------
    flow.append(Paragraph("OFFLINE SAMPLES", eyebrow))
    flow.append(Paragraph("Distribution by measurement", h2))
    if not offline_df.empty:
        summary = offline_df.groupby("measurement_name")["value"].agg(["count", "mean", "std", "min", "max"])
        unit_lookup = (
            offline_df.drop_duplicates(subset=["measurement_name"])
            .set_index("measurement_name")["unit"].to_dict()
        )
        rows = [["Measurement", "Unit", "n", "Mean", "Std", "Min", "Max"]]
        for name, r in summary.iterrows():
            rows.append([
                name,
                unit_lookup.get(name, ""),
                f"{int(r['count'])}",
                _fmt_num(r["mean"]),
                _fmt_num(r["std"]),
                _fmt_num(r["min"]),
                _fmt_num(r["max"]),
            ])
        m_table = Table(rows, repeatRows=1, colWidths=[
            1.8 * inch, 0.7 * inch, 0.5 * inch, 0.9 * inch, 0.9 * inch, 0.7 * inch, 0.7 * inch,
        ])
        m_table.setStyle(_table_style(header=True))
        flow.append(m_table)
    else:
        flow.append(Paragraph("No offline samples recorded.", body))
    flow.append(PageBreak())

    # ----- Audit trail ----------------------------------------------------
    flow.append(Paragraph("AUDIT TRAIL", eyebrow))
    flow.append(Paragraph("Tamper-evident chain summary", h2))

    chain_valid = audit_chain_status["valid"]
    status_color = "#0f7a45" if chain_valid else "#a8211a"
    status_word = "VERIFIED" if chain_valid else "FAILED"
    flow.append(Paragraph(
        f"Chain status: <font color='{status_color}'><b>{status_word}</b></font> "
        f"&nbsp;|&nbsp; campaign-scoped events: {audit_chain_status['record_count']}",
        body,
    ))
    root_h = audit_rows[0].record_hash if audit_rows else "—"
    tip_h = audit_rows[-1].record_hash if audit_rows else "—"
    flow.append(Paragraph(f"Root hash: <font face='Courier'>{root_h}</font>", mono))
    flow.append(Paragraph(f"Tip hash: &nbsp;<font face='Courier'>{tip_h}</font>", mono))
    flow.append(Spacer(1, 0.10 * inch))
    flow.append(Paragraph(
        f"Full audit log available in <font face='Courier'>audit_log.csv</font> "
        f"(SHA-256: <font face='Courier'>{audit_csv_sha256}</font>).",
        body,
    ))
    flow.append(Spacer(1, 0.16 * inch))

    if audit_rows:
        flow.append(Paragraph("First and last events", h2))
        sample = list(audit_rows[:5])
        if len(audit_rows) > 10:
            sample.append("ELLIPSIS")
            sample.extend(list(audit_rows[-5:]))
        data = [["#", "Timestamp", "Action", "Entity", "Actor", "Hash (first 12)"]]
        for ev in sample:
            if ev == "ELLIPSIS":
                data.append(["…", "…", "…", "…", "…", "…"])
                continue
            action_v = ev.action.value if hasattr(ev.action, "value") else str(ev.action)
            entity_v = ev.entity_type.value if hasattr(ev.entity_type, "value") else str(ev.entity_type)
            data.append([
                str(audit_rows.index(ev) + 1),
                _iso(ev.timestamp) or "",
                _short(action_v, 18),
                _short(f"{entity_v}:{ev.entity_id}", 22),
                _short(ev.actor, 22),
                (ev.record_hash or "")[:12],
            ])
        a_table = Table(data, repeatRows=1, colWidths=[
            0.4 * inch, 1.4 * inch, 1.2 * inch, 1.6 * inch, 1.4 * inch, 1.0 * inch,
        ])
        a_table.setStyle(_table_style(header=True))
        flow.append(a_table)

    doc.build(flow)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def _df_to_parquet_bytes(df: pd.DataFrame) -> Optional[bytes]:
    try:
        import pyarrow  # noqa: F401
    except Exception:
        return None
    buf = io.BytesIO()
    try:
        df.to_parquet(buf, index=False)
    except Exception:
        return None
    return buf.getvalue()


def _assemble_zip(files: Dict[str, bytes], *, fixed_mtime: datetime) -> bytes:
    out = io.BytesIO()
    date_time = (
        fixed_mtime.year, fixed_mtime.month, fixed_mtime.day,
        fixed_mtime.hour, fixed_mtime.minute, fixed_mtime.second,
    ) if fixed_mtime else (2024, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files.keys()):
            info = zipfile.ZipInfo(filename=name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return out.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(ts: Optional[datetime]) -> Optional[str]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat()


def _short(v: Any, n: int = 32) -> str:
    if v is None:
        return ""
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_num(v: Any, *, digits: int = 3) -> str:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        return f"{float(v):.{digits}g}" if digits > 0 else f"{float(v):.0f}"
    except Exception:
        return ""


def _xml_escape(s: Optional[str]) -> str:
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _to_native(v: Any) -> Any:
    """Convert pandas/numpy scalars to JSON-native primitives."""
    if v is None:
        return None
    try:
        import math
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:
        pass
    try:
        if hasattr(v, "item"):
            return v.item()
    except Exception:
        pass
    return v


def _table_style(*, header: bool):
    from reportlab.lib import colors
    from reportlab.platypus import TableStyle

    rules = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#0d0d12")),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.HexColor("#0d0d12")),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, colors.HexColor("#0d0d12")),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f4f7fa")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        rules += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#0d0d12")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")),
        ]
    return TableStyle(rules)
