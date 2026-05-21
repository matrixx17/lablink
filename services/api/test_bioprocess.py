"""Tests for bioprocess pivot: parsers, QC, alignment."""

import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parsers import parse_file, detect_format
from bioprocess_qc import bioprocess_qc_summary, check_vcd_growth_profile
from timeseries_align import align_run_series, extract_series_from_stats


FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures")


def test_sartorius_biostat_detection():
    path = os.path.join(FIXTURES, "biostat_run.csv")
    assert detect_format(path) == "sartorius_biostat"


def test_biostat_parse_series_points():
    path = os.path.join(FIXTURES, "biostat_run.csv")
    result = parse_file(path)
    assert result.instrument == "sartorius_biostat"
    assert result.data_kind == "continuous"
    assert len(result.series_points) > 0
    assert "Viable cells" in result.headers or any("viable" in h.lower() for h in result.headers)


def test_offline_titer_parser():
    path = os.path.join(FIXTURES, "offline_titer.csv")
    assert detect_format(path) == "bioprocess_offline"
    result = parse_file(path)
    assert result.data_kind == "discrete_offline"


def test_bioprocess_qc_vcd_crash_rule():
    findings = check_vcd_growth_profile(
        [15, 12, 10, 2, 1, 0.5],
        [0, 12, 24, 36, 48, 60],
    )
    assert any(f["rule"] == "vcd_early_crash" for f in findings)


def test_timeseries_alignment():
    continuous = {
        "do": ([0, 12, 24, 48], [45, 40, 35, 30]),
    }
    discrete = {
        "titer": ([24, 48, 72], [0.1, 0.5, 1.0]),
    }
    aligned = align_run_series(continuous, discrete)
    assert "titer" in str(aligned.get("discrete_fields", []))
    assert len(aligned.get("aligned_fields", [])) >= 1


def test_extract_series_from_stats():
    stats = {
        "Time [h]": {"values": [0, 24, 48]},
        "Titer": {"values": [0.1, 0.5, 1.0]},
    }
    extracted = extract_series_from_stats(stats, time_field="Time [h]")
    assert "Titer" in extracted
