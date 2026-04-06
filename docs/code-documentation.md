# Fleet Intelligence — Code Documentation

**Per-file reference and data contracts for the mining optimization pipeline.**

Wiktor Lisowski | April 2026

Last edited: 2026-04-06

### Change Log

| Date | Change |
|------|--------|
| 2026-04-06 | Step 2 features: 7-day rolling windows (5), `voltage_ripple_std_24h`, `chip_dropout_ratio`, `te_score_slope_24h`, `te_score_slope_7d` — updated §1.4, §2.7, §2.9, §2.10, §3.8, §3.9 feature counts (43→50 classifier, 6→8 temporal, 49→58 regressor) |
| 2026-04-06 | Added 6 hardware health sensor features (`fan_rpm`, `voltage_ripple_mv`, `reboot_count`, `chip_count_active`, `hashboard_count_active`, `dust_index`) — updated §1.4, §2.10, §3.8, §3.9 feature counts (37→43 classifier, 43→49 regressor) |
| 2026-04-05 | Added §1.4 feature computation timeline, expanded §2.10 (score.py) with feature justifications, known gaps, scoring window rationale |
| 2026-04-05 | Initial version |

---

## 1. Architecture

### 1.1 Pipeline Map

The pipeline has two paths that share a common prefix:

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

**Why two paths**: Training produces model artifacts from labeled data. Inference uses those artifacts to score new telemetry. Pre-processing (ingest → features → KPI) is identical in both paths — the `continue_from` pattern lets inference reuse pre-processing outputs without re-running them.

**Why orchestration is separate from task logic**: Each task is a standalone Python script that reads files from `/work/` and writes files to `/work/`. Tasks know nothing about Validance, workflows, or each other. The orchestration scripts (`orchestrate_training.py`, `orchestrate_inference.py`) chain tasks via Validance's REST API using `continue_from` hashes. This means tasks can be run manually for debugging, and the orchestration layer can be replaced without touching task code.

### 1.2 Script Inventory

**Orchestration** (invoke these):

| Script | What it chains | Timing |
|--------|----------------|--------|
| `scripts/orchestrate_training.py` | generate_corpus → pre_processing → train | ~10 min |
| `scripts/orchestrate_inference.py` | pre_processing → score → analyze | ~7 min |

**Data generation**:

| Script | Role |
|--------|------|
| `scripts/generate_training_corpus.py` | Runs physics simulation, writes CSV + metadata |
| `scripts/physics_engine.py` | Library — device models, anomaly injection, telemetry emission |

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

**Workflow definitions**:

| Script | Defines |
|--------|---------|
| `workflows/fleet_intelligence.py` | 5 workflow factories (pre_processing, train, score, analyze, generate_corpus) |

### 1.3 Cross-Script Constants

Values shared across multiple scripts. Change one, check the others.

| Constant | Value | Used by |
|----------|-------|---------|
| Sample interval | 5 min | All (implicit in window sizes) |
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

---

## 2. Per-File Reference

<a id="physics-engine"></a>
### 2.1 `scripts/physics_engine.py`

**Purpose**: Physics simulation library — device models, anomaly injection, and telemetry emission for synthetic data generation.

**Inputs**: Scenario JSON (via `load_scenario(path)`)
**Outputs**: In-memory `DeviceState` objects and telemetry dicts (consumed by generate_training_corpus.py)

**Key functions**:

| Function | What it does |
|----------|--------------|
| `load_scenario(path)` | Parses scenario JSON, resolves site/economic parameters |
| `simulate_tick(device, ...)` | Single physics timestep: applies load, anomalies, state transitions. Contains the CMOS power model (`P = k * V^2 * f + P_static(T)`), thermal model (exponential approach with inertia tau=0.4h), and cooling controller |
| `emit_telemetry_row(device, ...)` | Formats 35-column telemetry dict from device state |

**Non-obvious decisions**:
- 10 hardware models with specs from Bitmain/MicroBT/Canaan datasheets. Stock clock/voltage/hashrate/power are real values.
- 10 anomaly types implemented via physics: Arrhenius aging (thermal), solder fatigue (thermal cycling), dust fouling (% blockage/day), capacitor ESR increase, coolant viscosity.
- Operating modes (normal/overclock/underclock/idle) are V/f coupled — changing clock implicitly changes voltage, matching ASIC firmware behavior.
- Noise: 0.5% Gaussian on hashrate, 1 mV on voltage ripple, 20 RPM on fan speed. Power has no additive sensor noise (deterministic CMOS model). See DR-CAL-08.

<a id="generate-corpus"></a>
### 2.2 `scripts/generate_training_corpus.py`

**Purpose**: Runs physics simulation over configurable scenarios, writes labeled training data.

**Inputs**: `data/scenarios/*.json`
**Outputs**: `training_telemetry.csv`, `training_telemetry.parquet`, `training_metadata.json`, `training_labels.csv`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `generate_scenario_data(scenario_path, seed, prefix)` | Runs full simulation for one scenario, returns (rows, metadata) |
| `write_outputs(rows, metadata, output_dir)` | Writes CSV + Parquet + metadata JSON, computes SHA-256 hash |

**Non-obvious decisions**:
- Seed hierarchy: CLI `--seed` > scenario JSON seed > default (42). Multi-scenario runs prefix device_id with scenario name to avoid collisions.
- `--all` flag generates from all 5 scenarios in `data/scenarios/`. Single scenario via `--scenario path`.

<a id="orchestrate-training"></a>
### 2.3 `scripts/orchestrate_training.py`

**Purpose**: Chains generate_corpus → pre_processing → train via Validance REST API.

**Inputs**: `--api-url`, optionally `--telemetry-csv` + `--metadata-json` (to skip corpus generation)
**Outputs**: Console output with session hash and model artifact path

