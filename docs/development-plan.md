# Predictive Fleet Simulation — Development Plan

> From "what's wrong now?" to "what will go wrong, when, and at what cost?"

This document defines a 6-phase roadmap to evolve the MDK fleet intelligence pipeline from single-pass batch analysis into a predictive simulation system with continuous monitoring, trend-aware control, economic optimization, and AI-driven action proposals.

---

## Table of Contents

- [Current State](#current-state)
- [Phase 1: Data Separation](#phase-1-data-separation)
- [Phase 2: Continuous Simulation Loop](#phase-2-continuous-simulation-loop)
- [Phase 3: Rolling Window + Trend Analysis](#phase-3-rolling-window--trend-analysis)
- [Phase 4: Economic Cost Modeling](#phase-4-economic-cost-modeling)
- [Phase 5: Predictive Model Evolution](#phase-5-predictive-model-evolution)
- [Phase 6: AI Agent Integration (SafeClaw)](#phase-6-ai-agent-integration-safeclaw)
- [Key Design Decisions](#key-design-decisions)
- [Phase Dependency Graph](#phase-dependency-graph)

---

## Current State

The fleet intelligence pipeline (`mdk.fleet_intelligence`) is a 7-task DAG that runs as a single batch:

```
[1] ingest_telemetry
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4a] train_anomaly_model ──────┐
                               │
                          [4b] score_fleet
                               │
                          [5] optimize_fleet
                               │
                          [6] generate_report
```

**What it does well:**
- Physics-grounded True Efficiency KPI with 3-factor decomposition
- 55 engineered features across rolling windows, rates of change, cross-device correlations
- XGBoost anomaly detection (F1=92.8%) with per-anomaly sub-classifiers
- 4-tier controller with safety overrides and MOS command mapping
- Interactive HTML dashboard with 7 embedded charts

**What it cannot do:**
- No forward prediction — no time-to-failure estimates
- No trend detection — no assessment of whether risk is rising, stable, or recovering
- No adaptive baselines — all thresholds are fixed
- No economic optimization — commands issued for safety, not cost-minimization
- No continuous operation — single-pass batch, no simulation loop
- No historical context across runs — each execution is independent

---

## Phase 1: Data Separation

**Goal:** Split the monolithic data generator into two purpose-built tools — a rich training corpus generator and a tick-by-tick simulation engine — establishing the data foundation for all subsequent phases.

### Rationale

The original monolithic generator served double duty: training data and pipeline input. These are fundamentally different needs. Training requires breadth (many scenarios, long spans, diverse anomalies). Simulation requires statefulness (tick-by-tick progression, real-time-compatible output, speed control). Separating them lets each evolve independently.

### Architectural Changes

**New files:**

| File | Purpose |
|------|---------|
| `scripts/generate_training_corpus.py` | Parameterized multi-scenario training data generator |
| `scripts/simulation_engine.py` | Stateful tick-by-tick telemetry simulator with speed control |
| `data/scenarios/baseline.json` | Default fleet — 10 devices, 30 days, no anomalies |
| `data/scenarios/summer_heatwave.json` | Sustained high ambient, cooling stress |
| `data/scenarios/psu_degradation.json` | Multi-device PSU failure cascade |
| `data/scenarios/cooling_failure.json` | Partial coolant loop failure (hydro units) |
| `data/scenarios/asic_aging.json` | Long-term chip degradation (90+ days) |

**Modified files:**

| File | Change |
|------|--------|
| `scripts/physics_engine.py` | Shared physics module extracted from original generator. |

### Training Corpus Generator (`generate_training_corpus.py`)

Produces large, diverse datasets for model training:

- **Scenario-driven:** Each scenario JSON defines device profiles, environmental parameters, anomaly injection schedule, and duration
- **Scale:** 10+ devices, 90+ days per scenario, composable anomaly types
- **Extended anomaly library:** Beyond the current 3 types (thermal degradation, PSU instability, hashrate decay), add: fan bearing wear, thermal paste degradation, coolant loop fouling, firmware hashrate cliff, power grid instability
- **Parameterized injection:** Scenario JSON specifies anomaly onset, ramp rate, severity curve, and affected devices — not hardcoded
- **Output:** `training_telemetry.parquet`, `training_metadata.json`, `training_labels.csv`

Scenario JSON structure:
```json
{
  "name": "psu_degradation",
  "duration_days": 90,
  "fleet": [
    {"model": "S21-HYD", "count": 3},
    {"model": "S19XP", "count": 4},
    {"model": "S19jPro", "count": 3}
  ],
  "environment": {
    "latitude": 64.5,
    "energy_cost_base": 0.035,
    "energy_cost_peak": 0.065,
    "ambient_override": null
  },
  "anomalies": [
    {"type": "psu_instability", "device_idx": [2, 5], "start_day": 10, "ramp_days": 5, "severity": 0.8},
    {"type": "psu_instability", "device_idx": [7], "start_day": 40, "ramp_days": 3, "severity": 0.95}
  ]
}
```

### Simulation Engine (`simulation_engine.py`)

Tick-by-tick stateful telemetry generation:

- **Stateful:** Maintains `DeviceState` across ticks. Each call advances the simulation by one interval (default 5 min).
- **Speed control:** `--speed-factor N` where 1=real-time, 60=1hr/min, 1440=1day/min
- **Offline mode:** `--speed-factor 0` (or `--offline`) runs at max speed — same code path, no sleep
- **Output per tick:** One batch of telemetry rows (all devices at current timestamp) written to a specified directory
- **Scenario input:** Same scenario JSON format as training corpus generator
- **Interface:** Identical CSV schema to real MOS telemetry feed — the analysis pipeline cannot distinguish simulated from real data

```
# Real-time simulation (5 min intervals)
python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --speed-factor 1

# Accelerated (1 day per minute)
python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --speed-factor 1440

# Offline batch (max speed, writes all ticks immediately)
python scripts/simulation_engine.py --scenario data/scenarios/baseline.json --offline
```

### Workflow Engine Capabilities Used

None — Phase 1 is pure data infrastructure. The generators are standalone scripts that produce files consumed by the existing pipeline.

### DAG After Phase 1

No change to the DAG. The pipeline consumes the same input format regardless of which generator produced it.

```
[1] ingest_telemetry          ← reads from training corpus OR simulation engine
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4a] train_anomaly_model ──────┐
                               │
                          [4b] score_fleet
                          ...
```

### Verification Criteria

- [ ] Training corpus generates 90+ day datasets with 10+ devices
- [ ] Simulation engine produces identical 35-column telemetry schema
- [ ] Pipeline runs identically on data from either generator (same output structure, comparable metrics)
- [ ] `--speed-factor 60` produces one telemetry batch per second
- [ ] `--offline` completes 30-day simulation in < 30 seconds
- [ ] All 5 scenario files produce valid, parseable datasets
- [ ] Model trained on multi-scenario corpus achieves F1 >= 90% (current baseline: 92.8%)

### Dependencies

None — this is the foundation phase.

---

## Phase 2: Continuous Simulation Loop

**Goal:** Run the fleet intelligence pipeline continuously against a simulated MOS data feed, using persistent workers and session continuations to maintain state across analysis cycles.

### Rationale

The current pipeline runs once and exits. A real MOS deployment would receive telemetry continuously and analyze it at regular intervals. This phase introduces a long-running simulation loop that triggers the analysis pipeline per batch, building toward the always-on monitoring system needed for trend analysis (Phase 3) and predictive control (Phases 4-6).

### Architectural Changes

**New files:**

| File | Purpose |
|------|---------|
| `workflows/fleet_simulation.py` | Workflow definition for the simulation orchestrator |
| `scripts/simulation_loop.py` | Orchestrator script — runs inside persistent container |

**Modified files:**

| File | Change |
|------|--------|
| `workflows/fleet_intelligence.py` | Add `mode` parameter: `training` (full DAG) vs `inference` (skip train, use pre-trained model) |
| `tasks/score.py` | Accept `--model-path` argument to load pre-trained model instead of requiring train task output |

### Simulation Loop Architecture

```
┌─────────────────────────────────────────────────────┐
│  Persistent Container (simulation_loop.py)          │
│                                                     │
│  while running:                                     │
│    1. simulation_engine.advance(interval)            │
│    2. write batch → /work/input/                     │
│    3. POST /api/workflows/mdk.fleet_intelligence/    │
│       trigger {                                      │
│         session_hash: "sim_abc123",                  │
│         continue_from: prior_run_hash,               │
│         parameters: {                                │
│           mode: "inference",                         │
│           model_path: "/work/models/latest.joblib"   │
│         }                                            │
│       }                                              │
│    4. poll for completion                             │
│    5. read results → update metrics                  │
│    6. sleep(interval / speed_factor)                 │
│                                                     │
└─────────────────────────────────────────────────────┘
         │                         ▲
         │  trigger per batch      │  results
         ▼                         │
┌─────────────────────────────────────────────────────┐
│  mdk.fleet_intelligence (inference mode)            │
│                                                     │
│  ingest → features → kpi → score → optimize → report│
│                              │                      │
│                    (uses pre-trained model)          │
└─────────────────────────────────────────────────────┘
```

### Dual-Mode DAG

The `fleet_intelligence` workflow gains a `mode` parameter:

- **`training` (default):** Full DAG as today — ingest through report, including `train_anomaly_model`
- **`inference`:** Skips training. `score_fleet` loads a pre-trained model from `--model-path` instead of depending on `train_anomaly_model` output. DAG becomes:

```
[1] ingest_telemetry
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4b] score_fleet            ← loads pre-trained model
 │
[5] optimize_fleet
 │
[6] generate_report
```

### Workflow Engine Capabilities Used

| Capability | Purpose |
|------------|---------|
| **Persistent workers** (`persistent=True`) | Simulation container stays alive across ticks — maintains device state, avoids container restart overhead |
| **Sessions** (`session_hash`) | Groups all analysis runs in one simulation under a single session ID for querying and correlation |
| **Continuations** (`continue_from`) | Links each analysis run to its predecessor, building a chain that Phase 3 reads for historical context |

### Verification Criteria

- [ ] Simulation loop completes 12+ analysis cycles at 60x speed without errors
- [ ] All cycles share the same `session_hash`
- [ ] Continuation chain is intact — each run references its predecessor
- [ ] Inference mode skips training and uses pre-trained model correctly
- [ ] Pre-trained model path is configurable (not hardcoded)
- [ ] Loop handles transient API failures gracefully (retry with backoff)
- [ ] `/api/sessions/{session_hash}/runs` returns all linked analysis runs

### Dependencies

- **Phase 1:** Simulation engine provides tick-by-tick telemetry feed

---

## Phase 3: Rolling Window + Trend Analysis

**Goal:** Add a trend analysis task that reads historical context from the continuation chain, computes per-device trend vectors, and projects future states — transforming the controller from reactive ("what's wrong now?") to predictive ("what will go wrong in Y hours?").

### Rationale

This is the highest-value phase. The current pipeline has zero memory across runs — each analysis is independent. By reading history from the continuation chain, we can detect whether devices are degrading, stable, or recovering. Forward projection enables proactive intervention before a device crosses a critical threshold. A device at TE=0.90 but falling at -0.03/hour is more urgent than one at TE=0.80 but recovering.

### Architectural Changes

**New files:**

| File | Purpose |
|------|---------|
| `tasks/trend_analysis.py` | Per-device trend computation, regime detection, forward projection |

**Modified files:**

| File | Change |
|------|--------|
| `workflows/fleet_intelligence.py` | Insert `analyze_trends` task between `score_fleet` and `optimize_fleet` |
| `tasks/optimize.py` | Read trend analysis output; trend-aware tier classification |
| `tasks/report.py` | Add trend visualizations (TE trajectory, projected thresholds, trend heatmap) |

### Trend Analysis Task

Inserted between `score_fleet` and `optimize_fleet`:

```python
# tasks/trend_analysis.py
#
# Reads: kpi_timeseries.parquet, fleet_risk_scores.json,
#         + historical KPI/risk from prior N continuation runs
# Writes: trend_analysis.json
```

**Per-device computations:**

| Metric | Method | Windows |
|--------|--------|---------|
| TE_score trend | Linear regression slope | 1h, 6h, 24h, 7d |
| Risk trend | Linear regression slope | 1h, 6h, 24h, 7d |
| Temperature trend | EWMA + slope | 6h, 24h |
| Regime change | CUSUM detection | Rolling |
| Forward projection | Linear extrapolation from current slope | Per threshold |

**Output structure** (`trend_analysis.json`):

```json
{
  "session_hash": "sim_abc123",
  "analysis_timestamp": "2026-04-15T10:00:00Z",
  "continuation_depth": 48,
  "devices": {
    "ASIC-001": {
      "current": {"te_score": 0.92, "risk": 0.15, "temperature_c": 62.3},
      "trends": {
        "te_score_1h": {"slope": -0.001, "r2": 0.85, "direction": "stable"},
        "te_score_24h": {"slope": -0.008, "r2": 0.92, "direction": "declining"},
        "risk_24h": {"slope": 0.003, "r2": 0.78, "direction": "rising"}
      },
      "projections": {
        "te_score_crosses_0.8": {"hours": 72.5, "confidence": 0.82},
        "te_score_crosses_0.6": {"hours": 168.0, "confidence": 0.61},
        "risk_crosses_0.5": {"hours": null, "confidence": null}
      },
      "regime": "stable",
      "regime_change_detected": false
    },
    "ASIC-007": {
      "current": {"te_score": 0.71, "risk": 0.68, "temperature_c": 74.1},
      "trends": {
        "te_score_1h": {"slope": -0.025, "r2": 0.97, "direction": "falling_fast"},
        "te_score_24h": {"slope": -0.018, "r2": 0.95, "direction": "declining"}
      },
      "projections": {
        "te_score_crosses_0.6": {"hours": 4.4, "confidence": 0.95}
      },
      "regime": "degrading",
      "regime_change_detected": true
    }
  }
}
```

### Trend-Aware Controller

The optimizer gains trend context for tier classification:

| Scenario | Current Tier | Trend-Aware Tier | Rationale |
|----------|-------------|------------------|-----------|
| TE=0.90, stable | HEALTHY | HEALTHY | No change needed |
| TE=0.90, falling -0.03/hr | HEALTHY | **WARNING** | Projects DEGRADED in 3h |
| TE=0.70, falling | DEGRADED | **CRITICAL** | Rapid degradation, immediate action |
| TE=0.75, recovering +0.01/hr | DEGRADED | DEGRADED (RECOVERING) | Watch, reduce intervention urgency |
| TE=0.85, regime change detected | HEALTHY | **WARNING** | New degradation pattern onset |

### DAG After Phase 3

```
[1] ingest_telemetry
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4a] train_anomaly_model ──────┐       (training mode only)
                               │
                          [4b] score_fleet
                               │
                          [5]  analyze_trends    ← NEW
                               │
                          [6]  optimize_fleet    ← now reads trend_analysis.json
                               │
                          [7]  generate_report   ← trend charts added
```

### Workflow Engine Capabilities Used

| Capability | Purpose |
|------------|---------|
| **Continuations** (`continue_from`) | `analyze_trends` reads KPI/risk data from prior N runs to compute multi-window trends |
| **Sessions** (`session_hash`) | Scopes the rolling window to the current simulation session |

### Verification Criteria

- [ ] Trend analysis reads at least 12 prior continuation runs for 1h window
- [ ] Forward projection for a linearly-degrading device is within 10% of actual crossing time
- [ ] Recovering devices are correctly classified (positive TE slope)
- [ ] Regime change detection fires within 2 intervals of actual onset
- [ ] Trend-aware controller escalates TE=0.90-but-falling from HEALTHY to WARNING
- [ ] Report includes trend trajectory chart and projected threshold crossings
- [ ] Performance: trend analysis completes in < 10 seconds for 48 continuation-depth history

### Dependencies

- **Phase 2:** Continuous simulation loop provides the continuation chain that trend analysis reads

---

## Phase 4: Economic Cost Modeling

**Goal:** Replace the current fixed-threshold tier system with cost-driven decision making. For each device, compute the expected cost of each possible action over configurable time horizons, and select the action that minimizes total cost.

### Rationale

The current controller makes decisions based on physics (temperature limits, risk thresholds) but ignores economics. An operator cares about total cost of ownership: a device at WARNING might be cheaper to keep running (accepting degradation) than to shut down for maintenance during peak pricing. Conversely, a HEALTHY device might be worth underclocking if electricity costs exceed marginal revenue. Cost modeling turns the controller into an economic optimizer.

### Architectural Changes

**New files:**

| File | Purpose |
|------|---------|
| `tasks/cost_projection.py` | Per-device cost computation over configurable horizons |
| `data/cost_model.json` | Economic parameters: energy costs, revenue model, maintenance costs |

**Modified files:**

| File | Change |
|------|--------|
| `workflows/fleet_intelligence.py` | Insert `project_costs` between `analyze_trends` and `optimize_fleet` |
| `tasks/optimize.py` | Use cost projections to select optimal action per device; add fleet-level constraints |
| `tasks/report.py` | Add cost breakdown charts, ROI projections, fleet-level economic summary |

### Cost Model Configuration (`data/cost_model.json`)

```json
{
  "version": "1.0",
  "currency": "USD",
  "energy": {
    "base_rate_kwh": 0.035,
    "peak_rate_kwh": 0.065,
    "peak_hours": [8, 9, 10, 11, 17, 18, 19, 20]
  },
  "revenue": {
    "btc_price_usd": 85000,
    "network_difficulty": 119.1e12,
    "pool_fee_pct": 1.5,
    "block_reward_btc": 3.125
  },
  "maintenance": {
    "inspection_cost_usd": 150,
    "minor_repair_usd": 500,
    "major_repair_usd": 2000,
    "technician_hourly_usd": 75,
    "avg_inspection_hours": 1.0,
    "avg_repair_hours": 4.0
  },
  "downtime": {
    "revenue_loss_per_th_hour": null,
    "opportunity_cost_multiplier": 1.2
  },
  "fleet_constraints": {
    "max_simultaneous_offline_pct": 20,
    "min_operational_hashrate_pct": 70
  },
  "horizons_hours": [24, 168, 720]
}
```

### Cost Projection Task (`tasks/cost_projection.py`)

For each device, computes the expected cost of each action over each horizon:

| Action | Cost Components |
|--------|----------------|
| **Do nothing** | Projected energy cost + projected revenue - failure risk × (repair cost + downtime revenue loss) |
| **Underclock** | Reduced energy cost + reduced revenue + extended component life |
| **Schedule maintenance** | Maintenance cost + downtime cost + restored efficiency × remaining horizon revenue |
| **Immediate shutdown** | Zero energy + zero revenue + risk mitigation (avoided catastrophic failure) |

**Output** (`cost_projections.json`):

```json
{
  "ASIC-007": {
    "current_hourly_profit_usd": 0.42,
    "projections": {
      "24h": {
        "do_nothing": {"cost": 28.50, "revenue": 8.20, "risk_cost": 45.00, "net": -65.30},
        "underclock_80pct": {"cost": 22.10, "revenue": 6.56, "risk_cost": 12.00, "net": -27.54},
        "schedule_maintenance": {"cost": 12.00, "revenue": 0.00, "risk_cost": 0.00, "net": -162.00},
        "shutdown": {"cost": 0.00, "revenue": 0.00, "risk_cost": 0.00, "net": -10.08}
      },
      "7d": { "..." : "..." },
      "30d": { "..." : "..." }
    },
    "recommended_action": "underclock_80pct",
    "recommended_horizon": "24h",
    "rationale": "Minimizes 24h expected loss; maintenance NPV-positive only at 30d horizon"
  }
}
```

**Fleet-level constraints:**
- Never take > 20% of fleet hashrate offline simultaneously
- Prioritize maintenance scheduling to spread downtime across shifts
- If constraint binds, defer lower-priority maintenance to next window

### DAG After Phase 4

```
[1] ingest_telemetry
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4a] train_anomaly_model ──────┐       (training mode only)
                               │
                          [4b] score_fleet
                               │
                          [5]  analyze_trends
                               │
                          [6]  project_costs      ← NEW
                               │
                          [7]  optimize_fleet      ← now cost-driven
                               │
                          [8]  generate_report     ← cost breakdown added
```

### Workflow Engine Capabilities Used

| Capability | Purpose |
|------------|---------|
| **Continuations** | Cost projections use trend data from continuation chain for failure probability curves |
| **Sessions** | Cost history across a simulation session enables ROI tracking |

### Verification Criteria

- [ ] Cost model JSON is loaded and validated at task start (schema check)
- [ ] Per-device cost projections are computed for all 3 horizons
- [ ] Fleet constraint (max 20% offline) is enforced — verify with scenario where 50% of devices are CRITICAL
- [ ] Optimizer selects cost-minimizing action, not just threshold-based tier
- [ ] Report includes dollar-denominated fleet profit/loss summary
- [ ] A HEALTHY device is underclocked when energy cost exceeds marginal revenue
- [ ] Maintenance scheduling respects fleet constraint even when multiple devices need repair

### Dependencies

- **Phase 3:** Trend projections feed failure probability curves into cost calculations

---

## Phase 5: Predictive Model Evolution

**Goal:** Upgrade the ML model from binary anomaly classification to multi-horizon regression with uncertainty quantification. The model predicts what a device's TE_score will be at t+1h, t+6h, t+24h, and t+7d, with confidence bounds — replacing point estimates with probabilistic forecasts.

### Rationale

The current XGBoost classifier answers "is this device anomalous right now?" (binary, backward-looking). Phases 3-4 use linear extrapolation for forward projection — adequate for monotonic degradation but poor for non-linear patterns (sudden failure modes, periodic oscillations, recovery after intervention). A dedicated multi-horizon regression model trained on accumulated simulation data will produce more accurate and calibrated predictions.

### Architectural Changes

**Modified files:**

| File | Change |
|------|--------|
| `tasks/train_model.py` | Train multi-output regression (TE at 4 horizons) + quantile regression for uncertainty |
| `tasks/score.py` | Output multi-horizon predictions with confidence intervals instead of binary risk |
| `tasks/trend_analysis.py` | Use model predictions instead of linear extrapolation for forward projection |
| `tasks/cost_projection.py` | Use quantile predictions for risk-weighted cost computation |
| `tasks/report.py` | Add prediction fan charts, calibration plots, model comparison dashboard |

### Model Architecture

**Primary model:** Multi-output XGBoost regressor

- **Targets:** `te_score_t+1h`, `te_score_t+6h`, `te_score_t+24h`, `te_score_t+7d`
- **Features:** Same 35-feature set + new temporal features from trend analysis (slopes, regime flags)
- **Training data:** Accumulated from multiple simulation sessions via continuation chain

**Uncertainty quantification:** Quantile regression (separate models for 10th, 50th, 90th percentiles)

```
Prediction output per device:
{
  "te_score_t+1h":  {"p10": 0.82, "p50": 0.88, "p90": 0.91},
  "te_score_t+6h":  {"p10": 0.71, "p50": 0.83, "p90": 0.89},
  "te_score_t+24h": {"p10": 0.55, "p50": 0.74, "p90": 0.85},
  "te_score_t+7d":  {"p10": 0.30, "p50": 0.62, "p90": 0.80}
}
```

### Auto-Retraining Trigger

Model accuracy degrades as fleet conditions evolve. Auto-retrain when:

1. Rolling RMSE on recent predictions exceeds threshold for N consecutive cycles
2. Calibration check: actual outcomes fall outside predicted 80% interval more than 30% of the time
3. New anomaly patterns detected (regime changes in > 20% of fleet)

When triggered, the simulation loop triggers a `training` mode run:

```
[simulation_loop detects accuracy degradation]
  │
  └─→ POST /api/workflows/mdk.fleet_intelligence/trigger
      { mode: "training", session_hash: "...", parameters: { retrain: true } }
      │
      └─→ Full DAG: ingest → features → kpi → TRAIN → score (validation) → report
          Uses accumulated data from continuation chain as expanded training set
```

### Model Versioning

- Artifacts: `anomaly_model_v{N}.joblib` (not overwritten)
- Promotion: new model replaces active model only if validation metrics improve
- Rollback: prior model preserved, can be reverted via `--model-path`

### DAG After Phase 5

No structural DAG change — the same tasks run, but `score_fleet` outputs richer predictions:

```
[1] ingest_telemetry
 │
[2] engineer_features
 │
[3] compute_true_efficiency
 │
[4a] train_anomaly_model ──────┐   ← now trains multi-horizon regression + quantile models
                               │
                          [4b] score_fleet       ← outputs probabilistic multi-horizon predictions
                               │
                          [5]  analyze_trends    ← uses model predictions instead of linear extrapolation
                               │
                          [6]  project_costs     ← risk-weighted by prediction confidence intervals
                               │
                          [7]  optimize_fleet
                               │
                          [8]  generate_report   ← prediction fan charts, calibration plots
```

### Workflow Engine Capabilities Used

| Capability | Purpose |
|------------|---------|
| **Continuations** | Accumulated training data from continuation chain for richer model training |
| **Sessions** | Track model performance across a simulation session; trigger retraining |
| **Dual-mode DAG** | Auto-retraining triggers full pipeline in `training` mode |

### Verification Criteria

- [ ] Multi-horizon predictions are produced for all 4 time horizons
- [ ] Quantile regression produces well-ordered intervals (p10 < p50 < p90)
- [ ] Multi-horizon RMSE beats linear extrapolation by > 15% on held-out test set
- [ ] 80% prediction interval captures actual outcome 75-85% of the time (calibration)
- [ ] Auto-retraining triggers when accuracy degrades (inject scenario with novel anomaly pattern)
- [ ] Model versioning works — new model promoted only if metrics improve
- [ ] Rollback to prior model version works via `--model-path`

### Dependencies

- **Phase 3:** Trend features (slopes, regime flags) are used as model inputs
- **Phase 4:** Cost projection consumes uncertainty bounds from quantile predictions

---

## Phase 6: AI Agent Integration (SafeClaw)

**Goal:** Enable an AI agent (via SafeClaw) to read fleet intelligence reports and propose control actions through the validated execution pipeline — with approval gates, learned policies, rate limiting, and full audit trails.

### Rationale

Phases 1-5 build an autonomous analysis pipeline: continuous simulation, trend detection, cost optimization, probabilistic prediction. But the controller output (`fleet_actions.json`) is a recommendation — nobody acts on it. Phase 6 closes the loop by connecting an AI agent that reads the analysis output, reasons about fleet state, and proposes concrete actions (underclock, schedule maintenance, emergency shutdown) through a validated execution pipeline. The approval gate ensures a human operator remains in the loop for critical decisions, while learned policies allow safe recurring actions to auto-approve over time.

### Architectural Changes

**New files:**

| File | Purpose |
|------|---------|
| `tasks/control_action.py` | Executes validated fleet control actions (underclock, maintenance, shutdown) |

**New catalog entries** (in `connectors/catalog/default.json`):

| Template | Approval | Rate Limit | Description |
|----------|----------|------------|-------------|
| `fleet_status_query` | auto-approve | 200/hr | Read-only fleet health queries |
| `fleet_underclock` | human-confirm | 50/hr | Reduce device clock frequency |
| `fleet_schedule_maintenance` | human-confirm | 20/hr | Schedule device for maintenance window |
| `fleet_emergency_shutdown` | always-human | 5/hr | Immediate device shutdown (policy ceiling — never auto-approves) |

**Modified files:**

| File | Change |
|------|--------|
| `connectors/catalog/default.json` | Add 4 fleet control catalog templates |
| `tasks/report.py` | Add agent action log, approval audit trail, proposal history |

### Agent Interaction Flow

```
┌─────────────────────────────────────────────────────────────┐
│  AI Agent (via SafeClaw meta-tool)                          │
│                                                             │
│  1. Reads fleet intelligence report (HTML or JSON)          │
│  2. Reasons: "Device 7 projected CRITICAL in 6h,            │
│     underclock to 70% minimizes 24h cost"                   │
│  3. Proposes: fleet_underclock(device_id="ASIC-007",        │
│               target_pct=70, reason="thermal trend +        │
│               cost projection")                             │
│                                                             │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  Proposal Pipeline (Validance kernel)                       │
│                                                             │
│  catalog lookup → rate limit check → learned policy →       │
│  approval gate → secret injection → container execution     │
│                                                             │
│  Approval tiers:                                            │
│    fleet_status_query      → auto-approve (read-only)       │
│    fleet_underclock        → learned policy OR human         │
│    fleet_schedule_maint    → learned policy OR human         │
│    fleet_emergency_shutdown → ALWAYS human (policy ceiling)  │
│                                                             │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│  control_action.py (task container)                         │
│                                                             │
│  1. Validate action against fleet constraints               │
│     (max 20% offline, min hashrate threshold)               │
│  2. Simulate action effect on device state                  │
│  3. Execute: write MOS command payload                      │
│  4. Return: projected impact (TE change, cost change,       │
│     capacity change)                                        │
│  5. Write audit record: prediction → proposal → approval    │
│     → execution → outcome                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Learned Policy Progression

The approval gate starts conservative and learns from operator behavior:

1. **Initial state:** All fleet control actions require human confirmation
2. **Pattern recognition:** After an operator approves "underclock ASIC-007 to 70% when risk > 0.8" three times in a row, the learned policy proposes auto-approval for that pattern
3. **Trust escalation:** Operator can promote the pattern to auto-approve
4. **Policy ceiling:** `fleet_emergency_shutdown` never auto-approves regardless of learned policy — this is a hard constraint

### Audit Trail

Every agent action produces a complete audit record:

```
prediction (Phase 5) → trend analysis (Phase 3) → cost projection (Phase 4)
  → agent reasoning → proposal → catalog validation → rate limit check
  → learned policy evaluation → approval decision → execution → outcome
  → feedback (was the prediction correct? did the action help?)
```

### DAG After Phase 6

The analysis DAG is unchanged. Agent actions are separate proposal-pipeline executions triggered by the AI agent, not tasks within the analysis DAG:

```
Analysis DAG (unchanged from Phase 5):
[1] ingest → [2] features → [3] kpi → [4a] train → [4b] score →
[5] trends → [6] costs → [7] optimize → [8] report

Agent Actions (separate execution path):
[Agent reads report] → [Proposes action via SafeClaw] →
[Proposal Pipeline: catalog → rate limit → policy → approval → execute] →
[control_action.py] → [Result returned to agent]
```

### Workflow Engine Capabilities Used

| Capability | Purpose |
|------------|---------|
| **Proposal Pipeline** | Validates, rate-limits, and gates agent-proposed fleet actions |
| **Catalog** | Closed vocabulary of allowed fleet actions (4 templates) |
| **Approval Gate** | Human-in-the-loop for control actions; policy ceiling for emergency shutdown |
| **Learned Policy** | Auto-approve safe recurring patterns after operator establishes trust |
| **Rate Limiting** | Prevent runaway agent actions (e.g., underclocking entire fleet in a loop) |
| **Audit Trail** | Full provenance from prediction through execution to outcome |

### Verification Criteria

- [ ] All 4 catalog templates are valid and loadable
- [ ] `fleet_status_query` auto-approves without human interaction
- [ ] `fleet_underclock` and `fleet_schedule_maintenance` require human approval initially
- [ ] `fleet_emergency_shutdown` always requires human approval (even after learned policy)
- [ ] Rate limiting blocks > 50 underclock proposals per hour
- [ ] Fleet constraint (max 20% offline) is enforced in `control_action.py`
- [ ] Audit trail links prediction → proposal → approval → execution → outcome
- [ ] Agent can read fleet report and propose contextually appropriate actions

### Dependencies

- **Phase 4:** Cost projections inform agent reasoning about which action to propose
- **Phase 5:** Probabilistic predictions give the agent confidence-aware fleet context

---

## Key Design Decisions

### 1. The simulator IS the pipeline input

`simulation_engine.py` produces the same CSV schema as a real MOS telemetry feed. The analysis pipeline cannot distinguish simulated from real data. In production, the simulation engine is swapped for a real MOS data connector — no pipeline code changes.

### 2. Same code path offline and online

The `--speed-factor` flag is the only difference between real-time monitoring and offline batch analysis. There is no separate "batch mode" code path. Offline analysis is just simulation at max speed. This eliminates an entire class of "works in batch but breaks in streaming" bugs.

### 3. The workflow engine owns execution and audit, not intelligence

ML models, trend analysis, cost optimization, and control logic are task scripts inside containers. The workflow engine orchestrates, audits, gates, and provides continuations. The intelligence is portable — it could run outside the workflow engine with a different orchestrator. This separation ensures the ML pipeline is not locked to any specific execution platform.

### 4. SafeClaw is the capstone, not the foundation

Phases 1-5 produce a fully functional predictive fleet optimization system without any AI agent involvement. The analysis pipeline runs autonomously, produces actionable reports, and could drive a rule-based controller indefinitely. Phase 6 adds AI-driven control as an enhancement — the agent proposes actions that the pipeline already recommends, but with natural language reasoning and adaptive approval. If the agent is removed, the system continues to function.

### 5. Naming boundary maintained

All fleet intelligence code uses generic workflow vocabulary: tasks, workflows, sessions, continuations, proposals. No SafeClaw-specific names appear in pipeline code. The integration surface is the catalog templates (Phase 6) and the REST API — both are generic, caller-agnostic interfaces. The AI agent is "a caller" — the pipeline does not know or care that it is an LLM.

---

## Phase Dependency Graph

```
Phase 1: Data Separation
   │      (training corpus + simulation engine)
   │
   ▼
Phase 2: Continuous Simulation Loop
   │      (persistent workers + sessions + continuations)
   │
   ▼
Phase 3: Rolling Window + Trend Analysis       ← highest standalone value
   │      (continuation-chain history + forward projection)
   │
   ▼
Phase 4: Economic Cost Modeling
   │      (cost-driven decisions + fleet constraints)
   │
   ▼
Phase 5: Predictive Model Evolution
   │      (multi-horizon regression + uncertainty + auto-retraining)
   │
   ▼
Phase 6: AI Agent Integration (SafeClaw)       ← capstone
          (catalog + approval + learned policy + audit)
```

Each phase builds on the prior phase's infrastructure. Phases 1-3 deliver the most immediate value and can be deployed independently. Phases 4-5 add economic optimization and ML sophistication. Phase 6 is the capstone that enables AI-driven fleet control.
