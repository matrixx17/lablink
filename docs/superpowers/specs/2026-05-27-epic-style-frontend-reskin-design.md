# Epic-style (hybrid Hyperdrive) frontend reskin — design

Date: 2026-05-27
Status: Approved (direction, scope, coverage); pending spec review.

## Goal

Restyle the LabLink frontend to evoke **Epic Systems EHR software**, specifically a
**hybrid of modern Hyperdrive and classic Hyperspace**: Hyperdrive's cleaner deep-blue
palette and chrome (top band, activity tab strip, left Storyboard context rail, flat
controls, semantic status pills) combined with Hyperspace's higher information density
(small system fonts, tight striped tables, square corners, a bottom status bar).

This is "in the style of" Epic — no Epic trademarks, logos, or product names are used.
The product keeps the **LabLink** name and all existing content/functionality.

## Decisions (locked)

- **Visual direction:** hybrid Hyperdrive (confirmed against an approved mockup).
- **Scope/depth:** shell chrome + design tokens + shared components; the two campaign
  pages (list + detail) are tailored for density; all other pages inherit the new look
  through the shared component/token layer with only minor spacing nudges. No deliberate
  per-page redesign of molecule/run/SAR/audit/methods/batch pages.
- **Coverage:** both verticals — Computational Chemistry and Wet Lab / Bioprocess.

## How the codebase is wired (constraints on the file map)

- The **only served app is `frontend/compchem-dashboard`** (Vite, port 3000 → 5173).
  Its `App.tsx` renders **one shared `Layout`** for every route and imports the wetlab
  pages via cross-import from `../../wetlab-dashboard/src/pages/*`.
- **Global tokens** (`:root` CSS variables) come **only** from
  `compchem-dashboard/src/styles.css` (loaded by `main.tsx`). Both dashboards' module CSS
  reference the same variable names, so retokening here reskins both verticals at once.
  `wetlab-dashboard/src/styles.css` and `wetlab-dashboard/index.html` are **not loaded**.
- The **shell** is solely `compchem-dashboard/src/components/Layout.tsx` +
  `Layout.module.css`.
- **Component and page CSS is loaded from both dashboards:** compchem pages use
  `compchem-dashboard`'s `ui.module.css` / `pages.module.css`; wetlab pages use
  `wetlab-dashboard`'s copies. The wetlab `DemoTour.tsx` also imports the wetlab
  `Layout.module.css` (for tour-modal/launcher classes) and `demoTour.css`. **Therefore
  these duplicated module files must be edited in both dashboards to stay consistent.**

### File map

Edit (compchem only — single source):
- `compchem-dashboard/src/styles.css` — tokens, base typography, globals.
- `compchem-dashboard/index.html` — drop the serif/Inter Google-font links (system fonts).
- `compchem-dashboard/src/components/Layout.tsx` + `Layout.module.css` — the shell.

Edit in **both** `compchem-dashboard` and `wetlab-dashboard` (`src/...`):
- `components/ui.module.css` (+ `ui.tsx` where markup must change, e.g. a new `Storyboard`).
- `pages/pages.module.css`.
- `components/demoTour.css` and `components/Layout.module.css` (tour modal + launcher
  classes referenced by each `DemoTour.tsx`).
- `pages/CampaignsPage.tsx` and `pages/CampaignDetailPage.tsx` (density tailoring +
  Storyboard usage).

Optional consistency (not loaded, low value): mirror tokens into
`wetlab-dashboard/src/styles.css` so a future standalone wetlab build matches.

## Section 1 — Design tokens (`styles.css`)

Replace the current warm/editorial tokens with the Epic hybrid set (names unchanged so
module CSS keeps working; values change):

- Surfaces: `--bg #e9eef3`, `--bg-elev #ffffff`, `--bg-elev-2 #f6f9fc` (hover/inset),
  `--bg-mute #f5f8fb` (rail/bands).
- Chrome (new vars): `--band #0a4d8c`, `--band-strong #07396b`, `--tabstrip #13629f`,
  `--tab-active #ffffff`.
- Ink: `--ink #1b2733`, `--ink-2 #3a4a59`, `--ink-3 #5a6b7b`, `--ink-4 #9aa9b8`.
- Rules: `--rule rgba(20,40,60,.10)`, `--rule-2 #c2cedb`, `--rule-3 #9fb4c9`.
- Accent: `--accent #0a4d8c`, `--accent-soft #e7eff7`, `--accent-strong #07396b`.
- Semantic: keep existing green/amber/red values (they already read as clinical status).
- Typography: `--display` and `--body` both become the system stack
  `"Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif`; `--mono` →
  `Consolas, "SFMono-Regular", ui-monospace, Menlo, monospace`. Base `font-size: 11.5px`,
  `line-height: 1.35`.
- Shape/density: introduce `--radius: 2px`; tighten the spacing used by components.
- Base element styles updated: headings become small weighted sans (no serif H1/H2),
  inputs/`pre`/`code`/links/selection recolored to the blue palette, default
  `border-radius` → `--radius`.

## Section 2 — Shell / chrome (`Layout.tsx` + `Layout.module.css`)

