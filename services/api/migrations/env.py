"""
Alembic Environment Configuration for LabLink AI

This file configures Alembic to:
- Use environment variables for database connection
- Import all SQLAlchemy models for autogenerate
- Support both online and offline migrations
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import database models and Base
from database import (
    Base, FileRecord, WebhookSubscription, AuditLog, Baseline,
    RunRecord, MeasurementSeries, ApiKey,
)

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLAlchemy MetaData object for 'autogenerate' support
target_metadata = Base.metadata

# ============================================
# Database URL from Environment
# ============================================

def get_database_url() -> str:
    """
    Get database URL from environment variables.

    Uses individual connection parameters or falls back to DATABASE_URL.
    """
    # Check for full DATABASE_URL first
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    # Build from individual components
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "lablink")

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


# ============================================
# Migration Functions
# ============================================

def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Compare types to detect column type changes
        compare_type=True,
        # Compare server defaults
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    # Override sqlalchemy.url with environment variable
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Compare types to detect column type changes
            compare_type=True,
            # Compare server defaults
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# Run appropriate migration function
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
