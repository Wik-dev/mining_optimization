#!/usr/bin/env python3
"""
Task 5: Optimize Fleet — The Controller
========================================
Tier-based controller that consumes risk scores and telemetry snapshots,
applies safety overrides, and emits concrete operational commands per device.

This is the "AI Controller → Command Execution" stage from the assignment.

Tiers:
    CRITICAL  — mean_risk > 0.9: underclock to 70%, schedule immediate inspection
    WARNING   — mean_risk > 0.5: underclock to 85%, schedule next-window inspection
    DEGRADED  — te_score < 0.8 and risk ≤ 0.5: minor tuning, increase monitoring
    HEALTHY   — otherwise: hold settings, suggest overclock if conditions allow

Safety overrides (applied before tier logic):
    - Thermal hard limit: T > 80°C → force underclock + max cooling
    - Overvoltage: V > 110% stock → reset to stock voltage
    - Fleet redundancy: never schedule all devices of same model simultaneously

Inputs:  fleet_risk_scores.json, kpi_timeseries.parquet, fleet_metadata.json
Outputs: fleet_actions.json
Vars:    actions_issued, devices_underclocked, devices_inspected
"""

import json
import pandas as pd

CONTROLLER_VERSION = "1.0-rulebased"

# Tier thresholds
CRITICAL_RISK = 0.9
WARNING_RISK = 0.5
DEGRADED_TE_SCORE = 0.8

# Safety limits
THERMAL_HARD_LIMIT_C = 80.0
OVERVOLTAGE_PCT = 1.10  # 110% of stock

# Overclock suggestion conditions
OVERCLOCK_HEADROOM_C = 10.0
OVERCLOCK_BOOST = 1.05  # 5% overclock


def load_stock_specs(meta: dict) -> dict:
    """Build device_id → stock specs lookup."""
    specs = {}
    for dev in meta["fleet"]:
        specs[dev["device_id"]] = {
            "stock_clock_ghz": dev["stock_clock_ghz"],
            "stock_voltage_v": dev["stock_voltage_v"],
            "nominal_hashrate_th": dev["nominal_hashrate_th"],
            "model": dev.get("model", "unknown"),
        }
    return specs


def classify_tier(risk: dict) -> str:
    """Assign severity tier based on risk score and TE health."""
    mean_risk = risk["mean_risk"]
    te_score = risk["latest_snapshot"]["te_score"]

    if mean_risk > CRITICAL_RISK:
        return "CRITICAL"
    elif mean_risk > WARNING_RISK:
        return "WARNING"
    elif te_score < DEGRADED_TE_SCORE and mean_risk <= WARNING_RISK:
        return "DEGRADED"
    else:
        return "HEALTHY"


def apply_safety_overrides(risk: dict, stock: dict) -> list:
    """Check safety constraints. Returns list of override commands + reasons."""
    overrides = []
    snap = risk["latest_snapshot"]

    # Thermal hard limit
    if snap["temperature_c"] > THERMAL_HARD_LIMIT_C:
        target_clock = round(stock["stock_clock_ghz"] * 0.80, 4)
        overrides.append({
            "command": {"type": "set_clock", "value_ghz": target_clock, "priority": "CRITICAL"},
            "reason": f"SAFETY: temperature {snap['temperature_c']:.1f}°C > {THERMAL_HARD_LIMIT_C}°C hard limit",
            "override": "thermal_hard_limit_80C",
        })

    # Overvoltage protection
    if snap["voltage_v"] > stock["stock_voltage_v"] * OVERVOLTAGE_PCT:
        overrides.append({
            "command": {"type": "set_voltage", "value_v": stock["stock_voltage_v"], "priority": "CRITICAL"},
            "reason": f"SAFETY: voltage {snap['voltage_v']:.4f}V > {OVERVOLTAGE_PCT:.0%} of stock ({stock['stock_voltage_v']:.3f}V)",
            "override": "overvoltage_110pct_stock",
        })

    return overrides


def generate_tier_commands(tier: str, risk: dict, stock: dict) -> list:
    """Generate operational commands based on tier classification."""
    commands = []
    snap = risk["latest_snapshot"]

    if tier == "CRITICAL":
        target_clock = round(stock["stock_clock_ghz"] * 0.70, 4)
        target_voltage = round(stock["stock_voltage_v"] * 0.95, 4)
        commands.append({"type": "set_clock", "value_ghz": target_clock, "priority": "HIGH"})
        commands.append({"type": "set_voltage", "value_v": target_voltage, "priority": "HIGH"})
        commands.append({"type": "schedule_inspection", "urgency": "immediate", "priority": "HIGH"})
        commands.append({"type": "set_monitoring_interval", "value_seconds": 60, "priority": "MEDIUM"})

    elif tier == "WARNING":
        target_clock = round(stock["stock_clock_ghz"] * 0.85, 4)
        commands.append({"type": "set_clock", "value_ghz": target_clock, "priority": "MEDIUM"})
        commands.append({"type": "schedule_inspection", "urgency": "next_window", "priority": "MEDIUM"})
        commands.append({"type": "set_monitoring_interval", "value_seconds": 120, "priority": "LOW"})

    elif tier == "DEGRADED":
        # Nudge voltage back toward stock if drifting
        if abs(snap["voltage_v"] - stock["stock_voltage_v"]) > 0.01:
            commands.append({"type": "set_voltage", "value_v": stock["stock_voltage_v"], "priority": "LOW"})
        commands.append({"type": "set_monitoring_interval", "value_seconds": 180, "priority": "LOW"})

    else:  # HEALTHY
        commands.append({"type": "hold_settings", "priority": "LOW"})

        # Suggest mild overclock if conditions permit
        thermal_headroom = 85.0 - snap["temperature_c"]
        if (thermal_headroom > OVERCLOCK_HEADROOM_C
                and snap["operating_mode"] != "overclock"):
            target_clock = round(stock["stock_clock_ghz"] * OVERCLOCK_BOOST, 4)
            commands.append({
                "type": "suggest_overclock",
                "value_ghz": target_clock,
                "priority": "LOW",
                "condition": f"thermal_headroom={thermal_headroom:.1f}°C",
            })

    return commands


