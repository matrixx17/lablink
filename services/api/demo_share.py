"""Persistent demo share-code store.

Each call to GET /demo/share with a `label` mints a tracked short_code.
GET /demo/share without a label returns the same untracked URL it always
has, so existing share buttons keep working unchanged.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.exc import SQLAlchemyError

from database import Base, SessionLocal


SHORT_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o/1/l
SHORT_CODE_LENGTH = 10


class DemoShareCode(Base):
    """Tracked share-link record."""

    __tablename__ = "demo_share_codes"

    short_code = Column(String(24), primary_key=True)
    domain = Column(String(16), nullable=False)
    label = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: _now())
    opened_count = Column(Integer, nullable=False, default=0)
    last_opened_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_demo_share_codes_created_at", "created_at"),)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mint_code() -> str:
    return "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH))


def create_share_code(domain: str, label: Optional[str]) -> Optional[str]:
    """Persist a new short_code. Returns None if the DB is unavailable
    (caller should fall back to the untracked URL)."""
    if domain not in {"compchem", "wetlab", "both"}:
        raise ValueError("domain must be compchem, wetlab, or both")
    db = SessionLocal()
    try:
        # Retry a couple of times in the (vanishingly unlikely) case of a
        # short_code collision — the alphabet × length gives ~10^15 keys.
        for _ in range(4):
            code = _mint_code()
            if not db.get(DemoShareCode, code):
                db.add(DemoShareCode(
                    short_code=code,
                    domain=domain,
                    label=(label or None),
                    created_at=_now(),
                    opened_count=0,
                ))
                db.commit()
                return code
        return None
    except SQLAlchemyError:
        db.rollback()
        return None
    finally:
        db.close()


def record_open(short_code: str) -> bool:
    """Increment opened_count and stamp last_opened_at. Returns True if the
    short_code existed."""
    if not short_code:
        return False
    db = SessionLocal()
    try:
        row = db.get(DemoShareCode, short_code)
        if not row:
            return False
        row.opened_count = (row.opened_count or 0) + 1
        row.last_opened_at = _now()
        db.commit()
        return True
    except SQLAlchemyError:
        db.rollback()
        return False
    finally:
        db.close()
