"""
Tests for wet lab assay QC checks.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers.wetlab.assay_qc import AssayQCEngine, check_dmso_dilution
from assay_qc import assay_qc_summary


def _dmso_df(final_conc_um: float) -> pd.DataFrame:
    return pd.DataFrame({
        "compound_id": ["CMPD-001"],
        "stock_conc_mM": [10.0],
        "v1_uL": [10.0],
        "final_conc_uM": [final_conc_um],
        "v2_mL": [10.0],
        "concentration_uM": [final_conc_um],
        "response": [50.0],
    })


def test_dmso_dilution_passes_exact_calculation():
    results = check_dmso_dilution(_dmso_df(final_conc_um=10.0))

    assert results == []


def test_dmso_dilution_passes_within_tolerance():
    results = check_dmso_dilution(_dmso_df(final_conc_um=10.49))

    assert results == []


def test_dmso_dilution_fails_outside_tolerance():
    results = check_dmso_dilution(_dmso_df(final_conc_um=12.0))

    assert len(results) == 1
    assert results[0].status == "fail"
    assert results[0].rule == "dmso_dilution"
    assert "DMSO dilution error row 1" in results[0].message


def test_assay_engine_runs_dmso_check_for_dose_response():
    results = AssayQCEngine().run(_dmso_df(final_conc_um=12.0), "dose_response")

    assert any(result.rule == "dmso_dilution" and result.status == "fail" for result in results)


def test_assay_qc_summary_merges_precomputed_findings():
    summary = assay_qc_summary(
        stats={"response": {"values": [1, 2, 3], "mean": 2, "std": 1, "n": 3}},
        assay_format="dose_response",
        precomputed_findings=[{
            "rule": "dmso_dilution",
            "status": "fail",
            "message": "DMSO dilution error row 1: expected 10 uM, got 12 uM (C1V1≠C2V2)",
        }],
    )

    assert summary["qc_mode"] == "assay"
    assert summary["assay_format"] == "dose_response"
    assert summary["overall_status"] == "fail"
    assert summary["domain_findings"][0]["severity"] == "fail"
