#!/usr/bin/env python3
"""
Shared Physics Engine for Mining Fleet Simulation
==================================================
Provides device models, physics simulation, anomaly injection, and telemetry
emission used by both generate_training_corpus.py and simulation_engine.py.

Core physics library with:
    - 10 device models (was 4)
    - 10 anomaly types (was 3)
    - Fan, dust, Arrhenius aging, solder fatigue, coolant fouling models
    - Operational state machine (RUNNING/CURTAILED/MAINTENANCE/FAILED)
    - Economic layer (hashprice, margin)
    - 35-column telemetry schema (backward-compatible superset of original 17)

Sources: Bitmain S21 troubleshooting, D-Central capacitor degradation,
         MicroBT M60S manual, Hashrate Index, CoinWarz calculator,
         Riot Platforms reports, ANTSPACE HK3 maintenance guide.

Author: Wiktor (MDK assignment, April 2026)
"""

import math
import random
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta

# ─── Operating Modes ──────────────────────────────────────────────────────────

MODE_NORMAL = "normal"
MODE_OVERCLOCK = "overclock"
MODE_UNDERCLOCK = "underclock"
MODE_IDLE = "idle"

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

# ─── Operational States ──────────────────────────────────────────────────────

STATE_RUNNING = "RUNNING"
STATE_CURTAILED = "CURTAILED"
STATE_MAINTENANCE = "MAINTENANCE"
STATE_FAILED = "FAILED"

# ─── Error Codes ──────────────────────────────────────────────────────────────
# From Bitmain log taxonomy / common firmware error categories

ERROR_NONE = "NONE"
ERROR_FAN_LOST = "ERROR_FAN_LOST"
ERROR_OVERTEMP = "OVERTEMP_PROTECTION"
ERROR_MISSING_CHIPS = "MISSING_CHIPS"
ERROR_POWER_FAULT = "POWER_FAULT"
ERROR_NETWORK_FAULT = "NETWORK_FAULT"
ERROR_PIC_READ = "PIC_READ_ERROR"

# ─── Device Model Catalog ────────────────────────────────────────────────────
# 10 models: 4 original + 6 new from research (D-Central, Mineshop, MicroBT, Canaan)
#
# Each entry: model_name, stock_clock_ghz, stock_voltage_v, nominal_hashrate_th,
#             nominal_power_w, efficiency_jth, cooling_base_w, cooling_type,
#             rated_temp_c, nominal_chip_count, nominal_hashboard_count

DEVICE_MODELS = {
    # ── Existing 4 models (identical specs to the original v1 generator) ──
    "S21-HYD": {
        "stock_clock_ghz": 1.60,
        "stock_voltage_v": 0.30,
        "nominal_hashrate_th": 335.0,
        "nominal_power_w": 5025.0,
        "efficiency_jth": 15.0,
        "cooling_base_w": 500.0,
        "cooling_type": "hydro",
        "gen": "current",
        "rated_temp_c": 75.0,   # Hydro-cooled: tighter thermal envelope
        "nominal_chip_count": 444,
        "nominal_hashboard_count": 3,
    },
    "M66S": {
        "stock_clock_ghz": 1.50,
        "stock_voltage_v": 0.32,
        "nominal_hashrate_th": 298.0,
        "nominal_power_w": 5370.0,
        "efficiency_jth": 18.0,
        "cooling_base_w": 550.0,
        "cooling_type": "air",
        "gen": "current",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 408,
        "nominal_hashboard_count": 3,
    },
    "S19XP": {
        "stock_clock_ghz": 1.35,
        "stock_voltage_v": 0.35,
        "nominal_hashrate_th": 141.0,
        "nominal_power_w": 3010.0,
        "efficiency_jth": 21.3,
        "cooling_base_w": 420.0,
        "cooling_type": "air",
        "gen": "previous",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 342,
        "nominal_hashboard_count": 3,
    },
    "S19jPro": {
        "stock_clock_ghz": 1.20,
        "stock_voltage_v": 0.38,
        "nominal_hashrate_th": 104.0,
        "nominal_power_w": 3068.0,
        "efficiency_jth": 29.5,
        "cooling_base_w": 400.0,
        "cooling_type": "air",
        "gen": "previous",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 342,
        "nominal_hashboard_count": 3,
    },
    # ── New 6 models from research ──────────────────────────────────────────
    # S21XP: Mineshop/D-Central — flagship air-cooled
    "S21XP": {
        "stock_clock_ghz": 1.55,
        "stock_voltage_v": 0.29,
        "nominal_hashrate_th": 270.0,
        "nominal_power_w": 3645.0,
        "efficiency_jth": 13.5,
        "cooling_base_w": 480.0,
        "cooling_type": "air",
        "gen": "flagship",
        "rated_temp_c": 78.0,
        "nominal_chip_count": 444,
        "nominal_hashboard_count": 3,
    },
    # S21Pro: D-Central — current gen air-cooled
    "S21Pro": {
        "stock_clock_ghz": 1.50,
        "stock_voltage_v": 0.30,
        "nominal_hashrate_th": 234.0,
        "nominal_power_w": 3510.0,
        "efficiency_jth": 15.0,
        "cooling_base_w": 460.0,
        "cooling_type": "air",
        "gen": "current",
        "rated_temp_c": 78.0,
        "nominal_chip_count": 444,
        "nominal_hashboard_count": 3,
    },
    # S21: D-Central/MiningNow — current gen standard
    "S21": {
        "stock_clock_ghz": 1.45,
        "stock_voltage_v": 0.31,
        "nominal_hashrate_th": 200.0,
        "nominal_power_w": 3500.0,
        "efficiency_jth": 17.5,
        "cooling_base_w": 440.0,
        "cooling_type": "air",
        "gen": "current",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 444,
        "nominal_hashboard_count": 3,
    },
    # M60S: MicroBT M60S manual — current gen
    "M60S": {
        "stock_clock_ghz": 1.40,
        "stock_voltage_v": 0.33,
        "nominal_hashrate_th": 186.0,
        "nominal_power_w": 3441.0,
        "efficiency_jth": 18.5,
        "cooling_base_w": 430.0,
        "cooling_type": "air",
        "gen": "current",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 384,
        "nominal_hashboard_count": 3,
    },
    # S19kPro: Hashrate Index — previous gen budget
    "S19kPro": {
        "stock_clock_ghz": 1.25,
        "stock_voltage_v": 0.36,
        "nominal_hashrate_th": 120.0,
        "nominal_power_w": 2760.0,
        "efficiency_jth": 23.0,
        "cooling_base_w": 400.0,
        "cooling_type": "air",
        "gen": "previous",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 342,
        "nominal_hashboard_count": 3,
    },
    # A1566: Canaan press release — current gen
    "A1566": {
        "stock_clock_ghz": 1.42,
        "stock_voltage_v": 0.32,
        "nominal_hashrate_th": 185.0,
        "nominal_power_w": 3420.0,
        "efficiency_jth": 18.5,
        "cooling_base_w": 435.0,
        "cooling_type": "air",
        "gen": "current",
        "rated_temp_c": 80.0,
        "nominal_chip_count": 380,
        "nominal_hashboard_count": 3,
    },
}

