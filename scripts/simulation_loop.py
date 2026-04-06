#!/usr/bin/env python3
"""
Continuous Simulation Loop — Phase 2 Orchestrator
===================================================
Runs inside a persistent container as an engine-managed task (Pattern 5a from
orchestration-patterns.md). Generates telemetry in intervals via SimulationEngine,
triggers pipeline runs for each interval, and chains runs into a session via
continuation hashes.

Cycle 0 (training):
    SimulationEngine.advance(24h) → batch CSV →
    mdk.pre_processing → mdk.train (chained via continue_from)

Cycles 1..N (inference):
    SimulationEngine.advance(1h) → batch CSV →
    mdk.pre_processing → mdk.score → mdk.analyze (chained via continue_from)

Error handling:
    - Retry with exponential backoff (3 attempts: 5s, 15s, 45s)
    - Circuit breaker: 3 consecutive failures → 60s pause
    - Failed cycles logged and skipped (loop continues)

Usage:
    python scripts/simulation_loop.py \\
        --scenario data/scenarios/baseline.json \\
        --cycles 12 --api-url http://localhost:8000 --offline

    # Offline mode: skip API calls, just run simulation + write metrics
    python scripts/simulation_loop.py \\
        --scenario data/scenarios/summer_heatwave.json \\
        --cycles 24 --offline

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Import SimulationEngine from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation_engine import SimulationEngine

logger = logging.getLogger("simulation_loop")

# ── Retry / circuit breaker configuration ────────────────────────────────────
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]        # seconds between retries
CIRCUIT_BREAKER_THRESHOLD = 3       # consecutive failures before pause
CIRCUIT_BREAKER_PAUSE = 60          # seconds to pause after circuit break
POLL_INTERVAL = 5                   # seconds between status polls
POLL_TIMEOUT = 1800                 # max seconds to wait for workflow completion

# Training cycle generates 24h of data to ensure enough samples for model
# training (at 5-min intervals, 24h = 288 ticks × N devices).
TRAINING_INTERVAL_MINUTES = 24 * 60  # 1440 min = 1 day
# Inference cycles generate 1h of data per interval.
INFERENCE_INTERVAL_MINUTES = 60


@dataclass
class CycleResult:
    """Result of a single simulation cycle."""
    cycle: int
    mode: str                       # "training" or "inference"
    success: bool
    batch_csv: str = ""
    batch_meta: str = ""
    workflow_hash: str = ""
    model_path: str = ""
    elapsed_seconds: float = 0.0
    error: str = ""
    sim_timestamp: str = ""


@dataclass
class SimulationMetrics:
    """Accumulated metrics across all cycles."""
    session_hash: str = ""
    scenario: str = ""
    cycles_completed: int = 0
    cycles_failed: int = 0
    cycles_total: int = 0
    training_workflow_hash: str = ""
    model_path: str = ""
    results: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "session_hash": self.session_hash,
            "scenario": self.scenario,
            "cycles_completed": self.cycles_completed,
            "cycles_failed": self.cycles_failed,
            "cycles_total": self.cycles_total,
            "training_workflow_hash": self.training_workflow_hash,
            "model_path": self.model_path,
            "results": [
                {
                    "cycle": r.cycle,
                    "mode": r.mode,
                    "success": r.success,
                    "workflow_hash": r.workflow_hash,
                    "elapsed_seconds": round(r.elapsed_seconds, 1),
                    "error": r.error,
                    "sim_timestamp": r.sim_timestamp,
                }
                for r in self.results
            ],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class WorkflowAPIClient:
    """HTTP client for triggering and polling workflow executions.

    Uses the workflow engine's REST API to trigger pipeline runs and wait
    for completion. Retry with exponential backoff on transient failures.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        # Import requests lazily — allows offline mode without the dependency
        import requests
        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"

    def trigger_workflow(self, workflow_name: str, parameters: dict,
                         session_hash: str,
                         continue_from: str = None) -> str:
        """Trigger a workflow execution and return the workflow hash.

        Args:
            workflow_name: e.g. "mdk.fleet_training"
            parameters: workflow parameters dict
            session_hash: session identifier for continuation chain
            continue_from: previous workflow hash for continuation

        Returns:
            workflow_hash of the triggered execution

        Raises:
            RuntimeError on non-retryable failure after all retries
        """
        url = f"{self.base_url}/api/workflows/{workflow_name}/trigger"
        payload = {
            "parameters": parameters,
            "session_hash": session_hash,
        }
        if continue_from:
            payload["continue_from"] = continue_from

        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.post(url, json=payload, timeout=30)
                if resp.status_code in (200, 201, 202):
                    data = resp.json()
                    return data.get("workflow_hash", data.get("hash", ""))
                elif resp.status_code >= 500:
                    # Server error — retryable
                    logger.warning("Trigger attempt %d/%d: HTTP %d — %s",
                                   attempt + 1, MAX_RETRIES, resp.status_code,
                                   resp.text[:200])
                else:
                    # Client error — not retryable
                    raise RuntimeError(
                        f"Trigger failed: HTTP {resp.status_code} — {resp.text[:200]}")
            except (ConnectionError, OSError) as e:
                logger.warning("Trigger attempt %d/%d: connection error — %s",
                               attempt + 1, MAX_RETRIES, e)

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])

        raise RuntimeError(f"Trigger failed after {MAX_RETRIES} retries")

    def poll_completion(self, workflow_name: str, workflow_hash: str) -> dict:
        """Poll workflow status until completion or timeout.

        Returns:
            Status dict with at least {"status": "completed"|"failed", ...}

        Raises:
            TimeoutError if workflow doesn't complete within POLL_TIMEOUT
        """
        url = (f"{self.base_url}/api/workflows/{workflow_name}/status"
               f"?workflow_hash={workflow_hash}")
        start = time.monotonic()

        while time.monotonic() - start < POLL_TIMEOUT:
            try:
                resp = self._session.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "unknown")
                    if status in ("completed", "success", "failed", "error"):
                        return data
            except (ConnectionError, OSError) as e:
                logger.debug("Poll error (will retry): %s", e)

            time.sleep(POLL_INTERVAL)

        raise TimeoutError(
            f"Workflow {workflow_hash} did not complete within {POLL_TIMEOUT}s")

    def get_file_url(self, workflow_hash: str, file_name: str) -> Optional[str]:
        """Get the storage URI for a workflow output file by name.

        Searches the files list from GET /api/files/{hash} for a matching
        output file. Returns the file:// or azure:// URI.
        """
        url = f"{self.base_url}/api/files/{workflow_hash}"
        try:
            resp = self._session.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for f in data.get("files", []):
                    if f.get("file_name") == file_name and f.get("is_output"):
                        return f.get("uri", "")
        except (ConnectionError, OSError):
            pass
        return None


