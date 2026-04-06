"""
Tests for tasks/trend_analysis.py — pure function tests with synthetic data.

No pipeline, Docker, or file I/O — all functions receive DataFrames or arrays.

Run: cd mining_optimization && python -m pytest tests/ -v
"""

import sys
import os
import numpy as np
import pandas as pd
import pytest

# Add parent dir to path so we can import tasks.trend_analysis
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tasks.trend_analysis import (
    compute_linear_trend,
    compute_ewma_trend,
    detect_regime_change_cusum,
    project_threshold_crossing,
    classify_direction,
    analyze_device_trends,
    slope_per_sample_to_per_hour,
    MIN_SAMPLES,
    CUSUM_H,
    CUSUM_K,
)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def make_device_df(te_scores, temperature_c=None, n_samples=None,
                   anomaly_prob=None):
    """Build a minimal device DataFrame for testing."""
    if n_samples is None:
        n_samples = len(te_scores)

    timestamps = pd.date_range("2026-04-01", periods=n_samples, freq="5min")
    data = {
        "timestamp": timestamps,
        "device_id": "TEST-001",
        "te_score": te_scores[:n_samples] if len(te_scores) >= n_samples else te_scores,
    }
    if temperature_c is not None:
        data["temperature_c"] = temperature_c[:n_samples]
    if anomaly_prob is not None:
        data["anomaly_prob"] = anomaly_prob[:n_samples]
    return pd.DataFrame(data)


# ═════════════════════════════════════════════════════════════════════════════
# compute_linear_trend
# ═════════════════════════════════════════════════════════════════════════════

