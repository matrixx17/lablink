# Epic-style (hybrid Hyperdrive) Frontend Reskin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the LabLink frontend to look like Epic EHR software — a hybrid of modern Hyperdrive (deep-blue band, activity tab strip, Storyboard rail, flat controls) and classic Hyperspace density (small system fonts, dense striped tables, square corners, bottom status bar) — across both the Computational Chemistry and Wet Lab verticals, preserving all functionality.

**Architecture:** The design is token-driven: `compchem-dashboard/src/styles.css` defines `:root` CSS variables consumed by CSS-module files. The only served app is `compchem-dashboard`, which renders one shared `Layout` for every route and cross-imports the wetlab pages. So tokens + shell are edited once (compchem), while duplicated module CSS (`ui.module.css`, `pages.module.css`, `demoTour.css`, `Layout.module.css` tour classes) is edited in **both** dashboards because both are loaded at runtime.

**Tech Stack:** React + TypeScript + Vite, CSS Modules, react-router, Shepherd.js (tour), Playwright (verification via `frontend/demo-qa`). Runs in Docker (`lablink-frontend` container, HMR via volume mount).

**Note on TDD:** This is a visual reskin — there is no unit-test surface for CSS. Per task, "verification" means: `tsc -b` passes, a Playwright screenshot is captured and visually checked against the approved mockup, and tour anchors still resolve. Commit after each task.

**Spec:** `docs/superpowers/specs/2026-05-27-epic-style-frontend-reskin-design.md`

---

## Pre-flight (once, before Task 1)

- [ ] **Confirm the stack is running**

Run: `docker ps --format '{{.Names}}: {{.Status}}'`
Expected: `lablink-frontend`, `lablink-api`, `lablink-postgres`, `lablink-minio` all `Up`. If frontend is down: `docker compose up -d frontend`.

- [ ] **Confirm baseline build is green**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0`.

- [ ] **Confirm a clean git state for this work**

Run: `git status --short`
Expected: working tree shows only the previously-committed/known changes. Create a branch for this feature: `git checkout -b epic-style-reskin`

---

## Task 0: Verification screenshot helper

**Files:**
- Create: `frontend/demo-qa/shoot-reskin.mjs`

- [ ] **Step 1: Write the screenshot helper**

```js
// Usage: node shoot-reskin.mjs <compchem|wetlab> <outTag>
// Resets a demo, seeds the session, screenshots key pages + an active tour step + finish modal.
import { chromium } from "@playwright/test";
import * as fs from "node:fs";

const BASE = process.env.DEMO_BASE_URL || "http://localhost:3000";
const domain = process.argv[2] || "compchem";
const tag = process.argv[3] || "after";
const OUT = `reskin-shots/${domain}-${tag}`;
fs.mkdirSync(OUT, { recursive: true });
const prefix = domain === "wetlab" ? "/wetlab" : "";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const r = await ctx.request.post(`${BASE}/demo/reset-and-enter?domain=${domain}`);
if (!r.ok()) throw new Error(`reset failed ${r.status()}`);
const entry = await r.json();

await page.goto(`${BASE}/demo`);
await page.evaluate((e) => {
  sessionStorage.setItem("lablink_demo_session", e.session_token);
  sessionStorage.setItem("lablink_demo_expires_at", e.session_expires_at);
  sessionStorage.setItem("lablink_demo_domain", e.domain);
  const skipped = JSON.stringify({ status: "skipped", stepIndex: 0 });
  sessionStorage.setItem("lablink.compchem.guidedTour", skipped);
  sessionStorage.setItem("lablink.wetlab.guidedTour", skipped);
}, entry);

async function shoot(name, path) {
  await page.goto(`${BASE}${path}${path.includes("?") ? "&" : "?"}org=${entry.org_id}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
}

await shoot("01-list", `${prefix}/campaigns`);
await shoot("02-detail", `${prefix}/campaigns/${entry.campaign_id}`);
if (domain === "compchem") {
  await shoot("03-sar", `/campaigns/${entry.campaign_id}/sar`);
  await shoot("04-audit", `/campaigns/${entry.campaign_id}/audit`);
} else {
  await shoot("03-audit", `/wetlab/campaigns/${entry.campaign_id}/audit`);
  await shoot("04-methods", `/wetlab/campaigns/${entry.campaign_id}/methods`);
}
await browser.close();
console.log("done", domain, tag, "->", OUT);
```

- [ ] **Step 2: Capture a BEFORE baseline (sanity that the harness works)**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem before`
Expected: `done compchem before -> reskin-shots/compchem-before`; PNGs exist. View `reskin-shots/compchem-before/01-list.png` to confirm it captures the current (pre-reskin) UI.

- [ ] **Step 3: Ignore the shot output dir**

Append `reskin-shots/` to `frontend/demo-qa/.gitignore` (create the line if missing).

- [ ] **Step 4: Commit**

```bash
git add frontend/demo-qa/shoot-reskin.mjs frontend/demo-qa/.gitignore
git commit -m "test: add Epic-reskin screenshot helper"
```

---

## Task 1: Design tokens + base globals

**Files:**
- Modify: `frontend/compchem-dashboard/src/styles.css` (full rewrite of `:root` block + base element styles)
- Modify: `frontend/compchem-dashboard/index.html` (remove serif/Inter Google-font links)

- [ ] **Step 1: Replace the `:root` token block and base typography in `styles.css`**

Replace the file's top section (the `/* … */` banner through the `h1..h4`, `p`, `.num/code` blocks — i.e. lines 1–146 in the current file) with:

```css
/*
  LabLink — Epic-style (hybrid Hyperdrive) design system.
  Deep-blue chrome + Hyperspace density. System fonts, square corners,
  dense tables. Token NAMES match the prior system so module CSS keeps working.
*/

:root {
  /* ---- Surface ---- */
  --bg:        #e9eef3;   /* app background */
  --bg-elev:   #ffffff;   /* panels, tables, cards */
  --bg-elev-2: #f6f9fc;   /* zebra stripe / hover / inset */
  --bg-mute:   #f5f8fb;   /* rails, section bands, status bar */

  /* ---- Chrome ---- */
  --band:        #0a4d8c; /* top band */
  --band-strong: #07396b;
  --tabstrip:    #13629f; /* activity tab strip */
  --tab-active:  #ffffff;

  /* ---- Ink ---- */
  --ink:   #1b2733;
  --ink-2: #3a4a59;
  --ink-3: #5a6b7b;
  --ink-4: #9aa9b8;

  /* ---- Rules ---- */
  --rule:   rgba(20, 40, 60, 0.10);
  --rule-2: #c2cedb;
  --rule-3: #9fb4c9;

  /* ---- Accent (blue) ---- */
  --accent:        #0a4d8c;
  --accent-soft:   #e7eff7;
  --accent-strong: #07396b;

  /* ---- Semantic (clinical status) ---- */
  --good:      #1f7a4d;
  --good-soft: #e8f3ec;
  --warn:      #a36800;
  --warn-soft: #f7f0df;
  --bad:       #a32218;
  --bad-soft:  #f6e7e5;

  /* ---- Typography: system stack (Epic uses Segoe UI) ---- */
  --display: "Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif;
  --body:    "Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif;
  --mono:    Consolas, "SFMono-Regular", ui-monospace, Menlo, monospace;

  /* ---- Shape / Layout ---- */
  --radius: 2px;
  --max:    1400px;
  --rail:   132px;   /* storyboard rail width */
  --pad-x:  16px;

  color: var(--ink);
  background: var(--bg);
  font-family: var(--body);
  font-size: 11.5px;
  line-height: 1.35;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; }

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

button, input, select, textarea { font: inherit; color: inherit; }

input, select, textarea {
  background: var(--bg-elev);
  border: 1px solid var(--rule-2);
  color: var(--ink);
  padding: 3px 7px;
  border-radius: var(--radius);
  font-size: 11.5px;
  transition: border-color 100ms ease, box-shadow 100ms ease;
}
input::placeholder, textarea::placeholder { color: var(--ink-4); }
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}

pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--ink-2);
  background: var(--bg-mute);
  padding: 8px 10px;
  border: 1px solid var(--rule-2);
  border-radius: var(--radius);
}

