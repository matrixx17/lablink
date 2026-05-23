# LabLink AI — CLAUDE.md

Lab-data middleware for biotech / life sciences. Two product verticals share
the same backend codebase but ship as separate dashboards:

- **Bioprocess (wet lab)** — `pivot/bioprocess-open-core` — bioreactor runs,
  campaigns/batches/timeseries/offline samples. Dashboard:
  `frontend/wetlab-dashboard`.
- **Comp-chem** — `pivot/compchem-campaigns` — molecule/campaign/run model
  with RDKit + cclib. Dashboard: `frontend/compchem-dashboard`.

Keep the two dashboards verticalized: a wet lab demo must not show comp-chem
UI and vice versa. Merge later.

---

## Mental model

```
Lab file → Edge agent (parsers) → S3/MinIO upload → POST /events
       → schema mapping → QC → DB record → series persistence
       → run/campaign alignment → audit log → webhooks → baseline update
```

Central orchestrator: `process_manifest()` in `services/api/app.py`. 8 sequential
steps; only the DB record write is critical, everything else logs-and-continues.

For run/campaign-centric data: `runs_service.py` persists `MeasurementSeries`
and rebuilds alignment via `timeseries_align.py`. Wet lab campaigns/batches
are separate first-class tables (`campaigns`, `batches`, `timeseries_data`,
`offline_samples`) introduced in migration `003_wetlab`.

## Design patterns to know

**Non-critical failure model.** All steps in `process_manifest()` except the
DB write wrap their logic in try/except, log, continue.

**Parser plugin architecture.** `parsers/__init__.py` registers parsers in
priority order; `GenericCSVParser` is always last. `BaseParser.detect(path)`
decides if a file is theirs.

**Semantic schema mapping.** `OntologyMapper` singleton loads
`ontology/canonical_fields.yaml` and matches column headers via
sentence-transformer cosine similarity. Threshold **0.65** — below that, field
is `"unknown"`.

**Welford's online algorithm.** `baselines.py` maintains running mean/std
without storing history. Baselines only update when QC passes.

**Three independent circuit breakers** (DB / Storage / Webhooks) with
different thresholds in `circuit_breaker.py`.

**Webhook security.** HMAC-SHA256 with timestamp inside the signed payload
(replay protection). Auto-deactivate after 10 consecutive failures.

**Multi-tenancy.** `org_id` filter on every DB query, every S3 key prefix,
every webhook/baseline/audit scope.

**Auth.** API-key header (`X-API-Key`), SHA256-hashed in DB. With
`AUTH_REQUIRED=false` (dev), `org_id` query param is accepted instead.
`resolve_auth()` in `auth.py` is the FastAPI dependency.

**21 CFR Part 11 audit chain.** Each `audit_logs` row hashes
`(timestamp + org_id + action + entity + details + previous_hash)` with
SHA256. `GET /audit/verify` walks the chain.

## Gotchas that have bitten us

**SQLAlchemy reserves `metadata` on the declarative base.** Use
`extra_metadata = Column("metadata", JSONB, ...)` — Python attribute different
from DB column name. Caught when comp-chem layer 2 tried to import the
models; layer 0 had silently broken `import compchem_models`.

**Audit hash chain is fragile across DB drivers.** `datetime.isoformat()`
drops tzinfo differently on SQLite vs Postgres; microsecond precision drifts.
`compute_audit_hash()` must run timestamps through `_canonical_timestamp()`
(UTC normalize, strip tzinfo, truncate to ms) before hashing — both at write
and at verify.

**SQLite test environment can't render JSONB or ARRAY.** Tests patch
`SQLiteTypeCompiler.visit_JSONB` and create only the comp-chem tables
explicitly via `Base.metadata.create_all(tables=[...])`. Don't `create_all()`
the whole metadata or it tries to build the webhooks ARRAY column.

**`*.csv` is gitignored.** Test fixtures need `git add -f`.

**Top-pose outlier detection needs leave-one-out z-scores.** A dramatic
outlier (e.g. -15 against -7.x cluster) inflates the std of the full sample
enough that it never crosses 3σ. Measure the candidate against the **rest**
of the distribution. See `compchem_qc.py`.

## Database schema (current)

Eleven tables, three Alembic migrations: `001_initial`, `002_bioprocess`,
`003_wetlab`. The full table catalog lives in the SQLAlchemy models in
`services/api/database.py` (bioprocess) and `services/api/compchem_models.py`
(on the comp-chem branch). When working from a memory of "I think the column
is X" — verify by reading the model first.

Comp-chem tables are all `cc_` prefixed.

## API surface

Routes prefixed `/api/v1`. Run the API and visit `/docs` for the live
OpenAPI catalog — it is the authoritative source. Major route families:

- Core ingest: `/presign`, `/events`, `/files`, `/formats`
- Bioprocess runs: `/runs/*`
- Wet lab: `/campaigns`, `/campaigns/{id}`, `/campaigns/{id}/batches`,
  `/batches/{id}/timeseries`, `/batches/{id}/samples`
- QC + baselines: `/baselines/*`
- Audit: `/audit`, `/audit/verify`
- Webhooks: `/webhooks/*`
- Health/breakers: `/healthz`, `/health`, `/circuit-breakers/*`
- Auth/compliance: `/auth/keys`, `/compliance/soc2-readiness`
- Comp-chem (other branch): `/campaigns`, `/runs/ingest`, `/molecules/{id}`,
  `/campaigns/{id}/export`, `/audit/verify/{campaign_id}`

Webhook events: `file.ingested`, `file.processed`, `schema.mapped`,
`qc.completed`, `qc.anomaly_detected`.

## Tech stack notes

Stack: FastAPI + SQLAlchemy 2.0 + Alembic + Postgres 16 + MinIO/S3 +
sentence-transformers + RDKit + cclib. React + Vite + Recharts dashboards.
Exact versions live in `services/api/requirements.txt` and the dashboard
`package.json` — check those, don't trust pinned versions in this doc.

## Dev workflow

`make help` lists everything. Most common: `make up`, `make migrate`,
`make logs-api`, `make shell`. For frontend work, `cd frontend/wetlab-dashboard
&& npm run dev`. Vite dev server proxies `/api/v1` to the API container.

Adding a parser: extend `BaseParser`, register in `parsers/__init__.py`
before `GenericCSVParser`. Adding canonical fields: edit
`ontology/canonical_fields.yaml` — re-indexed at startup.

Migrations: change models in `database.py` → `make migrate-new MSG="..."` →
review the generated file → `make migrate`.

## Known gaps

No OAuth2 (only API keys), no rate limiting, no async job queue (webhooks
fire inline), no CI/CD, limited test coverage, no HTTPS (delegated to
reverse proxy), no automated `RunStatus.COMPLETE`/`ARCHIVED` lifecycle.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Append to that file (not this one) when
making major changes.
