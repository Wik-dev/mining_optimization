#!/usr/bin/env python3
"""
Task 3: Compute True Efficiency KPI
====================================
Implements the TE formula from docs/true-efficiency-kpi.md.

    TE = (P_asic + P_cooling_norm) / (H × η_v)

Decomposition:
    TE_base      = P_asic / H                          (naive J/TH)
    η_v          = (V_optimal(f) / V_actual)²           (voltage efficiency)
    P_cool_norm  = P_cool × (T_chip - T_ref) / (T_chip - T_amb)
    R_cool       = (P_asic + P_cool_norm) / P_asic     (cooling overhead)
    TE_score     = TE_nominal / TE                      (health score)

Inputs:  features.parquet, fleet_metadata.json
Outputs: kpi_timeseries.parquet
Vars:    mean_te, worst_device, worst_te_score
"""

import json
import pandas as pd
import numpy as np

# Constants (see docs/true-efficiency-kpi.md)
T_REF = 25.0        # Reference ambient temperature (°C)
VF_ALPHA = 0.6       # V/f scaling exponent for CMOS
THERMAL_FLOOR = 1.0  # Min (T_chip - T_ambient) to avoid division by zero


def compute_voltage_efficiency(df: pd.DataFrame) -> pd.Series:
    """η_v = (V_optimal(f_actual) / V_actual)²"""
    v_optimal = df["stock_voltage"] * (
        df["clock_ghz"] / df["stock_clock"].replace(0, np.nan)
    ) ** VF_ALPHA

    eta_v = (v_optimal / df["voltage_v"].replace(0, np.nan)) ** 2
    return eta_v.clip(0, 2.0).fillna(1.0)  # Bound to [0, 2] for sanity


def compute_cooling_normalized(df: pd.DataFrame) -> pd.Series:
    """P_cooling_norm = P_cooling × (T_chip - T_ref) / max(T_chip - T_ambient, floor)"""
    thermal_delta = (df["temperature_c"] - df["ambient_temp_c"]).clip(lower=THERMAL_FLOOR)
    ref_delta = df["temperature_c"] - T_REF

    p_cool_norm = df["cooling_power_w"] * ref_delta / thermal_delta
    return p_cool_norm.clip(lower=0).fillna(0)


def compute_te_nominal(meta: dict) -> dict:
    """Compute TE at stock settings + T_ref for each device (baseline)."""
    nominals = {}
    for dev in meta["fleet"]:
        p_asic = dev["nominal_power_w"]
        h = dev["nominal_hashrate_th"]
        # At stock + T_ref: η_v = 1.0, cooling ≈ 10% of ASIC power.
        # Rationale: site metadata says "hydro + air" cooling at latitude 64.5°N.
        # At T_ref=25°C, hydro-cooled ASICs in the 3–5kW range typically draw
        # 300–500W base cooling, which is ~10% of ASIC power. This is a
        # model-agnostic estimate; per-model calibration would require real data.
        p_cool_est = p_asic * 0.10
        te_nom = (p_asic + p_cool_est) / h  # η_v = 1.0 at stock
        nominals[dev["device_id"]] = te_nom
    return nominals


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_parquet("features.parquet")
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    # ── Filter idle samples (hashrate = 0 → TE undefined) ───────────────
    active_mask = df["hashrate_th"] > 0
    df_active = df[active_mask].copy()

    # ── Compute TE components ────────────────────────────────────────────
    df_active["eta_v"] = compute_voltage_efficiency(df_active)
    df_active["p_cooling_norm"] = compute_cooling_normalized(df_active)

    # TE_base (naive J/TH)
    df_active["te_base"] = df_active["power_w"] / df_active["hashrate_th"]

    # Voltage penalty
    df_active["voltage_penalty"] = 1.0 / df_active["eta_v"]

    # Cooling overhead ratio
    df_active["cooling_ratio"] = (
        (df_active["power_w"] + df_active["p_cooling_norm"]) / df_active["power_w"]
    )

    # True Efficiency
    df_active["true_efficiency"] = (
        (df_active["power_w"] + df_active["p_cooling_norm"])
        / (df_active["hashrate_th"] * df_active["eta_v"])
    )

    # TE_score (health score relative to nominal)
    te_nominals = compute_te_nominal(meta)
    df_active["te_nominal"] = df_active["device_id"].map(te_nominals)
    df_active["te_score"] = df_active["te_nominal"] / df_active["true_efficiency"]

    # ── Merge back to full dataframe (idle rows get NaN) ─────────────────
    kpi_cols = [
        "eta_v", "p_cooling_norm", "te_base", "voltage_penalty",
        "cooling_ratio", "true_efficiency", "te_nominal", "te_score",
    ]
    for col in kpi_cols:
        df[col] = np.nan
    # Use index-aligned assignment (not .values) to prevent silent misalignment
    df.loc[active_mask, kpi_cols] = df_active[kpi_cols]

    # ── Summary ──────────────────────────────────────────────────────────
    mean_te = float(df_active["true_efficiency"].mean())

    device_scores = df_active.groupby("device_id")["te_score"].mean()
    worst_device = device_scores.idxmin()
    worst_te_score = float(device_scores.min())

    print(f"Mean TE: {mean_te:.2f} J/TH")
    print(f"Worst device: {worst_device} (TE_score={worst_te_score:.3f})")

    # ── Write outputs ────────────────────────────────────────────────────
    df.to_parquet("kpi_timeseries.parquet", index=False)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "mean_te": round(mean_te, 4),
            "worst_device": worst_device,
            "worst_te_score": round(worst_te_score, 4),
        }, f)


if __name__ == "__main__":
    main()
