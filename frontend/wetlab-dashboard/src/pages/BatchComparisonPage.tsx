import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  WetlabBatch,
  WetlabCampaign,
  WetlabSample,
  WetlabTimeseries,
} from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  DataTable,
  ErrorBox,
  fmtNumber,
  HeroHeader,
  KpiStrip,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

// --------- styling tokens shared across the page's two charts ---------

const CHART_GRID = "rgba(20, 24, 31, 0.08)";
const AXIS_INK = "#7a8290";
const AXIS_TICK = "#4a5260";
const TOOLTIP_CONTENT = {
  background: "#ffffff",
  border: "1px solid rgba(20,24,31,0.14)",
  borderRadius: 6,
  fontFamily: "Inter Tight",
  fontSize: 12.5,
  color: "#14181f",
  boxShadow: "0 1px 0 rgba(20,24,31,0.06)",
};

// Editorial palette: ink lead, neutrals for the rest.
const LEAD_INK = "#14181f";
const LEAD_GOLD = "#a36800";       // also our --warn token; works as accent
const VCD_BAR = "#1f7a4d";          // good (green)
const TITER_BAR = "#1d2a4e";        // accent (ink-blue)
const TRAJECTORY_PALETTE = ["#1d2a4e", "#a36800", "#1f7a4d", "#a32218"];

// Parameters available in the trajectory overlay (offline-only, since
// continuous and offline come from different sources).
const TRAJECTORY_PARAMETERS = [
  { key: "titer_mg_per_l",         label: "Titer (mg/L)" },
  { key: "vcd_e6_per_ml",          label: "VCD (×10⁶/mL)" },
  { key: "viability_percent",      label: "Viability (%)" },
  { key: "glucose_g_per_l",        label: "Glucose (g/L)" },
] as const;
type TrajectoryParam = typeof TRAJECTORY_PARAMETERS[number]["key"];

// ----------------------------------------------------------------------

type BatchSummary = {
  batch: WetlabBatch;
  finalTiter: number | null;
  peakVcd: number | null;
  minViability: number | null;
  durationDays: number | null;
  leadCondition: boolean;
  qcStatus: string | null;
};

function summarize(batch: WetlabBatch, samples: WetlabSample[]): BatchSummary {
  // Prefer the server-computed `summary_metrics` if present (from
  // ?include_metrics=true). Otherwise reconstruct from the samples.
  const sm = batch.summary_metrics;
  if (sm) {
    return {
      batch,
      finalTiter: sm.final_titer ?? null,
      peakVcd: sm.peak_vcd ?? null,
      minViability: sm.min_viability ?? null,
      durationDays: sm.run_duration_days ?? null,
      leadCondition: !!sm.lead_condition,
      qcStatus: (sm as { qc_status?: string | null }).qc_status ?? null,
    };
  }

  const titer = samples
    .filter((s) => s.measurement_name === "titer_mg_per_l" && s.value != null)
    .sort((a, b) => (a.sample_time_hours ?? 0) - (b.sample_time_hours ?? 0));
  const vcd = samples
    .filter(
      (s) =>
        (s.measurement_name === "viable_cell_density_e6_per_ml" ||
          s.measurement_name === "vcd_e6_per_ml") &&
        s.value != null,
    )
    .map((s) => s.value as number);
  const via = samples
    .filter((s) => s.measurement_name === "viability_percent" && s.value != null)
    .map((s) => s.value as number);
  const times = samples
    .map((s) => s.sample_time_hours)
    .filter((t): t is number => typeof t === "number");
  const durationDays = times.length > 0
    ? (Math.max(...times) - Math.min(...times)) / 24.0
    : null;

  const extra = (batch.extra_params || {}) as {
    lead_condition?: boolean;
    qc_status?: string;
  };
  return {
    batch,
    finalTiter: titer.length > 0 ? (titer[titer.length - 1].value as number) : null,
    peakVcd: vcd.length > 0 ? Math.max(...vcd) : null,
    minViability: via.length > 0 ? Math.min(...via) : null,
    durationDays,
    leadCondition: !!extra.lead_condition,
    qcStatus: extra.qc_status ?? null,
  };
}

