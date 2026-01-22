"""
Unit tests for the QC module.
"""

import pytest
import numpy as np
from qc import (
    qc_summary,
    _safe_float,
    _compute_stats,
    _check_zscore_anomalies,
    _check_drift,
    _check_monotonicity,
    _check_completeness,
    _check_range,
    QCStatus,
    AnomalyType,
)


class TestSafeFloat:
    """Tests for _safe_float helper."""

    def test_valid_float(self):
        assert _safe_float(3.14) == 3.14
        assert _safe_float(42) == 42.0
        assert _safe_float("3.14") == 3.14

    def test_none(self):
        assert _safe_float(None) is None

    def test_nan(self):
        assert _safe_float(float('nan')) is None

    def test_inf(self):
        assert _safe_float(float('inf')) is None
        assert _safe_float(float('-inf')) is None

    def test_invalid_string(self):
        assert _safe_float("not a number") is None
        assert _safe_float("") is None


class TestComputeStats:
    """Tests for _compute_stats helper."""

    def test_basic_stats(self):
        values = [1, 2, 3, 4, 5]
        stats = _compute_stats(values)
        assert stats["mean"] == 3.0
        assert stats["n"] == 5
        assert stats["n_valid"] == 5
        assert stats["null_pct"] == 0.0

    def test_with_nulls(self):
        values = [1, 2, None, 4, 5]
        stats = _compute_stats(values)
        assert stats["mean"] == 3.0
        assert stats["n"] == 5
        assert stats["n_valid"] == 4
        assert stats["null_pct"] == 20.0

    def test_empty_values(self):
        stats = _compute_stats([])
        assert stats["mean"] is None
        assert stats["n"] == 0

    def test_all_nulls(self):
        stats = _compute_stats([None, None, None])
        assert stats["mean"] is None
        assert stats["n"] == 3
        assert stats["n_valid"] == 0
        assert stats["null_pct"] == 100.0


class TestZScoreAnomalies:
    """Tests for z-score anomaly detection."""

    def test_no_anomalies(self):
        values = [10, 11, 10, 11, 10]
        anomalies = _check_zscore_anomalies(values, mean=10.4, std=0.5, threshold=3.0)
        assert len(anomalies) == 0

    def test_detects_outlier(self):
        # Create clear outlier: 19 values at 10, one at 100
        # With 20 values, std is small enough that 100 exceeds 3 std
        values = [10] * 19 + [100]
        mean = np.mean(values)
        std = np.std(values, ddof=1)
        anomalies = _check_zscore_anomalies(values, mean=mean, std=std, threshold=3.0)
        assert len(anomalies) == 1
        assert anomalies[0]["type"] == AnomalyType.ZSCORE.value
        assert anomalies[0]["details"]["index"] == 19
        assert anomalies[0]["details"]["value"] == 100

    def test_zero_std_no_crash(self):
        values = [5, 5, 5, 5, 5]
        anomalies = _check_zscore_anomalies(values, mean=5.0, std=0.0, threshold=3.0)
        assert len(anomalies) == 0

    def test_skips_nulls(self):
        values = [10, None, 10, None, 100]
        anomalies = _check_zscore_anomalies(values, mean=40, std=10, threshold=3.0)
        # Should process only non-null values
        assert all(a["details"]["value"] is not None for a in anomalies)


