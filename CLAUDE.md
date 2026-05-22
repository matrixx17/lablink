# LabLink AI — CLAUDE.md

Developer reference for working with this codebase. Update this file whenever major changes are made.

---

## What This Project Does

LabLink AI is a **lab data middleware platform** for biotechnology and life sciences. It sits between physical lab instruments and downstream systems (LIMS, ELNs, dashboards), automating:

- File parsing for multiple instrument formats
- AI-powered semantic schema mapping (sentence-transformer embeddings)
- Quality control with anomaly detection and historical drift monitoring
- Data normalization to standard formats (LabLink Standard Format, Allotrope Simple Model)
- 21 CFR Part 11 compliant tamper-evident audit logging
- Real-time webhook notifications for downstream systems
- **Run-centric bioprocess model**: batch/campaign runs aggregate multiple instrument files with queryable time-series and ASM-first export

---

## Architecture Overview

```
Lab Instrument File
    → Edge Agent (watchdog file watcher)
    → Parse file (bioprocess parsers → Agilent ChemStation → generic CSV fallback)
    → Upload raw file to S3/MinIO
    → POST /events (manifest with run/batch context)
    → API: schema mapping → QC → DB record → series persistence → run alignment → audit log → webhooks → baseline update
    → Downstream systems query /files/{id}/normalized or /runs/{id}/normalized
```

The central orchestrator is `process_manifest()` in `services/api/app.py`. It runs 8 sequential steps; all steps except DB record creation are non-critical (failures log and continue).

For run-centric data, `runs_service.py` handles: creating/linking `RunRecord`s, persisting `MeasurementSeries`, and rebuilding run alignment via `timeseries_align.py`.

---

## Directory Structure

```
lablink/
├── services/api/           # Main FastAPI service
│   ├── app.py              # Routes + process_manifest() orchestrator (~1600 lines)
│   ├── database.py         # SQLAlchemy models + audit hash chaining (now includes RunRecord, MeasurementSeries, ApiKey)
│   ├── mapping.py          # OntologyMapper singleton (embedding-based schema matching)
│   ├── qc.py               # QC checks (z-score, drift, monotonicity, completeness, range)
│   ├── bioprocess_qc.py    # Domain QC rules: VCD growth, DO/pH excursions, titer trajectory
│   ├── bioprocess_routes.py # Run CRUD, series, alignment, auth, compliance, dashboard routes
│   ├── runs_service.py     # Run lifecycle: create/link runs, persist MeasurementSeries, QC
│   ├── timeseries_align.py # Align discrete offline samples onto continuous run timeline
│   ├── auth.py             # API key generation/verification, resolve_auth() FastAPI dependency
│   ├── compliance.py       # SOC 2 readiness checklist endpoint (self-assessment)
│   ├── startup_checks.py   # Warns at startup if bioprocess migration hasn't been applied
│   ├── transform.py        # LSF and ASM format transformations (+ transform_run_to_asm)
│   ├── webhooks.py         # Async webhook delivery with HMAC signing + retry
│   ├── baselines.py        # Welford's online algorithm for baseline stats
│   ├── circuit_breaker.py  # Circuit breaker state machine (Storage, Webhooks, DB)
│   ├── exceptions.py       # Custom exception hierarchy (base: LabLinkError)
│   ├── logging_config.py   # Structured JSON logging with context vars
│   ├── storage.py          # boto3 S3/MinIO client
│   ├── test_qc.py          # QC module tests
│   ├── test_bioprocess.py  # Bioprocess module tests
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── static/dashboard/   # Read-only process scientist dashboard (index.html)
│   └── migrations/
│       └── versions/       # Alembic migration files (002_bioprocess adds runs, measurement_series, api_keys)
├── parsers/                # Instrument file parser plugins
│   ├── __init__.py         # Parser registry (register_parser, detect order)
│   ├── base.py             # BaseParser ABC + ParsedResult dataclass (now includes data_kind, series_points, time_column)
│   ├── generic_csv.py      # Generic CSV parser (always-last fallback)
│   ├── agilent_chemstation.py  # Agilent ChemStation HPLC/GC parser
│   └── bioprocess_platform.py  # Bioprocess parsers: Sartorius, Eppendorf, Cytiva, Nova, Vi-CELL, offline
├── edge/
│   └── agent.py            # Watchdog file watcher, parser orchestration, dead letter queue
├── ontology/
│   └── canonical_fields.yaml   # ~70 canonical lab fields with synonyms for embedding matching
├── tests/
│   └── fixtures/           # biostat_run.csv, offline_titer.csv — sample data for testing
├── docs/
│   └── api.md              # API reference
├── scripts/
│   └── init-db.sql
├── tests/                  # Test directory (limited in MVP)
├── docker-compose.yml      # Dev: api + postgres + minio
├── docker-compose.prod.yml # Prod overrides
├── Makefile                # All dev commands
└── .env.example            # Environment variable template
```

