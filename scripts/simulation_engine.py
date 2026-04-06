#!/usr/bin/env python3
"""
Tick-by-Tick Mining Fleet Simulation Engine
============================================
Stateful simulator with speed control for real-time or accelerated operation.
Uses the shared physics engine for identical physics and telemetry schema.

Usage:
    # Real-time (1 tick per 5 minutes):
    python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --speed-factor 1

    # Accelerated (1 day per minute):
    python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --speed-factor 1440

    # Offline (max speed, no sleep):
    python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --offline

    # Output to specific file:
    python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --offline \
        --output data/generated/fleet_telemetry.csv

Output:
    CSV file with identical 35-column schema as training corpus generator.
    Also generates fleet_metadata.json alongside the CSV.

Author: Wiktor (MDK assignment, April 2026)
"""

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from typing import List, Tuple

# Import shared physics engine (same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from physics_engine import (
    TELEMETRY_COLUMNS, DEVICE_MODELS, DEFAULT_ECONOMIC,
    DeviceState, AnomalySchedule,
    load_scenario, create_fleet_from_scenario, create_anomaly_schedules_from_scenario,
    create_default_fleet, create_default_anomaly_schedule,
    simulate_tick, emit_telemetry_row, ambient_temperature, energy_price,
    SITE_ARCHETYPES,
)

DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "data", "generated", "fleet_telemetry.csv")


