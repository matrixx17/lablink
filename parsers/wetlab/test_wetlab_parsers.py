"""
Smoke tests for the new wet lab parsers.

Each test writes a minimal synthetic CSV via `tempfile`, asserts that
the corresponding parser detects it and produces sensible
`series_points` / metadata / data_kind. Run with:

    cd /Users/vedantajain/lablink && python3 -m pytest parsers/wetlab/test_wetlab_parsers.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

# Allow `from parsers.wetlab.xxx import ...` when run via pytest from any cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from parsers.wetlab.akta_csv import AktaCsvParser
from parsers.wetlab.canonical_names import (
    canonicalize_parameter,
    derive_unit_for,
    is_time_column,
)
from parsers.wetlab.generic_bioprocess_csv import GenericBioprocessCsvParser
from parsers.wetlab.generic_offline_sample_csv import (
    GenericOfflineSampleCsvParser,
)


# --------------------------------------------------------------------- helpers


def _write_tmp(suffix: str, content: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# --------------------------------------------------------------------- canonicalisation


def test_canonicalize_parameter_handles_units_and_aliases():
    assert canonicalize_parameter("DO [%]") == "do_percent"
    assert canonicalize_parameter("Temp (°C)") == "temperature_c"
    assert canonicalize_parameter("pH [-]") == "ph"
    assert canonicalize_parameter("Viable Cells/mL") == "vcd_e6_per_ml"
    assert canonicalize_parameter("Glucose [g/L]") == "glucose_g_per_l"
    assert canonicalize_parameter("Titer (mg/L)") == "titer_mg_per_l"
    # Non-bioprocess header doesn't match
    assert canonicalize_parameter("Sample Time (h)") is None


def test_is_time_column():
    assert is_time_column("Time (h)") is True
    assert is_time_column("Elapsed Time") is True
    assert is_time_column("DateTime") is True
    assert is_time_column("DO [%]") is False


def test_derive_unit_for_falls_back_to_canonical_default():
    assert derive_unit_for("ph", "pH") == "pH"
    assert derive_unit_for("do_percent", "DO") == "%"
    assert derive_unit_for("temperature_c", "Temp") == "°C"


# --------------------------------------------------------------------- ÄKTA


def test_akta_parser_detects_and_parses_chromatogram():
    csv_body = (
        "Method:,protein_a_capture\n"
        "Column:,MabSelect SuRe\n"
        "Run date:,2024-04-12 10:30\n"
        "UNICORN version:,7.5\n"
        "\n"
        "ml,mAU,%B\n"
        "0.0,0.5,0.0\n"
        "1.0,1.2,0.0\n"
        "2.0,15.3,5.0\n"
        "3.0,250.4,10.0\n"
        "4.0,45.1,20.0\n"
        "\n"
        "Peak Name,Retention Volume (ml),Area,Height\n"
        "Peak 1,3.0,420.1,250.4\n"
    )
    path = _write_tmp(".csv", csv_body)
    try:
        parser = AktaCsvParser()
        assert parser.detect(path) is True

        result = parser.parse(path)
        assert result.instrument == "akta"
        assert result.metadata.get("method") == "protein_a_capture"
        assert result.metadata.get("column") == "MabSelect SuRe"
        assert result.metadata.get("x_axis") == "ml"

        # series_points keyed by ml; should have both UV and %B
        fields = {p["field"] for p in result.series_points}
        assert "uv_absorbance_mau" in fields
        assert "buffer_b_percent" in fields

        # Peak table parsed
        peaks = result.metadata.get("peaks") or []
        assert len(peaks) == 1
        assert peaks[0]["retention_volume_ml"] == 3.0
    finally:
        os.unlink(path)


# --------------------------------------------------------------------- generic bioprocess CSV


def test_generic_bioprocess_csv_detects_and_canonicalises():
    # Header: time + 3 bioprocess parameters with embedded units
    csv_body = (
        "Time (h),pH [-],DO [%],Temp (°C),Agitation (rpm)\n"
        "0.0,7.00,40.5,37.0,200\n"
        "1.0,7.01,40.1,37.0,210\n"
        "2.0,7.00,39.8,37.1,220\n"
        "3.0,6.99,40.2,37.0,230\n"
    )
    path = _write_tmp(".csv", csv_body)
    try:
        parser = GenericBioprocessCsvParser()
        assert parser.detect(path) is True

        result = parser.parse(path)
        assert result.instrument == "generic_bioprocess"
        assert result.data_kind == "continuous"
        # Canonical names land in series_points
        fields = {p["field"] for p in result.series_points}
        assert {"ph", "do_percent", "temperature_c", "agitation_rpm"}.issubset(fields)
        # Units carried through
        units = {p["field"]: p["unit"] for p in result.series_points}
        assert units["ph"] in ("pH", "-")
        assert units["do_percent"] == "%"
        # Time column converted to hours-since-first
        t_values = sorted({p["t"] for p in result.series_points})
        assert t_values[0] == 0.0
        assert t_values[-1] == pytest.approx(3.0)
    finally:
        os.unlink(path)


def test_generic_bioprocess_csv_skips_metadata_preamble():
    csv_body = (
        "Method: fed-batch\n"
        "Operator: demo\n"
        "Date: 2024-04-12\n"
        "\n"
        "Time (h),pH,DO [%]\n"
        "0,7.0,40\n"
        "1,7.0,40\n"
    )
    path = _write_tmp(".csv", csv_body)
    try:
        parser = GenericBioprocessCsvParser()
        # The metadata-preamble heuristic in _find_header_row should still
        # locate the real header at line 4.
        assert parser.detect(path) is True
        result = parser.parse(path)
        assert "ph" in {p["field"] for p in result.series_points}
    finally:
        os.unlink(path)


# --------------------------------------------------------------------- offline samples


def test_offline_wide_csv_detects_and_parses():
    csv_body = (
        "Sample Time (h),VCD (e6/mL),Viability (%),Glucose (g/L),Titer (mg/L)\n"
        "24,2.5,98.0,5.2,5.0\n"
        "48,5.8,97.0,3.8,150.0\n"
        "72,9.2,95.0,2.1,420.0\n"
        "96,14.1,92.0,0.8,820.0\n"
        "120,17.5,88.0,4.5,1240.0\n"
    )
    path = _write_tmp(".csv", csv_body)
    try:
        parser = GenericOfflineSampleCsvParser()
        assert parser.detect(path) is True

        result = parser.parse(path)
        assert result.data_kind == "discrete_offline"
        assert result.metadata.get("shape") == "wide"
        fields = {p["field"] for p in result.series_points}
        assert {
            "vcd_e6_per_ml",
            "viability_percent",
            "glucose_g_per_l",
            "titer_mg_per_l",
        }.issubset(fields)
        # Stats summary populated per canonical field
        assert "vcd_e6_per_ml" in result.raw_stats
        assert result.raw_stats["vcd_e6_per_ml"]["max"] == pytest.approx(17.5)
    finally:
        os.unlink(path)


def test_offline_long_csv_detects_and_parses():
    csv_body = (
        "Sample Time (h),Parameter,Value,Unit\n"
        "24,Viable Cells/mL,2.5,e6/mL\n"
        "24,Viability,98.0,%\n"
        "48,Viable Cells/mL,5.8,e6/mL\n"
        "48,Viability,97.0,%\n"
    )
    path = _write_tmp(".csv", csv_body)
    try:
        parser = GenericOfflineSampleCsvParser()
        assert parser.detect(path) is True

        result = parser.parse(path)
        assert result.metadata.get("shape") == "long"
        # Both canonical fields seen
        fields = {p["field"] for p in result.series_points}
        assert "vcd_e6_per_ml" in fields and "viability_percent" in fields
    finally:
        os.unlink(path)


def test_offline_parser_rejects_long_continuous_files():
    """A 100-row CSV is the controller log, not an offline sample sheet."""
    rows = ["Time (h),VCD (e6/mL),Viability (%)"]
    for i in range(80):
        rows.append(f"{i * 2},1.0,99.0")
    path = _write_tmp(".csv", "\n".join(rows))
    try:
        parser = GenericOfflineSampleCsvParser()
        # Should NOT detect — too many rows for "discrete offline"
        assert parser.detect(path) is False
    finally:
        os.unlink(path)
