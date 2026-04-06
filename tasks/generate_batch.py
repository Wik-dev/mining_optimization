#!/usr/bin/env python3
"""
Generate Batch — Task Wrapper
==============================
Thin wrapper that reads inputs from CWD (Validance convention: files staged
by the engine into the task's working directory) and writes outputs to CWD.

No Validance imports — this is a pure computation task.

Inputs (staged by engine into CWD):
    scenario.json           — scenario definition (always present)
    sim_state.json          — engine state from previous cycle (optional, absent on cycle 0)

Environment:
    CTX_INTERVAL_MINUTES    — simulated minutes to advance (from trigger params)

Outputs (written to CWD):
    batch_telemetry.csv     — telemetry batch in 35-column pipeline schema
    batch_metadata.json     — provenance, fleet specs, batch window, data hash
    sim_state.json          — engine state for next cycle
    _validance_vars.json    — tick_cursor, batch_index, sim_timestamp

Author: Wiktor (MDK assignment, April 2026)
"""

import os
import sys

# Tasks run with CWD = /work/{task_name}. The scripts/ directory is at /app/scripts/
# inside the Docker image.
sys.path.insert(0, "/app/scripts")
from generate_batch import generate_batch


def main():
    cwd = os.getcwd()

    scenario_path = os.path.join(cwd, "scenario.json")
    if not os.path.exists(scenario_path):
        print(f"ERROR: scenario.json not found in {cwd}")
        sys.exit(1)

    # sim_state.json is optional — absent on first cycle (training batch)
    state_path = os.path.join(cwd, "sim_state.json")
    if not os.path.exists(state_path):
        state_path = None

    # Interval comes from trigger parameters via CTX_* env var (ADR-005 §2.3.1).
    # Default: 60 minutes (1 hour) for inference cycles.
    interval_minutes = int(os.environ.get("CTX_INTERVAL_MINUTES", "60"))

    generate_batch(
        scenario_path=scenario_path,
        interval_minutes=interval_minutes,
        output_dir=cwd,
        state_path=state_path,
    )


if __name__ == "__main__":
    main()
