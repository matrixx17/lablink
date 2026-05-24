# LabLink AI

**Campaign-centric record system for biotech R&D — wet-lab bioprocess and computational chemistry.**

LabLink started as a generic lab-data normalization pipeline. It pivoted into something more focused: a system of record for *campaigns* — the multi-week, multi-file, multi-stakeholder workflows that actually drive decisions in a lab. Files are events; the campaign is the durable object that auditors, partners, and ML pipelines query.

The platform ships in two parallel verticals, each on its own branch:

| Vertical | Branch | Object of record | What it ingests |
|---|---|---|---|
| **Wet-lab bioprocess** | [`pivot/bioprocess-open-core`](https://github.com/vedantajn/lablink/tree/pivot/bioprocess-open-core) | A bioprocess **run** (batch/campaign) | Sartorius / Eppendorf / Cytiva controllers, Nova / Vi-CELL offline analyzers |
| **Computational chemistry** | [`pivot/compchem-campaigns`](https://github.com/vedantajn/lablink/tree/pivot/compchem-campaigns) | A drug-discovery **campaign** | GROMACS, OpenMM, AutoDock Vina, Gnina, Glide, Gaussian, ORCA, RDKit property tables |

Both verticals share the same architectural spine — edge agent → parsers → API → Postgres + S3 → hash-chained audit log → webhooks/dashboard. They diverge in their data model (continuous time-series vs. molecule-keyed metric accumulation) and in their domain QC.

---

## Why "campaigns" instead of "files"?

A single Sartorius `.csv` or a single GROMACS `.log` tells you almost nothing in isolation. The questions a scientist actually has are:

- "Show me every run on EGFR lead-opt round 3 and the best docking score per molecule." (compchem)
- "How did titer trajectory in this fed-batch run compare to the last five at the same scale?" (bioprocess)
- "Prove to the regulator that the IND filing's binding-affinity number came from this exact docked pose, on this protein structure, with this software version, signed off by this scientist on this date." (both)

Campaign-level identity — projects, campaigns, runs, molecules, audit chains — is the only data model where those questions have crisp answers.

---

## Architecture (shared spine)

```
Lab instrument or simulation
    → Edge agent (watchdog file watcher)
    → Parser plugin (vertical-specific)
    → Upload raw bytes to S3 / MinIO
    → POST manifest to API
    → API: persist record + metric series, run domain QC, append hash-chained audit event, fan out webhooks
    → Frontend dashboard / downstream systems query campaign or run
```

Central orchestration lives in `services/api/`. The edge agents live in `edge/`. Parsers are plugins in `parsers/` registered in priority order — see `CLAUDE.md` for the plugin contract.

---

## Vertical 1 — Wet-lab bioprocess (`pivot/bioprocess-open-core`)

### What it does

Aggregates **multiple instrument files into a single bioprocess run** (batch / campaign), aligns discrete offline samples (Vi-CELL viability, offline titer) onto the continuous controller timeline, and exports the whole thing as ASM (Allotrope Simple Model) for downstream systems.

### Key entities

- **Run** — first-class bioprocess run. Holds aggregated QC + the per-field alignment JSON.
- **MeasurementSeries** — queryable time-series, one row per field per run.
- **ApiKey** — per-org API key (SHA256-hashed, prefix shown once).
- **AuditLog** — 21 CFR Part 11 hash chain with new actions `run_created` / `run_completed`.

### Parsers included

`SartoriusAmbrParser`, `SartoriusBiostatParser`, `EppendorfBioFloParser`, `CytivaBioreactorParser`, `NovaBioProfileParser`, `BeckmanViCellParser`, plus a generic offline-sample parser. `AgilentChemStationParser` and `GenericCSVParser` remain as fallbacks.

### Domain QC

`bioprocess_qc.py` layers on top of the generic QC engine:
- VCD growth profile (early crash, no-decline, stalled growth)
- DO / pH setpoint excursion
- Titer trajectory

### Where to look

- `services/api/bioprocess_routes.py` — run CRUD, series, alignment, auth, SOC 2 readiness, dashboard
- `services/api/runs_service.py` — run lifecycle + series persistence
- `services/api/timeseries_align.py` — discrete-onto-continuous alignment
- `services/api/static/dashboard/` — read-only process-scientist dashboard

---

## Vertical 2 — Computational chemistry (`pivot/compchem-campaigns`)

### What it does

Aggregates docking, MD, FEP, and DFT runs across thousands of molecules into a single **campaign** (e.g. "EGFR lead-opt round 3"), deduplicates molecules by InChIKey, tracks reproducibility fingerprints (software version, forcefield, config hash), maintains a parent→child run DAG for provenance, and produces ML-ready exports.

### Key entities

```
Organization
└── Project              (drug target / program)
    └── Campaign         (PRIMARY OBJECT — lead-opt, screening, FEP, …)
        ├── Run          (one simulation job)
        │   ├── RunInput / RunOutput   (file artifacts)
        │   ├── RunMetric              (scalar results, unit MANDATORY)
        │   └── RunLineage             (parent→child DAG)
        └── Molecule     (deduped by InChIKey)
            ├── MoleculeProperty       (MW, LogP, TPSA, …)
            └── AssayResult            (joins RunMetrics to molecules)
```

Plus `cc_audit_events` — independent hash chain, so the compchem audit history can be verified separately from the wet-lab one.

### Parsers included

`parsers/compchem/` — Gaussian, ORCA, Glide, AutoDock Vina, Gnina, GROMACS, OpenMM, RDKit property tables. Binary artifacts (`.xtc`, `.dcd`, `.maegz`) inherit metadata from sibling log files. Streaming SHA256 in 1 MB chunks so multi-GB trajectories don't blow memory.

### Domain QC

`compchem_qc.py` covers:
- **MD stability** — termination status, Cα RMSD early drift, post-equilibration energy variance, PBC unwrap failure
- **Docking validity** — top-pose outlier (leave-one-out z), score collapse, unparseable SMILES
- **DFT convergence** — excess SCF cycles, missing/extra imaginary modes, BSSE-uncorrected binding energies
- **Property range** — Lipinski / Veber, PAINS substructure alerts (RDKit)

### Where to look

- `services/api/compchem_routes.py` — campaigns, run ingest, molecules, exports, audit verify, demo endpoints
- `services/api/compchem_ingest.py` — manifest → DB persistence with RDKit canonicalisation
- `services/api/demo_seed.py` — curated EGFR lead-opt demo dataset (10 molecules, 17 runs, full audit trail)
- `edge/compchem_agent.py` — watchdog agent + `.lablink.yaml` campaign context resolver
- `frontend/compchem-dashboard/` — React/Vite dashboard

---

## Running the comp-chem demo

The cleanest end-to-end path through the platform. Everything below is from the `pivot/compchem-campaigns` branch.

### Prerequisites

- Docker + Docker Compose v2
- Make (optional)
- Ports 3000 (frontend), 8000 (API), 5432 (Postgres), 9000/9001 (MinIO) free

### 1. Configure

```bash
git checkout pivot/compchem-campaigns
cp .env.example .env
```

Edit `.env` and set at minimum:
```bash
DEMO_RESET_SECRET=<pick-a-secret>
JWT_SECRET=<long-random-string>
ENVIRONMENT=development
```

### 2. Bring it up

```bash
docker compose up -d postgres minio
docker compose run --rm api alembic upgrade head
docker compose up -d --build api frontend
```

Verify:
```bash
curl http://localhost:8000/health        # {"status": "ok"}
open http://localhost:3000/demo          # demo entry page
```

### 3. Seed the demo dataset

A single API call seeds the demo org, admin user, EGFR lead-opt campaign, 10 molecules, 17 runs, and full audit trail. It's idempotent — calling it again resets to the same canonical state:

```bash
curl -X POST http://localhost:8000/api/v1/demo/login
```

Demo credentials:
- Email: `demo@lablink.io`
- Password: `LabLinkDemo2024`
- Org: `demo-therapeutics`

### 4. Click through the demo

From [http://localhost:3000/demo](http://localhost:3000/demo), enter the demo. You should see:

- The campaign overview with a green **"Lead Nominated"** status badge
- An **AC-007** lead-candidate card with a 2D structure (RDKit-rendered)
- "Delivered by Bio Labs on May 22, 2026" delivery info
- A timeline of audit events (CRO delivery → docking runs → MD → DFT → lead nomination)
- The SAR explorer, the molecule detail page, the audit trail, the methods export

### 5. Pre-call reset ritual

Before a live demo, wipe state so the previous walkthrough doesn't pollute it:

```bash
curl -X POST http://localhost:8000/demo/reset \
  -H "X-Demo-Reset-Secret: $DEMO_RESET_SECRET"
curl http://localhost:8000/health
```

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Browser shows `502 Bad Gateway` after clicking "Enter Demo" | Frontend container can't reach API. Ensure `VITE_API_PROXY_TARGET=http://api:8000` is set in `docker-compose.yml`. |
| Molecule pages say "RDKit unavailable" | RDKit's drawing libs (`libXrender`, `libexpat1`, `libfontconfig1`, `libfreetype6`) missing from API image. Rebuild with `docker compose build --no-cache api`. |
| 401 on `/demo/reset` | Shell `$DEMO_RESET_SECRET` is empty. It's set in `.env` (read by Docker), not in your shell. Either export it or paste the literal value. |
| "Open full run record" returns 500 | Stale image — pull latest on `pivot/compchem-campaigns` and rebuild API. |

---

## Running the wet-lab bioprocess version

```bash
git checkout pivot/bioprocess-open-core
cp .env.example .env
docker compose up -d --build
docker compose run --rm api alembic upgrade head
```

Then either:
- Drop a sample CSV in `sample_data/incoming/` and let the edge agent pick it up (`make edge-test`)
- Hit the read-only process-scientist dashboard at `http://localhost:8000/dashboard`
- POST a bioprocess run via `POST /api/v1/runs` and stream metrics into it

The bioprocess vertical does not currently ship a curated demo dataset equivalent to the comp-chem `/api/v1/demo/login`. See `CLAUDE.md` on that branch for the run-creation API and the `MeasurementSeries` model.

---

## Repository layout

```
lablink/
├── services/api/                 # FastAPI service (both verticals)
│   ├── app.py                    # Routes + process_manifest() orchestrator
│   ├── database.py               # Shared models (files, audit, baselines, runs, api keys)
│   ├── compchem_models.py        # Comp-chem models (projects, campaigns, runs, molecules)
│   ├── compchem_routes.py        # Comp-chem API surface
│   ├── compchem_ingest.py        # Comp-chem manifest persistence
│   ├── compchem_qc.py            # Comp-chem domain QC (MD / docking / DFT / PAINS)
│   ├── bioprocess_routes.py      # Bioprocess API surface
│   ├── bioprocess_qc.py          # Bioprocess domain QC (VCD / DO / pH / titer)
│   ├── runs_service.py           # Bioprocess run lifecycle
│   ├── timeseries_align.py       # Discrete-onto-continuous alignment
│   ├── demo_seed.py              # Comp-chem demo dataset
│   ├── qc.py / baselines.py      # Generic QC engine + Welford baselines (shared)
│   ├── webhooks.py / audit       # Hash-chained audit + signed webhook delivery (shared)
│   └── migrations/versions/      # Alembic — 001 generic, 002 bioprocess, 003 compchem
├── parsers/                      # Instrument file parsers
│   ├── bioprocess_platform.py    # Sartorius, Eppendorf, Cytiva, Nova, Vi-CELL
│   ├── agilent_chemstation.py    # HPLC / GC
│   ├── generic_csv.py            # Always-last fallback
│   └── compchem/                 # Gaussian, ORCA, Glide, Vina, Gnina, GROMACS, OpenMM, RDKit
├── edge/
│   ├── agent.py                  # Wet-lab watchdog
│   └── compchem_agent.py         # Comp-chem watchdog + .lablink.yaml resolver
├── ontology/canonical_fields.yaml
├── frontend/compchem-dashboard/  # React/Vite dashboard (comp-chem)
├── scripts/                      # Operational scripts (demo org, demo ritual)
└── docker-compose.yml
```

---

## Tech stack

| Layer | Library |
|---|---|
| API framework | FastAPI + Uvicorn (uvloop) |
| ORM / migrations | SQLAlchemy 2 + Alembic |
| Database | PostgreSQL 16 |
| Object storage | MinIO (dev) / AWS S3 (prod) |
| Embeddings (schema mapping) | sentence-transformers (`all-MiniLM-L6-v2`) |
| Chemistry | RDKit 2024.3, cclib 1.8 |
| Data | pandas, numpy, pyarrow |
| Frontend | React + Vite + Recharts |
| File watching | watchdog |

---

## Compliance posture

- **Hash-chained audit log** — every write to a campaign, run, molecule, or assay result appends a SHA256-linked event. `POST /api/v1/audit/verify/<campaign_id>` re-walks the chain and reports any tamper.
- **Reproducibility fingerprint** — every comp-chem run carries `software_name`, `software_version`, `forcefield`, `config_hash` (SHA256 of the full parameter set), `cli_args`, and `compute_environment`.
- **Mandatory units on every metric** — `RunMetric.unit` is `NOT NULL`. Unit mismatches surface at query time, never silently averaged.
- **SOC 2 readiness self-assessment** — `GET /api/v1/compliance/soc2-readiness` (bioprocess vertical).
- **21 CFR Part 11** — hash-chained `audit_logs` with `record_hash`/`previous_hash` on every row.

---

## Known gaps

- No OAuth2 — API key only.
- No rate limiting in production.
- Webhook delivery is inline (no Celery/Redis queue).
- Wet-lab vertical has no curated demo dataset equivalent to the comp-chem `/api/v1/demo/login`.
- No CI/CD pipeline checked in.
- No `/demo/ids` convenience endpoint yet — campaign and lead-molecule IDs are fetched via `/api/v1/campaigns?org_id=demo-therapeutics` then `/campaigns/<id>/molecules?…`.

See each branch's `CLAUDE.md` for full architecture notes, design decisions, and changelog.

---

## License

Proprietary — Acme Therapeutics confidential. (Update once licensing decision is made.)
