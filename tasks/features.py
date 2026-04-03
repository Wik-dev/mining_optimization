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
WINDOW_SHORT = 12     # 1 hour
WINDOW_MEDIUM = 144   # 12 hours
WINDOW_LONG = 288     # 24 hours

TELEMETRY_COLS = [
    "temperature_c", "power_w", "hashrate_th",
    "voltage_v", "cooling_power_w", "efficiency_jth",
]

# Columns present before feature engineering (for feature count diff)
EXPECTED_TELEMETRY_COLS = {
    "timestamp", "device_id", "model",
    "clock_ghz", "voltage_v", "hashrate_th",
    "power_w", "temperature_c", "cooling_power_w",
    "ambient_temp_c", "energy_price_kwh",
    "operating_mode", "efficiency_jth",
    "label_thermal_deg", "label_psu_instability",
    "label_hashrate_decay", "label_any_anomaly",
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
                                                "nominal_efficiency"}
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