class TestLinearTrend:
    def test_perfect_decline(self):
        """Perfect linear decline: slope should be exact, R²=1.0."""
        values = np.linspace(1.0, 0.5, 100)
        result = compute_linear_trend(values)
        assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)
        assert result["slope_per_sample"] < 0
        assert result["n_samples"] == 100

    def test_perfect_stable(self):
        """Constant values: slope=0, R²=0 (no variance to explain)."""
        values = np.full(100, 0.95)
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] == pytest.approx(0.0, abs=1e-10)
        # R² is 0 when SS_tot = 0 (constant data)
        assert result["r_squared"] == pytest.approx(0.0, abs=1e-6)

    def test_noisy_decline(self):
        """Decline with noise: slope negative, R² between 0 and 1."""
        rng = np.random.default_rng(42)
        values = np.linspace(1.0, 0.7, 200) + rng.normal(0, 0.02, 200)
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] < 0
        assert 0.5 < result["r_squared"] < 1.0
        assert result["n_samples"] == 200

    def test_recovery(self):
        """Upward trend: positive slope."""
        values = np.linspace(0.7, 1.0, 100)
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] > 0
        assert result["r_squared"] == pytest.approx(1.0, abs=1e-6)

    def test_insufficient_data(self):
        """Fewer than MIN_SAMPLES: returns zero slope and R²."""
        values = np.array([1.0, 0.9, 0.8])
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] == 0.0
        assert result["r_squared"] == 0.0
        assert result["n_samples"] == 3

    def test_with_nans(self):
        """NaN values are filtered; trend computed on clean data."""
        values = np.linspace(1.0, 0.5, 20)
        values[5] = np.nan
        values[10] = np.nan
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] < 0
        assert result["n_samples"] == 18

    def test_all_nan(self):
        """All NaN: returns zero slope."""
        values = np.full(10, np.nan)
        result = compute_linear_trend(values)
        assert result["slope_per_sample"] == 0.0
        assert result["r_squared"] == 0.0

    def test_empty_array(self):
        """Empty array: returns zero slope."""
        result = compute_linear_trend(np.array([]))
        assert result["slope_per_sample"] == 0.0
        assert result["n_samples"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# compute_ewma_trend
# ═════════════════════════════════════════════════════════════════════════════

class TestEWMATrend:
    def test_rising_temperature(self):
        """Monotonic rise: EWMA slope should be positive."""
        values = np.linspace(50, 70, 100)
        result = compute_ewma_trend(values, span=12)
        assert result["slope_per_sample"] > 0
        assert result["last_ewma"] > 60

    def test_stable_temperature(self):
        """Constant temperature: slope near zero."""
        values = np.full(100, 55.0)
        result = compute_ewma_trend(values, span=12)
        assert abs(result["slope_per_sample"]) < 1e-6

    def test_insufficient_data(self):
        """Too few samples: returns zero."""
        values = np.array([50, 51, 52])
        result = compute_ewma_trend(values, span=12)
        assert result["slope_per_sample"] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# detect_regime_change_cusum
# ═════════════════════════════════════════════════════════════════════════════

class TestCUSUM:
    def test_stationary_no_alarm(self):
        """Stationary series with low noise: no regime change detected.

        Uses reference period (first 25%) to estimate mu/sigma. With truly
        stationary data and small noise, CUSUM should not alarm.
        """
        rng = np.random.default_rng(42)
        values = 0.95 + rng.normal(0, 0.005, 200)
        result = detect_regime_change_cusum(values)
        assert result["regime_change"] is False
        assert result["change_index"] is None

    def test_step_change_detect(self):
        """Step change from 0.95 to 0.7: CUSUM should detect it.

        Reference period (first 25% = first 100 samples) learns the baseline
        at 0.95. The step down to 0.70 at index 200 is a massive deviation.
        """
        rng = np.random.default_rng(42)
        values = np.concatenate([
            0.95 + rng.normal(0, 0.005, 200),
            0.70 + rng.normal(0, 0.005, 200),
        ])
        result = detect_regime_change_cusum(values)
        assert result["regime_change"] is True
        assert result["change_index"] is not None
        # Detection should happen shortly after the transition at index 200
        assert 195 <= result["change_index"] <= 260

    def test_gradual_drift(self):
        """Slow drift: CUSUM eventually detects it.

        Reference period learns baseline from the first 125 samples (stationary
        with small noise). Then values start drifting down — detected as decreasing.
        Noise is required so sigma > 0 in the reference period.
        """
        rng = np.random.default_rng(99)
        values = np.concatenate([
            0.95 + rng.normal(0, 0.005, 200),
            np.linspace(0.95, 0.5, 300) + rng.normal(0, 0.005, 300),
        ])
        result = detect_regime_change_cusum(values)
        assert result["regime_change"] is True
        assert result["direction"] == "decreasing"

    def test_upward_step(self):
        """Upward step change: direction should be 'increasing'."""
        rng = np.random.default_rng(42)
        values = np.concatenate([
            0.70 + rng.normal(0, 0.005, 200),
            0.95 + rng.normal(0, 0.005, 200),
        ])
        result = detect_regime_change_cusum(values)
        assert result["regime_change"] is True
        assert result["direction"] == "increasing"

    def test_insufficient_data(self):
        """Too few samples: no detection (need at least 2*MIN_SAMPLES)."""
        values = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
        result = detect_regime_change_cusum(values)
        assert result["regime_change"] is False


# ═════════════════════════════════════════════════════════════════════════════
# project_threshold_crossing
# ═════════════════════════════════════════════════════════════════════════════

class TestProjection:
    def test_exact_crossing_time(self):
        """Declining from 1.0 at -0.02/h toward 0.8: should cross in 10h."""
        result = project_threshold_crossing(
            current_value=1.0, slope_per_hour=-0.02,
            r_squared=0.9, threshold=0.8,
        )
        assert result["will_cross"] is True
        assert result["hours_to_crossing"] == pytest.approx(10.0, abs=0.5)
        assert result["confidence"] == pytest.approx(0.9)

    def test_moving_away(self):
        """Value above threshold with positive slope: no crossing."""
        result = project_threshold_crossing(
            current_value=0.95, slope_per_hour=0.01,
            r_squared=0.8, threshold=0.8,
        )
        assert result["will_cross"] is False
        assert result["hours_to_crossing"] is None

    def test_already_below(self):
        """Already below threshold with negative slope: moving further away."""
        result = project_threshold_crossing(
            current_value=0.7, slope_per_hour=-0.01,
            r_squared=0.8, threshold=0.8,
        )
        # Crossing would be in the past (negative hours) — not projected
        assert result["will_cross"] is False

    def test_low_r_squared(self):
        """R² below threshold: don't project."""
        result = project_threshold_crossing(
            current_value=1.0, slope_per_hour=-0.02,
            r_squared=0.05, threshold=0.8,
        )
        assert result["will_cross"] is False

    def test_zero_slope(self):
        """Zero slope: no crossing ever."""
        result = project_threshold_crossing(
            current_value=0.95, slope_per_hour=0.0,
            r_squared=0.9, threshold=0.8,
        )
        assert result["will_cross"] is False

    def test_recovering_toward_threshold_from_below(self):
        """Rising from 0.7 toward 0.8: will cross (recovery)."""
        result = project_threshold_crossing(
            current_value=0.7, slope_per_hour=0.01,
            r_squared=0.8, threshold=0.8,
        )
        assert result["will_cross"] is True
        assert result["hours_to_crossing"] == pytest.approx(10.0, abs=0.5)


# ═════════════════════════════════════════════════════════════════════════════
# classify_direction
# ═════════════════════════════════════════════════════════════════════════════

class TestClassifyDirection:
    def test_falling_fast(self):
        assert classify_direction(-0.03) == "falling_fast"
        assert classify_direction(-0.021) == "falling_fast"

    def test_declining(self):
        assert classify_direction(-0.01) == "declining"
        # -0.02 is lower boundary of declining (inclusive): -0.02 <= slope < -0.005
        assert classify_direction(-0.02) == "declining"

    def test_stable(self):
        assert classify_direction(0.0) == "stable"
        assert classify_direction(0.001) == "stable"
        assert classify_direction(-0.001) == "stable"
        assert classify_direction(-0.004) == "stable"
        assert classify_direction(0.004) == "stable"

    def test_recovering(self):
        assert classify_direction(0.01) == "recovering"
        # +0.005 is lower boundary of recovering (inclusive)
        assert classify_direction(0.005) == "recovering"

    def test_recovering_fast(self):
        assert classify_direction(0.03) == "recovering_fast"
        # +0.02 is lower boundary of recovering_fast (inclusive)
        assert classify_direction(0.02) == "recovering_fast"

    def test_boundaries(self):
        """Boundaries use low <= slope < high, so lower bound is inclusive."""
        # -0.005 is lower bound of stable (inclusive)
        assert classify_direction(-0.005) == "stable"
        # +0.005 is lower bound of recovering (inclusive)
        assert classify_direction(+0.005) == "recovering"
        # Values just inside each range
        assert classify_direction(-0.0049) == "stable"
        assert classify_direction(+0.0049) == "stable"


# ═════════════════════════════════════════════════════════════════════════════
# analyze_device_trends (integration of pure functions)
# ═════════════════════════════════════════════════════════════════════════════

class TestAnalyzeDeviceTrends:
    def test_healthy_stable(self):
        """Healthy device with stable TE_score: no alarms, stable direction.

        Uses stationary data with very small noise (sigma=0.001). The reference
        period captures the baseline and CUSUM should not trigger.
        """
        rng = np.random.default_rng(42)
        te = 0.95 + rng.normal(0, 0.001, 288)
        temp = 55.0 + rng.normal(0, 0.5, 288)
        df = make_device_df(te, temperature_c=temp)

        result = analyze_device_trends(df, "ASIC-001")
        assert result["primary_direction"] == "stable"
        assert result["current_state"]["te_score"] is not None

    def test_healthy_falling(self):
        """Healthy TE_score but declining trend: early warning.

        Decline of 0.25 over 288 samples (24h) = -0.0104/h → "declining".
        """
        te = np.linspace(0.98, 0.73, 288)
        df = make_device_df(te)

        result = analyze_device_trends(df, "ASIC-002")
        assert result["primary_direction"] in ("declining", "falling_fast")
        assert result["primary_slope_per_hour"] < -0.005

    def test_degraded_falling_fast(self):
        """Already degraded and falling fast: urgent action needed.

        Decline of 0.35 over 288 samples (24h) = -0.0146/h → "declining".
        """
        te = np.linspace(0.78, 0.43, 288)
        df = make_device_df(te)

        result = analyze_device_trends(df, "ASIC-003")
        assert result["primary_slope_per_hour"] < -0.005
        assert result["current_state"]["te_score"] < 0.5

    def test_recovering(self):
        """Device recovering from degraded state.

        Rise of 0.25 over 288 samples (24h) = +0.0104/h → "recovering".
        """
        te = np.linspace(0.7, 0.95, 288)
        df = make_device_df(te)

        result = analyze_device_trends(df, "ASIC-004")
        assert result["primary_direction"] in ("recovering", "recovering_fast")
        assert result["primary_slope_per_hour"] > 0.005

    def test_regime_change_step(self):
        """Step change in TE_score triggers CUSUM alarm.

        First half at 0.95 (reference), second half at 0.70 (step down).
        """
        rng = np.random.default_rng(42)
        te = np.concatenate([
            0.95 + rng.normal(0, 0.005, 500),
            0.70 + rng.normal(0, 0.005, 500),
        ])
        df = make_device_df(te)

        result = analyze_device_trends(df, "ASIC-005")
        assert result["regime"]["change_detected"] is True

    def test_minimal_data(self):
        """Only a handful of samples: should not crash, returns stable."""
        te = np.array([0.9, 0.91, 0.89, 0.9, 0.88, 0.9, 0.91])
        df = make_device_df(te)

        result = analyze_device_trends(df, "ASIC-006")
        assert result["device_id"] == "ASIC-006"
        assert result["primary_direction"] == "stable"


# ═════════════════════════════════════════════════════════════════════════════
# Tier integration scenarios (testing how trends should influence tiers)
# These test the pure analysis output that optimize.py will consume.
# ═════════════════════════════════════════════════════════════════════════════

class TestTierIntegrationScenarios:
    """Verify trend outputs match what the controller needs for escalation."""

    def test_scenario_healthy_stable_no_escalation(self):
        """Healthy + stable → no trend-based escalation needed."""
        rng = np.random.default_rng(42)
        te = 0.95 + rng.normal(0, 0.001, 500)
        df = make_device_df(te)
        result = analyze_device_trends(df, "ASIC-001")
        # Controller should NOT escalate
        assert result["primary_direction"] == "stable"
        assert abs(result["primary_slope_per_hour"]) < 0.005

    def test_scenario_healthy_falling_escalate_to_warning(self):
        """Healthy TE but falling → controller should escalate to WARNING."""
        te = np.linspace(0.99, 0.82, 288)
        df = make_device_df(te)
        result = analyze_device_trends(df, "ASIC-002")
        # Slope should indicate declining/falling_fast
        assert result["primary_slope_per_hour"] < -0.005
        # R² should be high enough to trust
        assert result["primary_r_squared"] > 0.3

    def test_scenario_degraded_falling_fast_escalate_to_critical(self):
        """Degraded + falling_fast → controller should escalate toward CRITICAL.

        Steep decline: 0.79 → 0.20 over 24h = -0.0246/h → falling_fast.
        """
        te = np.linspace(0.79, 0.20, 288)
        df = make_device_df(te)
        result = analyze_device_trends(df, "ASIC-003")
        assert result["primary_slope_per_hour"] < -0.02
        assert result["primary_r_squared"] > 0.3

    def test_scenario_recovering_no_deescalation(self):
        """Degraded + recovering → annotate but don't de-escalate (conservative)."""
        te = np.linspace(0.7, 0.88, 288)
        df = make_device_df(te)
        result = analyze_device_trends(df, "ASIC-004")
        assert result["primary_slope_per_hour"] > 0.005
        assert result["primary_direction"] in ("recovering", "recovering_fast")

    def test_scenario_regime_change_healthy_escalate(self):
        """Regime change + currently HEALTHY → controller should escalate to WARNING.

        Both segments need small noise so the CUSUM reference period has sigma > 0.
        """
        rng = np.random.default_rng(77)
        te = np.concatenate([
            0.95 + rng.normal(0, 0.005, 500),
            0.85 + rng.normal(0, 0.005, 500),
        ])
        df = make_device_df(te)
        result = analyze_device_trends(df, "ASIC-005")
        assert result["regime"]["change_detected"] is True


# ═════════════════════════════════════════════════════════════════════════════
# slope_per_sample_to_per_hour conversion
# ═════════════════════════════════════════════════════════════════════════════

class TestSlopeConversion:
    def test_conversion(self):
        """12 samples per hour at 5-min intervals."""
        # slope_per_sample * 12 = slope_per_hour
        assert slope_per_sample_to_per_hour(0.001) == pytest.approx(0.012, abs=1e-6)
        assert slope_per_sample_to_per_hour(-0.001) == pytest.approx(-0.012, abs=1e-6)
        assert slope_per_sample_to_per_hour(0.0) == 0.0
