import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, MoleculeDetail } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, fmtDate, fmtNumber, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

type MoleculeRun = MoleculeDetail["runs"][number];

const runIcons: Record<string, string> = {
  docking: "⚓",
  molecular_dynamics: "〰",
  dft: "⚛",
  property_prediction: "▣"
};

function runLabel(kind: string) {
  if (kind === "molecular_dynamics") return "MD Simulation";
  if (kind === "dft") return "DFT";
  if (kind === "docking") return "Docking Run";
  return kind.replace(/_/g, " ");
}

function runOwner(run: MoleculeRun) {
  const actor = (run.audit_events || []).find((event) => event.action === "run_submitted")?.actor || "";
  if (actor.includes("cro")) return "Bio Labs";
  if (actor.includes("demo_computational_team")) return "Demo Therapeutics";
  return "Demo Therapeutics";
}

function qcDotClass(status?: string | null) {
  const value = (status || "").toLowerCase();
  if (value.includes("pass") || value.includes("complete")) return styles.qcDotPass;
  if (value.includes("warn")) return styles.qcDotWarn;
  if (value.includes("fail")) return styles.qcDotFail;
  return styles.qcDotNeutral;
}

function metricRank(metric: string, runKind?: string) {
  const name = metric.toLowerCase();
  if (name.includes("dock") || name.includes("affinity") || name.includes("binding")) return 0;
  if (runKind === "molecular_dynamics" || name.includes("rmsd") || name.includes("energy")) return 1;
  if (runKind === "dft" || name.includes("homo") || name.includes("lumo") || name.includes("final_energy")) return 2;
  return 3;
}

function bestMetric(run: MoleculeRun) {
  const metrics = run.metrics || [];
  return metrics.find((m) => /docking_score_top|best_binding_affinity|md_rmsd|homo_lumo_gap/i.test(m.name)) || metrics[0];
}

function formatMetric(value?: number, unit?: string) {
  if (value === undefined || value === null) return "-";
  return `${fmtNumber(value)}${unit ? ` ${unit}` : ""}`;
}

