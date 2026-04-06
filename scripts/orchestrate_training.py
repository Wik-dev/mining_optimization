#!/usr/bin/env python3
"""
Training Orchestrator — Sequential Chain (Pattern 1)
=====================================================
Chains composable workflows for the full training path:

    generate_corpus → pre_processing → train

Each workflow passes outputs to the next via ``continue_from``. All runs share
a single ``session_hash`` for audit traceability.

Usage:
    python scripts/orchestrate_training.py --api-url http://localhost:8001

    # Skip corpus generation (use existing CSV):
    python scripts/orchestrate_training.py --api-url http://localhost:8001 \\
        --telemetry-csv /work/training_telemetry.csv \\
        --metadata-json /work/training_metadata.json

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

logger = logging.getLogger("orchestrate_training")

POLL_INTERVAL = 5
POLL_TIMEOUT = 3600  # Training can take up to 20 min for full corpus


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


def get_file_url(session: requests.Session, api_url: str,
                 workflow_hash: str, file_name: str) -> str | None:
    """Get the storage URI for a workflow output file."""
    url = f"{api_url}/api/files/{workflow_hash}"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            for f in resp.json().get("files", []):
                if f.get("file_name") == file_name and f.get("is_output"):
                    return f.get("uri", "")
    except (ConnectionError, OSError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Training orchestrator (Pattern 1 chain)")
    parser.add_argument("--api-url", default="http://localhost:8001",
                        help="Validance API URL")
    parser.add_argument("--telemetry-csv", default=None,
                        help="Skip corpus generation; use this telemetry CSV")
    parser.add_argument("--metadata-json", default=None,
                        help="Skip corpus generation; use this metadata JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"

    session_hash = hashlib.sha256(
        f"training_{datetime.now().isoformat()}".encode()
    ).hexdigest()[:16]

    logger.info("Training orchestration — session=%s", session_hash)

    prev_hash = None

    # ── Step 1: Generate corpus (or skip) ──────────────────────────────────
    if args.telemetry_csv and args.metadata_json:
        logger.info("Skipping corpus generation — using provided files")
        telemetry_uri = args.telemetry_csv
        metadata_uri = args.metadata_json
    else:
        logger.info("Step 1/3: Generating training corpus (mdk.generate_corpus)")
        wf_hash = trigger_workflow(
            session, args.api_url, "mdk.generate_corpus",
            parameters={}, session_hash=session_hash,
        )
        logger.info("  Triggered: %s", wf_hash)
        poll_completion(session, args.api_url, "mdk.generate_corpus", wf_hash)
        logger.info("  Completed")

        telemetry_uri = get_file_url(session, args.api_url, wf_hash, "training_telemetry.csv")
        metadata_uri = get_file_url(session, args.api_url, wf_hash, "training_metadata.json")
        prev_hash = wf_hash

        if not telemetry_uri or not metadata_uri:
            logger.error("Failed to resolve corpus output URIs")
            sys.exit(1)

    # ── Step 2: Pre-processing ─────────────────────────────────────────────
    logger.info("Step 2/3: Pre-processing (mdk.pre_processing)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.pre_processing",
        parameters={
            "telemetry_csv_path": telemetry_uri,
            "metadata_json_path": metadata_uri,
        },
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.pre_processing", wf_hash)
    logger.info("  Completed")
    prev_hash = wf_hash

    # ── Step 3: Train ──────────────────────────────────────────────────────
    logger.info("Step 3/3: Training (mdk.train)")
    wf_hash = trigger_workflow(
        session, args.api_url, "mdk.train",
        parameters={},
        session_hash=session_hash,
        continue_from=prev_hash,
    )
    logger.info("  Triggered: %s", wf_hash)
    poll_completion(session, args.api_url, "mdk.train", wf_hash)
    logger.info("  Completed")

    # ── Summary ────────────────────────────────────────────────────────────
    model_uri = get_file_url(session, args.api_url, wf_hash, "anomaly_model.joblib")
    print(f"\nTraining complete:")
    print(f"  Session:       {session_hash}")
    print(f"  Train hash:    {wf_hash}")
    print(f"  Model artifact: {model_uri or '(not found)'}")


if __name__ == "__main__":
    main()