# ─── Site Archetypes ──────────────────────────────────────────────────────────
# Three reference climates for scenario design.
# Source: notes_mining_data.md, deep-research-report temperature distributions

SITE_ARCHETYPES = {
    "northern": {
        "latitude": 64.5,
        "ambient_baseline_c": -5.0,   # Early spring at 64°N
        "seasonal_swing_c": 7.5,
        "energy_cost_base_kwh": 0.035,
        "energy_cost_peak_kwh": 0.065,
    },
    "temperate": {
        "latitude": 31.0,
        "ambient_baseline_c": 15.0,   # Texas-like
        "seasonal_swing_c": 15.0,
        "energy_cost_base_kwh": 0.040,
        "energy_cost_peak_kwh": 0.070,
    },
    "hot": {
        "latitude": 24.0,
        "ambient_baseline_c": 30.0,   # UAE / equatorial
        "seasonal_swing_c": 8.0,
        "energy_cost_base_kwh": 0.045,
        "energy_cost_peak_kwh": 0.075,
    },
}


# ─── Device State ─────────────────────────────────────────────────────────────

@dataclass
class DeviceState:
    """Mutable state for a single ASIC device.

    Extended from original with new telemetry channels, internal degradation
    state, and operational state. All new fields default to neutral values
    for backward compatibility.
    """
    device_id: str
    model: str
    stock_clock_ghz: float
    stock_voltage_v: float
    nominal_hashrate_th: float
    nominal_power_w: float
    efficiency_jth: float
    cooling_base_w: float

    # Model metadata
    cooling_type: str = "air"
    rated_temp_c: float = 80.0
    nominal_chip_count: int = 342
    nominal_hashboard_count: int = 3

    # ── Dynamic state (original fields) ──
    clock_ghz: float = 0.0
    voltage_v: float = 0.0
    hashrate_th: float = 0.0
    power_w: float = 0.0
    temperature_c: float = 40.0
    cooling_power_w: float = 0.0
    mode: str = MODE_NORMAL

    # ── Original anomaly flags ──
    anomaly_thermal_deg: bool = False
    anomaly_psu_instability: bool = False
    anomaly_hashrate_decay: bool = False

    # ── Original internal degradation ──
    _thermal_fouling: float = 0.0
    _chip_degradation: float = 0.0
    _psu_ripple: float = 0.0

    # ── New telemetry channels ──
    fan_rpm: float = 0.0
    fan_rpm_target: float = 2000.0
    dust_index: float = 0.0            # 0–1, accumulation measure
    inlet_temp_c: float = 0.0          # Ambient + recirculation from dust
    voltage_ripple_mv: float = 0.0     # PSU health signal
    error_code: str = ERROR_NONE
    reboot_count: int = 0
    chip_count_active: int = 0         # Set from nominal in __post_init__
    hashboard_count_active: int = 3

    # ── New anomaly flags ──
    anomaly_fan_bearing_wear: bool = False
    anomaly_capacitor_aging: bool = False
    anomaly_dust_fouling: bool = False
    anomaly_thermal_paste_deg: bool = False
    anomaly_solder_joint_fatigue: bool = False
    anomaly_coolant_loop_fouling: bool = False
    anomaly_firmware_cliff: bool = False

    # ── Internal degradation state (new) ──
    _fan_bearing_health: float = 1.0          # 1=new → 0=seized
    _capacitor_health: float = 1.0            # 1=new → 0=dead (Arrhenius-driven)
    _thermal_paste_delta: float = 0.0         # Additional °C from paste degradation
    _solder_fatigue_cycles: float = 0.0       # Cumulative thermal cycle count
    _solder_affected_chips: int = 0           # Chips lost to solder fatigue
    _coolant_fouling: float = 0.0             # 0–1, hydro only
    _firmware_cliff_factor: float = 1.0       # 1.0 or <1.0 after firmware issue
    _arrhenius_accumulator: float = 0.0       # Cumulative aging integral
    _thermal_cycle_count: int = 0             # Number of significant thermal cycles
    _time_since_last_clean_days: float = 0.0  # Dust accumulation timer
    _prev_temp_for_cycle: float = 40.0        # For thermal cycle detection

    # ── Operational state ──
    operational_state: str = STATE_RUNNING
    economic_margin_usd: float = 0.0

    def __post_init__(self):
        self.clock_ghz = self.stock_clock_ghz
        self.voltage_v = self.stock_voltage_v
        self.chip_count_active = self.nominal_chip_count
        self.hashboard_count_active = self.nominal_hashboard_count


# ─── Environment Models ──────────────────────────────────────────────────────

def ambient_temperature(day: int, hour: float, site: dict) -> float:
    """Sinusoidal ambient temperature model with seasonal and diurnal variation.

    Parameterized by site archetype instead of hardcoded northern site.
    Identical behavior to original when called with northern site params.
    """
    baseline = site["ambient_baseline_c"]
    swing = site["seasonal_swing_c"]

    # Seasonal: day 0 = April 2, peak around summer solstice (day 172)
    day_of_year_approx = 92 + day
    seasonal = baseline + swing * math.sin(
        2 * math.pi * (day_of_year_approx - 80) / 365
    )

    # Diurnal: ±4°C swing, peak at 14:00
    diurnal = 4.0 * math.sin(2 * math.pi * (hour - 6) / 24)

    return seasonal + diurnal + random.gauss(0, 0.5)


