#!/usr/bin/env python3
"""
Task 4c: Cost Projection — Economic Cost Modeling
==================================================
Computes expected cost of each possible action per device over configurable
horizons, then selects the action that minimizes total cost.

This is Phase 4 of the fleet intelligence pipeline: cost-driven decision making.
Devices at WARNING might be cheaper to keep running than to shut down during peak
pricing — this task makes that explicit with dollar values.

Failure model: Weibull distribution (shape=2.5, increasing hazard rate).
Scale parameter inversely proportional to risk score. At risk=1.0, scale=168h
(63.2% failure probability within 1 week). This models the "bathtub curve"
wear-out region typical of ASIC semiconductors under thermal stress.

Phase 3 stub: loads trend_analysis.json if present. When absent, uses risk-only
Weibull. When Phase 3 lands, trend slopes adjust effective risk (weighted by
R² confidence) — zero code changes needed in this file.

6 actions evaluated per device:
    do_nothing          — current energy + risk cost - revenue
    underclock_90pct    — reduced power (P ∝ f^2.5) + reduced hashrate (H ∝ f)
    underclock_80pct    — deeper underclock, greater life extension
    underclock_70pct    — maximum underclock, maximum life extension
    schedule_maintenance — repair cost + downtime, then restored performance
    shutdown            — opportunity cost only, zero risk

Inputs:  fleet_risk_scores.json, fleet_metadata.json, cost_model.json,
         kpi_timeseries.parquet, [optional] trend_analysis.json
Outputs: cost_projections.json
Vars:    fleet_hourly_profit_usd, devices_with_negative_profit, avg_horizon_24h_net_usd
"""

import json
import math
import os

import pandas as pd
import numpy as np

# Weibull shape parameter: >1 means increasing failure rate (wear-out).
# 2.5 is typical for semiconductor aging under thermal cycling.
# Source: reliability engineering literature for power semiconductor devices.
WEIBULL_SHAPE = 2.5

# At risk=1.0, scale=168h → P(fail) = 1 - exp(-(168/168)^2.5) ≈ 63.2%
# This calibrates the model so a maximally-risky device has ~63% failure
# probability within one week — conservative for production use.
WEIBULL_BASE_SCALE_HOURS = 168.0

# ASIC power scaling: P ∝ f^2.5 (frequency^2.5). This approximation accounts
# for the V/f coupling in modern ASICs — when frequency drops, voltage drops
# proportionally (via the ASIC's V/f curve), and power = C * V² * f.
# With V ∝ f, P ∝ f³ theoretically, but empirical measurements on S19/S21
# show ~f^2.5 due to static power and cooling overhead not scaling with clock.
POWER_FREQUENCY_EXPONENT = 2.5

# Underclock life extension: reducing clock speed by X% extends the Weibull
# scale parameter. At 70% clock, thermal stress drops significantly, extending
# mean time to failure. Factor = 1 / (clock_fraction^1.5) — empirical fit
# from accelerated life testing data on ASIC miners.
LIFE_EXTENSION_EXPONENT = 1.5

# Trend adjustment sensitivity: how much Phase 3 trend slopes affect
# effective risk. Capped to prevent runaway adjustments from noisy slopes.
TREND_SLOPE_WEIGHT = 0.5
TREND_MAX_ADJUSTMENT = 0.3
TREND_REGIME_CHANGE_MULTIPLIER = 1.2


