"""Persistent anonymous sessions for public demos.

In-memory dict serves as a read-through cache in front of a `demo_sessions`
Postgres table so demo links survive API restarts. Function signatures are
unchanged; each call opens its own SessionLocal so existing call sites
(middleware, route guards) don't need a db dependency.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import HTTPException
from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.exc import SQLAlchemyError

from database import Base, SessionLocal


DEMO_SESSION_TTL_SECONDS = 2 * 60 * 60
DEMO_ORG_ID = "demo-therapeutics"
DEMO_ROLE = "demo_viewer"
DEMO_ACTOR_PREFIX = "demo:"
DEMO_RESTRICTED_DETAIL = (
    "This action requires an account. In the live product, you can manage "
    "workspace data with role-based audit controls. Request early access ->"
)


class DemoSessionRecord(Base):
    """Persistent demo session row."""

    __tablename__ = "demo_sessions"

    token = Column(String(64), primary_key=True)
    domain = Column(String(16), nullable=False)
    org_id = Column(String(128), nullable=False)
    role = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: _now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_demo_sessions_expires_at", "expires_at"),)


@dataclass(frozen=True)
class DemoSession:
    token: str
    domain: str
    org_id: str
    role: str
    expires_at: datetime


_sessions: Dict[str, DemoSession] = {}
_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dataclass(row: DemoSessionRecord) -> DemoSession:
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return DemoSession(
        token=row.token,
        domain=row.domain,
        org_id=row.org_id,
        role=row.role,
        expires_at=expires_at,
    )


def create_demo_session(domain: str, org_id: str = DEMO_ORG_ID) -> DemoSession:
    if domain not in {"compchem", "wetlab"}:
        raise ValueError("domain must be 'compchem' or 'wetlab'")
    now = _now()
    session = DemoSession(
        token=str(uuid.uuid4()),
        domain=domain,
        org_id=org_id,
        role=DEMO_ROLE,
        expires_at=now + timedelta(seconds=DEMO_SESSION_TTL_SECONDS),
    )
    db = SessionLocal()
    try:
        db.add(
            DemoSessionRecord(
                token=session.token,
                domain=session.domain,
                org_id=session.org_id,
                role=session.role,
                created_at=now,
                expires_at=session.expires_at,
                last_seen_at=now,
            )
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        # Persistent store unavailable; fall through to the in-memory cache so
        # the demo still works on a brand-new DB without the migration applied.
    finally:
        db.close()

    with _lock:
        _purge_expired_locked()
        _sessions[session.token] = session
    return session


def get_demo_session(token: str) -> Optional[DemoSession]:
    if not token:
        return None
    with _lock:
        cached = _sessions.get(token)
    if cached:
        if cached.expires_at <= _now():
            _drop_session(token)
            return None
        return cached

    # Cache miss — read-through from the persistent store.
    db = SessionLocal()
    try:
        row = db.get(DemoSessionRecord, token)
        if not row:
            return None
        session = _to_dataclass(row)
        if session.expires_at <= _now():
            db.delete(row)
            db.commit()
            return None
        # Touch last_seen_at; cheap and useful for the share-tracking pass.
        row.last_seen_at = _now()
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return None
    finally:
        db.close()

    with _lock:
        _sessions[session.token] = session
    return session


def _drop_session(token: str) -> None:
    with _lock:
        _sessions.pop(token, None)
    db = SessionLocal()
    try:
        row = db.get(DemoSessionRecord, token)
        if row:
            db.delete(row)
            db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def _purge_expired_locked() -> None:
    now = _now()
    expired = [token for token, session in _sessions.items() if session.expires_at <= now]
    for token in expired:
        _sessions.pop(token, None)


def purge_expired_persistent_sessions() -> int:
    """Delete expired rows from the persistent store. Called by the
    background reset worker; returns the count of rows removed (0 on
    error or when the table is absent).
    """
    db = SessionLocal()
    try:
        deleted = (
            db.query(DemoSessionRecord)
            .filter(DemoSessionRecord.expires_at <= _now())
            .delete(synchronize_session=False)
        )
        db.commit()
        return int(deleted)
    except SQLAlchemyError:
        db.rollback()
        return 0
    finally:
        db.close()


def demo_actor(token: str) -> str:
    return f"{DEMO_ACTOR_PREFIX}{token}"


def is_demo_actor(actor: str) -> bool:
    return bool(actor and actor.startswith(DEMO_ACTOR_PREFIX))


def is_demo_auth(auth: tuple[str, str]) -> bool:
    return len(auth) >= 2 and is_demo_actor(str(auth[1]))


def reject_demo_write(auth: tuple[str, str], action_description: str = "perform this action") -> None:
    if is_demo_auth(auth):
        raise HTTPException(
            status_code=403,
            detail=(
                "This action requires an account. In the live product, you can "
                f"{action_description}. Request early access ->"
            ),
        )
