# Fleet Intelligence — Code Documentation

**Per-file reference and data contracts for the mining optimization pipeline.**

Wiktor Lisowski | April 2026

---

## 1. Architecture

### 1.1 Pipeline Map

```
                          ┌─────────────────────────┐
                          │  scripts/                │
                          │  generate_training_      │
                          │  corpus.py               │
                          │  (+ physics_engine.py)   │
                          └───────────┬──────────────┘
                                      │ training_telemetry.csv
                                      │ training_metadata.json
                                      ▼
┌─────────────────────────────────────────────────────────────────┐
│  Shared prefix (mdk.pre_processing)                             │
│                                                                 │
│  tasks/ingest.py → tasks/features.py → tasks/kpi.py            │
│  telemetry.parquet  features.parquet    kpi_timeseries.parquet  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
    Training path                    Inference path
    (mdk.train)                      (mdk.score → mdk.analyze)

    tasks/train_model.py             tasks/score.py
    → anomaly_model.joblib           → fleet_risk_scores.json
    → regression_model_v*.joblib             │
    → model_metrics.json             tasks/trend_analysis.py
                                     → trend_analysis.json
                                             │
                                     tasks/optimize.py
                                     → fleet_actions.json
                                             │
                                     tasks/report.py
                                     → report.html
```

Training produces model artifacts from labeled data. Inference uses those artifacts to score new telemetry. Pre-processing (ingest → features → KPI) is identical in both paths — the `continue_from` pattern reuses pre-processing outputs without re-running them.

Each task is a standalone Python script that reads from `/work/` and writes to `/work/`. Tasks know nothing about workflows or each other. Orchestration scripts chain tasks via the Validance REST API.

### 1.2 Script Inventory

**Orchestration**:

| Script | What it chains |
|--------|----------------|
| `scripts/orchestrate_training.py` | generate_corpus → pre_processing → train |
| `scripts/orchestrate_inference.py` | pre_processing → score → analyze |
| `scripts/orchestrate_simulation.py` | generate_batch → [pre_processing → score → analyze] x N cycles |

**Data generation**:

| Script | Role |
|--------|------|
| `scripts/generate_training_corpus.py` | Runs physics simulation, writes CSV + metadata |
| `scripts/physics_engine.py` | Device models, anomaly injection, telemetry emission |

**Pipeline tasks** (run inside containers):

| # | Script | Input | Output |
|---|--------|-------|--------|
| 1 | `tasks/ingest.py` | CSV + metadata JSON | `telemetry.parquet` |
| 2 | `tasks/features.py` | `telemetry.parquet` | `features.parquet` |
| 3 | `tasks/kpi.py` | `features.parquet` | `kpi_timeseries.parquet` |
| 4 | `tasks/train_model.py` | `kpi_timeseries.parquet` | `anomaly_model.joblib`, `model_metrics.json` |
| 5 | `tasks/score.py` | `kpi_timeseries.parquet` + model | `fleet_risk_scores.json` |
| 6 | `tasks/trend_analysis.py` | `kpi_timeseries.parquet` + risk scores | `trend_analysis.json` |
| 7 | `tasks/optimize.py` | risk scores + trends + metadata | `fleet_actions.json` |
| 8 | `tasks/report.py` | all of the above | `report.html` |

**Fleet control tasks** (AI agent tools, `fleet-control` image):

| Script | Purpose |
|--------|---------|
| `tasks/fleet_status.py` | Query fleet health (summary, device detail, tier breakdown, risk ranking) |
| `tasks/control_action.py` | Fleet actions (underclock, schedule maintenance, emergency shutdown) |
| `tasks/pipeline_status.py` | Query Validance API for latest pipeline run refs |

**Workflow definitions**:

| Script | Defines |
|--------|---------|
| `workflows/fleet_intelligence.py` | 7 workflow factories (pre_processing, train, score, analyze, generate_corpus, generate_batch, fleet_simulation) |

### 1.3 Cross-Script Constants