def validate_cost_model(cm: dict) -> None:
    """Schema validation of cost_model.json. Raises ValueError on bad data."""
    required_sections = ["energy", "revenue", "maintenance", "downtime",
                         "failure", "fleet_constraints", "horizons_hours",
                         "underclock_levels"]
    for section in required_sections:
        if section not in cm:
            raise ValueError(f"cost_model.json missing required section: '{section}'")

    # Energy
    energy = cm["energy"]
    for key in ["base_rate_kwh", "peak_rate_kwh", "peak_hours"]:
        if key not in energy:
            raise ValueError(f"cost_model.energy missing '{key}'")
    if energy["base_rate_kwh"] <= 0 or energy["peak_rate_kwh"] <= 0:
        raise ValueError("Energy rates must be positive")
    if not isinstance(energy["peak_hours"], list):
        raise ValueError("peak_hours must be a list of integers")

    # Revenue
    rev = cm["revenue"]
    for key in ["btc_price_usd", "network_difficulty", "pool_fee_pct", "block_reward_btc"]:
        if key not in rev:
            raise ValueError(f"cost_model.revenue missing '{key}'")
    if rev["btc_price_usd"] <= 0:
        raise ValueError("btc_price_usd must be positive")
    if rev["network_difficulty"] <= 0:
        raise ValueError("network_difficulty must be positive")

    # Maintenance
    maint = cm["maintenance"]
    for key in ["inspection_cost_usd", "minor_repair_usd", "major_repair_usd",
                "technician_hourly_usd", "avg_inspection_hours",
                "avg_minor_repair_hours", "avg_major_repair_hours",
                "maintenance_restores_te_to"]:
        if key not in maint:
            raise ValueError(f"cost_model.maintenance missing '{key}'")

    # Horizons
    horizons = cm["horizons_hours"]
    if not isinstance(horizons, list) or len(horizons) == 0:
        raise ValueError("horizons_hours must be a non-empty list")
    for h in horizons:
        if h <= 0:
            raise ValueError(f"Horizon must be positive, got {h}")

    # Underclock levels
    for key, val in cm["underclock_levels"].items():
        if not (0.0 < val < 1.0):
            raise ValueError(f"Underclock level {key}={val} must be in (0, 1)")


def compute_btc_revenue_per_th_hour(cm: dict) -> float:
    """Bitcoin mining revenue per TH/s per hour in USD.

    Derivation:
        blocks_per_hour = 3600 / 600 = 6 (Bitcoin targets 10-min blocks)
        network_hashrate = difficulty * 2^32 / 600  (hashes/second)
        share_per_th = 1e12 / network_hashrate
        revenue_per_th_hour = blocks_per_hour * reward * share * (1 - fee) * price

    At difficulty=119.12T, BTC=$85k, reward=3.125:
        ≈ $0.0018/TH/hr → S21-HYD (335 TH) ≈ $0.60/hr revenue
    """
    rev = cm["revenue"]
    blocks_per_hour = 6.0  # Bitcoin: 10-minute block target
    # Network hashrate in H/s from difficulty
    network_hashrate_hs = rev["network_difficulty"] * (2**32) / 600.0
    # Share of 1 TH/s (= 1e12 H/s) of the network
    share_per_th = 1e12 / network_hashrate_hs
    fee_fraction = rev["pool_fee_pct"] / 100.0
    revenue = (blocks_per_hour * rev["block_reward_btc"] * share_per_th
               * (1.0 - fee_fraction) * rev["btc_price_usd"])
    return revenue


def compute_energy_cost_per_hour(power_w: float, cm: dict, hour_of_day: int = -1) -> float:
    """Energy cost for a device running at given power for one hour.

    Uses time-of-use pricing: peak rate during peak_hours, base rate otherwise.
    When hour_of_day=-1, returns the weighted average across 24 hours for
    horizon-based projections (12 peak + 12 off-peak by default).
    """
    energy = cm["energy"]
    power_kw = power_w / 1000.0

    if hour_of_day >= 0:
        rate = (energy["peak_rate_kwh"] if hour_of_day in energy["peak_hours"]
                else energy["base_rate_kwh"])
        return power_kw * rate

    # Weighted average for projection
    peak_count = len(energy["peak_hours"])
    off_peak_count = 24 - peak_count
    avg_rate = ((peak_count * energy["peak_rate_kwh"]
                 + off_peak_count * energy["base_rate_kwh"]) / 24.0)
    return power_kw * avg_rate


