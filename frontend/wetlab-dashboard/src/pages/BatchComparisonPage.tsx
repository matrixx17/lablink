import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
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
} from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  DataTable,
  EmptyState,
  ErrorBox,
  fmtNumber,
  HeroHeader,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import styles from "./pages.module.css";

type BatchSummary = {
  batch: WetlabBatch;
  finalTiter: number | null;
  peakVcd: number | null;
};

function summarize(batch: WetlabBatch, samples: WetlabSample[]): BatchSummary {
  const titer = samples
    .filter((s) => s.measurement_name === "titer_mg_per_l" && s.value != null)
    .sort((a, b) => (a.sample_time_hours ?? 0) - (b.sample_time_hours ?? 0));
  const vcd = samples
    .filter(
      (s) =>
        s.measurement_name === "viable_cell_density_e6_per_ml" &&
        s.value != null,
    )
    .map((s) => s.value as number);

  return {
    batch,
    finalTiter: titer.length > 0 ? (titer[titer.length - 1].value as number) : null,
    peakVcd: vcd.length > 0 ? Math.max(...vcd) : null,
  };
}

// SCADA palette: amber leads (winner), cyan supporting, slate baseline.
const BAR_COLORS = ["#5dd0e0", "#94a3b8", "#f5a623"];

export default function BatchComparisonPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [summaries, setSummaries] = useState<BatchSummary[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setCampaign(null);
    setSummaries(null);
    setError(null);

    (async () => {
      try {
        const [camp, batches] = await Promise.all([
          api.campaign(campaignId, orgId),
          api.campaignBatches(campaignId, orgId),
        ]);
        setCampaign(camp);
        const enriched = await Promise.all(
          batches.map(async (b) => {
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
      condition:
        (s.batch.extra_params as { condition_label?: string } | undefined)
          ?.condition_label ?? "",
    }));
  }, [summaries]);

  const lead = useMemo(() => {
    if (!summaries || summaries.length === 0) return null;
    return summaries.reduce(
      (best, cur) =>
        (cur.finalTiter ?? -Infinity) > (best.finalTiter ?? -Infinity) ? cur : best,
      summaries[0],
    );
  }, [summaries]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign || !summaries) {
    return <EmptyState>Loading batch comparison…</EmptyState>;
  }

  // index the lead batch so we can recolor its bar amber
  const leadIndex = lead
    ? chartRows.findIndex((r) => r.batch === (lead.batch.batch_number || lead.batch.id.slice(0, 8)))
    : -1;

  const barFill = (i: number) =>
    i === leadIndex ? "#f5a623" : BAR_COLORS[i % BAR_COLORS.length];

  return (
    <div className={`${styles.grid} ${styles.reveal}`}>
      <HeroHeader
        eyebrow="Side-by-side analysis"
        title={`${campaign.name} — Batch comparison`}
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

      {lead ? (
        <div className={styles.winnerCallout}>
          <MedalGlyph />
          <div>
            <strong>Lead candidate · {lead.batch.batch_number}</strong>
            <p>
              Final titer <span className="num">{fmtNumber(lead.finalTiter, 0)}</span> mg/L,
              peak VCD <span className="num">{fmtNumber(lead.peakVcd, 2)}</span> ×10⁶
              cells/mL
              {(lead.batch.extra_params as { condition_label?: string } | undefined)
                ?.condition_label
                ? ` — ${(lead.batch.extra_params as { condition_label?: string }).condition_label}`
                : ""}
              .
            </p>
          </div>
        </div>
      ) : null}

      <SectionRule eyebrow="Yield" title="Final titer (mg/L)" />
      <div className={styles.chartPanel}>
        <div className={styles.chartCanvas} style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <BarChart
              data={chartRows}
              margin={{ top: 16, right: 24, left: 8, bottom: 24 }}
            >
              <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,166,35,0.12)" />
              <XAxis
                dataKey="batch"
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
              />
              <YAxis
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
                label={{
                  value: "mg/L",
                  angle: -90,
                  position: "insideLeft",
                  fill: "#6f7e9b",
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                }}
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
                cursor={{ fill: "rgba(245,166,35,0.06)" }}
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 0)}
                labelFormatter={(l) => `Batch ${l}`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                  color: "#b8c3d6",
                }}
              />
              <Bar dataKey="final_titer_mg_per_l" name="Final titer (mg/L)" radius={[2, 2, 0, 0]}>
                {chartRows.map((_, i) => (
                  <Cell key={i} fill={barFill(i)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <SectionRule eyebrow="Growth" title="Peak VCD (×10⁶ cells/mL)" />
      <div className={styles.chartPanel}>
        <div className={styles.chartCanvas} style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <BarChart
              data={chartRows}
              margin={{ top: 16, right: 24, left: 8, bottom: 24 }}
            >
              <CartesianGrid strokeDasharray="2 4" stroke="rgba(245,166,35,0.12)" />
              <XAxis
                dataKey="batch"
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
              />
              <YAxis
                stroke="#6f7e9b"
                tick={{ fill: "#b8c3d6", fontFamily: "IBM Plex Mono", fontSize: 11 }}
                label={{
                  value: "×10⁶/mL",
                  angle: -90,
                  position: "insideLeft",
                  fill: "#6f7e9b",
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                }}
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
                cursor={{ fill: "rgba(245,166,35,0.06)" }}
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 2)}
                labelFormatter={(l) => `Batch ${l}`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "IBM Plex Mono",
                  fontSize: 11,
                  color: "#b8c3d6",
                }}
              />
              <Bar dataKey="peak_vcd_e6_per_ml" name="Peak VCD (×10⁶/mL)" radius={[2, 2, 0, 0]}>
                {chartRows.map((_, i) => (
                  <Cell key={i} fill={barFill(i)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <SectionRule eyebrow="Roster" title="All batches" />
      <DataTable>
        <thead>
          <tr>
            <th>Batch</th>
            <th>Condition</th>
            <th>Final titer (mg/L)</th>
            <th>Peak VCD (×10⁶/mL)</th>
            <th>Status</th>
            <th aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          {summaries.map((s) => (
            <tr key={s.batch.id}>
              <td className="num">{s.batch.batch_number}</td>
              <td>
                {(s.batch.extra_params as { condition_label?: string } | undefined)
                  ?.condition_label ?? "—"}
              </td>
              <td className="num">{fmtNumber(s.finalTiter, 0)}</td>
              <td className="num">{fmtNumber(s.peakVcd, 2)}</td>
              <td>
                <StatusBadge status={s.batch.status} />
              </td>
              <td>
                <SecondaryButton
                  as="a"
                  href={withOrg(
                    `/campaigns/${campaignId}/batches/${s.batch.id}/timeline`,
                    orgId,
                  )}
                >
                  Timeline →
                </SecondaryButton>
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </div>
  );
}

function MedalGlyph() {
  // Small inline-SVG "winner" mark — a concentric medal evoking
  // batch-quality certification. Color via currentColor in CSS.
  return (
    <svg
      width="48"
      height="48"
      viewBox="0 0 48 48"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden
    >
      <circle cx="24" cy="24" r="14" />
      <circle cx="24" cy="24" r="9" />
      <circle cx="24" cy="24" r="3" fill="currentColor" />
      <path d="M14 12l-4 14M34 12l4 14" strokeWidth="1.2" />
    </svg>
  );
}