def apply_fleet_redundancy(actions: list) -> list:
    """Never schedule all devices of same model for inspection simultaneously."""
    # Group by model
    model_inspections = {}
    for action in actions:
        model = action.get("model", "unknown")
        has_inspection = any(
            c["type"] == "schedule_inspection" for c in action.get("commands", [])
        )
        if has_inspection:
            model_inspections.setdefault(model, []).append(action)

    deferred = []
    for model, model_actions in model_inspections.items():
        # Count total devices of this model (not just flagged ones)
        model_device_count = sum(1 for a in actions if a.get("model") == model)

        if len(model_actions) >= model_device_count and model_device_count > 1:
            # All devices of this model are being inspected — defer the lowest-risk one
            model_actions.sort(key=lambda a: a["risk_score"])
            defer = model_actions[0]
            defer["commands"] = [
                c for c in defer["commands"] if c["type"] != "schedule_inspection"
            ]
            defer["commands"].append({
                "type": "schedule_inspection",
                "urgency": "deferred",
                "priority": "LOW",
                "reason": "fleet_redundancy: at least one device of this model stays operational",
            })
            defer["rationale"].append(
                f"Inspection deferred — fleet redundancy for model {model}"
            )
            deferred.append(defer["device_id"])

    return deferred


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    with open("fleet_risk_scores.json") as f:
        scores = json.load(f)
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    stock_specs = load_stock_specs(meta)

    # ── Process each device ──────────────────────────────────────────────
    actions = []
    safety_constraints_applied = set()

    for risk in scores["device_risks"]:
        device_id = risk["device_id"]
        stock = stock_specs.get(device_id, {})
        if not stock:
            print(f"Warning: no stock specs for {device_id}, skipping")
            continue

        # Safety overrides first
        overrides = apply_safety_overrides(risk, stock)
        for ov in overrides:
            safety_constraints_applied.add(ov["override"])

        # Tier classification
        tier = classify_tier(risk)

        # If safety override forced underclock, escalate tier to at least WARNING
        if overrides and tier == "HEALTHY":
            tier = "WARNING"

        # Generate tier commands
        commands = generate_tier_commands(tier, risk, stock)

        # Prepend any safety override commands
        safety_commands = [ov["command"] for ov in overrides]
        all_commands = safety_commands + commands

        # Build rationale
        rationale = []
        for ov in overrides:
            rationale.append(ov["reason"])
        snap = risk["latest_snapshot"]
        rationale.append(
            f"Risk {risk['mean_risk']:.2f}, TE_score {snap['te_score']:.3f} → tier {tier}"
        )

        action = {
            "device_id": device_id,
            "model": stock.get("model", "unknown"),
            "tier": tier,
            "risk_score": risk["mean_risk"],
            "te_score": snap["te_score"],
            "commands": all_commands,
            "rationale": rationale,
        }
        actions.append(action)

    # ── Fleet redundancy override ────────────────────────────────────────
    deferred_devices = apply_fleet_redundancy(actions)
    if deferred_devices:
        safety_constraints_applied.add("fleet_redundancy_per_model")
        print(f"Fleet redundancy: deferred inspection for {deferred_devices}")

    # ── Summary ──────────────────────────────────────────────────────────
    tier_counts = {}
    for a in actions:
        tier_counts[a["tier"]] = tier_counts.get(a["tier"], 0) + 1

    devices_underclocked = sum(
        1 for a in actions
        if any(c["type"] == "set_clock" for c in a["commands"])
    )
    devices_inspected = sum(
        1 for a in actions
        if any(c["type"] == "schedule_inspection" and c.get("urgency") != "deferred"
               for c in a["commands"])
    )
    actions_issued = sum(len(a["commands"]) for a in actions)

    print(f"\nController output:")
    print(f"  Tiers: {tier_counts}")
    print(f"  Actions issued: {actions_issued}")
    print(f"  Devices underclocked: {devices_underclocked}")
    print(f"  Devices scheduled for inspection: {devices_inspected}")

    for a in actions:
        tier_badge = {"CRITICAL": "🔴", "WARNING": "🟡", "DEGRADED": "🟠", "HEALTHY": "🟢"}.get(a["tier"], "⚪")
        print(f"  {tier_badge} {a['device_id']} ({a['model']}): "
              f"tier={a['tier']}  risk={a['risk_score']:.3f}  "
              f"commands={[c['type'] for c in a['commands']]}")

    # ── Write outputs ────────────────────────────────────────────────────
    output = {
        "controller_version": CONTROLLER_VERSION,
        "scoring_window": {
            "start": scores["window_start"],
            "end": scores["window_end"],
        },
        "tier_counts": tier_counts,
        "actions": actions,
        "safety_constraints_applied": sorted(safety_constraints_applied),
    }

    with open("fleet_actions.json", "w") as f:
        json.dump(output, f, indent=2)

    with open("_validance_vars.json", "w") as f:
        json.dump({
            "actions_issued": actions_issued,
            "devices_underclocked": devices_underclocked,
            "devices_inspected": devices_inspected,
        }, f)


if __name__ == "__main__":
    main()
