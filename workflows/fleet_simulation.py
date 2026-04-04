"""
MDK Fleet Simulation Workflow
==============================
Orchestration workflow (Pattern 5a) that runs a continuous simulation loop
as an engine-managed persistent task.

The simulation_loop.py orchestrator inside the container:
  1. Generates telemetry batches via SimulationEngine
  2. Triggers mdk.fleet_intelligence (training, cycle 0)
  3. Triggers mdk.fleet_intelligence.inference (cycles 1..N)
  4. Chains runs via session_hash + continue_from

Parameters:
  - scenario_path: path to scenario JSON
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
    # The container accesses the API via Docker network (WORKFLOW_API_URL env var).
    orchestrator = Task(
        name="simulation_orchestrator",
        command=(
            "python /app/scripts/simulation_loop.py"
            " --scenario /work/scenario.json"
            " --cycles ${cycles}"
            " --output-dir /work"
        ),
        docker_image=TASK_IMAGE,
        inputs={
            "scenario.json": "${scenario_path}",
        },
        environment={
            "WORKFLOW_API_URL": "${api_url}",
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