def energy_price(hour: float, day: int, site: dict) -> float:
    """Time-of-use electricity pricing with site-specific rates.

    Peak: 08:00-20:00 weekdays. Off-peak: nights and weekends.
    Identical logic to original, parameterized by site.
    """
    weekday = day % 7
    is_weekend = weekday >= 5
    is_peak = not is_weekend and 8 <= hour < 20

    base = site["energy_cost_peak_kwh"] if is_peak else site["energy_cost_base_kwh"]
    noise = random.gauss(0, 0.002)
    return max(0.02, base + noise)


# ─── Operating Mode Selection ────────────────────────────────────────────────

def compute_operating_mode(device: DeviceState, e_price: float, t_ambient: float) -> str:
    """Rule-based operating mode selection.

    Identical logic to original the original v1 generator.
    """
    if e_price > 0.06:
        return MODE_UNDERCLOCK
    elif e_price > 0.07:
        return MODE_IDLE
    elif e_price < 0.04 and t_ambient < 5.0:
        return MODE_OVERCLOCK
    return MODE_NORMAL


# ─── Core Physics Step ────────────────────────────────────────────────────────

def step_physics(device: DeviceState, t_ambient: float, dt_hours: float) -> None:
    """Advance device state by one timestep using CMOS power model.

    Preserves original physics exactly, then layers new models on top.
    The original thermal_resistance, power, hashrate, temperature, and cooling
    calculations are identical to the original v1 generator.
    """
    mode = device.mode

    if mode == MODE_IDLE:
        device.clock_ghz = 0.0
        device.voltage_v = device.stock_voltage_v
        device.hashrate_th = 0.0
        device.power_w = 50.0   # Standby power
        device.cooling_power_w = device.cooling_base_w * 0.1
        device.temperature_c += (t_ambient + 5.0 - device.temperature_c) * 0.3
        return

    # ── Clock and voltage ──
    clock_mult = MODE_CLOCK_MULTIPLIER[mode]
    v_offset = MODE_VOLTAGE_OFFSET[mode]
    device.clock_ghz = device.stock_clock_ghz * clock_mult
    device.voltage_v = device.stock_voltage_v + v_offset

    # PSU instability anomaly: add voltage ripple (original behavior)
    if device._psu_ripple > 0:
        ripple = random.gauss(0, device._psu_ripple)
        device.voltage_v += ripple
        device.voltage_v = max(0.20, device.voltage_v)

    # Capacitor aging adds voltage ripple on top of PSU instability
    # Source: D-Central — degraded capacitors increase output ripple
    if device._capacitor_health < 1.0:
        cap_ripple_mv = (1.0 - device._capacitor_health) * 30.0
        device.voltage_ripple_mv = cap_ripple_mv + random.gauss(0, 2.0)
        # Ripple causes intermittent hash drops when severe
        if device._capacitor_health < 0.3:
            cap_voltage_noise = random.gauss(0, cap_ripple_mv / 1000.0)
            device.voltage_v += cap_voltage_noise
    else:
        device.voltage_ripple_mv = random.gauss(0, 1.0)  # Baseline noise

    device.voltage_ripple_mv = max(0.0, device.voltage_ripple_mv)

    # ── Power model: P = k × V² × f + P_static(T) ──
    # Calibrate k so that at stock settings we get nominal power
    k = device.nominal_power_w / (device.stock_voltage_v ** 2 * device.stock_clock_ghz)
    p_dynamic = k * device.voltage_v ** 2 * device.clock_ghz

    # Static/leakage power increases with temperature (exponential model)
    p_static_base = device.nominal_power_w * 0.05  # ~5% of total at 40°C
    temp_factor = math.exp(0.02 * (device.temperature_c - 40.0))
    p_static = p_static_base * temp_factor

    device.power_w = p_dynamic + p_static
    device.power_w += random.gauss(0, device.nominal_power_w * 0.005)
    device.power_w = max(0, device.power_w)

    # ── Hashrate: H ∝ f, minus chip degradation ──
    hash_per_ghz = device.nominal_hashrate_th / device.stock_clock_ghz
    chip_ratio = device.chip_count_active / device.nominal_chip_count
    degradation_factor = (1.0 - device._chip_degradation) * chip_ratio

    # Firmware cliff: sudden step-change in hashrate (not gradual)
    degradation_factor *= device._firmware_cliff_factor

    device.hashrate_th = hash_per_ghz * device.clock_ghz * degradation_factor
    device.hashrate_th += random.gauss(0, device.nominal_hashrate_th * 0.005)
    device.hashrate_th = max(0, device.hashrate_th)

    # ── Temperature model ──
    # Thermal resistance increases with fouling + thermal paste degradation
    thermal_resistance_clean = 0.008  # °C/W baseline
    thermal_resistance = thermal_resistance_clean * (1.0 + 2.0 * device._thermal_fouling)

    # Dust increases thermal resistance (source: Bitmain monthly cleaning guide)
    thermal_resistance *= (1.0 + 0.5 * device.dust_index)

    # Inlet temperature: ambient + recirculation from dust/airflow restriction
    device.inlet_temp_c = t_ambient + device.dust_index * 3.0

    t_target = device.inlet_temp_c + device.power_w * thermal_resistance
    # Add thermal paste degradation delta directly to target
    t_target += device._thermal_paste_delta

    # Coolant fouling reduces heat transfer (hydro only)
    # Source: ANTSPACE HK3 maintenance — flow restriction raises chip temp
    if device.cooling_type == "hydro" and device._coolant_fouling > 0:
        # Fouling reduces cooling capacity by up to 40%
        t_target += device.power_w * thermal_resistance * 0.4 * device._coolant_fouling

    # Thermal inertia (exponential decay toward target)
    tau = 0.4
    device.temperature_c += (t_target - device.temperature_c) * (1.0 - math.exp(-dt_hours / tau))
    device.temperature_c += random.gauss(0, 0.3)
    device.temperature_c = max(t_ambient, device.temperature_c)

    # ── Cooling power ──
    t_setpoint = 65.0
    cooling_proportional = max(0, device.temperature_c - t_setpoint) * 15.0
    device.cooling_power_w = device.cooling_base_w + cooling_proportional
    device.cooling_power_w *= (1.0 + 0.5 * device._thermal_fouling)
    # Dust increases cooling power draw (fans work harder)
    device.cooling_power_w *= (1.0 + 0.3 * device.dust_index)
    device.cooling_power_w += random.gauss(0, 10.0)
    device.cooling_power_w = max(0, device.cooling_power_w)


