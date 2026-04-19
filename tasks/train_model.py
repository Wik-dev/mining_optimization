#!/usr/bin/env python3
"""
Task 4a: Train Anomaly Model + Multi-Horizon Quantile Regression
=================================================================
Trains XGBoost classifiers on TE decomposition features + rolling stats (Phase 1),
then trains multi-horizon quantile regressors predicting future TE_score at
t+1h, t+6h, t+24h, t+7d with uncertainty bounds (p10/p50/p90) (Phase 5).

The classifier answers "is this device anomalous now?" while the regressor
answers "what will this device's TE_score be at each future horizon?"

Training uses 100% of the corpus — there is no internal train/test split.
Evaluation (accuracy, F1, calibration) happens at inference time against
independently generated data. This decoupling ensures:
  - Every anomaly type gets full training coverage
  - No accidental data leakage from temporal splits
  - Model quality is measured on truly unseen data, not a held-out slice

Inputs:  kpi_timeseries.parquet, fleet_metadata.json
Outputs: anomaly_model.joblib, regression_model_v{N}.joblib,
         model_metrics.json, model_registry.json
Vars:    train_samples, anomaly_rate, model_version
"""

import json
import os
import warnings
from datetime import datetime, timezone

import pandas as pd
import numpy as np
import joblib
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
    # Hardware health sensors (6 features) — raw telemetry passthrough.
    # Research: deep-research-report-mining.md, notes_mining_data.md
    # identify these as strongest early warning signals for fan bearing
    # wear, PSU capacitor aging, solder fatigue, and dust fouling.
    "fan_rpm", "voltage_ripple_mv", "reboot_count",
    "chip_count_active", "hashboard_count_active", "dust_index",
    # 7-day rolling windows (5 features) — multi-day baseline for gradual
    # degradation invisible in 24h windows.
    # Research: notes_mining_data.md line 42: "A 3°C rise over a week at
    # constant ambient is a stronger signal than absolute temperature."
    "temperature_c_mean_7d", "temperature_c_dev_7d",
    "power_w_mean_7d", "hashrate_th_mean_7d", "efficiency_jth_mean_7d",
    # Voltage ripple variance — PSU capacitor degradation manifests as
    # increasing variance before the mean shifts.
    # Research: notes_mining_data.md line 44.
    "voltage_ripple_std_24h",
    # Chip dropout ratio — active/nominal, normalized across models.
    # Research: notes_mining_data.md line 13.
    "chip_dropout_ratio",
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
    "fan_bearing_wear": "label_fan_bearing_wear",
    "capacitor_aging": "label_capacitor_aging",
    "dust_fouling": "label_dust_fouling",
    "thermal_paste_deg": "label_thermal_paste_deg",
    "solder_joint_fatigue": "label_solder_joint_fatigue",
    "coolant_loop_fouling": "label_coolant_loop_fouling",
    "firmware_cliff": "label_firmware_cliff",
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
    "te_score_slope_24h",        # linear trend over last 24h window
    "te_score_slope_7d",         # linear trend over last 7d window
]


def prepare_data(df: pd.DataFrame):
    """Filter to active samples and prepare features/labels."""
    mask = df["true_efficiency"].notna() & (df["hashrate_th"] > 0)
    df_active = df[mask].copy()

    available = [c for c in FEATURE_COLS if c in df_active.columns]
    X = df_active[available].fillna(0).replace([np.inf, -np.inf], 0)
    y = df_active[LABEL_COL].astype(int)

    return df_active, X, y, available


