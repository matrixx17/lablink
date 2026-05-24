"""
Tests for wet lab assay format detection in generic CSV tables.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from parsers.wetlab.assay_format import detect_assay_format
from parsers.wetlab.assay_qc import AssayQCEngine
from parsers.generic_csv import GenericCSVParser
from parsers import detect_format, parse_file


def test_detect_dose_response_format():
    df = pd.DataFrame({
        "compound_id": ["A", "A"],
        "concentration_uM": [0.1, 1.0],
        "response_pct": [10, 55],
    })

    assert detect_assay_format(df, list(df.columns)) == "dose_response"


def test_detect_potency_summary_format():
    df = pd.DataFrame({
        "compound_id": ["A", "B"],
        "IC50_nM": [12, 50],
    })

    assert detect_assay_format(df, list(df.columns)) == "potency_summary"


def test_detect_hplc_purity_format():
    df = pd.DataFrame({
        "compound_id": ["A", "B"],
        "purity": [98.2, 94.5],
        "retention_time": [4.1, 4.8],
    })

    assert detect_assay_format(df, list(df.columns)) == "hplc_purity"


def test_detect_bioprocess_offline_format():
    df = pd.DataFrame({
        "time_h": [0, 24],
        "vcd": [0.8, 6.4],
    })

    assert detect_assay_format(df, list(df.columns)) == "bioprocess_offline"


def test_detect_unknown_format():
    df = pd.DataFrame({
        "foo": [1, 2],
        "bar": [3, 4],
    })

    assert detect_assay_format(df, list(df.columns)) == "unknown"


def test_generic_csv_parser_adds_assay_metadata():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("compound_id,concentration_uM,response\n")
        f.write("A,0.1,5\n")
        f.write("A,1.0,50\n")
        path = f.name

    try:
        result = GenericCSVParser().parse(path)
    finally:
        os.unlink(path)

    assert result.metadata["assay_format"] == "dose_response"
    assert result.metadata["assay_qc"][0]["rule"] == "dose_min_points"


def test_potency_summary_converts_nm_to_um_before_range_check():
    df = pd.DataFrame({
        "compound_id": ["A"],
        "IC50_nM": [50_000],
    })

    results = AssayQCEngine().run(df, "potency_summary")

    assert not any(result.rule == "potency_range" for result in results)


def test_dose_response_replicate_consistency_warns_on_high_cv():
    df = pd.DataFrame({
        "compound_id": ["A", "A", "A", "A"],
        "concentration_uM": [0.1, 0.1, 1.0, 1.0],
        "response": [10.0, 12.0, 100.0, 150.0],
    })

    results = AssayQCEngine().run(df, "dose_response")
    replicate = next(result for result in results if result.rule == "replicate_consistency")

    assert replicate.status == "warn"
    assert round(replicate.value, 1) == 28.3
    assert replicate.message == "High replicate variability at 1 uM: CV = 28.3%"


def test_assay_parser_detects_96_well_plate_metadata_and_control_warning():
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
        f.write("well,concentration_uM,response\n")
        for row in "ABCDEFGH":
            for col in range(1, 13):
                well = f"{row}{col}"
                response = 1.0 if well in {"A1", "A12", "H1", "H12"} else 0.0
                concentration = (ord(row) - ord("A")) * 12 + col
                f.write(f"{well},{concentration},{response}\n")
        path = f.name

    try:
        assert detect_format(path) == "assay_table"
        result = parse_file(path)
    finally:
        os.unlink(path)

    assert result.metadata["assay_format"] == "dose_response"
    assert result.metadata["is_plate_data"] is True
    assert result.metadata["plate_format"] == 96
    assert any(
        finding["rule"] == "plate_control_wells"
        and finding["message"] == "Plate data detected (96 wells). Verify control well assignments."
        for finding in result.metadata["assay_qc"]
    )
