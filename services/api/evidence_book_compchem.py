"""
Campaign Evidence Book ZIP export — comp-chem branch.

Produces a self-contained, tamper-evident ZIP suitable for inclusion in
a due-diligence VDR or regulatory filing:

    {campaign_name}_EvidenceBook_{YYYY-MM-DD}.zip
      summary.pdf
      runs.csv (+ runs.parquet if pyarrow available)
      molecules.csv (+ molecules.parquet)
      metrics.csv (+ metrics.parquet)
      audit_log.csv
      campaign_bco.json
      verification.json

Two-pass build: content artifacts (CSVs / JSON / audit) → hashes →
PDF with audit-CSV hash embedded → verification.json → ZIP. Everything
in memory via BytesIO; no temp files.

For the wet lab vertical see the parallel module on the bioprocess
branch.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from compchem_bco import build_bco
from compchem_models import (
    AssayResult,
    AuditEvent,
    Campaign,
    Molecule,
    MoleculeProperty,
    Project,
    Run,
    RunMetric,
    compute_audit_hash,
    verify_cc_audit_chain,
)

# Hard memory cap. The whole zip lives in RAM; refuse pathological cases
# instead of OOM'ing the API container.
MAX_RUNS = 50_000

EVIDENCE_BOOK_SCHEMA = "lablink.evidence-book.verification/v1"
EVIDENCE_GENERATOR = "LabLink Provenance Engine v1.0"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_evidence_book(
    db: Session,
    campaign: Campaign,
    project: Project,
    org_id: str,
) -> Tuple[bytes, str]:
    """
    Build the Evidence Book ZIP for a campaign.

    Returns (zip_bytes, sha256_of_zip).
    """
    run_count = (
        db.query(Run)
        .filter(Run.campaign_id == campaign.id, Run.org_id == org_id)
        .count()
    )
    if run_count > MAX_RUNS:
        raise EvidenceBookTooLarge(
            f"Campaign has {run_count} runs (cap {MAX_RUNS}). "
            "Use /api/v1/campaigns/{id}/export?format=parquet instead."
        )

    generated_at = datetime.now(timezone.utc)

    # ----- PASS 1: content artifacts ----------------------------------------
    runs_df = _collect_runs_dataframe(db, campaign, org_id)
    molecules_df = _collect_molecules_dataframe(db, campaign, org_id)
    metrics_df = _collect_metrics_dataframe(db, campaign, org_id)
    audit_rows = _collect_campaign_audit(db, campaign, org_id)

    audit_csv_bytes = _audit_rows_to_csv_bytes(audit_rows)
    runs_csv_bytes = _df_to_csv_bytes(runs_df)
    molecules_csv_bytes = _df_to_csv_bytes(molecules_df)
    metrics_csv_bytes = _df_to_csv_bytes(metrics_df)

    bco = build_bco(db, campaign, project)
    bco_bytes = json.dumps(bco, indent=2, default=str).encode("utf-8")

    audit_chain_status = _verify_campaign_audit_chain(audit_rows)
    org_wide_audit = verify_cc_audit_chain(org_id=org_id, db=db)
    audit_chain_status["org_chain_valid"] = bool(org_wide_audit.get("valid"))
    audit_chain_status["total_events_org_wide"] = int(
        org_wide_audit.get("record_count", 0)
    )

    files: Dict[str, bytes] = {
        "runs.csv": runs_csv_bytes,
        "molecules.csv": molecules_csv_bytes,
        "metrics.csv": metrics_csv_bytes,
        "audit_log.csv": audit_csv_bytes,
        "campaign_bco.json": bco_bytes,
    }

    # Optional parquet — soft-import.
    for name, df in (
        ("runs.parquet", runs_df),
        ("molecules.parquet", molecules_df),
        ("metrics.parquet", metrics_df),
    ):
        parquet = _df_to_parquet_bytes(df)
        if parquet is not None:
            files[name] = parquet

    checksums = {name: _sha256(data) for name, data in files.items()}

    # ----- PASS 2: derived artifacts ----------------------------------------
    pdf_bytes = _build_pdf(
        campaign=campaign,
        project=project,
        runs_df=runs_df,
        molecules_df=molecules_df,
        metrics_df=metrics_df,
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
        "molecules_exported": int(len(molecules_df)),
        "runs_exported": int(len(runs_df)),
        "metrics_exported": int(len(metrics_df)),
        "root_hash": root_hash,
        "tip_hash": tip_hash,
        "file_checksums": {
            name: {"sha256": digest, "bytes": len(files[name])}
            for name, digest in checksums.items()
        },
    }
    verification_bytes = json.dumps(verification, indent=2, default=str).encode("utf-8")
    files["verification.json"] = verification_bytes

    zip_bytes = _assemble_zip(files, fixed_mtime=campaign.updated_at or generated_at)
    return zip_bytes, _sha256(zip_bytes)


class EvidenceBookTooLarge(Exception):
    pass


# ---------------------------------------------------------------------------
# Content collectors
# ---------------------------------------------------------------------------

def _collect_runs_dataframe(
    db: Session, campaign: Campaign, org_id: str
) -> pd.DataFrame:
    """Flat run-level dataframe. Includes reproducibility fingerprint columns
    plus the most useful parameters pulled out of `extra_metadata`."""
    runs = (
        db.query(Run)
        .filter(Run.campaign_id == campaign.id, Run.org_id == org_id)
        .order_by(Run.id.asc())
        .all()
    )

    # Molecule lookup by id for the label column
    molecule_ids = {r.molecule_id for r in runs if r.molecule_id is not None}
    mol_label: Dict[int, str] = {}
    if molecule_ids:
        for m in db.query(Molecule.id, Molecule.name, Molecule.external_id, Molecule.canonical_smiles).filter(
            Molecule.id.in_(molecule_ids)
        ).all():
            mol_label[m.id] = m.name or m.external_id or m.canonical_smiles or f"mol_{m.id}"

    rows = []
    for r in runs:
        meta = r.extra_metadata or {}
        rows.append({
            "run_id": r.id,
            "external_run_id": r.external_run_id,
            "molecule_id": r.molecule_id,
            "molecule_label": mol_label.get(r.molecule_id) if r.molecule_id else None,
            "run_kind": r.run_kind,
            "status": r.status,
            "qc_status": (
                (meta.get("server_qc") or {}).get("overall_status")
                or (meta.get("client_qc") or {}).get("overall_status")
            ),
            "software_name": r.software_name,
            "software_version": r.software_version,
            "forcefield": r.forcefield,
            "timestep_fs": meta.get("timestep_fs"),
            "ensemble": meta.get("ensemble"),
            "functional": meta.get("functional"),
            "basis_set": meta.get("basis_set"),
            "compute_environment": r.compute_environment,
            "wall_time_s": r.wall_time_s,
            "started_at": _iso(r.started_at),
            "completed_at": _iso(r.completed_at),
            "created_at": _iso(r.created_at),
        })
    return pd.DataFrame(rows)


def _collect_molecules_dataframe(
    db: Session, campaign: Campaign, org_id: str
) -> pd.DataFrame:
    """One row per molecule. Adds one column per metric_name with the best
    (min for "score"-like, max otherwise) value seen across all of that
    molecule's runs."""
    molecules = (
        db.query(Molecule)
        .filter(Molecule.campaign_id == campaign.id, Molecule.org_id == org_id)
        .order_by(Molecule.id.asc())
        .all()
    )
    base_rows = []
    qc_by_mol: Dict[int, str] = {}
    for m in molecules:
        meta = m.extra_metadata or {}
        qc = meta.get("qc_status") or meta.get("qc", {}).get("overall_status") if isinstance(meta.get("qc"), dict) else meta.get("qc_status")
        if qc:
            qc_by_mol[m.id] = qc
        base_rows.append({
            "molecule_id": m.id,
            "label": m.name or m.external_id or f"mol_{m.id}",
            "smiles": m.canonical_smiles,
            "inchi_key": m.inchi_key,
            "formula": m.formula,
            "molecular_weight": m.molecular_weight,
            "qc_status": qc_by_mol.get(m.id),
        })

    df = pd.DataFrame(base_rows)
    if df.empty:
        return df

    # Pivot metrics: for each metric_name, take the "best" value per molecule.
    # Heuristic: score/affinity/delta_g -> min; everything else -> max.
    metric_rows = (
        db.query(
            RunMetric.molecule_id,
            RunMetric.metric_name,
            RunMetric.value,
            RunMetric.unit,
        )
        .filter(RunMetric.org_id == org_id, RunMetric.molecule_id.in_([m.id for m in molecules]))
        .all()
    )
    if metric_rows:
        m_df = pd.DataFrame(metric_rows, columns=["molecule_id", "metric_name", "value", "unit"])
        for name, group in m_df.groupby("metric_name"):
            lower = name.lower()
            agg = group.groupby("molecule_id")["value"].min() if any(
                k in lower for k in ("score", "affinity", "delta_g", "energy")
            ) else group.groupby("molecule_id")["value"].max()
            df[f"metric_{name}"] = df["molecule_id"].map(agg)
    return df


