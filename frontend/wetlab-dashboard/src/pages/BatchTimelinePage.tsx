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
  WetlabQcResult,
  WetlabSample,
  WetlabTimeseries,
} from "../api/client";
import { downloadBatchRecord } from "../lib/evidenceBook";
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
  PrimaryButton,
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

// Two-axis layout: narrow-range parameters (pH, %, °C) on the left;
// wide-range parameters (×10⁶, mg/L, g/L, mOsm) on the right.
const RIGHT_AXIS_PARAMS = new Set([
  "vcd_e6_per_ml",
  "viable_cell_density_e6_per_ml",
  "titer_mg_per_l",
  "glucose_g_per_l",
  "lactate_g_per_l",
  "osmolality_mosm",
]);

function axisFor(parameter: string): "left" | "right" {
  return RIGHT_AXIS_PARAMS.has(parameter) ? "right" : "left";
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
  const [serverQc, setServerQc] = useState<WetlabQcResult[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<Set<string>>(DEFAULT_SELECTED);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    setBatch(null);
    setSeries(null);
    setSamples(null);
    setServerQc([]);
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
    // QC is non-blocking; if the endpoint errors (e.g. batch never run
    // through the engine) we just hide the section silently.
    api.batchQc(batchId, orgId).then(setServerQc).catch(() => setServerQc([]));
  }, [batchId, orgId]);

  const onDownloadBatchRecord = async () => {
    if (!batch) return;
    setExporting(true);
    setExportError(null);
    try {
      await downloadBatchRecord(batch.campaign_id, orgId);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

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

  // 4-card summary at the bottom of the page: pulls Peak VCD / Final Titer
  // / Min Viability / Run Duration directly from offline samples so the
  // page works against any wet lab batch without server-side aggregation.
  const summaryStats = useMemo(() => {
    if (!samples) {
      return {
        peakVcd: null as number | null,
        finalTiter: null as number | null,
        minViability: null as number | null,
        durationH: null as number | null,
      };
    }
    const vcd = samples
      .filter(
        (s) =>
          (s.measurement_name === "vcd_e6_per_ml" ||
            s.measurement_name === "viable_cell_density_e6_per_ml") &&
          s.value != null,
      )
      .map((s) => s.value as number);
    const titerRows = samples
      .filter((s) => s.measurement_name === "titer_mg_per_l" && s.value != null)
      .sort((a, b) => (a.sample_time_hours ?? 0) - (b.sample_time_hours ?? 0));
    const via = samples
      .filter((s) => s.measurement_name === "viability_percent" && s.value != null)
      .map((s) => s.value as number);

    const times = samples
      .map((s) => s.sample_time_hours)
      .filter((t): t is number => typeof t === "number");
    const durationH = times.length > 0
      ? Math.max(...times) - Math.min(...times)
      : null;

    return {
      peakVcd: vcd.length > 0 ? Math.max(...vcd) : null,
      finalTiter:
        titerRows.length > 0 ? (titerRows[titerRows.length - 1].value as number) : null,
      minViability: via.length > 0 ? Math.min(...via) : null,
      durationH,
    };
  }, [samples]);

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
            <PrimaryButton onClick={onDownloadBatchRecord} loading={exporting}>
              {exporting ? "Bundling…" : "Download Batch Record"}
            </PrimaryButton>
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

      {exportError ? <ErrorBox error={exportError} /> : null}

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

        <div className={styles.chartCanvas}>
          <svg viewBox="0 0 820 440" role="img" aria-label="Batch telemetry over time">
            {(() => {
              const width = 820;
              const height = 440;
              const margin = { top: 24, right: 28, bottom: 56, left: 64 };
              const plotWidth = width - margin.left - margin.right;
              const plotHeight = height - margin.top - margin.bottom;
              const xMax = Math.max(336, ...chartData.map((row) => row.hours || 0));
              const x = (hours: number) => margin.left + (hours / xMax) * plotWidth;
              const traces = [
                ...continuousParams.map((s) => ({ key: s.parameter_name, label: s.parameter_name, offline: false })),
                ...offlineParams.map((p) => ({ key: `${p}__offline`, label: p, offline: true })),
              ];
              const yFor = (key: string, value: number) => {
                const values = chartData
                  .map((row) => row[key])
                  .filter((item): item is number => typeof item === "number" && Number.isFinite(item));
                const min = Math.min(...values);
                const max = Math.max(...values);
                const denom = max === min ? 1 : max - min;
                return margin.top + plotHeight - ((value - min) / denom) * plotHeight;
              };
              return (
                <>
                  <rect x={margin.left} y={margin.top} width={plotWidth} height={plotHeight} fill="var(--bg-mute)" stroke="var(--rule)" />
                  {[0, 48, 96, 144, 192, 240, 288, 336].map((tick) => (
                    <g key={tick}>
                      <line x1={x(tick)} y1={margin.top} x2={x(tick)} y2={margin.top + plotHeight} stroke="var(--rule)" />
                      <text x={x(tick)} y={height - 28} textAnchor="middle" className={styles.svgAxisTick}>
                        {tick}
                      </text>
                    </g>
                  ))}
                  {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
                    const y = margin.top + tick * plotHeight;
                    return <line key={tick} x1={margin.left} y1={y} x2={margin.left + plotWidth} y2={y} stroke="var(--rule)" />;
                  })}
                  {traces.map((trace) => {
                    const color = paramColor(trace.label, allParams);
                    const points = chartData
                      .filter((row) => typeof row[trace.key] === "number" && Number.isFinite(row[trace.key]))
                      .map((row) => ({ hours: row.hours, value: row[trace.key] as number }));
                    if (points.length === 0) return null;
                    const path = points
                      .map((point, index) => `${index === 0 ? "M" : "L"} ${x(point.hours)} ${yFor(trace.key, point.value)}`)
                      .join(" ");
                    return (
                      <g key={trace.key}>
                        {!trace.offline ? <path d={path} fill="none" stroke={color} strokeWidth={1.8} /> : null}
                        {trace.offline
                          ? points.map((point) => (
                              <rect
                                key={`${trace.key}-${point.hours}`}
                                x={x(point.hours) - 3}
                                y={yFor(trace.key, point.value) - 3}
                                width={6}
                                height={6}
                                transform={`rotate(45 ${x(point.hours)} ${yFor(trace.key, point.value)})`}
                                fill={color}
                              />
                            ))
                          : null}
                      </g>
                    );
                  })}
                  <text x={margin.left + plotWidth / 2} y={height - 8} textAnchor="middle" className={styles.svgAxisLabel}>
                    Hours since inoculation
                  </text>
                  <text x={20} y={margin.top + plotHeight / 2} textAnchor="middle" className={styles.svgAxisLabel} transform={`rotate(-90 20 ${margin.top + plotHeight / 2})`}>
                    Normalized selected traces
                  </text>
                </>
              );
            })()}
          </svg>
          <div className={styles.chartLegend}>
            {[...continuousParams.map((s) => s.parameter_name), ...offlineParams].map((name) => (
              <span key={name}><i style={{ background: paramColor(name, allParams) }} /> {name}</span>
            ))}
          </div>
        </div>
      </div>

      {/* QC alerts — server-authoritative results from BioprocessQCEngine.
          Hidden entirely if the engine returned only "pass" results. */}
      {serverQc.some((r) => r.status !== "pass") ? (
        <div data-tour="wetlab-qc-flag">
          <SectionRule
            eyebrow="QC alerts"
            title={`Alerts (${serverQc.filter((r) => r.status !== "pass").length})`}
            actions={
              <StatusBadge
                status={
                  serverQc.some((r) => r.status === "fail") ? "fail" : "warn"
                }
              />
            }
          />
          <DataTable>
            <thead>
              <tr>
                <th>Check</th>
                <th>Status</th>
                <th>Message</th>
                <th>Timepoint</th>
              </tr>
            </thead>
            <tbody>
              {serverQc
                .filter((r) => r.status !== "pass")
                .map((r) => (
                  <tr key={`${r.check_name}-${r.timepoint_h ?? ""}`}>
                    <td>
                      <span className="num">{r.check_name}</span>
                    </td>
                    <td>
                      <StatusBadge status={r.status} />
                    </td>
                    <td>{r.message}</td>
                    <td className="num">
                      {r.timepoint_h != null
                        ? `${fmtNumber(r.timepoint_h, 1)} h`
                        : "—"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </DataTable>
        </div>
      ) : serverQc.length > 0 ? (
        <>
          <SectionRule
            eyebrow="QC alerts"
            title="All checks passed"
            actions={<StatusBadge status="pass" />}
          />
        </>
      ) : null}

      {/* Client-derived flags (cheap heuristic) — only shown when server QC
          is unavailable, so the page still has useful signal. */}
      {serverQc.length === 0 && qcFlags.length > 0 ? (
        <div data-tour="wetlab-qc-flag">
          <SectionRule
            eyebrow="QC (heuristic)"
            title={`Flags (${qcFlags.length})`}
            actions={
              <StatusBadge
                status={qcFlags.some((f) => f.severity === "fail") ? "fail" : "warn"}
              />
            }
          />
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
        </div>
      ) : null}

      {/* Outcome summary — 4 cards: Peak VCD / Final Titer / Min Viability / Run Duration */}
      <SectionRule eyebrow="Outcome" title="Key metrics" />
      <KpiStrip
        items={[
          {
            label: "Peak VCD",
            value:
              summaryStats.peakVcd != null
                ? fmtNumber(summaryStats.peakVcd, 2)
                : "—",
            unit: summaryStats.peakVcd != null ? "×10⁶/mL" : undefined,
            tone: "good",
          },
          {
            label: "Final titer",
            value:
              summaryStats.finalTiter != null
                ? fmtNumber(summaryStats.finalTiter, 0)
                : "—",
            unit: summaryStats.finalTiter != null ? "mg/L" : undefined,
            tone: "good",
          },
          {
            label: "Min viability",
            value:
              summaryStats.minViability != null
                ? fmtNumber(summaryStats.minViability, 1)
                : "—",
            unit: summaryStats.minViability != null ? "%" : undefined,
            tone:
              summaryStats.minViability != null && summaryStats.minViability < 70
                ? "warn"
                : "neutral",
          },
          {
            label: "Run duration",
            value:
              summaryStats.durationH != null
                ? fmtNumber(summaryStats.durationH, 0)
                : "—",
            unit: summaryStats.durationH != null ? "h" : undefined,
            tone: "neutral",
          },
        ]}
      />
    </div>
  );
}