# ─── Fan Model ────────────────────────────────────────────────────────────────
# Source: Bitmain S21 troubleshooting — firmware uses proportional control.
# Normal range: 2000–3500 RPM, saturation at physical limit.

def step_fan_physics(device: DeviceState) -> None:
    """Update fan RPM based on temperature and bearing health."""
    if device.cooling_type == "hydro":
        # Hydro units have low background RPM (radiator auxiliary fans only)
        device.fan_rpm_target = min(2000.0, 800.0 + 20.0 * max(0, device.temperature_c - 40))
    else:
        # Air-cooled: proportional control from chip temperature.
        # Real miner firmware responds across the full operating range (not just
        # above 65°C setpoint). Slope calibrated so fans reach max at ~90°C.
        # Source: Bitmain S21 troubleshooting — P-control with wide deadband.
        device.fan_rpm_target = min(3500.0, 2000.0 + 30.0 * max(0, device.temperature_c - 40))

    # Actual RPM degraded by bearing health + noise
    device.fan_rpm = device.fan_rpm_target * device._fan_bearing_health + random.gauss(0, 20.0)
    device.fan_rpm = max(0.0, device.fan_rpm)

    # If fan RPM drops too low, temperature will rise (positive feedback)
    if device._fan_bearing_health < 0.5:
        # Reduced airflow means cooling is less effective
        # Increase thermal fouling effect to simulate reduced cooling
        effective_cooling_loss = (1.0 - device._fan_bearing_health) * 0.3
        device.cooling_power_w *= (1.0 - effective_cooling_loss)


# ─── Dust Accumulation Model ─────────────────────────────────────────────────
# Source: Bitmain recommends monthly cleaning; 5-10% cooling power increase/month.
# dust_index reaches 1.0 in ~3 months without cleaning.

def step_dust_physics(device: DeviceState, dt_hours: float) -> None:
    """Accumulate dust over time. Cleaning events reset dust_index to 0."""
    # Accumulation rate: ~0.33/month = 0.33/(30*24) per hour
    dust_rate = 0.33 / (30.0 * 24.0)
    device.dust_index += dust_rate * dt_hours
    device.dust_index = min(1.0, device.dust_index)
    device._time_since_last_clean_days += dt_hours / 24.0


# ─── Arrhenius Capacitor Aging ────────────────────────────────────────────────
# Source: D-Central capacitor degradation article; Arrhenius equation.
# Lifespan halves for every 10°C above rated temperature.
# Base lifespan ~40,000 hours at rated temp.

def step_capacitor_aging(device: DeviceState, dt_hours: float) -> None:
    """Arrhenius-driven capacitor health degradation."""
    if device.temperature_c <= device.rated_temp_c:
        acceleration = 1.0
    else:
        # Halves life per 10°C excess above rated temperature
        acceleration = 2.0 ** ((device.temperature_c - device.rated_temp_c) / 10.0)

    # Base lifespan 40,000 hours; aging_increment per hour
    aging_increment = (1.0 / 40000.0) * acceleration * dt_hours
    device._arrhenius_accumulator += aging_increment
    device._capacitor_health = max(0.0, 1.0 - device._arrhenius_accumulator)


# ─── Thermal Paste Degradation ────────────────────────────────────────────────
# Source: notes_mining_data failure table — gradual increase in chip-ambient delta.
# Paste degrades 0→10°C additional delta over months of operation.

def step_thermal_paste_degradation(device: DeviceState, dt_hours: float,
                                    severity: float, ramp_progress: float) -> None:
    """Increase chip-ambient temperature delta from paste degradation."""
    # Maximum additional delta is 10°C at full severity
    device._thermal_paste_delta = 10.0 * severity * ramp_progress


# ─── Solder Joint Fatigue ─────────────────────────────────────────────────────
# Source: D-Central hashboard repair guide — thermal cycling causes solder
# joint cracks, leading to intermittent chip errors then hashboard offline.

def step_solder_fatigue(device: DeviceState, dt_hours: float) -> None:
    """Track thermal cycles and apply chip dropout from solder fatigue."""
    # Detect significant thermal cycles (>10°C swing)
    temp_delta = abs(device.temperature_c - device._prev_temp_for_cycle)
    if temp_delta > 10.0:
        device._thermal_cycle_count += 1
        device._prev_temp_for_cycle = device.temperature_c

    # Solder fatigue accumulates with thermal cycles
    # Typical failure threshold: ~5000-10000 cycles depending on quality
    if device._solder_fatigue_cycles > 0:
        # Calculate affected chips based on accumulated fatigue
        fatigue_ratio = min(1.0, device._solder_fatigue_cycles / 8000.0)
        chips_affected = int(fatigue_ratio * device.nominal_chip_count * 0.5)
        device._solder_affected_chips = chips_affected
        device.chip_count_active = max(
            0,
            device.nominal_chip_count - device._solder_affected_chips
        )

        # If more than 1/3 of chips on a hashboard are gone, lose the hashboard
        chips_per_board = device.nominal_chip_count // device.nominal_hashboard_count
        if chips_per_board > 0:
            boards_lost = device._solder_affected_chips // chips_per_board
            device.hashboard_count_active = max(0, device.nominal_hashboard_count - boards_lost)


# ─── Coolant Loop Fouling (Hydro Only) ───────────────────────────────────────
# Source: ANTSPACE HK3 maintenance — flow restriction reduces heat transfer.

def step_coolant_fouling(device: DeviceState, dt_hours: float,
                          severity: float, ramp_progress: float) -> None:
    """Increase coolant fouling for hydro-cooled devices."""
    if device.cooling_type != "hydro":
        return
    device._coolant_fouling = severity * ramp_progress


# ─── Error Code Generation ───────────────────────────────────────────────────
# Source: Bitmain log taxonomy. Error codes are categorical; NONE >95% of ticks.

