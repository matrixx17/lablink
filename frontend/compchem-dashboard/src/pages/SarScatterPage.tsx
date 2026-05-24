import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { api, Campaign, SarMolecule } from "../api/client";
import { useOrgId, withOrg } from "../components/Layout";
import { Card, EmptyState, ErrorBox, PageHeader, StatusBadge } from "../components/ui";
import styles from "./pages.module.css";

const QC_COLORS: Record<string, string> = {
  pass: "#22c55e",
  warn: "#f59e0b",
  fail: "#ef4444",
  unknown: "#94a3b8"
};

type PlotPoint = {
  molecule: SarMolecule;
  x: number;
  y: number;
  qc: string;
};

function metricLabel(metric: string) {
  return metric
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function displayName(molecule: SarMolecule) {
  return molecule.name || molecule.external_id || `Molecule ${molecule.id}`;
}

function truncSmiles(smiles?: string | null, n = 32) {
  if (!smiles) return "";
  return smiles.length > n ? `${smiles.slice(0, n)}…` : smiles;
}

function qcColor(status?: string | null) {
  const key = (status || "unknown").toLowerCase();
  return QC_COLORS[key] || QC_COLORS.unknown;
}

function formatValue(value: number) {
  if (!Number.isFinite(value)) return "-";
  if (Math.abs(value) >= 1000 || Math.abs(value) < 0.01) {
    return value.toExponential(2);
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

export default function SarScatterPage() {
  const { campaignId = "" } = useParams();
  const { orgId } = useOrgId();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [molecules, setMolecules] = useState<SarMolecule[]>([]);
  const [xMetric, setXMetric] = useState("");
  const [yMetric, setYMetric] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [highlightLead, setHighlightLead] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.campaign(campaignId, orgId),
      api.campaignMoleculesWithMetrics(campaignId, orgId)
    ])
      .then(([campaignResult, moleculeResult]) => {
        setCampaign(campaignResult);
        setMolecules(moleculeResult);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [campaignId, orgId]);

  const metrics = useMemo(() => {
    return Array.from(
      new Set(molecules.flatMap((molecule) => Object.keys(molecule.metrics || {})))
    ).sort();
  }, [molecules]);

  useEffect(() => {
    if (!metrics.length) return;
    if (!xMetric) {
      setXMetric(metrics.includes("docking_score_top") ? "docking_score_top" : metrics[0]);
    }
    if (!yMetric) {
      setYMetric(metrics.includes("mw") ? "mw" : (metrics[1] || metrics[0]));
    }
  }, [metrics, xMetric, yMetric]);

  const points = useMemo<PlotPoint[]>(() => {
    if (!xMetric || !yMetric) return [];
    return molecules
      .filter((molecule) => {
        const values = molecule.metrics || {};
        return typeof values[xMetric] === "number" && typeof values[yMetric] === "number";
      })
      .map((molecule) => ({
        molecule,
        x: molecule.metrics[xMetric],
        y: molecule.metrics[yMetric],
        qc: molecule.qc_status || "unknown"
      }));
  }, [molecules, xMetric, yMetric]);

  const selectedPoint = useMemo(
    () => {
      const plotted = points.find((p) => p.molecule.id === selectedId);
      if (plotted) return plotted;
      const molecule = molecules.find((m) => m.id === selectedId);
      if (!molecule) return null;
      return {
        molecule,
        x: typeof molecule.metrics?.[xMetric] === "number" ? molecule.metrics[xMetric] : Number.NaN,
        y: typeof molecule.metrics?.[yMetric] === "number" ? molecule.metrics[yMetric] : Number.NaN,
        qc: molecule.qc_status || "unknown"
      };
    },
    [molecules, points, selectedId, xMetric, yMetric]
  );

  useEffect(() => {
    if (selectedId || !campaign?.lead_molecule_id) return;
    if (molecules.some((molecule) => molecule.id === campaign.lead_molecule_id)) {
      setSelectedId(campaign.lead_molecule_id);
    }
  }, [campaign?.lead_molecule_id, molecules, selectedId]);

  if (error) return <ErrorBox error={error} />;

  return (
    <div className={styles.grid}>
      <PageHeader
        eyebrow={campaign?.name || "SAR"}
        title="Structure-activity scatter"
        actions={
          <button
            type="button"
            className={styles.secondaryButton}
            onClick={() => navigate(withOrg(`/campaigns/${campaignId}`, orgId))}
          >
            ← Back to campaign
          </button>
        }
      />

      <Card>
        <div className={styles.sarControls}>
          <label>
            <span>X Axis</span>
            <select value={xMetric} onChange={(event) => setXMetric(event.target.value)}>
              {metrics.map((metric) => (
                <option key={metric} value={metric}>
                  {metricLabel(metric)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Y Axis</span>
            <select value={yMetric} onChange={(event) => setYMetric(event.target.value)}>
              {metrics.map((metric) => (
                <option key={metric} value={metric}>
                  {metricLabel(metric)}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.countLine}>
            Showing <strong>{points.length}</strong> of <strong>{molecules.length}</strong> molecules
          </div>
          {campaign?.lead_molecule_id ? (
            <button
              type="button"
              className={`${styles.secondaryButton} ${highlightLead ? styles.toggleActive : ""}`}
              onClick={() => setHighlightLead((value) => !value)}
            >
              {highlightLead ? "Lead highlighted" : "Highlight lead"}
            </button>
          ) : null}
        </div>
      </Card>

      {loading ? (
        <EmptyState>Loading molecule metrics…</EmptyState>
      ) : !metrics.length ? (
        <EmptyState>No molecule metrics available yet for this campaign.</EmptyState>
      ) : (
        <div className={styles.sarLayout}>
          <Card className={styles.sarChartCard}>
            {campaign?.lead_molecule_id ? (
              <div className={styles.sarStory}>
                This chart shows docking scores vs. molecular weight for all 10 compounds screened by Bio Labs. The starred point (AC-007) was selected as the lead candidate. Green = QC pass, Amber = warn, Red = fail.
              </div>
            ) : null}
            {points.length < 2 ? (
              <div className={styles.centerMessage}>
                Not enough data for the selected metrics. Try a different axis combination.
              </div>
            ) : (
              <div className={styles.chart}>
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 18, right: 28, bottom: 48, left: 44 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis
                      type="number"
                      dataKey="x"
                      name={metricLabel(xMetric)}
                      label={{ value: metricLabel(xMetric), position: "bottom", offset: 18 }}
                      tick={{ fill: "#475569", fontSize: 12 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="y"
                      name={metricLabel(yMetric)}
                      label={{ value: metricLabel(yMetric), angle: -90, position: "left", offset: 20 }}
                      tick={{ fill: "#475569", fontSize: 12 }}
                    />
                    <Tooltip
                      cursor={{ strokeDasharray: "3 3" }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.[0]) return null;
                        const point = payload[0].payload as PlotPoint;
                        return (
                          <div className={styles.sarTooltip}>
                            <strong>{displayName(point.molecule)}</strong>
                            <div className={styles.tooltipMuted}>
                              {truncSmiles(point.molecule.smiles || point.molecule.canonical_smiles)}
                            </div>
                            <div className={styles.tooltipRow}>
                              <span>{metricLabel(xMetric)}</span>
                              <strong>{formatValue(point.x)}</strong>
                            </div>
                            <div className={styles.tooltipRow}>
                              <span>{metricLabel(yMetric)}</span>
                              <strong>{formatValue(point.y)}</strong>
                            </div>
                            <div className={styles.tooltipRow}>
                              <span>QC</span>
                              <strong>{point.qc || "unknown"}</strong>
                            </div>
                            <div className={styles.tooltipHint}>Click to pin details →</div>
                          </div>
                        );
                      }}
                    />
                    <Scatter
                      data={points}
                      isAnimationActive={false}
                      shape={(props: any) => {
                        const payload = props?.payload as PlotPoint | undefined;
                        const isSelected = payload?.molecule.id === selectedId;
                        const isLead = Boolean(
                          highlightLead &&
                          campaign?.lead_molecule_id &&
                          payload?.molecule.id === campaign.lead_molecule_id
                        );
                        const color = qcColor(payload?.qc);
                        const size = isLead ? 10 : 6;
                        return (
                          <g>
                            {isSelected ? (
                              <circle
                                cx={props.cx}
                                cy={props.cy}
                                r={isLead ? 15 : 11}
                                fill="none"
                                stroke={color}
                                strokeOpacity={0.5}
                                strokeWidth={2}
                              />
                            ) : null}
                            {isLead ? (
                              <polygon
                                points={starPoints(props.cx, props.cy, size, size * 0.45)}
                                fill={color}
                                stroke="#0f172a"
                                strokeWidth={1.8}
                                style={{ cursor: "pointer" }}
                              />
                            ) : (
                              <circle
                                cx={props.cx}
                                cy={props.cy}
                                r={isSelected ? 7 : 6}
                                fill={color}
                                stroke="#ffffff"
                                strokeWidth={1.5}
                                style={{ cursor: "pointer", transition: "r 120ms ease-out" }}
                              />
                            )}
                          </g>
                        );
                      }}
                      onClick={(point: any) => {
                        const payload = point?.payload as PlotPoint | undefined;
                        if (payload?.molecule?.id) {
                          setSelectedId(payload.molecule.id);
                        }
                      }}
                    />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            )}

            <div className={styles.sarLegend}>
              <span>
                <i style={{ background: QC_COLORS.pass }} />
                Pass
              </span>
              <span>
                <i style={{ background: QC_COLORS.warn }} />
                Warn
              </span>
              <span>
                <i style={{ background: QC_COLORS.fail }} />
                Fail
              </span>
              <span>
                <i style={{ background: QC_COLORS.unknown }} />
                Unknown
              </span>
            </div>
          </Card>

          <Card className={styles.sarSidePanel}>
            {selectedPoint ? (
              <SelectedMoleculePanel
                point={selectedPoint}
                xMetric={xMetric}
                yMetric={yMetric}
                onClose={() => setSelectedId(null)}
                onOpen={() => navigate(withOrg(`/molecules/${selectedPoint.molecule.id}`, orgId))}
                orgId={orgId}
              />
            ) : (
              <div className={styles.sidePanelEmpty}>
                <div className={styles.sidePanelEmptyIcon}>○</div>
                <div>
                  <strong>Click a point</strong>
                  <p>Pin a molecule here to view its metrics and jump to its detail page.</p>
                </div>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function starPoints(cx: number, cy: number, outer: number, inner: number) {
  const points: string[] = [];
  for (let i = 0; i < 10; i += 1) {
    const radius = i % 2 === 0 ? outer : inner;
    const angle = -Math.PI / 2 + (i * Math.PI) / 5;
    points.push(`${cx + Math.cos(angle) * radius},${cy + Math.sin(angle) * radius}`);
  }
  return points.join(" ");
}

function SelectedMoleculePanel({
  point,
  xMetric,
  yMetric,
  onClose,
  onOpen,
  orgId
}: {
  point: PlotPoint;
  xMetric: string;
  yMetric: string;
  onClose: () => void;
  onOpen: () => void;
  orgId: string;
}) {
  const allMetrics = Object.entries(point.molecule.metrics || {}).sort(([a], [b]) =>
    a.localeCompare(b)
  );
  return (
    <div className={styles.sidePanel}>
      <div className={styles.sidePanelHeader}>
        <div>
          <p className={styles.sidePanelEyebrow}>Selected molecule</p>
          <h2>{displayName(point.molecule)}</h2>
        </div>
        <button
          type="button"
          className={styles.iconButton}
          onClick={onClose}
          aria-label="Clear selection"
        >
          ×
        </button>
      </div>

      <div className={styles.sidePanelRow}>
        <StatusBadge status={point.qc} />
        {point.molecule.external_id ? (
          <span className={styles.muted}>{point.molecule.external_id}</span>
        ) : null}
      </div>

      <img
        className={styles.sidePanelStructure}
        src={api.moleculeSvgUrl(point.molecule.id, orgId)}
        alt={`Structure for ${displayName(point.molecule)}`}
      />

      <code className={styles.sidePanelSmiles}>
        {point.molecule.smiles || point.molecule.canonical_smiles}
      </code>

      <div className={styles.sidePanelMetrics}>
        {allMetrics.map(([name, value]) => {
          const highlight = name === xMetric || name === yMetric;
          return (
            <div
              key={name}
              className={`${styles.sidePanelMetric} ${highlight ? styles.sidePanelMetricActive : ""}`}
            >
              <span>{metricLabel(name)}</span>
              <strong>{formatValue(value)}</strong>
            </div>
          );
        })}
        {allMetrics.length === 0 ? (
          <div className={styles.muted}>No metrics recorded.</div>
        ) : null}
      </div>

      <button type="button" className={styles.primaryButton} onClick={onOpen}>
        View full record →
      </button>
    </div>
  );
}
