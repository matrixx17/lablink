"""
Smoke tests for comp-chem parsers and the campaign context resolver.

Run with:
    cd /Users/vedantajain/lablink
    python -m pytest parsers/compchem/test_compchem_parsers.py -v

Or quickly without pytest:
    python parsers/compchem/test_compchem_parsers.py
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from parsers.compchem import (  # noqa: E402
    detect_compchem_format,
    parse_compchem_file,
    RunKind,
    TerminationStatus,
)
from edge.campaign_context import CampaignContextResolver, infer_from_path  # noqa: E402


FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "compchem")


def test_vina_pdbqt_detection_and_parse():
    path = os.path.join(
        FIXTURES, "EGFR-program-2026", "lead_opt_round_3", "dock_LL042_out.pdbqt"
    )
    assert os.path.isfile(path), f"fixture missing: {path}"

    fmt = detect_compchem_format(path)
    assert fmt == "autodock_vina", f"expected autodock_vina, got {fmt}"

    result = parse_compchem_file(path)
    assert result.software_name == "AutoDock Vina"
    assert result.run_kind == RunKind.DOCKING
    assert result.termination_status == TerminationStatus.NORMAL
    assert result.metadata.get("n_poses") == 3

    # Best (most negative) affinity should be -9.2
    best = next(m for m in result.metrics if m.name == "best_binding_affinity")
    assert abs(best.value - (-9.2)) < 1e-6
    assert best.unit == "kcal/mol"

    # Per-pose rank metrics with rmsd_lb metadata
    rank_metrics = [m for m in result.metrics if m.name.startswith("pose_affinity_rank_")]
    assert len(rank_metrics) >= 1
    assert rank_metrics[0].metadata is not None
    assert "rmsd_lb_A" in rank_metrics[0].metadata


def test_rdkit_property_table():
    path = os.path.join(
        FIXTURES, "EGFR-program-2026", "lead_opt_round_3", "md_run", "properties.csv"
    )
    assert os.path.isfile(path)

    fmt = detect_compchem_format(path)
    assert fmt == "rdkit_property_table", f"expected rdkit_property_table, got {fmt}"

    result = parse_compchem_file(path)
    assert result.software_name == "RDKit"
    assert result.run_kind == RunKind.PROPERTY_PREDICTION
    assert result.metadata["n_molecules"] == 3
    assert "molwt" in result.metadata["descriptor_columns"]

    mean_mw = next(m for m in result.metrics if m.name == "mean_molwt")
    # Mean of 212.25, 230.24, 246.69 ≈ 229.73
    assert 229.0 < mean_mw.value < 230.5
    assert mean_mw.unit == "g/mol"


def test_campaign_context_resolution():
    watch_root = FIXTURES
    resolver = CampaignContextResolver(watch_root)

    sample_file = os.path.join(
        watch_root, "EGFR-program-2026", "lead_opt_round_3", "dock_LL042_out.pdbqt"
    )
    ctx = resolver.resolve(sample_file)
    assert ctx is not None, "expected to find .lablink.yaml in ancestor dir"
    assert ctx.org_id == "acme-pharma"
    assert ctx.project == "EGFR-program-2026"
    assert ctx.campaign == "lead_opt_round_3"
    assert ctx.molecule_smiles == "Cc1ccc(cc1)C(=O)Nc2ccncc2"
    assert ctx.molecule_name == "LL-042"
    assert ctx.run_defaults.get("software_name") == "AutoDock Vina"

    # Nested file inherits the same context
    nested = os.path.join(
        watch_root, "EGFR-program-2026", "lead_opt_round_3", "md_run", "properties.csv"
    )
    ctx2 = resolver.resolve(nested)
    assert ctx2 is not None
    assert ctx2.campaign == "lead_opt_round_3"


def test_no_context_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        resolver = CampaignContextResolver(tmp)
        stranger = os.path.join(tmp, "stranger.pdb")
        with open(stranger, "w") as f:
            f.write("placeholder\n")
        assert resolver.resolve(stranger) is None


def test_path_inference_standard_path():
    inferred = infer_from_path(
        "/tmp/watch/campaigns/lead_opt_round_3/molecules/LL-042/vina/run_12/dock.log",
        "/tmp/watch",
    )
    assert inferred.campaign_name == "lead_opt_round_3"
    assert inferred.molecule_label == "LL-042"
    assert inferred.run_type == "vina"
    assert inferred.run_index == 12


def test_path_inference_missing_campaign_component():
    inferred = infer_from_path(
        "/tmp/watch/molecules/LL-042/gromacs/run1/md.log",
        "/tmp/watch",
    )
    assert inferred.campaign_name is None
    assert inferred.molecule_label == "LL-042"
    assert inferred.run_type == "gromacs"
    assert inferred.run_index == 1


def test_path_inference_ambiguous_run_type_uses_first_path_match():
    inferred = infer_from_path(
        "/tmp/watch/campaigns/round1/molecules/LL-042/gromacs/vina/run_2/out.log",
        "/tmp/watch",
    )
    assert inferred.run_type == "gromacs"


def test_path_inference_no_recognizable_components():
    inferred = infer_from_path("/tmp/watch/random/output/file.dat", "/tmp/watch")
    assert inferred.campaign_name is None
    assert inferred.molecule_label is None
    assert inferred.run_type is None
    assert inferred.run_index is None
    assert inferred.has_context is False


def test_unknown_file_returns_stub_not_raise():
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        f.write("col_a,col_b\n1,2\n3,4\n")
        path = f.name
    try:
        # Plain 2-column CSV is not RDKit (no descriptor cols, no SMILES)
        assert detect_compchem_format(path) is None
        result = parse_compchem_file(path)
        # Unknown file still returns a result so the agent can upload raw bytes
        assert result.software_name == "unknown"
        assert result.run_kind == RunKind.OTHER
    finally:
        os.unlink(path)


def _run_all():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
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
