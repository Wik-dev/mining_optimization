#!/usr/bin/env python3
"""
Task 4b: Score Fleet (Online Inference Simulation)
===================================================
Loads pre-trained model and scores the latest scoring window of telemetry.
Simulates the real-time inference path: no training, just load + predict.

In production this would run every 5 minutes on the latest data from MDK.

Inputs:  kpi_timeseries.parquet, anomaly_model.joblib, fleet_metadata.json
Outputs: fleet_risk_scores.json
Vars:    flagged_devices, scoring_window_hours
"""

import json
import pandas as pd
import numpy as np
import joblib

SCORING_WINDOW_HOURS = 24


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_parquet("kpi_timeseries.parquet")
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    artifact = joblib.load("anomaly_model.joblib")
    model = artifact["model"]
    feature_names = artifact["feature_names"]
    threshold = artifact["threshold"]

    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    # ── Select scoring window (last N hours) ─────────────────────────────
    t_max = df["timestamp"].max()
    t_cutoff = t_max - pd.Timedelta(hours=SCORING_WINDOW_HOURS)
    window = df[df["timestamp"] > t_cutoff].copy()

    # Filter to active (non-idle) with valid TE
    active_mask = window["true_efficiency"].notna() & (window["hashrate_th"] > 0)
    window_active = window[active_mask].copy()

    print(f"Scoring window: {t_cutoff} → {t_max} ({len(window_active):,} active samples)")

    # ── Score ────────────────────────────────────────────────────────────
    available = [c for c in feature_names if c in window_active.columns]
    X = window_active[available].fillna(0).replace([np.inf, -np.inf], 0)
    y_proba = model.predict_proba(X)[:, 1]

    window_active = window_active.copy()
    window_active["anomaly_prob"] = y_proba

    # Sort by timestamp so "last" gives the most recent reading
    window_active = window_active.sort_values("timestamp")

    # ── Per-device risk aggregation ──────────────────────────────────────
    device_risks = []
    for device_id, group in window_active.groupby("device_id"):
        probs = group["anomaly_prob"]
        last_row = group.iloc[-1]

        risk = {
            "device_id": device_id,
            "model": last_row.get("model", "unknown"),
            "mean_risk": round(float(probs.mean()), 4),
            "max_risk": round(float(probs.max()), 4),
            "pct_flagged": round(float((probs > threshold).mean()), 4),
            "last_risk": round(float(probs.iloc[-1]), 4),
            "flagged": bool(probs.mean() > threshold),
            # Latest telemetry snapshot for the controller
            "latest_snapshot": {
                "timestamp": str(last_row["timestamp"]),
                "te_score": round(float(last_row.get("te_score", 0)), 4),
                "true_efficiency": round(float(last_row.get("true_efficiency", 0)), 2),
                "temperature_c": round(float(last_row.get("temperature_c", 0)), 2),
                "voltage_v": round(float(last_row.get("voltage_v", 0)), 4),
                "hashrate_th": round(float(last_row.get("hashrate_th", 0)), 2),
                "power_w": round(float(last_row.get("power_w", 0)), 1),
                "cooling_power_w": round(float(last_row.get("cooling_power_w", 0)), 1),
                "ambient_temp_c": round(float(last_row.get("ambient_temp_c", 0)), 2),
                "operating_mode": str(last_row.get("operating_mode", "unknown")),
            },
        }
        device_risks.append(risk)

    # Sort by risk descending
    device_risks.sort(key=lambda r: r["mean_risk"], reverse=True)
    flagged = sum(1 for d in device_risks if d["flagged"])

    print(f"Flagged devices: {flagged}/{len(device_risks)}")
    for d in device_risks:
        flag = " ** FLAGGED **" if d["flagged"] else ""
        print(f"  {d['device_id']}: mean_risk={d['mean_risk']:.3f}  "
              f"te_score={d['latest_snapshot']['te_score']:.3f}{flag}")

    # ── Write outputs ────────────────────────────────────────────────────
    output = {
        "scoring_window_hours": SCORING_WINDOW_HOURS,
        "window_start": str(t_cutoff),
        "window_end": str(t_max),
        "samples_scored": len(window_active),
        "threshold": threshold,
        "device_risks": device_risks,
    }

    with open("fleet_risk_scores.json", "w") as f:
        json.dump(output, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "flagged_devices": flagged,
            "scoring_window_hours": SCORING_WINDOW_HOURS,
        }, f)


if __name__ == "__main__":
    main()