def determine_error_code(device: DeviceState) -> str:
    """Determine current error code based on device health state."""
    # Fan lost: bearing health < 0.5
    if device._fan_bearing_health < 0.5 and random.random() < 0.3:
        return ERROR_FAN_LOST

    # Overtemp protection: chip temp > 90°C
    if device.temperature_c > 90.0:
        return ERROR_OVERTEMP

    # Missing chips: solder fatigue affected chips > 10% of total
    if device._solder_affected_chips > device.nominal_chip_count * 0.1:
        if random.random() < 0.2:
            return ERROR_MISSING_CHIPS

    # Power fault: capacitor health < 0.3
    if device._capacitor_health < 0.3 and random.random() < 0.15:
        return ERROR_POWER_FAULT

    # PIC read error: rare, stress-correlated
    if device.temperature_c > 85.0 and random.random() < 0.05:
        return ERROR_PIC_READ

    return ERROR_NONE


# ─── Reboot Logic ────────────────────────────────────────────────────────────

def check_reboot(device: DeviceState) -> None:
    """Check if device should auto-reboot based on error conditions.

    Reboots increase under stress (high temp, fan failure, PSU issues).
    """
    reboot_probability = 0.0

    if device.error_code == ERROR_OVERTEMP:
        reboot_probability += 0.1
    if device.error_code == ERROR_FAN_LOST:
        reboot_probability += 0.05
    if device.error_code == ERROR_POWER_FAULT:
        reboot_probability += 0.08
    if device._capacitor_health < 0.2:
        reboot_probability += 0.03

    if random.random() < reboot_probability:
        device.reboot_count += 1


# ─── Operational State Machine ────────────────────────────────────────────────
# Source: Riot Platforms reports ~90% operating/deployed ratio.
# Semi-Markov model from deep-research-report.
#
# RUNNING → CURTAILED:    economic_margin < 0 (unprofitable)
# RUNNING → FAILED:       temp > 95°C OR fan_health < 0.1 OR cap_health < 0.1
# RUNNING → MAINTENANCE:  scheduled event
# CURTAILED → RUNNING:    economic_margin > 0
# FAILED → MAINTENANCE:   repair event
# MAINTENANCE → RUNNING:  event ends

def step_operational_state(device: DeviceState, has_maintenance_event: bool = False,
                            maintenance_ends: bool = False) -> None:
    """Update operational state machine based on device health and economics."""
    state = device.operational_state

    if state == STATE_RUNNING:
        # Check failure conditions
        if (device.temperature_c > 95.0 or
                device._fan_bearing_health < 0.1 or
                device._capacitor_health < 0.1):
            device.operational_state = STATE_FAILED
        elif device.economic_margin_usd < 0:
            device.operational_state = STATE_CURTAILED
        elif has_maintenance_event:
            device.operational_state = STATE_MAINTENANCE

    elif state == STATE_CURTAILED:
        if device.economic_margin_usd > 0:
            device.operational_state = STATE_RUNNING
        elif (device.temperature_c > 95.0 or
              device._fan_bearing_health < 0.1 or
              device._capacitor_health < 0.1):
            device.operational_state = STATE_FAILED

    elif state == STATE_FAILED:
        if has_maintenance_event:
            device.operational_state = STATE_MAINTENANCE

    elif state == STATE_MAINTENANCE:
        if maintenance_ends:
            # Reset critical health parameters on maintenance completion
            device._fan_bearing_health = max(0.8, device._fan_bearing_health)
            device._capacitor_health = max(0.5, device._capacitor_health)
            device.temperature_c = 40.0
            device.operational_state = STATE_RUNNING


# ─── Economic Layer ──────────────────────────────────────────────────────────
# Source: CoinWarz calculator, Fortune April 2026 BTC price.
# hashprice = (block_reward × btc_price × 86400) / (difficulty × 2^32) × (1 - pool_fee)
# margin = (hashprice × hashrate / 24) - ((power + cooling) / 1000 × energy_price)

DEFAULT_ECONOMIC = {
    "btc_price_usd": 66650.0,
    "network_difficulty_t": 133.79,   # Trillions
    "block_reward_btc": 3.125,
    "pool_fee_pct": 1.5,
}


def compute_economic_margin(device: DeviceState, e_price: float,
                             economic: dict = None) -> float:
    """Compute hourly economic margin in USD for a device.

    Returns margin per hour: revenue minus electricity cost.
    """
    if economic is None:
        economic = DEFAULT_ECONOMIC

    btc_price = economic["btc_price_usd"]
    difficulty_t = economic["network_difficulty_t"]
    block_reward = economic["block_reward_btc"]
    pool_fee = economic["pool_fee_pct"] / 100.0

    # Hashprice: daily revenue per TH/s ($/TH/day)
    # Formula: (10^12 hashes/TH × block_reward × btc_price × 86400 sec/day)
    #          / (difficulty × 2^32) × (1 - pool_fee)
    # The 1e12 converts from per-hash to per-TH/s.
    # Source: CoinWarz calculator, standard Bitcoin mining revenue formula.
    difficulty_raw = difficulty_t * 1e12
    hashprice_daily = (1e12 * block_reward * btc_price * 86400.0) / (difficulty_raw * 2**32) * (1.0 - pool_fee)

    # Revenue per hour for this device
    revenue_hourly = hashprice_daily * device.hashrate_th / 24.0

    # Electricity cost per hour (device power + cooling)
    total_power_kw = (device.power_w + device.cooling_power_w) / 1000.0
    cost_hourly = total_power_kw * e_price

    margin = revenue_hourly - cost_hourly
    device.economic_margin_usd = round(margin, 4)
    return margin


# ─── Anomaly Injection System ────────────────────────────────────────────────
# 10 anomaly types: 3 original + 7 new.
# Each anomaly has a start_day, ramp_days, and severity (0-1).

@dataclass
class AnomalySchedule:
    """Defines when an anomaly starts and how it progresses."""
    device_idx: int
    anomaly_type: str
    start_day: int
    ramp_days: float
    severity: float


# All supported anomaly types
ANOMALY_TYPES = [
    # Original 3
    "thermal_deg",
    "psu_instability",
    "hashrate_decay",
    # New 7
    "fan_bearing_wear",
    "capacitor_aging",
    "dust_fouling",
    "thermal_paste_deg",
    "solder_joint_fatigue",
    "coolant_loop_fouling",
    "firmware_cliff",
]


