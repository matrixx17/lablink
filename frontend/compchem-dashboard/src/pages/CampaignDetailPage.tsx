import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Campaign, CampaignRun } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, fmtNumber, PageHeader, Stat, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [runs, setRuns] = useState<CampaignRun[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([api.campaign(id, orgId), api.campaignRuns(id, orgId)])
      .then(([c, r]) => {
        setCampaign(c);
        setRuns(r);
      })
      .catch(setError);
  }, [id, orgId]);

  const filteredRuns = useMemo(() => {
    return status ? runs.filter((run) => run.status === status || run.qc_status === status) : runs;
  }, [runs, status]);

  const statusCounts = useMemo(() => {
    return runs.reduce<Record<string, number>>((acc, run) => {
      const key = run.qc_status || run.status || "unknown";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }, [runs]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign) return <EmptyState>Loading campaign...</EmptyState>;

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={campaign.project_name}
        title={campaign.name}
        actions={
          <>
            <Link className={styles.secondaryButton} to={withOrg(`/campaigns/${id}/molecules`, orgId)}>SAR scatter</Link>
            <Link className={styles.secondaryButton} to={withOrg(`/audit/${id}`, orgId)}>Audit log</Link>
            <a className={styles.button} href={`/api/v1/campaigns/${id}/export?org_id=${encodeURIComponent(orgId)}&format=csv`}>
              Export CSV
            </a>
          </>
        }
      />
      <div className={styles.stats}>
        <Stat label="Runs" value={campaign.run_count} />
        <Stat label="Molecules" value={campaign.molecule_count} />
        <Stat label="Flagged / failed" value={Object.entries(statusCounts).filter(([k]) => /warn|fail|crash|flag/i.test(k)).reduce((n, [, v]) => n + v, 0)} />
        <Stat label="Started" value={fmtDate(campaign.started_at)} />
      </div>
      <Card>
        <div className={styles.toolbar}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {Object.keys(statusCounts).map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        {filteredRuns.length === 0 ? <EmptyState>No runs yet.</EmptyState> : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Molecule</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>QC</th>
                  <th>Software</th>
                  <th>Metrics</th>
                  <th>Completed</th>
                </tr>
              </thead>
              <tbody>
                {filteredRuns.map((run) => (
                  <tr key={run.id}>
                    <td><Link className={styles.link} to={withOrg(`/runs/${run.id}`, orgId)}>Run {run.id}</Link></td>
                    <td>
                      {run.molecule_id ? (
                        <Link className={styles.link} to={withOrg(`/molecules/${run.molecule_id}`, orgId)}>
                          {run.molecule_external_id || run.molecule_name || `Molecule ${run.molecule_id}`}
                        </Link>
                      ) : <span className={styles.muted}>multi / none</span>}
                    </td>
                    <td>{run.run_kind}</td>
                    <td><StatusBadge status={run.status} /></td>
                    <td><StatusBadge status={run.qc_status} /></td>
                    <td>{[run.software_name, run.software_version].filter(Boolean).join(" ") || "-"}</td>
                    <td>{fmtNumber(run.metric_count, 0)}</td>
                    <td>{fmtDate(run.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
