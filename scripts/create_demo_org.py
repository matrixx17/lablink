"""Create the demo organization and admin user.

Uses the same SQLAlchemy models, session factory, and password hashing
already wired into services/api. Idempotent: re-runs reuse the existing
org and skip the user if it already exists.
"""

from __future__ import annotations

import os
import sys
import uuid

# Make services/api importable (bare imports like `compchem_models`, `database`
# match how demo_seed.py is structured).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "services", "api"))

from database import SessionLocal  # noqa: E402
from compchem_models import Organization, OrgUser  # noqa: E402
from demo_seed import hash_demo_password  # noqa: E402

ORG_ID = "demo-therapeutics"
ORG_NAME = "Demo Therapeutics"
ADMIN_EMAIL = "demo@lablink.io"
ADMIN_PASSWORD = "LabLinkDemo2024"


def main() -> None:
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.name == ORG_NAME).first()
        if not org:
            org = db.query(Organization).filter(Organization.org_id == ORG_ID).first()
        if not org:
            org = Organization(org_id=ORG_ID, name=ORG_NAME, demo_mode=True)
            db.add(org)
            db.commit()
            db.refresh(org)
        else:
            org.demo_mode = True
            if not org.name:
                org.name = ORG_NAME
            db.commit()
            db.refresh(org)

        existing_user = (
            db.query(OrgUser)
            .filter(OrgUser.org_id == org.org_id, OrgUser.email == ADMIN_EMAIL)
            .first()
        )
        if not existing_user:
            db.add(OrgUser(
                id=str(uuid.uuid4()),
                org_id=org.org_id,
                email=ADMIN_EMAIL,
                password_hash=hash_demo_password(ADMIN_PASSWORD),
                is_admin=True,
            ))
            db.commit()

        print(f"Demo org created: {org.name}")
        print(f"Org ID: {org.org_id}")
        print(f"Admin user: {ADMIN_EMAIL}")
        print(f"Password: {ADMIN_PASSWORD}")
        print(f"Demo mode: {bool(org.demo_mode)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