| Constant | Value | Used by |
|----------|-------|---------|
| Sample interval | 5 min | All |
| `SCORING_WINDOW_HOURS` | 24 | score.py |
| `CLASSIFIER_THRESHOLD` | 0.3 | train_model.py, score.py |
| TE thresholds | 0.8 (DEGRADED), 0.6 (CRITICAL) | score.py, trend_analysis.py, optimize.py |
| `CRITICAL_RISK` | 0.9 | optimize.py |
| `WARNING_RISK` | 0.5 | optimize.py |
| `THERMAL_HARD_LIMIT_C` | 80.0 | optimize.py |
| `THERMAL_EMERGENCY_LOW_C` | 10.0 | optimize.py |
| `OVERVOLTAGE_PCT` | 1.10 | optimize.py |
| `T_REF` | 25.0 C | kpi.py |
| `VF_ALPHA` | 0.6 | kpi.py |
| `CUSUM_H` / `CUSUM_K` | 8.0 / 0.5 | trend_analysis.py |
| `TREND_ESCALATION_SLOPE` | -0.005/h | optimize.py |
| `TREND_CRITICAL_SLOPE` | -0.02/h | optimize.py |

### 1.4 Features

75 engineered features across rolling statistics, rates of change, fleet-relative z-scores, interaction terms, hardware diagnostics, and True Efficiency decomposition. Features incorporate up to 7 days of historical data (2016 samples at 5-min intervals), capturing gradual degradation patterns — fan bearing wear, PSU capacitor aging, thermal paste degradation, and dust fouling — that operate on multi-day timescales and would be invisible in shorter windows.

The classifier uses 50 features (stages 1+2). The regressor adds 8 autoregressive temporal features (lags, slopes, volatility) for a total of 58.

See [`feature-catalog.md`](feature-catalog.md) for the complete feature list with computation methods.

### 1.5 Knowledge Corpus & RAG Integration

The AI reasoning agent queries organizational knowledge via `knowledge_query` (a catalog task running in the `rag-tasks` Docker image).

**Knowledge corpus** (`knowledge/` directory — 8 markdown files):

| File | Content |
|------|---------|
| `company-profile.md` | Organization overview, location, capacity |
| `team-roster.md` | Personnel, roles, shifts, availability |
| `hardware-inventory.md` | ASIC models, batches, warranty, rack locations |
| `maintenance-sops.md` | Standard operating procedures |
| `facility-specs.md` | Power, cooling, network infrastructure |
| `financial-overview.md` | Electricity rates, budget, BTC breakeven |
| `vendor-contacts.md` | Suppliers, SLAs, spare parts inventory |
| `safety-procedures.md` | Emergency protocols, escalation matrix |

**Indexing pipeline** (`rag.ingest` workflow — 5 tasks):

```
knowledge_corpus.md → load_documents → chunk_documents → embed_chunks → build_index → build_receipt
```

Output: `index.json` (~44 chunks with embeddings), referenced as `@<ingest_hash>.build_index:result`.

**Query script** ([`modules/rag/tasks/knowledge_query.py`](../modules/rag/tasks/knowledge_query.py)): Reads query from `VALIDANCE_PARAMS`, loads `index.json`, embeds via OpenAI `text-embedding-3-small`, cosine similarity top-K=5, generates answer via `gpt-4.1-mini`.

---

## 2. Per-File Reference

### 2.1 `scripts/physics_engine.py`

**Purpose**: Physics simulation library — device models, anomaly injection, and telemetry emission.

**Inputs**: Scenario JSON (via `load_scenario(path)`)
**Outputs**: In-memory `DeviceState` objects and telemetry dicts

| Function | What it does |
|----------|--------------|
| `load_scenario(path)` | Parses scenario JSON, resolves site/economic parameters |
| `simulate_tick(device, ...)` | Single timestep: CMOS power model (`P = k * V^2 * f + P_static(T)`), thermal model, cooling controller |
| `emit_telemetry_row(device, ...)` | Formats 35-column telemetry dict from device state |

10 hardware models (specs from Bitmain/MicroBT/Canaan datasheets), 10 anomaly types (Arrhenius aging, solder fatigue, dust fouling, capacitor ESR, coolant viscosity, etc.).

### 2.2 `scripts/generate_training_corpus.py`

**Purpose**: Runs physics simulation over scenarios, writes labeled training data.

**Inputs**: `data/scenarios/*.json`
**Outputs**: `training_telemetry.csv`, `training_telemetry.parquet`, `training_metadata.json`, `training_labels.csv`

| Function | What it does |
|----------|--------------|
| `generate_scenario_data(scenario_path, seed, prefix)` | Full simulation for one scenario |
| `write_outputs(rows, metadata, output_dir)` | Writes CSV + Parquet + metadata JSON with SHA-256 hash |