::selection { background: var(--accent); color: #fff; }

/* ---- Headings — small weighted sans (no serif) ---- */
h1, h2, h3, h4 { color: var(--ink); margin: 0 0 0.4em; letter-spacing: 0; font-family: var(--body); }
h1 { font-weight: 600; font-size: 1.45rem; line-height: 1.15; }
h2 { font-weight: 600; font-size: 1.15rem; line-height: 1.2; }
h3 { font-weight: 600; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--ink-3); }
h4 { font-weight: 600; font-size: 0.95rem; }

p { margin: 0 0 0.6em; color: var(--ink-2); }
strong { font-weight: 600; color: var(--ink); }

.num, code, kbd {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum";
}
code {
  background: var(--bg-mute);
  padding: 1px 5px;
  border-radius: var(--radius);
  font-size: 0.9em;
  color: var(--ink);
}
```

Leave the existing scrollbar block and `@media (prefers-reduced-motion)` block (current lines 148–159) unchanged.

- [ ] **Step 2: Remove the serif/sans webfont `<link>`s from `index.html`**

Open `frontend/compchem-dashboard/index.html`. Delete any `<link>` tags that load Google Fonts (e.g. `fonts.googleapis.com` / `fonts.gstatic.com` for "Instrument Serif", "Source Serif", "Inter Tight", "Inter", "JetBrains Mono"). Keep everything else. (If there are no such links, note it and move on.)

- [ ] **Step 3: Verify build passes**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0`.

- [ ] **Step 4: Screenshot — confirm tokens applied**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem t1`
Expected: `done`. View `reskin-shots/compchem-t1/01-list.png`: background is light blue-gray, fonts are system sans, text is smaller. Layout is still the old sidebar shell (fixed in Task 2) — that's expected.

- [ ] **Step 5: Commit**

```bash
git add frontend/compchem-dashboard/src/styles.css frontend/compchem-dashboard/index.html
git commit -m "feat(ui): Epic-style design tokens and base typography"
```

---

## Task 2: Shell — top band, activity tabs, status bar

**Files:**
- Modify: `frontend/compchem-dashboard/src/components/Layout.tsx` (full rewrite of the returned JSX + add tab logic; keep all hooks/helpers)
- Modify: `frontend/compchem-dashboard/src/components/Layout.module.css` (replace shell/sidebar classes with band/tabstrip/statusbar; keep tour-modal + launcher classes)

- [ ] **Step 1: Rewrite `Layout.tsx`**

Replace the entire file with the following. (Helper functions `useOrgId`, `withOrg`, `detectVertical`, `copyText` and all effects are preserved; only the markup and the new `tabs` memo change.)

```tsx
import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { api, OrgInfo } from "../api/client";
import { getDemoSession } from "../lib/demoSession";
import CompchemDemoTour from "./DemoTour";
import WetlabDemoTour from "../../../wetlab-dashboard/src/components/DemoTour";
import styles from "./Layout.module.css";

const COPY_MESSAGE = "Link copied — anyone with this link can explore the demo.";

type Vertical = "compchem" | "wetlab";

function detectVertical(pathname: string): Vertical {
  return pathname.startsWith("/wetlab") ? "wetlab" : "compchem";
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textArea = document.createElement("textarea");
  textArea.value = value;
  textArea.setAttribute("readonly", "true");
  textArea.style.position = "fixed";
  textArea.style.opacity = "0";
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand("copy");
  document.body.removeChild(textArea);
}

export function useOrgId() {
  const [params, setParams] = useSearchParams();
  const orgId = params.get("org") || "demo-therapeutics";
  const setOrgId = (next: string) => {
    const copy = new URLSearchParams(params);
    copy.set("org", next || "demo-therapeutics");
    setParams(copy, { replace: true });
  };
  return { orgId, setOrgId };
}

