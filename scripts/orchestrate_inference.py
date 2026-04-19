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

Optional post-inference steps (Pattern A push):
  Step 4: Write fleet_summary.json to OpenClaw workspace (--workspace)
  Step 5: Notify OpenClaw agent via CLI (--openclaw-bin). Agent reads
          fleet_summary.json, reasons, proposes actions via safeclaw(). Response
          returned programmatically AND delivered to Telegram.

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
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

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


SCRIPTS_DIR = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Inference orchestrator (Pattern 1 chain)")
    parser.add_argument("--api-url", default="https://api.validance.io",
                        help="Validance API URL")
    parser.add_argument("--telemetry-csv", required=True,
                        help="Path/URI to telemetry CSV")
    parser.add_argument("--metadata-json", required=True,
                        help="Path/URI to fleet metadata JSON")
    parser.add_argument("--training-hash", required=True,
                        help="Workflow hash of the training run. Model artifacts "
                             "resolved via deep context (continue_from chain).")
    parser.add_argument("--workspace",
                        help="OpenClaw workspace path for fleet summary output "
                             "(default: OPENCLAW_WORKSPACE env var)")
    parser.add_argument("--openclaw-bin",
                        help="Path to openclaw CLI binary "
                             "(default: OPENCLAW_BIN env var, or 'openclaw')")
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
    prev_hash = wf_hash

    # ── Step 2: Score ──────────────────────────────────────────────────────
    # No model_path parameter — score resolves @train_anomaly_model:* from deep context.
    logger.info("Step 2/3: Scoring (mdk.score)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.score",
        parameters={},
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.score", wf_hash)
    logger.info("  Completed")
    prev_hash = wf_hash

    # ── Step 3: Analyze ────────────────────────────────────────────────────
    # Model metrics also resolved via deep context (@train_anomaly_model:model_metrics).
    logger.info("Step 3/3: Analysis (mdk.analyze)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.analyze",
        parameters={},
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.analyze", wf_hash)
    logger.info("  Completed")

    # ── Step 4 (optional): Write fleet summary to OpenClaw workspace ──────
    workspace = getattr(args, 'workspace', None) or os.environ.get('OPENCLAW_WORKSPACE')
    if workspace:
        logger.info("Step 4: Writing fleet summary to %s", workspace)
        # Deploy HEARTBEAT.md on first run (idempotent — overwrites if present)
        heartbeat_path = Path(workspace) / "HEARTBEAT.md"
        deploy_flag = [] if heartbeat_path.exists() else ["--deploy-heartbeat"]
        summary_result = subprocess.run([
            sys.executable, str(SCRIPTS_DIR / "write_fleet_summary.py"),
            "--analyze-hash", wf_hash,
            "--session-hash", session_hash,
            "--workspace", workspace,
            "--api-url", args.api_url,
        ] + deploy_flag, capture_output=True, text=True)
        if summary_result.returncode != 0:
            logger.warning("Fleet summary write failed: %s", summary_result.stderr)
        else:
            logger.info("Fleet summary written to %s/fleet_summary.json", workspace)
    else:
        logger.info("Skipping fleet summary (no --workspace or OPENCLAW_WORKSPACE configured)")

    # ── Step 5 (optional): Notify OpenClaw agent — Pattern A push ────────
    # Uses `openclaw agent` CLI to send a message through the gateway's
    # WebSocket RPC. The agent processes the message, reads fleet_summary.json,
    # reasons about flagged devices, and may call safeclaw() to propose actions.
    # Response returned programmatically AND delivered to Telegram (--deliver).
    # Heartbeat (Pattern B) runs independently on its own timer.
    openclaw_bin = args.openclaw_bin or os.environ.get('OPENCLAW_BIN', 'openclaw')
    if workspace:
        logger.info("Step 5: Notifying OpenClaw agent via CLI (%s)", openclaw_bin)
        message = (
            f"[{datetime.utcnow().strftime('%a %Y-%m-%d %H:%M UTC')}] "
            "Fleet inference pipeline complete. "
            f"New fleet_summary.json written (session {session_hash}). "
            "Read it and act on any flagged devices per HEARTBEAT.md instructions."
        )
        # Build CLI command. --dev flag uses the dev profile (port 19001).
        # --deliver sends the response to the configured Telegram channel.
        # --agent main targets the main agent session.
        # --json returns structured output for programmatic consumption.
        cmd = [
            openclaw_bin, "--dev", "agent",
            "--agent", "main",
            "--message", message,
            "--deliver",
            "--json",
            "--timeout", "180",
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=200,
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    status = data.get("status", "unknown")
                    logger.info("  OpenClaw agent responded: %s", status)
                except json.JSONDecodeError:
                    logger.info("  OpenClaw agent responded (non-JSON): %s",
                                result.stdout[:200].strip())
            else:
                logger.warning("  OpenClaw agent CLI failed (exit %d): %s",
                               result.returncode, result.stderr[:200].strip())
        except subprocess.TimeoutExpired:
            logger.warning("  OpenClaw agent CLI timed out (agent may still be processing)")
        except FileNotFoundError:
            logger.warning("  openclaw binary not found at '%s'. "
                           "Set --openclaw-bin or OPENCLAW_BIN.", openclaw_bin)
    else:
        logger.info("Skipping OpenClaw push (no --workspace configured)")

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\nInference complete:")
    print(f"  Session:      {session_hash}")
    print(f"  Analyze hash: {wf_hash}")
    print(f"  Outputs available in /work/: fleet_risk_scores.json, "
          f"fleet_actions.json, trend_analysis.json, report.html")


if __name__ == "__main__":
    main()
