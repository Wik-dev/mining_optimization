#!/usr/bin/env python3
"""
Task 5: Optimize Fleet — Tier Classification & Safety Overrides
================================================================
Deterministic controller that consumes risk scores and telemetry snapshots,
applies safety overrides, and emits tier classifications + safety flags per device.

This is the perception/classification layer. Action decisions (what to actually do
about a WARNING or CRITICAL device) are handled by the AI reasoning agent (SafeClaw)
with human approval via Validance's approval gate — not by deterministic optimization.

Tiers:
    CRITICAL  — mean_risk > 0.9: underclock to 70%, schedule immediate inspection
    WARNING   — mean_risk > 0.5: underclock to 85%, schedule next-window inspection
    DEGRADED  — te_score < 0.8 and risk ≤ 0.5: minor tuning, increase monitoring
    HEALTHY   — otherwise: hold settings, suggest overclock if conditions allow

Safety overrides (applied before tier logic — these ALWAYS win):
    - Thermal hard limit: T > 80°C → force underclock + max cooling
    - Thermal emergency low: T < 10°C → sleep mode + immediate inspection (coolant freeze risk)
    - Thermal low warning: T < 20°C → underclock to 70% + minimize fan (air-cooled only)
    - Overvoltage: V > 110% stock → reset frequency to stock (V/f coupled)
    - Fleet redundancy: never schedule all devices of same model simultaneously

MOS platform alignment:
    - All commands mapped to MOS RPC methods (setFrequency, setPowerMode, etc.)
    - No set_voltage commands — voltage is V/f coupled, controlled implicitly via frequency
    - Actions annotated with MOS error codes from the alert taxonomy

Inputs:  fleet_risk_scores.json, kpi_timeseries.parquet, fleet_metadata.json,
         trend_analysis.json (optional — trend-aware escalation)
Outputs: fleet_actions.json
Vars:    actions_issued, devices_underclocked, devices_inspected
"""

import json
import os

CONTROLLER_VERSION = "2.0-tier-only"

# Tier thresholds
CRITICAL_RISK = 0.9
WARNING_RISK = 0.5
DEGRADED_TE_SCORE = 0.8

# Safety limits
THERMAL_HARD_LIMIT_C = 80.0
OVERVOLTAGE_PCT = 1.10  # 110% of stock

# Gap 1: Two-tier low-temperature alert.
# Hydro-cooled site at 64.5°N (northern Sweden/Finland). Real risk at low temps is coolant
# viscosity increase and condensation on PCBs, not chip damage. Below 10°C, ethylene glycol
# coolant approaches viscosity limits for standard pump configurations — flow rate drops,
# causing localized hotspots despite low ambient. MOS inlet temp thresholds: 25°C warning,
# 20°C critical (low). We add a second emergency tier for freeze risk.
THERMAL_LOW_LIMIT_C = 20.0       # Low-temperature warning — reduce clock, minimize fan (air-cooled)
THERMAL_EMERGENCY_LOW_C = 10.0   # Emergency low — sleep mode, coolant freeze risk

# Overclock suggestion conditions
OVERCLOCK_HEADROOM_C = 10.0
OVERCLOCK_BOOST = 1.05  # 5% overclock

# ── Trend-Aware Constants ─────────────────────────────────────────────────────
# Slope thresholds for trend-based tier escalation. Slopes are TE_score per hour.
# A slope of -0.02/h means the device crosses a 0.2 TE_score band in 10 hours —
# fast enough to warrant pre-emptive escalation before the static tier catches it.
TREND_ESCALATION_SLOPE = -0.005   # Moderate decline: escalate HEALTHY → WARNING
TREND_CRITICAL_SLOPE = -0.02      # Fast decline: escalate one step toward CRITICAL
REGIME_CHANGE_ESCALATION = True   # CUSUM regime change → escalate HEALTHY to WARNING
TREND_MIN_R2 = 0.3                # Minimum R² to trust trend for escalation

