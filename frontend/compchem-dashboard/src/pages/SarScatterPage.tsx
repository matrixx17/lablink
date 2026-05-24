import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
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

const CHART_WIDTH = 720;
const CHART_HEIGHT = 420;
const CHART_MARGIN = { top: 28, right: 34, bottom: 62, left: 72 };

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

  const chart = useMemo(() => {
    if (!points.length) return null;
    const xValues = points.map((point) => point.x);
    const yValues = points.map((point) => point.y);
    const makeDomain = (values: number[]) => {
      const min = Math.min(...values);
      const max = Math.max(...values);
      if (min === max) return [min - 1, max + 1] as const;
      const pad = (max - min) * 0.12;
      return [min - pad, max + pad] as const;
    };
    const [xMin, xMax] = makeDomain(xValues);
    const [yMin, yMax] = makeDomain(yValues);
    const plotWidth = CHART_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right;
    const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;
    const xScale = (value: number) =>
      CHART_MARGIN.left + ((value - xMin) / (xMax - xMin)) * plotWidth;
    const yScale = (value: number) =>
      CHART_MARGIN.top + plotHeight - ((value - yMin) / (yMax - yMin)) * plotHeight;
    const ticks = Array.from({ length: 5 }, (_, index) => index / 4);
    return { xMin, xMax, yMin, yMax, plotWidth, plotHeight, xScale, yScale, ticks };
  }, [points]);

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
          <Card className={styles.sarChartCard} data-tour="compchem-sar-scatter">
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
                {chart ? (
                  <svg
                    viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
                    role="img"
                    aria-label={`${metricLabel(xMetric)} versus ${metricLabel(yMetric)} scatter plot`}
                  >
                    <rect
                      x={CHART_MARGIN.left}
                      y={CHART_MARGIN.top}
                      width={chart.plotWidth}
                      height={chart.plotHeight}
                      fill="var(--bg-mute)"
                      stroke="var(--rule)"
                    />
                    {chart.ticks.map((tick) => {
                      const x = CHART_MARGIN.left + tick * chart.plotWidth;
                      const y = CHART_MARGIN.top + tick * chart.plotHeight;
                      const xValue = chart.xMin + tick * (chart.xMax - chart.xMin);
                      const yValue = chart.yMax - tick * (chart.yMax - chart.yMin);
                      return (
                        <g key={tick}>
                          <line x1={x} y1={CHART_MARGIN.top} x2={x} y2={CHART_MARGIN.top + chart.plotHeight} stroke="var(--rule)" />
                          <line x1={CHART_MARGIN.left} y1={y} x2={CHART_MARGIN.left + chart.plotWidth} y2={y} stroke="var(--rule)" />
                          <text x={x} y={CHART_HEIGHT - 28} textAnchor="middle" className={styles.svgAxisTick}>
                            {formatValue(xValue)}
                          </text>
                          <text x={CHART_MARGIN.left - 12} y={y + 4} textAnchor="end" className={styles.svgAxisTick}>
                            {formatValue(yValue)}
                          </text>
                        </g>
                      );
                    })}
                    <text x={CHART_MARGIN.left + chart.plotWidth / 2} y={CHART_HEIGHT - 8} textAnchor="middle" className={styles.svgAxisLabel}>
                      {metricLabel(xMetric)}
                    </text>
                    <text
                      x={18}
                      y={CHART_MARGIN.top + chart.plotHeight / 2}
                      textAnchor="middle"
                      className={styles.svgAxisLabel}
                      transform={`rotate(-90 18 ${CHART_MARGIN.top + chart.plotHeight / 2})`}
                    >
                      {metricLabel(yMetric)}
                    </text>
                    {points.map((point) => {
                      const cx = chart.xScale(point.x);
                      const cy = chart.yScale(point.y);
                      const isSelected = point.molecule.id === selectedId;
                      const isLead = Boolean(
                        highlightLead &&
                        campaign?.lead_molecule_id &&
                        point.molecule.id === campaign.lead_molecule_id
                      );
                      const color = qcColor(point.qc);
                      return (
                        <g
                          key={point.molecule.id}
                          role="button"
                          tabIndex={0}
                          className={styles.svgScatterPoint}
                          onClick={() => setSelectedId(point.molecule.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedId(point.molecule.id);
                            }
                          }}
                        >
                          <title>
                            {displayName(point.molecule)}: {metricLabel(xMetric)} {formatValue(point.x)}, {metricLabel(yMetric)} {formatValue(point.y)}
                          </title>
                          {isSelected ? (
                            <circle cx={cx} cy={cy} r={isLead ? 15 : 11} fill="none" stroke={color} strokeOpacity={0.45} strokeWidth={2} />
                          ) : null}
                          {isLead ? (
                            <polygon points={starPoints(cx, cy, 10, 4.5)} fill={color} stroke="var(--ink)" strokeWidth={1.8} />
                          ) : (
                            <circle cx={cx} cy={cy} r={isSelected ? 7 : 6} fill={color} stroke="#ffffff" strokeWidth={1.5} />
                          )}
                        </g>
                      );
                    })}
                  </svg>
                ) : null}
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
