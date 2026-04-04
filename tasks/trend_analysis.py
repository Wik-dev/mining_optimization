#!/usr/bin/env python3
"""
Task 5a: Trend Analysis — Rolling Window + Regime Detection
============================================================
Computes per-device trend vectors from historical KPI timeseries data,
detects regime changes via CUSUM, and projects future threshold crossings.

This transforms the controller from reactive ("what's wrong now?") to
predictive ("what will go wrong in Y hours?").

Architecture:
    Pure functions (testable without pipeline):
        compute_linear_trend(), compute_ewma_trend(), detect_regime_change_cusum(),
        project_threshold_crossing(), classify_direction(), analyze_device_trends()

    Thin task wrapper:
        load_history() → analyze_fleet_trends() → write trend_analysis.json

History abstraction:
    load_history() reads kpi_timeseries.parquet in single-pass mode. When the
    continuous simulation loop (Phase 2) adds continuations, only this function
    changes. All analysis functions receive a DataFrame and are origin-agnostic.

Inputs:  kpi_timeseries.parquet, fleet_risk_scores.json
Outputs: trend_analysis.json
Vars:    devices_with_regime_change
"""

import json
import pandas as pd
import numpy as np


# ── Window Definitions ───────────────────────────────────────────────────────
# All windows defined in number of samples at 5-minute intervals.
# 1h=12, 6h=72, 24h=288, 7d=2016 samples.
SAMPLE_INTERVAL_MINUTES = 5

TREND_WINDOWS = {
    "1h":  12,
    "6h":  72,
    "24h": 288,
    "7d":  2016,
}

# Temperature uses a subset — 1h is too noisy for thermal trends.
TEMPERATURE_WINDOWS = {
    "6h":  72,
    "24h": 288,
}

# ── CUSUM Parameters ─────────────────────────────────────────────────────────
# Two-sided CUSUM for detecting mean shifts in TE_score.
# h=5.0 (decision interval) and k=0.5 (allowance/slack) are standard
# Hawkins (1993) defaults for detecting a 1-sigma shift. These give a
# reasonable balance between sensitivity and false alarm rate for mining
# telemetry at 5-min resolution.
CUSUM_H = 8.0   # Decision threshold — alarm when cumulative sum exceeds this
CUSUM_K = 0.5   # Allowance — the "slack" before accumulating deviations

# ── Direction Classification ─────────────────────────────────────────────────
# Based on TE_score slope per hour. TE_score is a ratio (nominal/actual TE),
# so 1.0 = healthy and values < 0.8 indicate degradation. A slope of -0.02/h
# means the device crosses a tier boundary (e.g., 1.0 → 0.8) in ~10 hours.
# Noise floor at ±0.002/h accounts for typical sensor noise + minor load
# fluctuations that don't represent real degradation.
DIRECTION_THRESHOLDS = {
    "falling_fast":    (-float("inf"), -0.02),     # Boundary crossing in <5h
    "declining":       (-0.02,         -0.005),     # Boundary in 10-40h
    "stable":          (-0.005,        +0.005),     # Within noise floor (contiguous)
    "recovering":      (+0.005,        +0.02),      # Improving
    "recovering_fast": (+0.02,         +float("inf")),
}

# ── Projection Thresholds ────────────────────────────────────────────────────
# TE_score thresholds for forward projection. 0.8 = DEGRADED boundary,
# 0.6 = severe degradation (well below minimum operational viability).
PROJECTION_THRESHOLDS = [0.8, 0.6]

# Minimum R² to trust a projection — below this, the linear fit is too noisy
# to extrapolate meaningfully. 0.3 is deliberately permissive; the confidence
# value (= R² itself) lets downstream consumers filter further.
MIN_R2_FOR_PROJECTION = 0.1

# Minimum samples required for a trend calculation to be meaningful.
# With fewer than 6 samples (30 min), statistical noise dominates.
MIN_SAMPLES = 6


# ═════════════════════════════════════════════════════════════════════════════
# Pure Functions — testable without pipeline, Docker, or file I/O
# ═════════════════════════════════════════════════════════════════════════════