def train_classifier(X, y, name="any_anomaly"):
    """Train XGBoost binary classifier on the full corpus.

    No internal evaluation — model quality is assessed at inference time
    against independently generated data. Returns model only.
    """
    n_neg = (y == 0).sum()
    n_pos = max((y == 1).sum(), 1)

    model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        # Compensate for class imbalance: healthy rows typically outnumber
        # anomaly rows. Without this, the model biases toward "healthy."
        scale_pos_weight=n_neg / n_pos,
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )
    model.fit(X, y)

    print(f"[{name}] Trained on {len(X):,} samples "
          f"({int(n_pos):,} positive, {int(n_neg):,} negative, "
          f"ratio={n_pos/len(X):.1%})")

    return model


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

        # 24h slope: captures daily-scale TE trend direction.
        g["te_score_slope_24h"] = _rolling_slope(te, window=288)

        # 7d slope: captures weekly-scale TE trend. Closes the temporal
        # feature / prediction horizon mismatch — the 7d regression target
        # now has a matching-scale slope input.
        # Research: notes_mining_data.md line 44: "efficiency degrades
        # before hashrate drops."
        g["te_score_slope_7d"] = _rolling_slope(te, window=2016)

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


def train_quantile_regressor(X, y, quantile: float, horizon: str):
    """Train a single XGBRegressor for one horizon x quantile combination.

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
    model.fit(X, y)

    return model


def train_all_regressors(X, targets, feature_names):
    """Train 12 quantile regressors (4 horizons x 3 quantiles) on full corpus.

    Returns nested dict: {horizon: {quantile_label: model}} and sample counts.
    Each horizon uses its own valid subset (rows where the target is not NaN —
    the last N rows per device have no future data to predict).
    """
    models = {}
    sample_counts = {}

    for horizon_name in HORIZONS:
        target_col = f"target_te_{horizon_name}"
        models[horizon_name] = {}

        # Valid rows for this horizon (no NaN targets)
        valid = targets[target_col].notna()
        X_valid = X[valid]
        y_valid = targets.loc[valid, target_col]

        print(f"\n[regression {horizon_name}] Training on {len(X_valid):,} samples")
        sample_counts[horizon_name] = len(X_valid)

        # Skip horizons with insufficient data (e.g., 7d horizon on short datasets)
        if len(X_valid) == 0:
            print(f"  Skipping {horizon_name}: insufficient data")
            for quantile in QUANTILES:
                q_label = f"p{int(quantile * 100)}"
                models[horizon_name][q_label] = None
            continue

        for quantile in QUANTILES:
            q_label = f"p{int(quantile * 100)}"
            model = train_quantile_regressor(X_valid, y_valid, quantile, horizon_name)
            models[horizon_name][q_label] = model
            print(f"  {q_label}: trained")

    return models, sample_counts


def get_next_version(registry_path: str) -> int:
    """Read model_registry.json and return next version number."""
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            registry = json.load(f)
        return registry.get("latest_version", 0) + 1
    return 1


def update_registry(registry_path: str, version: int,
                    sample_counts: dict, feature_names: list):
    """Update model_registry.json with new version.

    Without an internal test split, there are no RMSE/calibration metrics to
    compare. Promotion is based on training corpus size — a model trained on
    more data replaces one trained on less. First version is auto-promoted.
    """
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            registry = json.load(f)
    else:
        registry = {"versions": [], "active_version": None, "latest_version": 0}

    total_samples = sum(sample_counts.values())

    version_entry = {
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "total_regression_samples": total_samples,
        "per_horizon_samples": sample_counts,
        "artifact": f"regression_model_v{version}.joblib",
        "feature_count": len(feature_names),
    }

    # Promotion: first version auto-promotes; subsequent versions trained on
    # more data replace the active version.
    promote = False
    if registry["active_version"] is None:
        promote = True
    else:
        active = next(
            (v for v in registry["versions"]
             if v["version"] == registry["active_version"]),
            None
        )
        if active is None or total_samples >= active.get("total_regression_samples", 0):
            promote = True

    if promote:
        registry["active_version"] = version
        version_entry["promoted"] = True
        print(f"\nVersion {version} promoted to active ({total_samples:,} regression samples)")
    else:
        version_entry["promoted"] = False
        print(f"\nVersion {version} trained but NOT promoted")

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
    # Train on 100% of the corpus. No internal split — evaluation happens
    # at inference time against independently generated data.
    df_active, X, y, feature_names = prepare_data(df)
    anomaly_rate = float(y.mean())

    print(f"Training corpus: {len(X):,} samples ({anomaly_rate:.1%} anomaly)")
    print(f"Devices: {df_active['device_id'].nunique()}")
    print(f"Features: {len(feature_names)}")

    # ── Train primary model (any anomaly) ────────────────────────────────
    model = train_classifier(X, y, "any_anomaly")
    top_features = get_feature_importance(model, feature_names)

    # ── Train per-anomaly-type classifiers ───────────────────────────────
    # Each type gets its own binary classifier trained on the full corpus.
    # The per-type models are informational — the primary model (any_anomaly)
    # is what score.py uses for inference. Per-type feature importances help
    # interpret what the model learned for each failure mode.
    print("\nPer-anomaly-type classifiers:")
    per_anomaly = {}
    for anomaly_name, label_col in ANOMALY_TYPES.items():
        y_type = df_active[label_col].astype(int)
        n_positive = int(y_type.sum())

        if n_positive == 0:
            print(f"  {anomaly_name}: no positive samples — skipped")
            per_anomaly[anomaly_name] = {"skipped": True, "reason": "no positives in corpus"}
            continue

        sub_model = train_classifier(X, y_type, anomaly_name)
        sub_feats = get_feature_importance(sub_model, feature_names, top_n=5)

        # How many devices exhibit this anomaly type?
        devices_affected = int(df_active[df_active[label_col] == 1]["device_id"].nunique())

        per_anomaly[anomaly_name] = {
            "train_positives": n_positive,
            "positive_rate": round(n_positive / len(X), 4),
            "devices_affected": devices_affected,
            "top_features": sub_feats,
        }

    # ── Save classifier artifact ─────────────────────────────────────────
    # Threshold 0.3: biased toward recall — in mining, a missed failure (FN)
    # costs far more than an unnecessary inspection (FP).
    CLASSIFIER_THRESHOLD = 0.3
    model_artifact = {
        "model": model,
        "feature_names": feature_names,
        "threshold": CLASSIFIER_THRESHOLD,
    }
    joblib.dump(model_artifact, "anomaly_model.joblib")
    print(f"\nClassifier saved: anomaly_model.joblib")

    # ── Multi-horizon quantile regression ─────────────────────────────────
    print("\n" + "=" * 60)
    print("Multi-Horizon Quantile Regression")
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

    print(f"\nRegression data: {len(X_reg):,} samples")
    print(f"Features: {len(available_reg)} ({len(feature_names)} original + "
          f"{len(available_reg) - len(feature_names)} temporal)")

    # Train 12 quantile regressors (4 horizons × 3 quantiles) on full corpus
    reg_models, reg_sample_counts = train_all_regressors(
        X_reg, df_temporal, available_reg
    )

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
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact_path = f"regression_model_v{version}.joblib"
    joblib.dump(reg_artifact, artifact_path)
    print(f"\nRegression model saved: {artifact_path}")

    # Update registry (first version auto-promotes)
    promoted, version_entry = update_registry(
        registry_path, version, reg_sample_counts, available_reg
    )

    # ── Write metrics (training statistics only) ──────────────────────────
    # No accuracy/F1/calibration — those are inference-time metrics evaluated
    # against independently generated data.
    metrics = {
        "model": "XGBClassifier",
        "train_samples": len(X),
        "anomaly_rate": round(anomaly_rate, 4),
        "devices": int(df_active["device_id"].nunique()),
        "feature_count": len(feature_names),
        "top_features": top_features,
        "per_anomaly_type": per_anomaly,
        "threshold": CLASSIFIER_THRESHOLD,
        "regression": {
            "model_type": "multi_horizon_quantile_regression",
            "version": version,
            "promoted": promoted,
            "horizons": list(HORIZONS.keys()),
            "quantiles": QUANTILES,
            "feature_count": len(available_reg),
            "per_horizon": {
                h: {"train_samples": reg_sample_counts[h]}
                for h in HORIZONS
            },
        },
    }

    with open("model_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "train_samples": len(X),
            "anomaly_rate": round(anomaly_rate, 4),
            "model_version": version,
        }, f)


if __name__ == "__main__":
    main()
