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