function formatParam(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function MoleculePage() {
  const { id = "" } = useParams();
  const { orgId } = useOrgId();
  const [molecule, setMolecule] = useState<MoleculeDetail | null>(null);
  const [openRuns, setOpenRuns] = useState<Record<number, boolean>>({});
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.molecule(id, orgId).then(setMolecule).catch(setError);
  }, [id, orgId]);

  const lineageRuns = useMemo(() => {
    const runs = molecule?.runs || [];
    if (!runs.length) return [];
    const byId = new Map(runs.map((run) => [run.id, run]));
    const childIds = new Set((molecule?.lineage || []).map((edge) => edge.child_run_id));
    const start = runs.find((run) => !childIds.has(run.id) && run.run_kind === "docking") || runs.find((run) => run.run_kind === "docking") || runs[0];
    const ordered: MoleculeRun[] = [];
    const seen = new Set<number>();
    let current: MoleculeRun | undefined = start;
    while (current && !seen.has(current.id)) {
      ordered.push(current);
      seen.add(current.id);
      const nextEdge = (molecule?.lineage || []).find((edge) => edge.parent_run_id === current?.id && byId.has(edge.child_run_id));
      current = nextEdge ? byId.get(nextEdge.child_run_id) : undefined;
    }
    for (const run of runs) {
      if (!seen.has(run.id)) ordered.push(run);
    }
    return ordered;
  }, [molecule]);

  const metricRows = useMemo(() => {
    const rows = new Map<string, { metric: string; value: number; unit: string; runType: string; rank: number }>();
    for (const run of molecule?.runs || []) {
      for (const metric of run.metrics || []) {
        if (!rows.has(metric.name)) {
          rows.set(metric.name, {
            metric: metric.name,
            value: metric.value,
            unit: metric.unit,
            runType: run.run_kind,
            rank: metricRank(metric.name, run.run_kind)
          });
        }
      }
    }
    for (const [name, value] of Object.entries(molecule?.properties || {})) {
      if (!rows.has(name) && typeof value.value === "number") {
        rows.set(name, { metric: name, value: value.value, unit: value.unit || "", runType: "property", rank: 3 });
      }
    }
    return Array.from(rows.values()).sort((a, b) => a.rank - b.rank || a.metric.localeCompare(b.metric));
  }, [molecule]);

  if (error) return <ErrorBox error={error} />;
  if (!molecule) return <EmptyState>Loading molecule...</EmptyState>;

  const leadNomination = molecule.lead_nomination;
  const leadDetails = leadNomination?.details || {};
  const approvedBy = typeof leadDetails.approved_by === "string" ? leadDetails.approved_by : "Dr. John Doe";
  const approverName = approvedBy.split(",")[0];
  const leadDate = leadNomination?.timestamp ? new Date(leadNomination.timestamp).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }) : "May 22, 2026";

  const copySmiles = async () => {
    await navigator.clipboard.writeText(molecule.canonical_smiles);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={molecule.external_id || molecule.inchi_key}
        title={molecule.name || `Molecule ${molecule.id}`}
        actions={<Link className={styles.secondaryButton} to={withOrg(`/campaigns/${molecule.campaign_id}/molecules`, orgId)}>Back to SAR</Link>}
      />
      {molecule.is_campaign_lead ? (
        <div className={styles.leadBanner}>
          ⭐ Lead Candidate — nominated {leadDate} by {approverName}
        </div>
      ) : null}

      <Card className={styles.moleculeHeader}>
        <div>
          <img
            className={styles.moleculeStructureLarge}
            src={api.moleculeSvgUrl(molecule.id, orgId)}
            alt={`2D structure for ${molecule.name || molecule.id}`}
          />
        </div>
        <div className={styles.moleculeHeaderCopy}>
          <StatusBadge status={molecule.qc_status || "pass"} />
          <h1>{molecule.name || molecule.external_id || `Molecule ${molecule.id}`}</h1>
          <div className={styles.smilesBox}>
            <code>{molecule.canonical_smiles}</code>
            <button type="button" className={styles.secondaryButton} onClick={copySmiles}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
          <table className={styles.table}>
            <tbody>
              <tr><th>Formula</th><td>{molecule.formula || "-"}</td></tr>
              <tr><th>MW</th><td>{fmtNumber(molecule.molecular_weight)}</td></tr>
              <tr><th>InChIKey</th><td>{molecule.inchi_key}</td></tr>
            </tbody>
          </table>
        </div>
      </Card>

      <Card className={styles.tourCardAnchor} data-tour="compchem-lineage">
        <h2>Computational History</h2>
        {lineageRuns.length === 0 ? <EmptyState>No runs recorded.</EmptyState> : (
          <div className={styles.lineageFlow}>
            {lineageRuns.map((run, index) => {
              const metric = bestMetric(run);
              return (
                <div className={styles.lineageStep} key={run.id}>
                  {index > 0 ? <div className={styles.lineageArrow}>→</div> : null}
                  <button
                    type="button"
                    className={styles.lineageNode}
                    onClick={() => document.getElementById(`run-${run.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  >
                    <span className={styles.lineageIcon}>{runIcons[run.run_kind] || "▣"}</span>
                    <strong>{runLabel(run.run_kind)} — {runOwner(run)}</strong>
                    <span>{[run.software_name, run.software_version].filter(Boolean).join(" ") || "unknown software"}</span>
                    <span>{fmtDate(run.completed_at || run.started_at)}</span>
                    <span>{metric ? `${metric.name}: ${formatMetric(metric.value, metric.unit)}` : "No metrics"}</span>
                    <i className={`${styles.qcDot} ${qcDotClass(run.qc_status || run.status)}`} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <Card>
        <h2>Key Metrics Summary</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Value</th>
                <th>Run Type</th>
              </tr>
            </thead>
            <tbody>
              {metricRows.map((row) => (
                <tr key={row.metric}>
                  <td>{row.metric}</td>
                  <td>{formatMetric(row.value, row.unit)}</td>
                  <td>{runLabel(row.runType)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <h2>Run Details</h2>
        <div className={styles.accordionList}>
          {lineageRuns.map((run) => (
            <section className={styles.runAccordion} id={`run-${run.id}`} key={run.id}>
              <button
                type="button"
                className={styles.runAccordionHeader}
                onClick={() => setOpenRuns((current) => ({ ...current, [run.id]: !current[run.id] }))}
              >
                <span>{runIcons[run.run_kind] || "▣"} {runLabel(run.run_kind)}</span>
                <span>{fmtDate(run.completed_at || run.started_at)}</span>
                <StatusBadge status={run.qc_status || run.status} />
              </button>
              {openRuns[run.id] ? (
                <div className={styles.runAccordionBody}>
                  <h3>Parameters</h3>
                  <div className={styles.kvGrid}>
                    {Object.entries(run.parameters || {}).map(([key, value]) => (
                      <div key={key}>
                        <span>{key}</span>
                        <strong>{formatParam(value)}</strong>
                      </div>
                    ))}
                    {Object.keys(run.parameters || {}).length === 0 ? <p className={styles.muted}>No parameters recorded.</p> : null}
                  </div>
                  <h3>Metrics</h3>
                  <table className={styles.table}>
                    <tbody>
                      {(run.metrics || []).map((metric) => (
                        <tr key={metric.id}>
                          <th>{metric.name}</th>
                          <td>{formatMetric(metric.value, metric.unit)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h3>Audit Events</h3>
                  {(run.audit_events || []).length === 0 ? <p className={styles.muted}>No audit events recorded.</p> : (
                    <div className={styles.timeline}>
                      {(run.audit_events || []).map((event) => (
                        <div className={styles.timelineItem} key={event.id}>
                          <div className={styles.timelineIcon}>✓</div>
                          <div>
                            <div className={styles.timelineHeader}>
                              <strong>{event.action}</strong>
                              <span>{fmtDate(event.timestamp)}</span>
                            </div>
                            <p>{typeof event.details?.message === "string" ? event.details.message : event.actor || "Recorded by LabLink"}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <Link className={styles.link} to={withOrg(`/runs/${run.id}`, orgId)}>Open full run record →</Link>
                </div>
              ) : null}
            </section>
          ))}
        </div>
      </Card>
    </div>
  );
}