// ----------------------------------------------------------------------

export default function BatchComparisonPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [summaries, setSummaries] = useState<BatchSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  // Trajectory overlay state
  const [overlayOpen, setOverlayOpen] = useState(false);
  const [overlayParam, setOverlayParam] = useState<TrajectoryParam>("titer_mg_per_l");
  // Cached per-batch-id offline samples + timeseries for the overlay
  const [overlaySamples, setOverlaySamples] =
    useState<Record<string, WetlabSample[]> | null>(null);
  const [overlayContinuous, setOverlayContinuous] =
    useState<Record<string, WetlabTimeseries[]> | null>(null);
  const [overlayLoading, setOverlayLoading] = useState(false);
  const [overlayError, setOverlayError] = useState<string | null>(null);

  useEffect(() => {
    setCampaign(null);
    setSummaries(null);
    setError(null);
    setOverlayOpen(false);
    setOverlaySamples(null);
    setOverlayContinuous(null);

    (async () => {
      try {
        const [camp, batches] = await Promise.all([
          api.campaign(campaignId, orgId),
          // ?include_metrics=true returns summary_metrics per batch in one
          // round-trip, so we don't need to re-fetch every batch's samples.
          api.campaignBatchesWithMetrics(campaignId, orgId),
        ]);
        setCampaign(camp);
        // If summary_metrics came back, use them; otherwise fall back.
        const enriched = await Promise.all(
          batches.map(async (b) => {
            if (b.summary_metrics) {
              return summarize(b, []);
            }
            const samples = await api.batchSamples(b.id, orgId);
            return summarize(b, samples);
          }),
        );
        enriched.sort((a, b) =>
          (a.batch.batch_number || "").localeCompare(b.batch.batch_number || ""),
        );
        setSummaries(enriched);
      } catch (e) {
        setError(e);
      }
    })();
  }, [campaignId, orgId]);

  const chartRows = useMemo(() => {
    if (!summaries) return [];
    return summaries.map((s) => ({
      batch: s.batch.batch_number || s.batch.id.slice(0, 8),
      final_titer_mg_per_l: s.finalTiter ?? 0,
      peak_vcd_e6_per_ml: s.peakVcd ?? 0,
      lead_condition: s.leadCondition,
      condition:
        (s.batch.extra_params as { condition_label?: string } | undefined)
          ?.condition_label ?? "",
    }));
  }, [summaries]);

  // Pick the explicit lead_condition flag if set; otherwise fall back to
  // highest final titer. (The audit / methods exports also follow this rule.)
  const lead = useMemo(() => {
    if (!summaries || summaries.length === 0) return null;
    const flagged = summaries.find((s) => s.leadCondition);
    if (flagged) return flagged;
    return summaries.reduce(
      (best, cur) =>
        (cur.finalTiter ?? -Infinity) > (best.finalTiter ?? -Infinity) ? cur : best,
      summaries[0],
    );
  }, [summaries]);

  const leadName = lead?.batch.batch_number || null;

  // ----- Trajectory overlay data fetch (lazy) -----

  const ensureOverlayData = async () => {
    if (!summaries || (overlaySamples && overlayContinuous)) return;
    setOverlayLoading(true);
    setOverlayError(null);
    try {
      const batchIds = summaries.map((s) => s.batch.id);
      const [samples, series] = await Promise.all([
        Promise.all(batchIds.map((id) => api.batchSamples(id, orgId).then((rows) => [id, rows] as const))),
        Promise.all(batchIds.map((id) => api.batchTimeseries(id, orgId).then((rows) => [id, rows] as const))),
      ]);
      setOverlaySamples(Object.fromEntries(samples));
      setOverlayContinuous(Object.fromEntries(series));
    } catch (e) {
      setOverlayError(e instanceof Error ? e.message : String(e));
    } finally {
      setOverlayLoading(false);
    }
  };

  const toggleOverlay = async () => {
    const next = !overlayOpen;
    setOverlayOpen(next);
    if (next) await ensureOverlayData();
  };

  // Shape: one row per (hours bucket) with one column per batch carrying
  // the value of the selected parameter.
  const overlayChartData = useMemo(() => {
    if (!summaries || !overlayOpen) return [];
    if (!overlaySamples && !overlayContinuous) return [];

    // For offline parameters use samples; for continuous, use timeseries.
    const isContinuous = overlayParam === "ph_continuous" as TrajectoryParam;
    // (All four trajectory parameters above happen to be offline-only,
    // but the shape supports continuous extension later.)
    const buckets = new Map<number, Record<string, number | string>>();
    summaries.forEach((s) => {
      const batchName = s.batch.batch_number || s.batch.id.slice(0, 8);
      const rows = !isContinuous
        ? (overlaySamples?.[s.batch.id] || []).filter(
            (r) => r.measurement_name === overlayParam && r.value != null,
          )
        : [];
      rows.forEach((r) => {
        const hours = Math.round((r.sample_time_hours ?? 0) * 10) / 10;
        const entry = buckets.get(hours) ?? { hours };
        entry[batchName] = r.value as number;
        buckets.set(hours, entry);
      });
    });
    return Array.from(buckets.values()).sort((a, b) =>
      (a.hours as number) - (b.hours as number),
    );
  }, [summaries, overlaySamples, overlayContinuous, overlayParam, overlayOpen]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign || !summaries) {
    return <div className={styles.centerMessage}>Loading batch comparison…</div>;
  }

  const leadIndex = leadName
    ? chartRows.findIndex((r) => r.batch === leadName)
    : -1;

  const titerBarFill = (i: number) => (i === leadIndex ? LEAD_INK : TITER_BAR);
  const vcdBarFill = (i: number) => (i === leadIndex ? LEAD_INK : VCD_BAR);

  // Star icon rendered above the lead group on the X-axis tick label
  const xTickWithStar = ({ x, y, payload }: { x: number; y: number; payload: { value: string } }) => {
    const isLead = leadName != null && payload.value === leadName;
    return (
      <g transform={`translate(${x},${y})`}>
        <text
          x={0} y={14}
          textAnchor="middle"
          fill={isLead ? LEAD_GOLD : AXIS_TICK}
          fontFamily="JetBrains Mono"
          fontWeight={isLead ? 600 : 400}
          fontSize={11}
        >
          {payload.value}
        </text>
        {isLead ? (
          <text
            x={0} y={-6}
            textAnchor="middle"
            fill={LEAD_GOLD}
            fontFamily="Inter Tight"
            fontSize={14}
          >
            ★
          </text>
        ) : null}
      </g>
    );
  };

  return (
    <div className={styles.grid}>
      <HeroHeader
        eyebrow="Batch comparison"
        title={`${campaign.name} — comparison`}
        context={
          lead ? (
            <p>
              Select the optimal process condition. Lead:{" "}
              <strong>{lead.batch.batch_number}</strong> — final titer{" "}
              <strong>{fmtNumber(lead.finalTiter, 0)} mg/L</strong>, peak VCD{" "}
              <strong>{fmtNumber(lead.peakVcd, 2)} ×10⁶/mL</strong>
              {(lead.batch.extra_params as { condition_label?: string } | undefined)
                ?.condition_label
                ? ` — ${(lead.batch.extra_params as { condition_label?: string }).condition_label}`
                : ""}
              .
            </p>
          ) : (
            <p>Select the optimal process condition.</p>
          )
        }
        actions={
          <ActionBar>
            <SecondaryButton
              as="a"
              href={withOrg(`/campaigns/${campaignId}`, orgId)}
            >
              Back to campaign
            </SecondaryButton>
          </ActionBar>
        }
      />

      <KpiStrip
        items={[
          { label: "Batches", value: summaries.length },
          {
            label: "Best titer",
            value: lead ? fmtNumber(lead.finalTiter, 0) : "—",
            unit: lead ? "mg/L" : undefined,
            tone: "good",
          },
          {
            label: "Best peak VCD",
            value: lead ? fmtNumber(lead.peakVcd, 2) : "—",
            unit: lead ? "×10⁶/mL" : undefined,
            tone: "good",
          },
          { label: "Lead", value: leadName || "—" },
        ]}
      />

      {/* Grouped bar chart: Peak VCD (left axis, green) + Final Titer (right axis, ink).
          Lead batch is rendered in ink black to draw the eye. Gold star above
          the X-tick of the lead batch. */}
      <SectionRule eyebrow="Outcome" title="Peak VCD + Final titer" />
      <div className={styles.chartPanel}>
        <div className={styles.chartCanvas} style={{ width: "100%", height: 360 }}>
          <ResponsiveContainer>
            <BarChart
              data={chartRows}
              margin={{ top: 32, right: 24, left: 8, bottom: 24 }}
              barCategoryGap="28%"
            >
              <CartesianGrid strokeDasharray="2 4" stroke={CHART_GRID} />
              <XAxis
                dataKey="batch"
                stroke={AXIS_INK}
                tickLine={false}
                tick={xTickWithStar as never}
                height={40}
              />
              <YAxis
                yAxisId="left"
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                label={{
                  value: "Peak VCD (×10⁶/mL)",
                  angle: -90,
                  position: "insideLeft",
                  fill: AXIS_INK,
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                label={{
                  value: "Final titer (mg/L)",
                  angle: 90,
                  position: "insideRight",
                  fill: AXIS_INK,
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                }}
              />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT}
                cursor={{ fill: "rgba(20,24,31,0.04)" }}
                labelFormatter={(l) => `Batch ${l}`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "Inter Tight",
                  fontSize: 12,
                  color: AXIS_TICK,
                }}
              />
              <Bar
                yAxisId="left"
                dataKey="peak_vcd_e6_per_ml"
                name="Peak VCD (×10⁶/mL)"
                radius={[3, 3, 0, 0]}
                stroke={LEAD_GOLD}
                strokeWidth={0}
              >
                {chartRows.map((_, i) => (
                  <Cell
                    key={`vcd-${i}`}
                    fill={vcdBarFill(i)}
                    stroke={i === leadIndex ? LEAD_GOLD : undefined}
                    strokeWidth={i === leadIndex ? 2 : 0}
                  />
                ))}
              </Bar>
              <Bar
                yAxisId="right"
                dataKey="final_titer_mg_per_l"
                name="Final titer (mg/L)"
                radius={[3, 3, 0, 0]}
              >
                {chartRows.map((_, i) => (
                  <Cell
                    key={`titer-${i}`}
                    fill={titerBarFill(i)}
                    stroke={i === leadIndex ? LEAD_GOLD : undefined}
                    strokeWidth={i === leadIndex ? 2 : 0}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Trajectory overlay — collapsed by default, lazy-loads per-batch data. */}
      <SectionRule
        eyebrow="Trajectory"
        title="Per-batch evolution"
        actions={
          <ActionBar>
            <select
              value={overlayParam}
              onChange={(e) => setOverlayParam(e.target.value as TrajectoryParam)}
              aria-label="Overlay parameter"
              disabled={!overlayOpen}
            >
              {TRAJECTORY_PARAMETERS.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                </option>
              ))}
            </select>
            <SecondaryButton onClick={toggleOverlay} loading={overlayLoading}>
              {overlayOpen ? "Hide overlay" : "Show trajectory overlay"}
            </SecondaryButton>
          </ActionBar>
        }
      />
      {overlayError ? <ErrorBox error={overlayError} /> : null}
      {overlayOpen ? (
        <div className={styles.chartPanel}>
          <div className={styles.chartCanvas} style={{ width: "100%", height: 320 }}>
            <ResponsiveContainer>
              <LineChart
                data={overlayChartData}
                margin={{ top: 16, right: 24, left: 8, bottom: 24 }}
              >
                <CartesianGrid strokeDasharray="2 4" stroke={CHART_GRID} />
                <XAxis
                  dataKey="hours"
                  type="number"
                  stroke={AXIS_INK}
                  tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                  label={{
                    value: "hours since inoculation",
                    position: "insideBottom",
                    offset: -8,
                    fill: AXIS_INK,
                    fontFamily: "JetBrains Mono",
                    fontSize: 11,
                  }}
                />
                <YAxis
                  stroke={AXIS_INK}
                  tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={TOOLTIP_CONTENT}
                  labelFormatter={(h) => `t = ${fmtNumber(h as number, 1)} h`}
                  formatter={(v) =>
                    fmtNumber(typeof v === "number" ? v : Number(v), 2)
                  }
                />
                <Legend
                  wrapperStyle={{
                    fontFamily: "Inter Tight",
                    fontSize: 12,
                    color: AXIS_TICK,
                  }}
                />
                {summaries.map((s, i) => {
                  const name = s.batch.batch_number || s.batch.id.slice(0, 8);
                  const isLead = leadName != null && name === leadName;
                  return (
                    <Line
                      key={s.batch.id}
                      type="monotone"
                      dataKey={name}
                      stroke={
                        isLead
                          ? LEAD_INK
                          : TRAJECTORY_PALETTE[i % TRAJECTORY_PALETTE.length]
                      }
                      strokeWidth={isLead ? 3 : 1.5}
                      dot={false}
                      connectNulls
                      isAnimationActive={false}
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      <SectionRule eyebrow="Roster" title={`Batches (${summaries.length})`} />
      <DataTable>
        <thead>
          <tr>
            <th>Batch</th>
            <th>Condition</th>
            <th>Peak VCD</th>
            <th>Final titer</th>
            <th>Min viability</th>
            <th>Duration</th>
            <th>QC</th>
            <th aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {summaries.map((s) => {
            const isLead = leadName != null && s.batch.batch_number === leadName;
            return (
              <tr
                key={s.batch.id}
                style={isLead ? { background: "var(--good-soft)" } : undefined}
              >
                <td>
                  <span className="num">{s.batch.batch_number}</span>
                  {isLead ? (
                    <span
                      style={{
                        marginLeft: 8,
                        color: LEAD_GOLD,
                        fontSize: 12,
                      }}
                    >
                      ★ lead
                    </span>
                  ) : null}
                </td>
                <td>
                  {(s.batch.extra_params as { condition_label?: string } | undefined)
                    ?.condition_label ?? "—"}
                </td>
                <td className="num">{fmtNumber(s.peakVcd, 2)}</td>
                <td className="num">{fmtNumber(s.finalTiter, 0)}</td>
                <td className="num">{fmtNumber(s.minViability, 1)}</td>
                <td className="num">
                  {s.durationDays != null ? `${fmtNumber(s.durationDays, 1)} d` : "—"}
                </td>
                <td>
                  <StatusBadge status={s.qcStatus || s.batch.status} />
                </td>
                <td>
                  <SecondaryButton
                    as="a"
                    href={withOrg(
                      `/campaigns/${campaignId}/batches/${s.batch.id}/timeline`,
                      orgId,
                    )}
                  >
                    View full timeline →
                  </SecondaryButton>
                </td>
              </tr>
            );
          })}
        </tbody>
      </DataTable>
    </div>
  );
}