---

## Database Schema

Seven PostgreSQL tables managed by Alembic (two migrations: `001_initial`, `002_bioprocess`):

| Table | Purpose |
|---|---|
| `files` | Processed file records; `schema_guess` and `qc` stored as JSON columns; `run_id` FK to `runs`, `data_kind` (continuous/discrete_offline) |
| `runs` | First-class bioprocess run (batch/campaign); holds `qc` and `alignment` JSONB; unique on (org_id, external_run_id) |
| `measurement_series` | Queryable time-series per field per run; `time_values` and `values` as JSONB arrays; indexed on (run_id, field_name) |
| `api_keys` | Org API keys; stores prefix + SHA256 hash (never plaintext); `active` flag |
| `webhook_subscriptions` | Registered endpoints with event filters, secret, failure_count, active flag |
| `audit_logs` | 21 CFR Part 11 tamper-evident log; each row stores `previous_hash` + `record_hash` (SHA256); new actions: `run_created`, `run_completed` |
| `baselines` | Welford state (mean, std, n, M2) keyed on (org_id, instrument, field_name) — unique constraint |

**Audit hash chaining**: Each `audit_logs` record hashes (timestamp + org_id + action + entity + details + previous_hash) with SHA256. `GET /audit/verify` walks the chain to detect tampering.

**Baseline uniqueness**: `(org_id, instrument, field_name)` — one baseline per field per instrument per org.

**Run alignment**: When a file with a `run_id`/`external_run_id` is processed, `rebuild_run_alignment()` in `runs_service.py` re-aligns all `MeasurementSeries` on the run using `timeseries_align.align_run_series()`. Result is stored as `runs.alignment` JSONB.

---

## API Endpoints

All routes prefixed `/api/v1`. Data isolated by `org_id` query parameter.

### Core

| Method | Path | Description |
|---|---|---|
| `POST` | `/presign` | Get presigned S3 upload URL (1hr expiry, 100MB limit) |
| `POST` | `/events` | Submit file manifest; triggers full pipeline |
| `GET` | `/files` | List processed files for org |
| `GET` | `/files/{id}/normalized` | Get transformed data (`format=lablink` or `format=asm`) |
| `GET` | `/formats` | List available output formats |

### Quality Control

| Method | Path | Description |
|---|---|---|
| `GET` | `/baselines` | All baselines grouped by instrument |
| `GET` | `/baselines/{instrument}/{field}` | Single baseline detail |
| `POST` | `/baselines/reset` | Reset baselines (e.g. post-recalibration) |

### Webhooks

| Method | Path | Description |
|---|---|---|
| `GET` | `/webhooks` | List subscriptions |
| `POST` | `/webhooks` | Create subscription (secret returned once) |
| `DELETE` | `/webhooks/{id}` | Unsubscribe |
| `POST` | `/webhooks/{id}/test` | Test delivery |
| `PATCH` | `/webhooks/{id}/activate` | Reactivate after auto-deactivation |
| `GET` | `/webhooks/events` | List available event types |

### Audit & System

