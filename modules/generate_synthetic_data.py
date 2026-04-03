#!/usr/bin/env python3
"""
Synthetic Mining Fleet Telemetry Generator
==========================================
Generates realistic ASIC miner telemetry for the MDK AI-Driven Mining
Optimization & Predictive Maintenance project.

Physics model:
    - Dynamic power: P_dynamic ∝ C × V² × f  (CMOS switching power)
    - Static power:  P_static ∝ V × I_leak(T)  (leakage grows with temperature)
    - Hashrate:      H ∝ f  (SHA-256 throughput scales linearly with clock)
    - Temperature:   T = T_ambient + (P_total / cooling_capacity) + noise
    - Cooling power: P_cool = base_cool + k × max(0, T_chip - T_setpoint)

Anomaly patterns injected:
    A1  Gradual thermal degradation  — fan/heatsink fouling over days
    A2  Power supply instability     — voltage ripple spikes
    A3  Hashrate decay               — chip aging / partial ASIC failure

Author: Wiktor (MDK assignment, April 2026)
"""

import csv
import json
import random
import math
import hashlib
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

# ─── Configuration ───────────────────────────────────────────────────────────

SEED = 42
NUM_DEVICES = 10
DAYS = 30
INTERVAL_MINUTES = 5          # telemetry sample interval
SAMPLES_PER_DAY = 24 * 60 // INTERVAL_MINUTES   # 288

# Fleet composition — mix of hardware generations (from Gio's slides)
# Efficiency in W/TH at stock settings:
#   Modern top-tier:  ~15 W/TH (e.g. S21, M66)
#   Mid-gen:          ~21 W/TH (e.g. S19 XP)
#   Older:            ~28 W/TH (e.g. S19j Pro)

DEVICE_PROFILES = [
    # name_prefix, stock_clock_ghz, stock_voltage_v, nominal_hashrate_th,
    # nominal_power_w, efficiency_jth, cooling_base_w
    ("S21-HYD",   1.60, 0.30, 335.0, 5025.0, 15.0, 500.0),   # hydro-cooled flagship
    ("S21-HYD",   1.60, 0.30, 335.0, 5025.0, 15.0, 500.0),
    ("M66S",      1.50, 0.32, 298.0, 5370.0, 18.0, 550.0),
    ("M66S",      1.50, 0.32, 298.0, 5370.0, 18.0, 550.0),
    ("S19XP",     1.35, 0.35, 141.0, 3010.0, 21.3, 420.0),
    ("S19XP",     1.35, 0.35, 141.0, 3010.0, 21.3, 420.0),
    ("S19XP",     1.35, 0.35, 141.0, 3010.0, 21.3, 420.0),
    ("S19jPro",   1.20, 0.38, 104.0, 3068.0, 29.5, 400.0),
    ("S19jPro",   1.20, 0.38, 104.0, 3068.0, 29.5, 400.0),
    ("S19jPro",   1.20, 0.38, 104.0, 3068.0, 29.5, 400.0),
]

# Site-level parameters
SITE_LATITUDE = 64.5       # Northern site (e.g. Iceland/Nordics — hydro power)
ENERGY_COST_BASE = 0.035   # $/kWh — competitive rate per Gio's cost curves
ENERGY_COST_PEAK = 0.065   # $/kWh — peak hours surcharge

# Operating modes
MODE_NORMAL = "normal"
MODE_OVERCLOCK = "overclock"
MODE_UNDERCLOCK = "underclock"
MODE_IDLE = "idle"


# ─── Physics Engine ──────────────────────────────────────────────────────────

@dataclass
class DeviceState:
    """Mutable state for a single ASIC device."""
    device_id: str
    model: str
    stock_clock_ghz: float
    stock_voltage_v: float
    nominal_hashrate_th: float
    nominal_power_w: float
    efficiency_jth: float
    cooling_base_w: float

    # Dynamic state
    clock_ghz: float = 0.0
    voltage_v: float = 0.0
    hashrate_th: float = 0.0
    power_w: float = 0.0
    temperature_c: float = 40.0
    cooling_power_w: float = 0.0
    mode: str = MODE_NORMAL

    # Anomaly flags (for ground truth labels)
    anomaly_thermal_deg: bool = False
    anomaly_psu_instability: bool = False
    anomaly_hashrate_decay: bool = False

    # Internal degradation state
    _thermal_fouling: float = 0.0      # 0 = clean, 1 = fully fouled
    _chip_degradation: float = 0.0     # 0 = new, 1 = dead chips
    _psu_ripple: float = 0.0           # voltage noise amplitude

    def __post_init__(self):
        self.clock_ghz = self.stock_clock_ghz
        self.voltage_v = self.stock_voltage_v