def compute_linear_trend(values: np.ndarray) -> dict:
    """Compute OLS linear trend (slope + R²) over a 1-D array.

    Uses numpy's polyfit for efficiency. Slope is in units-per-sample;
    callers convert to per-hour using SAMPLE_INTERVAL_MINUTES.

    Returns:
        {"slope_per_sample": float, "r_squared": float, "n_samples": int}
        Returns slope=0, r²=0 if insufficient data.
    """
    n = len(values)
    if n < MIN_SAMPLES:
        return {"slope_per_sample": 0.0, "r_squared": 0.0, "n_samples": n}

    # Remove NaN
    mask = ~np.isnan(values)
    clean = values[mask]
    if len(clean) < MIN_SAMPLES:
        return {"slope_per_sample": 0.0, "r_squared": 0.0, "n_samples": len(clean)}

    x = np.arange(len(clean), dtype=float)
    coeffs = np.polyfit(x, clean, 1)
    slope = coeffs[0]

    # R² = 1 - SS_res / SS_tot
    y_pred = np.polyval(coeffs, x)
    ss_res = np.sum((clean - y_pred) ** 2)
    ss_tot = np.sum((clean - np.mean(clean)) ** 2)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return {
        "slope_per_sample": float(slope),
        "r_squared": float(max(0.0, r_squared)),
        "n_samples": int(len(clean)),
    }


def compute_ewma_trend(values: np.ndarray, span: int = 12) -> dict:
    """Compute EWMA smoothed trend with slope of the smoothed series.

    EWMA (exponentially weighted moving average) gives more weight to recent
    observations, making it responsive to recent changes while suppressing
    noise. The slope is computed via OLS on the EWMA-smoothed series.

    Args:
        values: Raw time series values.
        span: EWMA span in samples. Default 12 (= 1 hour at 5-min intervals).

    Returns:
        {"slope_per_sample": float, "r_squared": float, "last_ewma": float, "n_samples": int}
    """
    n = len(values)
    if n < MIN_SAMPLES:
        return {"slope_per_sample": 0.0, "r_squared": 0.0, "last_ewma": float("nan"), "n_samples": n}

    series = pd.Series(values)
    ewma = series.ewm(span=span, min_periods=MIN_SAMPLES).mean()
    clean_ewma = ewma.dropna().values

    if len(clean_ewma) < MIN_SAMPLES:
        return {"slope_per_sample": 0.0, "r_squared": 0.0, "last_ewma": float("nan"), "n_samples": len(clean_ewma)}

    trend = compute_linear_trend(clean_ewma)
    return {
        "slope_per_sample": trend["slope_per_sample"],
        "r_squared": trend["r_squared"],
        "last_ewma": float(clean_ewma[-1]),
        "n_samples": int(len(clean_ewma)),
    }


