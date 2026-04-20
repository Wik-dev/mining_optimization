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

Optional post-inference step (Pattern A push):
  Step 4: Notify OpenClaw agent via gateway webhook (--gateway-url). Pushes
          inline pipeline refs (session_hash + input_files) so the agent can
          call SafeClaw fleet actions directly. No workspace file needed.

Usage:
    python scripts/orchestrate_inference.py \\
        --api-url https://api.validance.io \\
        --telemetry-csv /work/fleet_telemetry.csv \\
        --metadata-json /work/fleet_metadata.json \\
        --training-hash 636e10ec2ad88f42

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
import logging
import os
import sys
import time
from datetime import datetime

import requests

logger = logging.getLogger("orchestrate_inference")

POLL_INTERVAL = 5
POLL_TIMEOUT = 1800
NOT_FOUND_GRACE_SECONDS = 30  # Allow engine time to write execution record


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
            elif resp.status_code == 404:
                elapsed = time.monotonic() - start
                if elapsed > NOT_FOUND_GRACE_SECONDS:
                    raise RuntimeError(
                        f"Workflow {workflow_name} ({workflow_hash}) not found "
                        f"after {elapsed:.0f}s — likely failed during engine "
                        f"initialization (check engine logs)")
        except (ConnectionError, OSError) as e:
            logger.debug("Poll error (will retry): %s", e)

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Workflow {workflow_name} ({workflow_hash}) did not complete within {POLL_TIMEOUT}s")


