"""
MDK Fleet Simulation Workflow
==============================
Orchestration workflow (Pattern 5a) that runs a continuous simulation loop
as an engine-managed persistent task.

The simulation_loop.py orchestrator inside the container:
  1. Generates telemetry batches via SimulationEngine
  2. Chains mdk.pre_processing → mdk.train (training, cycle 0)
  3. Chains mdk.pre_processing → mdk.score → mdk.analyze (cycles 1..N)
  4. Links runs via session_hash + continue_from

Parameters (trigger-time, exposed as CTX_* env vars):
  - scenario_path: URI to scenario JSON (file:// or azure://)
  - cycles: number of inference cycles after training
  - api_url: workflow engine API URL (for inner triggers)

Outputs:
  - simulation_metrics.json: accumulated cycle results
  - _validance_vars.json: cycles_completed, cycles_failed, session_hash

Author: Wiktor (MDK assignment, April 2026)
"""

from validance.sdk import Task, Workflow

TASK_IMAGE = "autoregistry.azurecr.io/mdk-fleet-intelligence:latest"


def create_workflow() -> Workflow:
    """Entry point for workflow discovery."""
    wf = Workflow("mdk.fleet_simulation")

    # Single persistent task: the simulation loop orchestrator.
    # Runs simulation_loop.py inside the container, which generates telemetry
    # batches and triggers inner workflow runs via the engine's REST API.
    #
    # Trigger parameters become CTX_* env vars (ADR-005 §2.3):
    #   api_url     → CTX_API_URL       (read by simulation_loop.py)
    #   cycles      → CTX_CYCLES        (shell-expanded in command)
    #   cost_model_path → CTX_COST_MODEL_PATH (read by simulation_loop.py)
    #   scenario_path → resolved as input URI
    orchestrator = Task(
        name="simulation_orchestrator",
        command=(
            "python /app/scripts/simulation_loop.py"
            " --scenario /work/scenario.json"
            ' --cycles "$CTX_CYCLES"'
            " --output-dir /work"
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
            "session_hash": "str",
        },
        persistent=True,
        timeout=7200,  # 2 hours max — long enough for 12+ cycles
    )

    wf.add_task(orchestrator)
    return wf