def ambient_temperature(day: int, hour: float, latitude: float) -> float:
    """Sinusoidal ambient temperature model with seasonal and diurnal variation.
    
    Northern site: cooler overall, seasonal swing ~15°C, diurnal swing ~8°C.
    """
    # Seasonal component (day 0 = April 2, roughly early spring in northern hemisphere)
    seasonal_offset = -5.0   # still cold in early April at 64°N
    seasonal_swing = 7.5
    day_of_year_approx = 92 + day  # April 2 ≈ day 92
    seasonal = seasonal_offset + seasonal_swing * math.sin(
        2 * math.pi * (day_of_year_approx - 80) / 365  # peak around day 172 (summer solstice)
    )

    # Diurnal component
    diurnal_swing = 4.0
    diurnal = diurnal_swing * math.sin(2 * math.pi * (hour - 6) / 24)  # peak at 14:00

    return seasonal + diurnal + random.gauss(0, 0.5)


def energy_price(hour: float, day: int) -> float:
    """Time-of-use electricity pricing model.
    
    Peak: 08:00-20:00 weekdays
    Off-peak: nights and weekends
    Add small random variation for market noise.
    """
    weekday = day % 7  # 0=Monday (April 2, 2026 is Thursday, so offset)
    is_weekend = weekday >= 5
    is_peak = not is_weekend and 8 <= hour < 20

    base = ENERGY_COST_PEAK if is_peak else ENERGY_COST_BASE
    noise = random.gauss(0, 0.002)
    return max(0.02, base + noise)


def compute_operating_mode(device: DeviceState, e_price: float, t_ambient: float) -> str:
    """Simple rule-based operating mode selection.
    
    - Overclock when energy is cheap and ambient is cool
    - Underclock when energy is expensive
    - Idle during extreme peak pricing
    - Normal otherwise
    """
    if e_price > 0.06:
        return MODE_UNDERCLOCK
    elif e_price > 0.07:
        return MODE_IDLE
    elif e_price < 0.04 and t_ambient < 5.0:
        return MODE_OVERCLOCK
    return MODE_NORMAL


MODE_CLOCK_MULTIPLIER = {
    MODE_NORMAL: 1.0,
    MODE_OVERCLOCK: 1.15,
    MODE_UNDERCLOCK: 0.80,
    MODE_IDLE: 0.0,
}

MODE_VOLTAGE_OFFSET = {
    MODE_NORMAL: 0.0,
    MODE_OVERCLOCK: 0.03,     # +30mV for stability at higher clock
    MODE_UNDERCLOCK: -0.02,   # -20mV undervolting
    MODE_IDLE: 0.0,
}