def _notify_agent(gateway_url: str, gateway_token: str, message: str) -> bool:
    """POST to OpenClaw /hooks/agent to trigger AI reasoning on flagged devices.

    Uses the gateway's webhook endpoint to inject a message into the active
    agent session. The agent then follows HEARTBEAT.md to propose fleet
    actions via SafeClaw.
    """
    url = f"{gateway_url.rstrip('/')}/hooks/agent"
    try:
        resp = requests.post(
            url,
            json={"message": message},
            headers={"Authorization": f"Bearer {gateway_token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info("  Agent notified (runId=%s)", data.get("runId", "?"))
            return True
        else:
            logger.warning("  Agent push failed: HTTP %d — %s",
                           resp.status_code, resp.text[:200])
    except (ConnectionError, OSError) as e:
        logger.warning("  Agent push failed: %s", e)
    return False


def _resolve_corpus_from_chain(session: requests.Session, api_url: str,
                                training_hash: str) -> tuple:
    """Walk training → pre_processing → corpus chain to find telemetry URIs."""
    current = training_hash
    for _ in range(5):  # max chain depth
        url = f"{api_url}/api/workflows/mdk.train/status?workflow_hash={current}"
        resp = session.get(url, timeout=10)
        if resp.status_code != 200:
            # Try other workflow names
            for wf in ["mdk.pre_processing", "mdk.generate_corpus"]:
                url = f"{api_url}/api/workflows/{wf}/status?workflow_hash={current}"
                resp = session.get(url, timeout=10)
                if resp.status_code == 200:
                    break
        if resp.status_code != 200:
            break
        data = resp.json()
        wf_name = data.get("workflow_name", "")

        # If this is a corpus generation run, get its output file URIs
        if wf_name == "mdk.generate_corpus":
            vars_url = f"{api_url}/api/variables/{current}"
            vr = session.get(vars_url, timeout=10)
            if vr.status_code == 200:
                vdata = vr.json()
                variables = vdata.get("variables", vdata) if isinstance(vdata, dict) else vdata
                telemetry = metadata = None
                for var in variables:
                    if var.get("variable_type") == "file":
                        if var.get("variable_name") == "telemetry_csv":
                            telemetry = var.get("file_uri")
                        elif var.get("variable_name") == "metadata_json":
                            metadata = var.get("file_uri")
                if telemetry and metadata:
                    return telemetry, metadata

        # Follow continuation chain
        parent = data.get("parameters", {}).get("continued_from")
        if not parent:
            break
        current = parent

    return None, None


def main():
    parser = argparse.ArgumentParser(description="Inference orchestrator (Pattern 1 chain)")
    parser.add_argument("--api-url", default="https://api.validance.io",
                        help="Validance API URL")
    parser.add_argument("--telemetry-csv", default=None,
                        help="Path/URI to telemetry CSV (default: resolved from training chain)")
    parser.add_argument("--metadata-json", default=None,
                        help="Path/URI to fleet metadata JSON (default: resolved from training chain)")
    parser.add_argument("--training-hash", required=True,
                        help="Workflow hash of the training run. Model artifacts "
                             "resolved via deep context (continue_from chain).")
    parser.add_argument("--gateway-url", type=str,
                        help="OpenClaw gateway URL for AI agent push "
                             "(default: CTX_GATEWAY_URL env var). "
                             "When set, notifies the agent after inference completes.")
    parser.add_argument("--gateway-token", type=str,
                        help="OpenClaw gateway hooks auth token "
                             "(default: CTX_GATEWAY_TOKEN env var)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    session_hash = hashlib.sha256(
        f"inference_{datetime.now().isoformat()}".encode()
    ).hexdigest()[:16]

    logger.info("Inference orchestration — session=%s", session_hash)

    # ── Resolve telemetry inputs from training chain if not provided ──────
    if not args.telemetry_csv or not args.metadata_json:
        logger.info("Resolving telemetry inputs from training chain %s", args.training_hash)
        telemetry_csv, metadata_json = _resolve_corpus_from_chain(
            session, args.api_url, args.training_hash)
        if not args.telemetry_csv:
            args.telemetry_csv = telemetry_csv
        if not args.metadata_json:
            args.metadata_json = metadata_json
        if not args.telemetry_csv or not args.metadata_json:
            logger.error("Could not resolve telemetry inputs from training chain. "
                         "Provide --telemetry-csv and --metadata-json explicitly.")
            sys.exit(1)
        logger.info("  telemetry: %s", args.telemetry_csv[:80])
        logger.info("  metadata:  %s", args.metadata_json[:80])

    # ── Step 1: Pre-processing ─────────────────────────────────────────────
    # Chain from training_hash: puts training outputs (model artifacts) in the
    # deep context chain. Downstream score/analyze resolve @train_anomaly_model:*
    # references automatically via recursive continuation.
    logger.info("Step 1/3: Pre-processing (mdk.pre_processing, chained from training %s)",
                args.training_hash)
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.pre_processing",
        parameters={
            "telemetry_csv_path": args.telemetry_csv,
            "metadata_json_path": args.metadata_json,
        },
        session_hash=session_hash,
        continue_from=args.training_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.pre_processing", wf_hash)
    logger.info("  Completed")
    pp_hash = wf_hash

    # ── Step 2: Score ──────────────────────────────────────────────────────
    # No model_path parameter — score resolves @train_anomaly_model:* from deep context.
    logger.info("Step 2/3: Scoring (mdk.score)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.score",
        parameters={},
        session_hash=session_hash,
        continue_from=pp_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.score", wf_hash)
    logger.info("  Completed")
    score_hash = wf_hash

    # ── Step 3: Analyze ────────────────────────────────────────────────────
    # Model metrics also resolved via deep context (@train_anomaly_model:model_metrics).
    logger.info("Step 3/3: Analysis (mdk.analyze)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.analyze",
        parameters={},
        session_hash=session_hash,
        continue_from=score_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.analyze", wf_hash)
    logger.info("  Completed")
    analyze_hash = wf_hash

    # ── Step 4 (optional): Notify OpenClaw agent — Pattern A push ────────
    # POST to OpenClaw gateway /hooks/agent with inline pipeline refs.
    # The agent follows HEARTBEAT.md: calls fleet_status_query with the refs
    # to get live data, reasons about flagged devices, proposes actions.
    # Same pattern as orchestrate_simulation.py — no workspace file needed.
    gateway_url = args.gateway_url or os.environ.get("CTX_GATEWAY_URL", "")
    gateway_token = args.gateway_token or os.environ.get("CTX_GATEWAY_TOKEN", "")
    if gateway_url and gateway_token:
        logger.info("Step 4: Notifying OpenClaw agent via gateway (%s)", gateway_url)
        message = (
            f"Fleet inference pipeline completed "
            f"(cutoff: {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')}).\n"
            f"session_hash: {session_hash}\n"
            f"input_files:\n"
            f"  fleet_risk_scores.json: @{score_hash}.score_fleet:risk_scores\n"
            f"  fleet_metadata.json: @{pp_hash}.ingest_telemetry:metadata\n"
            f"Follow HEARTBEAT.md."
        )
        _notify_agent(gateway_url, gateway_token, message)
    elif gateway_url and not gateway_token:
        logger.warning("--gateway-url set but --gateway-token missing — agent push disabled")
    else:
        logger.info("Skipping agent push (no --gateway-url configured)")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\nInference complete:")
    print(f"  Session:      {session_hash}")
    print(f"  Analyze hash: {analyze_hash}")
    print(f"  Outputs available in /work/: fleet_risk_scores.json, "
          f"fleet_actions.json, trend_analysis.json, report.html")


if __name__ == "__main__":
    main()
