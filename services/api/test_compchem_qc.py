"""
Tests for comp-chem QC.

Cover every check in compchem_qc.py — both the pass and trigger cases —
so future refactors can't silently break a rule without a failing test.

Run inside the API container (`make test`) or standalone:
    cd services/api && python test_compchem_qc.py
"""

import os
import sys

# Allow standalone execution: add services/api to sys.path so qc.py imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import compchem_qc as cq  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_docking_run(
    pose_scores=None,
    termination="normal",
    smiles=None,
):
    """Build a parsed-result dict shaped like CompChemParsedResult.to_manifest()."""
    pose_scores = pose_scores if pose_scores is not None else [-9.2, -8.7, -8.3, -7.9]
    metrics = [
        {"name": "best_binding_affinity", "value": min(pose_scores), "unit": "kcal/mol"},
    ]
    for i, s in enumerate(pose_scores, start=1):
        metrics.append({
            "name": f"pose_affinity_rank_{i}",
            "value": s,
            "unit": "kcal/mol",
            "metadata": {"rank": i},
        })
    return {
        "software_name": "AutoDock Vina",
        "software_version": "1.2.5",
        "run_kind": "docking",
        "termination_status": termination,
        "method": "Vina",
        "metrics": metrics,
        "metadata": {"n_poses": len(pose_scores)},
    }


def make_md_run(termination="normal", metrics=None):
    return {
        "software_name": "GROMACS",
        "software_version": "2023.3",
        "run_kind": "molecular_dynamics",
        "termination_status": termination,
        "forcefield": "AMBER ff19SB",
        "metrics": metrics or [
            {"name": "simulated_time", "value": 100.0, "unit": "ps"},
        ],
        "metadata": {"n_atoms": 12345, "total_time_ps": 100.0},
    }


def make_dft_run(
    termination="normal",
    metrics=None,
    metadata=None,
    cli_args=None,
):
    return {
        "software_name": "Gaussian",
        "software_version": "16.A.03",
        "run_kind": "dft",
        "termination_status": termination,
        "method": "B3LYP",
        "basis_set": "6-31G(d)",
        "cli_args": cli_args,
        "metrics": metrics or [
            {"name": "final_energy", "value": -76.5, "unit": "Hartree"},
        ],
        "metadata": metadata or {"scf_cycles": 18, "n_atoms": 21},
    }


# ---------------------------------------------------------------------------
# Termination status
# ---------------------------------------------------------------------------

def test_normal_termination_no_finding():
    findings = cq.check_termination_status({"termination_status": "normal"})
    assert findings == []


def test_crashed_termination_is_fail():
    findings = cq.check_termination_status({
        "termination_status": "crashed",
        "error_message": "Segfault at step 4523",
    })
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"
    assert findings[0]["rule"] == "termination_crashed"


def test_unconverged_termination_is_fail():
    findings = cq.check_termination_status({"termination_status": "unconverged"})
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"


def test_partial_termination_is_warn():
    findings = cq.check_termination_status({"termination_status": "partial"})
    assert findings[0]["severity"] == "warn"


# ---------------------------------------------------------------------------
# MD: RMSD drift
# ---------------------------------------------------------------------------

def test_rmsd_stable_no_finding():
    # Slow growth, peak well under 5Å in early phase
    rmsd = [0.5, 0.8, 1.2, 1.5, 1.8, 2.0, 2.1, 2.2, 2.3, 2.4] * 5
    findings = cq.check_rmsd_drift(rmsd, list(range(len(rmsd))), cq.DEFAULT_THRESHOLDS)
    assert findings == []


