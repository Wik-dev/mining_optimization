#!/usr/bin/env python3
"""
Task 4a: Train Anomaly Model + Multi-Horizon Quantile Regression
=================================================================
Trains XGBoost classifiers on TE decomposition features + rolling stats (Phase 1),
then trains multi-horizon quantile regressors predicting future TE_score at
t+1h, t+6h, t+24h, t+7d with uncertainty bounds (p10/p50/p90) (Phase 5).

The classifier answers "is this device anomalous now?" while the regressor
answers "what will this device's TE_score be at each future horizon?"

Uses time-based train/test split (first 70% train, last 30% test).

Inputs:  kpi_timeseries.parquet, fleet_metadata.json
Outputs: anomaly_model.joblib, regression_model_v{N}.joblib,
         model_metrics.json, model_registry.json
Vars:    model_accuracy, model_f1, train_samples, test_samples,
         regression_rmse_1h, regression_rmse_24h, calibration_80_avg, model_version
"""

import json
import os
import warnings
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import joblib
from sklearn.metrics import accuracy_score, f1_score, classification_report, mean_squared_error
from xgboost import XGBClassifier, XGBRegressor

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

# ── Phase 5: Multi-horizon quantile regression constants ─────────────────
# Horizons in number of 5-minute samples. Mining telemetry arrives at 5-min
# intervals (standard MOS polling frequency). Predicting at these horizons
# lets the controller schedule maintenance windows proactively.
HORIZONS = {
    "1h": 12,      # 12 × 5min = 1 hour
    "6h": 72,      # 72 × 5min = 6 hours
    "24h": 288,    # 288 × 5min = 24 hours
    "7d": 2016,    # 2016 × 5min = 7 days
}

# Quantile levels for uncertainty bounds. p10/p90 define the 80% prediction
# interval; p50 is the median forecast. Separately trained XGBRegressors
# because XGBoost's reg:quantile supports one quantile_alpha per model.
QUANTILES = [0.10, 0.50, 0.90]

# Autoregressive features computed from te_score history. These give the
# regressor a temporal signal without depending on Phase 3's trend_analysis.py.
# When Phase 3 lands, its richer features (CUSUM regime flags, multi-window
# slopes) become additional columns — no changes needed here.
TEMPORAL_FEATURES = [
    "te_score_lag_1h",           # te_score shifted back 12 samples
    "te_score_lag_6h",           # te_score shifted back 72 samples
    "te_score_lag_24h",          # te_score shifted back 288 samples
    "te_score_slope_1h",         # linear trend over last 1h window
    "te_score_slope_6h",         # linear trend over last 6h window
    "te_score_volatility_24h",   # rolling std over last 24h window
]


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


# ── Phase 5: Temporal feature engineering ────────────────────────────────