# ── MOS Platform Mappings ─────────────────────────────────────────────────────
# Gap 2: MOS command mapping. MOS exposes RPC methods, not raw register writes.
# Voltage is NOT independently controllable — it's coupled to frequency via the
# ASIC's V/f curve (set in firmware). setFrequency implicitly adjusts voltage.
# Source: miningos-wrk-miner-antminer, miningos-wrk-miner-whatsminer repos.
MOS_COMMAND_MAP = {
    "set_clock":              "setFrequency",       # Primary tuning control
    "set_power_mode":         "setPowerMode",        # normal / sleep
    "set_fan_mode":           "setFanControl",       # Air-cooled only; no MOS method for hydro pumps
    "schedule_inspection":    None,                  # Operational — no MOS RPC equivalent
    "set_monitoring_interval": None,                 # Internal pipeline config, not a device command
    "hold_settings":          None,                  # No-op — no MOS RPC needed
    "suggest_overclock":      "setFrequency",        # Same method, higher target
    "reboot":                 "reboot",
}

# Gap 3: MOS error codes. Maps our anomaly tiers to MOS alert code taxonomy.
# Source: MOS alert config (miningos-wrk-miner-antminer/config).
# This is a tier-based approximation — production would use per-anomaly-type
# classifier output for exact code mapping (e.g., which specific voltage fault).
MOS_ALERT_CODES = {
    "P:1": "High temperature protection triggered",
    "P:2": "Low temperature protection triggered",
    "R:1": "Low hashrate",
    "N:1": "High hashrate (anomalous)",
    "V:1": "Power initialization error",
    "V:2": "PSU not calibrated",
    "J0:8": "Insufficient hashboards",
    "J0:6": "Temperature sensor error",
    "L0:1": "Voltage/frequency exceeds limit",
    "L0:2": "Voltage/frequency mismatch",
    "J0:2": "Chip insufficiency",
    "M:1": "Memory allocation error",
}

# Tier-to-anomaly-code mapping. CRITICAL/WARNING tiers correlate with specific
# MOS error families. This is approximate — a real integration would map from
# the anomaly classifier's per-type output, not from the aggregate tier.
TIER_ANOMALY_MAP = {
    "CRITICAL": {
        "thermal_deg": ["P:1", "P:2"],
        "psu_instability": ["V:1", "L0:1"],
        "hashrate_decay": ["R:1", "J0:8"],
        "general": ["P:1", "V:1", "R:1"],
    },
    "WARNING": {
        "thermal_deg": ["P:1"],
        "psu_instability": ["V:2", "L0:2"],
        "hashrate_decay": ["R:1", "J0:2"],
        "general": ["P:1", "V:2", "R:1"],
    },
    "DEGRADED": {
        "thermal_deg": ["J0:6"],
        "psu_instability": ["L0:2"],
        "hashrate_decay": ["R:1"],
        "general": ["R:1"],
    },
    "HEALTHY": {
        "general": [],
    },
}


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


def classify_tier(risk: dict, trend: dict | None = None) -> tuple:
    """Assign severity tier based on risk score, TE health, and trend.

    Base tier logic is unchanged from v1.0. When trend data is available
    and R² >= TREND_MIN_R2, the tier can be escalated (never de-escalated)
    based on the slope direction:
    - slope < TREND_CRITICAL_SLOPE (-0.02/h): escalate one step toward CRITICAL
    - slope < TREND_ESCALATION_SLOPE (-0.005/h) and HEALTHY: escalate to WARNING
    - slope > +0.005 and DEGRADED: add RECOVERING annotation (no de-escalation)
    - CUSUM regime change and HEALTHY: escalate to WARNING

    De-escalation is deliberately avoided — conservative approach. A device
    that looks like it's recovering could relapse; let the static tier
    catch up naturally when risk scores improve.

    Returns:
        (tier: str, rationale_parts: list[str])
    """
    mean_risk = risk["mean_risk"]
    te_score = risk["latest_snapshot"]["te_score"]

    # ── Base tier (v1.0 logic, unchanged) ────────────────────────────────
    if mean_risk > CRITICAL_RISK:
        base_tier = "CRITICAL"
    elif mean_risk > WARNING_RISK:
        base_tier = "WARNING"
    elif te_score < DEGRADED_TE_SCORE and mean_risk <= WARNING_RISK:
        base_tier = "DEGRADED"
    else:
        base_tier = "HEALTHY"

    tier = base_tier
    rationale = []

    # ── Trend-based escalation (v1.1) ───────────────────────────────────
    if trend is None:
        return tier, rationale

    slope = trend.get("primary_slope_per_hour", 0.0)
    r2 = trend.get("primary_r_squared", 0.0)
    direction = trend.get("primary_direction", "stable")
    regime = trend.get("regime", {})
    regime_change = regime.get("change_detected", False)

    if r2 >= TREND_MIN_R2:
        # Fast decline: escalate one step toward CRITICAL
        if slope < TREND_CRITICAL_SLOPE:
            ESCALATION = {"HEALTHY": "WARNING", "WARNING": "CRITICAL", "DEGRADED": "CRITICAL"}
            new_tier = ESCALATION.get(tier)
            if new_tier and new_tier != tier:
                rationale.append(
                    f"TREND: slope {slope:.4f}/h (R²={r2:.2f}) → escalated {tier}→{new_tier}"
                )
                tier = new_tier

        # Moderate decline: escalate HEALTHY → WARNING
        elif slope < TREND_ESCALATION_SLOPE and tier == "HEALTHY":
            rationale.append(
                f"TREND: declining slope {slope:.4f}/h (R²={r2:.2f}) → escalated HEALTHY→WARNING"
            )
            tier = "WARNING"

        # Recovery annotation (no de-escalation)
        elif slope > 0.005 and base_tier == "DEGRADED":
            rationale.append(
                f"TREND: recovering (slope +{slope:.4f}/h, R²={r2:.2f}) — monitoring"
            )

    # CUSUM regime change escalation
    if REGIME_CHANGE_ESCALATION and regime_change and base_tier == "HEALTHY":
        if tier == "HEALTHY":  # Only if not already escalated by slope
            rationale.append(
                f"TREND: regime change detected (CUSUM, direction={regime.get('direction', '?')}) "
                f"→ escalated HEALTHY→WARNING"
            )
            tier = "WARNING"

    return tier, rationale


