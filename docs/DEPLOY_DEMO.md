# Deploying the LabLink Public Demo to Fly.io

This is the path from a clean Fly.io account to a public URL you can paste
into a Slack DM. Roughly 30 minutes end-to-end the first time, 5 minutes
to redeploy.

## Why Fly.io

- Free hobby tier covers one shared-CPU VM plus 1 GB Postgres — enough
  for the demo's read-mostly workload (no real ingest, no S3).
- One-process container model matches the existing API Dockerfile.
- Postgres add-on speaks the same protocol as our local `docker-compose`
  database, so the same migrations apply unchanged.

## Prereqs

- A Fly account with billing set up (free tier still requires a card).
- `flyctl` installed locally (`brew install flyctl` on macOS).
- This repo checked out at a clean commit on `main`.

## One-time provisioning

```bash
fly auth login

# 1) Create the app — accept the suggested name or set your own.
fly launch --no-deploy --copy-config=false \
  --name lablink-demo \
  --region iad \
  --dockerfile services/api/Dockerfile

# 2) Provision Postgres and attach it. This sets DATABASE_URL automatically.
fly postgres create --name lablink-demo-db --region iad --initial-cluster-size 1
fly postgres attach lablink-demo-db --app lablink-demo

# 3) Secrets the API expects at runtime.
fly secrets set \
  LABLINK_PUBLIC_BASE_URL="https://lablink-demo.fly.dev" \
  DEMO_RESET_SECRET="$(openssl rand -hex 32)" \
  --app lablink-demo
```

## Bundling the SPA into the API container

The unified comp-chem dashboard mounts wet-lab routes under `/wetlab/*`, so
one Vite build serves both verticals. We build it locally, copy `dist/` into
the container, and serve it via FastAPI's `StaticFiles`.

Add these lines to the end of `services/api/Dockerfile` (Stage 2, after
the existing `COPY` and `WORKDIR /app` commands):

```dockerfile
COPY frontend/compchem-dashboard/dist /app/static-dashboard
```

Mount the directory in `services/api/app.py` (one-time edit):

```python
from fastapi.staticfiles import StaticFiles
import os.path as _p

_static_dir = "/app/static-dashboard"
if _p.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="spa")
```

Keep this mount **after** every other `@app.get`/`@app.post` is registered
so the API routes still match first. The mount handles SPA-style fallback
because `StaticFiles(html=True)` serves `index.html` for unknown paths.

## Build + deploy

```bash
# 1) Build the unified dashboard against the Fly URL.
VITE_API_BASE="https://lablink-demo.fly.dev" \
  npm --prefix frontend/compchem-dashboard run build

# 2) Deploy. fly will package the API container with the built dist/
#    folder embedded.
fly deploy --app lablink-demo

# 3) Apply migrations on the new DB. The first deploy creates the
#    container but doesn't run alembic.
fly ssh console --app lablink-demo --command "alembic upgrade head"

# 4) Seed the demo data so /demo links land on populated campaigns.
fly ssh console --app lablink-demo --command \
  "python -c 'from database import SessionLocal; from demo_seed import reset_demo_environment; from wetlab_seed import delete_wetlab_demo, seed_wetlab_demo; db=SessionLocal(); reset_demo_environment(db); delete_wetlab_demo(db); seed_wetlab_demo(db); db.close(); print(\"seeded\")'"
```

## Smoke-test the deploy

```bash
# Landing page — should serve the SPA, not a JSON 404.
curl -s https://lablink-demo.fly.dev/demo | head

# Reset-and-enter — should return a session token and a redirect_url.
curl -s -X POST "https://lablink-demo.fly.dev/demo/reset-and-enter?domain=compchem" | jq

# First tracked share link.
curl -s "https://lablink-demo.fly.dev/demo/share?domain=both&label=internal-test" | jq .url
```

Paste the share URL into a browser. You should land on `/demo` with the
two-card selector and the `code=` parameter in the URL.

## Sharing with a recipient

```bash
# Mint a tracked link for Ryan.
curl -s "https://lablink-demo.fly.dev/demo/share?domain=compchem&label=ryan-cornell"

# Later, see whether they opened it:
fly ssh console --app lablink-demo --command \
  "python /app/scripts/demo_share_report.py"
```

## Redeploys

Every time you change the SPA or the API:

```bash
VITE_API_BASE="https://lablink-demo.fly.dev" \
  npm --prefix frontend/compchem-dashboard run build
fly deploy --app lablink-demo
```

If you add a migration: `fly ssh console --command "alembic upgrade head"`.

## Demo data freshness

The API spawns a daemon thread on startup that resets all demo data and
purges expired sessions every 30 minutes (`services/api/app.py:307-334`).
On Fly's free tier the machine can get auto-stopped — that's fine because
the very next request re-seeds the DB on a fresh startup.

## Cost

Single shared-CPU-1x Fly machine + the smallest Postgres = $0 in the
free allowance for low traffic. Monitor with `fly status --app lablink-demo`.

## Tearing it down

```bash
fly apps destroy lablink-demo
fly postgres destroy lablink-demo-db
```
