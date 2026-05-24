#!/usr/bin/env python3
"""Print a summary of tracked demo share links.

Usage:
    python scripts/demo_share_report.py           # all rows
    python scripts/demo_share_report.py opened    # only links that have been opened

The script reads DATABASE_URL from the environment (same as the API). Run
it from the repo root or set PYTHONPATH=services/api.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from repo root without setting PYTHONPATH manually.
SERVICES_API = Path(__file__).resolve().parent.parent / "services" / "api"
if str(SERVICES_API) not in sys.path:
    sys.path.insert(0, str(SERVICES_API))


def main() -> int:
    from database import SessionLocal  # noqa: E402  (sys.path mutation above)
    from demo_share import DemoShareCode  # noqa: E402

    only_opened = len(sys.argv) > 1 and sys.argv[1] == "opened"

    db = SessionLocal()
    try:
        query = db.query(DemoShareCode).order_by(DemoShareCode.created_at.desc())
        if only_opened:
            query = query.filter(DemoShareCode.opened_count > 0)
        rows = query.all()
    finally:
        db.close()

    if not rows:
        print("(no tracked share codes)")
        return 0

    header = f"{'short_code':<12}  {'domain':<8}  {'opens':>5}  {'last_opened':<25}  label"
    print(header)
    print("-" * len(header))
    for row in rows:
        last_opened = row.last_opened_at.isoformat(timespec="seconds") if row.last_opened_at else "—"
        label = (row.label or "").replace("\n", " ")[:60]
        print(f"{row.short_code:<12}  {row.domain:<8}  {row.opened_count:>5}  {last_opened:<25}  {label}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
