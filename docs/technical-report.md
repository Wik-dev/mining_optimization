# AI-Driven Mining Optimization & Predictive Maintenance

**Technical Report — MDK Assignment (Plan B, Tether)**

Victor Wiklander | April 2026

---

## 1. Problem Statement

Bitcoin mining profitability depends on marginal gains. At Tether's scale, operators manage fleets of heterogeneous ASICs (S21-HYD, M66S, S19XP, S19jPro) across sites where ambient conditions, energy pricing, and hardware health change continuously. Two problems dominate operational cost:

**Chip-level efficiency** — each ASIC has an optimal operating point defined by the interaction of clock frequency, core voltage, temperature, and cooling load. Currently, mode selection (overclock, underclock, idle) is done manually based on operator intuition. The standard efficiency metric (J/TH) ignores cooling overhead, voltage waste, and ambient conditions, making cross-device and cross-condition comparison unreliable.

**Predictive maintenance** — ASIC repair is the largest single cost line. Failures manifest as gradual degradation (thermal fouling, chip aging, PSU instability) that is detectable in telemetry days before critical failure, yet operators today have no systematic early-warning system.

This project addresses both problems through a unified data pipeline: a physics-grounded efficiency KPI that normalizes for operating conditions, and a machine learning model that detects degradation patterns in the KPI's decomposition factors.

## 2. True Efficiency KPI

### 2.1 Why Naive J/TH Fails

The standard metric `J/TH = P_asic / hashrate` conflates three independent dimensions: hardware quality, voltage management, and cooling overhead. A miner at -5°C ambient with free cooling looks identical to one running 20°C hotter with 50% more cooling power. An overclocked chip drawing 15% more voltage shows the same J/TH if its hashrate scales proportionally, even though it wastes disproportionate power on V² losses.

### 2.2 Formulation

True Efficiency (TE) separates what the operator controls from what the environment gives:

```
TE = (P_asic + P_cooling_norm) / (H × η_v)     [J/TH]
```

**Voltage efficiency factor** — captures how far the operating voltage deviates from the minimum stable voltage for the current clock frequency:

```
η_v = (V_optimal(f) / V_actual)²
V_optimal(f) = V_stock × (f / f_stock)^0.6
```

The exponent 0.6 reflects sub-linear V/f scaling in modern CMOS. When voltage is higher than necessary (overvolting, PSU ripple), η_v < 1 and TE increases proportionally.

**Ambient-normalized cooling** — removes geographic bias by projecting cooling power to a reference ambient of 25°C:

```
P_cooling_norm = P_cooling × (T_chip - 25°C) / max(T_chip - T_ambient, 1°C)
```

A site in Iceland at -5°C would see its apparent cooling advantage normalized out, enabling fair comparison with a site at 20°C.

### 2.3 Diagnostic Decomposition

TE factors into three independent components, each mapping to a specific failure mode:

```
TE = TE_base × (1/η_v) × R_cool
```

| Factor | Meaning | Anomaly signal |
|--------|---------|----------------|
| TE_base = P_asic / H | Hardware-intrinsic efficiency | Hashrate decay (chip aging) |
| 1/η_v | Voltage penalty | PSU instability |
| R_cool = (P + P_cool_norm) / P | Cooling overhead | Thermal degradation (fouling) |

This decomposition is the key insight: rather than training a model on raw telemetry (17 correlated signals), we train on *which TE component is drifting*. Each component isolates a single physical mechanism, making the model's predictions interpretable and actionable.

**Device health score** normalizes TE against each device's nominal baseline:

```
TE_score = TE_nominal / TE
```

A score of 1.0 means nominal performance. Below 0.9 triggers investigation; the decomposition tells the operator *why*.

## 3. System Architecture

The pipeline is a 7-task directed acyclic graph (DAG) with two distinct paths: an **offline batch path** (ingest → features → KPI → train) and an **online inference + control path** (score → optimize → report). This separation reflects the production intent: the model is trained periodically on historical data, while scoring and control run continuously on the latest telemetry window.

The DAG is defined in `workflows/fleet_intelligence.py` and executed through a workflow engine providing deterministic execution, full audit trail, and content-addressed versioning.

