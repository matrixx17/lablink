import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  WetlabBatch,
  WetlabSample,
  WetlabTimeseries,
} from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  Card,
  EmptyState,
  ErrorBox,
  fmtDate,
  fmtNumber,
  PageHeader,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

const COLORS = [
  "#2563eb", // blue
  "#dc2626", // red
  "#059669", // green
  "#d97706", // amber
  "#7c3aed", // violet
  "#0891b2", // cyan
  "#db2777", // pink
  "#65a30d", // lime
];

const DEFAULT_SELECTED = new Set([
  "ph",
  "do_percent",
  "viable_cell_density_e6_per_ml",
]);

type QcFlag = {
  hours: number;
  parameter: string;
  description: string;
  severity: "warn" | "fail";
};

// ------------ Helpers ------------------------------------------------------

function hoursSinceInoculation(unixSeconds: number, inoculationUnix: number): number {
  return (unixSeconds - inoculationUnix) / 3600;
}

function paramColor(name: string, allParams: string[]): string {
  const idx = allParams.indexOf(name);
  return COLORS[idx % COLORS.length];
}

function deriveQcFlags(
  series: WetlabTimeseries[],
  samples: WetlabSample[]
): QcFlag[] {
  const flags: QcFlag[] = [];

  // DO excursion: < 30% sustained for ≥ 1 sample point
  const doSeries = series.find((s) => s.parameter_name === "do_percent");
  if (doSeries && doSeries.inoculation_unix != null) {
    doSeries.values.forEach((v, i) => {
      if (v < 30) {
        flags.push({
          hours: hoursSinceInoculation(doSeries.timestamps[i], doSeries.inoculation_unix!),
          parameter: "do_percent",
          description: `Dissolved oxygen dropped to ${fmtNumber(v, 1)}% (setpoint 40%)`,
          severity: v < 20 ? "fail" : "warn",
        });
      }
    });
  }

  // pH deviation > 0.15 from running mean (very rough)
  const phSeries = series.find((s) => s.parameter_name === "ph");
  if (phSeries && phSeries.inoculation_unix != null && phSeries.values.length > 10) {
    const tail = phSeries.values.slice(10);
    const mean = tail.reduce((a, b) => a + b, 0) / tail.length;
    phSeries.values.forEach((v, i) => {
      if (i > 10 && Math.abs(v - mean) > 0.15) {
        flags.push({
          hours: hoursSinceInoculation(phSeries.timestamps[i], phSeries.inoculation_unix!),
          parameter: "ph",
          description: `pH deviation: ${fmtNumber(v, 2)} vs running mean ${fmtNumber(mean, 2)}`,
          severity: "warn",
        });
      }
    });
  }

  // VCD reversal: any drop > 15% between consecutive offline samples in growth phase
  const vcdSamples = samples
    .filter((s) => s.measurement_name === "viable_cell_density_e6_per_ml")
    .sort((a, b) => (a.sample_time_hours ?? 0) - (b.sample_time_hours ?? 0));
  for (let i = 1; i < vcdSamples.length; i++) {
    const prev = vcdSamples[i - 1].value ?? 0;
    const cur = vcdSamples[i].value ?? 0;
    const hours = vcdSamples[i].sample_time_hours ?? 0;
    if (hours < 9 * 24 && prev > 0 && cur < prev * 0.85) {
      flags.push({
        hours,
        parameter: "viable_cell_density_e6_per_ml",
        description: `VCD reversal during growth phase: ${fmtNumber(prev, 2)} → ${fmtNumber(cur, 2)} ×10⁶/mL`,
        severity: "warn",
      });
    }
  }

  // Offline samples explicitly flagged
  samples.forEach((s) => {
    if (s.qc_status === "warn" || s.qc_status === "fail") {
      flags.push({
        hours: s.sample_time_hours ?? 0,
        parameter: s.measurement_name,
        description: `Offline sample flagged (${s.qc_status}): ${fmtNumber(s.value ?? null, 2)} ${s.unit ?? ""}`.trim(),
        severity: s.qc_status === "fail" ? "fail" : "warn",
      });
    }
  });

  // Collapse duplicate near-time flags per parameter (keep first per 6h bucket)
  const seen = new Set<string>();
  return flags
    .sort((a, b) => a.hours - b.hours)
    .filter((f) => {
      const key = `${f.parameter}:${Math.floor(f.hours / 6)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

// ------------ Component ----------------------------------------------------

export default function BatchTimelinePage() {
  const { campaignId = "", batchId = "" } = useParams();
  const { orgId } = useOrgId();
  const [batch, setBatch] = useState<WetlabBatch | null>(null);
  const [series, setSeries] = useState<WetlabTimeseries[] | null>(null);
  const [samples, setSamples] = useState<WetlabSample[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<Set<string>>(DEFAULT_SELECTED);

  useEffect(() => {
    setBatch(null);
    setSeries(null);
    setSamples(null);
    setError(null);
    Promise.all([
      api.batch(batchId, orgId),
      api.batchTimeseries(batchId, orgId),
      api.batchSamples(batchId, orgId),
    ])
      .then(([b, ts, s]) => {
        setBatch(b);
        setSeries(ts);
        setSamples(s);
      })
      .catch(setError);
  }, [batchId, orgId]);

  const allParams = useMemo(() => {
    if (!series || !samples) return [];
    const set = new Set<string>();
    series.forEach((s) => set.add(s.parameter_name));
    samples.forEach((s) => set.add(s.measurement_name));
    return Array.from(set).sort();
  }, [series, samples]);

  const chartData = useMemo(() => {
    if (!series || !samples) return [];

    // Build a row per timepoint (hours since inoculation, rounded to nearest 0.1h)
    // Merge continuous + offline by hours bucket so the tooltip can show both.
    type Row = { hours: number; [k: string]: number };
    const rows = new Map<number, Row>();

    series.forEach((s) => {
      if (!selected.has(s.parameter_name)) return;
      const inoc = s.inoculation_unix ?? 0;
      s.timestamps.forEach((ts, i) => {
        const hours = Math.round(((ts - inoc) / 3600) * 10) / 10;
        if (!rows.has(hours)) rows.set(hours, { hours });
        rows.get(hours)![s.parameter_name] = s.values[i];
      });
    });

    samples.forEach((s) => {
      if (!selected.has(s.measurement_name)) return;
      const hours = Math.round((s.sample_time_hours ?? 0) * 10) / 10;
      if (!rows.has(hours)) rows.set(hours, { hours });
      // Use distinct key for scatter so Line + Scatter don't collide
      rows.get(hours)![`${s.measurement_name}__offline`] = s.value ?? NaN;
    });

    return Array.from(rows.values()).sort((a, b) => a.hours - b.hours);
  }, [series, samples, selected]);

  const qcFlags = useMemo(() => {
    if (!series || !samples) return [];
    return deriveQcFlags(series, samples);
  }, [series, samples]);

  const continuousParams = useMemo(
    () => (series ? series.filter((s) => selected.has(s.parameter_name)) : []),
    [series, selected]
  );
  const offlineParams = useMemo(() => {
    if (!samples) return [];
    const set = new Set<string>();
    samples.forEach((s) => {
      if (selected.has(s.measurement_name)) set.add(s.measurement_name);
    });
    return Array.from(set);
  }, [samples, selected]);

  if (error) return <ErrorBox error={error} />;
  if (!batch || !series || !samples) {
    return <EmptyState>Loading batch timeline...</EmptyState>;
  }

  const toggleParam = (p: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={`Batch / ${batch.bioreactor_model || "Bioreactor"}`}
        title={batch.batch_number || `Batch ${batch.id.slice(0, 8)}`}
        actions={
          <>
            <Link
              className={styles.secondaryButton}
              to={withOrg(`/campaigns/${campaignId}/compare`, orgId)}
            >
              Batch Comparison →
            </Link>
            <Link
              className={styles.secondaryButton}
              to={withOrg(`/campaigns/${campaignId}`, orgId)}
            >
              Back to campaign
            </Link>
          </>
        }
      />

      <div className={styles.stats}>
        <Card><StatusBadge status={batch.status} /></Card>
        <Card>Inoculated: {fmtDate(batch.inoculation_date)}</Card>
        <Card>Harvest: {fmtDate(batch.harvest_date)}</Card>
        <Card>Volume: {batch.volume_liters ? `${fmtNumber(batch.volume_liters, 2)} L` : "-"}</Card>
        <Card>Cell line: {batch.cell_line || "-"}</Card>
      </div>

      <Card>
        <h2>Parameters</h2>
        <div className={styles.toggleRow}>
          {allParams.map((p) => {
            const active = selected.has(p);
            return (
              <button
                key={p}
                type="button"
                onClick={() => toggleParam(p)}
                className={active ? styles.toggleOn : styles.toggleOff}
                style={active ? { borderColor: paramColor(p, allParams), color: paramColor(p, allParams) } : undefined}
              >
                {p}
              </button>
            );
          })}
        </div>

        <div style={{ width: "100%", height: 460 }}>
          <ResponsiveContainer>
            <ComposedChart data={chartData} margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis
                dataKey="hours"
                type="number"
                domain={[0, 336]}
                ticks={[0, 48, 96, 144, 192, 240, 288, 336]}
                label={{ value: "Hours since inoculation", position: "insideBottom", offset: -8 }}
              />
              <YAxis />
              <Tooltip
                formatter={(value, name) => [
                  fmtNumber(typeof value === "number" ? value : Number(value), 3),
                  String(name).replace("__offline", " (offline)"),
                ]}
                labelFormatter={(h) => `t = ${fmtNumber(h as number, 1)} h`}
              />
              <Legend />
              {continuousParams.map((s) => (
                <Line
                  key={s.id}
                  type="monotone"
                  dataKey={s.parameter_name}
                  stroke={paramColor(s.parameter_name, allParams)}
                  dot={false}
                  strokeWidth={1.5}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
              {offlineParams.map((p) => (
                <Scatter
                  key={p}
                  name={`${p} (offline)`}
                  dataKey={`${p}__offline`}
                  fill={paramColor(p, allParams)}
                  shape="diamond"
                />
              ))}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <h2>QC Flags</h2>
        {qcFlags.length === 0 ? (
          <EmptyState>No QC flags raised for this batch.</EmptyState>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Parameter</th>
                  <th>Description</th>
                  <th>Severity</th>
                </tr>
              </thead>
              <tbody>
                {qcFlags.map((f, i) => (
                  <tr key={`${f.parameter}-${i}`}>
                    <td>t = {fmtNumber(f.hours, 1)} h</td>
                    <td>{f.parameter}</td>
                    <td>{f.description}</td>
                    <td><StatusBadge status={f.severity} /></td>
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
