import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Artifact, RunDetail } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, fmtNumber, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

function ArtifactTable({ artifacts, kind, orgId }: { artifacts: Artifact[]; kind: "input" | "output"; orgId: string }) {
  const download = async (artifact: Artifact) => {
    const result = await api.artifactDownload(kind, artifact.id, orgId);
    window.open(result.url, "_blank", "noopener,noreferrer");
  };

  if (artifacts.length === 0) return <EmptyState>No {kind} artifacts recorded.</EmptyState>;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Filename</th>
            <th>Kind</th>
            <th>Size</th>
            <th>Hash</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {artifacts.map((artifact) => (
            <tr key={artifact.id}>
              <td>{artifact.filename}</td>
              <td>{artifact.input_kind || artifact.output_kind || "-"}</td>
              <td>{artifact.file_size_bytes ? `${fmtNumber(artifact.file_size_bytes, 0)} B` : "-"}</td>
              <td><span className={styles.muted}>{artifact.file_hash?.slice(0, 16) || "-"}</span></td>
              <td><button className={styles.secondaryButton} onClick={() => download(artifact)}>Download</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RunPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.run(id, orgId).then(setRun).catch(setError);
  }, [id, orgId]);

  if (error) return <ErrorBox error={error} />;
  if (!run) return <EmptyState>Loading run...</EmptyState>;

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={`${run.run_kind} / ${run.software_name || "unknown software"}`}
        title={run.name || run.external_run_id || `Run ${run.id}`}
        actions={<Link className={styles.secondaryButton} to={withOrg(`/campaigns/${run.campaign_id}`, orgId)}>Back to campaign</Link>}
      />
      {run.was_inferred ? (
        <div className={styles.warningBanner}>
          ⚠ Metadata for this run was inferred from the directory path. Verify the details below are correct.
        </div>
      ) : null}
      <div className={styles.stats} data-tour="compchem-run-detail">
        <Card><StatusBadge status={run.status} /></Card>
        <Card><StatusBadge status={(run.qc as { overall_status?: string } | null)?.overall_status} /></Card>
        <Card>Wall time: {run.wall_time_s ? `${fmtNumber(run.wall_time_s / 60)} min` : "-"}</Card>
        <Card>Metrics: {run.metrics.length}</Card>
      </div>
      <div className={styles.twoCol}>
        <Card>
          <h2>Input parameters</h2>
          <table className={styles.table}>
            <tbody>
              <tr><th>Software</th><td>{[run.software_name, run.software_version].filter(Boolean).join(" ") || "-"}</td></tr>
              <tr><th>Forcefield</th><td>{run.forcefield || "-"}</td></tr>
              <tr><th>Config hash</th><td>{run.config_hash || "-"}</td></tr>
              <tr><th>Compute env</th><td>{run.compute_environment || "-"}</td></tr>
              <tr><th>CLI args</th><td>{run.cli_args || "-"}</td></tr>
            </tbody>
          </table>
          <h3>Compute details</h3>
          <pre className={styles.json}>{JSON.stringify(run.compute_details || {}, null, 2)}</pre>
        </Card>
        <Card>
          <h2>Output metrics</h2>
          <table className={styles.table}>
            <thead>
              <tr><th>Metric</th><th>Value</th><th>Unit</th><th>stderr</th></tr>
            </thead>
            <tbody>
              {run.metrics.map((metric) => (
                <tr key={metric.id}>
                  <td>{metric.name}</td>
                  <td>{fmtNumber(metric.value)}</td>
                  <td>{metric.unit}</td>
                  <td>{fmtNumber(metric.stderr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      <Card>
        <h2>QC results</h2>
        <pre className={styles.json}>{JSON.stringify(run.qc || {}, null, 2)}</pre>
      </Card>
      <Card>
        <h2>Input files</h2>
        <ArtifactTable artifacts={run.inputs} kind="input" orgId={orgId} />
      </Card>
      <Card>
        <h2>Output files</h2>
        <ArtifactTable artifacts={run.outputs} kind="output" orgId={orgId} />
      </Card>
      <Card>
        <h2>Audit events</h2>
        <table className={styles.table}>
          <thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Hash</th></tr></thead>
          <tbody>
            {run.audit_events.map((event) => (
              <tr key={event.id}>
                <td>{fmtDate(event.timestamp)}</td>
                <td>{event.action}</td>
                <td>{event.actor || "-"}</td>
                <td><span className={styles.muted}>{event.record_hash?.slice(0, 20)}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