def failure_probability(risk: float, hours: float, trend_data: dict = None) -> float:
    """Weibull CDF: probability of failure within `hours` given current risk.

    P(fail) = 1 - exp(-(t / scale)^shape)

    Scale parameter is inversely proportional to risk:
        scale = WEIBULL_BASE_SCALE_HOURS / effective_risk

    At risk=1.0, scale=168h → P(168h) = 1 - exp(-1) ≈ 0.632
    At risk=0.1, scale=1680h → P(168h) ≈ 0.0003 (very safe)

    Phase 3 integration: when trend_data is provided, the 24h risk slope
    (weighted by R² confidence) adjusts effective risk, capturing the
    velocity of degradation not just the current level.
    """
    if risk <= 0:
        return 0.0

    effective_risk = risk

    # Phase 3 trend adjustment: slope of risk over 24h, weighted by fit quality
    if trend_data is not None:
        risk_trend = trend_data.get("risk_24h", {})
        slope = risk_trend.get("slope", 0.0)
        r_squared = risk_trend.get("r_squared", 0.0)
        # Positive slope = risk increasing → higher effective risk
        adjustment = slope * hours * r_squared * TREND_SLOPE_WEIGHT
        adjustment = max(-TREND_MAX_ADJUSTMENT, min(TREND_MAX_ADJUSTMENT, adjustment))
        effective_risk = max(0.01, min(1.0, effective_risk + adjustment))

        # Regime change detection: sudden shift in degradation pattern
        # adds a 20% risk multiplier as a precautionary buffer
        if trend_data.get("regime_change_detected", False):
            effective_risk = min(1.0, effective_risk * TREND_REGIME_CHANGE_MULTIPLIER)

    scale = WEIBULL_BASE_SCALE_HOURS / effective_risk
    return 1.0 - math.exp(-((hours / scale) ** WEIBULL_SHAPE))