| Method | Path | Description |
|---|---|---|
| `GET` | `/audit` | Query audit logs (date range, action, entity_id) |
| `GET` | `/audit/verify` | Verify audit hash chain integrity |
| `GET` | `/healthz` | Simple health check (for load balancers) |
| `GET` | `/health` | Detailed health (DB + storage connectivity) |
| `GET` | `/circuit-breakers` | Circuit breaker status |
| `POST` | `/circuit-breakers/reset` | Reset all breakers |
| `GET` | `/circuit-breakers/{service}` | Single service status |
| `POST` | `/circuit-breakers/{service}/reset` | Reset single breaker |

**Webhook events**: `file.ingested`, `file.processed`, `schema.mapped`, `qc.completed`, `qc.anomaly_detected`

### Bioprocess Runs (new in `pivot/bioprocess-open-core`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/runs` | Create or get-or-create a run |
| `GET` | `/runs` | List runs for org (filter by status) |
| `GET` | `/runs/{id}` | Get run detail with file count |
| `GET` | `/runs/{id}/series` | All measurement series on a run |
| `POST` | `/runs/{id}/align` | Recompute run alignment + QC |
| `GET` | `/runs/{id}/normalized` | Export run as ASM (only `format=asm` supported) |
| `GET` | `/runs/{id}/audit` | Audit log entries for a run |

### Auth & Compliance (new)

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/keys` | Issue API key (returns raw key once) |
| `GET` | `/compliance/soc2-readiness` | SOC 2 self-assessment checklist |

### Dashboard

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard` | Serves static read-only process scientist UI |

---

## Key Design Patterns

### Non-Critical Failure Model
Steps in `process_manifest()` that are non-critical wrap their logic in try/except, log the error, and continue. Only the DB file record write is critical. Non-critical steps: schema mapping, QC, audit logging, webhooks, baseline updates.

### Parser Plugin Architecture
`BaseParser` ABC with `detect(path)` and `parse(path)` methods. Parsers registered in `parsers/__init__.py` in priority order. New bioprocess parsers (`BioprocessOfflineParser`, `NovaBioProfileParser`, `BeckmanViCellParser`, `SartoriusAmbrParser`, `SartoriusBiostatParser`, `EppendorfBioFloParser`, `CytivaBioreactorParser`) are inserted before `AgilentChemStationParser`; `GenericCSVParser` always last as fallback. Add new parsers with `register_parser()`.

`ParsedResult` now carries `data_kind` (`continuous` | `discrete_offline`), `time_column`, and `series_points` (list of `{t, field, value}` dicts) for DB persistence.

### Semantic Schema Mapping
`OntologyMapper` singleton initialized at startup. Loads `ontology/canonical_fields.yaml` (~70 canonical fields with synonyms), computes sentence-transformer embeddings (model: `all-MiniLM-L6-v2`) for all synonyms, then matches incoming column headers via cosine similarity. Threshold: **0.65** — below this the field is mapped to `"unknown"`.

### Welford's Online Algorithm
`baselines.py` maintains running mean/std without storing historical values. Memory-bounded for long-running systems. Baselines only updated when QC passes (prevents anomalies corrupting reference distributions). Supports parallel combination of two independent Welford states.

### Circuit Breakers
Three independent circuit breakers with different thresholds:

| Service | Failure threshold | Recovery timeout | Half-open calls |
|---|---|---|---|
| Database | 3 | 15s | 1 |
| Storage | 5 | 30s | 2 |
| Webhooks | 10 | 60s | 3 |

### Webhook Security
HMAC-SHA256 signed payloads: `sha256=hmac_sha256(timestamp.payload, secret)`. Timestamp included in signature prevents replay attacks. Auto-deactivates subscription after 10 consecutive failures.

### Multi-Tenancy
Simple org-based isolation: `org_id` in every DB query, every S3 key prefix, and every webhook/baseline/audit scope.

### Authentication (new)
API key authentication via `X-API-Key` header. Keys are SHA256-hashed before storage — plaintext never persisted. Set `AUTH_REQUIRED=true` in production. When `AUTH_REQUIRED=false` (dev default), `org_id` query param is accepted. `resolve_auth()` in `auth.py` is a FastAPI dependency used across all new routes.