def apply_anomalies(device: DeviceState, device_idx: int,
                     day: float, dt_hours: float,
                     schedules: List[AnomalySchedule]) -> None:
    """Update device degradation state based on anomaly schedules.

    Original 3 anomaly types preserved with identical behavior.
    New 7 types layer additional physics on top.
    """
    # Reset all anomaly flags each tick (flags reflect current state)
    device.anomaly_thermal_deg = False
    device.anomaly_psu_instability = False
    device.anomaly_hashrate_decay = False
    device.anomaly_fan_bearing_wear = False
    device.anomaly_capacitor_aging = False
    device.anomaly_dust_fouling = False
    device.anomaly_thermal_paste_deg = False
    device.anomaly_solder_joint_fatigue = False
    device.anomaly_coolant_loop_fouling = False
    device.anomaly_firmware_cliff = False

    for sched in schedules:
        if sched.device_idx != device_idx:
            continue
        if day < sched.start_day:
            continue

        progress = min(1.0, (day - sched.start_day) / max(0.01, sched.ramp_days))
        severity = sched.severity * progress

        # ── Original anomaly types (identical behavior) ──
        if sched.anomaly_type == "thermal_deg":
            device._thermal_fouling = severity
            device.anomaly_thermal_deg = severity > 0.05

        elif sched.anomaly_type == "psu_instability":
            device._psu_ripple = severity * 0.05  # Up to 50mV ripple
            device.anomaly_psu_instability = severity > 0.05

        elif sched.anomaly_type == "hashrate_decay":
            device._chip_degradation = severity
            device.anomaly_hashrate_decay = severity > 0.02

        # ── New anomaly types ──

        elif sched.anomaly_type == "fan_bearing_wear":
            # Fan bearing health degrades from 1→0; cliff at 80% wear
            # Source: Miners1688 SLA — RPM variance then collapse
            device._fan_bearing_health = max(0.0, 1.0 - severity)
            device.anomaly_fan_bearing_wear = severity > 0.05

        elif sched.anomaly_type == "capacitor_aging":
            # Accelerated aging on top of natural Arrhenius
            # Source: D-Central capacitor degradation article
            accel_aging = severity * dt_hours / 5000.0  # Faster than natural
            device._arrhenius_accumulator += accel_aging
            device._capacitor_health = max(0.0, 1.0 - device._arrhenius_accumulator)
            device.anomaly_capacitor_aging = (1.0 - device._capacitor_health) > 0.05

        elif sched.anomaly_type == "dust_fouling":
            # Accelerated dust accumulation (e.g., dusty environment)
            # Source: Bitmain monthly cleaning guide
            extra_dust = severity * dt_hours / (20.0 * 24.0)  # Faster accumulation
            device.dust_index = min(1.0, device.dust_index + extra_dust)
            device.anomaly_dust_fouling = device.dust_index > 0.15

        elif sched.anomaly_type == "thermal_paste_deg":
            # Source: notes_mining_data failure table
            step_thermal_paste_degradation(device, dt_hours, sched.severity, progress)
            device.anomaly_thermal_paste_deg = device._thermal_paste_delta > 1.0

        elif sched.anomaly_type == "solder_joint_fatigue":
            # Accelerate thermal cycle counting
            # Source: D-Central hashboard repair
            device._solder_fatigue_cycles = severity * 8000.0  # Direct mapping
            step_solder_fatigue(device, dt_hours)
            device.anomaly_solder_joint_fatigue = device._solder_affected_chips > 0

        elif sched.anomaly_type == "coolant_loop_fouling":
            # Source: ANTSPACE HK3 maintenance
            step_coolant_fouling(device, dt_hours, sched.severity, progress)
            device.anomaly_coolant_loop_fouling = device._coolant_fouling > 0.05

        elif sched.anomaly_type == "firmware_cliff":
            # Step-change at a specific point in the ramp (not gradual)
            # Source: Bitmain firmware troubleshooting
            if progress >= 0.5:  # Cliff happens halfway through ramp
                # 10-30% hash drop depending on severity
                device._firmware_cliff_factor = 1.0 - (0.1 + 0.2 * sched.severity)
                device.anomaly_firmware_cliff = True
            else:
                device._firmware_cliff_factor = 1.0


# ─── Event System ────────────────────────────────────────────────────────────

def apply_events(device: DeviceState, device_idx: int, day: float,
                  events: List[dict], num_devices: int) -> Tuple[bool, bool]:
    """Process scheduled events (cleaning, firmware_update, maintenance).

    Returns (has_maintenance_event, maintenance_ends).
    """
    has_maintenance = False
    maintenance_ends = False

    for event in events:
        event_day = event.get("day", -1)
        if abs(day - event_day) > 0.5:  # Not on event day
            continue

        # Check if this event applies to this device
        indices = event.get("device_indices", [])
        if indices == "all":
            applies = True
        elif isinstance(indices, list) and device_idx in indices:
            applies = True
        else:
            applies = False

        if not applies:
            continue

        event_type = event.get("type", "")

        if event_type == "cleaning":
            # Reset dust accumulation
            device.dust_index = 0.0
            device._time_since_last_clean_days = 0.0

        elif event_type == "firmware_update":
            # Firmware update might fix firmware cliff or be neutral
            device._firmware_cliff_factor = 1.0
            device.anomaly_firmware_cliff = False

        elif event_type == "maintenance":
            has_maintenance = True
            # Maintenance lasts ~1 day; ends when we're past the event day
            if day > event_day + 0.5:
                maintenance_ends = True

    return has_maintenance, maintenance_ends


# ─── Full Simulation Tick ────────────────────────────────────────────────────

