#!/usr/bin/env python3
"""
Inference Orchestrator — Sequential Chain (Pattern 1)
======================================================
Chains composable workflows for the inference path:

    pre_processing → score → analyze

Each workflow passes outputs to the next via ``continue_from``. All runs share
a single ``session_hash`` for audit traceability.

After completion, pipeline outputs in /work/ are available for SafeClaw fleet
queries (fleet_risk_scores.json, fleet_actions.json, trend_analysis.json,
report.html).

Usage:
    python scripts/orchestrate_inference.py \\
        --api-url http://localhost:8001 \\
        --telemetry-csv /work/fleet_telemetry.csv \\
        --metadata-json /work/fleet_metadata.json \\
        --model-path /work/anomaly_model.joblib

Timing / loop considerations:
    - Feature engineering over growing history is O(n); scoring window is
      configurable (default 24h)
    - Set inference interval to 30min-1h (not every 5 min) for prototype
    - If a cycle is still running when the next tick arrives, skip
      (existing circuit breaker pattern)
    - Future optimization: incremental feature computation, streaming inference

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime

import requests

logger = logging.getLogger("orchestrate_inference")

POLL_INTERVAL = 5
POLL_TIMEOUT = 1800


def trigger_workflow(session: requests.Session, api_url: str,
                     workflow_name: str, parameters: dict,
                     session_hash: str, continue_from: str = None) -> str:
    """Trigger a workflow and return its workflow_hash."""
    url = f"{api_url}/api/workflows/{workflow_name}/trigger"
    payload = {
        "parameters": parameters,
        "session_hash": session_hash,
    }
    if continue_from:
        payload["continue_from"] = continue_from

    resp = session.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("workflow_hash", data.get("hash", ""))


def poll_completion(session: requests.Session, api_url: str,
                    workflow_name: str, workflow_hash: str) -> dict:
    """Poll until workflow completes or times out."""
    url = f"{api_url}/api/workflows/{workflow_name}/status?workflow_hash={workflow_hash}"
    start = time.monotonic()

    while time.monotonic() - start < POLL_TIMEOUT:
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status", "unknown")
                if status in ("completed", "success"):
                    return data
                if status in ("failed", "error"):
                    raise RuntimeError(f"Workflow {workflow_name} ({workflow_hash}) failed: {data}")
        except (ConnectionError, OSError) as e:
            logger.debug("Poll error (will retry): %s", e)

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Workflow {workflow_name} ({workflow_hash}) did not complete within {POLL_TIMEOUT}s")


def main():
    parser = argparse.ArgumentParser(description="Inference orchestrator (Pattern 1 chain)")
    parser.add_argument("--api-url", default="http://localhost:8001",
                        help="Validance API URL")
    parser.add_argument("--telemetry-csv", required=True,
                        help="Path/URI to telemetry CSV")
    parser.add_argument("--metadata-json", required=True,
                        help="Path/URI to fleet metadata JSON")
    parser.add_argument("--model-path", required=True,
                        help="Path/URI to pre-trained anomaly_model.joblib")
    parser.add_argument("--regression-model-path", default="",
                        help="Path/URI to regression model (optional)")
    parser.add_argument("--model-registry-path", default="",
                        help="Path/URI to model_registry.json (optional)")
    parser.add_argument("--model-metrics-path", default="",
                        help="Path/URI to model_metrics.json (optional, for report)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    session_hash = hashlib.sha256(
        f"inference_{datetime.now().isoformat()}".encode()
    ).hexdigest()[:16]

    logger.info("Inference orchestration — session=%s", session_hash)

    # ── Step 1: Pre-processing ─────────────────────────────────────────────
    logger.info("Step 1/3: Pre-processing (mdk.pre_processing)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.pre_processing",
        parameters={
            "telemetry_csv_path": args.telemetry_csv,
            "metadata_json_path": args.metadata_json,
        },
        session_hash=session_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.pre_processing", wf_hash)
    logger.info("  Completed")
    prev_hash = wf_hash

    # ── Step 2: Score ──────────────────────────────────────────────────────
    logger.info("Step 2/3: Scoring (mdk.score)")
    score_params = {
        "model_path": args.model_path,
    }
    if args.regression_model_path:
        score_params["regression_model_path"] = args.regression_model_path
    if args.model_registry_path:
        score_params["model_registry_path"] = args.model_registry_path

    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.score",
        parameters=score_params,
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.score", wf_hash)
    logger.info("  Completed")
    prev_hash = wf_hash

    # ── Step 3: Analyze ────────────────────────────────────────────────────
    logger.info("Step 3/3: Analysis (mdk.analyze)")
    analyze_params = {}
    if args.model_metrics_path:
        analyze_params["model_metrics_path"] = args.model_metrics_path

    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.analyze",
        parameters=analyze_params,
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.analyze", wf_hash)
    logger.info("  Completed")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\nInference complete:")
    print(f"  Session:      {session_hash}")
    print(f"  Analyze hash: {wf_hash}")
    print(f"  Outputs available in /work/: fleet_risk_scores.json, "
          f"fleet_actions.json, trend_analysis.json, report.html")


if __name__ == "__main__":
    main()
