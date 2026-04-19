#!/usr/bin/env python3
"""
Prediction Evaluation — Classifier + Regression Verification
=============================================================
Compares model predictions against ground truth in independently generated
inference data. Produces evaluation_report.json with:

  1. Classifier evaluation: device-level accuracy, precision, recall, F1
     against ground truth anomaly labels in the scoring window.

  2. Regression evaluation: TE predictions at t+1h/6h/24h/7d compared to
     actual future TE values. RMSE, MAE, and calibration (80% interval
     coverage) per horizon.

The scoring window for regression evaluation is placed at the midpoint of
the data (not the end), so future data exists for all horizons.

Usage:
    python evaluate_predictions.py \
        --kpi kpi_timeseries.parquet \
        --model anomaly_model.joblib \
        --regression-model regression_model_v1.joblib \
        --model-registry model_registry.json \
        --metadata fleet_metadata.json \
        --output evaluation_report.json

Or with Validance shared work directory:
    python evaluate_predictions.py \
        --shared-dir /path/to/shared/workflow_hash \
        --model-dir /path/to/training/shared/train_anomaly_model

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import joblib


# Same horizon definitions as train_model.py / score.py
HORIZONS = {
    "1h": 12,      # 12 × 5min
    "6h": 72,      # 72 × 5min
    "24h": 288,    # 288 × 5min
    "7d": 2016,    # 2016 × 5min
}

SCORING_WINDOW_HOURS = 24

ANOMALY_TYPES = [
    "label_thermal_deg", "label_psu_instability", "label_hashrate_decay",
    "label_fan_bearing_wear", "label_capacitor_aging", "label_dust_fouling",
    "label_thermal_paste_deg", "label_solder_joint_fatigue",
    "label_coolant_loop_fouling", "label_firmware_cliff",
]


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """Rolling linear regression slope (same as train_model/score)."""
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


def add_temporal_features(group: pd.DataFrame) -> pd.DataFrame:
    """Compute autoregressive TE features for a single device."""
    g = group.copy()
    te = g["te_score"]
    g["te_score_lag_1h"] = te.shift(12)
    g["te_score_lag_6h"] = te.shift(72)
    g["te_score_lag_24h"] = te.shift(288)
    g["te_score_slope_1h"] = _rolling_slope(te, window=12)
    g["te_score_slope_6h"] = _rolling_slope(te, window=72)
    g["te_score_volatility_24h"] = te.rolling(window=288, min_periods=12).std()
    return g


def evaluate_classifier(df, model_artifact, scoring_window_start, scoring_window_end):
    """Evaluate classifier predictions against ground truth labels.

    Scores the scoring window, then compares per-device flagging
    against actual anomaly labels. Returns device-level metrics.
    """
    model = model_artifact["model"]
    feature_names = model_artifact["feature_names"]
    threshold = model_artifact["threshold"]

    # Select scoring window
    window = df[(df["timestamp"] > scoring_window_start) &
                (df["timestamp"] <= scoring_window_end)].copy()

    # Filter to active samples
    active = window[window["true_efficiency"].notna() & (window["hashrate_th"] > 0)].copy()

    if len(active) == 0:
        return {"error": "no active samples in scoring window"}

    # Predict
    available = [c for c in feature_names if c in active.columns]
    X = active[available].fillna(0).replace([np.inf, -np.inf], 0)
    y_proba = model.predict_proba(X)[:, 1]
    active = active.copy()
    active["anomaly_prob"] = y_proba

    # Per-device aggregation (same as score.py)
    results = []
    for device_id, grp in active.groupby("device_id"):
        mean_prob = grp["anomaly_prob"].mean()
        predicted_flag = mean_prob > threshold

        # Ground truth: was this device anomalous in the window?
        actual_flag = grp["label_any_anomaly"].sum() > 0
        actual_rate = grp["label_any_anomaly"].mean()

        # Per-type ground truth
        actual_types = []
        for col in ANOMALY_TYPES:
            if col in grp.columns and grp[col].sum() > 0:
                actual_types.append(col.replace("label_", ""))

        results.append({
            "device_id": device_id,
            "predicted_flag": bool(predicted_flag),
            "predicted_prob": round(float(mean_prob), 4),
            "actual_flag": bool(actual_flag),
            "actual_anomaly_rate": round(float(actual_rate), 4),
            "actual_types": actual_types,
            "correct": predicted_flag == actual_flag,
        })

    # Compute aggregate metrics
    tp = sum(1 for r in results if r["predicted_flag"] and r["actual_flag"])
    fp = sum(1 for r in results if r["predicted_flag"] and not r["actual_flag"])
    tn = sum(1 for r in results if not r["predicted_flag"] and not r["actual_flag"])
    fn = sum(1 for r in results if not r["predicted_flag"] and r["actual_flag"])

    accuracy = (tp + tn) / max(len(results), 1)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)

    return {
        "scoring_window": {
            "start": str(scoring_window_start),
            "end": str(scoring_window_end),
            "samples": len(active),
            "devices_scored": len(results),
        },
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "per_device": results,
    }


def evaluate_regression(df, reg_artifact, eval_timestamp):
    """Evaluate regression predictions against actual future TE values.

    For each device, predicts TE at t+1h/6h/24h/7d from eval_timestamp,
    then compares against the actual TE values at those future timestamps.
    Returns RMSE, MAE, and calibration per horizon.
    """
    if reg_artifact is None:
        return {"error": "no regression model available"}

    models = reg_artifact["models"]
    feature_names = reg_artifact["feature_names"]

    # Filter to active data up to eval_timestamp (model only sees past)
    active = df[df["true_efficiency"].notna() & (df["hashrate_th"] > 0)].copy()
    active = active.sort_values(["device_id", "timestamp"])

    # Also need future data for verification
    future = df[df["timestamp"] > eval_timestamp].copy()
    if len(future) == 0:
        return {"error": "no future data after eval_timestamp"}

    per_horizon = {}
    for horizon_name, offset_samples in HORIZONS.items():
        # Target timestamp
        offset_minutes = offset_samples * 5
        target_time = eval_timestamp + pd.Timedelta(minutes=offset_minutes)

        predictions = []
        actuals = []
        p10_preds = []
        p90_preds = []

        for device_id, grp in active.groupby("device_id"):
            # Get data up to eval_timestamp for this device
            past = grp[grp["timestamp"] <= eval_timestamp]
            if len(past) < 288:  # Need at least 24h of history
                continue

            # Compute temporal features
            enriched = add_temporal_features(past)
            last_row = enriched.iloc[-1]

            # Align features
            X = pd.DataFrame([last_row.reindex(feature_names, fill_value=0)])
            X = X.fillna(0).replace([np.inf, -np.inf], 0)

            # Predict
            if models[horizon_name].get("p50") is None:
                continue

            p10 = float(models[horizon_name]["p10"].predict(X)[0])
            p50 = float(models[horizon_name]["p50"].predict(X)[0])
            p90 = float(models[horizon_name]["p90"].predict(X)[0])

            # Find actual TE at target timestamp (nearest within ±5 min)
            dev_future = future[future["device_id"] == device_id]
            if len(dev_future) == 0:
                continue

            nearest_idx = (dev_future["timestamp"] - target_time).abs().idxmin()
            nearest_row = dev_future.loc[nearest_idx]
            time_diff = abs((nearest_row["timestamp"] - target_time).total_seconds())

            # Only accept if within 10 minutes of target
            if time_diff > 600:
                continue

            actual_te = nearest_row["te_score"]
            if pd.isna(actual_te):
                continue
            actual_te = float(actual_te)

            predictions.append(p50)
            actuals.append(actual_te)
            p10_preds.append(p10)
            p90_preds.append(p90)

        if len(predictions) == 0:
            per_horizon[horizon_name] = {
                "error": f"no devices with data at {target_time}",
                "devices_evaluated": 0,
            }
            continue

        predictions = np.array(predictions)
        actuals = np.array(actuals)
        p10_preds = np.array(p10_preds)
        p90_preds = np.array(p90_preds)

        rmse = float(np.sqrt(np.mean((predictions - actuals) ** 2)))
        mae = float(np.mean(np.abs(predictions - actuals)))

        # Calibration: fraction of actuals within [p10, p90]
        in_interval = ((actuals >= p10_preds) & (actuals <= p90_preds)).mean()

        per_horizon[horizon_name] = {
            "devices_evaluated": len(predictions),
            "rmse_p50": round(rmse, 6),
            "mae_p50": round(mae, 6),
            "calibration_80": round(float(in_interval), 4),
            "mean_actual_te": round(float(actuals.mean()), 4),
            "mean_predicted_te": round(float(predictions.mean()), 4),
        }

    return {
        "eval_timestamp": str(eval_timestamp),
        "per_horizon": per_horizon,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate predictions against ground truth")
    parser.add_argument("--kpi", required=True, help="Path to kpi_timeseries.parquet")
    parser.add_argument("--model", required=True, help="Path to anomaly_model.joblib")
    parser.add_argument("--regression-model", help="Path to regression_model_v*.joblib")
    parser.add_argument("--metadata", help="Path to fleet_metadata.json")
    parser.add_argument("--output", default="evaluation_report.json", help="Output path")
    parser.add_argument("--eval-point", default="midpoint",
                        help="Where to evaluate: 'midpoint' (default) or 'last24h'")
    # Override classification threshold.
    parser.add_argument("--threshold", type=float, default=None,
                        help="Override anomaly probability threshold (default: use model artifact value)")
    args = parser.parse_args()

    # ── Load data ────────────────────────────────────────────────────────
    print("Loading data...")
    df = pd.read_parquet(args.kpi)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values(["device_id", "timestamp"])

    model_artifact = joblib.load(args.model)
    # CLI --threshold overrides the value baked into the model artifact
    if args.threshold is not None:
        model_artifact["threshold"] = args.threshold
        print(f"Threshold override: {args.threshold}")

    reg_artifact = None
    if args.regression_model and os.path.exists(args.regression_model):
        reg_artifact = joblib.load(args.regression_model)
        print(f"Regression model loaded: {len(reg_artifact['horizons'])} horizons")

    t_min, t_max = df["timestamp"].min(), df["timestamp"].max()
    total_hours = (t_max - t_min).total_seconds() / 3600
    print(f"Data range: {t_min} → {t_max} ({total_hours:.0f} hours)")
    print(f"Devices: {df['device_id'].nunique()}")
    print(f"Rows: {len(df):,}")

    # ── Choose evaluation point ──────────────────────────────────────────
    # Place scoring window so there's enough future data for regression
    # verification. Midpoint leaves ~45 days of future data for 7d horizon.
    if args.eval_point == "midpoint":
        eval_center = t_min + (t_max - t_min) / 2
    elif args.eval_point == "last24h":
        eval_center = t_max
    else:
        eval_center = pd.Timestamp(args.eval_point)

    scoring_window_end = eval_center
    scoring_window_start = eval_center - pd.Timedelta(hours=SCORING_WINDOW_HOURS)

    print(f"\nEvaluation point: {eval_center}")
    print(f"Classifier window: {scoring_window_start} → {scoring_window_end}")

    # ── Classifier evaluation ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Classifier Evaluation")
    print("=" * 60)

    clf_results = evaluate_classifier(
        df, model_artifact, scoring_window_start, scoring_window_end
    )

    if "error" not in clf_results:
        cm = clf_results["confusion_matrix"]
        print(f"\nDevices scored: {clf_results['scoring_window']['devices_scored']}")
        print(f"Confusion matrix: TP={cm['tp']} FP={cm['fp']} TN={cm['tn']} FN={cm['fn']}")
        print(f"Accuracy:  {clf_results['accuracy']:.4f}")
        print(f"Precision: {clf_results['precision']:.4f}")
        print(f"Recall:    {clf_results['recall']:.4f}")
        print(f"F1 Score:  {clf_results['f1_score']:.4f}")
        print("\nPer-device:")
        for d in clf_results["per_device"]:
            mark = "OK" if d["correct"] else "MISS"
            flag = "FLAGGED" if d["predicted_flag"] else "clear"
            truth = f"ANOMALOUS ({','.join(d['actual_types'])})" if d["actual_flag"] else "healthy"
            print(f"  [{mark:4s}] {d['device_id']:12s} {flag:8s} prob={d['predicted_prob']:.3f}  truth={truth}")
    else:
        print(f"  Error: {clf_results['error']}")

    # ── Regression evaluation ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Regression Evaluation")
    print("=" * 60)

    reg_results = evaluate_regression(df, reg_artifact, eval_center)

    if "error" not in reg_results:
        for h, metrics in reg_results["per_horizon"].items():
            if "error" in metrics:
                print(f"\n[{h}] {metrics['error']}")
            else:
                print(f"\n[{h}] {metrics['devices_evaluated']} devices")
                print(f"  RMSE:         {metrics['rmse_p50']:.6f}")
                print(f"  MAE:          {metrics['mae_p50']:.6f}")
                print(f"  Calibration:  {metrics['calibration_80']:.1%} "
                      f"(target: 75-85%)")
                print(f"  Mean actual:  {metrics['mean_actual_te']:.4f}")
                print(f"  Mean predict: {metrics['mean_predicted_te']:.4f}")
    else:
        print(f"  Error: {reg_results['error']}")

    # ── Write output ─────────────────────────────────────────────────────
    report = {
        "classifier": clf_results,
        "regression": reg_results,
        "data_summary": {
            "time_range": f"{t_min} → {t_max}",
            "total_hours": round(total_hours, 1),
            "devices": int(df["device_id"].nunique()),
            "rows": len(df),
        },
    }

    # Custom encoder for numpy types
    class NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj) if not np.isnan(obj) else None
            return super().default(obj)

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, cls=NumpyEncoder)

    print(f"\nEvaluation report written to {args.output}")


if __name__ == "__main__":
    main()
