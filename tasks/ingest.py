#!/usr/bin/env python3
"""
Task 1: Ingest Telemetry
========================
Reads raw CSV + metadata JSON, validates schema, converts to Parquet.

Inputs:  fleet_telemetry.csv, fleet_metadata.json
Outputs: telemetry.parquet (fleet_metadata.json is read-through, not copied)
Vars:    row_count, device_count, time_span_days
"""

import json
import pandas as pd

EXPECTED_COLUMNS = {
    "timestamp", "device_id", "model",
    "clock_ghz", "voltage_v", "hashrate_th",
    "power_w", "temperature_c", "cooling_power_w",
    "ambient_temp_c", "energy_price_kwh",
    "operating_mode", "efficiency_jth",
    "label_thermal_deg", "label_psu_instability",
    "label_hashrate_decay", "label_any_anomaly",
}

NUMERIC_COLUMNS = [
    "clock_ghz", "voltage_v", "hashrate_th", "power_w",
    "temperature_c", "cooling_power_w", "ambient_temp_c",
    "energy_price_kwh", "efficiency_jth",
]


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_csv("fleet_telemetry.csv")
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    # ── Validate schema ──────────────────────────────────────────────────
    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    # ── Data quality checks ──────────────────────────────────────────────
    n_nulls = df[list(EXPECTED_COLUMNS)].isnull().sum().sum()
    if n_nulls > 0:
        print(f"Warning: {n_nulls} null values found across telemetry columns")

    n_dupes = df.duplicated(subset=["timestamp", "device_id"]).sum()
    if n_dupes > 0:
        print(f"Warning: {n_dupes} duplicate (timestamp, device_id) rows — dropping")
        df = df.drop_duplicates(subset=["timestamp", "device_id"], keep="first")

    # ── Parse types ──────────────────────────────────────────────────────
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Label columns as int
    for col in ["label_thermal_deg", "label_psu_instability",
                "label_hashrate_decay", "label_any_anomaly"]:
        df[col] = df[col].astype(int)

    # Sort for downstream processing
    df = df.sort_values(["device_id", "timestamp"]).reset_index(drop=True)

    # ── Summary stats ────────────────────────────────────────────────────
    row_count = len(df)
    device_count = df["device_id"].nunique()
    time_span = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400

    print(f"Ingested {row_count:,} rows, {device_count} devices, {time_span:.1f} days")

    # ── Write outputs ────────────────────────────────────────────────────
    df.to_parquet("telemetry.parquet", index=False)
    # fleet_metadata.json already present in working dir — no copy needed

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "row_count": row_count,
            "device_count": device_count,
            "time_span_days": round(time_span, 2),
        }, f)


if __name__ == "__main__":
    main()
