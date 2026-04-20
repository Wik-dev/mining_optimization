"""
MDK Fleet Simulation Workflow — Pattern 5a Wrapper
====================================================
1-task ephemeral workflow that runs the growing-window simulation orchestrator
inside a container. The orchestrator triggers inner workflows (generate_batch,
pre_processing, score, analyze) via HTTP back to the engine.

This makes the simulation triggerable from the dashboard UI via
POST /api/workflows/mdk.fleet_simulation/trigger, instead of requiring
CLI access to run orchestrate_simulation.py directly.

Architecture:
  mdk.fleet_simulation (this, Pattern 5a outer shell)
    └→ orchestrate_simulation.py (inside container, Pattern 1 loop)
         └→ Phase 1: mdk.generate_batch (full scenario data, one-shot)
         └→ Phase 2: [mdk.pre_processing(cutoff) → mdk.score → mdk.analyze] × N cycles

  Each inference cycle uses a growing-window cutoff: the pre_processing step
  filters the full dataset to [t=0 → t=cutoff], so rolling feature windows
  (6h, 24h, 7d) are properly populated — matching real-world monitoring
  where a database accumulates telemetry over time.

Trigger parameters (→ CTX_* env vars via ADR-005 §2.3):
  scenario_path    → CTX_SCENARIO_PATH  (resolved as input URI → ./scenario.json)
  training_hash    → CTX_TRAINING_HASH  (workflow hash of the training run — model
                     artifacts resolved via deep context chain, not explicit paths)
  api_url          → CTX_API_URL        (engine REST API for inner triggers)
  interval_days    → CTX_INTERVAL_DAYS  (simulated days per cycle, default: 1)
  session_hash     → CTX_SESSION_HASH   (propagated to inner workflows so all runs
                     share one session — enables GET /api/executions?session=)
  gateway_url      → CTX_GATEWAY_URL   (optional: OpenClaw gateway for AI agent push,
                     e.g. http://172.18.0.1:18789. When set, the orchestrator POSTs
                     to /hooks/agent after cycles with flagged devices.)
  gateway_token    → CTX_GATEWAY_TOKEN (optional: hooks auth token for the gateway)

Author: Wiktor (MDK assignment, April 2026)
"""

from validance import Task, Workflow

TASK_IMAGE = "autoregistry.azurecr.io/mdk-fleet-intelligence:latest"


def create_workflow() -> Workflow:
    """Entry point for workflow discovery."""
    wf = Workflow("mdk.fleet_simulation")

    # Single ephemeral task: runs orchestrate_simulation.py which in turn
    # triggers inner workflows via HTTP back to the engine.
    #
    # Ephemeral, not persistent — simulation has a clear end (all scenario days).
    # Pattern 5a: this task orchestrates, inner workflows do the data work.
    #
    # Timeout: 4h — long scenarios (180 days × inference per day) can take
    # significant wall-clock time. Each inner cycle is ~2 min, so 180 cycles
    # ≈ 6h worst case. 4h covers most scenarios with some margin.
    orchestrator = Task(
        name="simulation_orchestrator",
        command=(
            "python /app/scripts/orchestrate_simulation.py"
            " --scenario ./scenario.json"
            ' --training-hash "$CTX_TRAINING_HASH"'
            ' --api-url "$CTX_API_URL"'
            ' --interval-days "$CTX_INTERVAL_DAYS"'
            ' --gateway-url "$CTX_GATEWAY_URL"'
            ' --gateway-token "$CTX_GATEWAY_TOKEN"'
        ),
        docker_image=TASK_IMAGE,
        inputs={
            "scenario.json": "${scenario_path}",
        },
        output_files={
            "metrics": "simulation_metrics.json",
        },
        output_vars={
            "cycles_completed": "int",
            "cycles_failed": "int",
            "total_cycles": "int",
            "session_hash": "str",
        },
        timeout=14400,  # 4h
    )

    wf.add_task(orchestrator)
    return wf