def _collect_metrics_dataframe(
    db: Session, campaign: Campaign, org_id: str
) -> pd.DataFrame:
    """Flat RunMetric table: one row per (run, metric)."""
    rows = (
        db.query(
            RunMetric.id.label("metric_id"),
            RunMetric.run_id,
            RunMetric.molecule_id,
            RunMetric.metric_name,
            RunMetric.value,
            RunMetric.unit,
            RunMetric.confidence,
            RunMetric.stderr,
            RunMetric.created_at,
        )
        .join(Run, Run.id == RunMetric.run_id)
        .filter(Run.campaign_id == campaign.id, RunMetric.org_id == org_id)
        .order_by(RunMetric.id.asc())
        .all()
    )
    data = []
    for r in rows:
        d = dict(r._mapping)
        d["created_at"] = _iso(d.get("created_at"))
        data.append(d)
    return pd.DataFrame(data)


def _collect_campaign_audit(
    db: Session, campaign: Campaign, org_id: str
) -> List[AuditEvent]:
    """Audit events touching this campaign or its child entities. Same trick
    as compchem_bco._collect_contributors: filter on entity_id == campaign or
    details["campaign_id"], plus backfill via child IDs for SQLite paths
    where the JSONB operator silently no-matches."""
    cid_str = str(campaign.id)
    rows = list(
        db.query(AuditEvent)
        .filter(
            AuditEvent.org_id == org_id,
            (
                (AuditEvent.entity_id == cid_str)
                | (AuditEvent.details["campaign_id"].astext == cid_str)
            ),
        )
        .order_by(AuditEvent.id.asc())
        .all()
    )

    child_ids: List[str] = []
    child_ids += [str(rid) for (rid,) in db.query(Run.id).filter(
        Run.campaign_id == campaign.id, Run.org_id == org_id
    ).all()]
    child_ids += [str(mid) for (mid,) in db.query(Molecule.id).filter(
        Molecule.campaign_id == campaign.id, Molecule.org_id == org_id
    ).all()]
    if child_ids:
        backfill = (
            db.query(AuditEvent)
            .filter(
                AuditEvent.org_id == org_id,
                AuditEvent.entity_type.in_(("run", "molecule")),
                AuditEvent.entity_id.in_(child_ids),
            )
            .order_by(AuditEvent.id.asc())
            .all()
        )
        seen = {r.id for r in rows}
        for r in backfill:
            if r.id not in seen:
                rows.append(r)
        rows.sort(key=lambda r: r.id)

    return rows