class SimulationLoop:
    """Orchestrates continuous simulation cycles.

    Manages the SimulationEngine for telemetry generation and the
    WorkflowAPIClient for triggering pipeline runs. Handles the training
    → inference transition and continuation chain.
    """

    def __init__(self, sim_engine: SimulationEngine, api_client=None,
                 cycles: int = 12, offline: bool = False,
                 output_dir: str = "/work"):
        self.sim = sim_engine
        self.api = api_client
        self.cycles = cycles
        self.offline = offline
        self.output_dir = output_dir

        # Session hash: deterministic from scenario + timestamp for reproducibility
        session_seed = f"sim_{sim_engine.scenario_name}_{datetime.now().isoformat()}"
        self.session_hash = hashlib.sha256(session_seed.encode()).hexdigest()[:16]

        self.metrics = SimulationMetrics(
            session_hash=self.session_hash,
            scenario=sim_engine.scenario_name,
            cycles_total=cycles,
            started_at=datetime.now().isoformat(),
        )

        # Circuit breaker state
        self._consecutive_failures = 0

    @staticmethod
    def _to_file_uri(path: str) -> str:
        """Prepend file:// to an absolute path if not already a URI."""
        if path.startswith('/') and '://' not in path:
            return f"file://{path}"
        return path

    def run(self) -> SimulationMetrics:
        """Execute the full simulation loop: 1 training + N inference cycles."""
        logger.info("Starting simulation loop: %d cycles, session=%s, scenario=%s",
                     self.cycles, self.session_hash, self.sim.scenario_name)

        prev_hash = None

        for cycle in range(self.cycles + 1):  # cycle 0 = training, 1..N = inference
            # Circuit breaker check
            if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                logger.warning("Circuit breaker: %d consecutive failures, "
                               "pausing %ds", self._consecutive_failures,
                               CIRCUIT_BREAKER_PAUSE)
                time.sleep(CIRCUIT_BREAKER_PAUSE)
                self._consecutive_failures = 0

            is_training = (cycle == 0)
            mode = "training" if is_training else "inference"
            interval = (TRAINING_INTERVAL_MINUTES if is_training
                        else INFERENCE_INTERVAL_MINUTES)

            logger.info("Cycle %d/%d (%s): advancing %d min from %s",
                         cycle, self.cycles, mode, interval,
                         self.sim.current_timestamp)

            result = self._run_cycle(cycle, mode, interval, prev_hash)
            self.metrics.results.append(result)

            if result.success:
                self.metrics.cycles_completed += 1
                self._consecutive_failures = 0
                prev_hash = result.workflow_hash or prev_hash

                if is_training and result.model_path:
                    self.metrics.training_workflow_hash = result.workflow_hash
                    self.metrics.model_path = result.model_path
            else:
                self.metrics.cycles_failed += 1
                self._consecutive_failures += 1
                logger.error("Cycle %d failed: %s", cycle, result.error)

            # Batch file cleanup to prevent disk accumulation
            self.sim.cleanup_old_batches(keep=50)

            # Write intermediate metrics after each cycle
            self._write_metrics()

        self.metrics.finished_at = datetime.now().isoformat()
        self._write_metrics()
        logger.info("Simulation complete: %d/%d cycles succeeded",
                     self.metrics.cycles_completed, self.cycles + 1)
        return self.metrics

    def _run_cycle(self, cycle: int, mode: str, interval_minutes: int,
                   prev_hash: Optional[str]) -> CycleResult:
        """Execute a single simulation cycle."""
        t0 = time.monotonic()
        result = CycleResult(cycle=cycle, mode=mode,
                             sim_timestamp=self.sim.current_timestamp,
                             success=False)

        try:
            # Generate telemetry batch
            batch_csv, batch_meta = self.sim.advance(interval_minutes)
            result.batch_csv = batch_csv
            result.batch_meta = batch_meta
            logger.info("  Batch generated: %s (%s)", batch_csv,
                         self.sim.current_timestamp)

            if self.offline:
                # Offline mode: skip API calls, just record the batch
                result.success = True
                result.elapsed_seconds = time.monotonic() - t0
                return result

            # Trigger the appropriate workflow chain
            if mode == "training":
                wf_hash = self._trigger_training(batch_csv, batch_meta)
            else:
                wf_hash = self._trigger_inference(batch_csv, batch_meta,
                                                   prev_hash)

            result.workflow_hash = wf_hash

            # Poll for completion of the final workflow in the chain
            final_wf = "mdk.train" if mode == "training" else "mdk.analyze"
            status = self.api.poll_completion(final_wf, wf_hash)

            if status.get("status") in ("completed", "success"):
                result.success = True
                # Extract model path from training run
                if mode == "training":
                    model_url = self.api.get_file_url(wf_hash, "anomaly_model.joblib")
                    result.model_path = model_url or ""
                    logger.info("  Model artifact: %s", result.model_path or "(not found)")
            else:
                result.error = f"Workflow {wf_hash} ended with status: {status.get('status')}"

        except Exception as e:
            result.error = str(e)
            logger.exception("Cycle %d error", cycle)

        result.elapsed_seconds = time.monotonic() - t0
        return result

    def _trigger_training(self, batch_csv: str, batch_meta: str) -> str:
        """Trigger training chain: pre_processing → train (cycle 0)."""
        params = {
            "telemetry_csv_path": self._to_file_uri(batch_csv),
            "metadata_json_path": self._to_file_uri(batch_meta),
        }
        # Step 1: pre_processing
        pp_hash = self.api.trigger_workflow(
            workflow_name="mdk.pre_processing",
            parameters=params,
            session_hash=self.session_hash,
        )
        status = self.api.poll_completion("mdk.pre_processing", pp_hash)
        if status.get("status") not in ("completed", "success"):
            raise RuntimeError(f"pre_processing failed: {status}")

        # Step 2: train (continue_from pre_processing)
        return self.api.trigger_workflow(
            workflow_name="mdk.train",
            parameters={},
            session_hash=self.session_hash,
            continue_from=pp_hash,
        )

    def _trigger_inference(self, batch_csv: str, batch_meta: str,
                           prev_hash: Optional[str]) -> str:
        """Trigger inference chain: pre_processing → score → analyze (cycles 1..N)."""
        # Step 1: pre_processing
        pp_params = {
            "telemetry_csv_path": self._to_file_uri(batch_csv),
            "metadata_json_path": self._to_file_uri(batch_meta),
        }
        pp_hash = self.api.trigger_workflow(
            workflow_name="mdk.pre_processing",
            parameters=pp_params,
            session_hash=self.session_hash,
            continue_from=prev_hash,
        )
        status = self.api.poll_completion("mdk.pre_processing", pp_hash)
        if status.get("status") not in ("completed", "success"):
            raise RuntimeError(f"pre_processing failed: {status}")

        # Step 2: score (continue_from pre_processing, model from training)
        score_params = {
            "model_path": self.metrics.model_path,
        }
        score_hash = self.api.trigger_workflow(
            workflow_name="mdk.score",
            parameters=score_params,
            session_hash=self.session_hash,
            continue_from=pp_hash,
        )
        status = self.api.poll_completion("mdk.score", score_hash)
        if status.get("status") not in ("completed", "success"):
            raise RuntimeError(f"score failed: {status}")

        # Step 3: analyze (continue_from score)
        analyze_params = {}
        if self.metrics.training_workflow_hash:
            metrics_url = self.api.get_file_url(
                self.metrics.training_workflow_hash, "model_metrics.json")
            if metrics_url:
                analyze_params["model_metrics_path"] = metrics_url

        return self.api.trigger_workflow(
            workflow_name="mdk.analyze",
            parameters=analyze_params,
            session_hash=self.session_hash,
            continue_from=score_hash,
        )

    def _write_metrics(self):
        """Write current metrics to JSON file."""
        metrics_path = os.path.join(self.output_dir, "simulation_metrics.json")
        with open(metrics_path, "w") as f:
            json.dump(self.metrics.to_dict(), f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Continuous simulation loop (Phase 2 orchestrator)")
    parser.add_argument("--scenario", type=str, default=None,
                        help="Path to scenario JSON file")
    parser.add_argument("--cycles", type=int, default=12,
                        help="Number of inference cycles after training (default: 12)")
    parser.add_argument("--api-url", type=str, default=None,
                        help="Workflow engine API URL "
                             "(default: WORKFLOW_API_URL env or http://localhost:8000)")
    parser.add_argument("--offline", action="store_true",
                        help="Offline mode: generate batches without API calls")
    parser.add_argument("--speed-factor", type=float, default=None,
                        help="Not used by loop (always batch mode). Reserved for compat.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed override")
    parser.add_argument("--output-dir", type=str, default="/work",
                        help="Output directory for metrics (default: /work)")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Resolve API URL: CLI arg > CTX_API_URL (engine param) > WORKFLOW_API_URL > default
    api_url = (args.api_url
               or os.environ.get("CTX_API_URL")
               or os.environ.get("WORKFLOW_API_URL")
               or "http://localhost:8000")

    # Create simulation engine — use absolute paths so file:// URIs resolve
    output_dir = os.path.abspath(args.output_dir)
    batch_dir = os.path.join(output_dir, "batches")
    sim_engine = SimulationEngine(
        scenario_path=args.scenario,
        output_dir=batch_dir,
        seed=args.seed,
    )

    logger.info("Simulation engine: %d devices, scenario=%s",
                 sim_engine.device_count, sim_engine.scenario_name)

    # Create API client (None if offline)
    api_client = None
    if not args.offline:
        api_client = WorkflowAPIClient(api_url)
        logger.info("API client: %s", api_url)
    else:
        logger.info("Offline mode: no API calls")

    # Run the loop
    loop = SimulationLoop(
        sim_engine=sim_engine,
        api_client=api_client,
        cycles=args.cycles,
        offline=args.offline,
        output_dir=output_dir,
    )

    metrics = loop.run()

    # Write final output vars for workflow engine consumption
    vars_path = os.path.join(args.output_dir, "_validance_vars.json")
    with open(vars_path, "w") as f:
        json.dump({
            "cycles_completed": metrics.cycles_completed,
            "cycles_failed": metrics.cycles_failed,
            "session_hash": metrics.session_hash,
        }, f)

    print(f"\nSimulation loop complete:")
    print(f"  Session:   {metrics.session_hash}")
    print(f"  Completed: {metrics.cycles_completed}/{metrics.cycles_total + 1} cycles")
    print(f"  Failed:    {metrics.cycles_failed}")
    print(f"  Metrics:   {args.output_dir}/simulation_metrics.json")

    # Exit with error code if too many failures
    if metrics.cycles_failed > metrics.cycles_completed:
        sys.exit(1)


if __name__ == "__main__":
    main()