export function withOrg(path: string, orgId: string) {
  const onWetlab = typeof window !== "undefined" && window.location.pathname.startsWith("/wetlab");
  const needsPrefix = onWetlab && path.startsWith("/") && !path.startsWith("/wetlab");
  const prefixed = needsPrefix ? `/wetlab${path}` : path;
  return `${prefixed}${prefixed.includes("?") ? "&" : "?"}org=${encodeURIComponent(orgId)}`;
}

type Tab = { label: string; to: string; end?: boolean };

export default function Layout() {
  const { orgId, setOrgId } = useOrgId();
  const location = useLocation();
  const vertical: Vertical = detectVertical(location.pathname);
  const [org, setOrg] = useState<OrgInfo | null>(null);
  const [demoRemainingMs, setDemoRemainingMs] = useState<number | null>(null);
  const [shareMessage, setShareMessage] = useState<string | null>(null);

  useEffect(() => {
    api.org(orgId).then(setOrg).catch(() => setOrg(null));
  }, [orgId]);

  useEffect(() => {
    if (org?.demo_mode) {
      document.body.dataset.lablinkDemo = "true";
    } else {
      delete document.body.dataset.lablinkDemo;
    }
  }, [org?.demo_mode]);

  useEffect(() => {
    const tick = () => {
      const session = getDemoSession();
      setDemoRemainingMs(session ? Math.max(0, Date.parse(session.expiresAt) - Date.now()) : null);
    };
    tick();
    const id = window.setInterval(tick, 60_000);
    return () => window.clearInterval(id);
  }, []);

  const restartDemo = async () => {
    const entry = await api.resetAndEnterDemo(vertical);
    window.location.assign(entry.redirect_url);
  };

  const shareDemo = async () => {
    const result = await api.shareDemo(vertical);
    await copyText(result.url);
    setShareMessage(COPY_MESSAGE);
  };

  const demoMinutes = demoRemainingMs == null ? null : Math.ceil(demoRemainingMs / 60_000);
  const demoHours = demoMinutes == null ? 0 : Math.floor(demoMinutes / 60);
  const demoMins = demoMinutes == null ? 0 : demoMinutes % 60;

  const compchemHome = `/campaigns?org=${encodeURIComponent(orgId)}`;
  const wetlabHome = `/wetlab/campaigns?org=${encodeURIComponent(orgId)}`;
  const homePath = vertical === "wetlab" ? wetlabHome : compchemHome;
  const brandSub = vertical === "wetlab" ? "Bioprocess" : "Computational Chemistry";
  const footerNote = vertical === "wetlab"
    ? "v0.1 · evidence-grade bioprocess provenance"
    : "v0.1 · evidence-grade computational provenance";

  // Activity tabs derived from the URL. With a campaign selected, show its
  // sub-activities; otherwise just the workspace tab.
  const campaignId = useMemo(() => {
    const m = location.pathname.match(/\/campaigns\/([^/?]+)/);
    return m ? m[1] : null;
  }, [location.pathname]);

  const tabs: Tab[] = useMemo(() => {
    if (!campaignId) {
      return [{ label: "Campaigns", to: vertical === "wetlab" ? "/wetlab/campaigns" : "/campaigns", end: true }];
    }
    if (vertical === "wetlab") {
      const b = `/wetlab/campaigns/${campaignId}`;
      return [
        { label: "Overview", to: b, end: true },
        { label: "Audit Trail", to: `${b}/audit` },
        { label: "Methods", to: `${b}/methods` },
      ];
    }
    const b = `/campaigns/${campaignId}`;
    return [
      { label: "Chart Review", to: b, end: true },
      { label: "Molecules", to: `${b}/molecules` },
      { label: "SAR Explorer", to: `${b}/sar` },
      { label: "Audit Trail", to: `${b}/audit` },
      { label: "Methods", to: `${b}/methods-export` },
    ];
  }, [campaignId, vertical]);

  const TourEl = vertical === "wetlab" ? WetlabDemoTour : CompchemDemoTour;

  return (
    <div className={styles.shell}>
      {/* Top band */}
      <header className={styles.band}>
        <Link to={homePath} className={styles.brand}>
          <span className={styles.wordmark}>LabLink</span>
          <span className={styles.brandSub}>{brandSub}</span>
        </Link>

        <div className={styles.bandRight}>
          <div className={styles.vertSwitch} role="tablist" aria-label="Switch vertical">
            <Link
              to={compchemHome}
              role="tab"
              aria-selected={vertical === "compchem"}
              className={vertical === "compchem" ? styles.vertActive : styles.vertInactive}
            >
              Comp Chem
            </Link>
            <Link
              to={wetlabHome}
              role="tab"
              aria-selected={vertical === "wetlab"}
              className={vertical === "wetlab" ? styles.vertActive : styles.vertInactive}
            >
              Wet Lab
            </Link>
          </div>

          {demoMinutes != null ? (
            <span className={styles.demoChip}>Demo Mode — {demoHours}h {demoMins.toString().padStart(2, "0")}m</span>
          ) : null}

          {(org?.demo_mode || demoMinutes != null) ? (
            <button type="button" className={styles.bandButton} onClick={shareDemo}>Share</button>
          ) : null}
          {demoMinutes != null ? (
            <button type="button" className={styles.bandButton} onClick={restartDemo}>Restart</button>
          ) : null}

          <span className={styles.orgTag} title="Organization">{orgId}</span>
        </div>
      </header>

      {/* Activity tab strip */}
      <nav className={styles.tabstrip} aria-label="Activities">
        {tabs.map((t) => (
          <NavLink
            key={t.to}
            to={withOrg(t.to, orgId)}
            end={t.end}
            className={({ isActive }) => (isActive ? styles.tabActive : styles.tab)}
          >
            {t.label}
          </NavLink>
        ))}
        <span className={styles.tabSpacer} />
        <div className={styles.tourSlot}>
          <TourEl orgId={orgId} />
        </div>
      </nav>

      {/* Main */}
      <main className={styles.main}>
        {org?.demo_mode ? (
          <div className={styles.demoBanner} data-lablink-demo-banner="true">
            <strong>Demo environment.</strong>
            <span>Data resets periodically. Create a free workspace to use your own.</span>
          </div>
        ) : null}
        {shareMessage ? <div className={styles.shareToast}>{shareMessage}</div> : null}
        <div className={styles.mainInner}>
          <Outlet />
        </div>
      </main>

      {/* Status bar */}
      <footer className={styles.statusbar}>
        <span>{demoMinutes != null ? `Demo Mode — ${demoHours}h ${demoMins.toString().padStart(2, "0")}m remaining` : "Connected"}</span>
        <span className={styles.statusMid}>{footerNote}</span>
        <span>
          <label htmlFor="org" className={styles.orgLabel}>Org</label>
          <input id="org" className={styles.orgInput} value={orgId} onChange={(e) => setOrgId(e.target.value)} />
        </span>
      </footer>
    </div>
  );
}
```

- [ ] **Step 2: Rewrite the shell layout in `Layout.module.css`**

Replace **only** the layout/sidebar classes — the current `.shell`, `.sidebar`, `.brand`, `.wordmark`, `.brandSub`, `.nav`, `.nav a` (and friends), `.verticalSwitcher*`, `.navGroupLabel`, `.demoShareNav`, `.orgBox`, `.demoChip*`, `.footerNote`, `.main`, `.mainInner`, `.demoBanner`, `.navTourButton`, `.floatingTourButton` — with the block below. **Do NOT touch** `.tourModalBackdrop`, `.tourModalCard`, `.tourModalActions`, `.tourModalPrimary`, `.tourModalSecondary`, `.tourModalFootnote` (those are restyled in Task 6). If a legacy class above is referenced nowhere after the `Layout.tsx` rewrite, deleting it is fine.

```css
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