`--all` flag generates from all 5 scenarios. Single scenario via `--scenario path`.

### 2.3 `scripts/orchestrate_training.py`

**Purpose**: Chains generate_corpus → pre_processing → train via Validance REST API.

**Inputs**: `--api-url`, optionally `--telemetry-csv` + `--metadata-json`
**Outputs**: Console output with session hash and model artifact path

| Function | What it does |
|----------|--------------|
| `trigger_workflow(session, api_url, name, params, hash, continue_from)` | POSTs to `/api/workflows/{name}/trigger` |
| `poll_completion(session, api_url, name, hash)` | Polls `/api/workflows/{name}/status` until done |

### 2.4 `scripts/orchestrate_inference.py`

**Purpose**: Chains pre_processing → score → analyze via Validance REST API. Optionally notifies the AI agent via gateway webhook after completion.

**Inputs**: `--api-url`, `--training-hash` (required), optionally `--gateway-url` + `--gateway-token`
**Outputs**: Console output; files available in `/work/`

### 2.5 `workflows/fleet_intelligence.py`

**Purpose**: Defines 7 composable Validance workflows as factory functions.

| Function | Tasks | Timeout |
|----------|-------|---------|
| `create_pre_processing_workflow()` | ingest → features → kpi (3 tasks) | 300/600/600s |
| `create_train_workflow()` | train_anomaly_model (1 task) | 3600s |
| `create_score_workflow()` | score_fleet (1 task) | 300s |
| `create_analyze_workflow()` | trends → optimize → report (3 tasks) | 300/300/600s |
| `create_corpus_workflow()` | generate_training_data (1 task) | 1200s |
| `create_batch_workflow()` | generate_batch (1 task) | 600s |
| `create_simulation_workflow()` | simulation_loop (1 task, Pattern 5a) | 7200s |

All tasks use `autoregistry.azurecr.io/mdk-fleet-intelligence:latest`. Inter-task references use `@task_name:output_key` syntax.

### 2.6 `tasks/ingest.py`

**Purpose**: Validates raw telemetry CSV, enforces schema, deduplicates, converts to Parquet.

**Inputs**: `fleet_telemetry.csv`, `fleet_metadata.json`
**Outputs**: `telemetry.parquet`, `_validance_vars.json`

Single-pass: load CSV → validate schema (23 columns) → warn on nulls → drop duplicate `(timestamp, device_id)` → parse types → sort by `[device_id, timestamp]` → write Parquet.

### 2.7 `tasks/features.py`

**Purpose**: Engineers 75 features from raw telemetry.

**Inputs**: `telemetry.parquet`, `fleet_metadata.json`
**Outputs**: `features.parquet`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `add_rolling_features(group)` | Per-device rolling mean/std at 30m, 1h, 12h, 24h, 7d windows |
| `add_rate_of_change(group)` | First-order diffs + 1h smoothed diffs |
| `add_cross_device_features(df)` | Z-scores within same model per timestamp |
| `add_interaction_features(df)` | power_per_ghz, thermal_headroom_c, cooling_effectiveness, etc. |

Window sizes in samples: 6 (30m), 12 (1h), 144 (12h), 288 (24h), 2016 (7d). See [`feature-catalog.md`](feature-catalog.md) for the full list.

### 2.8 `tasks/kpi.py`

**Purpose**: Computes True Efficiency with diagnostic decomposition and per-device health scores.

**Inputs**: `features.parquet`, `fleet_metadata.json`
**Outputs**: `kpi_timeseries.parquet`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `compute_voltage_efficiency(df)` | `eta_v = (V_optimal / V_actual)^2` where `V_optimal = V_stock * (f/f_stock)^0.6` |
| `compute_cooling_normalized(df)` | `P_cool_norm = P_cool * (T_chip - 25) / max(T_chip - T_ambient, 1)` |
| `compute_te_nominal(meta)` | TE at stock settings per device: `(P + 0.10*P) / H` |

Idle samples (hashrate <= 0) get NaN for all KPI columns.

### 2.9 `tasks/train_model.py`

**Purpose**: Trains XGBoost binary classifier (anomaly detection) and 12 quantile regressors (multi-horizon TE_score prediction).