def detect_regime_change_cusum(values: np.ndarray, h: float = CUSUM_H,
                                k: float = CUSUM_K) -> dict:
    """Two-sided CUSUM test for detecting mean shifts.

    Implements Page's (1954) CUSUM algorithm. Tracks cumulative positive and
    negative deviations from the mean; signals a regime change when either
    sum exceeds threshold h.

    The test normalizes by standard deviation so h and k are in sigma units.
    This makes the parameters transferable across different metrics and scales.

    Args:
        values: Time series (full history for the device).
        h: Decision threshold in standard deviations.
        k: Allowance (slack) — deviations smaller than k*sigma don't accumulate.

    Returns:
        {"regime_change": bool, "change_index": int|None, "direction": str,
         "max_cusum_pos": float, "max_cusum_neg": float}
    """
    result = {
        "regime_change": False,
        "change_index": None,
        "direction": "stable",
        "max_cusum_pos": 0.0,
        "max_cusum_neg": 0.0,
    }

    clean = values[~np.isnan(values)]
    if len(clean) < MIN_SAMPLES * 2:
        return result

    # Use first 25% as reference period for estimating process parameters.
    # This avoids contaminating mu/sigma with the change itself, which would
    # make the algorithm detect deviations from a meaningless average.
    ref_n = max(MIN_SAMPLES, len(clean) // 4)
    ref = clean[:ref_n]
    mu = np.mean(ref)
    sigma = np.std(ref, ddof=1)
    if sigma < 1e-10:
        return result

    # Normalize against reference period statistics
    z = (clean - mu) / sigma

    # Two-sided CUSUM
    s_pos = 0.0
    s_neg = 0.0
    max_pos = 0.0
    max_neg = 0.0
    first_alarm_idx = None
    alarm_direction = "stable"

    for i, zi in enumerate(z):
        s_pos = max(0.0, s_pos + zi - k)
        s_neg = max(0.0, s_neg - zi - k)
        max_pos = max(max_pos, s_pos)
        max_neg = max(max_neg, s_neg)

        if first_alarm_idx is None:
            if s_pos > h:
                first_alarm_idx = i
                alarm_direction = "increasing"
            elif s_neg > h:
                first_alarm_idx = i
                alarm_direction = "decreasing"

    result["max_cusum_pos"] = float(max_pos)
    result["max_cusum_neg"] = float(max_neg)

    if first_alarm_idx is not None:
        result["regime_change"] = True
        result["change_index"] = int(first_alarm_idx)
        result["direction"] = alarm_direction

    return result


def project_threshold_crossing(current_value: float, slope_per_hour: float,
                                r_squared: float, threshold: float) -> dict:
    """Project when a metric will cross a threshold via linear extrapolation.

    Only projects forward in time (positive hours). If the slope is moving
    away from the threshold, returns None for hours_to_crossing.

    Confidence = R² of the underlying trend — the caller's trend R².
    Higher R² means the linear model fits well and the projection is reliable.

    Args:
        current_value: Current metric value.
        slope_per_hour: Slope in units per hour (already converted from per-sample).
        r_squared: R² of the trend fit.
        threshold: Target threshold value.

    Returns:
        {"hours_to_crossing": float|None, "confidence": float, "will_cross": bool}
    """
    result = {
        "hours_to_crossing": None,
        "confidence": float(r_squared),
        "will_cross": False,
    }

    if r_squared < MIN_R2_FOR_PROJECTION or abs(slope_per_hour) < 1e-10:
        return result

    # Hours until current_value + slope*hours = threshold
    delta = threshold - current_value
    hours = delta / slope_per_hour

    # Only project forward (positive hours) and only if slope moves toward threshold
    if hours > 0:
        result["hours_to_crossing"] = round(float(hours), 1)
        result["will_cross"] = True

    return result


def classify_direction(slope_per_hour: float) -> str:
    """Classify trend direction based on TE_score slope per hour.

    Direction thresholds are calibrated to TE_score dynamics:
    - TE_score is a ratio (nominal_TE / actual_TE), range ~0.5 to ~1.2
    - A slope of -0.02/h means crossing from 1.0 to 0.8 in 10 hours
    - Noise floor ±0.002 accounts for sensor noise + minor load variation

    Returns one of: falling_fast, declining, stable, recovering, recovering_fast
    """
    for direction, (low, high) in DIRECTION_THRESHOLDS.items():
        if low <= slope_per_hour < high:
            return direction
    return "stable"


def slope_per_sample_to_per_hour(slope_per_sample: float) -> float:
    """Convert slope from per-sample to per-hour units."""
    samples_per_hour = 60.0 / SAMPLE_INTERVAL_MINUTES
    return slope_per_sample * samples_per_hour


def analyze_device_trends(device_df: pd.DataFrame, device_id: str,
                           risk_info: dict | None = None) -> dict:
    """Compute all trend metrics for a single device.

    This is the main per-device analysis function. It orchestrates:
    1. TE_score linear trends across multiple windows
    2. Temperature EWMA trends
    3. Risk trend (from anomaly_prob if available)
    4. CUSUM regime change detection
    5. Forward projections for threshold crossings
    6. Direction classification

    Args:
        device_df: Timeseries for one device, sorted by timestamp.
        device_id: Device identifier.
        risk_info: Optional dict from fleet_risk_scores.json for this device.

    Returns:
        Full trend analysis dict for the device.
    """
    te_scores = device_df["te_score"].values if "te_score" in device_df.columns else np.array([])
    temps = device_df["temperature_c"].values if "temperature_c" in device_df.columns else np.array([])

    # ── Current state ────────────────────────────────────────────────────
    current_te = float(te_scores[-1]) if len(te_scores) > 0 and not np.isnan(te_scores[-1]) else None
    current_temp = float(temps[-1]) if len(temps) > 0 and not np.isnan(temps[-1]) else None

    # ── TE_score trends across windows ───────────────────────────────────
    te_trends = {}
    for window_name, window_samples in TREND_WINDOWS.items():
        window_data = te_scores[-window_samples:] if len(te_scores) >= MIN_SAMPLES else te_scores
        trend = compute_linear_trend(window_data)
        slope_h = slope_per_sample_to_per_hour(trend["slope_per_sample"])
        te_trends[window_name] = {
            "slope_per_hour": round(slope_h, 6),
            "r_squared": round(trend["r_squared"], 4),
            "direction": classify_direction(slope_h),
            "n_samples": trend["n_samples"],
        }

    # ── Temperature trends (EWMA) ───────────────────────────────────────
    # EWMA span = 12 (1 hour) smooths 5-min sensor noise without hiding
    # real thermal drift. Temperature trends use a longer baseline than TE
    # because thermal mass creates slower dynamics.
    temp_trends = {}
    for window_name, window_samples in TEMPERATURE_WINDOWS.items():
        window_data = temps[-window_samples:] if len(temps) >= MIN_SAMPLES else temps
        ewma = compute_ewma_trend(window_data, span=12)
        slope_h = slope_per_sample_to_per_hour(ewma["slope_per_sample"])
        temp_trends[window_name] = {
            "slope_per_hour": round(slope_h, 4),
            "r_squared": round(ewma["r_squared"], 4),
            "last_ewma": round(ewma["last_ewma"], 2) if not np.isnan(ewma["last_ewma"]) else None,
            "n_samples": ewma["n_samples"],
        }

    # ── Risk trend (anomaly_prob if available) ───────────────────────────
    # anomaly_prob is only present when scoring has annotated the timeseries.
    # In single-pass mode (no Phase 2), the KPI parquet doesn't include it,
    # so risk trends are computed from mean_risk in fleet_risk_scores.json.
    risk_trends = {}
    if "anomaly_prob" in device_df.columns:
        risk_values = device_df["anomaly_prob"].values
        for window_name, window_samples in TREND_WINDOWS.items():
            window_data = risk_values[-window_samples:] if len(risk_values) >= MIN_SAMPLES else risk_values
            trend = compute_linear_trend(window_data)
            slope_h = slope_per_sample_to_per_hour(trend["slope_per_sample"])
            risk_trends[window_name] = {
                "slope_per_hour": round(slope_h, 6),
                "r_squared": round(trend["r_squared"], 4),
                "direction": classify_direction(slope_h),
                "n_samples": trend["n_samples"],
            }

    # ── CUSUM regime change detection ────────────────────────────────────
    cusum = detect_regime_change_cusum(te_scores)

    # ── Forward projections ──────────────────────────────────────────────
    # Use the 24h trend as the primary projection basis — it balances
    # recency with enough samples for statistical stability.
    projections = {}
    if current_te is not None and "24h" in te_trends:
        primary_trend = te_trends["24h"]
        for threshold in PROJECTION_THRESHOLDS:
            proj = project_threshold_crossing(
                current_value=current_te,
                slope_per_hour=primary_trend["slope_per_hour"],
                r_squared=primary_trend["r_squared"],
                threshold=threshold,
            )
            projections[str(threshold)] = proj

    # ── Primary direction (from 24h window, most operationally relevant) ─
    primary_direction = te_trends.get("24h", {}).get("direction", "stable")
    primary_slope = te_trends.get("24h", {}).get("slope_per_hour", 0.0)
    primary_r2 = te_trends.get("24h", {}).get("r_squared", 0.0)

    return {
        "device_id": device_id,
        "current_state": {
            "te_score": round(current_te, 4) if current_te is not None else None,
            "temperature_c": round(current_temp, 2) if current_temp is not None else None,
            "mean_risk": risk_info["mean_risk"] if risk_info else None,
        },
        "te_trends": te_trends,
        "temp_trends": temp_trends,
        "risk_trends": risk_trends,
        "regime": {
            "change_detected": cusum["regime_change"],
            "change_index": cusum["change_index"],
            "direction": cusum["direction"],
            "max_cusum_pos": round(cusum["max_cusum_pos"], 2),
            "max_cusum_neg": round(cusum["max_cusum_neg"], 2),
        },
        "projections": projections,
        "primary_direction": primary_direction,
        "primary_slope_per_hour": round(primary_slope, 6),
        "primary_r_squared": round(primary_r2, 4),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Task Wrapper — file I/O + pipeline integration
# ═════════════════════════════════════════════════════════════════════════════

def load_history() -> pd.DataFrame:
    """Load historical KPI timeseries.

    In single-pass mode (current), reads the full kpi_timeseries.parquet.
    When Phase 2 (continuous simulation loop) adds continuations, only this
    function changes — it will concatenate the current parquet with historical
    state from the continuation chain. All downstream analysis functions
    receive a DataFrame and are origin-agnostic.
    """
    df = pd.read_parquet("kpi_timeseries.parquet")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["device_id", "timestamp"])
    return df


def load_risk_scores() -> dict:
    """Load fleet risk scores. Returns empty dict on missing file."""
    try:
        with open("fleet_risk_scores.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def analyze_fleet_trends(df: pd.DataFrame, risk_scores: dict) -> dict:
    """Analyze trends for all devices in the fleet.

    Args:
        df: Full KPI timeseries (all devices, sorted by device_id + timestamp).
        risk_scores: Parsed fleet_risk_scores.json (or empty dict).

    Returns:
        Complete trend analysis dict with per-device results and fleet summary.
    """
    # Build risk lookup
    risk_lookup = {}
    for dr in risk_scores.get("device_risks", []):
        risk_lookup[dr["device_id"]] = dr

    device_results = []
    regime_change_count = 0

    for device_id, device_df in df.groupby("device_id"):
        device_df = device_df.sort_values("timestamp")
        risk_info = risk_lookup.get(device_id)
        result = analyze_device_trends(device_df, device_id, risk_info)
        device_results.append(result)

        if result["regime"]["change_detected"]:
            regime_change_count += 1

    # Fleet summary
    directions = [d["primary_direction"] for d in device_results]
    direction_counts = {}
    for d in directions:
        direction_counts[d] = direction_counts.get(d, 0) + 1

    return {
        "analysis_version": "3.0-trend",
        "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
        "windows": {k: v for k, v in TREND_WINDOWS.items()},
        "cusum_params": {"h": CUSUM_H, "k": CUSUM_K},
        "devices": device_results,
        "fleet_summary": {
            "device_count": len(device_results),
            "regime_changes": regime_change_count,
            "direction_distribution": direction_counts,
        },
    }


def main():
    print("Loading historical KPI data...")
    df = load_history()
    risk_scores = load_risk_scores()

    print(f"Loaded {len(df):,} samples across {df['device_id'].nunique()} devices")
    print(f"Time range: {df['timestamp'].min()} → {df['timestamp'].max()}")

    print("Analyzing fleet trends...")
    result = analyze_fleet_trends(df, risk_scores)

    # ── Print summary ─────────────────────────────────────────────────────
    summary = result["fleet_summary"]
    print(f"\nFleet Trend Summary:")
    print(f"  Devices analyzed: {summary['device_count']}")
    print(f"  Regime changes detected: {summary['regime_changes']}")
    print(f"  Direction distribution: {summary['direction_distribution']}")

    for dev in result["devices"]:
        state = dev["current_state"]
        regime_flag = " ⚠ REGIME CHANGE" if dev["regime"]["change_detected"] else ""
        proj_str = ""
        if "0.8" in dev.get("projections", {}):
            p = dev["projections"]["0.8"]
            if p["will_cross"]:
                proj_str = f"  → crosses 0.8 in {p['hours_to_crossing']:.0f}h (conf={p['confidence']:.2f})"

        te_str = f"{state['te_score']:.3f}" if state['te_score'] is not None else "N/A"
        slope = dev['primary_slope_per_hour'] or 0.0
        r2 = dev['primary_r_squared'] or 0.0
        print(f"  {dev['device_id']}: TE={te_str}  "
              f"dir={dev['primary_direction']}  slope={slope:.4f}/h  "
              f"R²={r2:.3f}{regime_flag}{proj_str}")

    # ── Write outputs ─────────────────────────────────────────────────────
    with open("trend_analysis.json", "w") as f:
        json.dump(result, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "devices_with_regime_change": summary["regime_changes"],
        }, f)

    print(f"\nWrote trend_analysis.json ({len(json.dumps(result)):,} bytes)")


if __name__ == "__main__":
    main()