### Bioprocess Domain QC (new)
`bioprocess_qc.py` layers domain rules on top of the generic QC engine. Activated automatically when instrument matches `BIOPROCESS_INSTRUMENTS`. Rules: VCD growth profile (early crash, no-decline, stalled growth), DO/pH setpoint excursion, titer trajectory. Domain findings escalate `overall_status` to `warn`/`fail`.

### Run-Centric Model (new)
Files attach to `RunRecord`s via `run_id` or `external_run_id`. `persist_measurement_series()` stores time-series per field in `MeasurementSeries`. `rebuild_run_alignment()` uses `timeseries_align.align_run_series()` to snap discrete offline samples (offline titer, Vi-CELL) onto the continuous controller timeline. Alignment stored as JSONB on the run.

---

## Tech Stack

| Layer | Library | Version |
|---|---|---|
| API framework | FastAPI | 0.115.0 |
| ASGI server | Uvicorn + uvloop | 0.30.6 |
| Validation | Pydantic | 2.9.2 |
| ORM | SQLAlchemy | 2.0.35 |
| Migrations | Alembic | 1.13.1 |
| DB driver | psycopg2-binary | 2.9.9 |
| Object storage | boto3 | 1.35.36 |
| AI/embeddings | sentence-transformers | 3.3.1 |
| Data processing | pandas + numpy | 2.2.3 / 2.1.2 |
| Async HTTP | httpx | 0.27.2 |
| Retry logic | tenacity | 8.2.3 |
| Logging | python-json-logger | 2.0.7 |
| File watching | watchdog | 4.0.1 (edge) |

**Infrastructure**: Docker Compose, PostgreSQL 16, MinIO (dev) / AWS S3 (prod).

---

## Environment Variables

Copy `.env.example` to `.env`. Key variables:

```bash
# API
API_HOST=0.0.0.0
API_PORT=8000
LOG_LEVEL=INFO          # DEBUG | INFO | WARNING | ERROR
JSON_LOGS=true

# PostgreSQL
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=lablink
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# S3/MinIO
S3_ENDPOINT=http://minio:9000       # https://s3.amazonaws.com for AWS
S3_BUCKET=lablink
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_SECURE=false                     # true for AWS

# Webhooks
WEBHOOK_TIMEOUT=10
WEBHOOK_MAX_RETRIES=3
WEBHOOK_DISABLE_AFTER_FAILURES=10

# Optional
SECRET_KEY=                         # for signing
CORS_ORIGINS=                       # comma-separated
SENTRY_DSN=
```

---

## Common Dev Commands

```bash
make init           # First-time setup: copy .env, build, start, migrate
make up             # Start dev environment (docker compose up -d)
make down           # Stop all services
make logs           # Follow all logs
make logs-api       # Follow API logs only
make shell          # Bash into API container
make shell-db       # psql into PostgreSQL

make migrate                        # Apply pending migrations
make migrate-new MSG="description"  # Create new Alembic migration
make migrate-down                   # Rollback one migration
make migrate-history                # Show migration history

make test           # Run pytest
make test-cov       # Run pytest with coverage

make edge-test      # Drop a sample CSV into sample_data/incoming/
make health         # curl /api/v1/health with pretty print

make up-prod        # Start with prod overrides
make reset          # Full reset — DELETES ALL DATA
```

---

## Adding a New Instrument Parser

1. Create `parsers/your_instrument.py` extending `BaseParser`
2. Implement `detect(path) -> bool` (check file extension and/or header content)
3. Implement `parse(path) -> ParsedResult`
4. Register it in `parsers/__init__.py` before `GenericCSVParser`:
   ```python
   from .your_instrument import YourInstrumentParser
   register_parser(YourInstrumentParser())
   ```

---

## Adding New Canonical Fields

Edit `ontology/canonical_fields.yaml`. Each entry needs a canonical name and a list of synonyms. The `OntologyMapper` re-indexes at startup — no code changes needed.

