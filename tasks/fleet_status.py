#!/usr/bin/env python3
"""
Fleet Status Query — Read-only fleet health inspector
======================================================
Invoked by the ``fleet_status_query`` catalog template via the Validance
proposal pipeline. Reads pipeline output files from /work/fleet/ and
returns structured JSON to stdout (captured by the engine).

Query types:
    summary        — fleet-wide: tier counts, flagged count, avg TE score,
                     worst device, total hashrate
    device_detail  — single device: risk assessment, latest telemetry snapshot,
                     stock specs, controller commands, MOS codes
    tier_breakdown — devices grouped by tier (CRITICAL/WARNING/DEGRADED/HEALTHY)
    risk_ranking   — all devices sorted by mean_risk descending

Data sources (read-only from /work/fleet/):
    fleet_risk_scores.json  — from score.py
    fleet_actions.json      — from optimize.py
    fleet_metadata.json     — device specs

Pure Python stdlib — no pandas, no ML dependencies.
"""

import json
import os
import sys
from pathlib import Path

# Pipeline output directory — mounted at /work/fleet/ in the container
FLEET_DATA_DIR = Path(os.environ.get("FLEET_DATA_DIR", "/work/fleet"))


def load_json(filename: str, required: bool = True):
    """Load a JSON file from the fleet data directory.

    Args:
        filename: Name of the JSON file.
        required: If True (default), exit with error when missing.
            If False, return None silently.
    """
    path = FLEET_DATA_DIR / filename
    if not path.exists():
        if not required:
            return None
        print(json.dumps({
            "status": "error",
            "error": f"Data file not found: {filename}",
        }))
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def query_summary(risk_scores: dict, actions_data: dict, metadata: dict) -> dict:
    """Fleet-wide summary: tier counts, flagged, avg TE, worst device, hashrate."""
    risks = risk_scores["device_risks"]
    flagged_count = sum(1 for r in risks if r["flagged"])
    avg_te_score = (
        sum(r["latest_snapshot"]["te_score"] for r in risks) / len(risks)
        if risks else 0.0
    )
    # Worst device = highest mean_risk
    worst = max(risks, key=lambda r: r["mean_risk"]) if risks else None
    # Total current hashrate from latest snapshots
    total_hashrate = sum(r["latest_snapshot"]["hashrate_th"] for r in risks)
    # Nominal fleet hashrate from metadata
    nominal_hashrate = sum(d["nominal_hashrate_th"] for d in metadata["fleet"])

    return {
        "status": "ok",
        "query_type": "summary",
        "fleet_size": len(risks),
        "flagged_count": flagged_count,
        "avg_te_score": round(avg_te_score, 4),
        "worst_device": worst["device_id"] if worst else None,
        "worst_mean_risk": round(worst["mean_risk"], 4) if worst else None,
        "total_hashrate_th": round(total_hashrate, 2),
        "nominal_hashrate_th": round(nominal_hashrate, 2),
        "hashrate_pct": round(total_hashrate / nominal_hashrate * 100, 1) if nominal_hashrate else 0,
        "tier_counts": actions_data.get("tier_counts", {}) if actions_data else {},
        "scoring_window": {
            "start": risk_scores.get("window_start"),
            "end": risk_scores.get("window_end"),
        },
        "controller_version": actions_data.get("controller_version") if actions_data else None,
    }


