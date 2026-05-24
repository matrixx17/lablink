import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  AuditEvent,
  Campaign,
  CampaignRun,
  DeliveryVerification,
  MoleculeListItem,
  MoleculeDetail,
} from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  DataTable,
  EmptyState,
  ErrorBox,
  fmtDate,
  fmtNumber,
  HeroHeader,
  KpiStrip,
  PrimaryButton,
  SecondaryButton,
  SectionRule,
  StatusBadge,
} from "../components/ui";
import { downloadBcoExport } from "../lib/bcoExport";
import { downloadEvidenceBook } from "../lib/evidenceBook";
import styles from "./pages.module.css";

const statusClass: Record<string, string> = {
  active: styles.statusActive,
  lead_nominated: styles.statusLead,
  completed: styles.statusComplete,
  archived: styles.statusComplete,
};

function dateOnly(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function messageFor(event: AuditEvent) {
  const details = event.details || {};
  return typeof details.message === "string" ? details.message : humanize(event.action);
}

function timelineIcon(action: string) {
  if (action === "cro_delivery") return "↓";
  if (action === "lead_nominated") return "★";
  return "·";
}

function humanize(value: string) {
  return value.replace(/_/g, " ");
}

function daysBetween(runs: CampaignRun[]) {
  const times = runs
    .flatMap((run) => [run.started_at, run.completed_at, run.created_at])
    .filter(Boolean)
    .map((value) => new Date(value as string).getTime())
    .filter(Number.isFinite);
  if (times.length < 2) return "—";
  const days = Math.max(1, Math.ceil((Math.max(...times) - Math.min(...times)) / 86_400_000));
  return `${days}`;
}

function qcPassRate(runs: CampaignRun[]) {
  if (!runs.length) return null;
  const pass = runs.filter((run) =>
    (run.qc_status || run.status || "").toLowerCase().includes("pass"),
  ).length;
  return Math.round((pass / runs.length) * 100);
}

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [runs, setRuns] = useState<CampaignRun[]>([]);
  const [molecules, setMolecules] = useState<MoleculeListItem[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [lead, setLead] = useState<MoleculeDetail | null>(null);
  const [deliveryVerification, setDeliveryVerification] = useState<DeliveryVerification | null>(null);
  const [verifyingDelivery, setVerifyingDelivery] = useState(false);
  const [status, setStatus] = useState("");
  const [exportingBco, setExportingBco] = useState(false);
  const [exportingEvidenceBook, setExportingEvidenceBook] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api.campaign(id, orgId),
      api.campaignRuns(id, orgId),
      api.campaignMolecules(id, orgId),
      api.audit(id, orgId),
    ])
      .then(([c, r, m, a]) => {
        setCampaign(c);
        setRuns(r);
        setMolecules(m);
        setAudit(a);
        if (c.has_cro_delivery) {
          setVerifyingDelivery(true);
          api
            .verifyDelivery(id, orgId)
            .then(setDeliveryVerification)
            .catch(setError)
            .finally(() => setVerifyingDelivery(false));
        } else {
          setDeliveryVerification(null);
        }
        if (c.lead_molecule_id) {
          api
            .molecule(c.lead_molecule_id, orgId)
            .then(setLead)
            .catch(() => setLead(null));
        } else {
          setLead(null);
        }
      })
      .catch(setError);
  }, [id, orgId]);

  const filteredRuns = useMemo(() => {
    return status
      ? runs.filter((run) => run.status === status || run.qc_status === status)
      : runs;
  }, [runs, status]);

  const statusCounts = useMemo(() => {
    return runs.reduce<Record<string, number>>((acc, run) => {
      const key = run.qc_status || run.status || "unknown";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});
  }, [runs]);

  const passRate = useMemo(() => qcPassRate(runs), [runs]);
  const passRateTone =
    passRate === null ? "neutral" : passRate > 80 ? "good" : passRate >= 60 ? "warn" : "bad";

  const exportBco = async () => {
    setExportingBco(true);
    try {
      await downloadBcoExport(id, orgId);
      setToast("BCO exported — IEEE 2791-2020 compliant");
      window.setTimeout(() => setToast(""), 3500);
    } catch (err) {
      setError(err);
    } finally {
      setExportingBco(false);
    }
  };

  const exportEvidenceBook = async () => {
    if (!campaign) return;
    setExportingEvidenceBook(true);
    try {
      const result = await downloadEvidenceBook(id, orgId);
      setToast(`Evidence Book exported — ${result.fileCount} files, ${campaign.name}`);
      window.setTimeout(() => setToast(""), 3500);
    } catch (err) {
      setError(err);
    } finally {
      setExportingEvidenceBook(false);
    }
  };

  const reverifyDelivery = async () => {
    setVerifyingDelivery(true);
    try {
      setDeliveryVerification(await api.verifyDelivery(id, orgId));
    } catch (err) {
      setError(err);
    } finally {
      setVerifyingDelivery(false);
    }
  };

  const delivery = campaign?.metadata || {};
  const deliveryExtra = (delivery.extra_params || {}) as Record<string, unknown>;
  const deliveredBy = String(
    delivery.delivered_by || delivery.cro_partner || deliveryExtra.delivered_by || "",
  );
  const deliveryDate = String(
    delivery.delivery_date || deliveryExtra.delivery_date || "",
  );

  const leadDockingScore = useMemo(() => {
    const assay =
      lead?.assay_results.find((result) => result.metric_name === "docking_score_top") ||
      lead?.assay_results.find((result) => result.metric_name === "best_binding_affinity");
    return assay ? { value: fmtNumber(assay.value), unit: assay.unit } : null;
  }, [lead]);

  const timeline = useMemo(() => {
    const major = audit.filter(
      (event) => event.action === "cro_delivery" || event.action === "lead_nominated",
    );
    const runComplete = audit.filter((event) => event.action === "run_complete");
    const visibleRuns =
      runComplete.length <= 2 ? runComplete : [runComplete[0], runComplete[runComplete.length - 1]];
    return {
      items: [...major, ...visibleRuns].sort((a, b) =>
        String(a.timestamp || "").localeCompare(String(b.timestamp || "")),
      ),
      hiddenRunCount: Math.max(0, runComplete.length - visibleRuns.length),
    };
  }, [audit]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign) return <div className={styles.centerMessage}>Loading campaign…</div>;
  const showBcoExport = !campaign.domain || campaign.domain === "compchem";

  return (
    <div className={styles.grid}>
      {toast ? (
        <div className={styles.toast} role="status" aria-live="polite">
          {toast}
        </div>
      ) : null}
      <HeroHeader
        eyebrow={campaign.project_name}
        title={campaign.name}
        context={
          <>
            {campaign.target_name ? (
              <p>
                Target: <strong>{campaign.target_name}</strong>
              </p>
            ) : null}
            {campaign.description ? <p>{campaign.description}</p> : null}
            {deliveredBy && deliveryDate ? (
              <p>
                Delivered by <strong>{deliveredBy}</strong> on{" "}
                <strong>{dateOnly(deliveryDate)}</strong>.
              </p>
            ) : null}
          </>
        }
        status={
          <span
            className={`${styles.campaignStatusPill} ${
              statusClass[campaign.status] || styles.statusComplete
            }`}
          >
            {humanize(campaign.status)}
          </span>
        }
        actions={
          <ActionBar>
            <PrimaryButton
              as="a"
              href={`/api/v1/campaigns/${id}/export?org_id=${encodeURIComponent(orgId)}&format=csv`}
            >
              Export CSV
            </PrimaryButton>
            <span data-tour="compchem-exports" className={styles.tourInlineGroup}>
              {showBcoExport ? (
                <SecondaryButton onClick={exportBco} disabled={exportingBco} loading={exportingBco}>
                  Export BCO
                </SecondaryButton>
              ) : null}
              <SecondaryButton
                onClick={exportEvidenceBook}
                disabled={exportingEvidenceBook}
                loading={exportingEvidenceBook}
              >
                Export Evidence Book
              </SecondaryButton>
            </span>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/audit`, orgId)}>
              Audit trail
            </SecondaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/sar`, orgId)}>
              SAR explorer
            </SecondaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/methods-export`, orgId)}>
              Methods
            </SecondaryButton>
          </ActionBar>
        }
      />

      {campaign.has_cro_delivery ? (
        <section className={styles.deliveryVerificationCard} data-tour="compchem-delivery">
          <div className={styles.deliveryVerificationIcon} aria-hidden>
            <svg viewBox="0 0 24 24" role="img">
              <path d="M12 3 5 6v5c0 4.4 2.8 8.4 7 10 4.2-1.6 7-5.6 7-10V6l-7-3Z" />
            </svg>
          </div>
          <div className={styles.deliveryVerificationBody}>
            <div className={styles.deliveryVerificationHeader}>
              <div>
                <p className={styles.leadEyebrow}>Delivery Verification</p>
                <h2>CRO Delivery</h2>
                <p className={styles.deliveryVerificationSubtitle}>
                  Delivered by{" "}
                  <strong>{deliveryVerification?.delivered_by || deliveredBy || "CRO uploader"}</strong>
                  {deliveryVerification?.delivered_at || deliveryDate ? (
                    <>
                      {" "}on{" "}
                      <strong>{dateOnly(deliveryVerification?.delivered_at || deliveryDate)}</strong>
                    </>
                  ) : null}
                </p>
              </div>
              <StatusBadge status={deliveryVerification?.verification_status || "unavailable"} />
            </div>
            <div className={styles.deliveryVerificationMeta}>
              <span>{deliveryVerification?.files_verified ?? 0} verified</span>
              <span>{deliveryVerification?.files_checked ?? 0} files checked</span>
              <span>{deliveryVerification?.files_modified ?? 0} modified</span>
            </div>
            {deliveryVerification?.demo_mode ? (
              <p className={styles.deliveryDemoNote}>
                Demo mode: object storage is unavailable, so verification is simulated from stored delivery hashes.
              </p>
            ) : null}
            {deliveryVerification?.modified_files?.length ? (
              <details className={styles.modifiedFiles}>
                <summary>Show modified filenames</summary>
                <ul>
                  {deliveryVerification.modified_files.map((filename) => (
                    <li key={filename}>{filename}</li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
          <div className={styles.deliveryVerificationActions}>
            <SecondaryButton onClick={reverifyDelivery} disabled={verifyingDelivery} loading={verifyingDelivery}>
              Re-verify now
            </SecondaryButton>
          </div>
        </section>
      ) : null}

      <KpiStrip
        items={[
          { label: "Compounds", value: campaign.molecule_count },
          { label: "Runs", value: campaign.run_count },
          {
            label: "QC pass rate",
            value: passRate === null ? "—" : passRate,
            unit: passRate === null ? undefined : "%",
            tone: passRateTone as "neutral" | "good" | "warn" | "bad",
          },
          { label: "Duration", value: daysBetween(runs), unit: "days" },
        ]}
      />

      {lead ? (
        <Link
          className={styles.leadBanner}
          to={withOrg(`/molecules/${lead.id}`, orgId)}
          style={{ textDecoration: "none" }}
          data-tour="compchem-lead"
        >
          <div>
            <p className={styles.leadEyebrow}>Lead candidate</p>
            <h3>{lead.name || lead.external_id || `Molecule ${lead.id}`}</h3>
            {lead.canonical_smiles ? (
              <div className={styles.smilesBox} style={{ marginTop: 14 }}>
                {lead.canonical_smiles}
              </div>
            ) : null}
          </div>
          {leadDockingScore ? (
            <div className={styles.leadScore}>
              {leadDockingScore.value}
              <small>{leadDockingScore.unit} · top docking</small>
            </div>
          ) : null}
        </Link>
      ) : null}

      <SectionRule eyebrow="Provenance" title="Major events" />
      {timeline.items.length === 0 ? (
        <EmptyState>No major audit events yet.</EmptyState>
      ) : (
        <div className={styles.timeline}>
          {timeline.items.map((event) => (
            <div className={styles.timelineItem} key={`${event.action}-${event.id}`}>
              <div className={styles.timelineIcon}>{timelineIcon(event.action)}</div>
              <div>
                <div className={styles.timelineHeader}>
                  <strong style={{ fontFamily: "var(--body)", fontSize: 14 }}>
                    {humanize(event.action)}
                  </strong>
                  <span>{fmtDate(event.timestamp)}</span>
                </div>
                <p style={{ margin: "6px 0 0" }}>{messageFor(event)}</p>
                {event.actor ? (
                  <span className={styles.muted}>actor · {event.actor}</span>
                ) : null}
              </div>
            </div>
          ))}
          {timeline.hiddenRunCount > 0 ? (
            <Link
              className={styles.link}
              to={withOrg(`/campaigns/${id}/audit`, orgId)}
              style={{ marginTop: 14, display: "inline-block" }}
            >
              and {timeline.hiddenRunCount} more runs in audit trail →
            </Link>
          ) : null}
        </div>
      )}

      <SectionRule
        eyebrow="Computational jobs"
        title={`Runs (${filteredRuns.length})`}
        actions={
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {Object.keys(statusCounts).map((s) => (
              <option key={s} value={s}>
                {s} ({statusCounts[s]})
              </option>
            ))}
          </select>
        }
      />
      {filteredRuns.length === 0 ? (
        <EmptyState>No runs yet.</EmptyState>
      ) : (
        <DataTable>
          <thead>
            <tr>
              <th>Run</th>
              <th>Molecule</th>
              <th>Kind</th>
              <th>Status</th>
              <th>QC</th>
              <th>Software</th>
              <th>Metrics</th>
              <th>Completed</th>
            </tr>
          </thead>
          <tbody>
            {filteredRuns.map((run) => (
              <tr key={run.id}>
                <td>
                  <Link className={styles.link} to={withOrg(`/runs/${run.id}`, orgId)}>
                    Run {run.id}
                  </Link>
                </td>
                <td>
                  {run.molecule_id ? (
                    <Link
                      className={styles.link}
                      to={withOrg(`/molecules/${run.molecule_id}`, orgId)}
                    >
                      {run.molecule_external_id || run.molecule_name || `Molecule ${run.molecule_id}`}
                    </Link>
                  ) : (
                    <span className={styles.muted}>multi / none</span>
                  )}
                </td>
                <td>{run.run_kind}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>
                  <StatusBadge status={run.qc_status} />
                </td>
                <td>
                  {[run.software_name, run.software_version].filter(Boolean).join(" ") || "—"}
                </td>
                <td className="num">{fmtNumber(run.metric_count, 0)}</td>
                <td className="num">{fmtDate(run.completed_at)}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}

      <SectionRule eyebrow="Compounds" title={`Molecules (${molecules.length})`} />
      {molecules.length === 0 ? (
        <EmptyState>No molecules recorded.</EmptyState>
      ) : (
        <DataTable>
          <thead>
            <tr>
              <th>Molecule</th>
              <th>InChIKey</th>
              <th>MW</th>
              <th>Runs</th>
              <th>Top metrics</th>
            </tr>
          </thead>
          <tbody>
            {molecules.map((molecule) => (
              <tr key={molecule.id}>
                <td>
                  <Link
                    className={styles.link}
                    to={withOrg(`/molecules/${molecule.id}`, orgId)}
                  >
                    {molecule.external_id || molecule.name || `Molecule ${molecule.id}`}
                  </Link>
                  {campaign.lead_molecule_id === molecule.id ? (
                    <div className={styles.muted}>lead candidate</div>
                  ) : null}
                </td>
                <td>
                  <span className={styles.muted}>{molecule.inchi_key}</span>
                </td>
                <td className="num">{fmtNumber(molecule.molecular_weight)}</td>
                <td className="num">{molecule.run_count}</td>
                <td>
                  {molecule.top_metrics
                    .slice(0, 2)
                    .map((m) => `${m.metric_name}: ${fmtNumber(m.best_value)} ${m.unit}`)
                    .join(", ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
    </div>
  );
}