---

## Running Migrations

```bash
# After changing SQLAlchemy models in database.py:
make migrate-new MSG="add your column"
# Review the generated file in services/api/migrations/versions/
make migrate
```

---

## Computational Chemistry Data Model (pivot/compchem-campaigns)

Layer 0 — data model only. No UI or parsing yet.

### Entity Hierarchy

```
Organization (org_id — no table, inherited from auth)
└── Project  (cc_projects)       — drug target or program (e.g. EGFR kinase)
    └── Campaign  (cc_campaigns) — lead-opt or screening campaign (PRIMARY OBJECT)
        ├── Run  (cc_runs)       — single simulation / calculation job
        │   ├── RunInput   (cc_run_inputs)    — input files, configs, forcefields
        │   ├── RunOutput  (cc_run_outputs)   — trajectories, result files, logs
        │   ├── RunMetric  (cc_run_metrics)   — scalar results with mandatory units
        │   └── RunLineage (cc_run_lineage)   — parent→child run DAG (provenance)
        └── Molecule  (cc_molecules)          — chemical entity, deduplicated by InChIKey
            ├── MoleculeProperty (cc_molecule_properties) — MW, LogP, TPSA, …
            └── AssayResult      (cc_assay_results)       — links RunMetrics to molecules
```

Plus `cc_audit_events` — tamper-evident hash-chain log (same SHA256 pattern as `audit_logs`).

All tables prefixed `cc_` to coexist with lab-instrument tables in the same schema.

### Key Design Decisions

- **Campaign is the first-class object.** Files are events; molecules accumulate runs over weeks.
- **InChIKey deduplication.** `canonical_smiles` is always computed by RDKit at ingest. `inchi_key` is the unique key within `(org_id, campaign_id)`. `smiles_provided` stores the original as submitted (audit trail).
- **Units are mandatory on every metric.** `RunMetric.unit` is `NOT NULL`. Mixed units (kcal/mol vs kJ/mol) surface at query time, never silently averaged.
- **Reproducibility fingerprint.** `Run` carries `software_name`, `software_version`, `forcefield`, `config_hash` (SHA256 of full parameter set), `cli_args`, and `compute_environment` as first-class columns.
- **Run provenance DAG.** `RunLineage` tracks parent→child dependencies (docking pose → MD input → FEP) without embedding graph logic in the run table.
- **Separate audit chain.** `cc_audit_events` is independent of `audit_logs` so lab-instrument and comp-chem audit chains can be verified independently.
- **`AssayResult` is the accumulation join.** To query "everything computed for scaffold X" join `cc_molecules → cc_assay_results → cc_run_metrics → cc_runs`.

### New File

| File | Purpose |
|---|---|
| `services/api/compchem_models.py` | All SQLAlchemy models + `log_cc_audit()` + `verify_cc_audit_chain()` |
| `migrations/versions/20260522_000001_compchem_campaigns.py` | Migration `003_compchem` (revises `002_bioprocess`) |

### What's Still Missing (Layer 2+)

- SMILES canonicalisation at ingest (requires RDKit in the API container)
- FastAPI routes for campaign/run/molecule CRUD (`POST /api/v1/cc/events` is the agent target)
- Molecule registration service (dedup by InChIKey, property calculation)
- Run submission + status polling
- Campaign export (IND / partner data room bundle)
- Scaffold query ("show me all runs for molecules matching substructure X")

---

## Comp-Chem Layer 1 — Ingestion (pivot/compchem-campaigns)

Watcher agent + parsers + campaign-aware classification. Files in `parsers/compchem/` and `edge/compchem_agent.py`.

### Key Conceptual Shift

For the existing lab-instrument agent, a file's identity is in its bytes (Agilent header, Sartorius columns). For comp-chem, **the file's bytes don't say which campaign it belongs to** — a GROMACS `.xtc` looks the same whether it's lead-opt round 3 against EGFR or fragment screening against JAK2. So the agent needs an out-of-band context signal *before* it can post a meaningful manifest.

