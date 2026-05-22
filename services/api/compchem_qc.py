"""
Chemistry-aware QC layered on the generic QC engine.

Translates the same field-level abstractions used by qc.py and
bioprocess_qc.py to the comp-chem domain, then adds chemistry-specific
findings (RMSD drift, SCF convergence, Lipinski violations, PAINS, etc.).

Input shape:
  Comp-chem QC operates on a SINGLE RUN (one docking job, one MD trajectory,
  one DFT calculation, one property table), not a time-series of files. The
  primary input is a CompChemParsedResult-style dict (i.e. parsed.to_manifest()
  from the edge agent) plus optional historical context — baselines computed
  across sibling runs in the same campaign.

Five generic checks are still applied where they make sense:

  - Z-score anomalies          → flag a docking pose whose score is a
                                 statistical outlier vs. other poses in the
                                 same run.
  - Historical drift           → flag if the best score for a new molecule
                                 has drifted from the campaign baseline.
  - Monotonicity / discontinuity → applied to MD potential energy time series
                                  to detect equilibration failures.
  - Completeness               → flag DFT runs with no converged SCF, MD
                                  runs with truncated trajectories.
  - Range validation           → e.g. flag DFT final energies that are
                                  positive (almost always a parser/units bug
                                  for molecules above ~6 atoms).

Domain checks (this file):

  Simulation stability (MD):
    - rmsd_drift              Cα RMSD vs. starting structure (when provided)
    - energy_conservation     PE variance after early equilibration window
    - termination_status      crashed/unconverged runs are not valid points
    - pbc_unwrap              atom coords outside box dimensions

  Docking validity:
    - top_pose_outlier        top score >3 SD below pose mean
    - score_collapse          all poses within 0.1 kcal/mol
    - top_pose_structural     RDKit sanitization of top-pose SMILES

  DFT convergence:
    - scf_convergence         did SCF converge? how many cycles?
    - imaginary_frequencies   minimum should have zero imaginary modes
    - bsse_correction         BSSE flag missing on binding-energy calc

  Property range:
    - lipinski_veber          MW ≤ 500, LogP ≤ 5, HBD ≤ 5, HBA ≤ 10,
                              RotB ≤ 10, TPSA ≤ 140 — configurable per project
    - pains_alerts            PAINS substructures via RDKit FilterCatalog

Every check produces a finding of shape:
    {
      "rule":     "<short_id>",
      "severity": "pass" | "warn" | "fail",
      "message":  "...",
      "details":  { ... }
    }

The overall status is the worst severity across generic + domain findings,
identical to bioprocess_qc's logic.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from qc import qc_summary, QCStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional RDKit — degrade gracefully if absent
# ---------------------------------------------------------------------------

try:
    from rdkit import Chem  # type: ignore
    from rdkit.Chem import AllChem, Descriptors  # type: ignore
    from rdkit.Chem.FilterCatalog import FilterCatalog, FilterCatalogParams  # type: ignore
    _HAS_RDKIT = True
except ImportError:
    _HAS_RDKIT = False
    Chem = None  # type: ignore


# Cached PAINS catalogue — initialised on first use
_PAINS_CATALOG: Optional[Any] = None


def _pains_catalog():
    """Lazy-init and cache RDKit's PAINS FilterCatalog."""
    global _PAINS_CATALOG
    if not _HAS_RDKIT:
        return None
    if _PAINS_CATALOG is None:
        params = FilterCatalogParams()
        params.AddCatalog(FilterCatalogParams.FilterCatalogs.PAINS)
        _PAINS_CATALOG = FilterCatalog(params)
    return _PAINS_CATALOG


