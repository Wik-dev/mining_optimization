#!/usr/bin/env python3
"""
Simulation Orchestrator — Growing-Window Inference (Pattern 1 / 5a)
====================================================================
Host-side orchestrator that generates all scenario data upfront, then runs
a growing-window inference loop where each cycle sees accumulated history.

Architecture insight (growing-window):
  The model was trained on full scenarios with populated rolling windows
  (6h, 24h, 7d). If each inference cycle only sees its own 1h batch, all
  windows are truncated — feature distribution mismatch vs training data.

  In the real world, telemetry accumulates in a database. Each inference run
  queries the *last N hours of accumulated data*, so windows are always full.
  This orchestrator mirrors that: generate all data upfront (Phase 1), then
  run inference on a growing time slice per cycle (Phase 2).

Two-phase architecture:
  Phase 1: Generate all scenario data (one-shot)
    - Trigger mdk.generate_batch with interval_minutes = duration_days * 1440
    - Produces a single CSV covering the entire scenario timeline

  Phase 2: Growing-window inference loop
    - Read scenario JSON to get duration_days → derive total_cycles
    - Each cycle computes cutoff = start_time + (cycle+1) * interval_days
    - Trigger mdk.pre_processing with cutoff_timestamp parameter
    - Then mdk.score → mdk.analyze as before

Usage (CLI, Pattern 1):
    python scripts/orchestrate_simulation.py \\
        --scenario data/scenarios/asic_aging.json \\
        --training-hash 636e10ec2ad88f42 \\
        --api-url http://localhost:8001

    # With custom interval (7 days per cycle instead of default 1):
    python scripts/orchestrate_simulation.py \\
        --scenario data/scenarios/asic_aging.json \\
        --training-hash 636e10ec2ad88f42 \\
        --interval-days 7

Usage (Pattern 5a — from mdk.fleet_simulation container):
    Reads CTX_* env vars when CLI args are absent:
      CTX_SCENARIO_PATH, CTX_TRAINING_HASH, CTX_API_URL, CTX_INTERVAL_DAYS

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger("orchestrate_simulation")

# ── Configuration ────────────────────────────────────────────────────────────

POLL_INTERVAL = 5
POLL_TIMEOUT = 1800  # 30 min max per workflow

MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_PAUSE = 60

# SimulationEngine start time (hardcoded in simulation_engine.py)
SIM_START_TIME = datetime(2026, 4, 2, 0, 0, 0)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CycleResult:
    """Result of a single inference cycle."""
    cycle: int
    success: bool
    cutoff_timestamp: str = ""
    data_rows: int = 0
    pipeline_hash: str = ""
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass
class SimulationMetrics:
    """Accumulated metrics across all cycles."""
    session_hash: str = ""
    scenario: str = ""
    training_hash: str = ""
    total_cycles: int = 0
    cycles_completed: int = 0
    cycles_failed: int = 0
    interval_days: int = 1
    duration_days: int = 0
    batch_hash: str = ""
    results: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "session_hash": self.session_hash,
            "scenario": self.scenario,
            "training_hash": self.training_hash,
            "total_cycles": self.total_cycles,
            "cycles_completed": self.cycles_completed,
            "cycles_failed": self.cycles_failed,
            "interval_days": self.interval_days,
            "duration_days": self.duration_days,
            "batch_hash": self.batch_hash,
            "results": [
                {
                    "cycle": r.cycle,
                    "success": r.success,
                    "cutoff_timestamp": r.cutoff_timestamp,
                    "data_rows": r.data_rows,
                    "pipeline_hash": r.pipeline_hash,
                    "elapsed_seconds": round(r.elapsed_seconds, 1),
                    "error": r.error,
                }
                for r in self.results
            ],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ── API helpers (same pattern as orchestrate_training.py) ────────────────────

def trigger_workflow(session: requests.Session, api_url: str,
                     workflow_name: str, parameters: dict,
                     session_hash: str, continue_from: str = None) -> str:
    """Trigger a workflow and return its workflow_hash, with retry."""
    url = f"{api_url}/api/workflows/{workflow_name}/trigger"
    payload = {
        "parameters": parameters,
        "session_hash": session_hash,
    }
    if continue_from:
        payload["continue_from"] = continue_from

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.post(url, json=payload, timeout=30)
            if resp.status_code in (200, 201, 202):
                data = resp.json()
                return data.get("workflow_hash", data.get("hash", ""))
            elif resp.status_code >= 500:
                logger.warning("Trigger attempt %d/%d: HTTP %d — %s",
                               attempt + 1, MAX_RETRIES, resp.status_code,
                               resp.text[:200])
            else:
                raise RuntimeError(
                    f"Trigger {workflow_name} failed: HTTP {resp.status_code} — "
                    f"{resp.text[:200]}")
        except (ConnectionError, OSError) as e:
            logger.warning("Trigger attempt %d/%d: connection error — %s",
                           attempt + 1, MAX_RETRIES, e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF[attempt])

    raise RuntimeError(f"Trigger {workflow_name} failed after {MAX_RETRIES} retries")


def poll_completion(session: requests.Session, api_url: str,
                    workflow_name: str, workflow_hash: str) -> dict:
    """Poll until workflow completes or times out."""
    url = (f"{api_url}/api/workflows/{workflow_name}/status"
           f"?workflow_hash={workflow_hash}")
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
                    raise RuntimeError(
                        f"Workflow {workflow_name} ({workflow_hash}) failed: {data}")
        except (ConnectionError, OSError) as e:
            logger.debug("Poll error (will retry): %s", e)

        time.sleep(POLL_INTERVAL)

    raise TimeoutError(
        f"Workflow {workflow_name} ({workflow_hash}) did not complete "
        f"within {POLL_TIMEOUT}s")


def get_file_url(session: requests.Session, api_url: str,
                 workflow_hash: str, file_name: str,
                 retries: int = 6, delay: float = 5.0) -> Optional[str]:
    """Get the engine-registered URI for a workflow output file.

    Retries because large file uploads to Azure complete asynchronously —
    the workflow may be SUCCESS before file references appear in the DB.
    """
    url = f"{api_url}/api/files/{workflow_hash}"
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=10)
            if resp.status_code == 200:
                for f in resp.json().get("files", []):
                    if f.get("file_name") == file_name and f.get("is_output"):
                        return f.get("uri", "")
        except (ConnectionError, OSError):
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def get_variable(session: requests.Session, api_url: str,
                 workflow_hash: str, variable_name: str) -> Optional[str]:
    """Get a task output variable from a completed workflow."""
    url = f"{api_url}/api/variables/{workflow_hash}?variable_name={variable_name}"
    try:
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("value", data.get(variable_name))
    except (ConnectionError, OSError):
        pass
    return None


# ── Main orchestration ───────────────────────────────────────────────────────

def run_simulation(api_url: str, scenario_path: str, interval_days: int,
                   output_dir: str, training_hash: str) -> SimulationMetrics:
    """Execute growing-window simulation: generate all data, then infer per day.

    Phase 1: Generate all scenario data in one shot (single mdk.generate_batch),
             chained from training_hash via continue_from so the training outputs
             (model artifacts) are available in the deep context chain.
    Phase 2: For each cycle, run inference on [t=0 → t=cutoff] via cutoff_timestamp.

    Model resolution uses Validance's deep context propagation: generate_batch
    continues from training_hash, so the entire chain (training → batch →
    pre_processing → score → analyze) has access to @train_anomaly_model:*
    outputs. No explicit model_path parameter needed.
    """
    http = requests.Session()
    http.headers["Content-Type"] = "application/json"

    # Read scenario to get duration_days
    with open(scenario_path) as f:
        scenario = json.load(f)
    duration_days = scenario["duration_days"]
    total_cycles = math.ceil(duration_days / interval_days)

    session_hash = hashlib.sha256(
        f"simulation_{os.path.basename(scenario_path)}_{datetime.now().isoformat()}".encode()
    ).hexdigest()[:16]

    # For inner workflow triggers, we need a host-resolvable URI for the scenario.
    # Pattern 5a: the original trigger parameter (CTX_SCENARIO_PATH) is already a
    # host-resolvable file:// URI — use it directly. Container-local paths like
    # /work/simulation_orchestrator/scenario.json are NOT resolvable by the host engine.
    # CLI mode: scenario_path is a local host path — convert to file:// URI.
    original_scenario_uri = os.environ.get("CTX_SCENARIO_PATH")
    if original_scenario_uri and original_scenario_uri.startswith("file://"):
        scenario_uri = original_scenario_uri
    else:
        scenario_uri = f"file://{os.path.abspath(scenario_path)}"

    metrics = SimulationMetrics(
        session_hash=session_hash,
        scenario=os.path.basename(scenario_path),
        training_hash=training_hash,
        total_cycles=total_cycles,
        interval_days=interval_days,
        duration_days=duration_days,
        started_at=datetime.now().isoformat(),
    )

    logger.info("Simulation orchestration — session=%s, scenario=%s, "
                "duration=%dd, interval=%dd, cycles=%d, training=%s",
                session_hash, metrics.scenario, duration_days,
                interval_days, total_cycles, training_hash)

    # ── Phase 1: Generate all scenario data (one-shot) ────────────────────
    logger.info("Phase 1: Generating full scenario data (%d days)", duration_days)

    full_interval_minutes = duration_days * 1440
    batch_params = {
        "scenario_path": scenario_uri,
        "interval_minutes": str(full_interval_minutes),
    }

    # Chain from training_hash: this puts the training workflow's outputs
    # (model artifacts) into the deep context chain. All downstream workflows
    # (pre_processing → score → analyze) inherit this via recursive continuation,
    # so @train_anomaly_model:model_artifact resolves automatically.
    batch_hash = trigger_workflow(
        http, api_url, "mdk.generate_batch",
        parameters=batch_params,
        session_hash=session_hash,
        continue_from=training_hash,
    )
    logger.info("  generate_batch triggered: %s (full %d-day scenario, chained from training %s)",
                batch_hash, duration_days, training_hash)
    poll_completion(http, api_url, "mdk.generate_batch", batch_hash)
    logger.info("  generate_batch completed")

    # Get engine-registered URIs for the full dataset
    csv_uri = get_file_url(http, api_url, batch_hash, "batch_telemetry.csv")
    meta_uri = get_file_url(http, api_url, batch_hash, "batch_metadata.json")

    if not csv_uri or not meta_uri:
        raise RuntimeError(f"Failed to resolve batch output URIs from {batch_hash}")

    metrics.batch_hash = batch_hash
    _write_metrics(metrics, output_dir)

    # ── Phase 2: Growing-window inference loop ────────────────────────────
    logger.info("Phase 2: Growing-window inference (%d cycles)", total_cycles)

    prev_hash = batch_hash
    consecutive_failures = 0

    for cycle in range(total_cycles):
        # Circuit breaker
        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            logger.warning("Circuit breaker: %d consecutive failures, pausing %ds",
                           consecutive_failures, CIRCUIT_BREAKER_PAUSE)
            time.sleep(CIRCUIT_BREAKER_PAUSE)
            consecutive_failures = 0

        # Cutoff = start_time + (cycle+1) * interval_days
        cutoff = SIM_START_TIME + timedelta(days=(cycle + 1) * interval_days)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

        logger.info("Cycle %d/%d: cutoff=%s (day %d)",
                     cycle + 1, total_cycles, cutoff_str, (cycle + 1) * interval_days)

        result = CycleResult(cycle=cycle + 1, success=False, cutoff_timestamp=cutoff_str)
        t0 = time.monotonic()

        try:
            # ── Pre-processing with cutoff ─────────────────────────────
            pp_params = {
                "telemetry_csv_path": csv_uri,
                "metadata_json_path": meta_uri,
                "cutoff_timestamp": cutoff_str,
            }

            pp_hash = trigger_workflow(
                http, api_url, "mdk.pre_processing",
                parameters=pp_params,
                session_hash=session_hash,
                continue_from=prev_hash,
            )
            logger.info("  pre_processing triggered: %s", pp_hash)
            poll_completion(http, api_url, "mdk.pre_processing", pp_hash)
            logger.info("  pre_processing completed")

            # Get row_count from pre_processing for metrics
            row_count = get_variable(http, api_url, pp_hash, "row_count")
            if row_count:
                result.data_rows = int(row_count)

            # ── Score + Analyze ────────────────────────────────────────
            # No model_path parameter needed — score resolves model artifacts
            # via @train_anomaly_model:* deep context references (training hash
            # is at the root of the continuation chain).
            score_hash = trigger_workflow(
                http, api_url, "mdk.score",
                parameters={},
                session_hash=session_hash,
                continue_from=pp_hash,
            )
            logger.info("  score triggered: %s", score_hash)
            poll_completion(http, api_url, "mdk.score", score_hash)
            logger.info("  score completed")

            # Model metrics also resolved via deep context (@train_anomaly_model:model_metrics)
            analyze_hash = trigger_workflow(
                http, api_url, "mdk.analyze",
                parameters={},
                session_hash=session_hash,
                continue_from=score_hash,
            )
            logger.info("  analyze triggered: %s", analyze_hash)
            poll_completion(http, api_url, "mdk.analyze", analyze_hash)
            logger.info("  analyze completed")

            result.pipeline_hash = analyze_hash

            # ── Cycle success ──────────────────────────────────────────
            result.success = True
            prev_hash = analyze_hash
            consecutive_failures = 0

        except Exception as e:
            result.error = str(e)
            consecutive_failures += 1
            logger.error("Cycle %d failed: %s", cycle + 1, e)

        result.elapsed_seconds = time.monotonic() - t0
        metrics.results.append(result)

        if result.success:
            metrics.cycles_completed += 1
        else:
            metrics.cycles_failed += 1

        # Write intermediate metrics
        _write_metrics(metrics, output_dir)

    metrics.finished_at = datetime.now().isoformat()
    _write_metrics(metrics, output_dir)

    logger.info("Simulation complete: %d/%d cycles succeeded",
                metrics.cycles_completed, total_cycles)

    # Write output vars for Pattern 5a container (read by _validance_vars.json)
    _write_validance_vars(metrics, output_dir)

    return metrics


def _write_metrics(metrics: SimulationMetrics, output_dir: str):
    """Write current metrics to JSON file."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "simulation_metrics.json")
    with open(path, "w") as f:
        json.dump(metrics.to_dict(), f, indent=2)