**MVP solution: `.lablink.yaml` config files.** The scientist drops one into their project root specifying `org_id`, `project`, `campaign`, optionally `molecule_smiles`, and run defaults. The agent walks up from each new file's directory until it finds the closest ancestor `.lablink.yaml` and uses that as context. Files with no resolvable context are moved to `.unclassified/` rather than uploaded — silent ingestion of context-less files is the failure mode the campaign model exists to prevent.

CLI tagging and directory-structure inference were considered but explicitly skipped for MVP (too magical, not auditable enough).

### New Files

| File | Purpose |
|---|---|
| `parsers/compchem/base.py` | `CompChemParser` ABC + `CompChemParsedResult` dataclass + `RunKind` / `TerminationStatus` enums. `CompChemMetric` has mandatory `unit`. |
| `parsers/compchem/__init__.py` | Registry (DFT → docking → MD → property tables). Unknown files return a stub result so raw bytes still upload. |
| `parsers/compchem/gaussian_orca.py` | Gaussian + ORCA log parsers via cclib (fallback regex when cclib missing). Extracts final energy (Hartree + kcal/mol), method, basis, convergence, termination. |
| `parsers/compchem/glide.py` | Schrödinger Glide `_dock.log` + `_pv.maegz` parsers. Binary maegz inherits metrics from sibling log. |
| `parsers/compchem/vina_gnina.py` | AutoDock Vina (`.pdbqt`, `.log`) + Gnina (`.log`, `.sdf` with CNNscore). Best binding affinity + per-pose rank metrics. |
| `parsers/compchem/gromacs.py` | GROMACS `.log` parser (definitive) + `.gro` / `.tpr` / `.xtc` / `.trr` / `.edr` recognised as artifacts inheriting log metadata. |
| `parsers/compchem/openmm.py` | OpenMM StateDataReporter CSV + output `.pdb` parsers; `.dcd` / `.h5` recognised as trajectory artifacts. |
| `parsers/compchem/rdkit_table.py` | RDKit property table (CSV/TSV/SDF) parser — detects via SMILES column + ≥2 known descriptor columns. |
| `edge/campaign_context.py` | `CampaignContextResolver`: walks up to find `.lablink.yaml`, caches by mtime, thread-safe. |
| `edge/compchem_agent.py` | Watchdog-based agent. Context-mandatory, SHA256-hashed, posts to `POST /api/v1/cc/events`. Includes `--dry-run` mode for testing without the API. |
| `parsers/compchem/test_compchem_parsers.py` | Smoke tests for parsers + context resolver. Run with `python parsers/compchem/test_compchem_parsers.py`. |
| `tests/fixtures/compchem/EGFR-program-2026/lead_opt_round_3/` | Sample `.lablink.yaml` + Vina pdbqt + RDKit property CSV fixtures. |

### Dependencies Added

`edge/requirements.txt` now includes:
- `PyYAML==6.0.2` — for `.lablink.yaml`
- `cclib==1.8.1` — Gaussian / ORCA / many QM packages, do not reimplement

### Detection / Parse Behaviour Highlights

- **Mandatory units on every metric.** `CompChemMetric.unit` is non-optional; DFT energies are emitted in both source unit (Hartree, eV) and kcal/mol so cross-job comparison surfaces unit mismatches at query time.
- **Termination status is first-class.** `NORMAL` / `UNCONVERGED` / `CRASHED` / `PARTIAL` / `UNKNOWN`. An unconverged DFT is uploaded with the result, not silently dropped.
- **Streaming SHA256.** Computed in 1MB chunks so multi-GB trajectories don't blow memory.
- **Binary artifacts inherit log metadata.** A standalone `.xtc` finds its sibling `.log` and copies software version / termination so the upload record is queryable.
- **Parsers never raise on malformed input** — they return a result with `parse_warnings` and `termination_status=UNKNOWN` so the raw bytes still get uploaded for forensic value.

### Running the Agent

