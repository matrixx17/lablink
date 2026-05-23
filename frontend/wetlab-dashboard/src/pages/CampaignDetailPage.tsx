import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, WetlabBatch, WetlabCampaign } from "../api/client";
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
import { downloadEvidenceBook } from "../lib/evidenceBook";
import styles from "./pages.module.css";

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [batches, setBatches] = useState<WetlabBatch[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  useEffect(() => {
    setCampaign(null);
    setBatches(null);
    setError(null);
    Promise.all([api.campaign(id, orgId), api.campaignBatches(id, orgId)])
      .then(([c, b]) => {
        setCampaign(c);
        setBatches(b);
      })
      .catch(setError);
  }, [id, orgId]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign || !batches) return <EmptyState>Loading campaign…</EmptyState>;

  const extra = (campaign.extra_params || {}) as {
    target?: string;
    process_type?: string;
    status?: string;
    cro_partner?: string;
    delivery_date?: string;
  };

  const onDownload = async () => {
    setExporting(true);
    setExportError(null);
    try {
      await downloadEvidenceBook(campaign.id, orgId);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const kpis: Kpi[] = [
    {
      label: "Status",
      value: extra.status || "active",
      tone:
        (extra.status || "").toLowerCase() === "lead_nominated"
          ? "good"
          : "neutral",
    },
    {
      label: "Batches",
      value: campaign.batch_count,
      unit: "runs",
      tone: "neutral",
    },
    {
      label: "Process",
      value: extra.process_type || "—",
      tone: "neutral",
    },
    {
      label: "CRO partner",
      value: extra.cro_partner || "—",
      tone: "neutral",
    },
    {
      label: "Delivered",
      value: fmtDateOnly(extra.delivery_date),
      tone: "neutral",
    },
  ];

  return (
    <div className={`${styles.grid} ${styles.reveal}`}>
      <HeroHeader
        eyebrow={extra.target ? `Target · ${extra.target}` : "Bioprocess campaign"}
        title={campaign.name}
        context={
          campaign.description ? <p>{campaign.description}</p> : undefined
        }
        status={<StatusBadge status={extra.status || "active"} />}
        actions={
          <ActionBar>
            <PrimaryButton onClick={onDownload} loading={exporting}>
              {exporting ? "Bundling…" : "Download Evidence Book"}
            </PrimaryButton>
            <SecondaryButton
              as="a"
              href={withOrg(`/campaigns/${campaign.id}/compare`, orgId)}
            >
              Batch comparison →
            </SecondaryButton>
          </ActionBar>
        }
      />

      {exportError ? <ErrorBox error={exportError} /> : null}

      <KpiStrip items={kpis} />

      <SectionRule eyebrow="Production lots" title={`Batches (${batches.length})`} />

      {batches.length === 0 ? (
        <EmptyState>No batches recorded for this campaign.</EmptyState>
      ) : (
        <DataTable>
          <thead>
            <tr>
              <th>Batch</th>
              <th>Condition</th>
              <th>Bioreactor</th>
              <th>Volume</th>
              <th>Inoculated</th>
              <th>Status</th>
              <th aria-label="actions" />
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => {
              const ep = (b.extra_params || {}) as { condition_label?: string };
              return (
                <tr key={b.id}>
                  <td>
                    <Link
                      className={styles.link}
                      to={withOrg(
                        `/campaigns/${campaign.id}/batches/${b.id}/timeline`,
                        orgId,
                      )}
                    >
                      {b.batch_number || b.id.slice(0, 8)}
                    </Link>
                    <div className={styles.muted}>{b.id.slice(0, 8)}</div>
                  </td>
                  <td>{ep.condition_label || "—"}</td>
                  <td>{b.bioreactor_model || "—"}</td>
                  <td className="num">
                    {b.volume_liters ? `${fmtNumber(b.volume_liters, 2)} L` : "—"}
                  </td>
                  <td className="num">{fmtDateOnly(b.inoculation_date)}</td>
                  <td>
                    <StatusBadge status={b.status} />
                  </td>
                  <td>
                    <SecondaryButton
                      as="a"
                      href={withOrg(
                        `/campaigns/${campaign.id}/batches/${b.id}/timeline`,
                        orgId,
                      )}
                    >
                      Timeline →
                    </SecondaryButton>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}