def _audit_rows_to_csv_bytes(rows: List[AuditEvent]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "sequence_number", "id", "timestamp", "action", "entity_type",
        "entity_id", "actor", "details_json", "previous_hash", "record_hash",
    ])
    for i, r in enumerate(rows, start=1):
        w.writerow([
            i, r.id, _iso(r.timestamp), r.action, r.entity_type,
            r.entity_id, r.actor,
            json.dumps(r.details, sort_keys=True, default=str) if r.details else "",
            r.previous_hash or "",
            r.record_hash or "",
        ])
    return buf.getvalue().encode("utf-8")


def _verify_campaign_audit_chain(rows: List[AuditEvent]) -> Dict[str, Any]:
    """
    Validate each row's record_hash recomputes correctly from its own fields.

    Note: rows are campaign-scoped; their previous_hash links point at the
    PREVIOUS ORG-WIDE event, not the previous campaign event. So we do NOT
    chain-walk here — we just verify each row hashes its own contents
    correctly. Org-wide chain validity is reported separately via
    verify_cc_audit_chain() in the caller.
    """
    errors = []
    for r in rows:
        expected = compute_audit_hash(
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
# PDF
# ---------------------------------------------------------------------------

def _build_pdf(
    *,
    campaign: Campaign,
    project: Project,
    runs_df: pd.DataFrame,
    molecules_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    audit_rows: List[AuditEvent],
    audit_chain_status: Dict[str, Any],
    audit_csv_sha256: str,
    generated_at: datetime,
) -> bytes:
    """Assemble the multi-page PDF. Lazy-imports reportlab so the module
    imports without it (e.g. during a test collection that doesn't run the
    export path)."""
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
    # compress=0 keeps the content stream uncompressed so external tools
    # (and our own test suite) can grep the document for the audit_log.csv
    # SHA-256 we embed on the Audit Trail page. The size cost is minor — a
    # few extra KB per page — and worth the auditability.
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
    flow.append(Paragraph(f"Project: {_xml_escape(project.name)}", body))
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

    lead = None
    if campaign.lead_molecule_id:
        lead = next((m for m in (
            molecules_df.to_dict(orient="records") if not molecules_df.empty else []
        ) if m.get("molecule_id") == campaign.lead_molecule_id), None)

    summary_rows = [
        ["Status", campaign.status],
        ["Campaign type", campaign.campaign_type],
        ["Run count", str(len(runs_df))],
        ["Molecule count", str(len(molecules_df))],
        ["Metric count", str(len(metrics_df))],
        ["Started", _iso(campaign.started_at) or "—"],
        ["Completed", _iso(campaign.completed_at) or "—"],
        ["Target metric", campaign.target_metric or "—"],
    ]
    if lead:
        summary_rows += [
            ["Lead candidate", lead.get("label") or "—"],
            ["Lead SMILES", lead.get("smiles") or "—"],
        ]

    s_table = Table(summary_rows, colWidths=[1.6 * inch, 4.6 * inch])
    s_table.setStyle(_table_style(header=False))
    flow.append(s_table)
    flow.append(PageBreak())

    # ----- Runs table ------------------------------------------------------
    flow.append(Paragraph("RUNS", eyebrow))
    flow.append(Paragraph(f"Showing {min(len(runs_df), 500)} of {len(runs_df)} runs", h2))
    if not runs_df.empty:
        cols = ["run_id", "molecule_label", "run_kind", "software_name", "software_version", "status", "qc_status", "created_at"]
        header = ["#", "Molecule", "Kind", "Software", "Ver.", "Status", "QC", "Created"]
        data = [header]
        for _, row in runs_df.head(500).iterrows():
            data.append([
                _short(row.get("run_id")),
                _short(row.get("molecule_label"), 22),
                _short(row.get("run_kind"), 14),
                _short(row.get("software_name"), 18),
                _short(row.get("software_version"), 10),
                _short(row.get("status"), 10),
                _short(row.get("qc_status"), 8),
                _short(row.get("created_at"), 19),
            ])
        runs_table = Table(data, repeatRows=1, colWidths=[
            0.5 * inch, 1.5 * inch, 0.9 * inch, 1.1 * inch,
            0.6 * inch, 0.7 * inch, 0.55 * inch, 1.15 * inch,
        ])
        runs_table.setStyle(_table_style(header=True))
        flow.append(runs_table)
        if len(runs_df) > 500:
            flow.append(Spacer(1, 0.08 * inch))
            flow.append(Paragraph(
                f"<i>Truncated to first 500 runs. Full table: <font face='Courier'>runs.csv</font>.</i>",
                body,
            ))
    else:
        flow.append(Paragraph("No runs recorded.", body))
    flow.append(PageBreak())

    # ----- Metrics summary -------------------------------------------------
    flow.append(Paragraph("METRICS", eyebrow))
    flow.append(Paragraph("Distribution across all runs", h2))
    if not metrics_df.empty:
        summary = metrics_df.groupby("metric_name")["value"].agg(["count", "mean", "min", "max"])
        unit_lookup = (
            metrics_df.drop_duplicates(subset=["metric_name"]).set_index("metric_name")["unit"].to_dict()
            if "unit" in metrics_df.columns else {}
        )
        rows = [["Metric", "Unit", "n", "Mean", "Min", "Max"]]
        for name, r in summary.iterrows():
            rows.append([
                name,
                unit_lookup.get(name, ""),
                f"{int(r['count'])}",
                _fmt_num(r["mean"]),
                _fmt_num(r["min"]),
                _fmt_num(r["max"]),
            ])
        m_table = Table(rows, repeatRows=1, colWidths=[
            2.0 * inch, 0.8 * inch, 0.6 * inch, 1.0 * inch, 1.0 * inch, 1.0 * inch,
        ])
        m_table.setStyle(_table_style(header=True))
        flow.append(m_table)
    else:
        flow.append(Paragraph("No metrics recorded.", body))
    flow.append(PageBreak())

    # ----- Audit trail -----------------------------------------------------
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
        for i, ev in enumerate(sample, start=1):
            if ev == "ELLIPSIS":
                data.append(["…", "…", "…", "…", "…", "…"])
                continue
            data.append([
                str(audit_rows.index(ev) + 1),
                _iso(ev.timestamp) or "",
                _short(ev.action, 18),
                _short(f"{ev.entity_type}:{ev.entity_id}", 22),
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
    """ZIP_DEFLATED with sorted entries and a fixed mtime so the archive
    bytes are stable across builds with the same inputs."""
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


def _fmt_num(v: Any) -> str:
    try:
        return f"{float(v):.3g}"
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
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#fafaf6")]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        rules += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#0d0d12")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f1e8")),
        ]
    return TableStyle(rules)