class TestDriftDetection:
    """Tests for historical drift detection."""

    def test_no_drift(self):
        result = _check_drift(
            current_mean=50.0,
            current_std=5.0,
            current_n=100,
            historical={"mean": 50.0, "std": 5.0, "n_samples": 1000},
            threshold_std=2.0,
        )
        assert result is None

    def test_detects_positive_drift(self):
        result = _check_drift(
            current_mean=60.0,  # Significantly higher
            current_std=5.0,
            current_n=100,
            historical={"mean": 50.0, "std": 5.0, "n_samples": 1000},
            threshold_std=2.0,
        )
        assert result is not None
        assert result["type"] == AnomalyType.DRIFT.value
        assert result["details"]["direction"] == "increase"
        assert result["details"]["drift_magnitude"] == 10.0

    def test_detects_negative_drift(self):
        result = _check_drift(
            current_mean=40.0,  # Significantly lower
            current_std=5.0,
            current_n=100,
            historical={"mean": 50.0, "std": 5.0, "n_samples": 1000},
            threshold_std=2.0,
        )
        assert result is not None
        assert result["details"]["direction"] == "decrease"

    def test_handles_missing_historical(self):
        result = _check_drift(
            current_mean=50.0,
            current_std=5.0,
            current_n=100,
            historical={},
            threshold_std=2.0,
        )
        assert result is None

    def test_handles_zero_historical_std(self):
        result = _check_drift(
            current_mean=50.0,
            current_std=5.0,
            current_n=100,
            historical={"mean": 50.0, "std": 0.0, "n_samples": 1000},
            threshold_std=2.0,
        )
        assert result is None


