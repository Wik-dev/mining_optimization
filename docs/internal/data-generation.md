# Data Generation

## Overview

`scripts/generate_training_corpus.py` produces reproducible synthetic telemetry datasets from scenario JSON files, using the shared physics engine (`scripts/physics_engine.py`). Supports single-scenario or multi-scenario composition for rich training corpora. Fully deterministic (per-scenario seeds).

## How Data Are Generated

The generator steps through time at 5-minute intervals. For each timestep, it computes site-level conditions (ambient temperature, energy price), selects an operating mode per device, then advances a physics simulation:

| Model layer | Formula / logic |
|---|---|
| **Dynamic power** | P = k × V² × f (CMOS switching power) |
| **Static power** | P_leak = base × exp(0.02 × (T - 40)) — leakage grows with temperature |
| **Hashrate** | H = (H_nom / f_stock) × f × (1 - chip_degradation) — linear with clock |
| **Temperature** | Thermal resistance model with exponential approach to equilibrium; resistance increases with fouling |
| **Cooling** | Proportional controller: P_cool = base + k × max(0, T - setpoint), degraded by fouling |
| **Ambient temp** | Sinusoidal (seasonal + diurnal), configurable per site archetype |
| **Energy price** | Time-of-use: peak/off-peak with market noise, configurable per site |

### Operating Modes

Rule-based mode selection per device based on energy price and ambient temperature:

| Mode | Trigger | Clock multiplier | Voltage offset |
|---|---|---|---|
| `overclock` | price < $0.04 and ambient < 5°C | ×1.15 | +30mV |
| `normal` | default | ×1.00 | 0 |
| `underclock` | price > $0.06 | ×0.80 | -20mV |
| `idle` | price > $0.07 | 0 | — |

### Anomaly Types

10 anomaly types available via scenario configuration:

| Type | Physical mechanism |
|---|---|
| `thermal_deg` | Thermal resistance increase (heatsink fouling) |
| `psu_instability` | Voltage ripple noise |
| `hashrate_decay` | Partial ASIC chip failure |
| `dust_fouling` | Dust accumulation on heatsinks/fans |
| `thermal_paste_deg` | Thermal interface degradation |
| `fan_bearing_wear` | Fan RPM decay from bearing wear |
| `capacitor_aging` | PSU capacitor ESR increase |
| `solder_joint_fatigue` | Thermal cycling solder degradation |
| `coolant_loop_fouling` | Hydro cooling loop contamination |
| `firmware_cliff` | Sudden performance cliff from firmware bug |

Each anomaly has a `severity` (0–1) and `ramp_days` controlling ramp rate. Ground truth labels are included in every row.

### Scenarios

Scenario JSON files in `data/scenarios/` define fleet composition, site, anomaly schedules, and events:

| Scenario | Devices | Duration | Focus |
|---|---|---|---|
| `baseline.json` | 10 | 30d | Healthy fleet, no anomalies |
| `summer_heatwave.json` | 12 | 90d | Dust fouling + thermal paste degradation |
| `cooling_failure.json` | 10 | 60d | Coolant fouling + fan bearing wear |
| `psu_degradation.json` | 10 | 90d | PSU instability + capacitor aging |
| `asic_aging.json` | 15 | 180d | Hashrate decay + solder fatigue + firmware cliff |

## Outputs

### `training_telemetry.csv` — 35-column telemetry

Backward-compatible superset of the original 17-column schema. Includes operational state, error codes, economic fields, and per-anomaly-type labels.

### `training_metadata.json`

Provenance: generator version, seeds, fleet specs, scenario details, anomaly schedules, label statistics, and SHA-256 hash.

### `training_labels.csv`

Label columns only (timestamp, device_id, all `label_*` fields) for quick analysis.

## Usage

```bash
# Single scenario
python scripts/generate_training_corpus.py --scenario data/scenarios/baseline.json

# All scenarios combined into one training corpus
python scripts/generate_training_corpus.py --all --output data/training/ --seed 42
```

Outputs to `data/training/` by default.