**Inputs**: `kpi_timeseries.parquet`, `fleet_metadata.json`
**Outputs**: `anomaly_model.joblib`, `regression_model_v{N}.joblib`, `model_registry.json`, `model_metrics.json`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `train_classifier(X, y, name)` | XGBoost with `scale_pos_weight = n_neg/n_pos` |
| `add_temporal_features(df)` | 8 autoregressive features: lags (1h, 6h, 24h), slopes (1h, 6h, 24h, 7d), volatility (24h) |
| `train_all_regressors(X, targets, names)` | 4 horizons x 3 quantiles = 12 XGBRegressor models |

Classifier: `n_estimators=200, max_depth=6, learning_rate=0.1, threshold=0.3`.
Regression: `objective='reg:quantileerror'` (pinball loss). Horizons: 1h, 6h, 24h, 7d. Quantiles: p10, p50, p90.

### 2.10 `tasks/score.py`

**Purpose**: Scores latest 24h window with pre-trained classifier and optionally regression model.

**Inputs**: `kpi_timeseries.parquet`, `anomaly_model.joblib`, `fleet_metadata.json`, optionally `regression_model_v{N}.joblib`
**Outputs**: `fleet_risk_scores.json`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `predict_horizons(last_row, artifact)` | Predicts TE_score at 4 horizons x 3 quantiles |
| `compute_predicted_crossings(predictions)` | Finds first horizon where p50 drops below 0.8 or 0.6 |
| `main()` | Selects 24h window, scores classifier, aggregates per-device |

The scoring window covers the last 24h, but the full history is used to compute features — rolling windows require up to 7 days of prior data for warm-up, and temporal lags look back 24h from the scoring boundary. Devices sorted by `mean_risk` descending.

### 2.11 `tasks/trend_analysis.py`

**Purpose**: Per-device trend vectors, CUSUM regime detection, and threshold crossing projections.

**Inputs**: `kpi_timeseries.parquet`, `fleet_risk_scores.json` (optional)
**Outputs**: `trend_analysis.json`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `compute_linear_trend(values)` | OLS slope + R^2 via polyfit |
| `detect_regime_change_cusum(values, h, k)` | Two-sided CUSUM (Page 1954). Reference period = first 25% of history |
| `project_threshold_crossing(current, slope, r2, threshold)` | Linear extrapolation to 0.8 and 0.6 thresholds |
| `classify_direction(slope_per_hour)` | falling_fast / declining / stable / recovering / recovering_fast |

CUSUM parameters: h=8.0, k=0.5. Direction thresholds: falling_fast < -0.02/h, declining < -0.005/h, stable within +/-0.005/h.

### 2.12 `tasks/optimize.py`

**Purpose**: Deterministic controller — safety overrides, tier classification, and MOS-mapped command generation.

**Inputs**: `fleet_risk_scores.json`, `kpi_timeseries.parquet`, `fleet_metadata.json`, optionally `trend_analysis.json`
**Outputs**: `fleet_actions.json`, `_validance_vars.json`

| Function | What it does |
|----------|--------------|
| `apply_safety_overrides(risk, stock)` | 4 hard safety constraints, always runs before tier logic |
| `classify_tier(risk, trend)` | Maps risk/TE_score to CRITICAL/WARNING/DEGRADED/HEALTHY |
| `apply_fleet_redundancy(actions)` | Defers lowest-risk device if all same-model devices are flagged |
| `annotate_mos_methods(actions)` | Maps command types to MOS RPC names |

Safety override order: thermal hard limit (80 C) → thermal emergency low (10 C) → thermal low warning (20 C) → overvoltage (110% stock). Trend escalation is one-directional (no de-escalation).

### 2.13 `tasks/report.py`

**Purpose**: Generates self-contained HTML dashboard with embedded base64 PNG charts.

**Inputs**: `kpi_timeseries.parquet`, `fleet_risk_scores.json`, `fleet_actions.json`, `fleet_metadata.json`, optionally `trend_analysis.json`, `model_metrics.json`
**Outputs**: `report.html`

| Function | Chart type |
|----------|-----------|
| `plot_te_timeseries(df)` | Line chart per device (hourly) |
| `plot_te_decomposition(df)` | Grouped bar (TE_base + voltage + cooling) |
| `plot_health_scores(df)` | Heatmap (device x date), RdYlGn |
| `plot_anomaly_timeline(df)` | Stacked area per anomaly type |
| `plot_risk_ranking(risk_scores)` | Horizontal bar, sorted by mean_risk |
| `plot_controller_tiers(actions)` | Bar + pie (tier distribution) |
| `plot_model_metrics(metrics)` | Feature importance (top 15) |

