import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, MoleculeDetail } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, fmtNumber, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

export default function MoleculePage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [molecule, setMolecule] = useState<MoleculeDetail | null>(null);
  const [metric, setMetric] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.molecule(id, orgId).then((mol) => {
      setMolecule(mol);
      const first = mol.assay_results[0]?.metric_name;
      if (first) setMetric(first);
    }).catch(setError);
  }, [id, orgId]);

  const metricNames = useMemo(() => {
    return Array.from(new Set((molecule?.assay_results || []).map((a) => a.metric_name))).sort();
  }, [molecule]);

  const chartData = useMemo(() => {
    return (molecule?.assay_results || [])
      .filter((a) => !metric || a.metric_name === metric)
      .slice()
      .reverse()
      .map((a, index) => ({ index: index + 1, value: a.value, metric: a.metric_name, unit: a.unit }));
  }, [molecule, metric]);

  if (error) return <ErrorBox error={error} />;
  if (!molecule) return <EmptyState>Loading molecule...</EmptyState>;

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={molecule.external_id || molecule.inchi_key}
        title={molecule.name || `Molecule ${molecule.id}`}
        actions={<Link className={styles.secondaryButton} to={withOrg(`/campaigns/${molecule.campaign_id}/molecules`, orgId)}>Back to SAR</Link>}
      />
      <div className={styles.twoCol}>
        <Card>
          <img
            className={styles.structure}
            src={api.moleculeSvgUrl(molecule.id, orgId)}
            alt={`2D structure for ${molecule.name || molecule.id}`}
          />
          <p className={styles.muted}>{molecule.canonical_smiles}</p>
          <table className={styles.table}>
            <tbody>
              <tr><th>Formula</th><td>{molecule.formula || "-"}</td></tr>
              <tr><th>MW</th><td>{fmtNumber(molecule.molecular_weight)}</td></tr>
              <tr><th>InChIKey</th><td>{molecule.inchi_key}</td></tr>
            </tbody>
          </table>
        </Card>
        <Card>
          <div className={styles.toolbar}>
            <label>
              Metric{" "}
              <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                {metricNames.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
          </div>
          {chartData.length === 0 ? <EmptyState>No metrics recorded for this molecule.</EmptyState> : (
            <div className={styles.chart}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 16, right: 24, bottom: 20, left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="index" label={{ value: "Run order", position: "bottom" }} />
                  <YAxis />
                  <Tooltip />
                  <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} dot />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>
      <Card>
        <h2>Associated runs</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Run</th>
                <th>Kind</th>
                <th>Status</th>
                <th>Software</th>
                <th>Metrics</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {molecule.runs.map((run) => (
                <tr key={run.id}>
                  <td><Link className={styles.link} to={withOrg(`/runs/${run.id}`, orgId)}>Run {run.id}</Link></td>
                  <td>{run.run_kind}</td>
                  <td><StatusBadge status={run.status} /></td>
                  <td>{[run.software_name, run.software_version].filter(Boolean).join(" ") || "-"}</td>
                  <td>{run.metric_count}</td>
                  <td>{fmtDate(run.completed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
