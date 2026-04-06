# Fleet Intelligence — System Overview

AI-driven optimization pipeline for Bitcoin mining operations. Addresses two core challenges: **chip-level efficiency optimization** (finding optimal frequency/voltage/temperature operating points per ASIC) and **predictive maintenance** (detecting hardware degradation days before failure).

Built as composable single-concern workflows executing in Docker containers, chained via `continue_from` (Pattern 1) with file-based inter-task communication, deterministic re-execution, and content-addressed versioning.

## Two-Layer Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        ML Detection Layer                                │
│                                                                          │
│  mdk.pre_processing        mdk.train / mdk.score        mdk.analyze     │
│  ┌──────────────┐          ┌──────────────────┐     ┌────────────────┐  │
│  │ 1. Ingest    │          │ train_anomaly    │     │ analyze_trends │  │
│  │ 2. Features  │──continue──▶ _model          │     │ optimize_fleet │  │
│  │ 3. KPI       │  _from   │                  │     │ generate_report│  │
│  └──────────────┘          │ score_fleet      │──▶  └────────────────┘  │
│                            └──────────────────┘                          │
│                                                                          │
│  Output: tiers (CRITICAL/WARNING/DEGRADED/HEALTHY), risk scores,         │
│          safety flags, trend context, MOS alert codes                    │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      AI Reasoning Layer (SafeClaw)                        │
│                                                                          │
│  Reads: ML output + real-time market data + operator knowledge base      │
│  Proposes: specific MOS commands with rationale                          │
│  Goes through: Validance approval gate (human-in-the-loop)              │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    Governance Layer (Validance)                           │
│                                                                          │
│  Approval gates · Learned policies · Rate limits · Audit trail           │
│  Content-addressed execution chain (SHA-256 workflow hashes)             │
└──────────────────────────────────────────────────────────────────────────┘
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
      │                                                    ▼             │              │
      │                                           trend_analysis.json    │              │
      │                                              (trends)            │              │
      │                                                    │             │              │
      │                                                    ▼             ▼              │
      │                                           fleet_actions.json ◀── kpi + metadata │
      │                                          (optimize: tier-only)                  │
      │                                                    │                            │
      └────────────────────────────────────────────────────▼────────────────────────────┘
                                                    report.html
                                                     (report)
