import { Link, NavLink, Outlet, useSearchParams } from "react-router-dom";
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

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar}>
        <Link to={withOrg("/campaigns", orgId)} className={styles.brand}>
          <div className={styles.wordmark}>
            LabLink<em>.</em>
          </div>
          <div className={styles.brandSub}>Bioprocess</div>
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
            placeholder="demo-therapeutics"
          />
        </div>

        <div className={styles.footerNote}>
          v0.1 · evidence-grade bioprocess provenance
        </div>
      </aside>

      <main className={styles.main}>
        <div className={styles.mainInner}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