```bash
# Install agent deps (one-time)
cd edge && pip install -r requirements.txt && cd ..

# Watch a project directory, dry-run mode (no API needed)
python edge/compchem_agent.py --watch /path/to/projects --dry-run --debug

# Real mode with API key (Layer 2 endpoint must exist)
python edge/compchem_agent.py \
  --watch /path/to/projects \
  --api http://localhost:8000 \
  --api-key llk_xxxxx
```

### Manifest Schema (POSTed to `/api/v1/cc/events`)

```json
{
  "s3_key": "data/acme-pharma/dock_LL042_out.pdbqt",
  "filename": "dock_LL042_out.pdbqt",
  "file_size_bytes": 612,
  "file_hash": "<sha256 hex>",
  "parser_name": "autodock_vina",
  "artifact_role": "metric_source",
  "parsed": { /* CompChemParsedResult.to_manifest() */ },
  "agent_timestamp": "2026-05-22T15:58:10Z",
  "org_id": "acme-pharma",
  "project": "EGFR-program-2026",
  "campaign": "lead_opt_round_3",
  "molecule_smiles": "Cc1ccc(cc1)C(=O)Nc2ccncc2",
  "molecule_name": "LL-042",
  "software_name": "AutoDock Vina",
  "forcefield": "AMBER ff19SB",
  "compute_environment": "hpc_slurm",
  "context_source": "/path/to/.lablink.yaml"
}
```

The agent currently treats HTTP 404/501 from `/cc/events` as "OK, endpoint not deployed yet" so Layer 1 is operable before Layer 2 lands.

---

## Comp-Chem Layer 3 — Chemistry-aware QC (pivot/compchem-campaigns)

Layered on the same generic engine (`qc.py`) used by lab-instrument and bioprocess flows. Same `pass / warn / fail` severity model, same per-field finding shape — but operating on a single comp-chem run rather than a time-series of files.

### New File

`services/api/compchem_qc.py` — `compchem_qc_summary()` is the top-level entry. Takes a `CompChemParsedResult.to_manifest()` dict + optional historical baselines / molecule SMILES / MD or DFT extras / per-project thresholds, returns the same shape as `bioprocess_qc_summary`:

```python
{
  "qc_mode": "compchem",
  "qc_flags":      { ... generic per-metric findings ... },
  "domain_findings":[ {rule, severity, message, details}, ... ],
  "overall_status": "pass" | "warn" | "fail",
  "summary": "...",
  "thresholds_used": { ... },
}
```

### Generic Checks Reused

All five generic engines apply to the run's metric list:
- Z-score anomalies (esp. on the synthesised `pose_scores` aggregate field)
- Historical drift vs. campaign baselines (e.g. best_binding_affinity drift)
- Monotonicity / discontinuity (on MD PE time-series fed via `expected_ranges`)
- Completeness (truncated metric arrays)
- Range validation

### Domain Checks Added

**Simulation stability (MD):**
| Rule | Severity | Trigger |
|---|---|---|
| `termination_crashed` | fail | `termination_status == "crashed"` |
| `termination_unconverged` | fail | `termination_status == "unconverged"` |
| `termination_partial` | warn | `termination_status == "partial"` |
| `rmsd_early_drift` | fail | Cα RMSD > 5 Å in first 20% of trajectory |
| `energy_variance_excess` | warn | post-equilibration PE std > 2× baseline std (first 10% = baseline) |
| `pbc_unwrap_failure` | fail | max atom coord exceeds box dimension |

**Docking validity:**
| Rule | Severity | Trigger |
|---|---|---|
| `top_pose_score_outlier` | warn | top pose >3σ better than rest of distribution (leave-one-out z; not full-sample z, which dilutes single-outlier signals in small N) |
| `docking_score_collapse` | fail | all poses within 0.1 kcal/mol — docker failed to discriminate |
| `top_pose_unparseable` / `top_pose_sanitize_failed` | fail | RDKit cannot parse or sanitize the docked SMILES |