```

All intermediate data is **Parquet** (typed, columnar, compressed). Final output is a self-contained **HTML** dashboard with base64-embedded charts.

## Composable Workflows

| Workflow | Tasks | Description |
|----------|-------|-------------|
| `mdk.pre_processing` | 3 | ingest → features → kpi (shared prefix) |
| `mdk.train` | 1 | Train XGBoost + quantile regressors |
| `mdk.score` | 1 | Score fleet with pre-trained model |
| `mdk.analyze` | 3 | trends → optimize (tier-only) → report |
| `mdk.generate_corpus` | 1 | Generate synthetic training data |

**Orchestration chains:**
- Training: `generate_corpus → pre_processing → train`
- Inference: `pre_processing → score → analyze`
- Simulation: persistent loop alternating training/inference cycles

## Pipeline Tasks

| # | Task | Script | What it does |
|---|------|--------|-------------|
| 1 | Ingest | `tasks/ingest.py` | CSV → Parquet, schema validation, dedup, type coercion |
| 2 | Features | `tasks/features.py` | 55 engineered features: rolling stats (30m/1h/12h/24h), rates of change, fleet-relative z-scores, physics interactions |
| 3 | KPI | `tasks/kpi.py` | True Efficiency formula: `TE = (P_asic + P_cooling_norm) / (H × η_v)` with diagnostic decomposition |
| 4a | Train | `tasks/train_model.py` | XGBoost binary classifier + 3 per-anomaly sub-classifiers + quantile regressors |
| 4b | Score | `tasks/score.py` | 24h sliding window inference → per-device risk scores |
| 5a | Trends | `tasks/trend_analysis.py` | Per-device trend vectors, CUSUM regime detection, projected threshold crossings |
| 5b | Optimize | `tasks/optimize.py` | Tier classification (CRITICAL/WARNING/DEGRADED/HEALTHY), safety overrides, fleet redundancy |
| 6 | Report | `tasks/report.py` | HTML dashboard: charts + risk table + action table |

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

| Tier | Trigger | Flags |
|------|---------|-------|
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

These are deterministic classifications. Action decisions (what MOS commands to actually execute) are proposed by the AI reasoning agent (SafeClaw) and approved through Validance's approval gate.

## Tech Stack

- **Python 3.11** — all tasks
- **pandas + NumPy** — data processing
- **XGBoost** — anomaly detection model
- **scikit-learn** — evaluation metrics
- **matplotlib** — chart rendering
- **joblib** — model serialization
- **Workflow SDK** — workflow definition (imported only in `workflows/fleet_intelligence.py`; tasks have no SDK dependency)
- **Docker** — all tasks run in `python:3.11-slim` containers

## Synthetic Data

The pipeline runs on physics-modeled synthetic telemetry (`scripts/generate_training_corpus.py` + `scripts/physics_engine.py`):

- 5 scenarios composing ~1.6M rows (baseline, summer_heatwave, psu_degradation, cooling_failure, asic_aging)
- 6 hardware models, fleets of 10–15 devices per scenario, durations from 30–180 days
- CMOS power model: `P = k × V² × f + P_leak(T)`
- 10 anomaly types with ground-truth labels (thermal_deg, psu_instability, hashrate_decay, fan_bearing_wear, capacitor_aging, dust_fouling, thermal_paste_deg, solder_joint_fatigue, coolant_loop_fouling, firmware_cliff)
- Northern-site ambient model (64.5°N, seasonal + diurnal)
- Time-of-use energy pricing (peak/off-peak)

## MOS/MDK Integration Target

Designed for integration with Tether's open-source MOS platform. Controller tier classifications map to MOS RPC methods, actions carry MOS error codes, and the SafeClaw agent connects MOS's multi-voter approval system to Validance's approval gate. See `mos-reference.md` and `mos_platform_audit.md` for field mapping and gap analysis.

## Project Structure

```
mining_optimization/
├── scripts/                  # Standalone scripts + orchestrators
│   ├── physics_engine.py             # Shared physics (10 models, 10 anomaly types)
│   ├── generate_training_corpus.py   # Scenario-driven data generator
│   ├── simulation_engine.py          # Tick-by-tick simulation
│   ├── simulation_loop.py            # Continuous simulation orchestrator
│   ├── orchestrate_training.py       # Training chain (Pattern 1)
│   └── orchestrate_inference.py      # Inference chain (Pattern 1)
├── data/                     # Generated data + pipeline artifacts (gitignored)
│   ├── generated/            # Pre-pipeline (regenerate: python scripts/generate_training_corpus.py --all)
│   └── pipeline/             # Pipeline output (regenerate: run workflow)
├── tasks/                    # Pipeline task scripts (standalone Python)
│   ├── ingest.py
│   ├── features.py
│   ├── kpi.py
│   ├── train_model.py
│   ├── score.py
│   ├── trend_analysis.py
│   ├── optimize.py
│   └── report.py
├── workflows/                # Workflow definitions
│   ├── fleet_intelligence.py         # 5 composable workflows
│   └── fleet_simulation.py           # Persistent simulation loop
├── tests/                    # Test suite (74 tests)
│   ├── conftest.py                   # Session-scoped pipeline fixture
│   ├── test_pipeline_integration.py  # Integration tests (28 tests)
│   ├── test_trend_analysis.py        # Trend unit tests (40 tests)
│   └── test_phase6_tasks.py          # Fleet control tests (8 tests)
├── docs/                     # Documentation
└── project_materials/        # Reference PDFs and transcripts
```