def query_device_detail(device_id: str, risk_scores: dict, actions_data: dict,
                        metadata: dict) -> dict:
    """Single device: risk, telemetry snapshot, stock specs, commands, MOS codes."""
    # Find device in risk scores
    device_risk = None
    for r in risk_scores["device_risks"]:
        if r["device_id"] == device_id:
            device_risk = r
            break

    if device_risk is None:
        return {
            "status": "error",
            "error": f"Device not found in risk scores: {device_id}",
        }

    # Find device in actions (actions_data may be None if fleet_actions.json absent)
    device_action = None
    if actions_data:
        for a in actions_data["actions"]:
            if a["device_id"] == device_id:
                device_action = a
                break

    # Find device specs in metadata
    device_spec = None
    for d in metadata["fleet"]:
        if d["device_id"] == device_id:
            device_spec = d
            break

    return {
        "status": "ok",
        "query_type": "device_detail",
        "device_id": device_id,
        "risk_assessment": {
            "mean_risk": device_risk["mean_risk"],
            "max_risk": device_risk["max_risk"],
            "pct_flagged": device_risk["pct_flagged"],
            "last_risk": device_risk["last_risk"],
            "flagged": device_risk["flagged"],
        },
        "latest_snapshot": device_risk["latest_snapshot"],
        "stock_specs": device_spec,
        "controller": {
            "tier": device_action["tier"] if device_action else None,
            "commands": device_action["commands"] if device_action else [],
            "rationale": device_action["rationale"] if device_action else [],
            "mos_alert_codes": device_action.get("mos_alert_codes", []) if device_action else [],
        },
    }


def query_tier_breakdown(actions_data) -> dict:
    """Devices grouped by tier (CRITICAL/WARNING/DEGRADED/HEALTHY)."""
    if not actions_data:
        return {"status": "ok", "query_type": "tier_breakdown",
                "tier_counts": {}, "tiers": {},
                "note": "fleet_actions.json not available"}
    tiers = {}
    for a in actions_data["actions"]:
        tier = a["tier"]
        if tier not in tiers:
            tiers[tier] = []
        tiers[tier].append({
            "device_id": a["device_id"],
            "model": a["model"],
            "risk_score": a["risk_score"],
            "te_score": a["te_score"],
            "command_count": len(a["commands"]),
        })

    return {
        "status": "ok",
        "query_type": "tier_breakdown",
        "tier_counts": actions_data.get("tier_counts", {}),
        "tiers": tiers,
    }


def query_risk_ranking(risk_scores: dict) -> dict:
    """All devices sorted by mean_risk descending."""
    ranked = sorted(
        risk_scores["device_risks"],
        key=lambda r: r["mean_risk"],
        reverse=True,
    )
    return {
        "status": "ok",
        "query_type": "risk_ranking",
        "threshold": risk_scores["threshold"],
        "devices": [
            {
                "device_id": r["device_id"],
                "model": r["model"],
                "mean_risk": r["mean_risk"],
                "max_risk": r["max_risk"],
                "flagged": r["flagged"],
                "te_score": r["latest_snapshot"]["te_score"],
            }
            for r in ranked
        ],
    }


def main():
    # Read parameters from VALIDANCE_PARAMS env var (JSON)
    params_raw = os.environ.get("VALIDANCE_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "error": "Invalid VALIDANCE_PARAMS JSON"}))
        sys.exit(1)

    query_type = params.get("query_type")
    if not query_type:
        print(json.dumps({"status": "error", "error": "Missing required parameter: query_type"}))
        sys.exit(1)

    # Load fleet data files (fleet_actions.json is optional — Pattern 5a
    # simulation runs don't register it in task_variables)
    risk_scores = load_json("fleet_risk_scores.json")
    actions_data = load_json("fleet_actions.json", required=False)
    metadata = load_json("fleet_metadata.json")

    # Dispatch query
    if query_type == "summary":
        result = query_summary(risk_scores, actions_data, metadata)
    elif query_type == "device_detail":
        device_id = params.get("device_id")
        if not device_id:
            result = {"status": "error", "error": "device_detail requires device_id parameter"}
        else:
            result = query_device_detail(device_id, risk_scores, actions_data, metadata)
    elif query_type == "tier_breakdown":
        result = query_tier_breakdown(actions_data)
    elif query_type == "risk_ranking":
        result = query_risk_ranking(risk_scores)
    else:
        result = {"status": "error", "error": f"Unknown query_type: {query_type}"}

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