**Key functions**:

| Function | What it does |
|----------|--------------|
| `trigger_workflow(session, api_url, name, params, hash, continue_from)` | POSTs to `/api/workflows/{name}/trigger`, returns workflow_hash |
| `poll_completion(session, api_url, name, hash)` | Polls `/api/workflows/{name}/status` until done or timeout |

**Non-obvious decisions**:
- Each step passes `continue_from=prev_hash` so Validance chains outputs from the previous workflow into the next.
- Session hash is SHA-256 of `"training_{timestamp}"` — links all workflows in a single audit trail.
- `POLL_TIMEOUT = 3600s` (1 hour) because full corpus training can take ~20 min.

<a id="orchestrate-inference"></a>
### 2.4 `scripts/orchestrate_inference.py`

**Purpose**: Chains pre_processing → score → analyze via Validance REST API.

**Inputs**: `--api-url`, `--telemetry-csv`, `--metadata-json`, `--model-path` (all required), optionally `--regression-model-path`, `--model-metrics-path`
**Outputs**: Console output; files available in `/work/`

**Key functions**: Same as orchestrate_training (`trigger_workflow`, `poll_completion`).

**Non-obvious decisions**:
- If no regression model path provided, scoring runs in classifier-only mode (no multi-horizon predictions).
- `POLL_TIMEOUT = 1800s` (30 min) — inference is faster than training.

<a id="fleet-intelligence"></a>
### 2.5 `workflows/fleet_intelligence.py`

**Purpose**: Defines 5 composable Validance workflows as factory functions.

**Key functions**:

| Function | Tasks | Timeout |
|----------|-------|---------|
| `create_pre_processing_workflow()` | ingest → features → kpi (3 tasks) | 300/600/600s |
| `create_train_workflow()` | train_anomaly_model (1 task) | 3600s |
| `create_score_workflow()` | score_fleet (1 task) | 300s |
| `create_analyze_workflow()` | trends → optimize → report (3 tasks) | 300/300/600s |
| `create_corpus_workflow()` | generate_training_data (1 task) | 1200s |

**Non-obvious decisions**:
- All tasks use `autoregistry.azurecr.io/mdk-fleet-intelligence:latest` image.
- Inter-task references use `@task_name:output_key` syntax. Workflow parameters use `${param_name}`.
- `WORKFLOWS` dict maps shortnames to factories — used by `register_validance_workflows.py` for discovery.
- Tasks run at `/app/tasks/` and `/app/scripts/` inside the container.

### 1.4 Feature Computation Timeline

Where each feature group is born and consumed across pipeline stages:

**Stage 1 — `features.py`** (per-device rolling windows, derivatives, fleet context)