def _write_validance_vars(metrics: SimulationMetrics, output_dir: str):
    """Write _validance_vars.json for Pattern 5a container integration."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "_validance_vars.json")
    with open(path, "w") as f:
        json.dump({
            "cycles_completed": metrics.cycles_completed,
            "cycles_failed": metrics.cycles_failed,
            "total_cycles": metrics.total_cycles,
            "session_hash": metrics.session_hash,
        }, f)


def main():
    parser = argparse.ArgumentParser(
        description="Simulation orchestrator — growing-window inference (Pattern 1 / 5a)")
    parser.add_argument("--scenario", type=str,
                        default=os.environ.get("CTX_SCENARIO_PATH"),
                        help="Path to scenario JSON file (or CTX_SCENARIO_PATH env)")
    parser.add_argument("--training-hash", type=str,
                        default=os.environ.get("CTX_TRAINING_HASH", ""),
                        help="Workflow hash of the training run. Model artifacts are "
                             "resolved via deep context (continue_from chain), not "
                             "explicit paths. Required.")
    parser.add_argument("--interval-days", type=int,
                        default=int(os.environ.get("CTX_INTERVAL_DAYS", "1")),
                        help="Simulated days per inference cycle (default: 1)")
    parser.add_argument("--api-url", type=str,
                        default=os.environ.get("CTX_API_URL", "http://localhost:8001"),
                        help="Validance API URL (default: http://localhost:8001)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory for simulation_metrics.json "
                             "(default: data/simulation/ or CWD in container)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not args.scenario:
        # Pattern 5a: scenario is staged as ./scenario.json by workflow inputs
        if os.path.exists("./scenario.json"):
            args.scenario = "./scenario.json"
        else:
            parser.error("--scenario is required (or set CTX_SCENARIO_PATH)")

    if not args.training_hash:
        parser.error("--training-hash is required (or set CTX_TRAINING_HASH)")

    if args.output_dir is None:
        # In container (Pattern 5a): write to CWD. On host: data/simulation/
        if os.environ.get("CTX_SCENARIO_PATH") or os.path.exists("./scenario.json"):
            args.output_dir = "."
        else:
            args.output_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "data", "simulation")

    metrics = run_simulation(
        api_url=args.api_url,
        scenario_path=args.scenario,
        interval_days=args.interval_days,
        output_dir=args.output_dir,
        training_hash=args.training_hash,
    )

    print(f"\nSimulation complete:")
    print(f"  Session:    {metrics.session_hash}")
    print(f"  Scenario:   {metrics.scenario} ({metrics.duration_days} days)")
    print(f"  Interval:   {metrics.interval_days} day(s) per cycle")
    print(f"  Completed:  {metrics.cycles_completed}/{metrics.total_cycles} cycles")
    print(f"  Failed:     {metrics.cycles_failed}")
    print(f"  Training:   {metrics.training_hash}")
    print(f"  Batch hash: {metrics.batch_hash}")
    print(f"  Metrics:    {args.output_dir}/simulation_metrics.json")

    if metrics.cycles_failed > metrics.cycles_completed:
        sys.exit(1)


if __name__ == "__main__":
    main()
