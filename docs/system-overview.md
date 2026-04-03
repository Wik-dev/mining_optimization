# Fleet Intelligence — System Overview

AI-driven optimization pipeline for Bitcoin mining operations. Addresses two core challenges: **chip-level efficiency optimization** (finding optimal frequency/voltage/temperature operating points per ASIC) and **predictive maintenance** (detecting hardware degradation days before failure).

Built as a 7-task DAG workflow executing in Docker containers with file-based inter-task communication, deterministic re-execution, and content-addressed versioning.

## Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │           Workflow Engine                     │
                        │         (DAG orchestration, containers)      │
                        └──────────────────┬──────────────────────────┘
                                           │
     ┌─────────────────────────────────────┼──────────────────────────────────┐
     │                            Pipeline DAG                                │
     │                                                                        │
     │  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐      │
     │  │  1. Ingest    │───▶│  2. Features  │───▶│  3. True Efficiency  │      │
     │  │              │    │              │    │     KPI               │      │
     │  │  CSV→Parquet │    │  55 features │    │  J/TH normalized     │      │
     │  │  schema val  │    │  4 categories│    │  TE decomposition    │      │
     │  └──────────────┘    └──────────────┘    └──────────┬───────────┘      │
     │                                                     │                  │
     │                                          ┌──────────▼───────────┐      │
     │                                          │  4a. Train Model     │      │
     │                                          │                      │      │
     │                                          │  XGBoost classifier  │      │
     │                                          │  per-anomaly-type    │      │
     │                                          │  sub-classifiers     │      │
     │                                          └──────────┬───────────┘      │
     │                                                     │                  │
     │                                          ┌──────────▼───────────┐      │
     │                                          │  4b. Score Fleet     │      │
     │                                          │                      │      │
     │                                          │  24h sliding window  │      │
     │                                          │  per-device risk     │      │
     │                                          └──────────┬───────────┘      │
     │                                                     │                  │
     │                                          ┌──────────▼───────────┐      │
     │                                          │  5. Optimize Fleet   │      │
     │                                          │                      │      │
     │                                          │  Tier controller     │      │
     │                                          │  Safety overrides    │      │
     │                                          │  Fleet redundancy    │      │
     │                                          └──────────┬───────────┘      │
     │                                                     │                  │
     │                                          ┌──────────▼───────────┐      │
     │                                          │  6. Generate Report  │◀─ all│
     │                                          │                      │      │
     │                                          │  HTML dashboard      │      │
     │                                          │  7 charts + tables   │      │
     │                                          └──────────────────────┘      │
     │                                                                        │
     └────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
fleet_telemetry.csv ──▶ telemetry.parquet ──▶ features.parquet ──▶ kpi_timeseries.parquet
fleet_metadata.json         (ingest)            (features)              (kpi)
      │                                                                   │
      │                                                    ┌──────────────┼──────────────┐
      │                                                    ▼              ▼              │
      │                                           anomaly_model.joblib   │              │
      │                                           model_metrics.json     │              │
      │                                              (train_model)       │              │
      │                                                    │             │              │
      │                                                    ▼             │              │
      │                                           fleet_risk_scores.json │              │
      │                                                 (score)          │              │
      │                                                    │             │              │
      │                                                    ▼             ▼              │
      │                                           fleet_actions.json ◀── kpi + metadata │
      │                                                (optimize)                       │
      │                                                    │                            │
      └────────────────────────────────────────────────────▼────────────────────────────┘
                                                    report.html
                                                     (report)
```

All intermediate data is **Parquet** (typed, columnar, compressed). Final output is a self-contained **HTML** dashboard with base64-embedded charts.

## Pipeline Tasks

| # | Task | Script | What it does |
|---|------|--------|-------------|
| 1 | Ingest | `tasks/ingest.py` | CSV → Parquet, schema validation, dedup, type coercion |
| 2 | Features | `tasks/features.py` | 55 engineered features: rolling stats (30m/1h/12h/24h), rates of change, fleet-relative z-scores, physics interactions |
| 3 | KPI | `tasks/kpi.py` | True Efficiency formula: `TE = (P_asic + P_cooling_norm) / (H × η_v)` with diagnostic decomposition |
| 4a | Train | `tasks/train_model.py` | XGBoost binary classifier + 3 per-anomaly sub-classifiers. Time-based 70/30 split |
| 4b | Score | `tasks/score.py` | 24h sliding window inference → per-device risk scores |
| 5 | Optimize | `tasks/optimize.py` | Tier-based controller (CRITICAL/WARNING/DEGRADED/HEALTHY), safety overrides, fleet redundancy |
| 6 | Report | `tasks/report.py` | HTML dashboard: 7 charts, risk table, action table |

## True Efficiency KPI

The central analytical contribution. Standard `J/TH = P/H` is insufficient — it ignores voltage inefficiency, cooling overhead, and ambient conditions.

```
TE = (P_asic + P_cooling_norm) / (H × η_v)    [J/TH, lower = better]