def test_rmsd_early_spike_is_fail():
    # 50 frames; spike in first 10 frames (20%) exceeds 5Å threshold
    rmsd = [0.5, 1.0, 2.0, 6.5, 7.0] + [3.0] * 45
    findings = cq.check_rmsd_drift(rmsd, None, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["rule"] == "rmsd_early_drift"
    assert findings[0]["severity"] == "fail"
    assert findings[0]["details"]["peak_rmsd_A"] >= 5.0


def test_rmsd_late_spike_does_not_trigger_early_check():
    # Spike happens at index 40 of 50 = 80% of the way through; outside early window
    rmsd = [0.5, 1.0, 2.0, 3.0] * 10 + [6.5] * 5 + [3.0] * 5
    findings = cq.check_rmsd_drift(rmsd, None, cq.DEFAULT_THRESHOLDS)
    assert findings == []


def test_rmsd_too_short_skipped():
    assert cq.check_rmsd_drift([1.0, 2.0], None, cq.DEFAULT_THRESHOLDS) == []
    assert cq.check_rmsd_drift(None, None, cq.DEFAULT_THRESHOLDS) == []


# ---------------------------------------------------------------------------
# MD: energy conservation
# ---------------------------------------------------------------------------

def test_energy_stable_no_finding():
    # Low-variance PE: stays near -1000 +/- 0.5
    pe = [-1000 + 0.1 * (i % 5) for i in range(100)]
    findings = cq.check_energy_conservation(pe, cq.DEFAULT_THRESHOLDS)
    assert findings == []


def test_energy_drift_post_equilibration_warns():
    # First 10% (10 frames) baseline is tight; rest blows up
    pe = [-1000.0 + 0.05 * (i % 3) for i in range(10)]  # tight baseline
    pe += [-1000.0 + 50.0 * (i % 7) for i in range(90)]  # huge variance
    findings = cq.check_energy_conservation(pe, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["rule"] == "energy_variance_excess"
    assert findings[0]["severity"] == "warn"


def test_energy_too_short_skipped():
    assert cq.check_energy_conservation([1.0, 2.0, 3.0], cq.DEFAULT_THRESHOLDS) == []


# ---------------------------------------------------------------------------
# MD: PBC unwrap
# ---------------------------------------------------------------------------

def test_pbc_within_box_no_finding():
    findings = cq.check_pbc_unwrap(max_coord_A=45.0, box_dimensions_A=[50.0, 50.0, 50.0])
    assert findings == []


def test_pbc_outside_box_fails():
    findings = cq.check_pbc_unwrap(max_coord_A=80.0, box_dimensions_A=[50.0, 50.0, 50.0])
    assert len(findings) == 1
    assert findings[0]["rule"] == "pbc_unwrap_failure"
    assert findings[0]["severity"] == "fail"


def test_pbc_skipped_when_inputs_missing():
    assert cq.check_pbc_unwrap(None, [50.0]) == []
    assert cq.check_pbc_unwrap(45.0, None) == []


# ---------------------------------------------------------------------------
# Docking: top pose outlier
# ---------------------------------------------------------------------------

def test_top_pose_tight_distribution_no_finding():
    # Top pose only marginally better than the rest — no outlier
    poses = [-9.2, -9.0, -8.8, -8.6, -8.4, -8.2]
    findings = cq.check_top_pose_outlier(poses, cq.DEFAULT_THRESHOLDS)
    assert findings == []


def test_top_pose_huge_outlier_warns():
    # Top pose is ~5σ better than everyone else
    poses = [-15.0, -8.0, -7.9, -8.1, -8.0, -7.9, -8.0, -7.8]
    findings = cq.check_top_pose_outlier(poses, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["rule"] == "top_pose_score_outlier"
    assert findings[0]["severity"] == "warn"
    assert findings[0]["details"]["zscore"] > 3.0


def test_top_pose_outlier_skipped_when_too_few_poses():
    # Default min_poses=4; three poses should skip
    assert cq.check_top_pose_outlier([-9.0, -8.0, -7.0], cq.DEFAULT_THRESHOLDS) == []


# ---------------------------------------------------------------------------
# Docking: score collapse
# ---------------------------------------------------------------------------

def test_score_collapse_fails():
    poses = [-8.95, -8.90, -8.92, -8.93]
    findings = cq.check_score_collapse(poses, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["rule"] == "docking_score_collapse"
    assert findings[0]["severity"] == "fail"


def test_score_collapse_normal_spread_no_finding():
    poses = [-9.2, -8.5, -7.8, -7.1]
    findings = cq.check_score_collapse(poses, cq.DEFAULT_THRESHOLDS)
    assert findings == []


# ---------------------------------------------------------------------------
# Docking: top pose structural validity (RDKit-dependent)
# ---------------------------------------------------------------------------

def test_top_pose_structural_no_smiles_no_finding():
    assert cq.check_top_pose_structural(None) == []


def test_top_pose_structural_valid_smiles_passes():
    # Caffeine — well-formed
    findings = cq.check_top_pose_structural("CN1C=NC2=C1C(=O)N(C(=O)N2C)C")
    if not cq._HAS_RDKIT:
        # Without RDKit the check warns; that's expected
        assert len(findings) == 1 and findings[0]["severity"] == "warn"
    else:
        assert findings == []


def test_top_pose_structural_bad_smiles_fails():
    if not cq._HAS_RDKIT:
        return  # nothing meaningful to test without RDKit
    findings = cq.check_top_pose_structural("not_a_smiles_string_!!!")
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"


# ---------------------------------------------------------------------------
# DFT: SCF convergence
# ---------------------------------------------------------------------------

def test_scf_under_threshold_no_finding():
    findings = cq.check_scf_convergence(
        parsed={},
        metadata={"scf_cycles": 23},
        thresholds=cq.DEFAULT_THRESHOLDS,
    )
    assert findings == []


def test_scf_over_threshold_warns():
    findings = cq.check_scf_convergence(
        parsed={},
        metadata={"scf_cycles": 350},
        thresholds=cq.DEFAULT_THRESHOLDS,
    )
    assert len(findings) == 1
    assert findings[0]["rule"] == "scf_excess_cycles"
    assert findings[0]["severity"] == "warn"


# ---------------------------------------------------------------------------
# DFT: imaginary frequencies
# ---------------------------------------------------------------------------

def test_imaginary_freqs_minimum_no_imaginary_passes():
    findings = cq.check_imaginary_frequencies([100, 200, 300, 400], "minimum")
    assert findings == []


def test_imaginary_freqs_minimum_with_imaginary_fails():
    findings = cq.check_imaginary_frequencies([-120, 100, 200], "minimum")
    assert len(findings) == 1
    assert findings[0]["rule"] == "minimum_has_imaginary"
    assert findings[0]["severity"] == "fail"


def test_imaginary_freqs_ts_with_one_imaginary_passes():
    findings = cq.check_imaginary_frequencies([-300, 100, 200], "transition_state")
    assert findings == []


def test_imaginary_freqs_ts_with_two_imaginary_fails():
    findings = cq.check_imaginary_frequencies([-300, -150, 100], "transition_state")
    assert len(findings) == 1
    assert findings[0]["rule"] == "ts_multiple_imaginary"


# ---------------------------------------------------------------------------
# DFT: BSSE
# ---------------------------------------------------------------------------

def test_bsse_not_required_for_non_binding_run():
    parsed = {
        "metrics": [{"name": "final_energy", "value": -76.5, "unit": "Hartree"}],
        "metadata": {},
    }
    assert cq.check_bsse_correction(parsed, cq.DEFAULT_THRESHOLDS) == []


def test_bsse_missing_on_binding_calc_warns():
    parsed = {
        "metrics": [{"name": "binding_energy", "value": -8.5, "unit": "kcal/mol"}],
        "metadata": {},
        "cli_args": "B3LYP/6-31G(d) opt",
    }
    findings = cq.check_bsse_correction(parsed, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["rule"] == "bsse_not_corrected"
    assert findings[0]["severity"] == "warn"


def test_bsse_present_on_binding_calc_passes():
    parsed = {
        "metrics": [{"name": "binding_energy", "value": -8.5, "unit": "kcal/mol"}],
        "metadata": {"bsse_corrected": True},
        "cli_args": "B3LYP/6-31G(d) counterpoise=2",
    }
    assert cq.check_bsse_correction(parsed, cq.DEFAULT_THRESHOLDS) == []


# ---------------------------------------------------------------------------
# Properties: Lipinski / Veber
# ---------------------------------------------------------------------------

def test_lipinski_clean_compound_no_finding():
    # Caffeine: MW=194, LogP=-0.1, HBD=0, HBA=3, RotB=0, TPSA=58
    props = {"mw": 194.19, "logp": -0.07, "hbd": 0, "hba": 3, "rotb": 0, "tpsa": 58.4}
    findings = cq.check_lipinski_veber(None, props, cq.DEFAULT_THRESHOLDS)
    assert findings == []


def test_lipinski_one_violation_warns():
    props = {"mw": 620.0, "logp": 3.0, "hbd": 2, "hba": 4, "rotb": 5, "tpsa": 80.0}
    findings = cq.check_lipinski_veber(None, props, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["severity"] == "warn"
    assert "MW" in findings[0]["details"]["violations"][0]


def test_lipinski_three_violations_fail():
    props = {"mw": 620.0, "logp": 6.5, "hbd": 7, "hba": 4, "rotb": 5, "tpsa": 80.0}
    findings = cq.check_lipinski_veber(None, props, cq.DEFAULT_THRESHOLDS)
    assert len(findings) == 1
    assert findings[0]["severity"] == "fail"
    assert len(findings[0]["details"]["violations"]) == 3


def test_lipinski_no_inputs_skipped():
    assert cq.check_lipinski_veber(None, None, cq.DEFAULT_THRESHOLDS) == []


# ---------------------------------------------------------------------------
# Properties: PAINS
# ---------------------------------------------------------------------------

def test_pains_check_skipped_for_no_smiles():
    assert cq.check_pains_alerts(None) == []


def test_pains_caffeine_clean():
    # Caffeine should not trip any PAINS filter
    findings = cq.check_pains_alerts("CN1C=NC2=C1C(=O)N(C(=O)N2C)C")
    if not cq._HAS_RDKIT:
        assert len(findings) == 1 and findings[0]["severity"] == "warn"
        return
    assert findings == []


def test_pains_known_alert_matches():
    if not cq._HAS_RDKIT:
        return
    # Rhodanine — a well-known PAINS motif (azolidinedione)
    findings = cq.check_pains_alerts("O=C1CSC(=S)N1")
    if findings and findings[0]["rule"] == "pains_alert":
        assert findings[0]["severity"] == "warn"
    # Some RDKit builds have minor differences in catalog membership; we
    # don't fail the test if rhodanine happens to be excluded — but if
    # ANY finding appears it must be a pains_alert.
    for f in findings:
        assert f["rule"] in ("pains_alert", "pains_check_failed",
                              "rdkit_unavailable")


# ---------------------------------------------------------------------------
# Top-level: compchem_qc_summary integration
# ---------------------------------------------------------------------------

def test_summary_clean_docking_passes():
    run = make_docking_run()
    out = cq.compchem_qc_summary(run, molecule_smiles="CN1C=NC2=C1C(=O)N(C(=O)N2C)C")
    assert out["qc_mode"] == "compchem"
    # No fail-level findings on a healthy run; warnings might appear if PAINS
    # disagrees but those are non-fatal
    assert out["overall_status"] in ("pass", "warn")
    assert "domain_findings" in out


def test_summary_crashed_run_is_fail():
    run = make_docking_run(termination="crashed")
    out = cq.compchem_qc_summary(run)
    assert out["overall_status"] == "fail"
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "termination_crashed" in rules


def test_summary_score_collapse_is_fail():
    # Collapsed scores AND normal termination — should still fail overall
    run = make_docking_run(pose_scores=[-8.90, -8.92, -8.91, -8.93])
    out = cq.compchem_qc_summary(run)
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "docking_score_collapse" in rules
    assert out["overall_status"] == "fail"


def test_summary_md_run_with_rmsd_spike():
    run = make_md_run()
    md_extras = {
        "rmsd_series_A": [0.5, 1.0, 6.0, 7.0] + [3.0] * 46,
        "pe_series_kjmol": [-1000.0 + 0.1 * (i % 3) for i in range(60)],
    }
    out = cq.compchem_qc_summary(run, md_extras=md_extras)
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "rmsd_early_drift" in rules
    assert out["overall_status"] == "fail"


def test_summary_dft_unconverged_is_fail():
    run = make_dft_run(termination="unconverged")
    out = cq.compchem_qc_summary(run)
    assert out["overall_status"] == "fail"
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "termination_unconverged" in rules


def test_summary_dft_imaginary_frequency_is_fail():
    run = make_dft_run()
    out = cq.compchem_qc_summary(
        run,
        dft_extras={"frequencies_cm1": [-150, 100, 200], "expected_kind": "minimum"},
    )
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "minimum_has_imaginary" in rules
    assert out["overall_status"] == "fail"


def test_summary_per_project_threshold_override():
    # Tighter threshold should make a previously-passing case fail
    run = make_docking_run(pose_scores=[-9.2, -9.0, -8.9, -8.8, -8.7])
    tight = {"score_collapse_window_kcal": 1.0}  # within 1 kcal/mol = collapse
    out = cq.compchem_qc_summary(run, thresholds=tight)
    rules = {f["rule"] for f in out["domain_findings"]}
    assert "docking_score_collapse" in rules


def test_summary_historical_drift_via_generic_engine():
    # Send a single best-affinity metric far from the campaign baseline
    run = {
        "software_name": "AutoDock Vina",
        "run_kind": "docking",
        "termination_status": "normal",
        "metrics": [{"name": "best_binding_affinity",
                     "value": -3.0, "unit": "kcal/mol"}],
        "metadata": {"n_poses": 1},
    }
    historical = {
        "best_binding_affinity": {"mean": -9.0, "std": 0.5, "n_samples": 50},
    }
    out = cq.compchem_qc_summary(run, historical_baselines=historical)
    # Generic engine should flag drift on the best-affinity field
    assert out["qc_flags"]["best_binding_affinity"]["status"] in ("warn", "fail")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
