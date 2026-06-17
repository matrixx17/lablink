"""
API key authentication and SOC 2 readiness helpers.
"""

import hashlib
import os
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from database import ApiKey, SessionLocal
from demo_sessions import DEMO_ORG_ID, demo_actor, get_demo_session

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true").lower() == "true"
BOOTSTRAP_API_KEY = os.getenv("LABLINK_BOOTSTRAP_API_KEY", "")


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> Tuple[str, str, str]:
    """Return (raw_key, prefix, hash)."""
    raw = f"llk_{secrets.token_urlsafe(32)}"
    prefix = raw[:12]
    return raw, prefix, hash_api_key(raw)


def create_api_key(org_id: str, name: str, db: Session) -> Tuple[ApiKey, str]:
    raw, prefix, key_hash = generate_api_key()
    record = ApiKey(
        org_id=org_id,
        name=name,
        key_prefix=prefix,
        key_hash=key_hash,
        active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record, raw


def verify_api_key(raw_key: str, db: Session) -> Optional[ApiKey]:
    if not raw_key:
        return None
    key_hash = hash_api_key(raw_key)
    record = (
        db.query(ApiKey)
        .filter(ApiKey.key_hash == key_hash, ApiKey.active.is_(True))
        .first()
    )
    if record:
        record.last_used_at = datetime.now(timezone.utc)
        db.commit()
    return record


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def resolve_auth(
    api_key: Optional[str] = Security(API_KEY_HEADER),
    authorization: Optional[str] = Header(None),
    org_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Tuple[str, str]:
    """
    Resolve (org_id, actor) from API key or bootstrap/dev mode.

    When AUTH_REQUIRED=false, org_id query param is accepted (legacy dev).
    When AUTH_REQUIRED=true, valid X-API-Key is mandatory.
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "demo" and token:
            session = get_demo_session(token.strip())
            if not session:
                raise HTTPException(status_code=401, detail="Demo session expired. Restart the demo.")
            if org_id and org_id != session.org_id:
                raise HTTPException(status_code=403, detail="org_id does not match demo session scope")
            return session.org_id, demo_actor(session.token)

    if api_key:
        record = verify_api_key(api_key, db)
        if record:
            return record.org_id, f"api-key:{record.name}"

    if BOOTSTRAP_API_KEY and api_key == BOOTSTRAP_API_KEY:
        return org_id or "default-org", "bootstrap-key"

    if AUTH_REQUIRED:
        raise HTTPException(
            status_code=401,
            detail="Valid X-API-Key required. Set AUTH_REQUIRED=false for local dev.",
        )

    return org_id or "default-org", "anonymous-dev"


def require_org_access(requested_org: str, authenticated_org: str) -> None:
    if requested_org != authenticated_org:
        raise HTTPException(status_code=403, detail="org_id does not match API key scope")
