import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
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
  Card,
  EmptyState,
  ErrorBox,
  fmtNumber,
  PageHeader,
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
        s.measurement_name === "viable_cell_density_e6_per_ml" && s.value != null
    )
    .map((s) => s.value as number);

  return {
    batch,
    finalTiter: titer.length > 0 ? (titer[titer.length - 1].value as number) : null,
    peakVcd: vcd.length > 0 ? Math.max(...vcd) : null,
  };
}

const BAR_COLORS = ["#94a3b8", "#60a5fa", "#10b981"];

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
          })
        );
        enriched.sort((a, b) =>
          (a.batch.batch_number || "").localeCompare(b.batch.batch_number || "")
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
      summaries[0]
    );
  }, [summaries]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign || !summaries) {
    return <EmptyState>Loading batch comparison...</EmptyState>;
  }

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow="Campaign comparison"
        title={`${campaign.name} — Batches`}
        actions={
          <Link
            className={styles.secondaryButton}
            to={withOrg(`/campaigns/${campaignId}`, orgId)}
          >
            Back to campaign
          </Link>
        }
      />

      {lead && (
        <Card>
          <h2>Why we picked {lead.batch.batch_number}</h2>
          <p>
            Final titer <strong>{fmtNumber(lead.finalTiter, 0)} mg/L</strong>,
            peak VCD <strong>{fmtNumber(lead.peakVcd, 2)} ×10⁶ cells/mL</strong>
            {(lead.batch.extra_params as { condition_label?: string } | undefined)
              ?.condition_label
              ? ` — ${(lead.batch.extra_params as { condition_label?: string }).condition_label}`
              : ""}
            .
          </p>
        </Card>
      )}

      <Card>
        <h2>Final titer (mg/L)</h2>
        <div style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <BarChart data={chartRows} margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="batch" />
              <YAxis label={{ value: "mg/L", angle: -90, position: "insideLeft" }} />
              <Tooltip
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 0)}
                labelFormatter={(label) => `Batch ${label}`}
              />
              <Legend />
              <Bar dataKey="final_titer_mg_per_l" name="Final titer (mg/L)">
                {chartRows.map((_, i) => (
                  <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <h2>Peak VCD (×10⁶ cells/mL)</h2>
        <div style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <BarChart data={chartRows} margin={{ top: 16, right: 24, left: 8, bottom: 24 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="batch" />
              <YAxis label={{ value: "×10⁶/mL", angle: -90, position: "insideLeft" }} />
              <Tooltip
                formatter={(v) => fmtNumber(typeof v === "number" ? v : Number(v), 2)}
                labelFormatter={(label) => `Batch ${label}`}
              />
              <Legend />
              <Bar dataKey="peak_vcd_e6_per_ml" name="Peak VCD (×10⁶/mL)">
                {chartRows.map((_, i) => (
                  <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <h2>Batch summary</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Batch</th>
                <th>Condition</th>
                <th>Final titer (mg/L)</th>
                <th>Peak VCD (×10⁶/mL)</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((s) => (
                <tr key={s.batch.id}>
                  <td>{s.batch.batch_number}</td>
                  <td>
                    {(s.batch.extra_params as { condition_label?: string } | undefined)
                      ?.condition_label ?? "-"}
                  </td>
                  <td>{fmtNumber(s.finalTiter, 0)}</td>
                  <td>{fmtNumber(s.peakVcd, 2)}</td>
                  <td>{s.batch.status}</td>
                  <td>
                    <Link
                      className={styles.secondaryButton}
                      to={withOrg(
                        `/campaigns/${campaignId}/batches/${s.batch.id}/timeline`,
                        orgId
                      )}
                    >
                      Timeline →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