| Group | Features | Window / method | In model? |
|-------|----------|-----------------|-----------|
| Rolling means | `{col}_mean_1h`, `_mean_12h`, `_mean_24h` | 12 / 144 / 288 samples | 1h, 24h yes; 12h **no** |
| Rolling std | `{col}_std_1h`, `_std_24h` | 12 / 288 samples | yes |
| Rolling deviation | `{col}_dev_24h` | z-score vs 24h mean | yes |
| Short hashrate | `hashrate_th_mean_30m` | 6 samples | **no** (future) |
| Rate of change (raw) | `d_{col}` | `.diff()` | **no** |
| Rate of change (smooth) | `d_{col}_smooth` | 12-sample rolling mean of diffs | yes |
| Fleet-relative | `{col}_fleet_z` | per-model z-score per timestamp | yes |
| Interaction | `power_per_ghz`, `thermal_headroom_c`, `cooling_effectiveness`, `hashrate_ratio`, `voltage_deviation` | point-in-time ratios | yes |
| Site conditions | `ambient_temp_c`, `energy_price_kwh` | passthrough | yes (but `energy_price` is **questionable** — see [§2.10 Gaps](#score)) |
| Hardware health | `fan_rpm`, `voltage_ripple_mv`, `reboot_count`, `chip_count_active`, `hashboard_count_active`, `dust_index` | raw telemetry passthrough | yes |
| 7-day rolling windows | `{col}_mean_7d`, `{col}_dev_7d` (temp, power, hashrate, efficiency) + `{col}_std_7d` (all 6 cols) | 2016 samples (7d) | mean_7d + dev_7d for 4 cols = yes (5 features); std_7d = intermediate |
| Hardware diagnostics | `voltage_ripple_std_24h`, `chip_dropout_ratio` | 288-sample rolling std; active/nominal ratio | yes |

`{col}` = `temperature_c`, `power_w`, `hashrate_th`, `voltage_v`, `cooling_power_w`, `efficiency_jth`

**Stage 2 — `kpi.py`** (True Efficiency decomposition)

| Feature | Formula | Notes |
|---------|---------|-------|
| `te_base` | P / H | Naive J/TH |
| `eta_v` | (V_opt / V_actual)² | Voltage efficiency, clipped [0, 2] |
| `voltage_penalty` | 1 / eta_v | Multiplier for voltage waste |
| `cooling_ratio` | (P + P_cool_norm) / P | Cooling overhead factor |
| `true_efficiency` | (P + P_cool_norm) / (H * eta_v) | Corrected J/TH |
| `te_score` | TE_nominal / true_efficiency | Health score: 1.0 = nominal, <1.0 = degrading |

**Stage 3 — `train_model.py` / `score.py`** (regressor-only temporal features)

| Feature | Method | Window |
|---------|--------|--------|
| `te_score_lag_1h` | `shift(12)` | 1h lookback |
| `te_score_lag_6h` | `shift(72)` | 6h lookback |
| `te_score_lag_24h` | `shift(288)` | 24h lookback |
| `te_score_slope_1h` | `polyfit(12)` | 1h linear trend |
| `te_score_slope_6h` | `polyfit(72)` | 6h linear trend |
| `te_score_volatility_24h` | `std(288)` | 24h rolling std |
| `te_score_slope_24h` | `polyfit(288)` | 24h linear trend |
| `te_score_slope_7d` | `polyfit(2016)` | 7d linear trend |

**Model consumption**: Classifier uses stages 1+2 (50 features). Regressor uses stages 1+2+3 (50 + 8 = 58 features).

#### History dependency at inference

How far back each model path looks, shown as a timeline from oldest data to present:

```
    oldest data                                                          now
    |                                                                     |
    |·····················  N days of history  ····························|
    |                                                                     |
    |  features.py: rolling windows need >=7d (2016 samples) of history   |
    |  kpi.py:      TE formula applied to every row                       |
    |                                                                     |
    |                                          |·· last 24h ··|          |
    |                                          |  Classifier   |          |
    |                                          |  scores each  |          |
    |                                          |  row in this  |          |
    |                                          |  window       |          |
    |                                                                     |
    |  Regressor: lags/slopes computed from full history ──────────> predict
    |             (needs 24h+ before scoring window)        last row only  |
```

| Model | History needed | Scores | Output |
|-------|---------------|--------|--------|
| Classifier | >=7d before window (for 7d rolling features to warm up) | Every row in last 24h | `mean_risk`, `max_risk`, `pct_flagged` per device |
| Regressor | Full history (lags up to 24h back from last row) | Last row only | TE_score at 1h/6h/24h/7d with p10/p50/p90 |

<a id="ingest"></a>
### 2.6 `tasks/ingest.py`

**Purpose**: Validates raw telemetry CSV, enforces schema, deduplicates, converts to Parquet.

**Inputs**: `fleet_telemetry.csv`, `fleet_metadata.json`
**Outputs**: `telemetry.parquet`, `_validance_vars.json`

**Key function**: `main()` — single-pass: load CSV, validate schema (23 expected columns), warn on nulls, drop duplicate `(timestamp, device_id)`, parse types, sort by `[device_id, timestamp]`, write Parquet.

**Non-obvious decisions**:
- Warns on nulls but does not fail — proceeds with NaN. Quality issues are surfaced, not blocked.
- Label columns (`label_thermal_deg`, etc.) are cast to int explicitly.

<a id="features"></a>
### 2.7 `tasks/features.py`

**Purpose**: Engineers ~55 features from raw telemetry: rolling stats, rates of change, fleet-relative z-scores, interaction terms.

**Inputs**: `telemetry.parquet`, `fleet_metadata.json`
**Outputs**: `features.parquet`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `add_rolling_features(group)` | Per-device rolling mean/std at 30m, 1h, 12h, 24h, 7d windows + `voltage_ripple_std_24h` |
| `add_rate_of_change(group)` | First-order diffs + 1h smoothed diffs for temp, power, hashrate, voltage |
| `add_cross_device_features(df)` | Z-scores within same model per timestamp |
| `add_interaction_features(df)` | power_per_ghz, thermal_headroom_c, cooling_effectiveness, hashrate_ratio, voltage_deviation, chip_dropout_ratio |

**Non-obvious decisions**:
- Window sizes in samples (not time): 6 (30m), 12 (1h), 144 (12h), 288 (24h), 2016 (7d) at 5-min intervals.
- 30m hashrate window (`hashrate_th_mean_30m`) approximates MOS's native `hashrate_30m` field. Computed but not yet in the classifier feature set.
- `thermal_headroom_c = 85.0 - temperature_c` uses 85 C (not the 80 C hard limit) — measures distance to junction temp ceiling, not the PCB trigger point.
- Cross-device z-scores are computed per-model (same hardware) per-timestamp. A device that's hot relative to its peers shows up even if the absolute temp is fine.

<a id="kpi"></a>
### 2.8 `tasks/kpi.py`

**Purpose**: Computes True Efficiency with diagnostic decomposition and per-device health scores.

**Inputs**: `features.parquet`, `fleet_metadata.json`
**Outputs**: `kpi_timeseries.parquet`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `compute_voltage_efficiency(df)` | `eta_v = (V_optimal / V_actual)^2` where `V_optimal = V_stock * (f/f_stock)^0.6` |
| `compute_cooling_normalized(df)` | `P_cool_norm = P_cool * (T_chip - 25) / max(T_chip - T_ambient, 1)` |
| `compute_te_nominal(meta)` | TE at stock settings per device: `(P + 0.10*P) / H` |

**Non-obvious decisions**:
- VF_ALPHA = 0.6: sub-linear V/f exponent for 7-14nm CMOS. Not 1.0 (linear) and not 0.5 (square-root).
- T_REF = 25 C: industry-standard reference ambient. Cooling cost normalized to this removes geographic bias.
- THERMAL_FLOOR = 1.0 C: prevents division by zero when `T_chip ≈ T_ambient`.
- eta_v clipped to [0, 2]: prevents pathological values from corrupting downstream.
- Cooling estimate for TE_nominal = 10% of ASIC power. Empirical for hydro-cooled 3-5 kW ASICs in northern climates.
- Idle samples (hashrate <= 0) get NaN for all KPI columns — TE is undefined when not hashing.

<a id="train-model"></a>
### 2.9 `tasks/train_model.py`

**Purpose**: Trains XGBoost binary classifier (anomaly detection) and 12 quantile regressors (multi-horizon TE_score prediction).

**Inputs**: `kpi_timeseries.parquet`, `fleet_metadata.json`
**Outputs**: `anomaly_model.joblib`, `regression_model_v{N}.joblib`, `model_registry.json`, `model_metrics.json`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `train_classifier(X, y, name)` | XGBoost with `scale_pos_weight = n_neg/n_pos`, no internal CV |
| `add_temporal_features(df)` | Adds 8 autoregressive features: lags (1h, 6h, 24h), slopes (1h, 6h, 24h, 7d), volatility (24h) |
| `train_all_regressors(X, targets, names)` | 4 horizons x 3 quantiles = 12 XGBRegressor models |
| `update_registry(path, version, ...)` | Auto-increments version; first version auto-promotes |

**Non-obvious decisions**:
- Trained on 100% of the corpus with no internal train/test split. Rationale: rare anomaly types get maximum coverage. Evaluation happens at inference time against independently generated data.
- Classifier: `n_estimators=200, max_depth=6, learning_rate=0.1`. Threshold = 0.3 (biased toward recall).
- Regression: `objective='reg:quantileerror'` (pinball loss). Each quantile is a separate model to avoid crossing violations.
- Horizons: 1h (12 samples), 6h (72), 24h (288), 7d (2016). Quantiles: p10, p50, p90.
- Temporal features for regression: `te_score_lag_1h`, `_lag_6h`, `_lag_24h`, `_slope_1h`, `_slope_6h`, `_volatility_24h`, `_slope_24h`, `_slope_7d`. These are per-device (no cross-device leakage). The 24h and 7d slopes close the temporal feature / prediction horizon mismatch — the 7d regression target now has matching-scale slope inputs.
- Post-prediction: enforce p10 <= p50 <= p90 to handle rare quantile crossings from separate models.
- Per-anomaly-type sub-classifiers trained for interpretability (feature importance per type) but not used for scoring.

<a id="score"></a>
### 2.10 `tasks/score.py`

**Purpose**: Scores latest 24h window with pre-trained classifier and (optionally) regression model.

**Inputs**: `kpi_timeseries.parquet`, `anomaly_model.joblib`, `fleet_metadata.json`, optionally `regression_model_v{N}.joblib` + `model_registry.json`
**Outputs**: `fleet_risk_scores.json`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `predict_horizons(last_row, artifact)` | Predicts TE_score at 4 horizons x 3 quantiles for one device |
| `compute_predicted_crossings(predictions)` | Finds first horizon where p50 drops below 0.8 or 0.6 |
| `main()` | Selects 24h window, scores classifier, aggregates per-device, optionally predicts |

**Non-obvious decisions**:
- Scoring window: last 24h of data. Uses full history (not just window) to compute temporal features — lags need earlier data.
- Graceful regression fallback: if no regression model found, outputs classifier-only scores (no `predictions` or `predicted_crossings` fields).
- Crossing confidence: "high" if p90 also crosses threshold, "medium" if only p50 crosses.
- Devices sorted by `mean_risk` descending in output.

#### Feature justification

All 50 classifier features and 8 regressor-only temporal features, with domain justification and confidence level:

| Group | Count | Features | Justification | Strength |
|-------|-------|----------|---------------|----------|
| TE decomposition | 6 | `te_base`, `voltage_penalty`, `cooling_ratio`, `eta_v`, `true_efficiency`, `te_score` | Directly from TE KPI formula; physics: P ∝ V²f (CMOS), cooling normalization. Documented in `docs/true-efficiency-kpi.md` | Strong |
| Rolling stats | 16 | `{col}_mean_1h`, `_std_1h`, `_mean_24h`, `_dev_24h` for 6 telemetry cols | Standard time-series summarization. 1h = recent trend, 24h = daily baseline, dev = z-score deviation. Window sizes are round-number conventions, not empirically tuned | Reasonable |
| Rate of change | 4 | `d_{col}_smooth` for temp, power, hashrate, voltage | First-order derivative captures onset of degradation. Smoothed (1h rolling) to suppress sensor noise | Strong |
| Interaction | 5 | `power_per_ghz`, `thermal_headroom_c`, `cooling_effectiveness`, `hashrate_ratio`, `voltage_deviation` | Physics-motivated diagnostic ratios. `power_per_ghz` ≈ constant if healthy; `thermal_headroom` = distance to limit | Strong |
| Fleet-relative | 4 | `{col}_fleet_z` for temp, power, hashrate, efficiency | Relative performance vs peers (same model, same timestamp). Catches individual device drift that absolute values miss | Reasonable |
| Site conditions | 2 | `ambient_temp_c`, `energy_price_kwh` | `ambient_temp_c`: relevant (affects cooling). `energy_price_kwh`: **no physical relationship to device health** — economic signal leaked into health detector | Weak (`energy_price`) |
| Hardware health sensors | 6 | `fan_rpm`, `voltage_ripple_mv`, `reboot_count`, `chip_count_active`, `hashboard_count_active`, `dust_index` | Raw telemetry passthrough from physics engine. Strongest early warning signals for fan bearing wear (RPM decline), PSU capacitor aging (ripple increase), solder fatigue (chip dropout), dust fouling (accumulation index). See `deep-research-report-mining.md`, `notes_mining_data.md` | Strong |
| 7-day rolling windows | 5 | `temperature_c_mean_7d`, `temperature_c_dev_7d`, `power_w_mean_7d`, `hashrate_th_mean_7d`, `efficiency_jth_mean_7d` | Multi-day baseline for gradual degradation invisible in 24h windows. `notes_mining_data.md` line 42: "A 3°C rise over a week at constant ambient is a stronger signal than absolute temperature." Fan bearing wear, PSU capacitor aging, thermal paste degradation, dust fouling all operate on week-scale timelines | Strong |
| Hardware diagnostics | 2 | `voltage_ripple_std_24h`, `chip_dropout_ratio` | `voltage_ripple_std_24h`: PSU capacitor aging manifests as increasing variance before the mean shifts (`notes_mining_data.md` line 44). `chip_dropout_ratio`: active/nominal chips normalized across models — "chip count dropping" is first predictive signal (`notes_mining_data.md` line 13) | Strong |
| Temporal (regressor only) | 8 | `te_score_lag_{1h,6h,24h}`, `te_score_slope_{1h,6h,24h,7d}`, `te_score_volatility_24h` | Autoregressive features encoding trajectory. Lags = level at past horizons, slopes = trend direction (1h–7d), volatility = stability. 24h and 7d slopes close the temporal feature / prediction horizon mismatch | Strong |

Total: 50 (classifier) / 58 (regressor = 50 + 8 temporal).

#### Known gaps and limitations

1. **No feature selection / ablation study** — all 50 features included by default. No evidence that dropping any specific feature hurts performance. XGBoost's built-in feature importance provides ranking but not necessity. See DR-CAL-09.

2. **Window sizes are convention, not calibrated** — 1h/12h/24h are round numbers. Comment in `features.py` mentions MOS provides 5s/5m/30m resolutions, but the windows don't map to those. The 12h window is computed but excluded from the model entirely.

3. **`energy_price_kwh` is a feature leak** — no physical relationship to device health. If XGBoost learns spurious correlations with this signal in synthetic data (e.g., anomalies happen to co-occur with certain price patterns), the model will fail on real data where the correlation doesn't hold.

4. **Fleet z-scores are snapshot-only** — computed per timestamp, not over a window. A device slowly drifting away from fleet median over days isn't captured; only instant divergence is visible.

5. **Computed features not in model** — `features.py` computes many rolling windows; not all are in `FEATURE_COLS`. The 12h rolling means (6), raw diffs (4), 30m hashrate (2), and several 7d std/dev columns are computed but excluded. Documented as "future work" but no ablation justifies exclusion either.

6. **Classifier and regressor see different feature sets** — classifier gets 50 point-in-time features, regressor gets 58 (50 + 8 temporal). The temporal features are the regressor's main advantage, but they're computed in both `train_model.py` and `score.py` (same logic, duplicated code).

7. **Scoring window is fixed at 24h** — no adaptive window. A degradation that started 36h ago shows diluted signal vs one that started 6h ago.

#### Why 24h? — scoring window rationale

The 24h window matches the intended batch cadence: score once per day, flag what needs attention. Shorter windows (e.g. 6h) would be more responsive but noisier — a single transient spike could trigger a false flag. Longer windows (e.g. 72h) dilute current degradation signal with historical health data. The regressor compensates for the fixed window by using full history for temporal features (lags, slopes), giving it trajectory awareness that the classifier's point-in-time view lacks.

<a id="trend-analysis"></a>
### 2.11 `tasks/trend_analysis.py`

**Purpose**: Per-device trend vectors, CUSUM regime detection, and threshold crossing projections.

**Inputs**: `kpi_timeseries.parquet`, `fleet_risk_scores.json` (optional)
**Outputs**: `trend_analysis.json`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `compute_linear_trend(values)` | OLS slope + R^2 via polyfit. Returns slope_per_sample, r_squared, n_samples |
| `detect_regime_change_cusum(values, h, k)` | Two-sided CUSUM (Page 1954). Reference period = first 25% of history |
| `project_threshold_crossing(current, slope, r2, threshold)` | Linear extrapolation to 0.8 and 0.6 thresholds |
| `classify_direction(slope_per_hour)` | Maps slope to falling_fast / declining / stable / recovering / recovering_fast |

**Non-obvious decisions**:
- CUSUM h=8.0, k=0.5 (Hawkins defaults for ~1-sigma shift detection). Reference period = first 25% of device history to avoid contaminating baseline with the change itself.
- Direction thresholds: falling_fast < -0.02/h, declining < -0.005/h, stable within +/-0.005/h. At 5-min sampling, slopes within +/-0.005 are indistinguishable from measurement noise.
- MIN_SAMPLES = 6 (30 min). Below this, noise dominates.
- MIN_R2_FOR_PROJECTION = 0.1 (permissive). The confidence value in the output *is* the R^2, so consumers can filter.
- Temperature trends use EWMA (span=12) for smoothing before slope computation. TE trends use raw linear regression.
- All functions are pure (no side effects, no file I/O). `analyze_device_trends()` orchestrates them per device.

<a id="optimize"></a>
### 2.12 `tasks/optimize.py`

**Purpose**: Deterministic controller — safety overrides, tier classification, and MOS-mapped command generation.

**Inputs**: `fleet_risk_scores.json`, `kpi_timeseries.parquet`, `fleet_metadata.json`, optionally `trend_analysis.json`
**Outputs**: `fleet_actions.json`, `_validance_vars.json`

**Key functions**:

| Function | What it does |
|----------|--------------|
| `apply_safety_overrides(risk, stock)` | Checks 4 safety constraints; returns override commands. Always runs before tier logic. |
| `classify_tier(risk, trend)` | Maps risk/TE_score to CRITICAL/WARNING/DEGRADED/HEALTHY. Applies trend-aware escalation (never de-escalates). |
| `apply_fleet_redundancy(actions)` | Defers lowest-risk device if all same-model devices are flagged for inspection |
| `annotate_mos_methods(actions)` | Maps command types to MOS RPC names (setFrequency, setPowerMode, etc.) |

**Non-obvious decisions**:
- Safety overrides applied BEFORE tier logic — they always win. Order: thermal hard limit (80 C) → thermal emergency low (10 C) → thermal low warning (20 C) → overvoltage (110% stock).
- No `set_voltage` command exists. Voltage is V/f coupled in ASIC firmware — reducing frequency implicitly restores nominal V/f point. This matches MOS's `setFrequency` as the primary tuning RPC.
- Trend escalation: slope < -0.005/h AND HEALTHY → WARNING. Slope < -0.02/h → escalate one step toward CRITICAL. CUSUM regime change AND HEALTHY → WARNING. Minimum R^2 = 0.3 to trust trend.
- Trend de-escalation is explicitly disabled (conservative). A recovering device stays at its tier until static logic catches up.
- Fleet redundancy: never all devices of same model offline. If conflict, lowest-risk device gets deferred.
- Overclock suggestion: only for HEALTHY devices with thermal_headroom > 10 C and not already overclocked. Target = stock * 1.05.

<a id="report"></a>
### 2.13 `tasks/report.py`

**Purpose**: Generates self-contained HTML dashboard with embedded base64 PNG charts.

**Inputs**: `kpi_timeseries.parquet`, `fleet_risk_scores.json`, `fleet_actions.json`, `fleet_metadata.json`, optionally `trend_analysis.json`, `model_metrics.json`
**Outputs**: `report.html`

**Key functions**:

| Function | Chart type | Data source |
|----------|-----------|-------------|
| `plot_te_timeseries(df)` | Line chart, per device, hourly | kpi_timeseries.parquet |
| `plot_te_decomposition(df)` | Grouped bar (TE_base + voltage + cooling) | kpi_timeseries.parquet |
| `plot_health_scores(df)` | Heatmap (device x date), RdYlGn | kpi_timeseries.parquet |
| `plot_anomaly_timeline(df)` | Stacked area per anomaly type | kpi_timeseries.parquet (label_* columns) |
| `plot_risk_ranking(risk_scores)` | Horizontal bar, sorted by mean_risk | fleet_risk_scores.json |
| `plot_controller_tiers(actions)` | Bar + pie (tier distribution) | fleet_actions.json |
| `plot_model_metrics(metrics)` | Feature importance (top 15), per-type breakdown | model_metrics.json |

**Non-obvious decisions**:
- All charts rendered with matplotlib Agg backend (no display), DPI=120, embedded as `data:image/png;base64,...`. No external dependencies in the HTML.
- Anomaly timeline dynamically discovers `label_*` columns with >= 1 positive sample. Handles varying anomaly mixes across scenarios without code changes.
- Health heatmap: vmin=0.5, vmax=1.2. Scores above 1.0 (better than nominal) appear as bright green.
- Tier colors: CRITICAL=#F44336, WARNING=#FF9800, DEGRADED=#FFC107, HEALTHY=#4CAF50.
- Optional sections (predictions, trends, economics) are included only if their input files exist. The report degrades gracefully.

---

## 3. Data Contracts

Schemas for all files that flow between tasks. Each schema is documented once; producers and consumers reference this section.

### 3.1 `telemetry.parquet`

**Producer**: [`tasks/ingest.py`](#ingest) | **Consumers**: [`tasks/features.py`](#features)

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
| `label_thermal_deg` | int64 | Binary: thermal degradation |
| `label_psu_instability` | int64 | Binary: PSU instability |
| `label_hashrate_decay` | int64 | Binary: hashrate decay |
| `label_any_anomaly` | int64 | Binary: any anomaly |
| `label_fan_bearing_wear` | int64 | Binary |
| `label_capacitor_aging` | int64 | Binary |
| `label_dust_fouling` | int64 | Binary |
| `label_thermal_paste_deg` | int64 | Binary |
| `label_solder_joint_fatigue` | int64 | Binary |
| `label_coolant_loop_fouling` | int64 | Binary |
| `label_firmware_cliff` | int64 | Binary |

**Invariants**: No duplicate (timestamp, device_id). Sorted by (device_id, timestamp).

### 3.2 `features.parquet`

**Producer**: [`tasks/features.py`](#features) | **Consumers**: [`tasks/kpi.py`](#kpi)

All columns from `telemetry.parquet`, plus:

**Device constants** (joined from metadata):

| Column | Type |
|--------|------|
| `stock_clock` | float64 |
| `stock_voltage` | float64 |
| `nominal_hashrate` | float64 |
| `nominal_power` | float64 |
| `nominal_efficiency` | float64 |

**Rolling statistics** — for each of `[temperature_c, power_w, hashrate_th, voltage_v, cooling_power_w, efficiency_jth]`:

| Pattern | Windows |
|---------|---------|
| `{col}_mean_1h`, `{col}_std_1h` | 12 samples |
| `{col}_mean_12h` | 144 samples |
| `{col}_mean_24h`, `{col}_std_24h`, `{col}_dev_24h` | 288 samples |

Plus `hashrate_th_mean_30m`, `hashrate_th_std_30m` (6 samples).

**Rate of change** — for `[temperature_c, power_w, hashrate_th, voltage_v]`:

| Column | Computation |
|--------|-------------|
| `d_{col}` | `.diff()` per device |
| `d_{col}_smooth` | 12-sample rolling mean of diffs |

**Cross-device** — for `[temperature_c, power_w, hashrate_th, efficiency_jth]`:

| Column | Computation |
|--------|-------------|
| `{col}_fleet_z` | Z-score within same model per timestamp |

**Interaction features**:

| Column | Formula |
|--------|---------|
| `power_per_ghz` | `power_w / clock_ghz` |
| `thermal_headroom_c` | `85.0 - temperature_c` |
| `cooling_effectiveness` | `(temperature_c - ambient_temp_c) / cooling_power_w` |
| `hashrate_ratio` | `hashrate_th / nominal_hashrate` |
| `voltage_deviation` | `voltage_v - stock_voltage` |

Total: ~55 columns on top of the raw telemetry.

### 3.3 `kpi_timeseries.parquet`

**Producer**: [`tasks/kpi.py`](#kpi) | **Consumers**: [`tasks/train_model.py`](#train-model), [`tasks/score.py`](#score), [`tasks/trend_analysis.py`](#trend-analysis), [`tasks/optimize.py`](#optimize), [`tasks/report.py`](#report)

All columns from `features.parquet`, plus:

| Column | Type | Formula | Notes |
|--------|------|---------|-------|
| `eta_v` | float64 | `(V_optimal / V_actual)^2` | Voltage efficiency, clipped [0, 2] |
| `p_cooling_norm` | float64 | `P_cool * (T_chip - 25) / max(T_chip - T_amb, 1)` | Normalized cooling power |
| `te_base` | float64 | `power_w / hashrate_th` | Naive J/TH |
| `voltage_penalty` | float64 | `1.0 / eta_v` | Voltage impact multiplier |
| `cooling_ratio` | float64 | `(power_w + p_cooling_norm) / power_w` | Cooling overhead ratio |
| `true_efficiency` | float64 | `(power_w + p_cooling_norm) / (hashrate_th * eta_v)` | True Efficiency (J/TH) |
| `te_nominal` | float64 | `(P + 0.10*P) / H` at stock | Per-device nominal TE |
| `te_score` | float64 | `te_nominal / true_efficiency` | Health score (1.0 = nominal) |

**Invariant**: KPI columns are NaN for idle samples (hashrate_th <= 0).

### 3.4 `fleet_risk_scores.json`

**Producer**: [`tasks/score.py`](#score) | **Consumers**: [`tasks/trend_analysis.py`](#trend-analysis), [`tasks/optimize.py`](#optimize), [`tasks/report.py`](#report)

```
{
  "scoring_window_hours": int,          // Always 24
  "window_start": str,                  // ISO timestamp
  "window_end": str,
  "samples_scored": int,
  "threshold": float,                   // Default 0.3
  "model_versions": {
    "classifier": str,                  // Artifact filename
    "regressor_version": int | null
  },
  "device_risks": [                     // Sorted by mean_risk descending
    {
      "device_id": str,
      "model": str,
      "mean_risk": float,              // [0, 1] — average anomaly prob over window
      "max_risk": float,               // [0, 1] — peak probability
      "pct_flagged": float,            // [0, 1] — fraction of samples > threshold
      "last_risk": float,              // [0, 1] — most recent probability
      "flagged": bool,                 // mean_risk > threshold
      "latest_snapshot": {
        "timestamp": str,
        "te_score": float,
        "true_efficiency": float,
        "temperature_c": float,
        "voltage_v": float,
        "hashrate_th": float,
        "power_w": float,
        "cooling_power_w": float,
        "ambient_temp_c": float,
        "operating_mode": str
      },
      "predictions": {                 // OPTIONAL — only if regression model present
        "te_score_1h": {"p10": float, "p50": float, "p90": float},
        "te_score_6h": {"p10": float, "p50": float, "p90": float},
        "te_score_24h": {"p10": float, "p50": float, "p90": float},
        "te_score_7d": {"p10": float, "p50": float, "p90": float}
      },
      "predicted_crossings": {         // OPTIONAL
        "te_0.8": {"horizon": str, "confidence": str, "p50": float} | null,
        "te_0.6": {"horizon": str, "confidence": str, "p50": float} | null
      }
    }
  ]
}
```

### 3.5 `trend_analysis.json`

**Producer**: [`tasks/trend_analysis.py`](#trend-analysis) | **Consumers**: [`tasks/optimize.py`](#optimize), [`tasks/report.py`](#report)

```
{
  "analysis_version": str,             // "3.0-trend"
  "sample_interval_minutes": int,      // 5
  "windows": {"1h": 12, "6h": 72, "24h": 288, "7d": 2016},
  "cusum_params": {"h": 8.0, "k": 0.5},
  "devices": [
    {
      "device_id": str,
      "current_state": {
        "te_score": float,
        "temperature_c": float,
        "mean_risk": float
      },
      "te_trends": {                   // One entry per window
        "1h"|"6h"|"24h"|"7d": {
          "slope_per_hour": float,     // TE_score change per hour
          "r_squared": float,          // [0, 1] — fit quality
          "direction": str,            // falling_fast|declining|stable|recovering|recovering_fast
          "n_samples": int
        }
      },
      "temp_trends": {                 // 6h and 24h only
        "6h"|"24h": {
          "slope_per_hour": float,
          "r_squared": float,
          "last_ewma": float,
          "n_samples": int
        }
      },
      "risk_trends": {                 // 1h only
        "1h": {
          "slope_per_hour": float,
          "r_squared": float,
          "direction": str,
          "n_samples": int
        }
      },
      "regime": {
        "change_detected": bool,
        "change_index": int | null,
        "direction": str,              // "increasing"|"decreasing"|"stable"
        "max_cusum_pos": float,
        "max_cusum_neg": float
      },
      "projections": {
        "0.8": {                       // DEGRADED threshold
          "hours_to_crossing": float | null,
          "confidence": float,         // = R^2 of underlying trend
          "will_cross": bool
        },
        "0.6": { ... }                // CRITICAL threshold
      },
      "primary_direction": str,        // 24h window direction
      "primary_slope_per_hour": float,
      "primary_r_squared": float
    }
  ],
  "fleet_summary": {
    "device_count": int,
    "regime_changes": int,
    "direction_distribution": {
      "stable": int, "declining": int, "falling_fast": int,
      "recovering": int, "recovering_fast": int
    }
  }
}
```

### 3.6 `fleet_actions.json`

**Producer**: [`tasks/optimize.py`](#optimize) | **Consumers**: [`tasks/report.py`](#report), SafeClaw

```
{
  "controller_version": str,           // "2.0-tier-only"
  "scoring_window": {
    "start": str,
    "end": str
  },
  "tier_counts": {
    "CRITICAL": int,
    "WARNING": int,
    "DEGRADED": int,
    "HEALTHY": int
  },
  "safety_constraints_applied": [str], // e.g. ["thermal_hard_limit_80C"]
  "actions": [
    {
      "device_id": str,
      "model": str,
      "tier": str,                     // CRITICAL|WARNING|DEGRADED|HEALTHY
      "risk_score": float,
      "te_score": float,
      "commands": [
        {
          "type": str,                 // set_clock|set_power_mode|schedule_inspection|...
          "value_ghz": float,          // for set_clock
          "value": str,                // for set_power_mode, set_fan_mode
          "urgency": str,              // for schedule_inspection
          "value_seconds": int,        // for set_monitoring_interval
          "priority": str,             // HIGH|MEDIUM|LOW
          "mos_method": str | null,    // MOS RPC name or null if operational
          "mos_note": str              // present when mos_method is null
        }
      ],
      "rationale": [str],             // Human-readable explanation lines
      "trend_context": {               // OPTIONAL — only if trend_analysis.json exists
        "direction": str,
        "slope_per_hour": float,
        "r_squared": float,
        "regime_change": bool
      },
      "mos_alert_codes": [str]        // e.g. ["P:1", "R:1"]
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

**Producer**: [`tasks/train_model.py`](#train-model) | **Consumers**: [`tasks/report.py`](#report)

```
{
  "model": str,                        // "XGBClassifier"
  "train_samples": int,
  "anomaly_rate": float,
  "devices": int,
  "feature_count": int,
  "threshold": float,
  "top_features": [
    {"feature": str, "importance": float}
  ],
  "per_anomaly_type": {
    "<type_name>": {
      "train_positives": int,
      "positive_rate": float,
      "devices_affected": int,
      "top_features": [{"feature": str, "importance": float}]
    } | {"skipped": true, "reason": str}  // if no positives
  },
  "regression": {                      // OPTIONAL — only if regression trained
    "model_type": str,
    "version": int,
    "promoted": bool,
    "horizons": [str],
    "quantiles": [float],
    "feature_count": int,
    "per_horizon": {
      "<horizon>": {"train_samples": int}
    }
  }
}
```

### 3.8 `anomaly_model.joblib`

**Producer**: [`tasks/train_model.py`](#train-model) | **Consumer**: [`tasks/score.py`](#score)

Joblib-pickled Python dict:

```python
{
    "model": XGBClassifier,            # Trained binary classifier
    "feature_names": [str],            # 50 feature names (see §2.9)
    "threshold": float                 # 0.3 default, CLI-overridable
}
```

Usage: `proba = model.predict_proba(X[feature_names])[:, 1]`, then `flagged = proba > threshold`.

### 3.9 `regression_model_v{N}.joblib`

**Producer**: [`tasks/train_model.py`](#train-model) | **Consumer**: [`tasks/score.py`](#score)

Joblib-pickled Python dict:

```python
{
    "model_type": "multi_horizon_quantile_regression",
    "version": int,
    "horizons": ["1h", "6h", "24h", "7d"],
    "quantiles": [0.1, 0.5, 0.9],
    "feature_names": [str],            # 50 base + 8 temporal = 58
    "models": {
        "1h": {"p10": XGBRegressor, "p50": XGBRegressor, "p90": XGBRegressor},
        "6h": {...},
        "24h": {...},
        "7d": {...}
    },
    "trained_at": str                  # ISO timestamp
}
```

Temporal features (8, added by score.py before prediction):
`te_score_lag_1h`, `te_score_lag_6h`, `te_score_lag_24h`, `te_score_slope_1h`, `te_score_slope_6h`, `te_score_volatility_24h`, `te_score_slope_24h`, `te_score_slope_7d`.

### 3.10 `training_metadata.json`

**Producer**: [`scripts/generate_training_corpus.py`](#generate-corpus) | **Consumer**: reference/verification

```
{
  "generator": str,
  "generated_at": str,
  "parameters": {
    "total_rows": int,
    "scenario_count": int,
    "seed_override": int | null,
    "columns": int,
    "num_devices": int
  },
  "fleet": [
    {
      "device_id": str,
      "model": str,
      "stock_clock_ghz": float,
      "stock_voltage_v": float,
      "nominal_hashrate_th": float,
      "nominal_power_w": float,
      "nominal_efficiency_jth": float
    }
  ],
  "scenarios": [
    {
      "name": str,
      "seed": int,
      "duration_days": int,
      "device_count": int,
      "total_rows": int,
      "anomalies": [
        {
          "device_idx": int,
          "type": str,
          "start_day": float,
          "ramp_days": float,
          "severity": float
        }
      ]
    }
  ],
  "label_stats": {"<label_column>": int},
  "data_hash_sha256": str
}
```

### 3.11 `fleet_metadata.json` (input)

**Producer**: external | **Consumers**: all tasks

```
{
  "fleet": [
    {
      "device_id": str,
      "model": str,
      "stock_clock_ghz": float,
      "stock_voltage_v": float,
      "nominal_hashrate_th": float,
      "nominal_power_w": float,
      "nominal_efficiency_jth": float
    }
  ]
}
```

### 3.12 `_validance_vars.json`

**Producer**: every task | **Consumer**: Validance kernel

Each task writes this file with task-specific output variables. Not consumed by other tasks — only by the Validance engine for workflow variable propagation.

| Task | Variables |
|------|-----------|
| ingest | `row_count`, `device_count`, `time_span_days` |
| features | `feature_count`, `sample_count` |
| kpi | `mean_te`, `worst_device`, `worst_te_score` |
| train_model | `train_samples`, `anomaly_rate`, `model_version` |
| score | `flagged_devices`, `scoring_window_hours` |
| trend_analysis | `devices_with_regime_change` |
| optimize | `actions_issued`, `devices_underclocked`, `devices_inspected` |
