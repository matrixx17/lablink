"""CLI entry point — delegates to services/api/wetlab_seed.py."""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "services", "api"))

from database import SessionLocal  # noqa: E402
from wetlab_seed import seed_wetlab_demo  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        result = seed_wetlab_demo(db)
    finally:
        db.close()
    print("Seeded wet lab demo campaign:")
    print(f"  campaign_id: {result['campaign_id']}")
    for name, bid in result["batch_ids"].items():
        print(f"  {name}: {bid}")


if __name__ == "__main__":
    main()