Charts rendered with matplotlib Agg backend, DPI=120, embedded as base64 PNG. Optional sections included only if their input files exist.

### 2.14 `tasks/fleet_status.py`

**Purpose**: Query fleet health data for the AI agent. Supports 4 query types: `summary`, `device_detail`, `tier_breakdown`, `risk_ranking`.

**Inputs**: `fleet_risk_scores.json`, `fleet_metadata.json` (via `/work/fleet/` mount from `input_files`)
**Outputs**: JSON to stdout

### 2.15 `tasks/control_action.py`

**Purpose**: Fleet control actions invoked by the AI agent through the governance API.

**Actions**: `underclock` (set clock % of stock), `maintenance` (schedule inspection/repair), `shutdown` (emergency power-off). Reads fleet data from `/work/fleet/` to validate constraints (fleet capacity, device existence).

### 2.16 `tasks/pipeline_status.py`

**Purpose**: Query the Validance REST API for the latest `mdk.score` pipeline run. Returns `session_hash`, `input_files` refs, and cycle info.

**API flow**:
1. `GET /api/runs?workflow_name=mdk.score&status=SUCCESS&limit=1` → latest score run
2. `GET /api/variables/{score_hash}` → risk_scores file ref
3. `parameters.continued_from` → pre_processing hash → metadata file ref

No workspace mount needed. Uses stdlib `urllib.request`.

---

## 3. Data Contracts

Schemas for all inter-task files.

### 3.1 `telemetry.parquet`

**Producer**: `tasks/ingest.py` | **Consumer**: `tasks/features.py`

| Column | Type | Notes |
|--------|------|-------|
| `timestamp` | datetime64[ns] | Sorted by (device_id, timestamp) |
| `device_id` | string | Unique miner identifier |
| `model` | string | Hardware model name |
| `clock_ghz` | float64 | Actual clock frequency |
| `voltage_v` | float64 | Supply voltage |
| `hashrate_th` | float64 | Hash rate (TH/s) |
| `power_w` | float64 | Power consumption (W) |
| `temperature_c` | float64 | Chip temperature (C) |
| `cooling_power_w` | float64 | Cooling system power (W) |
| `ambient_temp_c` | float64 | Ambient temperature (C) |
| `energy_price_kwh` | float64 | Spot energy price ($/kWh) |
| `operating_mode` | string | normal, overclock, sleep, etc. |
| `efficiency_jth` | float64 | Instantaneous J/TH |
| `fan_rpm` | float64 | Fan speed |
| `fan_rpm_target` | float64 | Target fan speed |
| `dust_index` | float64 | Dust accumulation factor |
| `inlet_temp_c` | float64 | Inlet air temperature |
| `voltage_ripple_mv` | float64 | Voltage ripple (mV) |
| `error_code` | string | MOS error code |
| `reboot_count` | int64 | Cumulative reboots |
| `chip_count_active` | int64 | Active ASIC chips |
| `hashboard_count_active` | int64 | Active hashboards |
| `operational_state` | string | RUNNING, CURTAILED, MAINTENANCE, FAILED |
| `economic_margin_usd` | float64 | Hourly profit margin ($) |
| `label_*` | int64 | Binary anomaly labels (10 types + `label_any_anomaly`) |

**Invariants**: No duplicate (timestamp, device_id). Sorted by (device_id, timestamp).

### 3.2 `features.parquet`

**Producer**: `tasks/features.py` | **Consumer**: `tasks/kpi.py`

All columns from `telemetry.parquet` plus ~55 engineered features. See [`feature-catalog.md`](feature-catalog.md) for the complete schema.

### 3.3 `kpi_timeseries.parquet`

**Producer**: `tasks/kpi.py` | **Consumers**: train_model, score, trend_analysis, optimize, report

All columns from `features.parquet`, plus:

| Column | Type | Formula |
|--------|------|---------|
| `eta_v` | float64 | `(V_optimal / V_actual)^2`, clipped [0, 2] |
| `p_cooling_norm` | float64 | `P_cool * (T_chip - 25) / max(T_chip - T_amb, 1)` |
| `te_base` | float64 | `power_w / hashrate_th` |
| `voltage_penalty` | float64 | `1.0 / eta_v` |
| `cooling_ratio` | float64 | `(power_w + p_cooling_norm) / power_w` |
| `true_efficiency` | float64 | `(power_w + p_cooling_norm) / (hashrate_th * eta_v)` |
| `te_nominal` | float64 | `(P + 0.10*P) / H` at stock |
| `te_score` | float64 | `te_nominal / true_efficiency` (1.0 = nominal) |