**DFT convergence:**
| Rule | Severity | Trigger |
|---|---|---|
| `scf_excess_cycles` | warn | SCF cycles > 200 |
| `minimum_has_imaginary` | fail | structure expected to be a minimum has imaginary modes |
| `ts_multiple_imaginary` | fail | TS expected to have exactly 1 imaginary mode |
| `bsse_not_corrected` | warn | binding-energy metric present, no counterpoise/BSSE marker |

**Property range (RDKit-based):**
| Rule | Severity | Trigger |
|---|---|---|
| `lipinski_veber_violations` | warn (1–2) / fail (3+) | MW≤500, LogP≤5, HBD≤5, HBA≤10, RotB≤10, TPSA≤140 (configurable per project) |
| `pains_alert` | warn | PAINS substructure detected via `RDKit FilterCatalog` |

### Thresholds Are Per-Project Overridable

Every numeric threshold is in `DEFAULT_THRESHOLDS` and deep-merged with the `thresholds=` argument so a campaign can tighten or loosen any rule without touching code. E.g.:

```python
compchem_qc_summary(parsed, thresholds={"rmsd_drift_A": 3.5,
                                         "lipinski": {"mw_max": 600}})
```

### Graceful RDKit Fallback

PAINS, structural sanity, and SMILES-driven Lipinski require RDKit. If RDKit is not installed, those checks emit a `rdkit_unavailable` warning rather than crashing — the agent and API still function. `services/api/requirements.txt` adds `rdkit==2024.3.5` so the production container has it; the edge agent's `requirements.txt` does NOT add it (heavy dep, agent runs on scientist laptops).

### Wired Into the Agent (Layer 1)

`edge/compchem_agent.py` now runs `compchem_qc_summary` against every parsed file before upload, logs PASS/WARN/FAIL inline, and includes the result as `client_qc` in the manifest. This is *advisory* — the server is the source of truth; the client QC gives the scientist a fast local signal when something is obviously wrong before bandwidth is spent.

### Tests

`services/api/test_compchem_qc.py` — **46 tests, all passing**. Covers every rule's positive and negative case (e.g. `test_rmsd_early_spike_is_fail` vs `test_rmsd_late_spike_does_not_trigger_early_check`), plus integration tests through `compchem_qc_summary` for the full crashed-run / score-collapse / unconverged-DFT / RMSD-spike / drift paths.

Run with: `cd services/api && python test_compchem_qc.py`

---

## What's Not Here Yet (Known Gaps)

- **Authentication** — API key infra added (`auth.py`); set `AUTH_REQUIRED=true` in production; no OAuth2
- **Rate limiting** — noted as optional in config but not implemented
- **Async job queue** — webhook delivery fires inline; no Celery/Redis
- **Comprehensive tests** — `test_qc.py` and `test_bioprocess.py` exist; no integration or e2e tests
- **CI/CD pipeline** — not configured
- **HTTPS** — delegates to reverse proxy
- **Run completion lifecycle** — `RunStatus.COMPLETE` / `ARCHIVED` transitions not yet automated; must be set manually

---

## Changelog

| Date | Change |
|---|---|
| 2026-05-22 | Comp-chem Layer 3: chemistry-aware QC (`compchem_qc.py`) — MD stability, docking validity, DFT convergence, Lipinski/Veber + PAINS; 46 tests passing; wired into agent as `client_qc` |
| 2026-05-22 | Comp-chem Layer 1: watcher agent (`edge/compchem_agent.py`), 7 parsers (GROMACS, OpenMM, Vina, Gnina, Glide, Gaussian, ORCA, RDKit tables), `.lablink.yaml` context resolver, smoke tests; SHA256 hashing + context-mandatory ingest |
| 2026-05-22 | Comp-chem Layer 0: Project/Campaign/Run/Molecule/AssayResult data model + migration 003 |
| 2026-05-22 | Bioprocess pivot: runs, measurement_series, API keys, bioprocess parsers, domain QC, dashboard, auth, SOC 2 compliance endpoint; bug fixes in runs_service, timeseries_align, migration |
| 2026-05-21 | Initial CLAUDE.md created from full codebase scan |