Where:
  η_v = (V_optimal / V_actual)²                Voltage efficiency factor
  V_optimal = V_stock × (f / f_stock)^0.6      CMOS voltage-frequency scaling
  P_cooling_norm = P_cool × (T_chip - 25) /    Ambient-normalized cooling cost
                   max(T_chip - T_ambient, 1)
```

TE decomposes into three independent diagnostic factors — each maps to a failure mode:

| Factor | Indicates |
|--------|-----------|
| `TE_base` (P/H) | Hashrate decay → chip degradation |
| `1/η_v` | Voltage penalty → PSU instability |
| `P_cooling_norm/H` | Cooling overhead → thermal fouling |

## Controller Tiers

| Tier | Trigger | Actions |
|------|---------|---------|
| CRITICAL | risk > 0.9 | Clock → 70% (V/f coupled), immediate inspection, 60s monitoring |
| WARNING | risk > 0.5 | Clock → 85%, next-window inspection, 120s monitoring |
| DEGRADED | TE_score < 0.8 | Reset frequency to stock (restores nominal V/f point), 180s monitoring |
| HEALTHY | default | Hold settings; suggest 5% overclock if headroom > 10°C |

Safety overrides (applied before tier logic):

| Override | Trigger | Action |
|----------|---------|--------|
| Thermal hard limit | T > 80°C | Clock → 80% stock (CRITICAL) |
| Thermal emergency low | T < 10°C | Sleep mode + immediate inspection — coolant freeze risk |
| Thermal low warning | 10°C ≤ T < 20°C | Clock → 70% + fan min (air-cooled only; hydro uses liquid loop) |
| Overvoltage | V > 110% stock | Reset frequency to stock — V/f coupled, voltage adjusts implicitly |
| Fleet redundancy | All devices of same model flagged | Defer lowest-risk device's inspection |

All commands are annotated with MOS RPC methods (`setFrequency`, `setPowerMode`, etc.) and MOS alert codes. No `set_voltage` commands — voltage is controlled implicitly through the ASIC's V/f curve.

## Tech Stack

- **Python 3.11** — all tasks
- **pandas + NumPy** — data processing
- **XGBoost** — anomaly detection model
- **scikit-learn** — evaluation metrics
- **matplotlib** — chart rendering
- **joblib** — model serialization
- **Workflow SDK** — DAG definition (imported only in `workflows/fleet_intelligence.py`; tasks have no SDK dependency)
- **Docker** — all tasks run in `python:3.11-slim` containers

## Synthetic Data

The pipeline runs on physics-modeled synthetic telemetry (`scripts/generate_synthetic_data.py`):

- 10 devices, 30 days, 5-min intervals → 86,400 rows
- CMOS power model: `P = k × V² × f + P_leak(T)`
- Injected anomalies with ground-truth labels (thermal degradation, PSU instability, hashrate decay)
- Northern-site ambient model (64.5°N, seasonal + diurnal)
- Time-of-use energy pricing (peak/off-peak)

## MOS/MDK Integration Target

Designed for integration with Tether's open-source MOS platform. Controller commands map to MOS RPC methods, actions carry MOS error codes, and the report includes the MOS multi-voter approval system context. See `mos-reference.md` and `mos_platform_audit.md` for field mapping and gap analysis.

## Project Structure

```
mining_optimization/
├── scripts/                  # Standalone scripts
│   └── generate_synthetic_data.py
├── data/                     # Generated data + pipeline artifacts (gitignored)
│   ├── generated/            # Pre-pipeline (regenerate: python scripts/generate_synthetic_data.py)
│   └── pipeline/             # Pipeline output (regenerate: run workflow)
├── tasks/                    # Pipeline task scripts (standalone Python)
│   ├── ingest.py
│   ├── features.py
│   ├── kpi.py
│   ├── train_model.py
│   ├── score.py
│   ├── optimize.py
│   └── report.py
├── workflows/                # Workflow DAG definition
│   └── fleet_intelligence.py
├── docs/                     # Documentation
└── project_materials/        # Reference PDFs and transcripts
```