class TestMonotonicity:
    """Tests for monotonicity and discontinuity detection."""

    def test_strictly_increasing_no_issues(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        anomalies = _check_monotonicity(values, expected_direction="increasing")
        # Filter out discontinuity checks, just look for monotonicity violations
        mono_anomalies = [a for a in anomalies if a["type"] == AnomalyType.MONOTONICITY.value]
        assert len(mono_anomalies) == 0

    def test_detects_reversal_in_increasing(self):
        values = [1, 2, 3, 2, 5, 6, 7, 8, 9, 10]  # 3 -> 2 is a reversal
        anomalies = _check_monotonicity(values, expected_direction="increasing")
        mono_anomalies = [a for a in anomalies if a["type"] == AnomalyType.MONOTONICITY.value]
        assert len(mono_anomalies) == 1
        assert mono_anomalies[0]["details"]["index"] == 3

    def test_detects_discontinuity(self):
        values = [1, 2, 3, 4, 100, 101, 102, 103, 104, 105]  # Jump from 4 to 100
        anomalies = _check_monotonicity(values, expected_direction="increasing")
        disc_anomalies = [a for a in anomalies if a["type"] == AnomalyType.DISCONTINUITY.value]
        assert len(disc_anomalies) >= 1
        # The jump should be flagged
        assert any(a["details"]["index"] == 4 for a in disc_anomalies)

    def test_auto_detect_increasing(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        anomalies = _check_monotonicity(values, expected_direction=None)
        mono_anomalies = [a for a in anomalies if a["type"] == AnomalyType.MONOTONICITY.value]
        assert len(mono_anomalies) == 0

    def test_auto_detect_decreasing(self):
        values = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        anomalies = _check_monotonicity(values, expected_direction=None)
        mono_anomalies = [a for a in anomalies if a["type"] == AnomalyType.MONOTONICITY.value]
        assert len(mono_anomalies) == 0

    def test_too_few_values(self):
        values = [1, 2]
        anomalies = _check_monotonicity(values)
        assert len(anomalies) == 0


class TestCompleteness:
    """Tests for completeness checks."""

    def test_sufficient_data(self):
        values = [1, 2, 3, 4, 5]
        anomalies = _check_completeness(values, null_pct=0.0, expected_n=5)
        assert len(anomalies) == 0

    def test_high_null_rate(self):
        values = [1, 2, 3, 4, 5]
        anomalies = _check_completeness(values, null_pct=15.0, expected_n=None)
        assert len(anomalies) == 1
        assert anomalies[0]["type"] == AnomalyType.MISSING.value
        assert anomalies[0]["details"]["issue"] == "high_null_rate"

    def test_insufficient_data_points(self):
        values = [1, 2, 3]
        anomalies = _check_completeness(values, null_pct=0.0, expected_n=10)
        assert len(anomalies) == 1
        assert anomalies[0]["details"]["issue"] == "insufficient_data_points"
        assert anomalies[0]["details"]["missing_count"] == 7

    def test_both_issues(self):
        values = [1, 2, 3]
        anomalies = _check_completeness(values, null_pct=20.0, expected_n=10)
        assert len(anomalies) == 2


class TestRangeValidation:
    """Tests for range validation."""

    def test_within_range(self):
        values = [0.5, 1.0, 1.5, 2.0]
        anomalies = _check_range(values, {"min": 0.0, "max": 3.0})
        assert len(anomalies) == 0

    def test_below_range(self):
        values = [0.5, -1.0, 1.5, 2.0]  # -1.0 is below min
        anomalies = _check_range(values, {"min": 0.0, "max": 3.0})
        assert len(anomalies) == 1
        assert anomalies[0]["details"]["issue"] == "below_range"
        assert anomalies[0]["details"]["value"] == -1.0

    def test_above_range(self):
        values = [0.5, 1.0, 1.5, 5.0]  # 5.0 is above max
        anomalies = _check_range(values, {"min": 0.0, "max": 3.0})
        assert len(anomalies) == 1
        assert anomalies[0]["details"]["issue"] == "above_range"

    def test_min_only(self):
        values = [-5, 0, 5, 10]
        anomalies = _check_range(values, {"min": 0})
        assert len(anomalies) == 1
        assert anomalies[0]["details"]["value"] == -5

    def test_max_only(self):
        values = [0, 5, 10, 100]
        anomalies = _check_range(values, {"max": 50})
        assert len(anomalies) == 1
        assert anomalies[0]["details"]["value"] == 100


class TestQCSummary:
    """Integration tests for qc_summary."""

    def test_all_pass(self):
        stats = {
            "temperature": {
                "values": [25.0, 25.1, 24.9, 25.0, 25.2, 24.8, 25.1, 25.0],
            }
        }
        result = qc_summary(stats)

        assert result["overall_status"] == "pass"
        assert "temperature" in result["qc_flags"]
        assert result["qc_flags"]["temperature"]["status"] == "pass"
        assert "All QC checks passed" in result["summary"]

    def test_zscore_warning(self):
        # Create data with one outlier
        values = [10.0] * 19 + [100.0]  # 100 is an outlier
        stats = {"measurement": {"values": values}}
        result = qc_summary(stats)

        assert result["overall_status"] == "warn"
        assert result["qc_flags"]["measurement"]["status"] == "warn"
        anomalies = result["qc_flags"]["measurement"]["anomalies"]
        assert any(a["type"] == "zscore" for a in anomalies)

    def test_drift_detection(self):
        stats = {
            "temperature": {
                "values": [30.0, 30.1, 29.9, 30.0],  # Current batch around 30
            }
        }
        historical = {
            "temperature": {"mean": 25.0, "std": 0.5, "n_samples": 1000}
        }
        result = qc_summary(stats, historical_baselines=historical)

        assert result["overall_status"] == "fail"
        assert result["qc_flags"]["temperature"]["status"] == "fail"
        anomalies = result["qc_flags"]["temperature"]["anomalies"]
        assert any(a["type"] == "drift" for a in anomalies)

    def test_range_violation(self):
        stats = {
            "ph": {"values": [7.0, 7.1, 6.9, 15.0]}  # 15.0 is outside pH range
        }
        expected_ranges = {"ph": {"min": 0, "max": 14}}
        result = qc_summary(stats, expected_ranges=expected_ranges)

        assert result["overall_status"] == "warn"
        anomalies = result["qc_flags"]["ph"]["anomalies"]
        assert any(a["type"] == "range" for a in anomalies)

    def test_missing_data_fail(self):
        # More than 10% nulls should trigger missing data check
        values = [1.0, None, None, 4.0, None, 6.0, None, 8.0]  # 50% null
        stats = {"concentration": {"values": values}}
        result = qc_summary(stats)

        assert result["overall_status"] == "fail"
        anomalies = result["qc_flags"]["concentration"]["anomalies"]
        assert any(a["type"] == "missing" for a in anomalies)

    def test_expected_count_check(self):
        stats = {"readings": {"values": [1, 2, 3]}}
        expected_counts = {"readings": 100}
        result = qc_summary(stats, expected_counts=expected_counts)

        assert result["overall_status"] == "fail"
        anomalies = result["qc_flags"]["readings"]["anomalies"]
        missing_anomalies = [a for a in anomalies if a["type"] == "missing"]
        assert any(a["details"]["issue"] == "insufficient_data_points" for a in missing_anomalies)

    def test_monotonicity_with_explicit_direction(self):
        stats = {
            "time_series": {
                "values": [1, 2, 3, 2, 5, 6, 7, 8, 9, 10]  # Reversal at index 3
            }
        }
        monotonic_fields = {"time_series": "increasing"}
        result = qc_summary(stats, monotonic_fields=monotonic_fields)

        anomalies = result["qc_flags"]["time_series"]["anomalies"]
        mono_anomalies = [a for a in anomalies if a["type"] == "monotonicity"]
        assert len(mono_anomalies) > 0

    def test_multiple_fields(self):
        stats = {
            "temperature": {"values": [25.0, 25.1, 24.9, 25.0]},  # OK
            # 19 values at 100, one extreme outlier at 1000 -> z-score > 3
            "pressure": {"values": [100] * 19 + [1000]},
        }
        result = qc_summary(stats)

        assert result["qc_flags"]["temperature"]["status"] == "pass"
        assert result["qc_flags"]["pressure"]["status"] == "warn"
        assert result["overall_status"] == "warn"

    def test_custom_thresholds(self):
        values = [10.0] * 9 + [15.0]  # 15 is 1.5 std away
        stats = {"measurement": {"values": values}}

        # With default threshold (3.0), this should pass
        result_default = qc_summary(stats, zscore_threshold=3.0)
        # With lower threshold (1.0), this should flag
        result_strict = qc_summary(stats, zscore_threshold=1.0)

        # Strictness should matter
        assert len(result_strict["qc_flags"]["measurement"]["anomalies"]) >= len(
            result_default["qc_flags"]["measurement"]["anomalies"]
        )

    def test_empty_stats(self):
        result = qc_summary({})
        assert result["overall_status"] == "pass"
        assert result["qc_flags"] == {}

    def test_provides_stats_summary(self):
        stats = {"temp": {"values": [1, 2, 3, 4, 5]}}
        result = qc_summary(stats)

        field_stats = result["qc_flags"]["temp"]["stats"]
        assert field_stats["mean"] == 3.0
        assert field_stats["n"] == 5
        assert field_stats["null_pct"] == 0.0
        assert "std" in field_stats


class TestEdgeCases:
    """Edge case handling tests."""

    def test_single_value(self):
        stats = {"temp": {"values": [25.0]}}
        result = qc_summary(stats)
        # Should not crash, std will be 0
        assert result["qc_flags"]["temp"]["stats"]["n"] == 1

    def test_all_same_values(self):
        stats = {"temp": {"values": [25.0, 25.0, 25.0, 25.0, 25.0]}}
        result = qc_summary(stats)
        # std should be 0, no anomalies expected
        assert result["overall_status"] == "pass"

    def test_mixed_types_in_values(self):
        stats = {"mixed": {"values": [1, "2", 3.0, "invalid", None, 5]}}
        result = qc_summary(stats)
        # Should handle gracefully
        assert "mixed" in result["qc_flags"]

    def test_precomputed_stats_used(self):
        stats = {
            "temp": {
                "values": [1, 2, 3, 4, 5],
                "mean": 100.0,  # Deliberately wrong
                "std": 50.0,
            }
        }
        result = qc_summary(stats)
        # Should use provided mean/std
        assert result["qc_flags"]["temp"]["stats"]["mean"] == 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