KPI columns are NaN for idle samples (hashrate_th <= 0).

### 3.4 `fleet_risk_scores.json`

**Producer**: `tasks/score.py` | **Consumers**: trend_analysis, optimize, report

```json
{
  "scoring_window_hours": 24,
  "window_start": "ISO timestamp",
  "window_end": "ISO timestamp",
  "samples_scored": 288,
  "threshold": 0.3,
  "model_versions": {
    "classifier": "anomaly_model.joblib",
    "regressor_version": 1
  },
  "device_risks": [
    {
      "device_id": "ASIC-001",
      "model": "S21XP",
      "mean_risk": 0.05,
      "max_risk": 0.12,
      "pct_flagged": 0.0,
      "last_risk": 0.03,
      "flagged": false,
      "latest_snapshot": {
        "timestamp": "...", "te_score": 0.98, "true_efficiency": 15.8,
        "temperature_c": 52.0, "voltage_v": 0.38, "hashrate_th": 270.0,
        "power_w": 4200.0, "cooling_power_w": 450.0, "ambient_temp_c": 20.0,
        "operating_mode": "normal"
      },
      "predictions": {
        "te_score_1h": {"p10": 0.95, "p50": 0.97, "p90": 0.99},
        "te_score_6h": {"p10": 0.93, "p50": 0.96, "p90": 0.99},
        "te_score_24h": {"p10": 0.90, "p50": 0.95, "p90": 0.98},
        "te_score_7d": {"p10": 0.85, "p50": 0.93, "p90": 0.97}
      },
      "predicted_crossings": {
        "te_0.8": null,
        "te_0.6": null
      }
    }
  ]
}
```

`predictions` and `predicted_crossings` are present only if a regression model is available.

### 3.5 `trend_analysis.json`

**Producer**: `tasks/trend_analysis.py` | **Consumers**: optimize, report

```json
{
  "analysis_version": "3.0-trend",
  "sample_interval_minutes": 5,
  "windows": {"1h": 12, "6h": 72, "24h": 288, "7d": 2016},
  "cusum_params": {"h": 8.0, "k": 0.5},
  "devices": [
    {
      "device_id": "ASIC-001",
      "current_state": {"te_score": 0.98, "temperature_c": 52.0, "mean_risk": 0.05},
      "te_trends": {
        "1h": {"slope_per_hour": -0.001, "r_squared": 0.85, "direction": "stable", "n_samples": 12},
        "6h": {"slope_per_hour": -0.002, "r_squared": 0.72, "direction": "stable", "n_samples": 72},
        "24h": {"slope_per_hour": -0.003, "r_squared": 0.65, "direction": "stable", "n_samples": 288},
        "7d": {"slope_per_hour": -0.001, "r_squared": 0.55, "direction": "stable", "n_samples": 2016}
      },
      "temp_trends": {"6h": {"slope_per_hour": 0.1, "r_squared": 0.3, "last_ewma": 52.0, "n_samples": 72}},
      "risk_trends": {"1h": {"slope_per_hour": 0.0, "r_squared": 0.1, "direction": "stable", "n_samples": 12}},
      "regime": {"change_detected": false, "change_index": null, "direction": "stable"},
      "projections": {
        "0.8": {"hours_to_crossing": null, "confidence": 0.65, "will_cross": false},
        "0.6": {"hours_to_crossing": null, "confidence": 0.65, "will_cross": false}
      },
      "primary_direction": "stable",
      "primary_slope_per_hour": -0.003,
      "primary_r_squared": 0.65
    }
  ],
  "fleet_summary": {"device_count": 10, "regime_changes": 0, "direction_distribution": {"stable": 10}}
}
```

### 3.6 `fleet_actions.json`

**Producer**: `tasks/optimize.py` | **Consumers**: report, AI agent