Replace the left-sidebar grid with stacked Hyperdrive chrome:

- **Top band** (`--band`): "LabLink" wordmark (left); right side shows the
  vertical/Comp Chem–Wet Lab **segmented switcher**, org id, the **Demo Mode** countdown,
  and a small toolbar/"⚙" menu hosting **Share this demo** and **Restart demo** (the
  controls currently in the sidebar `orgBox`/`demoShareNav`).
- **Activity tab strip** (`--tabstrip`): primary navigation as tabs. URL-derived:
  - No campaign selected (list route) → a top-level **Campaigns** tab is active.
  - A campaign selected → context tabs linking to that campaign's existing sub-routes:
    Comp Chem → Chart Review (`/campaigns/:id`), Molecules (`/campaigns/:id/molecules`),
    SAR (`/campaigns/:id/sar`), Audit (`/campaigns/:id/audit`),
    Methods (`/campaigns/:id/methods-export`); Wet Lab → the analogous `/wetlab/...`
    routes (Overview, Batches/Compare, Audit, Methods). Tab links go through `withOrg`
    so the `/wetlab` prefix and `org` param are preserved.
- **Status bar** (bottom, `--bg-mute`): Demo Mode remaining (mirrored), org, footer note,
  and incidental page status.
- **Storyboard rail:** provided as a reusable `Storyboard` component (Section 3), used by
  the campaign detail pages — not global shell state. Pages that don't render it simply
  have no rail. (Alternative considered: a global Outlet-context slot so the shell owns
  the rail; rejected to avoid cross-cutting context plumbing for this scope.)

The shell must keep the demo session/countdown effects, `useOrgId`, `restartDemo`,
`shareDemo`, vertical detection, and continue rendering `<Outlet/>` and the
`CompchemDemoTour`/`WetlabDemoTour` exactly as today (only their container/markup moves).

## Section 3 — Shared components (`ui.tsx`, `ui.module.css`)

- **Buttons:** `SecondaryButton` → rectangular toolbar button (`--radius`, thin
  `--rule-2` border, `#f3f7fb` face, blue text on hover). `PrimaryButton` → solid
  `--accent` blue action button, white text, `--radius`.
- **Tables:** dense — header row in `--accent`/`--accent-soft` with `--ink`-on-light or
  white text, zebra striping (`--bg-elev-2`), 2–3px cell padding, thin `--rule-2` borders,
  tabular-nums for numeric columns.
- **Cards → panels:** the rounded elevated cards become flat panels with a section-header
  band (blue left border + `--accent-soft` background + blue caption), matching the mockup.
- **`PageHeader` / `ActionBar`:** compact; actions render as the toolbar button row.
- **Pills/badges:** keep rounded status pills (`good/warn/bad` soft backgrounds) — the one
  place radius stays pill-shaped.
- **New `Storyboard` component:** a left context rail (~130px) taking a title, status, and
  a list of label/value rows; used by campaign detail pages.
- Forms/inputs already centralized in `styles.css`; only minor module tweaks expected.

## Section 4 — Tailored pages

- **CampaignsPage (both verticals):** present the campaign list as a compact Epic data
  grid (dense rows, status pills, blue header), dropping the large editorial hero.
- **CampaignDetailPage (both verticals):** wrap content in the `Storyboard` rail (name,
  status, target, lead, top metric, delivery, run/mol counts) + dense delivery-verification
  and compounds/batches panels, as mocked.
- All other pages (Molecule, Run, SAR, SarScatter, Audit, Methods, BatchComparison,
  BatchTimeline) inherit via tokens + components; only spacing nudges if something looks
  off in screenshots.

## Section 5 — Tour + finish modal (`demoTour.css`, both `Layout.module.css`, both `DemoTour.tsx`)

Restyle Shepherd tooltips and the "That's LabLink" modal to the Epic look (square corners,
blue primary buttons, system font). **Preserve the recent fixes:** tour-title wrapping,
`createPortal(..., document.body)` for the finish modal, and the persistent (non
auto-starting) launcher. No behavioral changes to the tour.

## Constraints / invariants

- **Preserve all functionality**: routing, demo session, share/restart, guided tour,
  exports, verification.
- **Keep every `data-tour="…"` anchor** on all pages — the guided tour resolves elements
  by these selectors; moving markup must carry the attributes along.
- **No Epic trademarks/logos/product names.**
- **System fonts only** (no new webfont requests); remove the now-unused Google-font links.
- Edit duplicated module files in **both** dashboards identically.

## Verification

Use the existing Playwright harness (`frontend/demo-qa`) to screenshot both verticals
after the change: campaigns list, campaign detail (with Storyboard), 2–3 inherited pages
(molecule/run/audit), an active tour step, and the finish modal. Confirm: chrome renders,
density reads as Epic, tour anchors still resolve (tour advances through all steps), finish
modal is clean, and `tsc -b` passes for the served app. Compare against the approved mockup.

## Out of scope

- Backend/API changes; data model; the migration work already completed.
- Deep redesign of non-campaign pages' internal layouts.
- A separately-served wetlab build (only the cross-imported pages are reskinned).
- Dark mode, theming toggle, or responsive/mobile rework beyond what exists.
