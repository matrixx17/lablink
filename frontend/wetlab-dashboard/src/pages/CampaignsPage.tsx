import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, WetlabCampaign } from "../api/client";
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

export default function CampaignsPage() {
  const { orgId } = useOrgId();
  const [campaigns, setCampaigns] = useState<WetlabCampaign[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api
      .campaigns(orgId)
      .then(setCampaigns)
      .catch(setError)
      .finally(() => setLoading(false));
  }, [orgId]);

  const rows = useMemo(() => {
    const q = filter.toLowerCase();
    return campaigns
      .filter((c) =>
        `${c.name} ${c.description || ""}`.toLowerCase().includes(q),
      )
      .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  }, [campaigns, filter]);

  return (
    <div className={styles.grid}>
      <HeroHeader
        eyebrow="Campaigns"
        title="Bioprocess, with provenance."
        context={
          <p>
            Every bioreactor campaign, every batch, every controller trace —
            captured in a tamper-evident audit chain and packaged for
            regulatory review.
          </p>
        }
        actions={<SecondaryButton as="a" href="/docs">API reference</SecondaryButton>}
      />

      {error ? <ErrorBox error={error} /> : null}

      <div className={styles.toolbar}>
        <input
          placeholder="Filter campaigns by name or description"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter campaigns"
        />
        <span className={styles.muted}>
          {loading
            ? "loading…"
            : `${rows.length} campaign${rows.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {!loading && rows.length === 0 ? (
        <EmptyState>
          {filter
            ? "No campaigns match this filter."
            : `No campaigns recorded for org ${orgId}.`}
        </EmptyState>
      ) : null}

      {rows.length > 0 ? (
        <DataTable>
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
                    <div className={styles.muted}>
                      {c.domain.toUpperCase()} · {c.id.slice(0, 8)}
                    </div>
                  </td>
                  <td>
                    <div>{extra.target || "—"}</div>
                    {extra.process_type ? (
                      <div className={styles.muted}>{extra.process_type}</div>
                    ) : null}
                  </td>
                  <td>
                    <StatusBadge status={extra.status || "active"} />
                  </td>
                  <td className="num">{c.batch_count}</td>
                  <td className="num">{fmtDateOnly(c.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      ) : null}
    </div>
  );
}