def _compute_single_action(action_name: str, hours: float, risk: float,
                           spec: dict, cm: dict, rev_per_th_hr: float,
                           trend_data: dict = None) -> dict:
    """Compute cost/revenue/risk_cost/net for one action+horizon combination.

    Returns dict with:
        revenue_usd: expected mining revenue over horizon
        energy_cost_usd: electricity cost over horizon
        risk_cost_usd: P(failure) × (repair + downtime revenue loss)
        maintenance_cost_usd: scheduled maintenance costs (if applicable)
        net_usd: revenue - energy - risk_cost - maintenance (positive = profitable)
    """
    nominal_power = spec["nominal_power_w"]
    nominal_hashrate = spec["nominal_hashrate_th"]
    maint = cm["maintenance"]
    fail = cm["failure"]

    if action_name == "do_nothing":
        power = nominal_power
        hashrate = nominal_hashrate
        p_fail = failure_probability(risk, hours, trend_data)
        energy_cost = compute_energy_cost_per_hour(power, cm) * hours
        revenue = rev_per_th_hr * hashrate * hours
        # Expected failure cost: probability × (repair + downtime lost revenue)
        downtime_loss = (rev_per_th_hr * hashrate * fail["catastrophic_downtime_hours"]
                         * cm["downtime"]["opportunity_cost_multiplier"])
        risk_cost = p_fail * (fail["catastrophic_repair_usd"]
                              * fail["cascading_damage_multiplier"] + downtime_loss)
        return {
            "revenue_usd": round(revenue, 2),
            "energy_cost_usd": round(energy_cost, 2),
            "risk_cost_usd": round(risk_cost, 2),
            "maintenance_cost_usd": 0.0,
            "net_usd": round(revenue - energy_cost - risk_cost, 2),
            "p_failure": round(p_fail, 4),
        }

    elif action_name.startswith("underclock_"):
        clock_fraction = cm["underclock_levels"][action_name]
        # Power scales as f^2.5 (V/f coupled ASICs — see module docstring)
        power = nominal_power * (clock_fraction ** POWER_FREQUENCY_EXPONENT)
        # Hashrate scales linearly with frequency
        hashrate = nominal_hashrate * clock_fraction
        # Life extension: lower clock → lower thermal stress → longer MTTF
        life_factor = 1.0 / (clock_fraction ** LIFE_EXTENSION_EXPONENT)
        adjusted_risk = risk / life_factor  # Effective risk drops
        p_fail = failure_probability(adjusted_risk, hours, trend_data)
        energy_cost = compute_energy_cost_per_hour(power, cm) * hours
        revenue = rev_per_th_hr * hashrate * hours
        downtime_loss = (rev_per_th_hr * hashrate * fail["catastrophic_downtime_hours"]
                         * cm["downtime"]["opportunity_cost_multiplier"])
        risk_cost = p_fail * (fail["catastrophic_repair_usd"]
                              * fail["cascading_damage_multiplier"] + downtime_loss)
        return {
            "revenue_usd": round(revenue, 2),
            "energy_cost_usd": round(energy_cost, 2),
            "risk_cost_usd": round(risk_cost, 2),
            "maintenance_cost_usd": 0.0,
            "net_usd": round(revenue - energy_cost - risk_cost, 2),
            "p_failure": round(p_fail, 4),
            "clock_fraction": clock_fraction,
            "power_reduction_pct": round((1 - clock_fraction ** POWER_FREQUENCY_EXPONENT) * 100, 1),
        }

    elif action_name == "schedule_maintenance":
        # Maintenance cost: repair + technician hours + downtime revenue loss
        # Use minor repair for WARNING, major for CRITICAL
        if risk > 0.9:
            repair_cost = maint["major_repair_usd"]
            repair_hours = maint["avg_major_repair_hours"]
        elif risk > 0.5:
            repair_cost = maint["minor_repair_usd"]
            repair_hours = maint["avg_minor_repair_hours"]
        else:
            repair_cost = maint["inspection_cost_usd"]
            repair_hours = maint["avg_inspection_hours"]

        technician_cost = maint["technician_hourly_usd"] * repair_hours
        maintenance_cost = repair_cost + technician_cost

        # Downtime = repair hours (device offline)
        downtime_loss = (rev_per_th_hr * nominal_hashrate * repair_hours
                         * cm["downtime"]["opportunity_cost_multiplier"])

        # After maintenance: restored performance for remaining horizon
        remaining_hours = max(0, hours - repair_hours)
        # Post-maintenance risk is very low (restored to near-new condition)
        restored_risk = risk * (1.0 - maint["maintenance_restores_te_to"])
        p_fail_remaining = failure_probability(restored_risk, remaining_hours, trend_data)

        revenue = rev_per_th_hr * nominal_hashrate * remaining_hours
        energy_cost = compute_energy_cost_per_hour(nominal_power, cm) * remaining_hours

        post_downtime_loss = (rev_per_th_hr * nominal_hashrate
                              * fail["catastrophic_downtime_hours"]
                              * cm["downtime"]["opportunity_cost_multiplier"])
        risk_cost = p_fail_remaining * (fail["catastrophic_repair_usd"]
                                        * fail["cascading_damage_multiplier"]
                                        + post_downtime_loss)

        net = revenue - energy_cost - risk_cost - maintenance_cost - downtime_loss
        return {
            "revenue_usd": round(revenue, 2),
            "energy_cost_usd": round(energy_cost, 2),
            "risk_cost_usd": round(risk_cost, 2),
            "maintenance_cost_usd": round(maintenance_cost + downtime_loss, 2),
            "net_usd": round(net, 2),
            "p_failure": round(p_fail_remaining, 4),
            "repair_hours": repair_hours,
        }

    elif action_name == "shutdown":
        # Shutdown: no revenue, no energy, no risk, only opportunity cost
        opportunity_cost = (rev_per_th_hr * nominal_hashrate * hours
                            * cm["downtime"]["opportunity_cost_multiplier"])
        return {
            "revenue_usd": 0.0,
            "energy_cost_usd": 0.0,
            "risk_cost_usd": 0.0,
            "maintenance_cost_usd": 0.0,
            "net_usd": round(-opportunity_cost, 2),
            "p_failure": 0.0,
            "opportunity_cost_usd": round(opportunity_cost, 2),
        }

    else:
        raise ValueError(f"Unknown action: {action_name}")


def _select_optimal_action(projections: dict, risk: float, te_score: float) -> dict:
    """Pick action maximizing net at risk-appropriate horizon.

    Horizon selection based on urgency:
        - High risk (>0.9): 24h horizon — need immediate action
        - Medium risk (>0.5): 168h (1 week) — plan near-term
        - Low risk (≤0.5): 720h (30 days) — optimize for long-term economics
    """
    if risk > 0.9:
        horizon_key = "24h"
    elif risk > 0.5:
        horizon_key = "168h"
    else:
        horizon_key = "720h"

    # Find best action at the selected horizon
    best_action = None
    best_net = float("-inf")
    for action_name, horizons in projections.items():
        if horizon_key in horizons:
            net = horizons[horizon_key]["net_usd"]
            if net > best_net:
                best_net = net
                best_action = action_name

    # Build rationale
    if best_action is None:
        best_action = "do_nothing"
        best_net = 0.0

    horizon_data = projections.get(best_action, {}).get(horizon_key, {})

    return {
        "recommended_action": best_action,
        "horizon": horizon_key,
        "net_usd": best_net,
        "p_failure": horizon_data.get("p_failure", 0.0),
        "rationale": _build_rationale(best_action, horizon_key, risk, te_score,
                                       projections),
    }


