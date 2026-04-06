#!/usr/bin/env python3
"""
Standalone Batch Generator — Single-Interval Telemetry Generation
==================================================================
Pure Python, no Validance dependency. Generates one batch of simulated
telemetry by advancing the SimulationEngine by a specified interval.

Used by:
  - tasks/generate_batch.py (Validance task wrapper)
  - Standalone execution (no Validance needed)

State continuity:
  On first invocation (no --state), initializes from the scenario JSON.
  On subsequent invocations, restores engine state from sim_state.json
  and advances from where it left off. This enables the host-side
  orchestrator to chain batch generations across separate task executions.

Usage:
    # First batch (24h for training):
    python scripts/generate_batch.py \\
        --scenario data/scenarios/asic_aging.json \\
        --interval 1440 \\
        --output-dir data/pipeline_run

    # Subsequent batches (1h each, with state continuity):
    python scripts/generate_batch.py \\
        --scenario data/scenarios/asic_aging.json \\
        --interval 60 \\
        --state data/pipeline_run/sim_state.json \\
        --output-dir data/pipeline_run

Outputs:
    batch_telemetry.csv   — telemetry in 35-column pipeline-compatible schema
    batch_metadata.json   — provenance, fleet specs, batch window, data hash
    sim_state.json        — engine state snapshot for next invocation
    _validance_vars.json  — tick_cursor, batch_index, sim_timestamp (for engine)

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import json
import os
import sys

# Import SimulationEngine from same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation_engine import SimulationEngine


def generate_batch(scenario_path: str, interval_minutes: int,
                   output_dir: str, state_path: str = None,
                   seed: int = None) -> dict:
    """Generate a single batch of telemetry.

    Args:
        scenario_path: Path to scenario JSON file.
        interval_minutes: Simulated minutes to advance (1440 = 24h, 60 = 1h).
        output_dir: Directory for output files.
        state_path: Path to sim_state.json from previous run (None for fresh start).
        seed: Random seed override (only used on fresh start).

    Returns:
        Dict with output paths and metadata:
        {
            "batch_csv": str,       # path to batch_telemetry.csv
            "batch_meta": str,      # path to batch_metadata.json
            "state_path": str,      # path to sim_state.json (updated)
            "tick_cursor": int,     # current tick position
            "batch_index": int,     # batch sequence number
            "sim_timestamp": str,   # current simulated time (ISO 8601)
        }
    """
    os.makedirs(output_dir, exist_ok=True)

    # Initialize or restore engine
    if state_path and os.path.exists(state_path):
        engine = SimulationEngine.from_state(state_path, scenario_path)
        print(f"Restored state: tick={engine._tick_cursor}, "
              f"batch={engine._batch_index}, "
              f"sim_time={engine.current_timestamp}")
    else:
        engine = SimulationEngine(
            scenario_path=scenario_path,
            output_dir=output_dir,
            seed=seed,
        )
        print(f"Fresh start: scenario={engine.scenario_name}, "
              f"devices={engine.device_count}")

    # Override output_dir (from_state restores the original, but the caller
    # may want outputs in a different location)
    engine._output_dir = output_dir

    # Advance simulation by the requested interval
    batch_csv, batch_meta = engine.advance(interval_minutes)

    # Rename outputs to stable names (pipeline expects batch_telemetry.csv,
    # not batch_0001.csv). Keep originals for the engine's internal tracking.
    stable_csv = os.path.join(output_dir, "batch_telemetry.csv")
    stable_meta = os.path.join(output_dir, "batch_metadata.json")

    # Copy to stable names (don't rename — engine tracks batch_NNNN files)
    import shutil
    shutil.copy2(batch_csv, stable_csv)
    shutil.copy2(batch_meta, stable_meta)

    # Save engine state for next invocation
    state_out = os.path.join(output_dir, "sim_state.json")
    engine.save_state(state_out)

    # Write _validance_vars.json for engine consumption
    # (tick_cursor, batch_index, sim_timestamp become output variables)
    vars_path = os.path.join(output_dir, "_validance_vars.json")
    with open(vars_path, "w") as f:
        json.dump({
            "tick_cursor": engine._tick_cursor,
            "batch_index": engine._batch_index,
            "sim_timestamp": engine.current_timestamp,
        }, f)

    result = {
        "batch_csv": stable_csv,
        "batch_meta": stable_meta,
        "state_path": state_out,
        "tick_cursor": engine._tick_cursor,
        "batch_index": engine._batch_index,
        "sim_timestamp": engine.current_timestamp,
    }

    print(f"Batch generated: {interval_minutes}min, "
          f"tick={result['tick_cursor']}, "
          f"batch={result['batch_index']}, "
          f"sim_time={result['sim_timestamp']}")
    print(f"  CSV:   {stable_csv}")
    print(f"  Meta:  {stable_meta}")
    print(f"  State: {state_out}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Generate a single batch of simulated fleet telemetry")
    parser.add_argument("--scenario", type=str, required=True,
                        help="Path to scenario JSON file")
    parser.add_argument("--interval", type=int, default=60,
                        help="Simulated minutes to advance (default: 60)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory (default: data/pipeline_run/)")
    parser.add_argument("--state", type=str, default=None,
                        help="Path to sim_state.json from previous run")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed override (fresh start only)")
    args = parser.parse_args()

    if args.output_dir is None:
        args.output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "data", "pipeline_run")

    generate_batch(
        scenario_path=args.scenario,
        interval_minutes=args.interval,
        output_dir=args.output_dir,
        state_path=args.state,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