def simulate_tick(device: DeviceState, device_idx: int, day: float, hour: float,
                   dt_hours: float, site: dict, economic: dict,
                   anomaly_schedules: List[AnomalySchedule],
                   events: List[dict], num_devices: int) -> None:
    """Execute one complete simulation tick for a device.

    Runs all physics models in correct dependency order.
    """
    t_amb = ambient_temperature(int(day), hour, site)

    # Skip physics for non-running states
    if device.operational_state == STATE_FAILED:
        device.power_w = 0.0
        device.hashrate_th = 0.0
        device.cooling_power_w = 0.0
        device.fan_rpm = 0.0
        device.error_code = ERROR_OVERTEMP if device.temperature_c > 90 else ERROR_POWER_FAULT
        # Temperature decays toward ambient when off
        device.temperature_c += (t_amb - device.temperature_c) * 0.1
        device.inlet_temp_c = t_amb
        compute_economic_margin(device, energy_price(hour, int(day), site), economic)
        return

    if device.operational_state == STATE_MAINTENANCE:
        device.power_w = 0.0
        device.hashrate_th = 0.0
        device.cooling_power_w = device.cooling_base_w * 0.1
        device.fan_rpm = 0.0
        device.temperature_c += (t_amb + 5.0 - device.temperature_c) * 0.2
        device.inlet_temp_c = t_amb
        device.error_code = ERROR_NONE
        compute_economic_margin(device, energy_price(hour, int(day), site), economic)
        return

    # 1. Apply anomaly progression
    apply_anomalies(device, device_idx, day, dt_hours, anomaly_schedules)

    # 2. Apply events (cleaning, firmware updates, maintenance scheduling)
    has_maint, maint_ends = apply_events(device, device_idx, day, events, num_devices)

    # 3. Dust accumulation (baseline, before anomaly-accelerated dust)
    step_dust_physics(device, dt_hours)

    # 4. Capacitor aging (natural Arrhenius, before anomaly-accelerated aging)
    step_capacitor_aging(device, dt_hours)

    # 5. Determine operating mode
    e_price = energy_price(hour, int(day), site)
    device.mode = compute_operating_mode(device, e_price, t_amb)

    # If curtailed, force underclock
    if device.operational_state == STATE_CURTAILED:
        device.mode = MODE_UNDERCLOCK

    # 6. Core physics step
    step_physics(device, t_amb, dt_hours)

    # 7. Fan physics (after temperature is updated)
    step_fan_physics(device)

    # 8. Error codes and reboots
    device.error_code = determine_error_code(device)
    check_reboot(device)

    # 9. Economic margin
    compute_economic_margin(device, e_price, economic)

    # 10. Operational state transitions
    step_operational_state(device, has_maint, maint_ends)


# ─── Telemetry Row Emission ──────────────────────────────────────────────────
# 35-column schema: original 17 unchanged in same order + 18 new columns appended.

TELEMETRY_COLUMNS = [
    # Original 17 columns (backward compatible)
    "timestamp", "device_id", "model",
    "clock_ghz", "voltage_v", "hashrate_th",
    "power_w", "temperature_c", "cooling_power_w",
    "ambient_temp_c", "energy_price_kwh",
    "operating_mode", "efficiency_jth",
    "label_thermal_deg", "label_psu_instability",
    "label_hashrate_decay", "label_any_anomaly",
    # New 18 columns
    "fan_rpm", "fan_rpm_target", "dust_index", "inlet_temp_c",
    "voltage_ripple_mv", "error_code", "reboot_count",
    "chip_count_active", "hashboard_count_active",
    "label_fan_bearing_wear", "label_capacitor_aging",
    "label_dust_fouling", "label_thermal_paste_deg",
    "label_solder_joint_fatigue", "label_coolant_loop_fouling",
    "label_firmware_cliff",
    "operational_state", "economic_margin_usd",
]


def emit_telemetry_row(device: DeviceState, timestamp: datetime,
                        t_ambient: float, e_price: float) -> dict:
    """Build a telemetry row dict with all 35 columns."""
    # Compute instantaneous efficiency
    if device.hashrate_th > 0:
        eff = device.power_w / device.hashrate_th
    else:
        eff = 0.0

    any_anomaly = (
        device.anomaly_thermal_deg or
        device.anomaly_psu_instability or
        device.anomaly_hashrate_decay or
        device.anomaly_fan_bearing_wear or
        device.anomaly_capacitor_aging or
        device.anomaly_dust_fouling or
        device.anomaly_thermal_paste_deg or
        device.anomaly_solder_joint_fatigue or
        device.anomaly_coolant_loop_fouling or
        device.anomaly_firmware_cliff
    )

    return {
        # Original 17
        "timestamp": timestamp.isoformat(),
        "device_id": device.device_id,
        "model": device.model,
        "clock_ghz": round(device.clock_ghz, 4),
        "voltage_v": round(device.voltage_v, 4),
        "hashrate_th": round(device.hashrate_th, 2),
        "power_w": round(device.power_w, 1),
        "temperature_c": round(device.temperature_c, 2),
        "cooling_power_w": round(device.cooling_power_w, 1),
        "ambient_temp_c": round(t_ambient, 2),
        "energy_price_kwh": round(e_price, 4),
        "operating_mode": device.mode,
        "efficiency_jth": round(eff, 2),
        "label_thermal_deg": int(device.anomaly_thermal_deg),
        "label_psu_instability": int(device.anomaly_psu_instability),
        "label_hashrate_decay": int(device.anomaly_hashrate_decay),
        "label_any_anomaly": int(any_anomaly),
        # New 18
        "fan_rpm": round(device.fan_rpm, 0),
        "fan_rpm_target": round(device.fan_rpm_target, 0),
        "dust_index": round(device.dust_index, 4),
        "inlet_temp_c": round(device.inlet_temp_c, 2),
        "voltage_ripple_mv": round(device.voltage_ripple_mv, 2),
        "error_code": device.error_code,
        "reboot_count": device.reboot_count,
        "chip_count_active": device.chip_count_active,
        "hashboard_count_active": device.hashboard_count_active,
        "label_fan_bearing_wear": int(device.anomaly_fan_bearing_wear),
        "label_capacitor_aging": int(device.anomaly_capacitor_aging),
        "label_dust_fouling": int(device.anomaly_dust_fouling),
        "label_thermal_paste_deg": int(device.anomaly_thermal_paste_deg),
        "label_solder_joint_fatigue": int(device.anomaly_solder_joint_fatigue),
        "label_coolant_loop_fouling": int(device.anomaly_coolant_loop_fouling),
        "label_firmware_cliff": int(device.anomaly_firmware_cliff),
        "operational_state": device.operational_state,
        "economic_margin_usd": round(device.economic_margin_usd, 4),
    }


