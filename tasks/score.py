#!/usr/bin/env python3
"""
Task 4b: Score Fleet (Online Inference Simulation)
===================================================
Loads pre-trained classifier and (optionally) multi-horizon quantile regressor,
then scores the latest scoring window of telemetry.

Binary risk scoring (classifier): "is this device anomalous right now?"
Multi-horizon prediction (regressor, Phase 5): "what will TE_score be at
t+1h, t+6h, t+24h, t+7d?" with p10/p50/p90 uncertainty bounds.

Graceful fallback: if no regression model exists, outputs classifier-only
scores. No downstream breakage.

Inputs:  kpi_timeseries.parquet, anomaly_model.joblib, fleet_metadata.json,
         regression_model_v{N}.joblib (optional), model_registry.json (optional)
Outputs: fleet_risk_scores.json
Vars:    flagged_devices, scoring_window_hours
"""

import argparse
import json
import os

import pandas as pd
import numpy as np
import joblib

SCORING_WINDOW_HOURS = 24

# Same horizon definitions as train_model.py
HORIZONS = {
    "1h": 12,
    "6h": 72,
    "24h": 288,
    "7d": 2016,
}

# TE_score thresholds for predicted crossing analysis.
# 0.8 = DEGRADED boundary (device needs attention),
# 0.6 = CRITICAL boundary (device needs immediate intervention).
# These align with the controller tier thresholds in optimize.py.
TE_THRESHOLDS = {
    "te_0.8": 0.8,   # DEGRADED
    "te_0.6": 0.6,   # CRITICAL
}


def load_regression_model(registry_path="model_registry.json"):
    """Load the active regression model from the registry.

    Returns (artifact, version) or (None, None) if no model available.
    Graceful: first run or missing model → classifier-only output.
    """
    if not os.path.exists(registry_path):
        return None, None

    with open(registry_path) as f:
        registry = json.load(f)

    active_version = registry.get("active_version")
    if active_version is None:
        return None, None

    active_entry = next(
        (v for v in registry["versions"] if v["version"] == active_version),
        None
    )
    if active_entry is None:
        return None, None

    artifact_path = active_entry["artifact"]
    if not os.path.exists(artifact_path):
        # Workflow input mapping may rename the file (e.g., registry says
        # "regression_model_v1.joblib" but the worker creates "regression_model.joblib"
        # from the input key). Try the generic name as fallback.
        fallback = "regression_model.joblib"
        if os.path.exists(fallback):
            print(f"Registry artifact {artifact_path} not found, using {fallback}")
            artifact_path = fallback
        else:
            print(f"Warning: regression artifact {artifact_path} not found, skipping predictions")
            return None, None

    artifact = joblib.load(artifact_path)
    return artifact, active_version