def run_simulation(scenario_path: str = None, output_path: str = None,
                    speed_factor: float = None, offline: bool = False,
                    seed: int = None) -> str:
    """Run the tick-by-tick simulation.

    Args:
        scenario_path: Path to scenario JSON (None for default fleet).
        output_path: Output CSV path.
        speed_factor: Time acceleration factor (1=real-time, 1440=1day/min).
        offline: If True, run at max speed with no sleep.
        seed: Random seed override.

    Returns:
        Path to output CSV file.
    """
    # Load scenario or use defaults
    if scenario_path:
        scenario = load_scenario(scenario_path)
        site = scenario["_resolved_site"]
        economic = scenario["_resolved_economic"]
        devices = create_fleet_from_scenario(scenario)
        anomaly_schedules = create_anomaly_schedules_from_scenario(scenario)
        events = scenario.get("events", [])
        duration_days = scenario.get("duration_days", 30)
        interval_minutes = scenario.get("interval_minutes", 5)
        effective_seed = seed if seed is not None else scenario.get("seed", 42)
        scenario_name = scenario.get("name", "custom")
    else:
        # Default: 10-device fleet with 3 anomaly types (baseline config)
        site = dict(SITE_ARCHETYPES["northern"])
        economic = dict(DEFAULT_ECONOMIC)
        devices = create_default_fleet()
        anomaly_schedules = create_default_anomaly_schedule()
        events = []
        duration_days = 30
        interval_minutes = 5
        effective_seed = seed if seed is not None else 42
        scenario_name = "default"

    random.seed(effective_seed)

    samples_per_day = 24 * 60 // interval_minutes
    total_samples = duration_days * samples_per_day
    dt_hours = interval_minutes / 60.0
    interval_seconds = interval_minutes * 60.0

    # Resolve output path
    if output_path is None:
        output_path = DEFAULT_OUTPUT
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    meta_path = os.path.join(output_dir, "fleet_metadata.json")
    start_time = datetime(2026, 4, 2, 0, 0, 0)

    print(f"Simulation: {scenario_name}")
    print(f"  Devices:  {len(devices)}")
    print(f"  Duration: {duration_days} days ({total_samples:,} ticks)")
    print(f"  Interval: {interval_minutes} min")
    if offline:
        print(f"  Mode:     OFFLINE (max speed)")
    elif speed_factor:
        print(f"  Mode:     {speed_factor}x speed "
              f"({interval_seconds/speed_factor:.2f}s between ticks)")
    else:
        print(f"  Mode:     real-time ({interval_minutes} min between ticks)")

    row_count = 0
    sim_start = time.monotonic()

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TELEMETRY_COLUMNS)
        writer.writeheader()

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
                writer.writerow(row)
                row_count += 1

            # Progress indicator
            if sample_idx % (samples_per_day * 5) == 0 and sample_idx > 0:
                elapsed = time.monotonic() - sim_start
                pct = 100 * sample_idx / total_samples
                print(f"  Day {int(day)}/{duration_days} ({pct:.0f}%) — "
                      f"{elapsed:.1f}s elapsed, {row_count:,} rows")

            # Speed control
            if not offline and speed_factor is not None and speed_factor > 0:
                sleep_time = interval_seconds / speed_factor
                if sleep_time > 0.001:  # Don't sleep for <1ms
                    time.sleep(sleep_time)
            elif not offline and speed_factor is None:
                # True real-time: sleep for the full interval
                time.sleep(interval_seconds)

    elapsed = time.monotonic() - sim_start

    # Write metadata (compatible with pipeline's fleet_metadata.json)
    metadata = {
        "generator": "MDK Simulation Engine v2.0",
        "generated_at": datetime.now().isoformat(),
        "seed": effective_seed,
        "parameters": {
            "num_devices": len(devices),
            "days": duration_days,
            "interval_minutes": interval_minutes,
            "samples_per_device": total_samples,
            "total_rows": row_count,
        },
        "site": {
            "latitude": site.get("latitude", 64.5),
            "energy_cost_base_kwh": site["energy_cost_base_kwh"],
            "energy_cost_peak_kwh": site["energy_cost_peak_kwh"],
            "cooling_type": "mixed",
            "location_description": f"Site archetype: {scenario_name}",
        },
        "fleet": [
            {
                "device_id": dev.device_id,
                "model": dev.model,
                "stock_clock_ghz": dev.stock_clock_ghz,
                "stock_voltage_v": dev.stock_voltage_v,
                "nominal_hashrate_th": dev.nominal_hashrate_th,
                "nominal_power_w": dev.nominal_power_w,
                "nominal_efficiency_jth": dev.efficiency_jth,
            }
            for dev in devices
        ],
        "anomalies_injected": [
            {
                "device_id": f"ASIC-{s.device_idx:03d}",
                "type": s.anomaly_type,
                "start_day": s.start_day,
                "ramp_days": s.ramp_days,
                "severity": s.severity,
            }
            for s in anomaly_schedules
        ],
    }

    # Compute data hash for provenance
    with open(output_path, "rb") as fh:
        metadata["data_hash_sha256"] = hashlib.sha256(fh.read()).hexdigest()

    # Add field descriptions (for pipeline compatibility)
    metadata["fields"] = {
        "timestamp": "ISO 8601 timestamp",
        "device_id": "Unique device identifier (ASIC-XXX)",
        "model": "Hardware model name",
        "clock_ghz": "ASIC core clock frequency in GHz",
        "voltage_v": "ASIC core voltage in volts",
        "hashrate_th": "Observed hashrate in TH/s",
        "power_w": "Total ASIC power consumption in watts",
        "temperature_c": "Chip junction temperature in °C",
        "cooling_power_w": "Cooling system power consumption in watts",
        "ambient_temp_c": "Ambient air temperature at site in °C",
        "energy_price_kwh": "Electricity spot price in $/kWh",
        "operating_mode": "Current mode: normal|overclock|underclock|idle",
        "efficiency_jth": "Instantaneous efficiency: power_w / hashrate_th (J/TH)",
        "label_thermal_deg": "Ground truth: thermal degradation active (0/1)",
        "label_psu_instability": "Ground truth: PSU voltage instability active (0/1)",
        "label_hashrate_decay": "Ground truth: chip degradation / hashrate decay (0/1)",
        "label_any_anomaly": "Ground truth: any anomaly active (0/1)",
        "fan_rpm": "Actual fan speed in RPM",
        "fan_rpm_target": "Target fan speed in RPM (from controller)",
        "dust_index": "Dust accumulation index (0=clean, 1=fully fouled)",
        "inlet_temp_c": "Inlet air temperature (ambient + recirculation)",
        "voltage_ripple_mv": "PSU output voltage ripple in millivolts",
        "error_code": "Firmware error code (categorical)",
        "reboot_count": "Cumulative reboot count",
        "chip_count_active": "Number of active ASIC chips",
        "hashboard_count_active": "Number of active hashboards",
        "label_fan_bearing_wear": "Ground truth: fan bearing wear (0/1)",
        "label_capacitor_aging": "Ground truth: capacitor aging (0/1)",
        "label_dust_fouling": "Ground truth: dust fouling (0/1)",
        "label_thermal_paste_deg": "Ground truth: thermal paste degradation (0/1)",
        "label_solder_joint_fatigue": "Ground truth: solder joint fatigue (0/1)",
        "label_coolant_loop_fouling": "Ground truth: coolant loop fouling (0/1)",
        "label_firmware_cliff": "Ground truth: firmware cliff (0/1)",
        "operational_state": "Device state: RUNNING|CURTAILED|MAINTENANCE|FAILED",
        "economic_margin_usd": "Hourly economic margin in USD",
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  Simulation Complete — {scenario_name}")
    print(f"{'=' * 60}")
    print(f"  Output:     {output_path}")
    print(f"  Metadata:   {meta_path}")
    print(f"  Rows:       {row_count:,}")
    print(f"  Devices:    {len(devices)}")
    print(f"  Duration:   {duration_days} days")
    print(f"  Wall time:  {elapsed:.1f}s")
    print(f"  Throughput: {row_count/elapsed:,.0f} rows/s")
    print(f"  SHA-256:    {metadata['data_hash_sha256'][:16]}...")
    print(f"{'=' * 60}")

    return output_path


class SimulationEngine:
    """Stateful tick-by-tick simulator with per-interval batching.

    Used by simulation_loop.py for continuous operation. Advances the simulation
    by caller-specified intervals, writing each batch to a separate CSV file.
    Reuses the same physics_engine primitives as run_simulation() — identical
    physics, just batched output.

    The existing run_simulation() function is unchanged (full-dataset mode).
    """

    def __init__(self, scenario_path: str = None, output_dir: str = None,
                 seed: int = None):
        """Initialize the simulation engine from a scenario.

        Args:
            scenario_path: Path to scenario JSON. None for default fleet.
            output_dir: Directory for batch CSV files. Default: data/generated/batches/
            seed: Random seed override.
        """
        if scenario_path:
            scenario = load_scenario(scenario_path)
            self._site = scenario["_resolved_site"]
            self._economic = scenario["_resolved_economic"]
            self._devices = create_fleet_from_scenario(scenario)
            self._anomaly_schedules = create_anomaly_schedules_from_scenario(scenario)
            self._events = scenario.get("events", [])
            self._interval_minutes = scenario.get("interval_minutes", 5)
            effective_seed = seed if seed is not None else scenario.get("seed", 42)
            self._scenario_name = scenario.get("name", "custom")
        else:
            self._site = dict(SITE_ARCHETYPES["northern"])
            self._economic = dict(DEFAULT_ECONOMIC)
            self._devices = create_default_fleet()
            self._anomaly_schedules = create_default_anomaly_schedule()
            self._events = []
            self._interval_minutes = 5
            effective_seed = seed if seed is not None else 42
            self._scenario_name = "default"

        random.seed(effective_seed)
        self._seed = effective_seed
        self._samples_per_day = 24 * 60 // self._interval_minutes
        self._dt_hours = self._interval_minutes / 60.0

        # Simulation clock: _tick_cursor counts total ticks elapsed
        self._tick_cursor = 0
        self._start_time = datetime(2026, 4, 2, 0, 0, 0)
        self._batch_index = 0

        # Output directory for batch files
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "data", "generated", "batches")
        self._output_dir = output_dir
        os.makedirs(self._output_dir, exist_ok=True)

    @property
    def current_timestamp(self) -> str:
        """Current simulated timestamp (ISO 8601)."""
        ts = self._start_time + timedelta(
            minutes=self._tick_cursor * self._interval_minutes)
        return ts.isoformat()

    @property
    def elapsed_days(self) -> float:
        """Simulated days elapsed since start."""
        return self._tick_cursor / self._samples_per_day

    @property
    def device_count(self) -> int:
        return len(self._devices)

    @property
    def scenario_name(self) -> str:
        return self._scenario_name

    def advance(self, interval_minutes: int = 60) -> Tuple[str, str]:
        """Advance simulation by interval_minutes, write batch CSV.

        Computes the number of ticks for the requested interval and runs
        physics_engine.simulate_tick() for each tick on every device.
        Writes results to a batch CSV file.

        Args:
            interval_minutes: Simulated minutes to advance. Must be a multiple
                of the scenario's tick interval (default 5 min). Clamped to
                at least 1 tick.

        Returns:
            (batch_csv_path, metadata_json_path) — paths to the output files.
        """
        ticks = max(1, interval_minutes // self._interval_minutes)
        batch_path = os.path.join(self._output_dir,
                                  f"batch_{self._batch_index:04d}.csv")
        meta_path = os.path.join(self._output_dir,
                                 f"batch_{self._batch_index:04d}_meta.json")

        row_count = 0
        batch_start_ts = None
        batch_end_ts = None

        with open(batch_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TELEMETRY_COLUMNS)
            writer.writeheader()

            for _ in range(ticks):
                sample_idx = self._tick_cursor
                ts = self._start_time + timedelta(
                    minutes=sample_idx * self._interval_minutes)
                day = sample_idx / self._samples_per_day
                hour = ((sample_idx % self._samples_per_day)
                        * self._interval_minutes / 60.0)

                if batch_start_ts is None:
                    batch_start_ts = ts
                batch_end_ts = ts

                t_amb = ambient_temperature(int(day), hour, self._site)
                e_price = energy_price(hour, int(day), self._site)

                for dev_idx, device in enumerate(self._devices):
                    simulate_tick(device, dev_idx, day, hour, self._dt_hours,
                                  self._site, self._economic,
                                  self._anomaly_schedules, self._events,
                                  len(self._devices))
                    row = emit_telemetry_row(device, ts, t_amb, e_price)
                    writer.writerow(row)
                    row_count += 1

                self._tick_cursor += 1

        # Write batch metadata (compatible with pipeline's fleet_metadata.json)
        metadata = {
            "generator": "MDK Simulation Engine v2.0 (batch mode)",
            "generated_at": datetime.now().isoformat(),
            "seed": self._seed,
            "batch_index": self._batch_index,
            "batch_start": batch_start_ts.isoformat() if batch_start_ts else None,
            "batch_end": batch_end_ts.isoformat() if batch_end_ts else None,
            "ticks_in_batch": ticks,
            "total_ticks_elapsed": self._tick_cursor,
            "elapsed_days": round(self.elapsed_days, 2),
            "parameters": {
                "num_devices": len(self._devices),
                "interval_minutes": self._interval_minutes,
                "total_rows": row_count,
            },
            "site": {
                "latitude": self._site.get("latitude", 64.5),
                "energy_cost_base_kwh": self._site["energy_cost_base_kwh"],
                "energy_cost_peak_kwh": self._site["energy_cost_peak_kwh"],
                "cooling_type": "mixed",
                "location_description": f"Site archetype: {self._scenario_name}",
            },
            "fleet": [
                {
                    "device_id": dev.device_id,
                    "model": dev.model,
                    "stock_clock_ghz": dev.stock_clock_ghz,
                    "stock_voltage_v": dev.stock_voltage_v,
                    "nominal_hashrate_th": dev.nominal_hashrate_th,
                    "nominal_power_w": dev.nominal_power_w,
                    "nominal_efficiency_jth": dev.efficiency_jth,
                }
                for dev in self._devices
            ],
            "anomalies_injected": [
                {
                    "device_id": f"ASIC-{s.device_idx:03d}",
                    "type": s.anomaly_type,
                    "start_day": s.start_day,
                    "ramp_days": s.ramp_days,
                    "severity": s.severity,
                }
                for s in self._anomaly_schedules
            ],
        }

        # Compute data hash for provenance
        with open(batch_path, "rb") as fh:
            metadata["data_hash_sha256"] = hashlib.sha256(fh.read()).hexdigest()

        # Pipeline-compatible field descriptions
        metadata["fields"] = {
            "timestamp": "ISO 8601 timestamp",
            "device_id": "Unique device identifier (ASIC-XXX)",
            "model": "Hardware model name",
        }

        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        self._batch_index += 1
        return batch_path, meta_path

    def cleanup_old_batches(self, keep: int = 50):
        """Remove oldest batch files, retaining at most `keep` batches.

        Prevents unbounded disk usage in long-running simulations.
        """
        import glob
        csvs = sorted(glob.glob(os.path.join(self._output_dir, "batch_*.csv")))
        metas = sorted(glob.glob(os.path.join(self._output_dir, "batch_*_meta.json")))
        for path in csvs[:-keep] if len(csvs) > keep else []:
            os.remove(path)
        for path in metas[:-keep] if len(metas) > keep else []:
            os.remove(path)


def main():
    parser = argparse.ArgumentParser(
        description="Tick-by-tick mining fleet simulation engine")
    parser.add_argument("--scenario", type=str,
                        help="Path to scenario JSON file (omit for default fleet)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV path (default: data/generated/fleet_telemetry.csv)")
    parser.add_argument("--speed-factor", type=float, default=None,
                        help="Time acceleration factor (1=real-time, 1440=1day/min)")
    parser.add_argument("--offline", action="store_true",
                        help="Run at max speed with no sleep (batch mode)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed override")
    args = parser.parse_args()

    if not args.offline and args.speed_factor is None:
        print("Warning: No --speed-factor or --offline specified. "
              "Running at real-time speed (very slow).")
        print("  Use --offline for batch generation or --speed-factor 1440 for 1day/min")
        confirm = input("  Continue? [y/N] ")
        if confirm.lower() != "y":
            sys.exit(0)

    run_simulation(
        scenario_path=args.scenario,
        output_path=args.output,
        speed_factor=args.speed_factor,
        offline=args.offline,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
