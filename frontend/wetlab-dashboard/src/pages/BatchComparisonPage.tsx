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
  KpiStrip,
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

// Editorial palette: ink for the lead, neutrals for the rest.
const NEUTRAL_BAR = "#b3b9c4";
const LEAD_BAR = "#14181f"; // ink

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
    return <div className={styles.centerMessage}>Loading batch comparison…</div>;
  }

  const leadIndex = lead
    ? chartRows.findIndex(
        (r) => r.batch === (lead.batch.batch_number || lead.batch.id.slice(0, 8)),
      )
    : -1;

  const barFill = (i: number) => (i === leadIndex ? LEAD_BAR : NEUTRAL_BAR);

  return (
    <div className={styles.grid}>
      <HeroHeader
        eyebrow="Side-by-side analysis"
        title={`${campaign.name} — comparison`}
        context={
          lead ? (
            <p>
              Lead: <strong>{lead.batch.batch_number}</strong>. Final titer{" "}
              <strong>{fmtNumber(lead.finalTiter, 0)} mg/L</strong>, peak VCD{" "}
              <strong>{fmtNumber(lead.peakVcd, 2)} ×10⁶ cells/mL</strong>
              {(lead.batch.extra_params as { condition_label?: string } | undefined)
                ?.condition_label
                ? ` — ${(lead.batch.extra_params as { condition_label?: string }).condition_label}`
                : ""}
              .
            </p>
          ) : undefined
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
          {
            label: "Lead",
            value: lead?.batch.batch_number || "—",
          },
        ]}
      />

      <SectionRule eyebrow="Yield" title="Final titer (mg/L)" />
      <div className={styles.chartPanel}>
        <div className={styles.chartCanvas} style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <BarChart data={chartRows} margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
              <CartesianGrid strokeDasharray="2 4" stroke={CHART_GRID} />
              <XAxis
                dataKey="batch"
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
              />
              <YAxis
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                label={{
                  value: "mg/L",
                  angle: -90,
                  position: "insideLeft",
                  fill: AXIS_INK,
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                }}
              />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT}
                cursor={{ fill: "rgba(20,24,31,0.04)" }}
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 0)}
                labelFormatter={(l) => `Batch ${l}`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "Inter Tight",
                  fontSize: 12,
                  color: "#4a5260",
                }}
              />
              <Bar dataKey="final_titer_mg_per_l" name="Final titer (mg/L)" radius={[3, 3, 0, 0]}>
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
            <BarChart data={chartRows} margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
              <CartesianGrid strokeDasharray="2 4" stroke={CHART_GRID} />
              <XAxis
                dataKey="batch"
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
              />
              <YAxis
                stroke={AXIS_INK}
                tick={{ fill: AXIS_TICK, fontFamily: "JetBrains Mono", fontSize: 11 }}
                label={{
                  value: "×10⁶/mL",
                  angle: -90,
                  position: "insideLeft",
                  fill: AXIS_INK,
                  fontFamily: "JetBrains Mono",
                  fontSize: 11,
                }}
              />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT}
                cursor={{ fill: "rgba(20,24,31,0.04)" }}
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 2)}
                labelFormatter={(l) => `Batch ${l}`}
              />
              <Legend
                wrapperStyle={{
                  fontFamily: "Inter Tight",
                  fontSize: 12,
                  color: "#4a5260",
                }}
              />
              <Bar dataKey="peak_vcd_e6_per_ml" name="Peak VCD (×10⁶/mL)" radius={[3, 3, 0, 0]}>
                {chartRows.map((_, i) => (
                  <Cell key={i} fill={barFill(i)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <SectionRule eyebrow="Roster" title={`Batches (${summaries.length})`} />
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