/* ---- Top band ---- */
.band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--band);
  color: #fff;
  padding: 4px 12px;
  height: 32px;
}
.brand { display: flex; align-items: baseline; gap: 8px; color: #fff; }
.brand:hover { text-decoration: none; }
.wordmark { font-weight: 700; font-size: 13px; letter-spacing: 0.2px; }
.brandSub { font-size: 10px; opacity: 0.8; }

.bandRight { display: flex; align-items: center; gap: 8px; font-size: 10.5px; }
.vertSwitch { display: flex; border: 1px solid rgba(255,255,255,0.35); border-radius: var(--radius); overflow: hidden; }
.vertActive, .vertInactive { padding: 1px 9px; color: #fff; }
.vertActive { background: #fff; color: var(--band); font-weight: 600; }
.vertActive:hover, .vertInactive:hover { text-decoration: none; }
.vertInactive:hover { background: rgba(255,255,255,0.15); }

.demoChip { background: rgba(255,255,255,0.16); padding: 1px 8px; border-radius: var(--radius); }
.bandButton {
  background: rgba(255,255,255,0.12);
  border: 1px solid rgba(255,255,255,0.35);
  color: #fff;
  padding: 1px 9px;
  border-radius: var(--radius);
  cursor: pointer;
}
.bandButton:hover { background: rgba(255,255,255,0.24); }
.orgTag { font-family: var(--mono); opacity: 0.85; }

/* ---- Activity tab strip ---- */
.tabstrip {
  display: flex;
  align-items: stretch;
  background: var(--tabstrip);
  border-bottom: 1px solid var(--band-strong);
  font-size: 11px;
}
.tab, .tabActive {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  color: #cfe2f3;
}
.tab:hover { background: rgba(255,255,255,0.10); color: #fff; text-decoration: none; }
.tabActive { background: var(--bg-elev); color: var(--band); font-weight: 600; }
.tabActive:hover { text-decoration: none; }
.tabSpacer { flex: 1; }
.tourSlot { display: flex; align-items: center; padding-right: 8px; }

/* ---- Main ---- */
.main { flex: 1; background: var(--bg); }
.mainInner { max-width: var(--max); margin: 0 auto; padding: 10px var(--pad-x) 16px; }
.demoBanner {
  display: flex; gap: 8px; align-items: center;
  background: var(--accent-soft);
  border-bottom: 1px solid var(--rule-2);
  color: var(--ink-2);
  padding: 4px var(--pad-x);
  font-size: 10.5px;
}
.shareToast {
  margin: 8px var(--pad-x) 0;
  background: var(--good-soft); color: var(--good);
  border: 1px solid var(--good); border-radius: var(--radius);
  padding: 4px 8px; font-size: 10.5px;
}

/* ---- Status bar ---- */
.statusbar {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  background: var(--bg-mute);
  border-top: 1px solid var(--rule-2);
  color: var(--ink-3);
  padding: 2px 12px;
  font-size: 10px;
}
.statusMid { color: var(--ink-4); }
.orgLabel { margin-right: 5px; text-transform: uppercase; letter-spacing: 0.06em; }
.orgInput { padding: 0 5px; font-size: 10px; width: 150px; height: 16px; }

/* ---- Tour launcher / restart (rendered in the tab strip) ---- */
.navTourButton {
  border: 1px solid var(--rule-2); background: #f3f7fb; color: var(--accent);
  padding: 1px 8px; border-radius: var(--radius); cursor: pointer; font-size: 10.5px;
}
.navTourButton:hover { background: var(--accent-soft); }
.floatingTourButton {
  position: fixed; right: 16px; bottom: 28px; z-index: 25;
  border: 1px solid var(--accent); background: var(--accent); color: #fff;
  padding: 5px 12px; border-radius: var(--radius); cursor: pointer;
  font-size: 11px; box-shadow: 0 6px 18px rgba(10,77,140,0.28);
}
.floatingTourButton:hover { background: var(--accent-strong); }
```

(`DemoTour.tsx` references `styles.navTourButton` and `styles.floatingTourButton` — both are kept above.)

- [ ] **Step 3: Verify build passes**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0`.

- [ ] **Step 4: Screenshot — confirm shell**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem t2`
Expected: `01-list.png` shows the deep-blue top band (LabLink + Comp Chem/Wet Lab switch + Share/Restart + org), a blue tab strip with a "Campaigns" tab, and a bottom status bar. `02-detail.png` shows the campaign tabs (Chart Review / Molecules / SAR Explorer / Audit Trail / Methods). The "Start guided tour" launcher still appears (floating, bottom-right).

- [ ] **Step 5: Verify navigation + tour still work**

Run: `cd frontend/demo-qa && node ../demo-qa/shoot-reskin.mjs wetlab t2`
Expected: wetlab `02-detail.png` shows Wet Lab tabs (Overview / Audit Trail / Methods) and the Wet Lab vertical highlighted in the band switch.

- [ ] **Step 6: Commit**

```bash
git add frontend/compchem-dashboard/src/components/Layout.tsx frontend/compchem-dashboard/src/components/Layout.module.css
git commit -m "feat(ui): Epic shell — top band, activity tabs, status bar"
```

---

## Task 3: Shared components — restyle + add Storyboard

**Files:**
- Modify: `frontend/compchem-dashboard/src/components/ui.module.css` AND `frontend/wetlab-dashboard/src/components/ui.module.css` (identical changes)
- Modify: `frontend/compchem-dashboard/src/components/ui.tsx` AND `frontend/wetlab-dashboard/src/components/ui.tsx` (add `Storyboard` + `DetailLayout`)

- [ ] **Step 1: Restyle key component classes in `ui.module.css` (compchem copy)**

Replace the rule bodies for these classes (keep any class not listed as-is). Use these exact declarations:

```css
/* Hero → compact page header */
.hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; padding: 6px 0 8px; border-bottom: 1px solid var(--rule-2); margin-bottom: 10px; }
.heroEyebrow { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--ink-3); margin: 0 0 2px; }
.heroTitle { font-family: var(--body); font-weight: 600; font-size: 1.3rem; line-height: 1.15; margin: 0; }
.heroContext { color: var(--ink-2); font-size: 11px; max-width: 70ch; margin-top: 2px; }
.heroStatus { margin-top: 4px; }
.heroActions { display: flex; gap: 5px; flex-wrap: wrap; }

/* Buttons */
.primaryButton { display: inline-flex; align-items: center; gap: 6px; background: var(--accent); color: #fff; border: 1px solid var(--accent); font-weight: 600; font-size: 11px; padding: 3px 12px; border-radius: var(--radius); cursor: pointer; }
.primaryButton:hover { background: var(--accent-strong); border-color: var(--accent-strong); text-decoration: none; }
.primaryButton[aria-disabled="true"], .primaryButton:disabled { opacity: 0.5; cursor: default; }
.secondaryButton, .tertiaryButton { display: inline-flex; align-items: center; gap: 6px; background: #f3f7fb; color: var(--ink); border: 1px solid var(--rule-2); font-weight: 500; font-size: 11px; padding: 3px 10px; border-radius: var(--radius); cursor: pointer; }
.secondaryButton:hover, .tertiaryButton:hover { background: var(--accent-soft); border-color: var(--rule-3); color: var(--accent); text-decoration: none; }
.secondaryButton[aria-disabled="true"], .secondaryButton:disabled { opacity: 0.5; cursor: default; }

/* Tables — dense, blue header, zebra */
.tableWrap { border: 1px solid var(--rule-2); border-radius: var(--radius); overflow-x: auto; background: var(--bg-elev); }
.table { width: 100%; border-collapse: collapse; font-size: 11px; }
.table thead th { position: sticky; top: 0; background: var(--accent-soft); color: var(--ink-2); text-align: left; font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em; padding: 4px 8px; border-bottom: 1px solid var(--rule-2); white-space: nowrap; }
.table tbody td { padding: 3px 8px; border-bottom: 1px solid var(--rule); vertical-align: top; }
.table tbody tr:nth-child(even) { background: var(--bg-elev-2); }
.table tbody tr:hover { background: var(--accent-soft); }

/* Card → flat panel with section band */
.card { background: var(--bg-elev); border: 1px solid var(--rule-2); border-radius: var(--radius); padding: 8px 10px; }

/* Section rule → blue-band header */
.sectionRule { display: flex; justify-content: space-between; align-items: center; gap: 12px; background: var(--accent-soft); border-left: 3px solid var(--accent); padding: 3px 8px; margin: 10px 0 6px; }
.sectionEyebrow { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent); margin: 0; }
.sectionTitle { font-family: var(--body); font-weight: 600; font-size: 0.95rem; color: var(--accent); margin: 0; }
.sectionActions { display: flex; gap: 5px; }

/* KPI strip — compact */
.kpiStrip { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1px; background: var(--rule-2); border: 1px solid var(--rule-2); border-radius: var(--radius); overflow: hidden; }
.kpi { background: var(--bg-elev); padding: 6px 9px; }
.kpiLabel { font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink-3); }
.kpiValue { font-size: 1.25rem; font-weight: 600; }
.kpiUnit { font-size: 10px; color: var(--ink-3); margin-left: 3px; }
.kpiHint { font-size: 10px; color: var(--ink-3); }

/* Status badges — keep pill shape */
.badge { display: inline-block; padding: 0 7px; border-radius: 9px; font-size: 10px; font-weight: 600; line-height: 16px; }
.badge_pass { background: var(--good-soft); color: var(--good); }
.badge_warn { background: var(--warn-soft); color: var(--warn); }
.badge_fail { background: var(--bad-soft); color: var(--bad); }
.badge_neutral { background: var(--bg-elev-2); color: var(--ink-3); }

/* Storyboard rail + detail layout */
.detailLayout { display: flex; gap: 12px; align-items: flex-start; }
.detailMain { flex: 1; min-width: 0; }
.storyboard { width: var(--rail); flex: 0 0 var(--rail); background: var(--bg-mute); border: 1px solid var(--rule-2); border-radius: var(--radius); padding: 7px 8px; font-size: 10px; color: var(--ink-2); position: sticky; top: 8px; }
.storyboardTitle { font-weight: 700; color: var(--accent); font-size: 11px; }
.storyboardRow { margin-top: 5px; }
.storyboardRow span { display: block; color: var(--ink-3); }
.storyboardRow strong { color: var(--ink); }
.storyboardDivider { border: none; border-top: 1px solid var(--rule-2); margin: 6px 0; }
```

- [ ] **Step 2: Apply the identical CSS from Step 1 to the wetlab copy**

Write the exact same rule changes to `frontend/wetlab-dashboard/src/components/ui.module.css`.

- [ ] **Step 3: Add `Storyboard` + `DetailLayout` to `ui.tsx` (compchem copy)**

Append these exports to `frontend/compchem-dashboard/src/components/ui.tsx`:

```tsx
// ============================================================
// Storyboard rail + detail two-column layout
// ============================================================

export function DetailLayout({
  storyboard,
  children,
}: {
  storyboard: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.detailLayout}>
      {storyboard}
      <div className={styles.detailMain}>{children}</div>
    </div>
  );
}

export type StoryboardRow = { label: string; value: React.ReactNode };

export function Storyboard({
  title,
  status,
  rows,
}: {
  title: React.ReactNode;
  status?: React.ReactNode;
  rows: StoryboardRow[];
}) {
  return (
    <aside className={styles.storyboard} aria-label="Storyboard">
      <div className={styles.storyboardTitle}>{title}</div>
      {status ? <div className={styles.storyboardRow}>{status}</div> : null}
      <hr className={styles.storyboardDivider} />
      {rows.map((r, i) => (
        <div className={styles.storyboardRow} key={`${r.label}-${i}`}>
          <span>{r.label}</span>
          <strong>{r.value}</strong>
        </div>
      ))}
    </aside>
  );
}
```

- [ ] **Step 4: Apply the identical `ui.tsx` additions to the wetlab copy**

Append the exact same `DetailLayout`, `StoryboardRow`, and `Storyboard` code to `frontend/wetlab-dashboard/src/components/ui.tsx`.

- [ ] **Step 5: Verify build passes**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0` (the new exports are unused until Task 5 — that's fine; they're exported so no unused error).

- [ ] **Step 6: Screenshot — confirm components**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem t3`
Expected: `01-list.png` table is now dense with a light-blue header row and zebra striping; buttons are rectangular; the hero is compact. `02-detail.png` panels/section bands show the blue left-border header style.

- [ ] **Step 7: Commit**

```bash
git add frontend/compchem-dashboard/src/components/ui.module.css frontend/compchem-dashboard/src/components/ui.tsx frontend/wetlab-dashboard/src/components/ui.module.css frontend/wetlab-dashboard/src/components/ui.tsx
git commit -m "feat(ui): Epic component styles + Storyboard rail component"
```

---

## Task 4: Page CSS density

**Files:**
- Modify: `frontend/compchem-dashboard/src/pages/pages.module.css` AND `frontend/wetlab-dashboard/src/pages/pages.module.css` (identical changes)

- [ ] **Step 1: Tighten shared page classes (compchem copy)**

Replace the rule bodies for these classes (keep others as-is):

```css
.grid { display: flex; flex-direction: column; gap: 8px; }
.reveal { animation: none; }
.toolbar { display: flex; align-items: center; gap: 8px; padding: 5px 0; flex-wrap: wrap; }
.muted { color: var(--ink-3); font-size: 10px; }
.link { color: var(--accent); font-weight: 600; }
.link:hover { text-decoration: underline; }
```

If `.grid` or `.reveal` do not exist in this file, add them. Leave all other page-specific classes unchanged (they inherit tokens).

- [ ] **Step 2: Apply identical changes to the wetlab copy**

Write the same rule changes to `frontend/wetlab-dashboard/src/pages/pages.module.css`.

- [ ] **Step 3: Verify build + screenshot**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"` → `EXIT:0`
Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem t4`
Expected: list/detail spacing is tighter; toolbar (filter + sort + count) reads as a compact control row.

- [ ] **Step 4: Commit**

```bash
git add frontend/compchem-dashboard/src/pages/pages.module.css frontend/wetlab-dashboard/src/pages/pages.module.css
git commit -m "feat(ui): denser page spacing for Epic look"
```

---

## Task 5: Campaign detail — Storyboard rail

The list pages already render via `DataTable`/`HeroHeader` and inherit the new look — no structural change. Only the **detail** pages get the Storyboard rail.

**Files:**
- Modify: `frontend/compchem-dashboard/src/pages/CampaignDetailPage.tsx`
- Modify: `frontend/wetlab-dashboard/src/pages/CampaignDetailPage.tsx`

- [ ] **Step 1: Read the compchem detail page to find the data already in scope**

Run: `sed -n '1,60p' frontend/compchem-dashboard/src/pages/CampaignDetailPage.tsx` (or open it). Identify the `campaign` object fields already available (`name`, `status`, `target_name`, `lead_molecule_id`, `run_count`, `molecule_count`, delivery fields) and the existing `<HeroHeader …>` block and its parent wrapper.

- [ ] **Step 2: Import the new components (compchem)**

In `frontend/compchem-dashboard/src/pages/CampaignDetailPage.tsx`, add `Storyboard`, `DetailLayout`, and `StatusBadge` to the existing import from `../components/ui` (StatusBadge may already be imported — don't duplicate).

- [ ] **Step 3: Wrap the page body in `DetailLayout` with a `Storyboard` (compchem)**

Find the top-level returned wrapper of the loaded campaign (the element that currently contains `<HeroHeader … />` and the rest). Wrap its children so the Storyboard sits to the left. Concretely, change the structure from:

```tsx
return (
  <div className={styles.grid}>
    <HeroHeader … />
    {/* …existing detail content… */}
  </div>
);
```

to:

```tsx
return (
  <DetailLayout
    storyboard={
      <Storyboard
        title={campaign.name}
        status={<StatusBadge status={campaign.status} />}
        rows={[
          { label: "Target", value: campaign.target_name || "—" },
          { label: "Lead", value: campaign.lead_molecule_id ? "AC-007" : "—" },
          { label: "Runs", value: campaign.run_count },
          { label: "Compounds", value: campaign.molecule_count },
          { label: "Type", value: campaign.campaign_type },
        ]}
      />
    }
  >
    <div className={styles.grid}>
      <HeroHeader … />
      {/* …existing detail content unchanged… */}
    </div>
  </DetailLayout>
);
```

Keep every existing child, prop, and **every `data-tour="…"` attribute** exactly as-is — only the outer wrapping changes. If `AC-007` is not the right lead label, use whatever lead-name variable the page already computes; if none, use `campaign.lead_molecule_id ?? "—"`.

- [ ] **Step 4: Do the same for the wetlab detail page**

In `frontend/wetlab-dashboard/src/pages/CampaignDetailPage.tsx`, import `Storyboard` and `DetailLayout` from `../components/ui`, and wrap the loaded-campaign body identically, using the wetlab campaign's fields. Use rows appropriate to bioprocess:

```tsx
rows={[
  { label: "Target", value: campaign.target_name || "—" },
  { label: "Process", value: campaign.campaign_type },
  { label: "Batches", value: campaign.run_count },
  { label: "Status", value: campaign.status },
]}
```

(Adjust field names to those the wetlab `Campaign` type actually exposes — match the existing usage already in that file. Keep all `data-tour` attributes.)

- [ ] **Step 5: Verify build passes**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0`. If a field name doesn't exist on the type, tsc will name it — replace with the correct existing field.

- [ ] **Step 6: Screenshot both verticals**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem t5 && node shoot-reskin.mjs wetlab t5`
Expected: `02-detail.png` (both) shows the left Storyboard rail (campaign name, status pill, target/lead/counts) beside the main content, matching the approved mockup.

- [ ] **Step 7: Commit**

```bash
git add frontend/compchem-dashboard/src/pages/CampaignDetailPage.tsx frontend/wetlab-dashboard/src/pages/CampaignDetailPage.tsx
git commit -m "feat(ui): campaign detail Storyboard rail (both verticals)"
```

---

## Task 6: Tour + finish modal restyle

**Files:**
- Modify: `frontend/compchem-dashboard/src/components/demoTour.css` AND `frontend/wetlab-dashboard/src/components/demoTour.css`
- Modify: `frontend/compchem-dashboard/src/components/Layout.module.css` AND `frontend/wetlab-dashboard/src/components/Layout.module.css` (tour-modal classes only)

- [ ] **Step 1: Restyle the Shepherd tooltip (compchem `demoTour.css`)**

Update these rule bodies (keep the `.shepherd-title` wrapping fix from prior work — `display:block; flex:1 1 auto; min-width:0; overflow-wrap:break-word` — intact):

```css
.lablinkTourStep { max-width: 380px; border: 1px solid var(--rule-2); border-radius: var(--radius); background: var(--bg-elev); box-shadow: 0 12px 40px rgba(10,40,70,0.18); color: var(--ink); font-family: var(--body); }
.lablinkTourStep .shepherd-content { border-radius: var(--radius); }
.lablinkTourStep .shepherd-header { padding: 8px 12px 0; background: var(--accent-soft); border-bottom: 1px solid var(--rule-2); }
.lablinkTourStep .shepherd-title { display: block; flex: 1 1 auto; min-width: 0; overflow-wrap: break-word; font-family: var(--body); font-size: 13px; font-weight: 600; line-height: 1.2; color: var(--accent); padding: 4px 0; }
.lablinkTourStep .shepherd-text { padding: 8px 12px 4px; color: var(--ink-2); font-size: 11.5px; line-height: 1.45; }
.lablinkTourStep .shepherd-footer { gap: 6px; justify-content: flex-end; padding: 8px 12px 12px; }
.lablinkTourStep .shepherd-button { border: 1px solid var(--accent); border-radius: var(--radius); background: var(--accent); color: #fff; cursor: pointer; font-family: var(--body); font-size: 11px; font-weight: 600; padding: 4px 12px; }
.lablinkTourStep .shepherd-button:hover { background: var(--accent-strong); }
.lablinkTourStep .shepherd-button-secondary { background: #f3f7fb; border-color: var(--rule-2); color: var(--ink); }
.lablinkTourStep .shepherd-button-secondary:hover { background: var(--accent-soft); color: var(--accent); }
```

Leave `.shepherd-cancel-icon`, `.shepherd-arrow::before`, the modal-overlay opacity rule, and `[data-tour] { scroll-margin }` as-is (they still work with tokens).

- [ ] **Step 2: Apply identical changes to the wetlab `demoTour.css`**

- [ ] **Step 3: Restyle the finish-modal classes in `Layout.module.css` (compchem)**

Update these rule bodies:

```css
.tourModalCard { width: min(440px, 100%); border: 1px solid var(--rule-2); border-radius: var(--radius); background: var(--bg-elev); padding: 18px 20px; box-shadow: 0 18px 60px rgba(10,40,70,0.22); }
.tourModalCard h2 { font-family: var(--body); font-size: 1.25rem; font-weight: 600; line-height: 1.2; color: var(--accent); }
.tourModalActions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 14px; }
.tourModalPrimary, .tourModalSecondary { border-radius: var(--radius); cursor: pointer; font-size: 11px; font-weight: 600; padding: 4px 12px; text-decoration: none; }
.tourModalPrimary { border: 1px solid var(--accent); background: var(--accent); color: #fff; }
.tourModalPrimary:hover { background: var(--accent-strong); color: #fff; }
.tourModalSecondary { border: 1px solid var(--rule-2); background: #f3f7fb; color: var(--ink); }
.tourModalSecondary:hover { background: var(--accent-soft); color: var(--accent); }
.tourModalFootnote { margin: 12px 0 0; color: var(--ink-3); font-size: 10.5px; }
```

Leave `.tourModalBackdrop` (the `position:fixed; z-index:40; place-items:center` overlay) unchanged.

- [ ] **Step 4: Apply identical changes to the wetlab `Layout.module.css`**

- [ ] **Step 5: Verify build + tour functions end-to-end**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"` → `EXIT:0`

Create and run a one-off tour check (the tour must advance through all anchors and open the finish modal):

```bash
cd frontend/demo-qa && cat > tour-check.mjs <<'EOF'
import { chromium } from "@playwright/test";
const BASE = "http://localhost:3000";
const domain = process.argv[2] || "compchem";
const prefix = domain === "wetlab" ? "/wetlab" : "";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
const p = await ctx.newPage();
const e = await (await ctx.request.post(`${BASE}/demo/reset-and-enter?domain=${domain}`)).json();
await p.goto(`${BASE}/demo`);
await p.evaluate((s)=>{sessionStorage.setItem("lablink_demo_session",s.session_token);sessionStorage.setItem("lablink_demo_expires_at",s.session_expires_at);sessionStorage.setItem("lablink_demo_domain",s.domain);},e);
await p.goto(`${BASE}${prefix}/campaigns/${e.campaign_id}?org=${e.org_id}`);
await p.waitForSelector("text=Start guided tour",{timeout:15000});
await p.click("text=Start guided tour");
let steps=0;
for(let i=0;i<14;i++){const el=await p.waitForSelector(".shepherd-element",{timeout:15000}).catch(()=>null);if(!el)break;steps++;await p.waitForTimeout(500);const last=p.locator(".shepherd-footer .shepherd-button").last();const t=await last.textContent().catch(()=>"");await last.click().catch(()=>{});await p.waitForTimeout(800);if(/Finish/i.test(t||""))break;}
await p.waitForTimeout(800);
const modal=await p.locator('[role="dialog"]').isVisible().catch(()=>false);
console.log(JSON.stringify({domain,steps,finishModalVisible:modal}));
await b.close();
EOF
node tour-check.mjs compchem && node tour-check.mjs wetlab`
```

Expected: each prints `steps` ≥ 6 and `finishModalVisible:true` (anchors still resolve; finish modal opens and renders above the page). Then remove the temp file: `rm frontend/demo-qa/tour-check.mjs`.

- [ ] **Step 6: Commit**

```bash
git add frontend/compchem-dashboard/src/components/demoTour.css frontend/wetlab-dashboard/src/components/demoTour.css frontend/compchem-dashboard/src/components/Layout.module.css frontend/wetlab-dashboard/src/components/Layout.module.css
git commit -m "feat(ui): Epic-style guided tour tooltips and finish modal"
```

---

## Task 7: Full verification sweep + polish

**Files:** none initially (screenshots only); fix spacing nits if found.

- [ ] **Step 1: Capture the full set for both verticals**

Run: `cd frontend/demo-qa && node shoot-reskin.mjs compchem final && node shoot-reskin.mjs wetlab final`
Expected: `done` for both; `reskin-shots/compchem-final/*` and `reskin-shots/wetlab-final/*` populated.

- [ ] **Step 2: Review each screenshot against the approved mockup**

Open and visually inspect (compchem-final + wetlab-final): `01-list`, `02-detail`, `03-*`, `04-*`. Checklist:
- Deep-blue top band + blue activity tabs present on every page.
- Dense tables: light-blue header, zebra rows, small system font.
- Campaign detail shows the Storyboard rail.
- Status bar at the bottom.
- No leftover serif headings, no oversized whitespace, no warm off-white background.
- Inherited pages (SAR/audit/molecule/methods) look consistent (no broken layout).

- [ ] **Step 3: Fix any spacing/contrast nits found**

For any issue, make the minimal CSS edit in the relevant `*.module.css` (both dashboards if it's a duplicated file), re-run the affected screenshot, and confirm. Commit each fix with `git commit -m "fix(ui): <what>"`.

- [ ] **Step 4: Final build check**

Run: `docker exec lablink-frontend sh -c "cd /app/compchem-dashboard && ./node_modules/.bin/tsc -b; echo EXIT:$?"`
Expected: `EXIT:0`.

- [ ] **Step 5: Final commit (if any pending) and summary**

```bash
git add -A && git commit -m "chore(ui): Epic-style reskin verification pass" || echo "nothing to commit"
git log --oneline -8
```

---

## Self-review notes (spec coverage)

- Tokens/typography/density → Task 1. Shell chrome (band, tabs, status bar, demo-control relocation, vertical switcher) → Task 2. Shared components (buttons, tables, panels, section bands, badges, KPI) + Storyboard → Task 3. Page density → Task 4. Campaign detail tailoring (Storyboard) → Task 5; campaign list inherits (noted). Tour + modal → Task 6. Both-dashboard duplication called out in Tasks 3/4/6. `data-tour` anchors preserved (Tasks 2/5) and verified (Task 6). No Epic trademarks. System fonts + font-link removal (Task 1). Playwright verification (Tasks 0/2/3/5/6/7) + `tsc -b` each task.
- Type consistency: `Storyboard`/`DetailLayout`/`StoryboardRow` defined in Task 3 and consumed in Task 5; CSS classes `.storyboard*`, `.detailLayout`, `.detailMain` defined in Task 3 Step 1 and used by the Task 3 components. `navTourButton`/`floatingTourButton` kept in Task 2 for `DemoTour.tsx`. Tour-modal classes deliberately deferred from Task 2 to Task 6.