def step_physics(device: DeviceState, t_ambient: float, dt_hours: float) -> None:
    """Advance device state by one timestep using CMOS power model."""

    mode = device.mode
    if mode == MODE_IDLE:
        device.clock_ghz = 0.0
        device.voltage_v = device.stock_voltage_v
        device.hashrate_th = 0.0
        device.power_w = 50.0   # standby power
        device.cooling_power_w = device.cooling_base_w * 0.1
        # Temperature decays toward ambient
        device.temperature_c += (t_ambient + 5.0 - device.temperature_c) * 0.3
        return

    # --- Clock and voltage ---
    clock_mult = MODE_CLOCK_MULTIPLIER[mode]
    v_offset = MODE_VOLTAGE_OFFSET[mode]
    device.clock_ghz = device.stock_clock_ghz * clock_mult
    device.voltage_v = device.stock_voltage_v + v_offset

    # PSU instability anomaly: add voltage ripple
    if device._psu_ripple > 0:
        ripple = random.gauss(0, device._psu_ripple)
        device.voltage_v += ripple
        device.voltage_v = max(0.20, device.voltage_v)

    # --- Power model: P = k × V² × f + P_static(T) ---
    # Calibrate k so that at stock settings we get nominal power
    k = device.nominal_power_w / (device.stock_voltage_v ** 2 * device.stock_clock_ghz)
    p_dynamic = k * device.voltage_v ** 2 * device.clock_ghz

    # Static/leakage power increases with temperature (exponential model)
    p_static_base = device.nominal_power_w * 0.05  # ~5% of total at 40°C
    temp_factor = math.exp(0.02 * (device.temperature_c - 40.0))
    p_static = p_static_base * temp_factor

    device.power_w = p_dynamic + p_static
    # Add measurement noise
    device.power_w += random.gauss(0, device.nominal_power_w * 0.005)
    device.power_w = max(0, device.power_w)

    # --- Hashrate: H ∝ f, minus chip degradation ---
    hash_per_ghz = device.nominal_hashrate_th / device.stock_clock_ghz
    device.hashrate_th = hash_per_ghz * device.clock_ghz * (1.0 - device._chip_degradation)
    # Add noise (~0.5% measurement jitter)
    device.hashrate_th += random.gauss(0, device.nominal_hashrate_th * 0.005)
    device.hashrate_th = max(0, device.hashrate_th)

    # --- Temperature model ---
    # Thermal resistance increases with fouling
    thermal_resistance_clean = 0.008   # °C/W baseline
    thermal_resistance = thermal_resistance_clean * (1.0 + 2.0 * device._thermal_fouling)

    t_target = t_ambient + device.power_w * thermal_resistance
    # Thermal inertia (exponential decay toward target)
    tau = 0.4  # time constant — chip heats/cools within a few intervals
    device.temperature_c += (t_target - device.temperature_c) * (1.0 - math.exp(-dt_hours / tau))
    device.temperature_c += random.gauss(0, 0.3)
    device.temperature_c = max(t_ambient, device.temperature_c)

    # --- Cooling power ---
    t_setpoint = 65.0  # cooling controller setpoint
    cooling_proportional = max(0, device.temperature_c - t_setpoint) * 15.0
    device.cooling_power_w = device.cooling_base_w + cooling_proportional
    # Fouling makes cooling work harder
    device.cooling_power_w *= (1.0 + 0.5 * device._thermal_fouling)
    device.cooling_power_w += random.gauss(0, 10.0)
    device.cooling_power_w = max(0, device.cooling_power_w)


# ─── Anomaly Injection ───────────────────────────────────────────────────────

@dataclass
class AnomalySchedule:
    """Defines when an anomaly starts and how it progresses."""
    device_idx: int
    anomaly_type: str        # "thermal_deg" | "psu_instability" | "hashrate_decay"
    start_day: int
    ramp_days: float         # days to reach full severity
    severity: float          # 0-1 scale

def create_anomaly_schedule(rng: random.Random) -> List[AnomalySchedule]:
    """Create a realistic mix of anomalies across the fleet."""
    schedules = []

    # A1: Thermal degradation — 2 devices, starts mid-period, gradual
    schedules.append(AnomalySchedule(
        device_idx=7,  # S19jPro — older hardware, more plausible
        anomaly_type="thermal_deg",
        start_day=8,
        ramp_days=15.0,
        severity=0.7,
    ))
    schedules.append(AnomalySchedule(
        device_idx=4,  # S19XP
        anomaly_type="thermal_deg",
        start_day=18,
        ramp_days=10.0,
        severity=0.4,
    ))

    # A2: PSU instability — 1 device, sudden onset
    schedules.append(AnomalySchedule(
        device_idx=3,  # M66S
        anomaly_type="psu_instability",
        start_day=14,
        ramp_days=2.0,
        severity=0.8,
    ))

    # A3: Hashrate decay — 2 devices, gradual chip failure
    schedules.append(AnomalySchedule(
        device_idx=9,  # S19jPro — oldest gen
        anomaly_type="hashrate_decay",
        start_day=5,
        ramp_days=20.0,
        severity=0.25,   # loses 25% of chips
    ))
    schedules.append(AnomalySchedule(
        device_idx=2,  # M66S
        anomaly_type="hashrate_decay",
        start_day=22,
        ramp_days=5.0,
        severity=0.15,
    ))

    return schedules


def apply_anomalies(device: DeviceState, device_idx: int,
                    day: float, schedules: List[AnomalySchedule]) -> None:
    """Update device degradation state based on anomaly schedules."""
    device.anomaly_thermal_deg = False
    device.anomaly_psu_instability = False
    device.anomaly_hashrate_decay = False

    for sched in schedules:
        if sched.device_idx != device_idx:
            continue
        if day < sched.start_day:
            continue

        progress = min(1.0, (day - sched.start_day) / sched.ramp_days)
        severity = sched.severity * progress

        if sched.anomaly_type == "thermal_deg":
            device._thermal_fouling = severity
            device.anomaly_thermal_deg = severity > 0.05
        elif sched.anomaly_type == "psu_instability":
            device._psu_ripple = severity * 0.05  # up to 50mV ripple at full severity
            device.anomaly_psu_instability = severity > 0.05
        elif sched.anomaly_type == "hashrate_decay":
            device._chip_degradation = severity
            device.anomaly_hashrate_decay = severity > 0.02


