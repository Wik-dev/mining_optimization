#!/usr/bin/env python3
"""
Scenario-Driven Training Corpus Generator
==========================================
Generates training datasets from scenario JSON files using the shared physics engine.
Supports single-scenario or multi-scenario composition for rich training corpora.

Usage:
    python scripts/generate_training_corpus.py --scenario data/scenarios/baseline.json
    python scripts/generate_training_corpus.py --all --output data/training/ --seed 42

Output:
    training_telemetry.csv    — 35-column telemetry (backward-compatible superset)
    training_telemetry.parquet — Same data in Parquet format
    training_metadata.json    — Provenance, fleet specs, anomaly schedules
    training_labels.csv       — Label columns only (for quick label analysis)

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import csv
import hashlib
import json
import os
import random
import sys
from datetime import datetime, timedelta
from typing import List, Tuple

# Import shared physics engine (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from physics_engine import (
    TELEMETRY_COLUMNS, DEVICE_MODELS, SITE_ARCHETYPES, DEFAULT_ECONOMIC,
    DeviceState, AnomalySchedule,
    load_scenario, create_fleet_from_scenario, create_anomaly_schedules_from_scenario,
    simulate_tick, emit_telemetry_row, ambient_temperature, energy_price,
)

DEFAULT_SCENARIOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "data", "scenarios")
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "data", "training")

LABEL_COLUMNS = [
    "timestamp", "device_id",
    "label_thermal_deg", "label_psu_instability",
    "label_hashrate_decay", "label_any_anomaly",
    "label_fan_bearing_wear", "label_capacitor_aging",
    "label_dust_fouling", "label_thermal_paste_deg",
    "label_solder_joint_fatigue", "label_coolant_loop_fouling",
    "label_firmware_cliff",
]


def generate_scenario_data(scenario_path: str, seed: int = None,
                            device_id_prefix: str = "") -> Tuple[List[dict], dict]:
    """Generate telemetry rows for a single scenario.

    Args:
        scenario_path: Path to scenario JSON file.
        seed: Random seed override (uses scenario seed if None).
        device_id_prefix: Prefix for device IDs (for multi-scenario composition).

    Returns:
        (rows, scenario_info) — list of telemetry row dicts + scenario metadata.
    """
    scenario = load_scenario(scenario_path)
    site = scenario["_resolved_site"]
    economic = scenario["_resolved_economic"]

    # Seed: CLI override > scenario > default
    effective_seed = seed if seed is not None else scenario.get("seed", 42)
    random.seed(effective_seed)

    duration_days = scenario.get("duration_days", 30)
    interval_minutes = scenario.get("interval_minutes", 5)
    samples_per_day = 24 * 60 // interval_minutes
    total_samples = duration_days * samples_per_day
    dt_hours = interval_minutes / 60.0

    # Create fleet and anomaly schedules
    devices = create_fleet_from_scenario(scenario)
    anomaly_schedules = create_anomaly_schedules_from_scenario(scenario)
    events = scenario.get("events", [])

    # Apply device ID prefix for multi-scenario composition
    if device_id_prefix:
        for dev in devices:
            dev.device_id = f"{device_id_prefix}_{dev.device_id}"
        # Also update anomaly schedule device indices (they reference by index, not ID)

    start_time = datetime(2026, 4, 2, 0, 0, 0)
    rows = []

    for sample_idx in range(total_samples):
        ts = start_time + timedelta(minutes=sample_idx * interval_minutes)
        day = sample_idx / samples_per_day
        hour = (sample_idx % samples_per_day) * interval_minutes / 60.0

        t_amb = ambient_temperature(int(day), hour, site)
        e_price = energy_price(hour, int(day), site)

        for dev_idx, device in enumerate(devices):
            simulate_tick(device, dev_idx, day, hour, dt_hours,
                          site, economic, anomaly_schedules, events, len(devices))
            row = emit_telemetry_row(device, ts, t_amb, e_price)
            rows.append(row)

        # Progress
        if sample_idx % (samples_per_day * 10) == 0 and sample_idx > 0:
            pct = 100 * sample_idx / total_samples
            print(f"  [{scenario.get('name', 'scenario')}] Day {int(day)}/{duration_days} ({pct:.0f}%)")

    # Build scenario info for metadata
    scenario_info = {
        "name": scenario.get("name", os.path.basename(scenario_path)),
        "description": scenario.get("description", ""),
        "seed": effective_seed,
        "duration_days": duration_days,
        "interval_minutes": interval_minutes,
        "device_count": len(devices),
        "total_rows": len(rows),
        "fleet": [
            {
                "device_id": dev.device_id,
                "model": dev.model,
                "stock_clock_ghz": dev.stock_clock_ghz,
                "stock_voltage_v": dev.stock_voltage_v,
                "nominal_hashrate_th": dev.nominal_hashrate_th,
                "nominal_power_w": dev.nominal_power_w,
                "nominal_efficiency_jth": dev.efficiency_jth,
                "nominal_chip_count": dev.nominal_chip_count,
                "nominal_hashboard_count": dev.nominal_hashboard_count,
            }
            for dev in devices
        ],
        "anomalies": [
            {
                "device_idx": s.device_idx,
                "type": s.anomaly_type,
                "start_day": s.start_day,
                "ramp_days": s.ramp_days,
                "severity": s.severity,
            }
            for s in anomaly_schedules
        ],
    }

    return rows, scenario_info


def write_outputs(rows: List[dict], metadata: dict, output_dir: str) -> Tuple[str, str]:
    """Write telemetry CSV, Parquet, metadata JSON, and labels CSV."""
    os.makedirs(output_dir, exist_ok=True)

    csv_path = os.path.join(output_dir, "training_telemetry.csv")
    parquet_path = os.path.join(output_dir, "training_telemetry.parquet")
    meta_path = os.path.join(output_dir, "training_metadata.json")
    labels_path = os.path.join(output_dir, "training_labels.csv")

    # Write CSV
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TELEMETRY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # Write labels CSV (subset of columns for quick analysis)
    with open(labels_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in LABEL_COLUMNS})

    # Compute data hash for provenance
    with open(csv_path, "rb") as f:
        data_hash = hashlib.sha256(f.read()).hexdigest()
    metadata["data_hash_sha256"] = data_hash

    # Write metadata
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Write Parquet (optional — requires pandas)
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        df.to_parquet(parquet_path, index=False)
        print(f"  Parquet: {parquet_path}")
    except ImportError:
        print("  Warning: pandas not available — skipping Parquet output")

    return csv_path, meta_path


def print_summary(metadata: dict, csv_path: str) -> None:
    """Print generation summary statistics."""
    print("\n" + "=" * 65)
    print("  Training Corpus Generation — Complete")
    print("=" * 65)
    print(f"  Output:     {csv_path}")
    print(f"  Rows:       {metadata['parameters']['total_rows']:,}")
    print(f"  Scenarios:  {len(metadata['scenarios'])}")
    print(f"  SHA-256:    {metadata.get('data_hash_sha256', 'N/A')[:16]}...")
    print()

    for sc in metadata["scenarios"]:
        anomaly_count = len(sc.get("anomalies", []))
        print(f"  [{sc['name']}] {sc['device_count']} devices, "
              f"{sc['duration_days']}d, {sc['total_rows']:,} rows, "
              f"{anomaly_count} anomaly schedules")

    # Quick label stats from the rows
    total = metadata["parameters"]["total_rows"]
    if "label_stats" in metadata:
        print()
        print("  Label distribution:")
        for label, count in metadata["label_stats"].items():
            pct = 100 * count / total if total > 0 else 0
            print(f"    {label}: {count:,} ({pct:.1f}%)")

    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(
        description="Generate training corpus from scenario JSON files")
    parser.add_argument("--scenario", type=str,
                        help="Path to a single scenario JSON file")
    parser.add_argument("--all", action="store_true",
                        help="Generate from all scenarios in data/scenarios/")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_DIR,
                        help="Output directory (default: data/training/)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed override (overrides scenario seeds)")
    parser.add_argument("--scenarios-dir", type=str, default=DEFAULT_SCENARIOS_DIR,
                        help="Directory containing scenario JSON files")
    args = parser.parse_args()

    if not args.scenario and not args.all:
        parser.error("Specify --scenario <path> or --all")

    # Collect scenario paths
    if args.all:
        scenario_dir = args.scenarios_dir
        if not os.path.isdir(scenario_dir):
            print(f"Error: scenarios directory not found: {scenario_dir}")
            sys.exit(1)
        scenario_paths = sorted([
            os.path.join(scenario_dir, f)
            for f in os.listdir(scenario_dir)
            if f.endswith(".json")
        ])
        if not scenario_paths:
            print(f"Error: no .json files found in {scenario_dir}")
            sys.exit(1)
        print(f"Found {len(scenario_paths)} scenarios in {scenario_dir}")
    else:
        if not os.path.isfile(args.scenario):
            print(f"Error: scenario file not found: {args.scenario}")
            sys.exit(1)
        scenario_paths = [args.scenario]

    # Generate data from each scenario
    all_rows = []
    scenario_infos = []

    for path in scenario_paths:
        scenario_name = os.path.splitext(os.path.basename(path))[0]
        print(f"\nGenerating: {scenario_name}")

        # Use scenario name as device ID prefix for multi-scenario composition
        prefix = scenario_name if len(scenario_paths) > 1 else ""
        rows, info = generate_scenario_data(path, seed=args.seed,
                                             device_id_prefix=prefix)
        all_rows.extend(rows)
        scenario_infos.append(info)
        print(f"  → {len(rows):,} rows from {info['device_count']} devices")

    # Compute label statistics
    label_keys = [k for k in TELEMETRY_COLUMNS if k.startswith("label_")]
    label_stats = {}
    for key in label_keys:
        count = sum(1 for r in all_rows if r.get(key) == 1 or r.get(key) == "1")
        label_stats[key] = count

    # Build top-level fleet list from all scenarios (pipeline tasks expect
    # metadata["fleet"] with device specs for feature engineering).
    # Device IDs are prefixed by scenario name in multi-scenario mode,
    # so there are no collisions.
    combined_fleet = []
    for info in scenario_infos:
        combined_fleet.extend(info.get("fleet", []))

    # Build metadata
    metadata = {
        "generator": "MDK Training Corpus Generator v2.0",
        "generated_at": datetime.now().isoformat(),
        "parameters": {
            "total_rows": len(all_rows),
            "scenario_count": len(scenario_infos),
            "seed_override": args.seed,
            "columns": len(TELEMETRY_COLUMNS),
            "num_devices": len(combined_fleet),
        },
        "fleet": combined_fleet,
        "scenarios": scenario_infos,
        "label_stats": label_stats,
        "columns": TELEMETRY_COLUMNS,
    }

    # Write outputs
    csv_path, meta_path = write_outputs(all_rows, metadata, args.output)
    print_summary(metadata, csv_path)


if __name__ == "__main__":
    main()