```json
{
  "controller_version": "2.0-tier-only",
  "scoring_window": {"start": "...", "end": "..."},
  "tier_counts": {"CRITICAL": 0, "WARNING": 2, "DEGRADED": 1, "HEALTHY": 7},
  "safety_constraints_applied": [],
  "actions": [
    {
      "device_id": "ASIC-009",
      "model": "S19XP",
      "tier": "WARNING",
      "risk_score": 0.55,
      "te_score": 0.78,
      "commands": [
        {"type": "set_clock", "value_ghz": 1.33, "priority": "HIGH", "mos_method": "setFrequency"},
        {"type": "schedule_inspection", "urgency": "next_window", "priority": "MEDIUM", "mos_method": null}
      ],
      "rationale": ["TE score 0.78 below DEGRADED threshold (0.80)", "Underclocking to reduce thermal stress"],
      "trend_context": {"direction": "declining", "slope_per_hour": -0.008, "r_squared": 0.72, "regime_change": false},
      "mos_alert_codes": ["P:1", "R:1"]
    }
  ]
}
```

**Command type → MOS method mapping**:

| type | mos_method | Notes |
|------|-----------|-------|
| `set_clock` | `setFrequency` | V/f coupled — voltage adjusts implicitly |
| `set_power_mode` | `setPowerMode` | normal or sleep |
| `set_fan_mode` | `setFanControl` | Air-cooled only |
| `reboot` | `reboot` | |
| `schedule_inspection` | null | Operational, no RPC |
| `set_monitoring_interval` | null | Internal pipeline config |
| `hold_settings` | null | No-op |
| `suggest_overclock` | `setFrequency` | Advisory only |

### 3.7 `model_metrics.json`

**Producer**: `tasks/train_model.py` | **Consumer**: `tasks/report.py`

```json
{
  "model": "XGBClassifier",
  "train_samples": 1500000,
  "anomaly_rate": 0.41,
  "devices": 57,
  "feature_count": 50,
  "threshold": 0.3,
  "top_features": [{"feature": "te_score", "importance": 0.15}],
  "per_anomaly_type": {
    "thermal_deg": {"train_positives": 50000, "positive_rate": 0.03, "devices_affected": 8, "top_features": []}
  },
  "regression": {
    "model_type": "multi_horizon_quantile_regression",
    "version": 1,
    "horizons": ["1h", "6h", "24h", "7d"],
    "quantiles": [0.1, 0.5, 0.9],
    "feature_count": 58
  }
}
```

### 3.8 `anomaly_model.joblib`

**Producer**: `tasks/train_model.py` | **Consumer**: `tasks/score.py`

```python
{
    "model": XGBClassifier,            # Trained binary classifier
    "feature_names": [str],            # 50 feature names
    "threshold": float                 # 0.3 default
}
```

Usage: `proba = model.predict_proba(X[feature_names])[:, 1]`, then `flagged = proba > threshold`.

### 3.9 `regression_model_v{N}.joblib`

**Producer**: `tasks/train_model.py` | **Consumer**: `tasks/score.py`

```python
{
    "model_type": "multi_horizon_quantile_regression",
    "version": int,
    "horizons": ["1h", "6h", "24h", "7d"],
    "quantiles": [0.1, 0.5, 0.9],
    "feature_names": [str],            # 50 base + 8 temporal = 58
    "models": {"1h": {"p10": XGBRegressor, "p50": XGBRegressor, "p90": XGBRegressor}, ...},
    "trained_at": str
}
```

### 3.10 `fleet_metadata.json` (input)

**Producer**: external | **Consumers**: all tasks

```json
{
  "fleet": [
    {
      "device_id": "ASIC-001",
      "model": "S21XP",
      "stock_clock_ghz": 1.40,
      "stock_voltage_v": 0.38,
      "nominal_hashrate_th": 270.0,
      "nominal_power_w": 4200.0,
      "nominal_efficiency_jth": 15.5
    }
  ]
}
```

### 3.11 `_validance_vars.json`

**Producer**: every task | **Consumer**: Validance kernel

Task-specific output variables for workflow variable propagation. Not consumed by other tasks.

| Task | Variables |
|------|-----------|
| ingest | `row_count`, `device_count`, `time_span_days` |
| features | `feature_count`, `sample_count` |
| kpi | `mean_te`, `worst_device`, `worst_te_score` |
| train_model | `train_samples`, `anomaly_rate`, `model_version` |
| score | `flagged_devices`, `scoring_window_hours` |
| trend_analysis | `devices_with_regime_change` |
| optimize | `actions_issued`, `devices_underclocked`, `devices_inspected` |
