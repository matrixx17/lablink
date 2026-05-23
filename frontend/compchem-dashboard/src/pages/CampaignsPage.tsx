import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, Campaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  DataTable,
  EmptyState,
  ErrorBox,
  fmtDateOnly,
  HeroHeader,
  SecondaryButton,
  StatusBadge,
} from "../components/ui";
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
    <div className={`${styles.grid} ${styles.reveal}`}>
      <HeroHeader
        eyebrow="Campaign registry"
        title="Computational chemistry campaigns"
        context={
          <p>
            Docking, MD, DFT, and property runs aggregated by program. Each campaign
            tracks compound provenance, QC, audit history, and SAR across iterations.
          </p>
        }
        actions={<SecondaryButton as="a" href="/docs">API docs</SecondaryButton>}
      />

      {error ? <ErrorBox error={error} /> : null}

      <div className={styles.toolbar}>
        <input
          placeholder="Filter by campaign, project, or status"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter campaigns"
        />
        <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)} aria-label="Sort campaigns">
          <option value="started_at">Newest first</option>
          <option value="name">Campaign name</option>
          <option value="project_name">Project</option>
          <option value="run_count">Most runs</option>
          <option value="molecule_count">Most compounds</option>
        </select>
        <span className={styles.muted}>
          {loading ? "loading…" : `${rows.length} campaign${rows.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {loading ? <EmptyState>Loading campaigns…</EmptyState> : null}
      {!loading && rows.length === 0 ? (
        <EmptyState>No campaigns found for org {orgId}.</EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <DataTable>
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Project</th>
              <th>Status</th>
              <th>Runs</th>
              <th>Compounds</th>
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
                <td className="num">{c.run_count}</td>
                <td className="num">{c.molecule_count}</td>
                <td className="num">{fmtDateOnly(c.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      ) : null}
    </div>
  );
}
