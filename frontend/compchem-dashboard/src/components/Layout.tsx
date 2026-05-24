import { useEffect, useState } from "react";
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
  const demoExpiring = demoMinutes != null && demoMinutes < 10;
  const demoHours = demoMinutes == null ? 0 : Math.floor(demoMinutes / 60);
  const demoMins = demoMinutes == null ? 0 : demoMinutes % 60;

  const compchemHome = `/campaigns?org=${encodeURIComponent(orgId)}`;
  const wetlabHome = `/wetlab/campaigns?org=${encodeURIComponent(orgId)}`;
  const homePath = vertical === "wetlab" ? wetlabHome : compchemHome;
  const brandSub = vertical === "wetlab" ? "Bioprocess" : "Computational Chemistry";
  const footerNote = vertical === "wetlab"
    ? "v0.1 · evidence-grade bioprocess provenance"
    : "v0.1 · evidence-grade computational provenance";

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link to={homePath} className={styles.brand}>
          <div className={styles.wordmark}>
            LabLink<em>.</em>
          </div>
          <div className={styles.brandSub}>{brandSub}</div>
        </Link>

        <div className={styles.verticalSwitcher} role="tablist" aria-label="Switch vertical">
          <Link
            to={compchemHome}
            role="tab"
            aria-selected={vertical === "compchem"}
            className={vertical === "compchem" ? styles.verticalSwitcherActive : styles.verticalSwitcherInactive}
          >
            Comp Chem
          </Link>
          <Link
            to={wetlabHome}
            role="tab"
            aria-selected={vertical === "wetlab"}
            className={vertical === "wetlab" ? styles.verticalSwitcherActive : styles.verticalSwitcherInactive}
          >
            Wet Lab
          </Link>
        </div>

        <div>
          <div className={styles.navGroupLabel}>Workspace</div>
          <nav className={styles.nav}>
            <NavLink to={withOrg("/campaigns", orgId)} end>Campaigns</NavLink>
            {vertical === "wetlab" ? (
              <WetlabDemoTour orgId={orgId} />
            ) : (
              <CompchemDemoTour orgId={orgId} />
            )}
          </nav>
          {(org?.demo_mode || demoMinutes != null) ? (
            <div className={styles.demoShareNav}>
              <button type="button" onClick={shareDemo}>Share this demo</button>
              {shareMessage ? <p>{shareMessage}</p> : null}
            </div>
          ) : null}
        </div>

        <div className={styles.orgBox}>
          {demoMinutes != null ? (
            <div className={`${styles.demoChip} ${demoExpiring ? styles.demoChipExpiring : ""}`}>
              {demoExpiring ? (
                <>
                  <span>Demo expiring —</span>
                  <button type="button" onClick={restartDemo}>Restart demo</button>
                </>
              ) : (
                <span>Demo Mode — {demoHours}h {demoMins.toString().padStart(2, "0")}m remaining</span>
              )}
            </div>
          ) : null}
          <label htmlFor="org">Organization</label>
          <input
            id="org"
            value={orgId}
            onChange={(event) => setOrgId(event.target.value)}
            placeholder="default-org"
          />
        </div>

        <div className={styles.footerNote}>{footerNote}</div>
      </aside>

      <main className={styles.main}>
        {org?.demo_mode ? (
          <div className={styles.demoBanner} data-lablink-demo-banner="true">
            <strong>Demo environment.</strong>
            <span>Data resets periodically. Create a free workspace to use your own.</span>
          </div>
        ) : null}
        <div className={styles.mainInner}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
