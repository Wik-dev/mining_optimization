#!/usr/bin/env python3
"""
Task 4a: Train Anomaly Model
=============================
Trains XGBoost classifiers on TE decomposition features + rolling stats.
Saves model artifact for downstream scoring (offline/online split).

Uses time-based train/test split (first 70% train, last 30% test).

Inputs:  kpi_timeseries.parquet, fleet_metadata.json
Outputs: anomaly_model.joblib, model_metrics.json
Vars:    model_accuracy, model_f1, train_samples, test_samples
"""

import json
import warnings
import pandas as pd
import numpy as np
import joblib
from sklearn.metrics import accuracy_score, f1_score, classification_report
from xgboost import XGBClassifier

warnings.filterwarnings("ignore", category=UserWarning)

# Features used for prediction (TE decomposition + rolling + interaction)
FEATURE_COLS = [
    # TE decomposition
    "te_base", "voltage_penalty", "cooling_ratio", "eta_v",
    "true_efficiency", "te_score",
    # Rolling stats
    "temperature_c_mean_1h", "temperature_c_std_1h",
    "temperature_c_mean_24h", "temperature_c_dev_24h",
    "power_w_mean_1h", "power_w_std_1h", "power_w_dev_24h",
    "hashrate_th_mean_1h", "hashrate_th_std_1h", "hashrate_th_dev_24h",
    "voltage_v_std_1h", "voltage_v_dev_24h",
    "cooling_power_w_mean_1h", "cooling_power_w_dev_24h",
    "efficiency_jth_mean_1h", "efficiency_jth_dev_24h",
    # Rates of change
    "d_temperature_c_smooth", "d_power_w_smooth",
    "d_hashrate_th_smooth", "d_voltage_v_smooth",
    # Interaction features
    "power_per_ghz", "thermal_headroom_c",
    "cooling_effectiveness", "hashrate_ratio", "voltage_deviation",
    # Fleet-relative
    "temperature_c_fleet_z", "power_w_fleet_z",
    "hashrate_th_fleet_z", "efficiency_jth_fleet_z",
    # Site conditions
    "ambient_temp_c", "energy_price_kwh",
]
# Available but not yet included: hashrate_th_mean_30m, hashrate_th_std_30m
# (30-min rolling hashrate, approximating MOS hashrate_30m resolution).
# These features are computed in features.py and present in the parquet.
# Include in future model iterations once we evaluate their predictive lift
# for hashrate_decay detection — the 30m window may capture gradual degradation
# patterns better than the 1h window.

LABEL_COL = "label_any_anomaly"

ANOMALY_TYPES = {
    "thermal_deg": "label_thermal_deg",
    "psu_instability": "label_psu_instability",
    "hashrate_decay": "label_hashrate_decay",
}


def prepare_data(df: pd.DataFrame):
    """Filter to active samples and prepare features/labels."""
    mask = df["true_efficiency"].notna() & (df["hashrate_th"] > 0)
    df_active = df[mask].copy()

    available = [c for c in FEATURE_COLS if c in df_active.columns]
    X = df_active[available].fillna(0).replace([np.inf, -np.inf], 0)
    y = df_active[LABEL_COL].astype(int)

    return df_active, X, y, available


def time_based_split(df: pd.DataFrame, X: pd.DataFrame, y: pd.Series,
                     train_ratio: float = 0.7):
    """Split by time to prevent data leakage."""
    timestamps = pd.to_datetime(df["timestamp"])
    cutoff = timestamps.quantile(train_ratio)

    train_mask = timestamps <= cutoff
    test_mask = ~train_mask

    return (X[train_mask], X[test_mask],
            y[train_mask], y[test_mask],
            df[train_mask], df[test_mask])


def train_classifier(X_train, y_train, X_test, y_test, name="any_anomaly"):
    """Train XGBoost binary classifier. Returns model + metrics."""
    n_neg = (y_train == 0).sum()
    n_pos = max((y_train == 1).sum(), 1)

    model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        scale_pos_weight=n_neg / n_pos,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, zero_division=0)

    print(f"[{name}] Accuracy: {acc:.4f}  F1: {f1:.4f}")
    print(classification_report(y_test, y_pred,
                                target_names=["healthy", "anomaly"],
                                zero_division=0))

    return model, acc, f1


def get_feature_importance(model, feature_names: list, top_n: int = 15) -> list:
    """Extract top feature importances."""
    importances = model.feature_importances_
    indices = np.argsort(importances)[::-1][:top_n]
    return [
        {"feature": feature_names[i], "importance": round(float(importances[i]), 4)}
        for i in indices
    ]


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_parquet("kpi_timeseries.parquet")
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # ── Prepare ──────────────────────────────────────────────────────────
    df_active, X, y, feature_names = prepare_data(df)
    X_train, X_test, y_train, y_test, df_train, df_test = time_based_split(
        df_active, X, y
    )

    print(f"Train: {len(X_train):,} samples ({y_train.mean():.1%} anomaly)")
    print(f"Test:  {len(X_test):,} samples ({y_test.mean():.1%} anomaly)")

    # ── Train primary model (any anomaly) ────────────────────────────────
    model, acc, f1 = train_classifier(X_train, y_train, X_test, y_test, "any_anomaly")
    top_features = get_feature_importance(model, feature_names)

    # ── Train per-anomaly-type classifiers ───────────────────────────────
    print("Per-anomaly-type classifiers:")
    per_anomaly = {}
    for anomaly_name, label_col in ANOMALY_TYPES.items():
        y_tr = df_train[label_col].astype(int)
        y_te = df_test[label_col].astype(int)

        if y_tr.sum() == 0:
            print(f"  {anomaly_name}: no positive samples in train — skipped")
            per_anomaly[anomaly_name] = {"skipped": True, "reason": "no train positives"}
            continue

        sub_model, sub_acc, sub_f1 = train_classifier(
            X_train, y_tr, X_test, y_te, anomaly_name
        )
        sub_feats = get_feature_importance(sub_model, feature_names, top_n=5)
        per_anomaly[anomaly_name] = {
            "accuracy": round(sub_acc, 4),
            "f1_score": round(sub_f1, 4),
            "test_positives": int(y_te.sum()),
            "top_features": sub_feats,
        }

    # ── Save model artifact ──────────────────────────────────────────────
    model_artifact = {
        "model": model,
        "feature_names": feature_names,
        "threshold": 0.5,
    }
    joblib.dump(model_artifact, "anomaly_model.joblib")
    print(f"\nModel saved: anomaly_model.joblib")

    # ── Write metrics ────────────────────────────────────────────────────
    metrics = {
        "model": "XGBClassifier",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "accuracy": round(acc, 4),
        "f1_score": round(f1, 4),
        "top_features": top_features,
        "per_anomaly_type": per_anomaly,
        "threshold": 0.5,
    }

    with open("model_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "model_accuracy": round(acc, 4),
            "model_f1": round(f1, 4),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        }, f)


if __name__ == "__main__":
    main()
