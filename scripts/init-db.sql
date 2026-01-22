-- ============================================
-- LabLink AI - Database Initialization Script
-- ============================================
-- This script runs when PostgreSQL container starts
-- for the first time (empty data volume).

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search

-- Grant permissions (for production, create a limited user)
-- CREATE USER lablink_app WITH PASSWORD 'app_password';
-- GRANT CONNECT ON DATABASE lablink TO lablink_app;
-- GRANT USAGE ON SCHEMA public TO lablink_app;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO lablink_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lablink_app;

-- Note: Tables are created by SQLAlchemy/Alembic migrations
-- This script is for database-level setup only
