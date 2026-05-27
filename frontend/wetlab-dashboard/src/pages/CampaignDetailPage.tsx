import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, CampaignApproval, WetlabBatch, WetlabCampaign } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  DataTable,
  DetailLayout,
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
  Storyboard,
} from "../components/ui";
import { downloadBatchRecord, downloadEvidenceBook } from "../lib/evidenceBook";
import { getDemoSession } from "../lib/demoSession";
import styles from "./pages.module.css";

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<WetlabCampaign | null>(null);
  const [batches, setBatches] = useState<WetlabBatch[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [exporting, setExporting] = useState(false);
  const [exportingBatch, setExportingBatch] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportToast, setExportToast] = useState<string | null>(null);
  const [approvalModalOpen, setApprovalModalOpen] = useState(false);
  const [demoRestrictionOpen, setDemoRestrictionOpen] = useState(false);
  const [approvalRole, setApprovalRole] = useState<"author" | "reviewer" | "approver">("reviewer");
  const [approvalComments, setApprovalComments] = useState("");
  const [submittingApproval, setSubmittingApproval] = useState(false);

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
    setExportToast(null);
    try {
      await downloadEvidenceBook(campaign.id, orgId);
      setExportToast(`Batch Record exported — ${batches.length} batches, ${campaign.name}`);
      window.setTimeout(() => setExportToast(null), 3500);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const onDownloadBatchRecord = async () => {
    setExportingBatch(true);
    setExportError(null);
    setExportToast(null);
    try {
      await downloadBatchRecord(campaign.id, orgId);
      setExportToast(`Batch Record exported — ${batches.length} batches, ${campaign.name}`);
      window.setTimeout(() => setExportToast(null), 3500);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportingBatch(false);
    }
  };

  const isWetlab = campaign.domain === "wetlab";
  const isDemoViewer = orgId === "demo-therapeutics" && getDemoSession()?.domain === "wetlab";
  const approvals = campaign.approvals || [];

  const refreshCampaign = async () => {
    const next = await api.campaign(id, orgId);
    setCampaign(next);
  };

  const openApprovalFlow = () => {
    if (isDemoViewer) {
      setDemoRestrictionOpen(true);
      return;
    }
    setApprovalModalOpen(true);
  };

  const submitApproval = async () => {
    setSubmittingApproval(true);
    setExportError(null);
    try {
      await api.approveCampaign(campaign.id, orgId, {
        approval_meaning: approvalRole,
        comments: approvalComments.trim() || undefined,
      });
      setApprovalComments("");
      setApprovalRole("reviewer");
      setApprovalModalOpen(false);
      await refreshCampaign();
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmittingApproval(false);
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
    <DetailLayout
      storyboard={
        <Storyboard
          title={campaign.name}
          status={<StatusBadge status={extra.status || "active"} />}
          rows={[
            { label: "Target", value: extra.target || "—" },
            { label: "Process", value: extra.process_type || "—" },
            { label: "Batches", value: campaign.batch_count },
            { label: "CRO", value: extra.cro_partner || "—" },
            { label: "Delivered", value: fmtDateOnly(extra.delivery_date) },
          ]}
        />
      }
    >
    <div className={styles.grid}>
      <HeroHeader
        eyebrow={extra.target ? `Target · ${extra.target}` : "Bioprocess campaign"}
        title={campaign.name}
        context={
          <>
            {campaign.description ? <p>{campaign.description}</p> : null}
            {extra.cro_partner && extra.delivery_date ? (
              <p>
                Delivered by <strong>{extra.cro_partner}</strong> on{" "}
                <strong>{fmtDateOnly(extra.delivery_date)}</strong>.
              </p>
            ) : null}
          </>
        }
        status={<StatusBadge status={extra.status || "active"} />}
        actions={
          <ActionBar>
            <PrimaryButton onClick={onDownload} loading={exporting}>
              {exporting ? "Bundling…" : "Download Evidence Book"}
            </PrimaryButton>
            {isWetlab ? (
              <span data-tour="wetlab-export" className={styles.tourInlineGroup}>
                <SecondaryButton
                  onClick={onDownloadBatchRecord}
                  loading={exportingBatch}
                >
                  {exportingBatch ? "Bundling…" : "Download Batch Record"}
                </SecondaryButton>
              </span>
            ) : null}
            <SecondaryButton
              as="a"
              href={withOrg(`/campaigns/${campaign.id}/methods`, orgId)}
            >
              Methods
            </SecondaryButton>
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
      {exportToast ? (
        <div className={styles.toast} role="status" aria-live="polite">
          {exportToast}
        </div>
      ) : null}

      <div className={styles.deliveryInfo} data-tour="wetlab-campaign-overview">
        <strong>{extra.cro_partner || "BioProcess Labs"} delivery verified</strong>
        <span>{campaign.batch_count} fed-batch conditions organized automatically</span>
        <span>{extra.process_type || "mAb process development"}</span>
      </div>

      <KpiStrip items={kpis} />

      <SectionRule eyebrow="Approval" title="Regulatory sign-off" />
      <ApprovalSection
        approvals={approvals}
        onAdd={openApprovalFlow}
      />

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

      {approvalModalOpen ? (
        <div className={styles.modalBackdrop} role="presentation">
          <div className={styles.modalCard} role="dialog" aria-modal="true" aria-labelledby="approval-title">
            <div className={styles.modalHeader}>
              <div>
                <p className={styles.modalEyebrow}>Campaign approval</p>
                <h3 id="approval-title">Sign off on this campaign</h3>
              </div>
              <button
                type="button"
                className={styles.modalClose}
                onClick={() => setApprovalModalOpen(false)}
                aria-label="Close approval modal"
              >
                ×
              </button>
            </div>
            <label className={styles.formField}>
              <span>Your role</span>
              <select value={approvalRole} onChange={(e) => setApprovalRole(e.target.value as typeof approvalRole)}>
                <option value="author">Author</option>
                <option value="reviewer">Reviewer</option>
                <option value="approver">Approver</option>
              </select>
            </label>
            <label className={styles.formField}>
              <span>Comments (optional)</span>
              <textarea
                value={approvalComments}
                onChange={(e) => setApprovalComments(e.target.value)}
                placeholder="e.g. Reviewed all docking parameters and MD stability metrics. Results are consistent with expectations."
              />
            </label>
            <div className={styles.modalActions}>
              <SecondaryButton onClick={() => setApprovalModalOpen(false)}>
                Cancel
              </SecondaryButton>
              <PrimaryButton onClick={submitApproval} loading={submittingApproval}>
                Sign off on this campaign
              </PrimaryButton>
            </div>
          </div>
        </div>
      ) : null}

      {demoRestrictionOpen ? (
        <div className={styles.modalBackdrop} role="presentation">
          <div className={styles.modalCard} role="dialog" aria-modal="true" aria-labelledby="demo-approval-title">
            <div className={styles.modalHeader}>
              <div>
                <p className={styles.modalEyebrow}>Demo restriction</p>
                <h3 id="demo-approval-title">Approval signing is disabled</h3>
              </div>
              <button
                type="button"
                className={styles.modalClose}
                onClick={() => setDemoRestrictionOpen(false)}
                aria-label="Close demo restriction modal"
              >
                ×
              </button>
            </div>
            <p className={styles.demoCopy}>
              This action requires an account. In the live product, you can sign off on campaigns
              here, creating a permanent record for regulatory submissions.{" "}
              <a href="mailto:hello@lablink.ai?subject=Early%20access%20request">
                Request early access →
              </a>
            </p>
            <div className={styles.modalActions}>
              <PrimaryButton onClick={() => setDemoRestrictionOpen(false)}>
                Got it
              </PrimaryButton>
            </div>
          </div>
        </div>
      ) : null}
    </div>
    </DetailLayout>
  );
}

function ApprovalSection({
  approvals,
  onAdd,
}: {
  approvals: CampaignApproval[];
  onAdd: () => void;
}) {
  if (approvals.length === 0) {
    return (
      <div className={styles.approvalPanel}>
        <div className={`${styles.approvalBanner} ${styles.approvalWarn}`}>
          This campaign has not been approved by a qualified reviewer. Add an approval before regulatory submission.
        </div>
        <SecondaryButton onClick={onAdd}>
          Add Approval
        </SecondaryButton>
      </div>
    );
  }

  return (
    <div className={styles.approvalPanel}>
      <div className={`${styles.approvalBanner} ${styles.approvalGood}`}>
        ✓ Campaign approved — {approvals.length} sign-off(s)
      </div>
      <div className={styles.approvalList}>
        {approvals.map((approval) => (
          <div className={styles.approvalItem} key={approval.id}>
            <span className={styles.roleBadge}>{approval.approval_meaning}</span>
            <strong>{approval.approved_by_name}</strong>
            <span>{fmtDateOnly(approval.created_at)}</span>
            {approval.comments ? <p>{truncate(approval.comments, 120)}</p> : null}
          </div>
        ))}
      </div>
      <button type="button" className={styles.textLinkButton} onClick={onAdd}>
        Add another approval
      </button>
    </div>
  );
}

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}