# ─── Data Generation ─────────────────────────────────────────────────────────

def generate_fleet_telemetry(output_dir: str = "data") -> Tuple[str, str]:
    """Generate synthetic telemetry CSV and metadata JSON.
    
    Returns:
        (csv_path, metadata_path)
    """
    random.seed(SEED)
    rng = random.Random(SEED)

    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "fleet_telemetry.csv")
    meta_path = os.path.join(output_dir, "fleet_metadata.json")

    # Initialize devices
    devices: List[DeviceState] = []
    for i, profile in enumerate(DEVICE_PROFILES):
        name_prefix, clock, voltage, hashrate, power, eff, cool = profile
        device = DeviceState(
            device_id=f"ASIC-{i:03d}",
            model=name_prefix,
            stock_clock_ghz=clock,
            stock_voltage_v=voltage,
            nominal_hashrate_th=hashrate,
            nominal_power_w=power,
            efficiency_jth=eff,
            cooling_base_w=cool,
        )
        devices.append(device)

    anomaly_schedules = create_anomaly_schedule(rng)

    # CSV header
    fieldnames = [
        "timestamp", "device_id", "model",
        "clock_ghz", "voltage_v", "hashrate_th",
        "power_w", "temperature_c", "cooling_power_w",
        "ambient_temp_c", "energy_price_kwh",
        "operating_mode", "efficiency_jth",
        # Ground truth labels (for model training)
        "label_thermal_deg", "label_psu_instability", "label_hashrate_decay",
        "label_any_anomaly",
    ]

    start_time = datetime(2026, 4, 2, 0, 0, 0)
    dt_hours = INTERVAL_MINUTES / 60.0
    total_samples = DAYS * SAMPLES_PER_DAY
    row_count = 0

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for sample_idx in range(total_samples):
            ts = start_time + timedelta(minutes=sample_idx * INTERVAL_MINUTES)
            day = sample_idx / SAMPLES_PER_DAY
            hour = (sample_idx % SAMPLES_PER_DAY) * INTERVAL_MINUTES / 60.0

            t_amb = ambient_temperature(int(day), hour, SITE_LATITUDE)
            e_price = energy_price(hour, int(day))

            for dev_idx, device in enumerate(devices):
                # Apply anomaly progression
                apply_anomalies(device, dev_idx, day, anomaly_schedules)

                # Determine operating mode
                device.mode = compute_operating_mode(device, e_price, t_amb)

                # Step physics
                step_physics(device, t_amb, dt_hours)

                # Compute instantaneous efficiency
                if device.hashrate_th > 0:
                    eff = device.power_w / device.hashrate_th
                else:
                    eff = 0.0

                any_anomaly = (device.anomaly_thermal_deg or
                               device.anomaly_psu_instability or
                               device.anomaly_hashrate_decay)

                writer.writerow({
                    "timestamp": ts.isoformat(),
                    "device_id": device.device_id,
                    "model": device.model,
                    "clock_ghz": round(device.clock_ghz, 4),
                    "voltage_v": round(device.voltage_v, 4),
                    "hashrate_th": round(device.hashrate_th, 2),
                    "power_w": round(device.power_w, 1),
                    "temperature_c": round(device.temperature_c, 2),
                    "cooling_power_w": round(device.cooling_power_w, 1),
                    "ambient_temp_c": round(t_amb, 2),
                    "energy_price_kwh": round(e_price, 4),
                    "operating_mode": device.mode,
                    "efficiency_jth": round(eff, 2),
                    "label_thermal_deg": int(device.anomaly_thermal_deg),
                    "label_psu_instability": int(device.anomaly_psu_instability),
                    "label_hashrate_decay": int(device.anomaly_hashrate_decay),
                    "label_any_anomaly": int(any_anomaly),
                })
                row_count += 1

            # Progress indicator
            if sample_idx % (SAMPLES_PER_DAY * 5) == 0 and sample_idx > 0:
                print(f"  Generated day {int(day)}/{DAYS}...")

    # ─── Metadata ────────────────────────────────────────────────────────────

    # Compute data hash for provenance
    with open(csv_path, "rb") as f:
        data_hash = hashlib.sha256(f.read()).hexdigest()

    metadata = {
        "generator": "MDK Synthetic Fleet Telemetry v1.0",
        "generated_at": datetime.now().isoformat(),
        "seed": SEED,
        "parameters": {
            "num_devices": NUM_DEVICES,
            "days": DAYS,
            "interval_minutes": INTERVAL_MINUTES,
            "samples_per_device": total_samples,
            "total_rows": row_count,
        },
        "site": {
            "latitude": SITE_LATITUDE,
            "energy_cost_base_kwh": ENERGY_COST_BASE,
            "energy_cost_peak_kwh": ENERGY_COST_PEAK,
            "cooling_type": "hydro + air",
            "location_description": "Northern site (hydro power, cool climate)",
        },
        "fleet": [
            {
                "device_id": f"ASIC-{i:03d}",
                "model": p[0],
                "stock_clock_ghz": p[1],
                "stock_voltage_v": p[2],
                "nominal_hashrate_th": p[3],
                "nominal_power_w": p[4],
                "nominal_efficiency_jth": p[5],
            }
            for i, p in enumerate(DEVICE_PROFILES)
        ],
        "anomalies_injected": [
            {
                "device_id": f"ASIC-{s.device_idx:03d}",
                "type": s.anomaly_type,
                "start_day": s.start_day,
                "ramp_days": s.ramp_days,
                "severity": s.severity,
            }
            for s in create_anomaly_schedule(rng)
        ],
        "data_hash_sha256": data_hash,
        "fields": {
            "timestamp": "ISO 8601 timestamp (5-min intervals)",
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
            "label_hashrate_decay": "Ground truth: chip degradation / hashrate decay active (0/1)",
            "label_any_anomaly": "Ground truth: any anomaly active (0/1)",
        },
    }

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return csv_path, meta_path


