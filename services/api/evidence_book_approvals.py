"""Approval metadata helpers for Evidence Book verification manifests."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.orm import Session


APPROVED_STATUSES = {"approved", "complete", "completed", "signed", "accepted"}


def collect_campaign_approvals(
    db: Session,
    *,
    campaign_id: Any,
    org_id: str,
    domain: str,
    campaign: Any,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Return approval rows and rollup status without requiring the approval
    workflow branch to be present in this worktree.
    """
    metadata_approvals, metadata_is_approved = _approvals_from_campaign_metadata(campaign)
    table_approvals = _approvals_from_optional_tables(
        db,
        campaign_id=campaign_id,
        org_id=org_id,
        domain=domain,
    )

    approvals = table_approvals or metadata_approvals
    if metadata_is_approved is not None:
        return approvals, bool(metadata_is_approved)
    return approvals, _derive_is_approved(approvals)


def _approvals_from_campaign_metadata(campaign: Any) -> Tuple[List[Dict[str, Any]], bool | None]:
    payloads: List[Dict[str, Any]] = []
    for attr in ("extra_metadata", "extra_params"):
        value = getattr(campaign, attr, None)
        if isinstance(value, dict):
            payloads.append(value)

    approvals: List[Dict[str, Any]] = []
    is_approved: bool | None = None
    for payload in payloads:
        raw_approvals = payload.get("approvals")
        if isinstance(raw_approvals, list):
            approvals = [_normalise_approval(row) for row in raw_approvals if isinstance(row, dict)]
        if "is_approved" in payload:
            is_approved = bool(payload.get("is_approved"))
        elif str(payload.get("approval_status", "")).lower() in APPROVED_STATUSES:
            is_approved = True

    return approvals, is_approved


def _approvals_from_optional_tables(
    db: Session,
    *,
    campaign_id: Any,
    org_id: str,
    domain: str,
) -> List[Dict[str, Any]]:
    bind = db.get_bind()
    try:
        inspector = inspect(bind)
        existing = set(inspector.get_table_names())
    except Exception:
        return []

    candidates = [
        "campaign_approvals",
        "cc_campaign_approvals",
        f"{domain}_campaign_approvals",
    ]
    for table_name in candidates:
        if table_name not in existing:
            continue
        rows = _read_approval_table(
            db,
            table_name=table_name,
            campaign_id=campaign_id,
            org_id=org_id,
            domain=domain,
        )
        if rows:
            return rows
    return []


def _read_approval_table(
    db: Session,
    *,
    table_name: str,
    campaign_id: Any,
    org_id: str,
    domain: str,
) -> List[Dict[str, Any]]:
    try:
        table = Table(table_name, MetaData(), autoload_with=db.get_bind())
    except Exception:
        return []

    columns = table.c
    campaign_col = columns.get("campaign_id")
    if campaign_col is None:
        campaign_col = columns.get("cc_campaign_id")
    if campaign_col is None:
        return []

    stmt = select(table).where(campaign_col == str(campaign_id))
    if "org_id" in columns:
        stmt = stmt.where(columns["org_id"] == org_id)
    if "domain" in columns:
        stmt = stmt.where(columns["domain"] == domain)
    if "created_at" in columns:
        stmt = stmt.order_by(columns["created_at"].asc())
    elif "id" in columns:
        stmt = stmt.order_by(columns["id"].asc())

    try:
        rows = db.execute(stmt).mappings().all()
    except Exception:
        return []

    return [_normalise_approval(dict(row)) for row in rows]


def _normalise_approval(row: Dict[str, Any]) -> Dict[str, Any]:
    def first(*names: str) -> Any:
        for name in names:
            value = row.get(name)
            if value is not None:
                return value
        return None

    status = first("status", "approval_status")
    if status is None and first("approval_meaning", "approved_by_name", "approved_by_user_id"):
        status = "approved"

    return {
        "id": _json_value(first("id", "approval_id")),
        "status": _json_value(status),
        "actor": _json_value(first("actor", "approved_by", "approved_by_name", "reviewer", "signer")),
        "role": _json_value(first("role", "approval_role", "approval_meaning", "reviewer_role")),
        "comment": _json_value(first("comment", "comments", "note", "reason")),
        "created_at": _json_value(first("created_at", "requested_at")),
        "approved_at": _json_value(first("approved_at", "signed_at", "completed_at")),
    }


def _derive_is_approved(approvals: List[Dict[str, Any]]) -> bool:
    if not approvals:
        return False
    statuses = [str(row.get("status") or "").lower() for row in approvals]
    return bool(statuses) and all(status in APPROVED_STATUSES for status in statuses)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
