import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, WetlabCampaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

export default function CampaignsPage() {
  const { orgId } = useOrgId();
  const [campaigns, setCampaigns] = useState<WetlabCampaign[]>([]);
  const [filter, setFilter] = useState("");
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
    return campaigns
      .filter((c) => `${c.name} ${c.description || ""}`.toLowerCase().includes(q))
      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  }, [campaigns, filter]);

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow="Campaign registry"
        title="Bioprocess campaigns"
        actions={<a className={styles.secondaryButton} href="/docs">API docs</a>}
      />
      {error ? <ErrorBox error={error} /> : null}
      <Card>
        <div className={styles.toolbar}>
          <input
            placeholder="Filter campaigns"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        {loading ? <EmptyState>Loading campaigns...</EmptyState> : null}
        {!loading && rows.length === 0 ? (
          <EmptyState>No wet lab campaigns found for org {orgId}.</EmptyState>
        ) : null}
        {rows.length > 0 ? (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Campaign</th>
                  <th>Target / process</th>
                  <th>Status</th>
                  <th>Batches</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => {
                  const extra = (c.extra_params || {}) as {
                    target?: string;
                    process_type?: string;
                    status?: string;
                  };
                  return (
                    <tr key={c.id}>
                      <td>
                        <Link className={styles.link} to={withOrg(`/campaigns/${c.id}`, orgId)}>
                          {c.name}
                        </Link>
                        <div className={styles.muted}>{c.domain}</div>
                      </td>
                      <td>
                        {extra.target || "-"}
                        {extra.process_type ? (
                          <div className={styles.muted}>{extra.process_type}</div>
                        ) : null}
                      </td>
                      <td><StatusBadge status={extra.status || "active"} /></td>
                      <td>{c.batch_count}</td>
                      <td>{fmtDate(c.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
