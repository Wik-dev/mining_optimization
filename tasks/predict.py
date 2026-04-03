#!/usr/bin/env python3
"""
Task 4: Predict Failures
========================
XGBoost classifier trained on TE decomposition features + rolling stats
to predict anomaly onset per device.

Uses time-based train/test split (first 70% train, last 30% test)
to prevent data leakage.

Inputs:  kpi_timeseries.parquet, fleet_metadata.json
Outputs: failure_predictions.json
Vars:    flagged_devices, model_accuracy, model_f1
"""

import json
import warnings
import pandas as pd
import numpy as np
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

LABEL_COL = "label_any_anomaly"

# Per-anomaly-type labels for multi-output predictions
ANOMALY_TYPES = {
    "thermal_deg": "label_thermal_deg",
    "psu_instability": "label_psu_instability",
    "hashrate_decay": "label_hashrate_decay",
}


def prepare_data(df: pd.DataFrame):
    """Filter to active samples and prepare features/labels."""
    # Only active (non-idle) samples with valid TE
    mask = df["true_efficiency"].notna() & (df["hashrate_th"] > 0)
    df_active = df[mask].copy()

    # Ensure feature columns exist (drop any missing)
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


def train_anomaly_detector(X_train, y_train, X_test, y_test):
    """Train XGBoost binary classifier for any-anomaly detection."""
    # Handle class imbalance with scale_pos_weight
    n_neg = (y_train == 0).sum()
    n_pos = max((y_train == 1).sum(), 1)
    scale = n_neg / n_pos

    model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        scale_pos_weight=scale,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    acc = accuracy_score(y_test, y_pred)
    f1 = f1_score(y_test, y_pred, zero_division=0)

    print(f"Accuracy: {acc:.4f}  F1: {f1:.4f}")
    print(classification_report(y_test, y_pred, target_names=["healthy", "anomaly"],
                                zero_division=0))

    return model, y_pred, y_proba, acc, f1


def compute_device_risk(df_test: pd.DataFrame, y_proba: np.ndarray,
                        threshold: float = 0.5) -> list:
    """Aggregate prediction probabilities to per-device risk scores."""
    df_risk = df_test[["device_id", "timestamp"]].copy()
    df_risk["anomaly_prob"] = y_proba

    # Sort by timestamp so "last" aggregation returns the most recent reading
    df_risk = df_risk.sort_values("timestamp")

    device_risk = df_risk.groupby("device_id").agg(
        mean_risk=("anomaly_prob", "mean"),
        max_risk=("anomaly_prob", "max"),
        pct_flagged=("anomaly_prob", lambda x: (x > threshold).mean()),
        last_risk=("anomaly_prob", "last"),
    ).round(4)

    device_risk["flagged"] = device_risk["mean_risk"] > threshold
    device_risk = device_risk.sort_values("mean_risk", ascending=False)

    return device_risk.reset_index().to_dict(orient="records")


def train_per_anomaly_classifiers(X_train, X_test, df_train, df_test,
                                   feature_names: list) -> dict:
    """Train separate classifiers for each anomaly type."""
    results = {}
    for anomaly_name, label_col in ANOMALY_TYPES.items():
        y_train = df_train[label_col].astype(int)
        y_test = df_test[label_col].astype(int)

        # Skip if no positive samples in train set
        if y_train.sum() == 0:
            print(f"  {anomaly_name}: no positive samples in train — skipped")
            results[anomaly_name] = {"skipped": True, "reason": "no train positives"}
            continue

        n_neg = (y_train == 0).sum()
        n_pos = max(y_train.sum(), 1)

        model = XGBClassifier(
            n_estimators=150,
            max_depth=5,
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

        # Top 5 features for this anomaly type
        importances = model.feature_importances_
        top_idx = np.argsort(importances)[::-1][:5]
        top_feats = [
            {"feature": feature_names[i], "importance": round(float(importances[i]), 4)}
            for i in top_idx
        ]

        print(f"  {anomaly_name}: acc={acc:.4f}  f1={f1:.4f}")
        results[anomaly_name] = {
            "accuracy": round(acc, 4),
            "f1_score": round(f1, 4),
            "test_positives": int(y_test.sum()),
            "top_features": top_feats,
        }

    return results


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

    # ── Train ────────────────────────────────────────────────────────────
    model, y_pred, y_proba, acc, f1 = train_anomaly_detector(
        X_train, y_train, X_test, y_test
    )

    # ── Device-level risk ────────────────────────────────────────────────
    device_risks = compute_device_risk(df_test, y_proba)
    flagged = sum(1 for d in device_risks if d["flagged"])

    print(f"\nFlagged devices: {flagged}/{len(device_risks)}")
    for d in device_risks:
        flag = " ** FLAGGED **" if d["flagged"] else ""
        print(f"  {d['device_id']}: mean_risk={d['mean_risk']:.3f}{flag}")

    # ── Feature importance ───────────────────────────────────────────────
    top_features = get_feature_importance(model, feature_names)

    # ── Per-anomaly-type classifiers ─────────────────────────────────────
    print("\nPer-anomaly-type classifiers:")
    per_anomaly = train_per_anomaly_classifiers(
        X_train, X_test, df_train, df_test, feature_names
    )

    # ── Write outputs ────────────────────────────────────────────────────
    predictions = {
        "model": "XGBClassifier",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "accuracy": round(acc, 4),
        "f1_score": round(f1, 4),
        "device_risks": device_risks,
        "top_features": top_features,
        "per_anomaly_type": per_anomaly,
        "threshold": 0.5,
    }

    with open("failure_predictions.json", "w") as f:
        json.dump(predictions, f, indent=2, default=str)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "flagged_devices": flagged,
            "model_accuracy": round(acc, 4),
            "model_f1": round(f1, 4),
        }, f)


if __name__ == "__main__":
    main()
