#!/usr/bin/env python3
"""
Retrain Monitor — Auto-Retraining Decision Logic
==================================================
Standalone module that evaluates whether the regression model should be
retrained. Not a DAG task yet — called for validation during training,
and later by Phase 2's simulation loop or a scheduled monitor.

Three trigger conditions (any fires → recommend retrain):

1. Rolling RMSE drift: p50 RMSE exceeds 2× baseline for 3+ consecutive cycles
2. Calibration drift: actuals outside 80% interval (p10–p90) >30% of the time
3. Fleet regime shift: KS-test on residual distribution vs training residuals
   fires for >20% of fleet devices

Inputs:  prediction_log.json (scored predictions with actuals),
         model_registry.json
Outputs: retrain_decision.json
"""

import json
import os
from datetime import datetime

import numpy as np

# Deferred import: scipy may not be available in all environments.
# Only imported when detect_regime_shift is called.


# ── Trigger thresholds ───────────────────────────────────────────────────
# These thresholds are calibrated conservatively. False positives (unnecessary
# retrains) waste compute but don't harm predictions. False negatives (missed
# drift) let stale models serve bad forecasts. Err on the side of retraining.

RMSE_MULTIPLIER = 2.0       # retrain if current RMSE > 2× baseline RMSE
RMSE_CONSECUTIVE = 3         # must exceed threshold for 3 consecutive cycles
CALIBRATION_THRESHOLD = 0.30 # retrain if >30% of actuals fall outside 80% interval
KS_PVALUE = 0.05             # KS-test p-value for significant distribution shift
FLEET_SHIFT_FRACTION = 0.20  # retrain if >20% of devices show regime shift


def compute_rolling_rmse(predictions, actuals, window_size=50):
    """Compute rolling RMSE over a sliding window of prediction/actual pairs.

    Returns array of RMSE values, one per window position.
    Used to detect gradual model degradation over time.
    """
    predictions = np.array(predictions)
    actuals = np.array(actuals)
    residuals = predictions - actuals

    if len(residuals) < window_size:
        # Not enough data for rolling window — return single RMSE
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        return np.array([rmse])

    n_windows = len(residuals) - window_size + 1
    rolling = np.zeros(n_windows)
    for i in range(n_windows):
        w = residuals[i:i + window_size]
        rolling[i] = np.sqrt(np.mean(w ** 2))

    return rolling


def check_calibration(predictions_p10, predictions_p90, actuals):
    """Check what fraction of actuals fall within the [p10, p90] interval.

    Target: 80% ± 5%. If coverage drops below 70% (i.e., >30% outside),
    the model's uncertainty estimates are unreliable and retraining is needed.

    Returns dict with coverage fraction and whether the trigger fired.
    """
    p10 = np.array(predictions_p10)
    p90 = np.array(predictions_p90)
    actual = np.array(actuals)

    in_interval = ((actual >= p10) & (actual <= p90)).mean()
    outside_fraction = 1.0 - in_interval

    return {
        "coverage_80": round(float(in_interval), 4),
        "outside_fraction": round(float(outside_fraction), 4),
        "trigger_fired": outside_fraction > CALIBRATION_THRESHOLD,
    }


def detect_regime_shift(current_residuals, baseline_residuals):
    """Detect distribution shift in residuals using Kolmogorov-Smirnov test.

    Compares current prediction residuals against training-time residuals.
    A significant KS statistic (p < 0.05) indicates the data distribution
    has shifted — the model was trained on a different regime.

    Returns dict with KS statistic, p-value, and whether shift detected.
    """
    from scipy.stats import ks_2samp

    current = np.array(current_residuals)
    baseline = np.array(baseline_residuals)

    if len(current) < 10 or len(baseline) < 10:
        return {
            "ks_statistic": None,
            "p_value": None,
            "shift_detected": False,
            "reason": "insufficient samples",
        }

    stat, pvalue = ks_2samp(current, baseline)

    return {
        "ks_statistic": round(float(stat), 4),
        "p_value": round(float(pvalue), 6),
        "shift_detected": pvalue < KS_PVALUE,
    }