def _build_rationale(action: str, horizon: str, risk: float, te_score: float,
                     projections: dict) -> str:
    """Human-readable explanation of why this action was selected."""
    action_data = projections.get(action, {}).get(horizon, {})
    net = action_data.get("net_usd", 0)

    if action == "do_nothing":
        return (f"Continue operating: net ${net:+.2f}/{horizon}. "
                f"Risk cost acceptable at current risk={risk:.2f}.")
    elif action.startswith("underclock_"):
        pct = int(float(action.split("_")[1].replace("pct", "")))
        do_nothing_net = projections.get("do_nothing", {}).get(horizon, {}).get("net_usd", 0)
        savings = net - do_nothing_net
        return (f"Underclock to {pct}%: net ${net:+.2f}/{horizon} "
                f"(${savings:+.2f} vs do_nothing). "
                f"Lower risk cost outweighs reduced revenue.")
    elif action == "schedule_maintenance":
        return (f"Schedule maintenance: net ${net:+.2f}/{horizon}. "
                f"Repair cost justified by risk reduction at risk={risk:.2f}.")
    elif action == "shutdown":
        return (f"Shutdown recommended: net ${net:+.2f}/{horizon}. "
                f"Device is unprofitable (risk={risk:.2f}, te_score={te_score:.3f}).")
    return f"Action {action}: net ${net:+.2f}/{horizon}"


def load_trend_data(device_id: str, trend_analysis: dict = None) -> dict:
    """Phase 3 stub: extract per-device trend data if available.

    When Phase 3 (trend analysis) is implemented, it outputs trend_analysis.json
    with per-device slope/R² for risk, temperature, hashrate, etc. This function
    extracts the relevant data for the failure probability model.

    Returns None if trend data unavailable → failure_probability() uses risk-only Weibull.
    """
    if trend_analysis is None:
        return None

    device_trends = trend_analysis.get("device_trends", {}).get(device_id)
    if device_trends is None:
        return None

    return device_trends


def compute_action_costs(device_risk: dict, spec: dict, cm: dict,
                         rev_per_th_hr: float, trend_data: dict = None) -> dict:
    """Evaluate 6 actions × 3 horizons for a single device.

    Returns nested dict: {action_name: {horizon_key: cost_breakdown}}
    """
    risk = device_risk["mean_risk"]
    horizons = cm["horizons_hours"]
    actions = ["do_nothing", "underclock_90pct", "underclock_80pct",
               "underclock_70pct", "schedule_maintenance", "shutdown"]

    projections = {}
    for action in actions:
        projections[action] = {}
        for h in horizons:
            horizon_key = f"{h}h"
            projections[action][horizon_key] = _compute_single_action(
                action, h, risk, spec, cm, rev_per_th_hr, trend_data
            )

    return projections


