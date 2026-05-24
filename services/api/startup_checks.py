"""Lightweight schema checks at API startup."""

import logging
from sqlalchemy import inspect

from database import engine

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("runs", "measurement_series", "api_keys")


def check_bioprocess_schema() -> None:
    """Warn if bioprocess migration has not been applied."""
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        if missing:
            logger.error(
                "Bioprocess tables missing: %s. Run: make migrate",
                ", ".join(missing),
            )
            return
        with engine.connect() as conn:
            cols = {c["name"] for c in inspector.get_columns("files")}
            if "run_id" not in cols:
                logger.error(
                    "files.run_id column missing. Run: make migrate"
                )
    except Exception as e:
        logger.warning("Schema check skipped: %s", e)
