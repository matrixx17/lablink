import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, AuditEvent, Campaign, CampaignRun, MoleculeListItem, MoleculeDetail } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import {
  ActionBar,
  Card,
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
import styles from "./pages.module.css";

const statusClass: Record<string, string> = {
  active: styles.statusActive,
  lead_nominated: styles.statusLead,
  completed: styles.statusComplete,
  archived: styles.statusComplete
};

function dateOnly(value?: string | null) {
  if (!value) return "-";
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function messageFor(event: AuditEvent) {
  const details = event.details || {};
  return typeof details.message === "string" ? details.message : humanize(event.action);
}

function timelineIcon(action: string) {
  if (action === "cro_delivery") return "📦";
  if (action === "lead_nominated") return "⭐";
  return "✅";
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
  if (times.length < 2) return "-";
  const days = Math.max(1, Math.ceil((Math.max(...times) - Math.min(...times)) / 86_400_000));
  return `${days} day${days === 1 ? "" : "s"}`;
}

function rangeFor(runs: CampaignRun[], events: AuditEvent[] = []) {
  const values = [
    ...runs.flatMap((run) => [run.created_at, run.started_at, run.completed_at]),
    ...events.map((event) => event.timestamp)
  ]
    .filter(Boolean)
    .map((value) => new Date(value as string).getTime())
    .filter(Number.isFinite);
  if (!values.length) return "-";
  return `${dateOnly(new Date(Math.min(...values)).toISOString())} - ${dateOnly(new Date(Math.max(...values)).toISOString())}`;
}

function qcPassRate(runs: CampaignRun[]) {
  if (!runs.length) return null;
  const pass = runs.filter((run) => (run.qc_status || run.status || "").toLowerCase().includes("pass")).length;
  return Math.round((pass / runs.length) * 100);
}

function phaseFor(run: CampaignRun) {
  const actor = (run.actor || "").toLowerCase();
  if (actor.includes("cro") || actor.includes("bio labs")) return "cro";
  if (actor.includes("demo_computational_team") || run.run_kind === "molecular_dynamics" || run.run_kind === "dft") return "internal";
  return "other";
}

export default function CampaignDetailPage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [runs, setRuns] = useState<CampaignRun[]>([]);
  const [molecules, setMolecules] = useState<MoleculeListItem[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [lead, setLead] = useState<MoleculeDetail | null>(null);
  const [status, setStatus] = useState("");
  const [exportingBco, setExportingBco] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api.campaign(id, orgId),
      api.campaignRuns(id, orgId),
      api.campaignMolecules(id, orgId),
      api.audit(id, orgId)
    ])
      .then(([c, r, m, a]) => {
        setCampaign(c);
        setRuns(r);
        setMolecules(m);
        setAudit(a);
        if (c.lead_molecule_id) {
          api.molecule(c.lead_molecule_id, orgId).then(setLead).catch(() => setLead(null));
        } else {
          setLead(null);
        }
      })
      .catch(setError);
  }, [id, orgId]);

  const filteredRuns = useMemo(() => {
    return status ? runs.filter((run) => run.status === status || run.qc_status === status) : runs;
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
    } catch (err) {
      setError(err);
    } finally {
      setExportingBco(false);
    }
  };

  const delivery = campaign?.metadata || {};
  const deliveryExtra = (delivery.extra_params || {}) as Record<string, unknown>;
  const deliveredBy = String(delivery.delivered_by || delivery.cro_partner || deliveryExtra.delivered_by || "");
  const deliveryDate = String(delivery.delivery_date || deliveryExtra.delivery_date || "");

  const leadDockingScore = useMemo(() => {
    const assay = lead?.assay_results.find((result) => result.metric_name === "docking_score_top")
      || lead?.assay_results.find((result) => result.metric_name === "best_binding_affinity");
    return assay ? `${fmtNumber(assay.value)} ${assay.unit}` : "-";
  }, [lead]);

  const timeline = useMemo(() => {
    const major = audit.filter((event) => event.action === "cro_delivery" || event.action === "lead_nominated");
    const runComplete = audit.filter((event) => event.action === "run_complete");
    const visibleRuns = runComplete.length <= 2 ? runComplete : [runComplete[0], runComplete[runComplete.length - 1]];
    return {
      items: [...major, ...visibleRuns].sort((a, b) => String(a.timestamp || "").localeCompare(String(b.timestamp || ""))),
      hiddenRunCount: Math.max(0, runComplete.length - visibleRuns.length)
    };
  }, [audit]);

  const phases = useMemo(() => {
    const croRuns = runs.filter((run) => phaseFor(run) === "cro");
    const internalRuns = runs.filter((run) => phaseFor(run) === "internal");
    const leadEvents = audit.filter((event) => event.action === "lead_nominated");
    return [
      {
        title: "Phase 1: CRO Delivery",
        runs: croRuns.length,
        molecules: new Set(croRuns.map((run) => run.molecule_id).filter(Boolean)).size,
        range: rangeFor(croRuns, audit.filter((event) => event.action === "cro_delivery"))
      },
      {
        title: "Phase 2: Internal Follow-up",
        runs: internalRuns.length,
        molecules: new Set(internalRuns.map((run) => run.molecule_id).filter(Boolean)).size,
        range: rangeFor(internalRuns)
      },
      {
        title: "Phase 3: Lead Selection",
        runs: 0,
        molecules: campaign?.lead_molecule_id ? 1 : 0,
        range: rangeFor([], leadEvents)
      }
    ];
  }, [audit, campaign?.lead_molecule_id, runs]);

  if (error) return <ErrorBox error={error} />;
  if (!campaign) return <EmptyState>Loading campaign...</EmptyState>;

  return (
    <div className={`${styles.grid} ${styles.reveal}`}>
      <HeroHeader
        eyebrow={campaign.project_name}
        title={campaign.name}
        context={
          <>
            <p className={styles.heroTarget}>{campaign.target_name || campaign.project_name}</p>
            {campaign.description ? <p className={styles.heroDescription}>{campaign.description}</p> : null}
            {deliveredBy && deliveryDate ? (
              <div className={styles.deliveryInfo}>
                Delivered by <strong>{deliveredBy}</strong> on <strong>{dateOnly(deliveryDate)}</strong>
              </div>
            ) : null}
          </>
        }
        status={
          <span className={`${styles.campaignStatusPill} ${statusClass[campaign.status] || styles.statusComplete}`}>
            {humanize(campaign.status)}
          </span>
        }
        actions={
          <ActionBar>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/sar`, orgId)}>SAR explorer</SecondaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/methods-export`, orgId)}>Methods</SecondaryButton>
            <SecondaryButton as="a" href={withOrg(`/campaigns/${id}/audit`, orgId)}>Audit trail</SecondaryButton>
            <PrimaryButton as="a" href={`/api/v1/campaigns/${id}/export?org_id=${encodeURIComponent(orgId)}&format=csv`}>
              Export CSV
            </PrimaryButton>
            <SecondaryButton onClick={exportBco} disabled={exportingBco} loading={exportingBco}>
              Export BCO
            </SecondaryButton>
          </ActionBar>
        }
      />

      {lead ? (
        <Link className={styles.leadCard} to={withOrg(`/molecules/${lead.id}`, orgId)}>
          <span className={styles.leadEyebrow}>Lead candidate</span>
          <img src={api.moleculeSvgUrl(lead.id, orgId)} alt={`Structure for ${lead.name || lead.external_id || "lead molecule"}`} />
          <strong>{lead.name || lead.external_id || `Molecule ${lead.id}`}</strong>
          <span>{lead.external_id || "selected lead"}</span>
          <div className={styles.leadScore}>
            <span>Top docking score</span>
            <strong>{leadDockingScore}</strong>
          </div>
        </Link>
      ) : null}

      <KpiStrip
        items={[
          { label: "Total compounds", value: campaign.molecule_count },
          { label: "Total runs", value: campaign.run_count },
          {
            label: "QC pass rate",
            value: passRate === null ? "—" : passRate,
            unit: passRate === null ? undefined : "%",
            tone: passRateTone as "neutral" | "good" | "warn" | "bad",
          },
          { label: "Campaign duration", value: daysBetween(runs) },
        ]}
      />

      <div className={styles.twoCol}>
        <Card>
          <SectionRule eyebrow="Provenance" title="Timeline" />
          {timeline.items.length === 0 ? <EmptyState>No major audit events yet.</EmptyState> : (
            <div className={styles.timeline}>
              {timeline.items.map((event) => (
                <div className={styles.timelineItem} key={`${event.action}-${event.id}`}>
                  <div className={styles.timelineIcon}>{timelineIcon(event.action)}</div>
                  <div>
                    <div className={styles.timelineHeader}>
                      <strong>{humanize(event.action)}</strong>
                      <span>{fmtDate(event.timestamp)}</span>
                    </div>
                    <p>{messageFor(event)}</p>
                    {event.actor ? <span className={styles.muted}>Actor: {event.actor}</span> : null}
                  </div>
                </div>
              ))}
              {timeline.hiddenRunCount > 0 ? (
                <Link className={styles.link} to={withOrg(`/campaigns/${id}/audit`, orgId)}>
                  and {timeline.hiddenRunCount} more runs
                </Link>
              ) : null}
            </div>
          )}
        </Card>

        <Card>
          <SectionRule eyebrow="Delivery phases" title="Phase breakdown" />
          <div className={styles.phaseGrid}>
            {phases.map((phase) => (
              <div className={styles.phaseCard} key={phase.title}>
                <h3>{phase.title}</h3>
                <div><strong>{phase.runs}</strong><span>runs</span></div>
                <div><strong>{phase.molecules}</strong><span>molecules</span></div>
                <p>{phase.range}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card>
        <SectionRule
          eyebrow="Runs"
          title="Computational jobs"
          actions={
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
              <option value="">All statuses</option>
              {Object.keys(statusCounts).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          }
        />
        {filteredRuns.length === 0 ? <EmptyState>No runs yet.</EmptyState> : (
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
                    <td><Link className={styles.link} to={withOrg(`/runs/${run.id}`, orgId)}>Run {run.id}</Link></td>
                    <td>
                      {run.molecule_id ? (
                        <Link className={styles.link} to={withOrg(`/molecules/${run.molecule_id}`, orgId)}>
                          {run.molecule_external_id || run.molecule_name || `Molecule ${run.molecule_id}`}
                        </Link>
                      ) : <span className={styles.muted}>multi / none</span>}
                    </td>
                    <td>{run.run_kind}</td>
                    <td><StatusBadge status={run.status} /></td>
                    <td><StatusBadge status={run.qc_status} /></td>
                    <td>{[run.software_name, run.software_version].filter(Boolean).join(" ") || "-"}</td>
                    <td>{fmtNumber(run.metric_count, 0)}</td>
                    <td>{fmtDate(run.completed_at)}</td>
                  </tr>
                ))}
            </tbody>
          </DataTable>
        )}
      </Card>

      <Card>
        <SectionRule eyebrow="Compounds" title="Molecules in campaign" />
        {molecules.length === 0 ? <EmptyState>No molecules recorded.</EmptyState> : (
          <DataTable>
              <thead>
                <tr>
                  <th>Molecule</th>
                  <th>InChIKey</th>
                  <th>MW</th>
                  <th>Runs</th>
                  <th>Top Metrics</th>
                </tr>
              </thead>
              <tbody>
                {molecules.map((molecule) => (
                  <tr key={molecule.id}>
                    <td>
                      <Link className={styles.link} to={withOrg(`/molecules/${molecule.id}`, orgId)}>
                        {molecule.external_id || molecule.name || `Molecule ${molecule.id}`}
                      </Link>
                      {campaign.lead_molecule_id === molecule.id ? <div className={styles.muted}>Lead candidate</div> : null}
                    </td>
                    <td><span className={styles.muted}>{molecule.inchi_key}</span></td>
                    <td>{fmtNumber(molecule.molecular_weight)}</td>
                    <td>{molecule.run_count}</td>
                    <td>{molecule.top_metrics.slice(0, 2).map((metric) => `${metric.metric_name}: ${fmtNumber(metric.best_value)} ${metric.unit}`).join(", ") || "-"}</td>
                  </tr>
                ))}
            </tbody>
          </DataTable>
        )}
      </Card>
    </div>
  );
}