```
  Offline (batch) path                    Online (per-interval) path
  ─────────────────────                   ──────────────────────────

┌─────────────────────┐
│ [1] ingest_telemetry │   CSV + metadata → Parquet
│     (86,400 rows)    │   Schema validation, type coercion, dedup
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ [2] engineer_features│   55 features per sample:
│     (rolling, Δ, z)  │   Rolling stats (30m/1h/12h/24h), rates of change,
└──────────┬──────────┘   fleet z-scores, interaction terms
           ▼
┌─────────────────────┐
│ [3] compute_true_eff │   TE formula, decomposition, TE_score
│     (KPI engine)     │   Health score per device against nominal
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ [4a] train_anomaly   │   XGBoost on 35 features (TE decomposition +
│      _model          │   rolling + interaction + fleet-relative)
└──────────┬──────────┘   + per-anomaly-type classifiers
           │
           │  model artifact (anomaly_model.joblib)
           ▼
┌─────────────────────┐
│ [4b] score_fleet     │   Load model, score last 24h window
│      (online)        │   Per-device risk aggregation + telemetry snapshot
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ [5] optimize_fleet   │   Tier-based controller (CRITICAL → WARNING →
│     (controller)     │   DEGRADED → HEALTHY) with safety overrides
└──────────┬──────────┘   Emits operational commands per device
           ▼
┌─────────────────────┐
│ [6] generate_report  │   HTML dashboard: 7 charts + action table +
│     (visualization)  │   risk ranking + per-anomaly-type breakdown
└─────────────────────┘
```

Each task runs in a `python:3.11-slim` Docker container. Inputs and outputs pass through a shared working directory using Parquet files (telemetry, features, KPI timeseries), JSON (metadata, risk scores, model metrics, controller actions), and a joblib model artifact. Inter-task data references use the `@task_name:output_key` syntax. Each task writes its output variables to `_validance_vars.json` for workflow-level observability.

The workflow definition serializes to JSON with a deterministic SHA-256 hash — any change to task definitions, dependencies, or parameters produces a new hash, enabling reproducible execution and audit.

**Why not a Jupyter notebook?** The workflow engine provides what notebooks cannot: deterministic re-execution, content-addressed versioning, containerized isolation per task, and a clear separation between pipeline definition (declarative JSON) and task logic (imperative Python). This matters for production mining operations where audit trail and reproducibility are requirements, not conveniences.

## 4. Data Pipeline

### 4.1 Synthetic Dataset

The dataset simulates a 10-device fleet over 30 days at 5-minute intervals (86,400 rows). The generator (`scripts/generate_synthetic_data.py`) is fully deterministic (seed=42) and implements a physics engine per device per timestep:

- **CMOS power model**: `P = k × V² × f + P_static(T)` — dynamic power scales quadratically with voltage; static/leakage power grows exponentially with temperature (`exp(0.02 × (T - 40))`)
- **Hashrate**: `H ∝ f × (1 - chip_degradation)` — linear with clock, reduced by chip failure
- **Thermal model**: exponential approach to equilibrium (`T_target = T_ambient + P × R_thermal`) with thermal inertia (τ = 0.4h) and resistance that increases with fouling (`R × (1 + 2 × fouling)`)
- **Cooling**: proportional controller with setpoint at 65°C, base load per device model (400–550W), fouling increases cooling power by up to 50%
- **Operating modes**: rule-based mode selection (overclock when price < $0.04 and ambient < 5°C; underclock when price > $0.06; idle when > $0.07), with per-mode clock multipliers and voltage offsets
- **Environment**: sinusoidal ambient temperature (seasonal + diurnal, 64.5°N latitude, April start), time-of-use energy pricing ($0.035 off-peak, $0.065 peak)

