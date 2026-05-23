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

// Editorial palette: ink-blue accent leads, muted ink+gray for supporting
// traces, semantic red only for the excursion channel. No rainbow.
const TRACE_COLORS: Record<string, string> = {
  ph: "#1d2a4e",                            // accent (ink-blue)
  do_percent: "#a36800",                    // warm (warn)
  temperature_c: "#7a8290",                 // neutral
  agitation_rpm: "#b3b9c4",                 // muted
  viable_cell_density_e6_per_ml: "#1f7a4d", // good (green)
  viability_percent: "#4a5260",
  titer_mg_per_l: "#14181f",                // ink, headline metric
  glucose_g_per_l: "#7a8290",
  lactate_g_per_l: "#a36800",
  osmolality_mosm: "#b3b9c4",
};
const FALLBACK = ["#1d2a4e", "#a36800", "#1f7a4d", "#a32218", "#7a8290", "#4a5260"];

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

// ---------- Helpers --------------------------------------------------------

function hoursSinceInoculation(unixSeconds: number, inoculationUnix: number): number {
  return (unixSeconds - inoculationUnix) / 3600;
}

function paramColor(name: string, allParams: string[]): string {
  if (TRACE_COLORS[name]) return TRACE_COLORS[name];
  const idx = allParams.indexOf(name);
  return FALLBACK[idx % FALLBACK.length];
}

function deriveQcFlags(
  series: WetlabTimeseries[],
  samples: WetlabSample[],
): QcFlag[] {
  const flags: QcFlag[] = [];

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
    return <div className={styles.centerMessage}>Loading batch telemetry…</div>;
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
    <div className={styles.grid}>
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
        status={<StatusBadge status={batch.status} />}
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
          <span>Selected parameters</span>
          <span className={styles.axisLabel}>t · hours since inoculation</span>
        </div>
        <div className={styles.toggleRow}>
          {allParams.map((p) => {
            const active = selected.has(p);
            return (
              <button
                key={p}
                type="button"
                onClick={() => toggleParam(p)}
                className={active ? styles.toggleOn : styles.toggleOff}
              >
                {p}
              </button>
            );
          })}
        </div>

        <div className={styles.chartCanvas} style={{ width: "100%", height: 440 }}>
          <ResponsiveContainer>
            <ComposedChart
              data={chartData}
              margin={{ top: 16, right: 24, left: 8, bottom: 24 }}
            >
              <CartesianGrid
                strokeDasharray="2 4"
                stroke="rgba(20, 24, 31, 0.08)"
              />
              <XAxis
                dataKey="hours"
                type="number"
                domain={[0, 336]}
                ticks={[0, 48, 96, 144, 192, 240, 288, 336]}
                stroke="#7a8290"
                tick={{ fill: "#4a5260", fontFamily: "JetBrains Mono", fontSize: 11 }}
                label={{
                  value: "h",
                  position: "insideBottomRight",
                  offset: -2,
                  fill: "#7a8290",
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                }}
              />
              <YAxis
                stroke="#7a8290"
                tick={{ fill: "#4a5260", fontFamily: "JetBrains Mono", fontSize: 11 }}
              />
              <Tooltip
                contentStyle={{
                  background: "#ffffff",
                  border: "1px solid rgba(20,24,31,0.14)",
                  borderRadius: 6,
                  fontFamily: "Inter Tight",
                  fontSize: 12.5,
                  color: "#14181f",
                  boxShadow: "0 1px 0 rgba(20,24,31,0.06)",
                }}
                cursor={{
                  stroke: "rgba(29, 42, 78, 0.4)",
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
                  fontFamily: "Inter Tight",
                  fontSize: 12,
                  color: "#4a5260",
                }}
              />
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