def apply_safety_overrides(risk: dict, stock: dict) -> list:
    """Check safety constraints. Returns list of override commands + reasons."""
    overrides = []
    snap = risk["latest_snapshot"]

    # ── High temperature: thermal hard limit (80°C) ───────────────────────
    if snap["temperature_c"] > THERMAL_HARD_LIMIT_C:
        target_clock = round(stock["stock_clock_ghz"] * 0.80, 4)
        overrides.append({
            "command": {"type": "set_clock", "value_ghz": target_clock, "priority": "CRITICAL"},
            "reason": f"SAFETY: temperature {snap['temperature_c']:.1f}°C > {THERMAL_HARD_LIMIT_C}°C hard limit",
            "override": "thermal_hard_limit_80C",
        })

    # ── Low temperature: emergency freeze risk (< 10°C) ──────────────────
    # Below 10°C, coolant viscosity spikes — pump flow drops, risk of localized
    # hotspots and condensation. Sleep mode eliminates heat generation entirely;
    # immediate inspection to check coolant state and condensation on PCBs.
    elif snap["temperature_c"] < THERMAL_EMERGENCY_LOW_C:
        overrides.append({
            "command": {"type": "set_power_mode", "value": "sleep", "priority": "CRITICAL"},
            "reason": (f"SAFETY: temperature {snap['temperature_c']:.1f}°C < {THERMAL_EMERGENCY_LOW_C}°C "
                       "— coolant freeze risk, condensation hazard"),
            "override": "thermal_emergency_low_10C",
        })
        overrides.append({
            "command": {"type": "schedule_inspection", "urgency": "immediate", "priority": "CRITICAL"},
            "reason": "SAFETY: immediate inspection required — check coolant viscosity and PCB condensation",
            "override": "thermal_emergency_low_10C",
        })

    # ── Low temperature: warning (< 20°C) ────────────────────────────────
    # Reduce clock to 70% to lower heat dissipation demand. For air-cooled models,
    # set fan to minimum to retain heat. For hydro units, fan command is N/A —
    # the relevant control would be pump speed, which MOS doesn't expose directly.
    elif snap["temperature_c"] < THERMAL_LOW_LIMIT_C:
        target_clock = round(stock["stock_clock_ghz"] * 0.70, 4)
        overrides.append({
            "command": {"type": "set_clock", "value_ghz": target_clock, "priority": "HIGH"},
            "reason": (f"SAFETY: temperature {snap['temperature_c']:.1f}°C < {THERMAL_LOW_LIMIT_C}°C "
                       "— low-temp warning, reduce power to manage coolant conditions"),
            "override": "thermal_low_limit_20C",
        })
        # Fan control only meaningful for air-cooled models. Hydro units use liquid loop —
        # fan command is N/A; the relevant control would be pump speed reduction.
        model = stock.get("model", "")
        is_hydro = "HYD" in model.upper() or "HYDRO" in model.upper()
        if not is_hydro:
            overrides.append({
                "command": {"type": "set_fan_mode", "value": "min", "priority": "HIGH"},
                "reason": "Low temp: minimize fan speed to retain heat (air-cooled only)",
                "override": "thermal_low_limit_20C",
            })

    # ── Overvoltage protection ────────────────────────────────────────────
    # MOS does not expose direct voltage control — voltage is coupled to frequency
    # via the ASIC's V/f curve. Reducing frequency to stock implicitly restores
    # nominal voltage. This replaces the previous set_voltage command.
    if snap["voltage_v"] > stock["stock_voltage_v"] * OVERVOLTAGE_PCT:
        overrides.append({
            "command": {
                "type": "set_clock", "value_ghz": stock["stock_clock_ghz"], "priority": "CRITICAL",
                "note": "V/f coupled — voltage adjusts implicitly with frequency",
            },
            "reason": (f"SAFETY: voltage {snap['voltage_v']:.4f}V > {OVERVOLTAGE_PCT:.0%} of stock "
                       f"({stock['stock_voltage_v']:.3f}V) — reset frequency to stock to restore nominal V/f point"),
            "override": "overvoltage_110pct_stock",
        })

    return overrides