Fleet composition (heterogeneous, matching Tether's hardware generations):

| Model | Count | Nominal Hashrate | Nominal Power | Efficiency |
|-------|-------|-----------------|---------------|------------|
| S21-HYD | 2 | 335 TH/s | 5025 W | 15.0 J/TH |
| M66S | 2 | 298 TH/s | 5370 W | 18.0 J/TH |
| S19XP | 3 | 141 TH/s | 3010 W | 21.3 J/TH |
| S19jPro | 3 | 104 TH/s | 3068 W | 29.5 J/TH |

Three anomaly patterns are injected with known onset times and linear ramp rates, providing ground-truth labels for supervised training:

| Anomaly | Devices | Onset | Ramp | Severity | Physical mechanism |
|---------|---------|-------|------|----------|-------------------|
| Thermal degradation | ASIC-007, ASIC-004 | Day 8, 18 | 15d, 10d | 70%, 40% | `_thermal_fouling` → rising thermal resistance (up to 3× clean) |
| PSU instability | ASIC-003 | Day 14 | 2d | 80% | `_psu_ripple` → voltage noise up to 50mV |
| Hashrate decay | ASIC-009, ASIC-002 | Day 5, 22 | 20d, 5d | 25%, 15% | `_chip_degradation` → partial ASIC failure |

### 4.2 Feature Engineering (Task 2)

55 features are computed per sample across five categories:

- **Device constants** — stock specs from fleet metadata (stock clock, stock voltage, nominal hashrate/power/efficiency) joined onto each telemetry row for baseline comparison
- **Rolling statistics** (30m/6 samples, 1h/12, 12h/144, 24h/288 windows): mean, std, and deviation from 24h baseline (z-score) for each of 6 telemetry signals (temperature, power, hashrate, voltage, cooling power, efficiency). Additionally, a 30-minute hashrate window (`hashrate_th_mean_30m`, `hashrate_th_std_30m`) approximates MOS's multi-resolution hashrate hierarchy (5s/5m/30m)
- **Rates of change**: first-order `.diff()` for temperature, power, hashrate, and voltage, plus 1h smoothed versions (`d_*_smooth`)
- **Fleet-relative z-scores**: per-timestamp, per-model-group normalization — a device running hot relative to its peers is more informative than absolute temperature
- **Interaction terms**: `power_per_ghz` (voltage stability proxy), `thermal_headroom_c` (85°C - T), `cooling_effectiveness` (thermal gradient per watt of cooling), `hashrate_ratio` (vs. nominal), `voltage_deviation` (from stock)

### 4.3 Model Features (Task 4a)

The anomaly model uses a curated subset of 35 features selected from the feature matrix:

- **TE decomposition** (6): `te_base`, `voltage_penalty`, `cooling_ratio`, `eta_v`, `true_efficiency`, `te_score`
- **Rolling stats** (14): 1h mean/std and 24h deviation for temperature, power, hashrate, voltage, cooling power, efficiency
- **Rates of change** (4): smoothed first differences for temperature, power, hashrate, voltage
- **Interaction features** (5): power per GHz, thermal headroom, cooling effectiveness, hashrate ratio, voltage deviation
- **Fleet-relative z-scores** (4): temperature, power, hashrate, efficiency
- **Site conditions** (2): ambient temperature, energy price

## 5. Results

### 5.1 Anomaly Detection (Task 4a)

The primary model (XGBoost, `n_estimators=200`, `max_depth=6`, `learning_rate=0.1`, class imbalance handled via `scale_pos_weight = n_neg / n_pos`) uses a time-based 70/30 train/test split (timestamp quantile cutoff — no shuffling, preventing data leakage from rolling features that look backward in time):

| Metric | Value |
|--------|-------|
| Accuracy | 93.5% |
| F1 score | 92.8% |
| Precision (anomaly) | 100% |
| Recall (anomaly) | 87% |

### 5.2 Fleet Scoring (Task 4b)

The scoring task simulates production real-time inference. It loads the pre-trained model artifact, selects the last 24-hour telemetry window, and runs `predict_proba` to produce per-sample anomaly probabilities. Per-device risk is aggregated as mean, max, and percentage of samples above the 0.5 threshold. Each device's latest telemetry snapshot (TE score, temperature, voltage, hashrate, operating mode) is included for downstream consumption by the controller.

The model correctly flagged all 5 anomalous devices (ASIC-003, -004, -007, -009 with >84% mean risk; ASIC-002 at 32% — its anomaly starts late on day 22, partially outside the scoring window). All 5 healthy devices scored <0.2% risk.

### 5.3 Per-Anomaly-Type Detection

Separate classifiers trained per anomaly type confirm that the TE decomposition provides discriminative features:

| Anomaly type | F1 | Top feature | Interpretation |
|---|---|---|---|
| Thermal degradation | 99.9% | `temperature_c_fleet_z` (84%) | Fouled device runs hotter than fleet peers |
| PSU instability | 98.1% | `voltage_v_std_1h` (65%) | Voltage ripple shows as short-term variance |
| Hashrate decay | 71.1% | `hashrate_th_fleet_z` (88%) | Degraded chips produce less hash than peers |

Hashrate decay is hardest to detect because the degradation is gradual (25% severity over 20 days) and its signal overlaps with normal operating mode changes. This is consistent with real-world expectations — slow chip aging is the most insidious failure mode.

### 5.4 Top Predictive Features

The aggregate model's feature importance confirms the TE decomposition thesis:

1. `true_efficiency` (31.5%) — the composite KPI itself is the strongest signal
2. `voltage_v_std_1h` (15.8%) — PSU instability marker
3. `efficiency_jth_fleet_z` (11.5%) — fleet-relative efficiency deviation
4. `hashrate_th_fleet_z` (6.7%) — fleet-relative hashrate deviation
5. `temperature_c_mean_24h` (5.2%) — thermal baseline drift

The TE decomposition factors (`te_score`, `voltage_penalty`, `eta_v`, `cooling_ratio`) collectively account for significant predictive power, validating the KPI design as both an operational metric and a feature engineering strategy.

## 6. Controller & Safety (Task 5)

The pipeline now includes an explicit controller stage that translates model risk scores into concrete operational commands. This is the "AI Controller → Command Execution" stage from the assignment.

### 6.1 Tier-Based Classification

Each device is assigned a severity tier based on its risk score and health:

| Tier | Condition | Response |
|------|-----------|----------|
| CRITICAL | mean_risk > 0.9 | Clock → 70% stock (V/f coupled — voltage adjusts implicitly), immediate inspection, 60s monitoring |
| WARNING | mean_risk > 0.5 | Clock → 85% stock, next-window inspection, 120s monitoring |
| DEGRADED | te_score < 0.8 and risk ≤ 0.5 | Reset frequency to stock (restores nominal V/f operating point), 180s monitoring |
| HEALTHY | otherwise | Hold settings; suggest 5% overclock if thermal headroom > 10°C |

### 6.2 Safety Overrides

Four hard safety constraints are evaluated **before** tier logic and supersede any tier-based command:

- **Thermal hard limit**: `T > 80°C` → force clock to 80% stock (CRITICAL priority). Aligns with MOS PCB critical threshold. Non-negotiable hardware protection.
- **Thermal emergency low**: `T < 10°C` → sleep mode + immediate inspection (CRITICAL priority). At the hydro-cooled northern site (64.5°N), sub-10°C temperatures risk coolant viscosity spikes and PCB condensation. Sleep mode eliminates heat generation; inspection checks coolant state.
- **Thermal low warning**: `10°C ≤ T < 20°C` → clock to 70% stock (HIGH priority). For air-cooled models, fan set to minimum to retain heat. For hydro units, fan command is N/A — the relevant control would be pump speed, which MOS doesn't expose directly.
- **Overvoltage protection**: `V > 110% stock` → reset frequency to stock (CRITICAL priority). MOS does not expose direct voltage control — voltage is coupled to frequency via the ASIC's V/f curve. Reducing frequency implicitly restores nominal voltage.

If a safety override fires on a HEALTHY device, the device is escalated to at least WARNING tier.

### 6.3 Fleet Redundancy

The controller enforces a fleet-level constraint: never schedule all devices of the same model for inspection simultaneously. If all devices of a model are flagged, the lowest-risk device's inspection is deferred with rationale logged. This preserves operational capacity per hardware generation.

### 6.4 Command Vocabulary

The controller emits commands mapped to MOS RPC methods. Every command in `fleet_actions.json` is annotated with its `mos_method` field via `MOS_COMMAND_MAP`:

| Command | MOS RPC method | Notes |
|---------|---------------|-------|
| `set_clock` | `setFrequency` | Primary tuning control. V/f coupled — voltage adjusts implicitly |
| `set_power_mode` | `setPowerMode` | `normal` / `sleep` (emergency low-temp response) |
| `set_fan_mode` | `setFanControl` | Air-cooled only; no MOS method for hydro pump control |
| `schedule_inspection` | — | Operational — no direct MOS RPC equivalent |
| `set_monitoring_interval` | — | Internal pipeline config, not a device command |
| `hold_settings` | — | No-op — no MOS RPC needed |
| `suggest_overclock` | `setFrequency` | Same method, higher target; conditional on thermal headroom |
| `reboot` | `reboot` | Device restart (2-3 min recovery) |

**No `set_voltage` commands** — MOS does not expose direct voltage control. Voltage is coupled to frequency via the ASIC firmware's V/f curve. All voltage management is achieved through frequency adjustment.

Each device action includes a rationale string, MOS method annotations, and MOS alert codes (`mos_alert_codes`) mapping the tier to the relevant MOS error code taxonomy (e.g., `P:1` for thermal protection, `R:1` for low hashrate, `V:1`/`V:2` for PSU errors).

### 6.5 Security & Safety Principles

An autonomous optimization agent controlling mining hardware introduces real risks:

**Control boundaries** — clock frequency and voltage adjustments are hard-coded as percentages of manufacturer stock settings, not learned parameters. The overclock suggestion is capped at 5% and gated on thermal headroom.

**Fail-safe defaults** — if the agent loses telemetry input (sensor failure, network partition), the system must revert to conservative stock settings, not maintain the last aggressive state.

**Audit trail** — every control action (mode change, clock adjustment) is logged with the input state that triggered it (risk score, TE_score, telemetry snapshot), the tier classification rationale, and any safety overrides applied. The workflow engine provides execution audit natively through its content-addressed execution chain.

**MOS approval gate** — in a live MOS deployment, controller commands do not execute immediately. They enter the orchestrator's multi-voter approval system (`reqVotesPos: 2`, `reqVotesNeg: 1`) — two positive votes are required to approve any write operation, and a single negative vote cancels it. The commands in `fleet_actions.json` represent recommendations entering this approval queue, providing a human-in-the-loop safety layer between the AI controller and hardware.

**Adversarial robustness** — in a production setting, telemetry data could be manipulated (compromised sensor, firmware bug). The fleet-relative z-score features provide some natural robustness — a single device reporting anomalous readings will diverge from fleet peers, but a fleet-wide sensor calibration drift would not be detected.

## 7. Report Dashboard (Task 6)

The final task produces a self-contained HTML dashboard with 7 matplotlib charts rendered as base64-embedded PNGs:

1. **TE timeseries** — fleet True Efficiency over time, 1h resampled per device
2. **TE decomposition** — stacked bar of TE_base + voltage penalty + cooling overhead per device
3. **Health scores** — heatmap of TE_score over date × device (RdYlGn colormap, vmin=0.5, vmax=1.2)
4. **Anomaly timeline** — 3-panel ground-truth anomaly labels with onset annotations
5. **Risk ranking** — horizontal bar of device mean_risk scores (red = flagged, green = healthy)
6. **Controller tiers** — per-device tier + risk bar chart alongside fleet health pie chart
7. **Feature importance** — top XGBoost feature importances

The dashboard also includes:
- Summary metrics banner (mean TE, device count, F1, critical/warning counts, worst device)
- Controller actions table with tier, risk, TE_score, commands, MOS RPC methods, MOS alert codes, and rationale per device
- Production safety note (orange callout) documenting the MOS multi-voter approval system — commands are recommendations, not immediate executions
- MOS alert code reference table (P:1, R:1, V:1, etc. with descriptions and severity)
- W/TH/s equivalence footnote after the TE decomposition chart (1 J/TH = 1 W/TH/s, aligning with MOS convention)
- Risk scores table with mean/max/pct_flagged per device
- Per-anomaly-type detection results table (F1, accuracy, test positives, top feature)
- Footer with model details, sample counts, and controller version

## 8. MOS/MDK Integration Path

### 8.1 Telemetry Mapping

The pipeline's telemetry schema maps directly onto Tether's MOS real-time fields:

| Pipeline field | MOS field | MOS source |
|---------------|-----------|------------|
| `hashrate_th` | `hashrate_5m` | Antminer/Whatsminer worker |
| `hashrate_th_mean_30m` | `hashrate_30m` | Approximated via 30-min rolling window (6 samples at 5-min interval) |
| `power_w` | `power_watts` | Antminer worker |
| `temperature_c` | `temp_chip` | Antminer worker |
| `ambient_temp_c` | `temp_ambient` | Site sensor |
| `cooling_power_w` | derived (power meter − ASIC power) | Schneider power meter worker |
| `efficiency_jth` | `efficiency_wths` | Same unit: 1 J/TH = 1 W/TH/s |

### 8.2 Command Mapping

Every command emitted by the controller is mapped to a MOS RPC method via `MOS_COMMAND_MAP` and annotated in `fleet_actions.json`:

| Controller command | MOS RPC method | Notes |
|---|---|---|
| `set_clock` | `setFrequency` | V/f coupled — voltage adjusts implicitly with frequency |
| `set_power_mode` | `setPowerMode` | `normal` / `sleep` |
| `set_fan_mode` | `setFanControl` | Air-cooled only |
| `suggest_overclock` | `setFrequency` | Higher target, conditional on thermal headroom |
| `reboot` | `reboot` | 2-3 min recovery |
| `schedule_inspection` | — | Operational, no MOS RPC equivalent |

No `set_voltage` commands are emitted — MOS does not expose direct voltage control. Voltage is managed implicitly through the ASIC firmware's V/f curve.

### 8.3 Error Code Taxonomy

Controller actions are annotated with MOS alert codes (`mos_alert_codes`) from the MOS error code taxonomy. The mapping is tier-based (approximate):

| Our anomaly type | MOS alert codes | Description |
|---|---|---|
| thermal_deg (CRITICAL) | `P:1`, `P:2` | High/low temperature protection |
| psu_instability (CRITICAL) | `V:1`, `L0:1` | Power init error, V/f exceeds limit |
| hashrate_decay (CRITICAL) | `R:1`, `J0:8` | Low hashrate, insufficient hashboards |

In production, exact code mapping would come from per-anomaly-type classifier output rather than tier-level heuristics.

### 8.4 Approval Gate

The controller's commands map to MOS's orchestrator multi-voter system (`reqVotesPos: 2`, `reqVotesNeg: 1`), which requires two positive approval votes before executing any write operation. A single negative vote cancels the action. This provides a human-in-the-loop safety layer documented in the report's production safety note.

### 8.5 Live Data Path

When MDK's adapter layer is available, the `ingest_telemetry` task can be pointed at the real-time API instead of the synthetic CSV with no changes to downstream tasks. The 30-minute hashrate rolling window in `features.py` would benefit from MOS's native `hashrate_30m` field, eliminating the approximation from 5-min samples.

See `docs/mos_platform_audit.md` for the full gap analysis and mitigation status.

## 9. Future Work

**Real data validation** — the synthetic dataset validates the pipeline architecture and model design, but the anomaly patterns are idealized. Real-world failures are messier: multiple failure modes co-occurring, sensor drift, non-stationary baselines. The per-anomaly-type classifier results (especially hashrate decay at 71% F1) indicate where the model needs real data to improve.

**Learned controller** — the current tier-based controller uses fixed thresholds and percentage-of-stock commands. The natural extension is a learned policy that uses the TE decomposition as state representation and optimizes for economic efficiency (`EE = TE × energy_price`). This could be framed as a contextual bandit problem where the state is (TE_score, ambient_temp, energy_price, tier) and the action is the operating mode — overclock, underclock, idle, or a continuous clock/voltage setpoint.

**Streaming inference** — the current `score_fleet` task processes a 24-hour batch window. In production, this should run as a streaming inference job triggered every 5 minutes on the latest telemetry, with the controller immediately emitting commands for any tier change.

**Cooling normalization calibration** — the 10% cooling overhead estimate in `compute_te_nominal` is model-agnostic. Per-model calibration from real data would improve TE_score accuracy, especially for hydro-cooled vs. air-cooled hardware.

---

**Repository**: `mining_optimization/`
**Workflow**: `mdk.fleet_intelligence`
**Dataset hash**: `45def9ee582d371261eb2178f9158f48ba487e06075449966a9efcbf8ba96e03`
