import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Campaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

type SortKey = "name" | "project_name" | "run_count" | "molecule_count" | "started_at";

export default function CampaignsPage() {
  const { orgId } = useOrgId();
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("started_at");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.campaigns(orgId)
      .then(setCampaigns)
      .catch(setError)
      .finally(() => setLoading(false));
  }, [orgId]);

  const rows = useMemo(() => {
    const q = filter.toLowerCase();
    return [...campaigns]
      .filter((c) => `${c.name} ${c.project_name} ${c.status}`.toLowerCase().includes(q))
      .sort((a, b) => {
        if (sortKey === "run_count" || sortKey === "molecule_count") return b[sortKey] - a[sortKey];
        return String(b[sortKey] || "").localeCompare(String(a[sortKey] || ""));
      });
  }, [campaigns, filter, sortKey]);

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow="Campaign registry"
        title="Comp-chem campaigns"
        actions={<a className={styles.secondaryButton} href="http://localhost:8000/docs">API docs</a>}
      />
      {error ? <ErrorBox error={error} /> : null}
      <Card>
        <div className={styles.toolbar}>
          <input placeholder="Filter campaigns" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
            <option value="started_at">Sort: newest</option>
            <option value="name">Sort: campaign</option>
            <option value="project_name">Sort: project</option>
            <option value="run_count">Sort: runs</option>
            <option value="molecule_count">Sort: molecules</option>
          </select>
        </div>
        {loading ? <EmptyState>Loading campaigns...</EmptyState> : null}
        {!loading && rows.length === 0 ? <EmptyState>No campaigns found for org {orgId}.</EmptyState> : null}
        {rows.length > 0 ? (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Project</th>
                  <th>Status</th>
                  <th>Runs</th>
                  <th>Molecules</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link className={styles.link} to={withOrg(`/campaigns/${c.id}`, orgId)}>{c.name}</Link>
                      <div className={styles.muted}>{c.campaign_type}</div>
                    </td>
                    <td>{c.project_name}</td>
                    <td><StatusBadge status={c.status} /></td>
                    <td>{c.run_count}</td>
                    <td>{c.molecule_count}</td>
                    <td>{fmtDate(c.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
