#!/usr/bin/env python3
"""
Fleet Summary Writer
====================
Reads inference pipeline outputs from the Validance API and writes a
consolidated ``fleet_summary.json`` to the OpenClaw agent workspace.

The summary gives the LLM pre-built ``@hash.task:var`` references so it can
include ``input_files`` in SafeClaw proposals without knowing pipeline internals.

Usage:
    python scripts/write_fleet_summary.py \
        --analyze-hash HASH \
        --session-hash HASH \
        --workspace PATH \
        --api-url URL \
        [--score-hash HASH]        # optional, skips session discovery
        [--preproc-hash HASH]      # optional, skips session discovery
        [--deploy-heartbeat]       # copy HEARTBEAT.md template to workspace

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger("write_fleet_summary")

SCRIPTS_DIR = Path(__file__).resolve().parent
HEARTBEAT_TEMPLATE = SCRIPTS_DIR.parent / "openclaw" / "HEARTBEAT.md"


def discover_hash_by_workflow(session: requests.Session, api_url: str,
                              session_hash: str, workflow_name: str) -> str | None:
    """Find a workflow hash by name within a session.

    API returns: {"session_hash": "...", "workflows": [...], "total": N}
    Each workflow entry has: workflow_hash, workflow_name, status, ...
    """
    url = f"{api_url}/api/executions?session={session_hash}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    workflows = data.get("workflows", []) if isinstance(data, dict) else data
    # Prefer the last successful match (retries appear after failures)
    best = None
    for wf in workflows:
        if wf.get("workflow_name") == workflow_name:
            if wf.get("status") == "SUCCESS":
                best = wf.get("workflow_hash")
            elif best is None:
                best = wf.get("workflow_hash")

    return best


def get_task_variable_uri(session: requests.Session, api_url: str,
                          workflow_hash: str, task_name: str,
                          var_name: str) -> str | None:
    """Get a task variable's file_uri from the Validance API.

    API returns: {"workflow_hash": "...", "variables": [...]}
    Each variable: {task_name, variable_name, variable_value, variable_type, file_uri}
    """
    url = f"{api_url}/api/variables/{workflow_hash}?task_name={task_name}"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    variables = data.get("variables", []) if isinstance(data, dict) else data
    for var in variables:
        if var.get("variable_name") == var_name:
            return var.get("file_uri")

    return None


def get_task_output_files(session: requests.Session, api_url: str,
                          workflow_hash: str, task_name: str) -> list[dict]:
    """Get output file metadata for a task.

    API returns: {"workflow_hash": "...", "workflow_name": "...", "files": [...], "total": N}
    Each file: {file_name, file_type, file_size, file_hash, is_input, is_output, uri, task_name, created_at}
    """
    url = f"{api_url}/api/files/{workflow_hash}?task_name={task_name}&file_type=output"
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("files", []) if isinstance(data, dict) else data


def fetch_fleet_actions(session: requests.Session, api_url: str,
                        analyze_hash: str) -> dict | None:
    """Fetch fleet_actions.json content from the optimize_fleet task output.

    Download endpoint: GET /api/files/{workflow_hash}/download?file_uri=...
    File entries use ``file_name`` (not filename/name).
    """
    files = get_task_output_files(session, api_url, analyze_hash, "optimize_fleet")
    for f in files:
        name = f.get("file_name", "")
        if "fleet_actions" in name:
            uri = f.get("uri", "")
            if uri:
                dl_url = f"{api_url}/api/files/{analyze_hash}/download?file_uri={uri}"
                dl_resp = session.get(dl_url, timeout=30)
                if dl_resp.status_code == 200:
                    return dl_resp.json()
    return None


def build_summary(session: requests.Session, api_url: str,
                  analyze_hash: str, score_hash: str,
                  preproc_hash: str, session_hash: str) -> dict:
    """Build the fleet_summary.json structure."""
    # Build input_files_refs using @hash.task:var notation
    score_ref = f"@{score_hash}.score_fleet:risk_scores"
    metadata_ref = f"@{preproc_hash}.ingest_telemetry:metadata"

    input_files_refs = {
        "fleet_risk_scores.json": score_ref,
        "fleet_metadata.json": metadata_ref,
    }

    # Try to fetch fleet actions for device-level detail
    fleet_actions = fetch_fleet_actions(session, api_url, analyze_hash)

    flagged_devices = []
    total_devices = 0
    flagged_count = 0
    worst_device = None
    worst_score = 0.0

    if fleet_actions:
        # fleet_actions.json structure: {"actions": [...], "fleet_summary": {...}}
        actions = fleet_actions.get("actions", [])
        fleet_meta = fleet_actions.get("fleet_summary", fleet_actions.get("summary", {}))
        total_devices = fleet_meta.get("total_devices", len(actions))

        for action in actions:
            device_id = action.get("device_id", "unknown")
            risk_score = action.get("risk_score", 0.0)
            risk_factors = action.get("risk_factors", [])
            recommended = action.get("recommended_action") or action.get("action", "monitor")

            # Flag devices with actionable risk scores
            if risk_score > 0.5 or recommended != "monitor":
                flagged_count += 1
                flagged_devices.append({
                    "device_id": device_id,
                    "risk_score": risk_score,
                    "risk_factors": risk_factors,
                    "recommended_action": recommended,
                    "input_files_refs": input_files_refs,
                })

            if risk_score > worst_score:
                worst_score = risk_score
                worst_device = device_id
    else:
        logger.warning("Could not fetch fleet_actions.json — summary will have no device detail")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_hash": session_hash,
        "pipeline_hashes": {
            "preproc": preproc_hash,
            "score": score_hash,
            "analyze": analyze_hash,
        },
        "fleet_overview": {
            "total_devices": total_devices,
            "flagged_count": flagged_count,
            "worst_device": worst_device,
        },
        "flagged_devices": flagged_devices,
    }


def main():
    parser = argparse.ArgumentParser(description="Write fleet summary to OpenClaw workspace")
    parser.add_argument("--analyze-hash", required=True,
                        help="Workflow hash of the analyze run")
    parser.add_argument("--session-hash", required=True,
                        help="Session hash for pipeline discovery")
    parser.add_argument("--workspace", required=True,
                        help="OpenClaw workspace path (e.g. ~/.openclaw/workspace)")
    parser.add_argument("--api-url", default="https://api.validance.io",
                        help="Validance API URL")
    parser.add_argument("--score-hash",
                        help="Override: workflow hash for mdk.score (skips discovery)")
    parser.add_argument("--preproc-hash",
                        help="Override: workflow hash for mdk.pre_processing (skips discovery)")
    parser.add_argument("--deploy-heartbeat", action="store_true",
                        help="Copy HEARTBEAT.md template to workspace")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    http = requests.Session()
    http.headers["Content-Type"] = "application/json"
    api_url = args.api_url.rstrip("/")
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    # Resolve workflow hashes
    score_hash = args.score_hash
    preproc_hash = args.preproc_hash

    if not score_hash:
        logger.info("Discovering mdk.score hash from session %s", args.session_hash)
        score_hash = discover_hash_by_workflow(http, api_url, args.session_hash, "mdk.score")
        if not score_hash:
            logger.error("Could not find mdk.score workflow in session %s", args.session_hash)
            sys.exit(1)
        logger.info("  Found: %s", score_hash)

    if not preproc_hash:
        logger.info("Discovering mdk.pre_processing hash from session %s", args.session_hash)
        preproc_hash = discover_hash_by_workflow(http, api_url, args.session_hash, "mdk.pre_processing")
        if not preproc_hash:
            logger.error("Could not find mdk.pre_processing workflow in session %s", args.session_hash)
            sys.exit(1)
        logger.info("  Found: %s", preproc_hash)

    # Build and write summary
    logger.info("Building fleet summary (analyze=%s, score=%s, preproc=%s)",
                args.analyze_hash, score_hash, preproc_hash)
    summary = build_summary(http, api_url, args.analyze_hash, score_hash,
                            preproc_hash, args.session_hash)

    output_path = workspace / "fleet_summary.json"
    output_path.write_text(json.dumps(summary, indent=2) + "\n")
    logger.info("Written: %s", output_path)

    # Deploy HEARTBEAT.md if requested
    if args.deploy_heartbeat:
        heartbeat_dest = workspace / "HEARTBEAT.md"
        if HEARTBEAT_TEMPLATE.exists():
            shutil.copy2(HEARTBEAT_TEMPLATE, heartbeat_dest)
            logger.info("Deployed HEARTBEAT.md to %s", heartbeat_dest)
        else:
            logger.warning("HEARTBEAT.md template not found at %s", HEARTBEAT_TEMPLATE)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