def generate_tier_commands(tier: str, risk: dict, stock: dict) -> list:
    """Generate operational commands based on tier classification."""
    commands = []
    snap = risk["latest_snapshot"]

    if tier == "CRITICAL":
        target_clock = round(stock["stock_clock_ghz"] * 0.70, 4)
        # V/f coupled — reducing frequency to 70% implicitly reduces voltage via the
        # ASIC's V/f curve. No standalone set_voltage; MOS only exposes setFrequency.
        commands.append({"type": "set_clock", "value_ghz": target_clock, "priority": "HIGH",
                         "note": "V/f coupled — voltage adjusts implicitly with frequency"})
        commands.append({"type": "schedule_inspection", "urgency": "immediate", "priority": "HIGH"})
        commands.append({"type": "set_monitoring_interval", "value_seconds": 60, "priority": "MEDIUM"})

    elif tier == "WARNING":
        target_clock = round(stock["stock_clock_ghz"] * 0.85, 4)
        commands.append({"type": "set_clock", "value_ghz": target_clock, "priority": "MEDIUM"})
        commands.append({"type": "schedule_inspection", "urgency": "next_window", "priority": "MEDIUM"})
        commands.append({"type": "set_monitoring_interval", "value_seconds": 120, "priority": "LOW"})

    elif tier == "DEGRADED":
        # Frequency reset to stock to restore nominal V/f operating point.
        # MOS doesn't expose voltage directly — resetting frequency is the correct
        # way to bring voltage back to nominal via the V/f curve.
        if abs(snap["voltage_v"] - stock["stock_voltage_v"]) > 0.01:
            commands.append({"type": "set_clock", "value_ghz": stock["stock_clock_ghz"], "priority": "LOW",
                             "note": "Frequency reset to stock to restore nominal V/f operating point"})
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


def annotate_mos_methods(actions: list) -> None:
    """Annotate each command with its MOS RPC method name.

    Ensures fleet_actions.json never contains a command type that doesn't have a
    known MOS mapping. Commands without a direct MOS method (e.g., schedule_inspection)
    are marked as operational/internal.
    """
    for action in actions:
        for cmd in action.get("commands", []):
            cmd_type = cmd["type"]
            mos_method = MOS_COMMAND_MAP.get(cmd_type)
            if mos_method:
                cmd["mos_method"] = mos_method
            else:
                cmd["mos_method"] = None
                cmd["mos_note"] = "Operational — no direct MOS RPC equivalent"


def annotate_mos_alert_codes(actions: list) -> None:
    """Annotate each action with relevant MOS alert codes based on tier.

    Uses TIER_ANOMALY_MAP for tier-based approximation. In production, this would
    use the per-anomaly-type classifier output for exact code mapping.
    """
    for action in actions:
        tier = action.get("tier", "HEALTHY")
        tier_codes = TIER_ANOMALY_MAP.get(tier, {})
        # Use 'general' mapping since we don't have per-anomaly breakdown at action level
        codes = tier_codes.get("general", [])
        action["mos_alert_codes"] = codes


