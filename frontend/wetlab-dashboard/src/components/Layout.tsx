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
        <div className={styles.systemStatus}>
          <span className={styles.systemStatusDot} aria-hidden />
          <span>System nominal</span>
        </div>

        <Link to={withOrg("/campaigns", orgId)} className={styles.brand}>
          <span className={styles.mark} aria-hidden />
          <div>
            <strong>LabLink</strong>
            <span>Bioprocess Console</span>
          </div>
        </Link>

        <nav className={styles.nav}>
          <NavLink to={withOrg("/campaigns", orgId)}>Campaigns</NavLink>
        </nav>

        <div className={styles.orgBox}>
          <label htmlFor="org">Org</label>
          <input
            id="org"
            value={orgId}
            onChange={(event) => setOrgId(event.target.value)}
            placeholder="demo-therapeutics"
          />
        </div>
      </aside>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
