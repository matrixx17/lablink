import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { api, Campaign, SarPoint, SarResponse } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, PageHeader } from "../components/ui";
import styles from "./pages.module.css";

const COLORS: Record<string, string> = {
  pass: "#16a34a",
  completed: "#16a34a",
  warn: "#f59e0b",
  fail: "#dc2626",
  failed: "#dc2626",
  crashed: "#dc2626",
  unknown: "#64748b"
};

function colorFor(point: SarPoint) {
  const key = (point.qc_status || point.run_status || "unknown").toLowerCase();
  return Object.entries(COLORS).find(([needle]) => key.includes(needle))?.[1] || COLORS.unknown;
}

export default function SarPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [sar, setSar] = useState<SarResponse | null>(null);
  const [xMetric, setXMetric] = useState("");
  const [yMetric, setYMetric] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.campaign(id, orgId).then(setCampaign).catch(setError);
  }, [id, orgId]);

  useEffect(() => {
    api.campaignSar(id, orgId, xMetric || undefined, yMetric || undefined)
      .then((result) => {
        setSar(result);
        if (!xMetric && result.metric_names[0]) setXMetric(result.metric_names[0]);
        if (!yMetric && result.metric_names[1]) setYMetric(result.metric_names[1]);
        if (!yMetric && result.metric_names.length === 1) setYMetric(result.metric_names[0]);
      })
      .catch(setError);
  }, [id, orgId, xMetric, yMetric]);

  const grouped = useMemo(() => {
    const buckets: Record<string, SarPoint[]> = {};
    for (const point of sar?.points || []) {
      const label = point.qc_status || point.run_status || "unknown";
      buckets[label] = [...(buckets[label] || []), point];
    }
    return buckets;
  }, [sar]);

  if (error) return <ErrorBox error={error} />;

  const metrics = sar?.metric_names || [];
  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={campaign?.name || "SAR"}
        title="SAR scatter plot"
        actions={<Link className={styles.secondaryButton} to={withOrg(`/campaigns/${id}`, orgId)}>Back to campaign</Link>}
      />
      <Card>
        <div className={styles.toolbar}>
          <label>
            X axis{" "}
            <select value={xMetric} onChange={(e) => setXMetric(e.target.value)}>
              {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label>
            Y axis{" "}
            <select value={yMetric} onChange={(e) => setYMetric(e.target.value)}>
              {metrics.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
        </div>
        {!sar ? <EmptyState>Loading SAR data...</EmptyState> : null}
        {sar && sar.points.length === 0 ? <EmptyState>No metric pairs available yet. Ingest at least two metrics per molecule/run.</EmptyState> : null}
        {sar && sar.points.length > 0 ? (
          <div className={styles.chart}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 20, right: 24, bottom: 28, left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" dataKey="x" name={xMetric} label={{ value: xMetric, position: "bottom" }} />
                <YAxis type="number" dataKey="y" name={yMetric} label={{ value: yMetric, angle: -90, position: "left" }} />
                <ZAxis range={[80, 80]} />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  content={({ active, payload }) => {
                    if (!active || !payload?.[0]) return null;
                    const p = payload[0].payload as SarPoint;
                    return (
                      <div style={{ background: "white", border: "1px solid #cbd5e1", borderRadius: 10, padding: 10 }}>
                        <strong>{p.molecule_external_id || p.molecule_name || `Molecule ${p.molecule_id}`}</strong>
                        <div>{p.x_metric}: {p.x} {p.x_unit}</div>
                        <div>{p.y_metric}: {p.y} {p.y_unit}</div>
                        <div>Run {p.run_id} / {p.qc_status || p.run_status}</div>
                        <Link className={styles.link} to={withOrg(`/molecules/${p.molecule_id}`, orgId)}>Open molecule</Link>
                      </div>
                    );
                  }}
                />
                {Object.entries(grouped).map(([label, points]) => (
                  <Scatter key={label} name={label} data={points} fill={colorFor(points[0])} />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
