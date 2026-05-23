import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useSearchParams } from "react-router-dom";
import { api, OrgInfo } from "../api/client";
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
          <div className={styles.wordmark}>
            LabLink<em>.</em>
          </div>
          <div className={styles.brandSub}>Computational Chemistry</div>
        </Link>

        <div>
          <div className={styles.navGroupLabel}>Workspace</div>
          <nav className={styles.nav}>
            <NavLink to={withOrg("/campaigns", orgId)} end>Campaigns</NavLink>
          </nav>
        </div>

        <div className={styles.orgBox}>
          <label htmlFor="org">Organization</label>
          <input
            id="org"
            value={orgId}
            onChange={(event) => setOrgId(event.target.value)}
            placeholder="default-org"
          />
        </div>

        <div className={styles.footerNote}>
          v0.1 · evidence-grade computational provenance
        </div>
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