def add_temporal_features_for_scoring(group: pd.DataFrame) -> pd.DataFrame:
    """Compute autoregressive TE features for a single device's scoring window.

    Same computation as train_model.add_temporal_features but operates on a
    single device group (already sorted by timestamp). Needs enough history
    before the scoring window for the lagged features — the full device
    timeseries is passed in, then we extract the scoring window rows after.
    """
    g = group.copy()
    te = g["te_score"]

    g["te_score_lag_1h"] = te.shift(12)
    g["te_score_lag_6h"] = te.shift(72)
    g["te_score_lag_24h"] = te.shift(288)

    g["te_score_slope_1h"] = _rolling_slope(te, window=12)
    g["te_score_slope_6h"] = _rolling_slope(te, window=72)
    g["te_score_volatility_24h"] = te.rolling(window=288, min_periods=12).std()

    return g


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """Compute rolling linear regression slope (same as train_model._rolling_slope)."""
    slopes = np.full(len(series), np.nan)
    values = series.values

    for i in range(window - 1, len(values)):
        window_vals = values[i - window + 1:i + 1]
        valid = ~np.isnan(window_vals)
        if valid.sum() >= max(window // 2, 2):
            x = np.arange(valid.sum())
            y = window_vals[valid]
            slopes[i] = np.polyfit(x, y, 1)[0]

    return pd.Series(slopes, index=series.index)


def predict_horizons(last_row_features, reg_artifact):
    """Predict TE_score at all horizons with quantile bounds.

    Uses the last row of the scoring window (most recent device state) as
    input to all 12 models. Enforces quantile ordering: p10 <= p50 <= p90
    via monotone sort to handle rare quantile crossing from separately
    trained models.
    """
    models = reg_artifact["models"]
    feature_names = reg_artifact["feature_names"]

    # Align features: use intersection of available vs expected, fill missing with 0
    available = [c for c in feature_names if c in last_row_features.index]
    X = pd.DataFrame([last_row_features.reindex(feature_names, fill_value=0)])
    X = X.fillna(0).replace([np.inf, -np.inf], 0)

    predictions = {}
    for horizon_name in reg_artifact["horizons"]:
        # Skip horizons where models were not trained (e.g., 7d on short datasets)
        if models[horizon_name].get("p50") is None:
            continue

        raw = {}
        for q_label in ["p10", "p50", "p90"]:
            model = models[horizon_name][q_label]
            raw[q_label] = float(model.predict(X)[0])

        # Enforce monotone ordering: p10 <= p50 <= p90
        # Separately trained quantile models can rarely produce crossings.
        sorted_vals = sorted([raw["p10"], raw["p50"], raw["p90"]])
        predictions[f"te_score_{horizon_name}"] = {
            "p10": round(sorted_vals[0], 4),
            "p50": round(sorted_vals[1], 4),
            "p90": round(sorted_vals[2], 4),
        }

    return predictions


def compute_predicted_crossings(predictions):
    """Identify when p50 forecast crosses TE thresholds.

    For each threshold (DEGRADED=0.8, CRITICAL=0.6), find the first horizon
    where p50 drops below. Confidence is "high" if p90 is also below the
    threshold (entire prediction interval below), "medium" otherwise (only
    the median forecast crosses).
    """
    crossings = {}
    horizon_order = ["1h", "6h", "24h", "7d"]

    for threshold_name, threshold_val in TE_THRESHOLDS.items():
        for h in horizon_order:
            key = f"te_score_{h}"
            if key not in predictions:
                continue
            pred = predictions[key]
            if pred["p50"] < threshold_val:
                confidence = "high" if pred["p90"] < threshold_val else "medium"
                crossings[threshold_name] = {
                    "horizon": h,
                    "confidence": confidence,
                    "p50": pred["p50"],
                }
                break

    return crossings


def parse_args():
    parser = argparse.ArgumentParser(description="Score fleet devices for anomaly risk")
    parser.add_argument("--model-path", default="anomaly_model.joblib",
                        help="Path to classifier model artifact (default: anomaly_model.joblib)")
    # Override the classification threshold baked into the model artifact
    # (default 0.3). Lower → higher recall, more inspections.
    # See docs/evaluation-analysis.md for rationale and tuning guide.
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override anomaly probability threshold (default: use model artifact value)")
    return parser.parse_args()


def main():
    args = parse_args()

    # ── Load classifier ──────────────────────────────────────────────────
    df = pd.read_parquet("kpi_timeseries.parquet")
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    artifact = joblib.load(args.model_path)
    model = artifact["model"]
    feature_names = artifact["feature_names"]
    # CLI --threshold overrides the model artifact value without retraining.
    threshold = args.threshold if args.threshold is not None else artifact["threshold"]
    print(f"Classification threshold: {threshold}"
          f"{' (CLI override)' if args.threshold is not None else ' (from model artifact)'}")

    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    # ── Load regression model (graceful fallback) ─────────────────────────
    reg_artifact, reg_version = load_regression_model()
    has_regression = reg_artifact is not None
    if has_regression:
        print(f"Regression model loaded: version {reg_version} "
              f"({len(reg_artifact['horizons'])} horizons × "
              f"{len(reg_artifact['quantiles'])} quantiles)")
    else:
        print("No regression model available — classifier-only scoring")

    # ── Select scoring window (last N hours) ─────────────────────────────
    t_max = df["timestamp"].max()
    t_cutoff = t_max - pd.Timedelta(hours=SCORING_WINDOW_HOURS)
    window = df[df["timestamp"] > t_cutoff].copy()

    # Filter to active (non-idle) with valid TE
    active_mask = window["true_efficiency"].notna() & (window["hashrate_th"] > 0)
    window_active = window[active_mask].copy()

    print(f"Scoring window: {t_cutoff} → {t_max} ({len(window_active):,} active samples)")

    # ── Score (classifier) ───────────────────────────────────────────────
    available = [c for c in feature_names if c in window_active.columns]
    X = window_active[available].fillna(0).replace([np.inf, -np.inf], 0)
    y_proba = model.predict_proba(X)[:, 1]

    window_active = window_active.copy()
    window_active["anomaly_prob"] = y_proba

    # Sort by timestamp so "last" gives the most recent reading
    window_active = window_active.sort_values("timestamp")

    # ── Prepare temporal features for regression (if available) ──────────
    # Need full device history for lagged features, not just scoring window.
    device_temporal = {}
    if has_regression:
        print("Computing temporal features for regression predictions...")
        active_full = df[df["true_efficiency"].notna() & (df["hashrate_th"] > 0)].copy()
        active_full = active_full.sort_values(["device_id", "timestamp"])

        for device_id, group in active_full.groupby("device_id"):
            enriched = add_temporal_features_for_scoring(group)
            # Keep only the last row (most recent state) for prediction
            device_temporal[device_id] = enriched.iloc[-1]

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

        # Phase 5: multi-horizon predictions
        if has_regression and device_id in device_temporal:
            last_features = device_temporal[device_id]
            predictions = predict_horizons(last_features, reg_artifact)
            crossings = compute_predicted_crossings(predictions)
            risk["predictions"] = predictions
            if crossings:
                risk["predicted_crossings"] = crossings

        device_risks.append(risk)

    # Sort by risk descending
    device_risks.sort(key=lambda r: r["mean_risk"], reverse=True)
    flagged = sum(1 for d in device_risks if d["flagged"])

    print(f"\nFlagged devices: {flagged}/{len(device_risks)}")
    for d in device_risks:
        flag = " ** FLAGGED **" if d["flagged"] else ""
        pred_info = ""
        if "predictions" in d:
            p50_1h = d["predictions"].get("te_score_1h", {}).get("p50", "?")
            p50_24h = d["predictions"].get("te_score_24h", {}).get("p50", "?")
            pred_info = f"  pred_1h={p50_1h}  pred_24h={p50_24h}"
        print(f"  {d['device_id']}: mean_risk={d['mean_risk']:.3f}  "
              f"te_score={d['latest_snapshot']['te_score']:.3f}{pred_info}{flag}")

    # ── Write outputs ────────────────────────────────────────────────────
    output = {
        "scoring_window_hours": SCORING_WINDOW_HOURS,
        "window_start": str(t_cutoff),
        "window_end": str(t_max),
        "samples_scored": len(window_active),
        "threshold": threshold,
        "device_risks": device_risks,
    }

    # Track which model versions produced these scores
    if has_regression:
        output["model_versions"] = {
            "classifier": "anomaly_model.joblib",
            "regressor_version": reg_version,
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