# ─── Summary Statistics ──────────────────────────────────────────────────────

def print_summary(csv_path: str, meta_path: str) -> None:
    """Print dataset summary statistics."""
    with open(meta_path) as f:
        meta = json.load(f)

    print("\n" + "=" * 60)
    print("  MDK Synthetic Fleet Telemetry — Generation Complete")
    print("=" * 60)
    print(f"  Output:     {csv_path}")
    print(f"  Metadata:   {meta_path}")
    print(f"  Rows:       {meta['parameters']['total_rows']:,}")
    print(f"  Devices:    {meta['parameters']['num_devices']}")
    print(f"  Duration:   {meta['parameters']['days']} days")
    print(f"  Interval:   {meta['parameters']['interval_minutes']} min")
    print(f"  SHA-256:    {meta['data_hash_sha256'][:16]}...")
    print()

    print("  Fleet composition:")
    for d in meta["fleet"]:
        print(f"    {d['device_id']}  {d['model']:10s}  "
              f"{d['nominal_hashrate_th']:6.0f} TH/s  "
              f"{d['nominal_efficiency_jth']:5.1f} J/TH")

    print()
    print("  Injected anomalies:")
    for a in meta["anomalies_injected"]:
        print(f"    {a['device_id']}  {a['type']:20s}  "
              f"day {a['start_day']:2d}  ramp {a['ramp_days']:.0f}d  "
              f"severity {a['severity']:.0%}")

    # Quick stats from CSV
    import statistics
    temps, powers, hashrates, anomaly_count = [], [], [], 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if float(row["hashrate_th"]) > 0:
                temps.append(float(row["temperature_c"]))
                powers.append(float(row["power_w"]))
                hashrates.append(float(row["hashrate_th"]))
            if row["label_any_anomaly"] == "1":
                anomaly_count += 1

    total_rows = meta["parameters"]["total_rows"]
    print()
    print("  Quick statistics (non-idle samples):")
    print(f"    Temperature:  {statistics.mean(temps):.1f}°C mean, "
          f"{min(temps):.1f}–{max(temps):.1f}°C range")
    print(f"    Power:        {statistics.mean(powers):.0f}W mean, "
          f"{min(powers):.0f}–{max(powers):.0f}W range")
    print(f"    Hashrate:     {statistics.mean(hashrates):.1f} TH/s mean")
    print(f"    Anomaly rows: {anomaly_count:,} / {total_rows:,} "
          f"({100*anomaly_count/total_rows:.1f}%)")
    print("=" * 60)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating synthetic mining fleet telemetry...")
    csv_path, meta_path = generate_fleet_telemetry(output_dir="data")
    print_summary(csv_path, meta_path)
