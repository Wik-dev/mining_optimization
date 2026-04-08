#!/usr/bin/env python3
"""
Fleet Control Action — Validated fleet control execution
=========================================================
Invoked by ``fleet_underclock``, ``fleet_schedule_maintenance``, and
``fleet_emergency_shutdown`` catalog templates via the Validance proposal
pipeline. Validates constraints, generates MOS command payloads, writes
audit records.

Actions:
    underclock   — Reduce device clock frequency to a target percentage of stock.
                   Validates fleet hashrate stays >= 70% and target >= 50%.
    maintenance  — Schedule device for maintenance (inspection, repair, firmware).
                   Validates fleet redundancy (max 20% offline unless immediate).
    shutdown     — Immediate device shutdown. Always proceeds if human-approved;
                   reports fleet capacity impact for operator awareness.

Data sources (read-only from /work/fleet/):
    fleet_risk_scores.json  — device risk assessments
    fleet_actions.json      — controller recommendations
    fleet_metadata.json     — device stock specs

Output: JSON to stdout with status, details, fleet_impact, risk_context, audit.
Also writes agent_actions.json (append-only log) and _validance_vars.json.

Exit code 1 + {"status": "rejected"} for constraint violations.

Pure Python stdlib — no pandas, no ML dependencies.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Pipeline output directory — mounted at /work/fleet/ in the container
FLEET_DATA_DIR = Path(os.environ.get("FLEET_DATA_DIR", "/work/fleet"))

# Fleet safety constraints — reused from optimize.py (controller constants).
# Never take > 20% of fleet offline simultaneously. Protects against cascading
# capacity loss when agent schedules multiple maintenance windows.
MAX_OFFLINE_PCT = 20

# Maintain at least 70% of nominal fleet hashrate. Prevents the agent from
# underclocking the fleet into unprofitability. Based on mining economics:
# at 70% hashrate, revenue still covers operating costs at most energy prices.
MIN_HASHRATE_PCT = 70

# Cannot underclock below 50% of stock frequency. Below 50%, V/f curve
# enters non-linear region where efficiency actually degrades — the ASIC
# firmware's V/f table doesn't have validated points below this threshold.
MIN_UNDERCLOCK_PCT = 50


def load_json(filename: str) -> dict:
    """Load a JSON file from the fleet data directory."""
    path = FLEET_DATA_DIR / filename
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def load_fleet_data() -> tuple:
    """Load all fleet data files. Exit with error if critical files missing."""
    risk_scores = load_json("fleet_risk_scores.json")
    actions_data = load_json("fleet_actions.json")
    metadata = load_json("fleet_metadata.json")

    if risk_scores is None or metadata is None:
        print(json.dumps({
            "status": "rejected",
            "reason": "Required fleet data files not found in /work/fleet/",
        }))
        sys.exit(1)

    return risk_scores, actions_data, metadata


def get_device_spec(device_id: str, metadata: dict) -> dict:
    """Look up device stock specs from metadata."""
    for d in metadata["fleet"]:
        if d["device_id"] == device_id:
            return d
    return None


def get_device_risk(device_id: str, risk_scores: dict) -> dict:
    """Look up device risk assessment."""
    for r in risk_scores["device_risks"]:
        if r["device_id"] == device_id:
            return r
    return None


def get_device_action(device_id: str, actions_data: dict) -> dict:
    """Look up controller recommendation for device."""
    if actions_data is None:
        return None
    for a in actions_data["actions"]:
        if a["device_id"] == device_id:
            return a
    return None


def compute_fleet_hashrate(risk_scores: dict, metadata: dict) -> dict:
    """Compute current and nominal fleet hashrate."""
    current_total = sum(
        r["latest_snapshot"]["hashrate_th"]
        for r in risk_scores["device_risks"]
    )
    nominal_total = sum(d["nominal_hashrate_th"] for d in metadata["fleet"])
    return {
        "current_th": round(current_total, 2),
        "nominal_th": round(nominal_total, 2),
        "current_pct": round(current_total / nominal_total * 100, 1) if nominal_total else 0,
    }


def append_audit_log(action_record: dict):
    """Append action record to agent_actions.json (append-only log)."""
    log_path = FLEET_DATA_DIR / "agent_actions.json"
    entries = []
    if log_path.exists():
        try:
            with open(log_path) as f:
                entries = json.load(f)
        except (json.JSONDecodeError, ValueError):
            entries = []

    entries.append(action_record)

    with open(log_path, "w") as f:
        json.dump(entries, f, indent=2)


def action_underclock(params: dict) -> dict:
    """Reduce a device's clock frequency to a target percentage of stock.

    Validates:
    - Device exists in fleet
    - target_pct in [50, 100] range (V/f curve validity)
    - Fleet hashrate stays >= 70% of nominal after underclock
    """
    device_id = params.get("device_id")
    target_pct = params.get("target_pct")
    value_ghz = params.get("value_ghz")
    reason = params.get("reason", "")

    if not device_id or (target_pct is None and value_ghz is None):
        return {"status": "rejected", "reason": "Missing required: device_id and target_pct (or value_ghz)"}

    risk_scores, actions_data, metadata = load_fleet_data()
    spec = get_device_spec(device_id, metadata)
    if spec is None:
        return {"status": "rejected", "reason": f"Unknown device: {device_id}"}

    # Accept value_ghz as alternative to target_pct — compute percentage from
    # the device's stock clock speed. This allows callers (e.g., dashboards)
    # that know the target GHz but not the stock clock to submit proposals.
    if target_pct is None and value_ghz is not None:
        stock_ghz = spec.get("stock_clock_ghz")
        if not stock_ghz:
            return {"status": "rejected", "reason": f"No stock_clock_ghz for device {device_id}"}
        target_pct = round(value_ghz / stock_ghz * 100, 1)

    risk = get_device_risk(device_id, risk_scores)
    action = get_device_action(device_id, actions_data)

    # Validate underclock range
    if target_pct < MIN_UNDERCLOCK_PCT:
        return {
            "status": "rejected",
            "reason": (f"target_pct {target_pct}% below minimum {MIN_UNDERCLOCK_PCT}%. "
                       "V/f curve not validated below this threshold."),
        }
    if target_pct > 100:
        return {"status": "rejected", "reason": f"target_pct {target_pct}% exceeds 100%"}

    # Compute fleet hashrate impact
    fleet_hr = compute_fleet_hashrate(risk_scores, metadata)
    current_device_hr = risk["latest_snapshot"]["hashrate_th"] if risk else spec["nominal_hashrate_th"]
    # Estimate post-underclock hashrate: proportional to frequency reduction
    # This is approximate — real V/f relationship is non-linear, but linear
    # approximation is conservative (actual hashrate drops less than frequency)
    estimated_new_hr = current_device_hr * (target_pct / 100.0)
    hr_delta = current_device_hr - estimated_new_hr
    post_fleet_hr = fleet_hr["current_th"] - hr_delta
    post_fleet_pct = round(post_fleet_hr / fleet_hr["nominal_th"] * 100, 1) if fleet_hr["nominal_th"] else 0

    if post_fleet_pct < MIN_HASHRATE_PCT:
        return {
            "status": "rejected",
            "reason": (f"Fleet hashrate would drop to {post_fleet_pct}% of nominal "
                       f"(minimum: {MIN_HASHRATE_PCT}%). "
                       f"Device {device_id} contributes {current_device_hr:.1f} TH/s."),
        }

    # Generate MOS command payload
    target_ghz = round(spec["stock_clock_ghz"] * target_pct / 100.0, 4)
    mos_command = {
        "method": "setFrequency",
        "params": {"frequency_ghz": target_ghz},
        "note": "V/f coupled — voltage adjusts implicitly with frequency",
    }

    result = {
        "status": "executed",
        "action": "underclock",
        "device_id": device_id,
        "details": {
            "target_pct": target_pct,
            "target_ghz": target_ghz,
            "stock_ghz": spec["stock_clock_ghz"],
            "previous_hashrate_th": round(current_device_hr, 2),
            "estimated_new_hashrate_th": round(estimated_new_hr, 2),
        },
        "mos_command": mos_command,
        "fleet_impact": {
            "pre_hashrate_pct": fleet_hr["current_pct"],
            "post_hashrate_pct": post_fleet_pct,
            "hashrate_delta_th": round(-hr_delta, 2),
        },
        "risk_context": {
            "mean_risk": risk["mean_risk"] if risk else None,
            "tier": action["tier"] if action else None,
            "te_score": risk["latest_snapshot"]["te_score"] if risk else None,
        },
        "reason": reason,
    }

    # Audit record
    audit = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "underclock",
        "device_id": device_id,
        "params": {"target_pct": target_pct, "target_ghz": target_ghz},
        "result": "executed",
        "reason": reason,
        "fleet_impact": result["fleet_impact"],
    }
    result["audit"] = audit
    append_audit_log(audit)

    return result


def action_maintenance(params: dict) -> dict:
    """Schedule a device for maintenance.

    Validates:
    - Device exists
    - Fleet redundancy: not all devices of same model offline
    - Capacity: max 20% of fleet offline unless urgency=immediate
    """
    device_id = params.get("device_id")
    maintenance_type = params.get("maintenance_type")
    urgency = params.get("urgency", "scheduled")
    reason = params.get("reason", "")

    if not device_id or not maintenance_type:
        return {"status": "rejected", "reason": "Missing required: device_id and maintenance_type"}

    valid_types = ["inspection", "minor_repair", "major_repair", "firmware_update"]
    if maintenance_type not in valid_types:
        return {"status": "rejected", "reason": f"Invalid maintenance_type: {maintenance_type}. Must be one of: {valid_types}"}

    valid_urgency = ["immediate", "next_window", "scheduled"]
    if urgency not in valid_urgency:
        return {"status": "rejected", "reason": f"Invalid urgency: {urgency}. Must be one of: {valid_urgency}"}

    risk_scores, actions_data, metadata = load_fleet_data()
    spec = get_device_spec(device_id, metadata)
    if spec is None:
        return {"status": "rejected", "reason": f"Unknown device: {device_id}"}

    risk = get_device_risk(device_id, risk_scores)
    action = get_device_action(device_id, actions_data)

    # Fleet redundancy check: not all devices of same model can be offline
    device_model = spec["model"]
    same_model_devices = [d for d in metadata["fleet"] if d["model"] == device_model]
    if len(same_model_devices) <= 1:
        # Only one device of this model — allow maintenance (no redundancy possible)
        pass
    else:
        # Check if other devices of same model are already scheduled for maintenance
        # (from controller recommendations — inspection = pending offline)
        if actions_data:
            same_model_inspections = sum(
                1 for a in actions_data["actions"]
                if a["model"] == device_model
                and any(c["type"] == "schedule_inspection" and c.get("urgency") != "deferred"
                        for c in a["commands"])
            )
            # If all other same-model devices are already flagged for inspection,
            # this would leave zero operational devices of this model
            if same_model_inspections >= len(same_model_devices) - 1:
                if urgency != "immediate":
                    return {
                        "status": "rejected",
                        "reason": (f"Fleet redundancy: all {len(same_model_devices)} {device_model} devices "
                                   "would be offline. Defer to next_window or use urgency=immediate to override."),
                    }

    # Capacity check: max 20% of fleet offline (unless immediate urgency)
    fleet_size = len(metadata["fleet"])
    max_offline = max(1, int(fleet_size * MAX_OFFLINE_PCT / 100))

    # Count currently scheduled inspections
    current_offline = 0
    if actions_data:
        current_offline = sum(
            1 for a in actions_data["actions"]
            if any(c["type"] == "schedule_inspection" and c.get("urgency") == "immediate"
                   for c in a["commands"])
        )

    if current_offline >= max_offline and urgency != "immediate":
        return {
            "status": "rejected",
            "reason": (f"Fleet capacity: {current_offline}/{fleet_size} devices already scheduled for "
                       f"maintenance (max {MAX_OFFLINE_PCT}%). Use urgency=immediate to override."),
        }

    result = {
        "status": "executed",
        "action": "maintenance",
        "device_id": device_id,
        "details": {
            "maintenance_type": maintenance_type,
            "urgency": urgency,
            "model": device_model,
        },
        "fleet_impact": {
            "fleet_size": fleet_size,
            "current_offline": current_offline,
            "post_offline": current_offline + 1,
            "max_offline_pct": MAX_OFFLINE_PCT,
        },
        "risk_context": {
            "mean_risk": risk["mean_risk"] if risk else None,
            "tier": action["tier"] if action else None,
            "te_score": risk["latest_snapshot"]["te_score"] if risk else None,
        },
        "reason": reason,
    }

    audit = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "maintenance",
        "device_id": device_id,
        "params": {"maintenance_type": maintenance_type, "urgency": urgency},
        "result": "executed",
        "reason": reason,
        "fleet_impact": result["fleet_impact"],
    }
    result["audit"] = audit
    append_audit_log(audit)

    return result


def action_shutdown(params: dict) -> dict:
    """Emergency device shutdown.

    Always proceeds if human-approved (policy ceiling ensures human approval).
    Reports fleet capacity impact for operator awareness.
    """
    device_id = params.get("device_id")
    reason = params.get("reason", "")
    schedule_inspection = params.get("schedule_inspection", False)

    if not device_id:
        return {"status": "rejected", "reason": "Missing required: device_id"}

    risk_scores, actions_data, metadata = load_fleet_data()
    spec = get_device_spec(device_id, metadata)
    if spec is None:
        return {"status": "rejected", "reason": f"Unknown device: {device_id}"}

    risk = get_device_risk(device_id, risk_scores)
    action = get_device_action(device_id, actions_data)

    # Compute capacity impact (informational — shutdown always proceeds if approved)
    fleet_hr = compute_fleet_hashrate(risk_scores, metadata)
    device_hr = risk["latest_snapshot"]["hashrate_th"] if risk else spec["nominal_hashrate_th"]
    post_fleet_hr = fleet_hr["current_th"] - device_hr
    post_fleet_pct = round(post_fleet_hr / fleet_hr["nominal_th"] * 100, 1) if fleet_hr["nominal_th"] else 0

    # MOS command: setPowerMode("sleep") for immediate shutdown
    mos_command = {
        "method": "setPowerMode",
        "params": {"mode": "sleep"},
    }

    result = {
        "status": "executed",
        "action": "shutdown",
        "device_id": device_id,
        "details": {
            "model": spec["model"],
            "schedule_inspection": schedule_inspection,
        },
        "mos_command": mos_command,
        "fleet_impact": {
            "device_hashrate_th": round(device_hr, 2),
            "pre_hashrate_pct": fleet_hr["current_pct"],
            "post_hashrate_pct": post_fleet_pct,
            "capacity_warning": post_fleet_pct < MIN_HASHRATE_PCT,
        },
        "risk_context": {
            "mean_risk": risk["mean_risk"] if risk else None,
            "tier": action["tier"] if action else None,
            "te_score": risk["latest_snapshot"]["te_score"] if risk else None,
        },
        "reason": reason,
    }

    audit = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "shutdown",
        "device_id": device_id,
        "params": {"schedule_inspection": schedule_inspection},
        "result": "executed",
        "reason": reason,
        "fleet_impact": result["fleet_impact"],
    }
    result["audit"] = audit
    append_audit_log(audit)

    return result


def main():
    parser = argparse.ArgumentParser(description="Fleet control action executor")
    parser.add_argument("--action", required=True,
                        choices=["underclock", "maintenance", "shutdown"],
                        help="Control action to execute")
    args = parser.parse_args()

    # Read parameters from VALIDANCE_PARAMS env var (JSON)
    params_raw = os.environ.get("VALIDANCE_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        print(json.dumps({"status": "rejected", "reason": "Invalid VALIDANCE_PARAMS JSON"}))
        sys.exit(1)

    # Dispatch action
    if args.action == "underclock":
        result = action_underclock(params)
    elif args.action == "maintenance":
        result = action_maintenance(params)
    elif args.action == "shutdown":
        result = action_shutdown(params)
    else:
        result = {"status": "rejected", "reason": f"Unknown action: {args.action}"}

    print(json.dumps(result, indent=2))

    # Write _validance_vars.json (standard task output convention)
    vars_path = Path("/work") / "_validance_vars.json"
    try:
        vars_path.parent.mkdir(parents=True, exist_ok=True)
        with open(vars_path, "w") as f:
            json.dump({
                "action": args.action,
                "device_id": params.get("device_id", ""),
                "status": result["status"],
            }, f)
    except OSError:
        pass  # Non-critical — container may not have /work writable

    # Exit with error code for rejections
    if result.get("status") == "rejected":
        sys.exit(1)


if __name__ == "__main__":
    main()