def main():
    # ── Load inputs ───────────────────────────────────────────────────────
    with open("fleet_risk_scores.json") as f:
        scores = json.load(f)
    with open("fleet_metadata.json") as f:
        meta = json.load(f)
    with open("cost_model.json") as f:
        cost_model = json.load(f)

    validate_cost_model(cost_model)
    print("Cost model validated successfully")

    # Phase 3 stub: load trend analysis if available
    trend_analysis = None
    if os.path.exists("trend_analysis.json"):
        with open("trend_analysis.json") as f:
            trend_analysis = json.load(f)
        print("Trend analysis loaded — failure model will use trend-adjusted risk")
    else:
        print("No trend_analysis.json found — using risk-only Weibull failure model")

    # ── Build spec lookup ─────────────────────────────────────────────────
    specs = {}
    for dev in meta["fleet"]:
        specs[dev["device_id"]] = {
            "nominal_hashrate_th": dev["nominal_hashrate_th"],
            "nominal_power_w": dev["nominal_power_w"],
            "model": dev.get("model", "unknown"),
        }

    # ── Compute revenue baseline ──────────────────────────────────────────
    rev_per_th_hr = compute_btc_revenue_per_th_hour(cost_model)
    print(f"BTC revenue: ${rev_per_th_hr:.6f}/TH/hr")

    # Sanity check: S21-HYD (335 TH) should earn ~$0.60/hr
    s21_hourly = rev_per_th_hr * 335.0
    print(f"  S21-HYD (335 TH/s) hourly revenue: ${s21_hourly:.2f}")

    # ── Project costs per device ──────────────────────────────────────────
    device_projections = []
    fleet_hourly_profit = 0.0
    negative_profit_count = 0

    for device_risk in scores["device_risks"]:
        device_id = device_risk["device_id"]
        spec = specs.get(device_id)
        if not spec:
            print(f"Warning: no specs for {device_id}, skipping")
            continue

        # Per-device trend data (Phase 3 stub)
        trend_data = load_trend_data(device_id, trend_analysis)

        # Evaluate all actions × horizons
        projections = compute_action_costs(device_risk, spec, cost_model,
                                           rev_per_th_hr, trend_data)

        # Select optimal action
        te_score = device_risk["latest_snapshot"]["te_score"]
        optimal = _select_optimal_action(projections, device_risk["mean_risk"],
                                          te_score)

        # Compute current hourly profit (do_nothing at 1-hour extrapolation)
        hourly_energy = compute_energy_cost_per_hour(spec["nominal_power_w"], cost_model)
        hourly_revenue = rev_per_th_hr * spec["nominal_hashrate_th"]
        hourly_profit = hourly_revenue - hourly_energy

        if hourly_profit < 0:
            negative_profit_count += 1
        fleet_hourly_profit += hourly_profit

        device_proj = {
            "device_id": device_id,
            "model": spec["model"],
            "risk_score": device_risk["mean_risk"],
            "te_score": te_score,
            "hourly_revenue_usd": round(hourly_revenue, 4),
            "hourly_energy_cost_usd": round(hourly_energy, 4),
            "hourly_profit_usd": round(hourly_profit, 4),
            "optimal": optimal,
            "projections": projections,
            "trend_data_available": trend_data is not None,
        }
        device_projections.append(device_proj)

        # Print summary
        action_emoji = {
            "do_nothing": "▶", "shutdown": "⏹",
            "schedule_maintenance": "🔧",
        }
        emoji = action_emoji.get(optimal["recommended_action"], "⏬")
        print(f"  {emoji} {device_id} ({spec['model']}): "
              f"profit=${hourly_profit:.2f}/hr, "
              f"action={optimal['recommended_action']} "
              f"(net=${optimal['net_usd']:+.2f}/{optimal['horizon']})")

    # ── Compute fleet-level 24h net ───────────────────────────────────────
    total_24h_net = sum(
        dp["projections"]["do_nothing"]["24h"]["net_usd"]
        for dp in device_projections
    )
    avg_24h_net = total_24h_net / max(len(device_projections), 1)

    print(f"\nFleet summary:")
    print(f"  Hourly profit: ${fleet_hourly_profit:.2f}")
    print(f"  Devices with negative profit: {negative_profit_count}")
    print(f"  Average 24h net per device: ${avg_24h_net:.2f}")

    # ── Write outputs ─────────────────────────────────────────────────────
    output = {
        "cost_model_version": cost_model["version"],
        "btc_price_usd": cost_model["revenue"]["btc_price_usd"],
        "revenue_per_th_hr_usd": round(rev_per_th_hr, 6),
        "fleet_hourly_profit_usd": round(fleet_hourly_profit, 2),
        "fleet_daily_profit_usd": round(fleet_hourly_profit * 24, 2),
        "devices_with_negative_profit": negative_profit_count,
        "trend_analysis_available": trend_analysis is not None,
        "device_projections": device_projections,
    }

    with open("cost_projections.json", "w") as f:
        json.dump(output, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "fleet_hourly_profit_usd": round(fleet_hourly_profit, 2),
            "devices_with_negative_profit": negative_profit_count,
            "avg_horizon_24h_net_usd": round(avg_24h_net, 2),
        }, f)

    print(f"\nCost projections written: {len(device_projections)} devices")


if __name__ == "__main__":
    main()
