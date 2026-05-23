import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
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
  ActionBar,
  DataTable,
  EmptyState,
  ErrorBox,
  fmtDateOnly,
  fmtNumber,
  HeroHeader,
  Kpi,
  KpiStrip,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

// SCADA palette — cyan and amber lead, deep red for excursions, then
// muted accents for additional parameters. Deliberately not a generic
// "rainbow" — only the lead trace, the setpoint trace, and the excursion
// channel should command attention.
const TRACE_COLORS: Record<string, string> = {
  ph: "#5dd0e0",                            // cyan — measured
  do_percent: "#f5a623",                    // amber — setpoint axis
  temperature_c: "#a78bfa",                 // muted violet
  agitation_rpm: "#94a3b8",                 // slate
  viable_cell_density_e6_per_ml: "#4ade80", // green
  viability_percent: "#86efac",
  titer_mg_per_l: "#f59e0b",
  glucose_g_per_l: "#fde68a",
  lactate_g_per_l: "#fb923c",
  osmolality_mosm: "#cbd5e1",
};
const FALLBACK = ["#5dd0e0", "#f5a623", "#4ade80", "#fb923c", "#a78bfa", "#cbd5e1", "#e64545"];

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

// ---------- Helpers ---------------------------------------------------------

function hoursSinceInoculation(unixSeconds: number, inoculationUnix: number): number {
  return (unixSeconds - inoculationUnix) / 3600;
}

function paramColor(name: string, allParams: string[]): string {
  if (TRACE_COLORS[name]) return TRACE_COLORS[name];
  const idx = allParams.indexOf(name);
  return FALLBACK[idx % FALLBACK.length];
}

function useRuntimeCounter(inoculationDate?: string | null): string {
  // Reading the human elapsed time since inoculation, updated every second.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (!inoculationDate) return "T+ --:--:--";
  const t0 = new Date(inoculationDate).getTime();
  const elapsed = Math.max(0, now - t0);
  const totalSec = Math.floor(elapsed / 1000);
  const days = Math.floor(totalSec / 86400);
  const hours = Math.floor((totalSec % 86400) / 3600);
  const mins = Math.floor((totalSec % 3600) / 60);
  const secs = totalSec % 60;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  if (days > 0) {
    return `T+ ${days}d ${pad(hours)}:${pad(mins)}:${pad(secs)}`;
  }
  return `T+ ${pad(hours)}:${pad(mins)}:${pad(secs)}`;
}

