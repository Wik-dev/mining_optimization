#!/usr/bin/env python3
"""
Task 2: Engineer Features
=========================
Computes rolling statistics, rates of change, and cross-device normalization
from raw telemetry. Joins device constants from fleet metadata.

Inputs:  telemetry.parquet, fleet_metadata.json
Outputs: features.parquet
Vars:    feature_count, sample_count
"""

import json
import pandas as pd
import numpy as np

# Rolling window sizes (in samples; 1 sample = 5 min)
# MOS provides hashrate at 5s/5m/30m resolutions. Our 5-min sampling can't replicate
# the 5s granularity, but we approximate the hierarchy with multi-resolution windows.
WINDOW_VERY_SHORT = 6  # 30 min — maps to MOS hashrate_30m smoothed trend
WINDOW_SHORT = 12      # 1 hour
WINDOW_MEDIUM = 144    # 12 hours
WINDOW_LONG = 288      # 24 hours
WINDOW_WEEK = 2016     # 7 days

TELEMETRY_COLS = [
    "temperature_c", "power_w", "hashrate_th",
    "voltage_v", "cooling_power_w", "efficiency_jth",
]

# Columns present before feature engineering (for feature count diff).
# Must match physics_engine.TELEMETRY_COLUMNS (35 cols) so that only
# truly engineered columns are counted as features.
EXPECTED_TELEMETRY_COLS = {
    # Original 17 columns
    "timestamp", "device_id", "model",
    "clock_ghz", "voltage_v", "hashrate_th",
    "power_w", "temperature_c", "cooling_power_w",
    "ambient_temp_c", "energy_price_kwh",
    "operating_mode", "efficiency_jth",
    "label_thermal_deg", "label_psu_instability",
    "label_hashrate_decay", "label_any_anomaly",
    # Extended columns (physics engine v2 — 18 cols)
    "fan_rpm", "fan_rpm_target", "dust_index", "inlet_temp_c",
    "voltage_ripple_mv", "error_code", "reboot_count",
    "chip_count_active", "hashboard_count_active",
    "label_fan_bearing_wear", "label_capacitor_aging",
    "label_dust_fouling", "label_thermal_paste_deg",
    "label_solder_joint_fatigue", "label_coolant_loop_fouling",
    "label_firmware_cliff",
    "operational_state", "economic_margin_usd",
}