def add_temporal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute autoregressive TE features per device.

    These features give the regressor temporal context without depending on
    Phase 3's trend_analysis.py. Per-device grouping prevents cross-device
    leakage (device A's history doesn't inform device B's prediction).

    Features:
    - Lagged TE scores (1h, 6h, 24h lookback)
    - Linear trend slopes (1h, 6h windows via rolling polyfit)
    - 24h volatility (rolling standard deviation)
    """
    df = df.sort_values(["device_id", "timestamp"]).copy()

    groups = []
    for _device_id, group in df.groupby("device_id"):
        g = group.copy()
        te = g["te_score"]

        # Lagged values: what was TE_score N samples ago?
        g["te_score_lag_1h"] = te.shift(12)
        g["te_score_lag_6h"] = te.shift(72)
        g["te_score_lag_24h"] = te.shift(288)

        # Rolling linear slope over 1h (12 samples): captures short-term trend
        # direction. Positive = improving, negative = degrading.
        g["te_score_slope_1h"] = _rolling_slope(te, window=12)

        # Rolling linear slope over 6h (72 samples): captures medium-term trend.
        g["te_score_slope_6h"] = _rolling_slope(te, window=72)

        # Rolling volatility over 24h: high volatility indicates unstable device
        # behavior (PSU instability, thermal cycling). 288 samples = 24h at 5-min.
        g["te_score_volatility_24h"] = te.rolling(window=288, min_periods=12).std()

        groups.append(g)

    return pd.concat(groups, ignore_index=True)


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """Compute rolling linear regression slope.

    Uses numpy polyfit on each window. Returns slope (units: TE_score change
    per sample, i.e., per 5 minutes). A slope of -0.001 means TE_score drops
    ~0.001 every 5 minutes = ~0.012/hour.
    """
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


def create_regression_targets(df: pd.DataFrame) -> pd.DataFrame:
    """Create forward-shifted TE_score targets per device.

    For each horizon, the target is "what will te_score be N samples from now?"
    Rows at the end of each device's series get NaN targets (no future data).
    Each horizon trains on its own valid subset, so 7d loses ~23% of 30-day
    data while 1h loses only ~0.1%.
    """
    df = df.sort_values(["device_id", "timestamp"]).copy()

    groups = []
    for _device_id, group in df.groupby("device_id"):
        g = group.copy()
        for horizon_name, offset in HORIZONS.items():
            g[f"target_te_{horizon_name}"] = g["te_score"].shift(-offset)
        groups.append(g)

    return pd.concat(groups, ignore_index=True)


def train_quantile_regressor(X_train, y_train, X_test, y_test,
                             quantile: float, horizon: str):
    """Train a single XGBRegressor for one horizon × quantile combination.

    Uses XGBoost's reg:quantileerror objective which minimizes the pinball loss
    for the specified quantile_alpha. This is equivalent to quantile regression
    but uses gradient boosting instead of linear models.
    """
    model = XGBRegressor(
        objective="reg:quantileerror",
        quantile_alpha=quantile,
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        random_state=42,
        verbosity=0,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(np.mean(np.abs(y_test - y_pred)))

    return model, {"rmse": round(rmse, 6), "mae": round(mae, 6)}


def train_all_regressors(X_train, X_test, targets_train, targets_test,
                         feature_names):
    """Train 12 quantile regressors (4 horizons × 3 quantiles).

    Returns nested dict: {horizon: {quantile_label: model}} and metrics dict.
    Each horizon uses its own valid subset (rows where the target is not NaN).
    """
    models = {}
    metrics = {}

    for horizon_name in HORIZONS:
        target_col = f"target_te_{horizon_name}"
        models[horizon_name] = {}
        metrics[horizon_name] = {}

        # Valid rows for this horizon (no NaN targets)
        train_valid = targets_train[target_col].notna()
        test_valid = targets_test[target_col].notna()

        Xtr = X_train[train_valid]
        ytr = targets_train.loc[train_valid, target_col]
        Xte = X_test[test_valid]
        yte = targets_test.loc[test_valid, target_col]

        print(f"\n[regression {horizon_name}] Train: {len(Xtr):,}  Test: {len(Xte):,}")

        # Skip horizons with insufficient data (e.g., 7d horizon on short datasets)
        if len(Xtr) == 0 or len(Xte) == 0:
            print(f"  Skipping {horizon_name}: insufficient data")
            for quantile in QUANTILES:
                q_label = f"p{int(quantile * 100)}"
                models[horizon_name][q_label] = None
                metrics[horizon_name][q_label] = {"rmse": float("nan"), "mae": float("nan")}
            continue

        for quantile in QUANTILES:
            q_label = f"p{int(quantile * 100)}"
            model, q_metrics = train_quantile_regressor(
                Xtr, ytr, Xte, yte, quantile, horizon_name
            )
            models[horizon_name][q_label] = model
            metrics[horizon_name][q_label] = q_metrics
            print(f"  {q_label}: RMSE={q_metrics['rmse']:.4f}  MAE={q_metrics['mae']:.4f}")

    return models, metrics


def evaluate_calibration(models: dict, X_test, targets_test) -> dict:
    """Check calibration: fraction of actuals within [p10, p90] interval.

    Target coverage for an 80% prediction interval is 75-85%. Under 70%
    indicates overconfident predictions; over 90% indicates overly wide
    intervals (wasted precision).
    """
    calibration = {}

    for horizon_name in HORIZONS:
        target_col = f"target_te_{horizon_name}"
        test_valid = targets_test[target_col].notna()
        Xte = X_test[test_valid]
        yte = targets_test.loc[test_valid, target_col].values

        if len(yte) == 0 or models[horizon_name]["p10"] is None:
            calibration[horizon_name] = {"coverage_80": None, "n_samples": 0}
            continue

        p10_pred = models[horizon_name]["p10"].predict(Xte)
        p90_pred = models[horizon_name]["p90"].predict(Xte)

        # Fraction of actuals falling within [p10, p90]
        in_interval = ((yte >= p10_pred) & (yte <= p90_pred)).mean()
        calibration[horizon_name] = {
            "coverage_80": round(float(in_interval), 4),
            "n_samples": int(len(yte)),
        }
        print(f"[calibration {horizon_name}] 80% interval coverage: {in_interval:.1%} "
              f"(target: 75-85%, n={len(yte):,})")

    return calibration


def get_next_version(registry_path: str) -> int:
    """Read model_registry.json and return next version number."""
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            registry = json.load(f)
        return registry.get("latest_version", 0) + 1
    return 1


def update_registry(registry_path: str, version: int, metrics: dict,
                    calibration: dict, feature_names: list):
    """Update model_registry.json with new version, promote if metrics improve.

    Promotion logic: a new version is promoted to active if its average p50
    RMSE across all horizons is lower than the current active version's.
    First version is auto-promoted.
    """
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            registry = json.load(f)
    else:
        registry = {"versions": [], "active_version": None, "latest_version": 0}

    # Compute summary metrics for this version
    avg_rmse_p50 = np.mean([
        metrics[h]["p50"]["rmse"] for h in HORIZONS
    ])
    avg_calibration = np.mean([
        calibration[h]["coverage_80"]
        for h in HORIZONS if calibration[h]["coverage_80"] is not None
    ])

    version_entry = {
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "avg_rmse_p50": round(float(avg_rmse_p50), 6),
        "avg_calibration_80": round(float(avg_calibration), 4),
        "per_horizon_rmse_p50": {
            h: metrics[h]["p50"]["rmse"] for h in HORIZONS
        },
        "artifact": f"regression_model_v{version}.joblib",
        "feature_count": len(feature_names),
    }

    # Promotion: first version auto-promotes; subsequent versions must beat
    # the active version's avg RMSE (lower is better).
    promote = False
    if registry["active_version"] is None:
        promote = True
    else:
        active = next(
            (v for v in registry["versions"]
             if v["version"] == registry["active_version"]),
            None
        )
        if active is None or avg_rmse_p50 < active["avg_rmse_p50"]:
            promote = True

    if promote:
        registry["active_version"] = version
        version_entry["promoted"] = True
        print(f"\nVersion {version} promoted to active (avg RMSE p50: {avg_rmse_p50:.4f})")
    else:
        version_entry["promoted"] = False
        print(f"\nVersion {version} trained but NOT promoted "
              f"(avg RMSE p50: {avg_rmse_p50:.4f}, active is better)")

    registry["versions"].append(version_entry)
    registry["latest_version"] = version

    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2)

    return promote, version_entry


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

    # ── Save classifier artifact ─────────────────────────────────────────
    model_artifact = {
        "model": model,
        "feature_names": feature_names,
        "threshold": 0.5,
    }
    joblib.dump(model_artifact, "anomaly_model.joblib")
    print(f"\nClassifier saved: anomaly_model.joblib")

    # ── Phase 5: Multi-horizon quantile regression ───────────────────────
    print("\n" + "=" * 60)
    print("Phase 5: Multi-Horizon Quantile Regression")
    print("=" * 60)

    # Add temporal features (autoregressive TE inputs)
    print("\nComputing temporal features...")
    df_temporal = add_temporal_features(df_active)

    # Create forward-shifted regression targets
    print("Creating regression targets...")
    df_temporal = create_regression_targets(df_temporal)

    # Prepare regression feature matrix (original features + temporal)
    reg_feature_names = feature_names + TEMPORAL_FEATURES
    available_reg = [c for c in reg_feature_names if c in df_temporal.columns]
    X_reg = df_temporal[available_reg].fillna(0).replace([np.inf, -np.inf], 0)

    # Time-based split for regression (same 70/30 ratio)
    timestamps = pd.to_datetime(df_temporal["timestamp"])
    cutoff = timestamps.quantile(0.7)
    train_mask = timestamps <= cutoff
    test_mask = ~train_mask

    X_reg_train = X_reg[train_mask]
    X_reg_test = X_reg[test_mask]
    targets_train = df_temporal[train_mask]
    targets_test = df_temporal[test_mask]

    print(f"\nRegression data: {len(X_reg_train):,} train, {len(X_reg_test):,} test")
    print(f"Features: {len(available_reg)} ({len(feature_names)} original + "
          f"{len(available_reg) - len(feature_names)} temporal)")

    # Train 12 quantile regressors (4 horizons × 3 quantiles)
    reg_models, reg_metrics = train_all_regressors(
        X_reg_train, X_reg_test, targets_train, targets_test, available_reg
    )

    # Evaluate calibration (80% prediction interval coverage)
    calibration = evaluate_calibration(reg_models, X_reg_test, targets_test)

    # ── Model versioning ─────────────────────────────────────────────────
    registry_path = "model_registry.json"
    version = get_next_version(registry_path)

    # Save regression artifact
    reg_artifact = {
        "model_type": "multi_horizon_quantile_regression",
        "version": version,
        "horizons": list(HORIZONS.keys()),
        "quantiles": QUANTILES,
        "feature_names": available_reg,
        "models": reg_models,
        "metrics": reg_metrics,
        "calibration": calibration,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact_path = f"regression_model_v{version}.joblib"
    joblib.dump(reg_artifact, artifact_path)
    print(f"\nRegression model saved: {artifact_path}")

    # Update registry (auto-promotes if first version or metrics improve)
    promoted, version_entry = update_registry(
        registry_path, version, reg_metrics, calibration, available_reg
    )

    # ── Write metrics (extended with regression section) ──────────────────
    avg_calibration = np.mean([
        calibration[h]["coverage_80"]
        for h in HORIZONS if calibration[h]["coverage_80"] is not None
    ])

    metrics = {
        "model": "XGBClassifier",
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "accuracy": round(acc, 4),
        "f1_score": round(f1, 4),
        "top_features": top_features,
        "per_anomaly_type": per_anomaly,
        "threshold": 0.5,
        # Phase 5 regression metrics
        "regression": {
            "model_type": "multi_horizon_quantile_regression",
            "version": version,
            "promoted": promoted,
            "horizons": list(HORIZONS.keys()),
            "quantiles": QUANTILES,
            "feature_count": len(available_reg),
            "per_horizon": {
                h: {
                    "rmse_p50": reg_metrics[h]["p50"]["rmse"],
                    "mae_p50": reg_metrics[h]["p50"]["mae"],
                    "calibration_80": calibration[h]["coverage_80"],
                    "test_samples": calibration[h]["n_samples"],
                }
                for h in HORIZONS
            },
            "avg_rmse_p50": round(float(np.nanmean([
                reg_metrics[h]["p50"]["rmse"] for h in HORIZONS
            ])), 6),
            "avg_calibration_80": round(float(avg_calibration), 4),
        },
    }

    with open("model_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "model_accuracy": round(acc, 4),
            "model_f1": round(f1, 4),
            "train_samples": len(X_train),
            "test_samples": len(X_test),
            "regression_rmse_1h": reg_metrics["1h"]["p50"]["rmse"],
            "regression_rmse_24h": reg_metrics["24h"]["p50"]["rmse"],
            "calibration_80_avg": round(float(avg_calibration), 4),
            "model_version": version,
        }, f)


if __name__ == "__main__":
    main()
