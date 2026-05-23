import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import { api, Campaign, OrgInfo } from "../api/client";
import styles from "./Layout.module.css";

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
  return `${path}${path.includes("?") ? "&" : "?"}org=${encodeURIComponent(orgId)}`;
}

export default function Layout() {
  const { orgId, setOrgId } = useOrgId();
  const [org, setOrg] = useState<OrgInfo | null>(null);

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

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link to={withOrg("/campaigns", orgId)} className={styles.brand}>
          <span className={styles.mark} aria-hidden />
          <div>
            <strong>LabLink</strong>
            <span>Comp-Chem Console</span>
          </div>
        </Link>
        <nav className={styles.nav}>
          <NavLink to={withOrg("/campaigns", orgId)}>Campaigns</NavLink>
        </nav>
        <div className={styles.orgBox}>
          <label htmlFor="org">Org ID</label>
          <input
            id="org"
            value={orgId}
            onChange={(event) => setOrgId(event.target.value)}
            placeholder="default-org"
          />
        </div>
      </aside>
      <main className={styles.main}>
        {org?.demo_mode ? (
          <div className={styles.demoBanner} data-lablink-demo-banner="true">
            <strong>Demo Environment</strong>; data resets periodically.{" "}
            <span>Create a free account to use your own data.</span>
          </div>
        ) : null}
        <Outlet />
      </main>
      {org?.demo_mode ? <DemoGuide orgId={orgId} demoBannerShowing /> : null}
    </div>
  );
}

function DemoGuide({ orgId, demoBannerShowing }: { orgId: string; demoBannerShowing: boolean }) {
  const location = useLocation();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [visited, setVisited] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(`lablink-demo-guide:${orgId}`) || "[]");
    } catch {
      return [];
    }
  });
  const [open, setOpen] = useState(() => visited.length < 2);

  useEffect(() => {
    api.campaigns(orgId)
      .then((campaigns) => setCampaign(campaigns[0] || null))
      .catch(() => setCampaign(null));
  }, [orgId]);

  useEffect(() => {
    try {
      const stored = JSON.parse(localStorage.getItem(`lablink-demo-guide:${orgId}`) || "[]");
      setVisited(Array.isArray(stored) ? stored : []);
      setOpen(!Array.isArray(stored) || stored.length < 2);
    } catch {
      setVisited([]);
      setOpen(true);
    }
  }, [orgId]);

  const campaignId = campaign?.id;
  const steps = [
    {
      id: "overview",
      label: "1. Campaign Overview — see the full campaign summary and lead candidate",
      to: campaignId ? withOrg(`/campaigns/${campaignId}`, orgId) : withOrg("/campaigns", orgId),
      matches: () => Boolean(campaignId && location.pathname === `/campaigns/${campaignId}`)
    },
    {
      id: "sar",
      label: "2. SAR Explorer — explore structure-activity relationships visually",
      to: campaignId ? withOrg(`/campaigns/${campaignId}/sar`, orgId) : withOrg("/campaigns", orgId),
      matches: () => Boolean(campaignId && (
        location.pathname === `/campaigns/${campaignId}/sar` ||
        location.pathname === `/campaigns/${campaignId}/molecules`
      ))
    },
    {
      id: "lead",
      label: "3. Lead Compound — view AC-007's full computational history",
      to: campaign?.lead_molecule_id ? withOrg(`/molecules/${campaign.lead_molecule_id}`, orgId) : withOrg("/campaigns", orgId),
      matches: () => Boolean(campaign?.lead_molecule_id && location.pathname === `/molecules/${campaign.lead_molecule_id}`)
    },
    {
      id: "audit",
      label: "4. Audit Trail — verify the tamper-evident delivery record",
      to: campaignId ? withOrg(`/campaigns/${campaignId}/audit`, orgId) : withOrg("/campaigns", orgId),
      matches: () => Boolean(campaignId && location.pathname === `/campaigns/${campaignId}/audit`)
    },
    {
      id: "methods",
      label: "5. Methods Export — copy the auto-generated methods section",
      to: campaignId ? withOrg(`/campaigns/${campaignId}/methods-export`, orgId) : withOrg("/campaigns", orgId),
      matches: () => Boolean(campaignId && location.pathname === `/campaigns/${campaignId}/methods-export`)
    }
  ];

  useEffect(() => {
    const matched = steps.find((step) => step.matches())?.id;
    if (!matched || visited.includes(matched)) return;
    const next = [...visited, matched];
    setVisited(next);
    localStorage.setItem(`lablink-demo-guide:${orgId}`, JSON.stringify(next));
    if (next.length >= 2) setOpen(false);
  }, [location.pathname, orgId, steps, visited]);

  const shouldRender =
    window.location.hostname.includes("demo") ||
    demoBannerShowing ||
    document.body.dataset.lablinkDemo === "true" ||
    Boolean(document.querySelector("[data-lablink-demo-banner='true']"));

  if (!shouldRender) return null;

  return (
    <div className={styles.demoGuide}>
      <button type="button" className={styles.demoGuideToggle} onClick={() => setOpen((current) => !current)}>
        📋 Demo Walkthrough
        <span>{visited.length}/5</span>
      </button>
      {open ? (
        <div className={styles.demoGuidePanel}>
          <strong>📋 Demo Walkthrough</strong>
          <div className={styles.demoGuideList}>
            {steps.map((step) => (
              <Link key={step.id} to={step.to}>
                <span>{visited.includes(step.id) ? "☑" : "☐"}</span>
                {step.label}
              </Link>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