def add_device_constants(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Join stock specs from fleet metadata onto telemetry."""
    fleet_df = pd.DataFrame(meta["fleet"])
    fleet_df = fleet_df.rename(columns={
        "stock_clock_ghz": "stock_clock",
        "stock_voltage_v": "stock_voltage",
        "nominal_hashrate_th": "nominal_hashrate",
        "nominal_power_w": "nominal_power",
        "nominal_efficiency_jth": "nominal_efficiency",
        "nominal_chip_count": "nominal_chips",
    })
    # Drop 'model' from fleet_df to avoid collision with telemetry's 'model' column
    fleet_df = fleet_df.drop(columns=["model"], errors="ignore")
    return df.merge(fleet_df, on="device_id", how="left")


def add_rolling_features(group: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling stats per device (applied via groupby)."""
    g = group.sort_values("timestamp").copy()

    for col in TELEMETRY_COLS:
        series = g[col]

        # Short window — recent trend
        roll_s = series.rolling(WINDOW_SHORT, min_periods=1)
        g[f"{col}_mean_1h"] = roll_s.mean()
        g[f"{col}_std_1h"] = roll_s.std().fillna(0)

        # Medium window — half-day baseline
        roll_m = series.rolling(WINDOW_MEDIUM, min_periods=1)
        g[f"{col}_mean_12h"] = roll_m.mean()

        # Long window — daily baseline
        roll_l = series.rolling(WINDOW_LONG, min_periods=1)
        g[f"{col}_mean_24h"] = roll_l.mean()
        g[f"{col}_std_24h"] = roll_l.std().fillna(0)

        # Deviation from 24h baseline (z-score like)
        std_24h = g[f"{col}_std_24h"].replace(0, np.nan)
        g[f"{col}_dev_24h"] = (series - g[f"{col}_mean_24h"]) / std_24h
        g[f"{col}_dev_24h"] = g[f"{col}_dev_24h"].fillna(0)

        # Weekly window — multi-day baseline for detecting gradual degradation.
        # Research: notes_mining_data.md line 42 — "A 3°C rise over a week at
        # constant ambient is a stronger signal than absolute temperature."
        roll_w = series.rolling(WINDOW_WEEK, min_periods=1)
        g[f"{col}_mean_7d"] = roll_w.mean()
        g[f"{col}_std_7d"] = roll_w.std().fillna(0)

        # Deviation from 7d baseline
        std_7d = g[f"{col}_std_7d"].replace(0, np.nan)
        g[f"{col}_dev_7d"] = (series - g[f"{col}_mean_7d"]) / std_7d
        g[f"{col}_dev_7d"] = g[f"{col}_dev_7d"].fillna(0)

    # 30-min hashrate window — approximates MOS hashrate_30m smoothed trend.
    # Only computed for hashrate: this is the field where MOS provides multi-resolution
    # data (5s/5m/30m). Our 5-min sampling interval means the 30m window uses 6 samples,
    # which captures the same smoothing horizon but not the intra-sample volatility.
    hr = g["hashrate_th"]
    roll_vs = hr.rolling(WINDOW_VERY_SHORT, min_periods=1)
    g["hashrate_th_mean_30m"] = roll_vs.mean()
    g["hashrate_th_std_30m"] = roll_vs.std().fillna(0)

    # Voltage ripple 24h std — captures increasing PSU instability before
    # the mean shifts. Research: notes_mining_data.md line 44 — "PSU
    # degradation shows as increasing variance in voltage readings before
    # the mean shifts."
    if "voltage_ripple_mv" in g.columns:
        vr = g["voltage_ripple_mv"]
        g["voltage_ripple_std_24h"] = vr.rolling(WINDOW_LONG, min_periods=1).std().fillna(0)

    return g


def add_rate_of_change(group: pd.DataFrame) -> pd.DataFrame:
    """First-order differences (rate of change per 5-min interval)."""
    g = group.sort_values("timestamp").copy()

    for col in ["temperature_c", "power_w", "hashrate_th", "voltage_v"]:
        g[f"d_{col}"] = g[col].diff().fillna(0)

    # Smoothed rate of change (1h rolling mean of diffs)
    for col in ["temperature_c", "power_w", "hashrate_th", "voltage_v"]:
        g[f"d_{col}_smooth"] = (
            g[f"d_{col}"].rolling(WINDOW_SHORT, min_periods=1).mean()
        )

    return g


def add_cross_device_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-timestamp fleet-relative features (z-scores within same model)."""
    for col in ["temperature_c", "power_w", "hashrate_th", "efficiency_jth"]:
        group_stats = df.groupby(["timestamp", "model"])[col].transform
        mean = group_stats("mean")
        std = group_stats("std").replace(0, np.nan)
        df[f"{col}_fleet_z"] = ((df[col] - mean) / std).fillna(0)

    return df


def add_interaction_features(df: pd.DataFrame) -> pd.DataFrame:
    """Physics-motivated interaction terms."""
    # Power per unit frequency (should be ~constant if voltage is stable)
    df["power_per_ghz"] = df["power_w"] / df["clock_ghz"].replace(0, np.nan)
    df["power_per_ghz"] = df["power_per_ghz"].fillna(0)

    # Thermal headroom: how close to critical temperature (85°C typical limit)
    df["thermal_headroom_c"] = 85.0 - df["temperature_c"]

    # Cooling effectiveness: thermal gradient per watt of cooling
    df["cooling_effectiveness"] = (
        (df["temperature_c"] - df["ambient_temp_c"])
        / df["cooling_power_w"].replace(0, np.nan)
    ).fillna(0)

    # Hash efficiency relative to nominal
    df["hashrate_ratio"] = (
        df["hashrate_th"] / df["nominal_hashrate"].replace(0, np.nan)
    ).fillna(0)

    # Voltage deviation from stock (absolute)
    df["voltage_deviation"] = df["voltage_v"] - df["stock_voltage"]

    # Chip dropout ratio: active chips / nominal chips for this model.
    # Normalizes across models (444-chip S21-HYD vs 342-chip S19 Pro).
    # Research: notes_mining_data.md line 13 — "chip count dropping" is
    # first predictive signal for hashboard failure.
    if "nominal_chips" in df.columns:
        df["chip_dropout_ratio"] = (
            df["chip_count_active"] / df["nominal_chips"].replace(0, np.nan)
        ).fillna(1.0)  # 1.0 = all chips active

    return df


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_parquet("telemetry.parquet")
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # ── Join device constants ────────────────────────────────────────────
    df = add_device_constants(df, meta)

    # ── Per-device rolling features ──────────────────────────────────────
    df = pd.concat([
        add_rolling_features(g) for _, g in df.groupby("device_id")
    ], ignore_index=True)
    df = pd.concat([
        add_rate_of_change(g) for _, g in df.groupby("device_id")
    ], ignore_index=True)

    # ── Cross-device features ────────────────────────────────────────────
    df = add_cross_device_features(df)

    # ── Interaction features ─────────────────────────────────────────────
    df = add_interaction_features(df)

    # ── Clean up ─────────────────────────────────────────────────────────
    df = df.sort_values(["device_id", "timestamp"]).reset_index(drop=True)

    # Raw telemetry columns from ingest + device constants joined in this task
    raw_cols = set(EXPECTED_TELEMETRY_COLS) | {"stock_clock", "stock_voltage",
                                                "nominal_hashrate", "nominal_power",
                                                "nominal_efficiency", "nominal_chips"}
    feature_count = len([c for c in df.columns if c not in raw_cols])
    sample_count = len(df)

    print(f"Engineered {feature_count} features across {sample_count:,} samples")

    # ── Write outputs ────────────────────────────────────────────────────
    df.to_parquet("features.parquet", index=False)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "feature_count": feature_count,
            "sample_count": sample_count,
        }, f)


if __name__ == "__main__":
    main()