function deriveQcFlags(
  series: WetlabTimeseries[],
  samples: WetlabSample[],
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

  // pH deviation > 0.15 from running mean
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

  // VCD reversal during growth phase
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

  // Explicitly-flagged offline samples
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

  // Dedup: one flag per parameter per 6h bucket
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

// ---------- Component ------------------------------------------------------

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

  const runtime = useRuntimeCounter(batch?.inoculation_date);

  const allParams = useMemo(() => {
    if (!series || !samples) return [];
    const set = new Set<string>();
    series.forEach((s) => set.add(s.parameter_name));
    samples.forEach((s) => set.add(s.measurement_name));
    return Array.from(set).sort();
  }, [series, samples]);

  const chartData = useMemo(() => {
    if (!series || !samples) return [];

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
    [series, selected],
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
    return <EmptyState>Connecting to batch telemetry…</EmptyState>;
  }

  const toggleParam = (p: string) => {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };

  const extra = (batch.extra_params || {}) as { condition_label?: string };

  const kpis: Kpi[] = [
    { label: "Status", value: batch.status, tone: "neutral" },
    {
      label: "Inoculated",
      value: fmtDateOnly(batch.inoculation_date),
      tone: "neutral",
    },
    {
      label: "Harvest",
      value: fmtDateOnly(batch.harvest_date),
      tone: "neutral",
    },
    {
      label: "Volume",
      value: batch.volume_liters ? fmtNumber(batch.volume_liters, 2) : "—",
      unit: batch.volume_liters ? "L" : undefined,
      tone: "neutral",
    },
    {
      label: "Cell line",
      value: batch.cell_line || "—",
      tone: "neutral",
    },
    {
      label: "QC flags",
      value: qcFlags.length,
      tone: qcFlags.length === 0 ? "good" : qcFlags.some((f) => f.severity === "fail") ? "bad" : "warn",
    },
  ];

  return (
    <div className={`${styles.grid} ${styles.reveal}`}>
      <HeroHeader
        eyebrow={`Batch · ${batch.bioreactor_model || "Bioreactor"}`}
        title={batch.batch_number || `Batch ${batch.id.slice(0, 8)}`}
        context={
          extra.condition_label ? (
            <p>
              Condition: <strong>{extra.condition_label}</strong>
            </p>
          ) : undefined
        }
        status={
          <>
            <StatusBadge status={batch.status} />
            <span className={styles.runtimeCounter}>{runtime}</span>
          </>
        }
        actions={
          <ActionBar>
            <SecondaryButton
              as="a"
              href={withOrg(`/campaigns/${campaignId}/compare`, orgId)}
            >
              Batch comparison →
            </SecondaryButton>
            <SecondaryButton
              as="a"
              href={withOrg(`/campaigns/${campaignId}`, orgId)}
            >
              Back to campaign
            </SecondaryButton>
          </ActionBar>
        }
      />

      <KpiStrip items={kpis} />

      <SectionRule eyebrow="Telemetry" title="Continuous + offline trace" />

      <div className={styles.chartPanel}>
        <div className={styles.chartHead}>
          <span>signal · selected parameters</span>
          <span className={styles.axisLabel}>t / hours since inoculation</span>
        </div>
        <div className={styles.toggleRow}>
          {allParams.map((p) => {
            const active = selected.has(p);
            const c = paramColor(p, allParams);
            return (
              <button
                key={p}
                type="button"
                onClick={() => toggleParam(p)}
                className={active ? styles.toggleOn : styles.toggleOff}
                style={active ? { background: c, borderColor: c, color: "#0a1228" } : { color: c }}
              >
                {p}
              </button>
            );
          })}
        </div>

        <div className={styles.chartCanvas} style={{ width: "100%", height: 460 }}>
          <ResponsiveContainer>
            <ComposedChart
              data={chartData}
              margin={{ top: 16, right: 24, left: 8, bottom: 24 }}
            >
              <CartesianGrid
                strokeDasharray="2 4"
                stroke="rgba(245, 166, 35, 0.12)"
              />
              <XAxis
                dataKey="hours"
                type="number"
                domain={[0, 336]}
                ticks={[0, 48, 96, 144, 192, 240, 288, 336]}
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
                label={{
                  value: "h",
                  position: "insideBottomRight",
                  offset: -2,
                  fill: "#6f7e9b",
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                }}
              />
              <YAxis
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
              />
              <Tooltip
                contentStyle={{
                  background: "#0a1228",
                  border: "1px solid rgba(245,166,35,0.4)",
                  borderRadius: 2,
                  fontFamily: "IBM Plex Mono",
                  fontSize: 12,
                  color: "#e7ecf3",
                }}
                cursor={{
                  stroke: "rgba(245, 166, 35, 0.4)",
                  strokeDasharray: "2 2",
                }}
                formatter={(value, name) => [
                  fmtNumber(typeof value === "number" ? value : Number(value), 3),
                  String(name).replace("__offline", " (offline)"),
                ]}
                labelFormatter={(h) => `t = ${fmtNumber(h as number, 1)} h`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                  color: "#b8c3d6",
                }}
              />
              {continuousParams.map((s) => (
                <Line
                  key={s.id}
                  type="monotone"
                  dataKey={s.parameter_name}
                  stroke={paramColor(s.parameter_name, allParams)}
                  dot={false}
                  strokeWidth={1.6}
                  connectNulls
                  isAnimationActive={true}
                  animationDuration={800}
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
      </div>

      <SectionRule
        eyebrow="QC"
        title={`Flags (${qcFlags.length})`}
        actions={
          qcFlags.length > 0 ? (
            <StatusBadge
              status={qcFlags.some((f) => f.severity === "fail") ? "fail" : "warn"}
            />
          ) : (
            <StatusBadge status="pass" />
          )
        }
      />

      {qcFlags.length === 0 ? (
        <EmptyState>No QC flags raised for this batch.</EmptyState>
      ) : (
        <DataTable>
          <thead>
            <tr>
              <th>t</th>
              <th>Parameter</th>
              <th>Description</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {qcFlags.map((f, i) => (
              <tr key={`${f.parameter}-${i}`}>
                <td className="num">{fmtNumber(f.hours, 1)} h</td>
                <td>
                  <span className="num">{f.parameter}</span>
                </td>
                <td>{f.description}</td>
                <td>
                  <StatusBadge status={f.severity} />
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}