# ─── Scenario Loading ────────────────────────────────────────────────────────

def load_scenario(path: str) -> dict:
    """Load and validate a scenario JSON file.

    Returns parsed scenario dict with resolved site archetype and fleet.
    """
    with open(path) as f:
        scenario = json.load(f)

    # Resolve site archetype
    site_cfg = scenario.get("site", {})
    archetype_name = site_cfg.get("archetype", "northern")
    if archetype_name in SITE_ARCHETYPES:
        site = dict(SITE_ARCHETYPES[archetype_name])
        # Override with explicit values from scenario
        for key in ["latitude", "energy_cost_base_kwh", "energy_cost_peak_kwh"]:
            if key in site_cfg:
                site[key] = site_cfg[key]
    else:
        # Build site from explicit values
        site = {
            "latitude": site_cfg.get("latitude", 64.5),
            "ambient_baseline_c": site_cfg.get("ambient_baseline_c", -5.0),
            "seasonal_swing_c": site_cfg.get("seasonal_swing_c", 7.5),
            "energy_cost_base_kwh": site_cfg.get("energy_cost_base_kwh", 0.035),
            "energy_cost_peak_kwh": site_cfg.get("energy_cost_peak_kwh", 0.065),
        }
    scenario["_resolved_site"] = site

    # Resolve economic parameters
    economic = dict(DEFAULT_ECONOMIC)
    if "economic" in scenario:
        for key in DEFAULT_ECONOMIC:
            if key in scenario["economic"]:
                economic[key] = scenario["economic"][key]
    scenario["_resolved_economic"] = economic

    return scenario


def create_fleet_from_scenario(scenario: dict) -> List[DeviceState]:
    """Instantiate fleet of DeviceState objects from scenario definition."""
    fleet_spec = scenario.get("fleet", [])
    devices = []
    device_idx = 0

    for entry in fleet_spec:
        model_name = entry["model"]
        count = entry.get("count", 1)

        if model_name not in DEVICE_MODELS:
            raise ValueError(f"Unknown device model: {model_name}. "
                             f"Available: {list(DEVICE_MODELS.keys())}")

        specs = DEVICE_MODELS[model_name]

        for _ in range(count):
            device = DeviceState(
                device_id=f"ASIC-{device_idx:03d}",
                model=model_name,
                stock_clock_ghz=specs["stock_clock_ghz"],
                stock_voltage_v=specs["stock_voltage_v"],
                nominal_hashrate_th=specs["nominal_hashrate_th"],
                nominal_power_w=specs["nominal_power_w"],
                efficiency_jth=specs["efficiency_jth"],
                cooling_base_w=specs["cooling_base_w"],
                cooling_type=specs["cooling_type"],
                rated_temp_c=specs["rated_temp_c"],
                nominal_chip_count=specs["nominal_chip_count"],
                nominal_hashboard_count=specs["nominal_hashboard_count"],
            )
            devices.append(device)
            device_idx += 1

    return devices


def create_anomaly_schedules_from_scenario(scenario: dict) -> List[AnomalySchedule]:
    """Build anomaly schedules from scenario JSON."""
    anomalies_cfg = scenario.get("anomalies", [])
    schedules = []

    for anomaly in anomalies_cfg:
        atype = anomaly["type"]
        if atype not in ANOMALY_TYPES:
            raise ValueError(f"Unknown anomaly type: {atype}. "
                             f"Available: {ANOMALY_TYPES}")

        device_indices = anomaly.get("device_indices", [])
        if isinstance(device_indices, int):
            device_indices = [device_indices]

        start_day = anomaly.get("start_day", 0)
        ramp_days = anomaly.get("ramp_days", 30.0)
        severity = anomaly.get("severity", 0.5)

        for idx in device_indices:
            schedules.append(AnomalySchedule(
                device_idx=idx,
                anomaly_type=atype,
                start_day=start_day,
                ramp_days=ramp_days,
                severity=severity,
            ))

    return schedules


# ─── Default Fleet (matches original the original v1 generator exactly) ─────

def create_default_fleet() -> List[DeviceState]:
    """Create the original 10-device fleet from the original v1 generator.

    Identical composition: 2×S21-HYD, 2×M66S, 3×S19XP, 3×S19jPro.
    """
    profile_list = [
        "S21-HYD", "S21-HYD",
        "M66S", "M66S",
        "S19XP", "S19XP", "S19XP",
        "S19jPro", "S19jPro", "S19jPro",
    ]

    devices = []
    for i, model_name in enumerate(profile_list):
        specs = DEVICE_MODELS[model_name]
        device = DeviceState(
            device_id=f"ASIC-{i:03d}",
            model=model_name,
            stock_clock_ghz=specs["stock_clock_ghz"],
            stock_voltage_v=specs["stock_voltage_v"],
            nominal_hashrate_th=specs["nominal_hashrate_th"],
            nominal_power_w=specs["nominal_power_w"],
            efficiency_jth=specs["efficiency_jth"],
            cooling_base_w=specs["cooling_base_w"],
            cooling_type=specs["cooling_type"],
            rated_temp_c=specs["rated_temp_c"],
            nominal_chip_count=specs["nominal_chip_count"],
            nominal_hashboard_count=specs["nominal_hashboard_count"],
        )
        devices.append(device)

    return devices


def create_default_anomaly_schedule() -> List[AnomalySchedule]:
    """Create the original anomaly schedule from the original v1 generator.

    Identical: 2× thermal_deg, 1× psu_instability, 2× hashrate_decay.
    """
    return [
        AnomalySchedule(device_idx=7, anomaly_type="thermal_deg",
                         start_day=8, ramp_days=15.0, severity=0.7),
        AnomalySchedule(device_idx=4, anomaly_type="thermal_deg",
                         start_day=18, ramp_days=10.0, severity=0.4),
        AnomalySchedule(device_idx=3, anomaly_type="psu_instability",
                         start_day=14, ramp_days=2.0, severity=0.8),
        AnomalySchedule(device_idx=9, anomaly_type="hashrate_decay",
                         start_day=5, ramp_days=20.0, severity=0.25),
        AnomalySchedule(device_idx=2, anomaly_type="hashrate_decay",
                         start_day=22, ramp_days=5.0, severity=0.15),
    ]