def evaluate(prediction_log_path, registry_path="model_registry.json"):
    """Run all three retrain triggers and produce a decision.

    The prediction_log is expected to contain per-device prediction/actual
    pairs accumulated over scoring cycles:

    {
        "horizon": "1h",
        "devices": {
            "ASIC-001": {
                "predictions_p10": [...],
                "predictions_p50": [...],
                "predictions_p90": [...],
                "actuals": [...],
                "baseline_residuals": [...]  # from training time
            },
            ...
        }
    }

    Returns retrain_decision dict.
    """
    if not os.path.exists(prediction_log_path):
        return {
            "should_retrain": False,
            "triggers_fired": [],
            "reason": "no prediction log available",
            "evaluated_at": datetime.now(datetime.timezone.utc).isoformat(),
        }

    with open(prediction_log_path) as f:
        log = json.load(f)

    devices = log.get("devices", {})
    if not devices:
        return {
            "should_retrain": False,
            "triggers_fired": [],
            "reason": "empty prediction log",
            "evaluated_at": datetime.now(datetime.timezone.utc).isoformat(),
        }

    # Load baseline RMSE from registry
    baseline_rmse = None
    if os.path.exists(registry_path):
        with open(registry_path) as f:
            registry = json.load(f)
        active_version = registry.get("active_version")
        if active_version:
            active_entry = next(
                (v for v in registry["versions"] if v["version"] == active_version),
                None
            )
            if active_entry:
                baseline_rmse = active_entry.get("avg_rmse_p50")

    triggers_fired = []
    details = {}

    # ── Trigger 1: Rolling RMSE drift ────────────────────────────────────
    if baseline_rmse is not None:
        rmse_threshold = baseline_rmse * RMSE_MULTIPLIER
        rmse_exceeded_counts = []

        for device_id, device_data in devices.items():
            preds = device_data.get("predictions_p50", [])
            actuals = device_data.get("actuals", [])
            if len(preds) < 10 or len(actuals) < 10:
                continue

            rolling = compute_rolling_rmse(preds, actuals)
            # Count consecutive windows exceeding threshold
            consecutive = 0
            max_consecutive = 0
            for rmse_val in rolling:
                if rmse_val > rmse_threshold:
                    consecutive += 1
                    max_consecutive = max(max_consecutive, consecutive)
                else:
                    consecutive = 0
            rmse_exceeded_counts.append(max_consecutive)

        if rmse_exceeded_counts and max(rmse_exceeded_counts) >= RMSE_CONSECUTIVE:
            triggers_fired.append("rolling_rmse_drift")
            details["rolling_rmse"] = {
                "baseline_rmse": baseline_rmse,
                "threshold": rmse_threshold,
                "max_consecutive_exceedances": max(rmse_exceeded_counts),
                "devices_evaluated": len(rmse_exceeded_counts),
            }

    # ── Trigger 2: Calibration drift ─────────────────────────────────────
    all_p10, all_p90, all_actuals = [], [], []
    for device_data in devices.values():
        all_p10.extend(device_data.get("predictions_p10", []))
        all_p90.extend(device_data.get("predictions_p90", []))
        all_actuals.extend(device_data.get("actuals", []))

    if len(all_actuals) >= 20:
        cal = check_calibration(all_p10, all_p90, all_actuals)
        details["calibration"] = cal
        if cal["trigger_fired"]:
            triggers_fired.append("calibration_drift")

    # ── Trigger 3: Fleet regime shift ────────────────────────────────────
    shifted_devices = 0
    total_evaluated = 0
    shift_details = {}

    for device_id, device_data in devices.items():
        current_resid = device_data.get("current_residuals", [])
        baseline_resid = device_data.get("baseline_residuals", [])

        if len(current_resid) < 10 or len(baseline_resid) < 10:
            continue

        total_evaluated += 1
        result = detect_regime_shift(current_resid, baseline_resid)
        shift_details[device_id] = result
        if result["shift_detected"]:
            shifted_devices += 1

    if total_evaluated > 0:
        shift_fraction = shifted_devices / total_evaluated
        details["regime_shift"] = {
            "shifted_devices": shifted_devices,
            "total_evaluated": total_evaluated,
            "shift_fraction": round(shift_fraction, 4),
            "threshold": FLEET_SHIFT_FRACTION,
            "per_device": shift_details,
        }
        if shift_fraction > FLEET_SHIFT_FRACTION:
            triggers_fired.append("fleet_regime_shift")

    # ── Decision ─────────────────────────────────────────────────────────
    decision = {
        "should_retrain": len(triggers_fired) > 0,
        "triggers_fired": triggers_fired,
        "details": details,
        "evaluated_at": datetime.now(datetime.timezone.utc).isoformat(),
    }

    return decision


def main():
    """CLI entry point for standalone evaluation."""
    decision = evaluate("prediction_log.json")

    with open("retrain_decision.json", "w") as f:
        json.dump(decision, f, indent=2)

    if decision["should_retrain"]:
        print(f"RETRAIN RECOMMENDED — triggers: {', '.join(decision['triggers_fired'])}")
    else:
        print("No retrain needed — all metrics within bounds")

    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
