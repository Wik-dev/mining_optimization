# Data Generation

## Overview

`modules/generate_synthetic_data.py` produces a reproducible synthetic telemetry dataset simulating a 10-device ASIC mining fleet over 30 days. Deterministic (seed=42), single-script, zero external dependencies.

## How Data Are Generated

The generator steps through time at 5-minute intervals. For each timestep, it computes site-level conditions (ambient temperature, energy price), selects an operating mode per device, then advances a physics simulation:

| Model layer | Formula / logic |
|---|---|
| **Dynamic power** | P = k × V² × f (CMOS switching power) |
| **Static power** | P_leak = base × exp(0.02 × (T - 40)) — leakage grows with temperature |
| **Hashrate** | H = (H_nom / f_stock) × f × (1 - chip_degradation) — linear with clock |
| **Temperature** | Thermal resistance model with exponential approach to equilibrium; resistance increases with fouling |
| **Cooling** | Proportional controller: P_cool = base + k × max(0, T - setpoint), degraded by fouling |
| **Ambient temp** | Sinusoidal (seasonal + diurnal), northern site at 64.5°N |
| **Energy price** | Time-of-use: peak ($0.065/kWh, weekday 08-20), off-peak ($0.035/kWh), with market noise |

### Operating Modes

Rule-based mode selection per device based on energy price and ambient temperature:

| Mode | Trigger | Clock multiplier | Voltage offset |
|---|---|---|---|
| `overclock` | price < $0.04 and ambient < 5°C | ×1.15 | +30mV |
| `normal` | default | ×1.00 | 0 |
| `underclock` | price > $0.06 | ×0.80 | -20mV |
| `idle` | price > $0.07 | 0 | — |

### Anomaly Injection

Three anomaly types are injected on specific devices with scheduled start days and gradual ramp-up:

| Type | Devices | Mechanism | Observable effect |
|---|---|---|---|
| **A1 — Thermal degradation** | ASIC-007 (day 8), ASIC-004 (day 18) | Increases thermal resistance (fouling) | Creeping temperature rise, increased cooling power |
| **A2 — PSU instability** | ASIC-003 (day 14) | Adds voltage ripple noise | Chaotic voltage-driven thermal swings |
| **A3 — Hashrate decay** | ASIC-009 (day 5), ASIC-002 (day 22) | Reduces effective chip count | Steady hashrate drop, efficiency degradation |

Each anomaly has a `severity` (0–1) and `ramp_days` controlling how quickly it reaches full severity. Ground truth labels are included in every row for supervised training.

## Outputs

### `fleet_telemetry.csv` — 86,400 rows

| Field | Description |
|---|---|
| `timestamp` | ISO 8601, 5-min intervals starting 2026-04-02 |
| `device_id` | `ASIC-000` through `ASIC-009` |
| `model` | Hardware model (S21-HYD, M66S, S19XP, S19jPro) |
| `clock_ghz` | Core clock frequency |
| `voltage_v` | Core voltage |
| `hashrate_th` | Observed hashrate (TH/s) |
| `power_w` | Total ASIC power consumption (W) |
| `temperature_c` | Chip junction temperature (°C) |
| `cooling_power_w` | Cooling system power (W) |
| `ambient_temp_c` | Site ambient temperature (°C) |
| `energy_price_kwh` | Electricity spot price ($/kWh) |
| `operating_mode` | `normal` / `overclock` / `underclock` / `idle` |
| `efficiency_jth` | Instantaneous efficiency (J/TH) |
| `label_thermal_deg` | Ground truth: thermal degradation (0/1) |
| `label_psu_instability` | Ground truth: PSU instability (0/1) |
| `label_hashrate_decay` | Ground truth: hashrate decay (0/1) |
| `label_any_anomaly` | Ground truth: any anomaly active (0/1) |

### `fleet_metadata.json`

Provenance file: generator version, seed, full fleet specs (model, stock clock/voltage, nominal hashrate/power/efficiency per device), anomaly schedule, field descriptions, and SHA-256 hash of the CSV.

### `anomaly_verification.png`

Verification plots confirming all three anomaly types produce distinct, separable signals from healthy baselines.

## Configuration Options

All tuneable at the top of the script:

| Parameter | Default | Effect |
|---|---|---|
| `SEED` | 42 | Random seed — change for different noise realizations |
| `NUM_DEVICES` | 10 | Fleet size (must match `DEVICE_PROFILES` length) |
| `DAYS` | 30 | Simulation duration |
| `INTERVAL_MINUTES` | 5 | Telemetry sample rate |
| `DEVICE_PROFILES` | 10 entries | Fleet composition — model, stock settings, efficiency |
| `SITE_LATITUDE` | 64.5 | Controls ambient temperature model |
| `ENERGY_COST_BASE/PEAK` | 0.035 / 0.065 | Energy pricing bounds ($/kWh) |

Anomaly schedules are defined in `create_anomaly_schedule()` — device index, type, start day, ramp rate, and severity.

## Usage

```bash
cd modules/
python generate_synthetic_data.py
```

Outputs to `data/` by default. Prints fleet composition, anomaly schedule, and summary statistics on completion.
