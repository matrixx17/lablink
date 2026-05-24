import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { api, Campaign, SarPoint, SarResponse } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, PageHeader } from "../components/ui";
import styles from "./pages.module.css";

const QC_COLORS: Record<string, string> = {
  pass: "#22c55e",
  completed: "#22c55e",
  normal: "#22c55e",
  warn: "#f59e0b",
  fail: "#ef4444",
  failed: "#ef4444",
  crashed: "#ef4444",
  unknown: "#94a3b8"
};

function colorFor(point: SarPoint) {
  const key = (point.qc_status || point.run_status || "unknown").toLowerCase();
  const match = Object.entries(QC_COLORS).find(([needle]) => key.includes(needle));
  return match?.[1] || QC_COLORS.unknown;
}

function metricLabel(metric: string) {
  return metric
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export default function SarPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [sar, setSar] = useState<SarResponse | null>(null);
  const [xMetric, setXMetric] = useState("");
  const [yMetric, setYMetric] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.campaign(id, orgId).then(setCampaign).catch(setError);
  }, [id, orgId]);

  useEffect(() => {
    api
      .campaignSar(id, orgId, xMetric || undefined, yMetric || undefined)
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
        actions={
          <button
            type="button"
            className={styles.secondaryButton}
            onClick={() => navigate(withOrg(`/campaigns/${id}`, orgId))}
          >
            ← Back to campaign
          </button>
        }
      />
      <Card>
        <div className={styles.sarControls}>
          <label>
            <span>X Axis</span>
            <select value={xMetric} onChange={(e) => setXMetric(e.target.value)}>
              {metrics.map((m) => (
                <option key={m} value={m}>
                  {metricLabel(m)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Y Axis</span>
            <select value={yMetric} onChange={(e) => setYMetric(e.target.value)}>
              {metrics.map((m) => (
                <option key={m} value={m}>
                  {metricLabel(m)}
                </option>
              ))}
            </select>
          </label>
          {sar ? (
            <div className={styles.countLine}>
              <strong>{sar.points.length}</strong> points
            </div>
          ) : null}
        </div>

        {!sar ? <EmptyState>Loading SAR data…</EmptyState> : null}
        {sar && sar.points.length === 0 ? (
          <EmptyState>
            No metric pairs available yet. Ingest at least two metrics per molecule/run.
          </EmptyState>
        ) : null}
        {sar && sar.points.length > 0 ? (
          <div className={styles.chart}>
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 18, right: 28, bottom: 48, left: 44 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis
                  type="number"
                  dataKey="x"
                  name={metricLabel(xMetric)}
                  label={{ value: metricLabel(xMetric), position: "bottom", offset: 18 }}
                  tick={{ fill: "#475569", fontSize: 12 }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name={metricLabel(yMetric)}
                  label={{
                    value: metricLabel(yMetric),
                    angle: -90,
                    position: "left",
                    offset: 20
                  }}
                  tick={{ fill: "#475569", fontSize: 12 }}
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  content={({ active, payload }) => {
                    if (!active || !payload?.[0]) return null;
                    const p = payload[0].payload as SarPoint;
                    return (
                      <div className={styles.sarTooltip}>
                        <strong>
                          {p.molecule_external_id || p.molecule_name || `Molecule ${p.molecule_id}`}
                        </strong>
                        <div className={styles.tooltipRow}>
                          <span>{metricLabel(p.x_metric)}</span>
                          <strong>
                            {p.x} {p.x_unit}
                          </strong>
                        </div>
                        <div className={styles.tooltipRow}>
                          <span>{metricLabel(p.y_metric)}</span>
                          <strong>
                            {p.y} {p.y_unit}
                          </strong>
                        </div>
                        <div className={styles.tooltipRow}>
                          <span>Run</span>
                          <strong>
                            #{p.run_id} · {p.qc_status || p.run_status}
                          </strong>
                        </div>
                        <div className={styles.tooltipHint}>Click to open molecule →</div>
                      </div>
                    );
                  }}
                />
                {Object.entries(grouped).map(([label, points]) => (
                  <Scatter
                    key={label}
                    name={label}
                    data={points}
                    isAnimationActive={false}
                    shape={(props: any) => (
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={6}
                        fill={colorFor(props.payload as SarPoint)}
                        stroke="#fff"
                        strokeWidth={1.5}
                        style={{ cursor: "pointer" }}
                      />
                    )}
                    onClick={(point: any) => {
                      const p = point?.payload as SarPoint | undefined;
                      if (p?.molecule_id) {
                        navigate(withOrg(`/molecules/${p.molecule_id}`, orgId));
                      }
                    }}
                  />
                ))}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