# ---------------------------------------------------------------------------
# Default thresholds (override per-project via thresholds= argument)
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: Dict[str, Any] = {
    # MD stability
    "rmsd_drift_A": 5.0,                 # Cα RMSD ceiling, Å
    "rmsd_drift_early_frac": 0.20,       # check within first 20% of trajectory
    "energy_var_sigma": 2.0,             # PE variance vs equilibration window
    "energy_baseline_frac": 0.10,        # first 10% used as equilibration

    # Docking
    "pose_outlier_sigma": 3.0,           # top pose must not exceed 3σ of distribution
    "score_collapse_window_kcal": 0.1,   # all poses within this = failure
    "min_poses_for_outlier_check": 4,    # need at least N poses for statistics

    # DFT
    "scf_max_cycles": 200,
    "require_bsse_for_binding": True,    # warn if missing on binding-energy runs

    # Property range — Lipinski + Veber druglike space
    "lipinski": {
        "mw_max": 500.0,
        "logp_max": 5.0,
        "hbd_max": 5,
        "hba_max": 10,
    },
    "veber": {
        "rotb_max": 10,
        "tpsa_max": 140.0,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(rule: str, severity: str, message: str, **details: Any) -> Dict[str, Any]:
    """Construct a uniform finding record."""
    out: Dict[str, Any] = {
        "rule": rule,
        "severity": severity,
        "message": message,
    }
    if details:
        out["details"] = details
    return out


def _worst_severity(findings: List[Dict[str, Any]]) -> QCStatus:
    sevs = {f.get("severity") for f in findings}
    if "fail" in sevs:
        return QCStatus.FAIL
    if "warn" in sevs:
        return QCStatus.WARN
    return QCStatus.PASS


def _metrics_to_stats(metrics: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Reshape a CompChemParsedResult.metrics list into the {field: {values: [...]}}
    format expected by qc.qc_summary.

    Metrics sharing a name (e.g. 'pose_affinity_rank_1', '..._2') are NOT
    grouped — each is its own field. To run the generic engine over the full
    pose distribution we explicitly synthesise a 'pose_scores' aggregate
    elsewhere in this module.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for m in metrics:
        name = m.get("name")
        val = m.get("value")
        if name is None or val is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        stats.setdefault(name, {"values": [], "unit": m.get("unit")})
        stats[name]["values"].append(v)
    return stats


def _extract_pose_scores(metrics: List[Dict[str, Any]]) -> List[float]:
    """Pull out per-pose docking scores from rank-named metrics."""
    poses: List[float] = []
    for m in metrics:
        name = (m.get("name") or "").lower()
        if name.startswith(("pose_affinity_rank_", "pose_score_rank_")):
            try:
                poses.append(float(m["value"]))
            except (TypeError, ValueError, KeyError):
                continue
    return poses


# ---------------------------------------------------------------------------
# Simulation stability checks (MD)
# ---------------------------------------------------------------------------

def check_termination_status(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A crashed run that produced partial output is not a valid data point."""
    status = (parsed.get("termination_status") or "").lower()
    if status == "normal":
        return []
    if status == "crashed":
        return [_finding(
            "termination_crashed", "fail",
            "Run terminated abnormally — outputs are partial and should not be "
            "treated as a valid data point.",
            error=parsed.get("error_message"),
        )]
    if status == "unconverged":
        return [_finding(
            "termination_unconverged", "fail",
            "Calculation did not converge. Treat results as invalid until "
            "convergence is achieved or the geometry is rebuilt.",
        )]
    if status == "partial":
        return [_finding(
            "termination_partial", "warn",
            "Run produced partial output without a clean termination marker. "
            "Inspect manually before downstream use.",
        )]
    # unknown
    return [_finding(
        "termination_unknown", "warn",
        "Termination status could not be determined from output. Consider "
        "re-running with verbose logging.",
    )]


def check_rmsd_drift(
    rmsd_series: Optional[List[float]],
    time_series: Optional[List[float]],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Cα RMSD vs starting structure. Flag if it exceeds the ceiling within the
    early phase of the simulation — likely indicates poor system preparation
    (bad solvation, wrong forcefield assignment, missing restraints).

    Inputs are optional — if the parser didn't extract an RMSD series we
    simply skip this check.
    """
    if not rmsd_series or len(rmsd_series) < 5:
        return []

    ceiling_A = float(thresholds.get("rmsd_drift_A", 5.0))
    early_frac = float(thresholds.get("rmsd_drift_early_frac", 0.20))

    arr = np.array(rmsd_series, dtype=float)
    n = len(arr)
    early_n = max(2, int(n * early_frac))
    early_window = arr[:early_n]

    peak = float(np.max(early_window))
    if peak <= ceiling_A:
        return []

    peak_idx = int(np.argmax(early_window))
    when = None
    if time_series and len(time_series) >= early_n:
        try:
            when = float(time_series[peak_idx])
        except (TypeError, ValueError):
            pass

    return [_finding(
        "rmsd_early_drift", "fail",
        f"Cα RMSD reached {peak:.2f} Å in the first {early_frac:.0%} of "
        f"simulation time (threshold {ceiling_A:.1f} Å). System is likely "
        f"unstable — check system preparation (solvation, ions, ff assignment).",
        peak_rmsd_A=round(peak, 3),
        threshold_A=ceiling_A,
        early_window_fraction=early_frac,
        time_of_peak=when,
    )]


def check_energy_conservation(
    pe_series: Optional[List[float]],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Energy conservation: in a well-equilibrated NVE simulation the PE
    variance after equilibration should resemble the equilibration-window
    variance. We exclude the first `energy_baseline_frac` of the trajectory
    as equilibration and flag if post-equilibration variance exceeds
    `energy_var_sigma` × baseline std.

    For NVT/NPT runs this check is less stringent but still catches gross
    instability.
    """
    if not pe_series or len(pe_series) < 20:
        return []

    arr = np.array(pe_series, dtype=float)
    baseline_frac = float(thresholds.get("energy_baseline_frac", 0.10))
    n_baseline = max(2, int(len(arr) * baseline_frac))
    baseline = arr[:n_baseline]
    rest = arr[n_baseline:]

    base_std = float(np.std(baseline, ddof=1)) if len(baseline) > 1 else 0.0
    rest_std = float(np.std(rest, ddof=1)) if len(rest) > 1 else 0.0
    sigma_limit = float(thresholds.get("energy_var_sigma", 2.0))

    if base_std <= 0:
        return []

    if rest_std > sigma_limit * base_std:
        return [_finding(
            "energy_variance_excess", "warn",
            f"Potential-energy variance after equilibration "
            f"({rest_std:.3g}) exceeds {sigma_limit}× baseline ({base_std:.3g}). "
            f"Possible drift, instability, or insufficient equilibration.",
            baseline_std=round(base_std, 4),
            post_equilibration_std=round(rest_std, 4),
            sigma_threshold=sigma_limit,
        )]
    return []


def check_pbc_unwrap(
    max_coord_A: Optional[float],
    box_dimensions_A: Optional[List[float]],
) -> List[Dict[str, Any]]:
    """
    PBC artifact: any atom coordinate exceeding box dimensions indicates an
    unwrapping failure (post-processing bug or wrong .gro file used as input).
    """
    if max_coord_A is None or not box_dimensions_A:
        return []
    try:
        box_max = max(float(d) for d in box_dimensions_A)
    except (TypeError, ValueError):
        return []
    if float(max_coord_A) <= box_max * 1.01:  # 1% tolerance for rounding
        return []
    return [_finding(
        "pbc_unwrap_failure", "fail",
        f"Atom coordinate ({float(max_coord_A):.2f} Å) exceeds box dimension "
        f"({box_max:.2f} Å). PBC unwrapping has failed — fix trajectory "
        f"before any geometric analysis.",
        max_coord_A=round(float(max_coord_A), 3),
        box_max_A=round(box_max, 3),
    )]


# ---------------------------------------------------------------------------
# Docking validity checks
# ---------------------------------------------------------------------------

def check_top_pose_outlier(
    pose_scores: List[float],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Top pose >Nσ better than the rest of the pose distribution.

    Uses leave-one-out statistics: a single dramatic outlier in a small
    sample pulls the full-sample mean toward itself and inflates the
    std, so a true outlier may have z < threshold against the full
    sample. The statistically meaningful question is "is the best pose
    far from the OTHER poses' distribution?"
    """
    min_n = int(thresholds.get("min_poses_for_outlier_check", 4))
    if len(pose_scores) < min_n:
        return []
    sigma_limit = float(thresholds.get("pose_outlier_sigma", 3.0))

    arr = np.array(pose_scores, dtype=float)
    top_idx = int(np.argmin(arr))  # most-negative = best in Vina/Glide
    top = float(arr[top_idx])

    rest = np.delete(arr, top_idx)
    if len(rest) < 2:
        return []
    rest_mean = float(np.mean(rest))
    rest_std = float(np.std(rest, ddof=1))
    if rest_std <= 0:
        return []

    z = (rest_mean - top) / rest_std    # positive = top is better than the rest

    if z > sigma_limit:
        return [_finding(
            "top_pose_score_outlier", "warn",
            f"Top pose score ({top:.2f}) is {z:.2f}σ better than the rest of "
            f"the pose distribution (mean {rest_mean:.2f}, σ {rest_std:.2f}). "
            f"Verify manually — large outliers may indicate genuine "
            f"high-affinity binding or a misranked pose.",
            top_score=round(top, 3),
            other_poses_mean=round(rest_mean, 3),
            other_poses_std=round(rest_std, 3),
            zscore=round(z, 2),
            threshold_sigma=sigma_limit,
        )]
    return []


def check_score_collapse(
    pose_scores: List[float],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """All poses within a narrow window = the docker failed to discriminate."""
    if len(pose_scores) < 3:
        return []
    window = float(thresholds.get("score_collapse_window_kcal", 0.1))
    arr = np.array(pose_scores, dtype=float)
    spread = float(np.max(arr) - np.min(arr))
    if spread < window:
        return [_finding(
            "docking_score_collapse", "fail",
            f"All {len(arr)} poses cluster within {spread:.3f} kcal/mol "
            f"(threshold {window} kcal/mol). Docking likely failed to "
            f"explore conformational space — check receptor preparation "
            f"and search exhaustiveness.",
            n_poses=len(arr),
            score_spread_kcal=round(spread, 4),
            window_kcal=window,
        )]
    return []


def check_top_pose_structural(
    top_pose_smiles: Optional[str],
) -> List[Dict[str, Any]]:
    """RDKit sanitization on the top pose — if it fails the pose is corrupt."""
    if not top_pose_smiles:
        return []
    if not _HAS_RDKIT:
        return [_finding(
            "rdkit_unavailable", "warn",
            "RDKit not available — cannot validate top pose structure.",
        )]
    try:
        mol = Chem.MolFromSmiles(top_pose_smiles)
        if mol is None:
            return [_finding(
                "top_pose_unparseable", "fail",
                f"RDKit could not parse top-pose SMILES: {top_pose_smiles!r}",
                smiles=top_pose_smiles,
            )]
        Chem.SanitizeMol(mol)
        return []
    except Exception as e:
        return [_finding(
            "top_pose_sanitize_failed", "fail",
            f"RDKit sanitization failed on top-pose SMILES — pose structure "
            f"is corrupt: {e}",
            smiles=top_pose_smiles,
            error=str(e),
        )]


# ---------------------------------------------------------------------------
# DFT convergence checks
# ---------------------------------------------------------------------------

def check_scf_convergence(
    parsed: Dict[str, Any],
    metadata: Dict[str, Any],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """SCF cycles + geometry convergence."""
    findings: List[Dict[str, Any]] = []
    max_cycles = int(thresholds.get("scf_max_cycles", 200))

    scf_cycles = metadata.get("scf_cycles")
    if isinstance(scf_cycles, (int, float)) and scf_cycles > max_cycles:
        findings.append(_finding(
            "scf_excess_cycles", "warn",
            f"SCF required {int(scf_cycles)} cycles (threshold {max_cycles}). "
            f"Consider tightening initial guess or using different convergence "
            f"acceleration.",
            scf_cycles=int(scf_cycles),
            threshold=max_cycles,
        ))

    # termination_unconverged is handled at the run level (check_termination_status)
    return findings


def check_imaginary_frequencies(
    frequencies_cm1: Optional[List[float]],
    expected_kind: str = "minimum",
) -> List[Dict[str, Any]]:
    """
    For a structure expected to be a minimum, all frequencies must be real
    (positive). One imaginary mode = transition state, not a minimum.
    """
    if not frequencies_cm1:
        return []
    imaginary = [f for f in frequencies_cm1 if isinstance(f, (int, float)) and f < 0]
    if not imaginary:
        return []
    if expected_kind == "transition_state":
        # One imaginary mode is exactly what we want for a TS
        if len(imaginary) == 1:
            return []
        return [_finding(
            "ts_multiple_imaginary", "fail",
            f"Transition state expected exactly 1 imaginary frequency, "
            f"found {len(imaginary)}.",
            imaginary_frequencies_cm1=imaginary,
        )]
    # expected_kind == "minimum"
    return [_finding(
        "minimum_has_imaginary", "fail",
        f"Geometry expected to be a minimum has {len(imaginary)} "
        f"imaginary frequency/frequencies (most negative: "
        f"{min(imaginary):.1f} cm⁻¹). This is a saddle point — reoptimise.",
        imaginary_frequencies_cm1=imaginary,
    )]


def check_bsse_correction(
    parsed: Dict[str, Any],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For binding-energy calculations, BSSE (basis set superposition error)
    correction must be flagged. Detection: any metric named like
    'binding_energy' / 'interaction_energy' / 'delta_e_bind' and no
    counterpoise/BSSE marker in metadata or cli_args.
    """
    if not thresholds.get("require_bsse_for_binding", True):
        return []

    binding_metric_keywords = ("binding_energy", "interaction_energy",
                               "delta_e_bind", "delta_g_bind")
    metrics = parsed.get("metrics") or []
    is_binding = any(
        any(kw in (m.get("name") or "").lower() for kw in binding_metric_keywords)
        for m in metrics
    )
    if not is_binding:
        return []

    cli = (parsed.get("cli_args") or "").lower()
    meta = parsed.get("metadata") or {}
    meta_text = " ".join(str(v).lower() for v in meta.values())
    has_bsse = (
        "counterpoise" in cli or "counterpoise" in meta_text
        or "bsse" in cli or "bsse" in meta_text
        or meta.get("bsse_corrected") is True
    )
    if has_bsse:
        return []
    return [_finding(
        "bsse_not_corrected", "warn",
        "Binding-energy calculation detected with no BSSE / counterpoise "
        "correction marker. Uncorrected binding energies are systematically "
        "too negative — verify before reporting.",
    )]


# ---------------------------------------------------------------------------
# Molecular property range checks
# ---------------------------------------------------------------------------

def check_lipinski_veber(
    smiles: Optional[str],
    properties: Optional[Dict[str, float]],
    thresholds: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Lipinski + Veber rule-of-five-ish checks.

    Two input shapes supported:
      - smiles: RDKit computes the properties on the fly.
      - properties: pre-computed dict from an upstream parser
        (e.g. RDKit property table). Keys: mw, logp, hbd, hba, rotb, tpsa.
    """
    lipinski = thresholds.get("lipinski") or DEFAULT_THRESHOLDS["lipinski"]
    veber = thresholds.get("veber") or DEFAULT_THRESHOLDS["veber"]
    props: Dict[str, float] = {}

    if properties:
        for k, v in properties.items():
            try:
                props[k.lower()] = float(v)
            except (TypeError, ValueError):
                continue
    elif smiles and _HAS_RDKIT:
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return [_finding(
                    "lipinski_unparseable_smiles", "warn",
                    f"Could not parse SMILES for Lipinski/Veber check: {smiles!r}",
                    smiles=smiles,
                )]
            props = {
                "mw": float(Descriptors.MolWt(mol)),
                "logp": float(Descriptors.MolLogP(mol)),
                "hbd": float(Descriptors.NumHDonors(mol)),
                "hba": float(Descriptors.NumHAcceptors(mol)),
                "rotb": float(Descriptors.NumRotatableBonds(mol)),
                "tpsa": float(Descriptors.TPSA(mol)),
            }
        except Exception as e:
            return [_finding(
                "lipinski_compute_failed", "warn",
                f"Could not compute Lipinski/Veber properties: {e}",
                smiles=smiles,
            )]
    else:
        if smiles and not _HAS_RDKIT:
            return [_finding(
                "rdkit_unavailable", "warn",
                "RDKit not available — cannot compute Lipinski/Veber properties.",
            )]
        return []

    violations: List[str] = []
    if "mw" in props and props["mw"] > lipinski["mw_max"]:
        violations.append(f"MW={props['mw']:.1f} > {lipinski['mw_max']}")
    if "logp" in props and props["logp"] > lipinski["logp_max"]:
        violations.append(f"LogP={props['logp']:.2f} > {lipinski['logp_max']}")
    if "hbd" in props and props["hbd"] > lipinski["hbd_max"]:
        violations.append(f"HBD={int(props['hbd'])} > {lipinski['hbd_max']}")
    if "hba" in props and props["hba"] > lipinski["hba_max"]:
        violations.append(f"HBA={int(props['hba'])} > {lipinski['hba_max']}")
    if "rotb" in props and props["rotb"] > veber["rotb_max"]:
        violations.append(f"RotB={int(props['rotb'])} > {veber['rotb_max']}")
    if "tpsa" in props and props["tpsa"] > veber["tpsa_max"]:
        violations.append(f"TPSA={props['tpsa']:.1f} > {veber['tpsa_max']}")

    if not violations:
        return []
    # 1-2 violations = warn (typical lead-opt territory); 3+ = fail (outside
    # druglike space)
    severity = "fail" if len(violations) >= 3 else "warn"
    return [_finding(
        "lipinski_veber_violations", severity,
        f"{len(violations)} Lipinski/Veber violation(s): {'; '.join(violations)}.",
        violations=violations,
        properties=props,
    )]


def check_pains_alerts(smiles: Optional[str]) -> List[Dict[str, Any]]:
    """PAINS substructure detection via RDKit's FilterCatalog."""
    if not smiles:
        return []
    if not _HAS_RDKIT:
        return [_finding(
            "rdkit_unavailable", "warn",
            "RDKit not available — cannot run PAINS filter.",
        )]
    catalog = _pains_catalog()
    if catalog is None:
        return []
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return []  # already covered by lipinski_unparseable_smiles
        matches = catalog.GetMatches(mol)
        if not matches:
            return []
        names = [m.GetDescription() for m in matches]
        return [_finding(
            "pains_alert", "warn",
            f"PAINS substructure(s) matched: {', '.join(names)}. "
            f"These motifs are known to produce assay artefacts; treat "
            f"any positive screening result with extra scepticism.",
            matched_filters=names,
            smiles=smiles,
        )]
    except Exception as e:
        return [_finding(
            "pains_check_failed", "warn",
            f"PAINS check raised: {e}",
            smiles=smiles,
        )]


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def compchem_qc_summary(
    parsed: Dict[str, Any],
    historical_baselines: Optional[Dict[str, Dict[str, Any]]] = None,
    expected_ranges: Optional[Dict[str, Dict[str, float]]] = None,
    molecule_smiles: Optional[str] = None,
    molecule_properties: Optional[Dict[str, float]] = None,
    md_extras: Optional[Dict[str, Any]] = None,
    dft_extras: Optional[Dict[str, Any]] = None,
    thresholds: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run generic QC over a comp-chem parsed result, then apply domain rules.

    Args:
      parsed: a CompChemParsedResult.to_manifest() dict — must contain
              software_name, run_kind, termination_status, metrics, metadata.
      historical_baselines: optional {metric_name: {mean, std, n_samples}}
              for drift detection vs. campaign sibling runs.
      expected_ranges: optional {metric_name: {min, max}}
      molecule_smiles: SMILES for property / PAINS checks (docking & property runs).
      molecule_properties: pre-computed {mw, logp, hbd, hba, rotb, tpsa} —
              wins over computing from SMILES (e.g. from RDKit property table parse).
      md_extras: optional MD-only inputs:
              {
                "rmsd_series_A": [...],     # Cα RMSD vs starting structure, Å
                "rmsd_time_ps": [...],
                "pe_series_kjmol": [...],   # potential energy series
                "max_atom_coord_A": float,  # max(|x|,|y|,|z|) over all atoms/frames
                "box_dimensions_A": [Lx, Ly, Lz],
              }
      dft_extras: optional DFT-only inputs:
              {
                "frequencies_cm1": [...],
                "expected_kind": "minimum" | "transition_state",
              }
      thresholds: per-project override of DEFAULT_THRESHOLDS (deep-merged).
    """
    thresholds = _merge_thresholds(DEFAULT_THRESHOLDS, thresholds or {})
    metrics = parsed.get("metrics") or []
    metadata = parsed.get("metadata") or {}
    run_kind = (parsed.get("run_kind") or "other").lower()

    # ---- Generic engine over the metric values ----
    stats = _metrics_to_stats(metrics)
    # Synthesise an aggregate 'pose_scores' field so the generic z-score check
    # operates on the full pose distribution as one field rather than one
    # per-rank metric.
    pose_scores = _extract_pose_scores(metrics)
    if pose_scores:
        stats["pose_scores"] = {"values": pose_scores, "unit": "kcal/mol"}

    generic = qc_summary(
        stats=stats,
        historical_baselines=historical_baselines,
        expected_ranges=expected_ranges,
    )

    # ---- Domain findings ----
    domain_findings: List[Dict[str, Any]] = []
    domain_findings.extend(check_termination_status(parsed))

    if run_kind in ("molecular_dynamics", "free_energy"):
        domain_findings.extend(_md_findings(md_extras or {}, thresholds))

    if run_kind == "docking":
        domain_findings.extend(check_top_pose_outlier(pose_scores, thresholds))
        domain_findings.extend(check_score_collapse(pose_scores, thresholds))
        domain_findings.extend(check_top_pose_structural(molecule_smiles))

    if run_kind in ("dft", "semi_empirical"):
        domain_findings.extend(check_scf_convergence(parsed, metadata, thresholds))
        domain_findings.extend(check_bsse_correction(parsed, thresholds))
        if dft_extras and "frequencies_cm1" in dft_extras:
            domain_findings.extend(check_imaginary_frequencies(
                dft_extras["frequencies_cm1"],
                dft_extras.get("expected_kind", "minimum"),
            ))

    # Property checks: applicable on docking (the docked ligand), property
    # prediction (the table SMILES is upstream), and generally any run with
    # a molecule_smiles attached.
    if molecule_smiles or molecule_properties:
        domain_findings.extend(check_lipinski_veber(
            molecule_smiles, molecule_properties, thresholds,
        ))
        if molecule_smiles:
            domain_findings.extend(check_pains_alerts(molecule_smiles))

    # ---- Combine into overall status ----
    domain_status = _worst_severity(domain_findings)
    generic_status = QCStatus(generic.get("overall_status", "pass"))
    overall = _combine_status(generic_status, domain_status)

    summary = _build_summary(generic, domain_findings, overall)

    return {
        "qc_mode": "compchem",
        "qc_flags": generic.get("qc_flags", {}),
        "domain_findings": domain_findings,
        "overall_status": overall.value,
        "summary": summary,
        "thresholds_used": thresholds,
    }


def _md_findings(extras: Dict[str, Any], thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    findings.extend(check_rmsd_drift(
        extras.get("rmsd_series_A"),
        extras.get("rmsd_time_ps"),
        thresholds,
    ))
    findings.extend(check_energy_conservation(
        extras.get("pe_series_kjmol"),
        thresholds,
    ))
    findings.extend(check_pbc_unwrap(
        extras.get("max_atom_coord_A"),
        extras.get("box_dimensions_A"),
    ))
    return findings


def _combine_status(a: QCStatus, b: QCStatus) -> QCStatus:
    order = {QCStatus.PASS: 0, QCStatus.WARN: 1, QCStatus.FAIL: 2}
    return a if order[a] >= order[b] else b


def _merge_thresholds(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(defaults)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_thresholds(out[k], v)
        else:
            out[k] = v
    return out


def _build_summary(
    generic: Dict[str, Any],
    domain_findings: List[Dict[str, Any]],
    overall: QCStatus,
) -> str:
    if overall == QCStatus.PASS:
        return "All comp-chem QC checks passed."
    parts: List[str] = []
    if domain_findings:
        worst_rules = [
            f["rule"] for f in domain_findings if f.get("severity") in ("fail", "warn")
        ][:5]
        if worst_rules:
            parts.append("Domain: " + ", ".join(worst_rules))
    if generic.get("qc_flags"):
        affected = [
            field for field, data in generic["qc_flags"].items()
            if data.get("status") != "pass"
        ][:5]
        if affected:
            parts.append("Generic: " + ", ".join(affected))
    word = "warnings" if overall == QCStatus.WARN else "failures"
    return f"Comp-chem QC {word}. " + "; ".join(parts) if parts else f"Comp-chem QC {word}."
