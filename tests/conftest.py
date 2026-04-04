"""
Shared fixtures for mining optimization integration tests.

Session-scoped pipeline run: generates a small dataset, runs the full 9-task
training pipeline once, and shares artifacts across all test classes.
"""

import json
import os
import shutil
import sys

import pytest

# Add project root to path so tasks/ and scripts/ are importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


MINI_SCENARIO = {
    "name": "integration_test_mini",
    "description": "Small fleet for integration testing: 5 devices, 14 days, 2 anomalies",
    "seed": 42,
    "duration_days": 14,
    "interval_minutes": 5,
    "fleet": [
        {"model": "S19XP", "count": 2},
        {"model": "S19jPro", "count": 1},
        {"model": "M66S", "count": 1},
        {"model": "S21-HYD", "count": 1},
    ],
    "site": {
        "archetype": "northern",
        "energy_cost_base_kwh": 0.035,
        "energy_cost_peak_kwh": 0.065,
    },
    "economic": {
        "btc_price_usd": 85000,
        "network_difficulty_t": 119.12,
        "block_reward_btc": 3.125,
        "pool_fee_pct": 1.5,
    },
    "anomalies": [
        {
            "type": "thermal_deg",
            "device_indices": [2],
            "start_day": 3,
            "ramp_days": 2,
            "severity": 0.7,
        },
        {
            "type": "psu_instability",
            "device_indices": [4],
            "start_day": 4,
            "ramp_days": 2,
            "severity": 0.6,
        },
    ],
    "events": [],
}


def _run_pipeline(work_dir: str):
    """Run the full 9-task training pipeline in work_dir."""
    orig_dir = os.getcwd()
    orig_argv = sys.argv

    try:
        os.chdir(work_dir)
        # Reset sys.argv so argparse in tasks doesn't pick up pytest args
        sys.argv = ["task"]

        from tasks.ingest import main as ingest_main
        from tasks.features import main as features_main
        from tasks.kpi import main as kpi_main
        from tasks.train_model import main as train_main
        from tasks.score import main as score_main
        from tasks.trend_analysis import main as trend_main
        from tasks.cost_projection import main as cost_main
        from tasks.optimize import main as optimize_main
        from tasks.report import main as report_main

        print("\n=== Pipeline: ingest ===")
        ingest_main()

        print("\n=== Pipeline: features ===")
        features_main()

        print("\n=== Pipeline: kpi ===")
        kpi_main()

        print("\n=== Pipeline: train_model ===")
        train_main()

        print("\n=== Pipeline: score ===")
        score_main()

        print("\n=== Pipeline: trend_analysis ===")
        trend_main()

        print("\n=== Pipeline: cost_projection ===")
        cost_main()

        print("\n=== Pipeline: optimize ===")
        optimize_main()

        print("\n=== Pipeline: report ===")
        report_main()

    finally:
        os.chdir(orig_dir)
        sys.argv = orig_argv


@pytest.fixture(scope="session")
def pipeline_dir(tmp_path_factory):
    """Generate mini dataset -> run full 9-task training pipeline -> return artifact dir."""
    work_dir = str(tmp_path_factory.mktemp("pipeline"))

    # Write mini scenario JSON
    scenario_path = os.path.join(work_dir, "mini_scenario.json")
    with open(scenario_path, "w") as f:
        json.dump(MINI_SCENARIO, f, indent=2)

    # Copy cost_model.json (required by cost_projection.py)
    cost_model_src = os.path.join(PROJECT_ROOT, "data", "cost_model.json")
    shutil.copy2(cost_model_src, os.path.join(work_dir, "cost_model.json"))

    # Generate dataset via simulation engine
    scripts_dir = os.path.join(PROJECT_ROOT, "scripts")
    sys.path.insert(0, scripts_dir)
    from simulation_engine import run_simulation

    csv_path = os.path.join(work_dir, "fleet_telemetry.csv")
    run_simulation(
        scenario_path=scenario_path,
        output_path=csv_path,
        offline=True,
        seed=42,
    )

    # Run the full pipeline
    _run_pipeline(work_dir)

    return work_dir


@pytest.fixture(scope="session")
def pipeline_artifacts(pipeline_dir):
    """Dict of loaded pipeline artifacts (DataFrames, JSON dicts, paths)."""
    import pandas as pd

    artifacts = {"dir": pipeline_dir}

    # Parquet files
    for name in ["telemetry", "features", "kpi_timeseries"]:
        path = os.path.join(pipeline_dir, f"{name}.parquet")
        if os.path.exists(path):
            artifacts[name] = pd.read_parquet(path)

    # JSON files
    for name in [
        "fleet_risk_scores", "fleet_actions", "fleet_metadata",
        "model_metrics", "model_registry", "trend_analysis",
        "cost_projections",
    ]:
        path = os.path.join(pipeline_dir, f"{name}.json")
        if os.path.exists(path):
            with open(path) as f:
                artifacts[name] = json.load(f)

    # Validance vars from each task
    vars_path = os.path.join(pipeline_dir, "_validance_vars.json")
    if os.path.exists(vars_path):
        with open(vars_path) as f:
            artifacts["last_vars"] = json.load(f)

    return artifacts