def load_trend_data() -> dict | None:
    """Load trend analysis results. Returns None if not available.

    Backward compatible: if trend_analysis.json doesn't exist (e.g., running
    v1.0 pipeline or trend task was skipped), the controller falls back to
    static tier logic.
    """
    try:
        with open("trend_analysis.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    with open("fleet_risk_scores.json") as f:
        scores = json.load(f)
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    stock_specs = load_stock_specs(meta)

    # Load trend data (optional — graceful fallback to v1.0 logic)
    trend_data = load_trend_data()
    trend_lookup = {}
    if trend_data:
        for dev_trend in trend_data.get("devices", []):
            trend_lookup[dev_trend["device_id"]] = dev_trend
        print(f"Loaded trend data: {len(trend_lookup)} devices")
    else:
        print("No trend_analysis.json found — using static tier logic (v1.0 fallback)")

    # ── Process each device ──────────────────────────────────────────────
    actions = []
    safety_constraints_applied = set()

    for risk in scores["device_risks"]:
        device_id = risk["device_id"]
        stock = stock_specs.get(device_id, {})
        if not stock:
            print(f"Warning: no stock specs for {device_id}, skipping")
            continue

        # (1) Safety overrides first — these ALWAYS win
        overrides = apply_safety_overrides(risk, stock)
        has_safety_override = len(overrides) > 0
        for ov in overrides:
            safety_constraints_applied.add(ov["override"])

        # (2) Tier classification (trend-aware when data available)
        device_trend = trend_lookup.get(device_id)
        tier, trend_rationale = classify_tier(risk, trend=device_trend)

        # If safety override forced underclock, escalate tier to at least WARNING
        if overrides and tier == "HEALTHY":
            tier = "WARNING"

        # (3) Generate tier commands
        tier_commands = generate_tier_commands(tier, risk, stock)

        # Merge: safety overrides + tier commands
        safety_commands = [ov["command"] for ov in overrides]
        all_commands = safety_commands + tier_commands

        # Build rationale
        rationale = []
        for ov in overrides:
            rationale.append(ov["reason"])
        snap = risk["latest_snapshot"]
        rationale.append(
            f"Risk {risk['mean_risk']:.2f}, TE_score {snap['te_score']:.3f} → tier {tier}"
        )
        # Append trend-based rationale (v1.1)
        rationale.extend(trend_rationale)

        # Trend context (v1.1) — attached to action for downstream consumers
        trend_context = None
        if device_trend:
            trend_context = {
                "direction": device_trend.get("primary_direction", "unknown"),
                "slope_per_hour": device_trend.get("primary_slope_per_hour", 0.0),
                "r_squared": device_trend.get("primary_r_squared", 0.0),
                "regime_change": device_trend.get("regime", {}).get("change_detected", False),
            }

        action = {
            "device_id": device_id,
            "model": stock.get("model", "unknown"),
            "tier": tier,
            "risk_score": risk["mean_risk"],
            "te_score": snap["te_score"],
            "commands": all_commands,
            "rationale": rationale,
            "trend_context": trend_context,
        }

        actions.append(action)

    # ── Fleet redundancy override ────────────────────────────────────────
    deferred_devices = apply_fleet_redundancy(actions)
    if deferred_devices:
        safety_constraints_applied.add("fleet_redundancy_per_model")
        print(f"Fleet redundancy: deferred inspection for {deferred_devices}")

    # ── MOS platform annotations ──────────────────────────────────────────
    annotate_mos_methods(actions)
    annotate_mos_alert_codes(actions)

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

    print(f"\nController output ({CONTROLLER_VERSION}):")
    print(f"  Tiers: {tier_counts}")
    print(f"  Actions issued: {actions_issued}")
    print(f"  Devices underclocked: {devices_underclocked}")
    print(f"  Devices scheduled for inspection: {devices_inspected}")

    for a in actions:
        tier_badge = {"CRITICAL": "🔴", "WARNING": "🟡", "DEGRADED": "🟠", "HEALTHY": "🟢"}.get(a["tier"], "⚪")
        print(f"  {tier_badge} {a['device_id']} ({a['model']}): "
              f"tier={a['tier']}  risk={a['risk_score']:.3f}"
              f"  commands={[c['type'] for c in a['commands']]}")

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
