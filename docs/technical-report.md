# AI-Driven Mining Optimization & Predictive Maintenance

**Technical Report — MDK Assignment**

Wiktor Lisowski | April 2026 | Last updated: 2026-04-18

---

## 1. Problem Statement

Bitcoin mining profitability depends on marginal gains. At scale, operators manage fleets of heterogeneous ASICs (S21-HYD, M66S, S19XP, S19jPro, S19kPro, A1566) across sites where ambient conditions, energy pricing, and hardware health change continuously. Two problems dominate operational cost:

**Chip-level efficiency** — each ASIC has an optimal operating point defined by the interaction of clock frequency, core voltage, temperature, and cooling load. Currently, mode selection (overclock, underclock, idle) is done manually based on operator intuition. The standard efficiency metric (J/TH) ignores cooling overhead, voltage waste, and ambient conditions, making cross-device and cross-condition comparison unreliable.

**Predictive maintenance** — ASIC repair is the largest single cost line. Failures manifest as gradual degradation (thermal fouling, chip aging, PSU instability) that is detectable in telemetry days before critical failure, yet operators today have no systematic early-warning system.

This project addresses both problems through a three-layer architecture: a supervised ML detection layer that flags anomalies and classifies device health, an AI reasoning agent that synthesizes ML output with market data and organizational knowledge to propose specific corrective actions, and a governance layer that enforces human approval before execution.

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

The system uses a **three-layer architecture**: an ML detection layer that identifies anomalies and classifies device health, an AI reasoning layer that proposes corrective actions using multi-source context, and a governance layer that enforces human approval and audit.

### 3.1 ML Detection Layer (Composable Workflows)

The pipeline is split into composable single-concern workflows chained via `continue_from`:

```
  Shared prefix (mdk.pre_processing)
  ──────────────────────────────────

┌─────────────────────┐
│ [1] ingest_telemetry │   CSV + metadata → Parquet
│                      │   Schema validation, type coercion, dedup
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ [2] engineer_features│   75 features per device-timestep:
│     (rolling, Δ, z)  │   Rolling stats (30m/1h/12h/24h/7d), rates of change,
└──────────┬──────────┘   fleet z-scores, interaction terms
           ▼
┌─────────────────────┐
│ [3] compute_true_eff │   TE formula, decomposition, TE_score
│     (KPI engine)     │   Health score per device against nominal
└─────────────────────┘

  Training path (mdk.train)         Inference path (mdk.score)
  ─────────────────────────         ──────────────────────────

┌─────────────────────┐         ┌─────────────────────┐
│ train_anomaly_model  │         │ score_fleet          │
│ XGBoost + quantile   │         │ 24h window → risk    │
│ regressors           │         │ per-device scores    │
└─────────────────────┘         └─────────────────────┘

  Analysis (mdk.analyze)
  ──────────────────────

┌─────────────────────┐
│ analyze_trends       │   CUSUM, slope, projected crossings
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ optimize_fleet       ���   Tier classification + safety overrides
│ (deterministic)      │   CRITICAL/WARNING/DEGRADED/HEALTHY
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ generate_report      │   HTML dashboard: charts + tables
└─────────────────────┘
```

### 3.2 AI Reasoning Layer

The ML layer outputs tier classifications and safety flags — deterministic, auditable observations. The *action decisions* (what to actually do about a WARNING or CRITICAL device) are handled by a separate LLM-based reasoning agent that gathers context from three complementary information layers before proposing actions:

```
Orchestrator (post-inference cycle)
  → Notifies agent: "Cycle N complete" + pipeline references
  → AI agent wakes up, gathers three-layer context:

  1. ML Perception:
     fleet_status_query(risk_ranking) → anomaly scores, risk rankings per device

  2. Market Awareness:
     web_search("Bitcoin BTC price USD today") → current BTC price

  3. Organizational Context:
     knowledge_query("What SOP applies to thermal issues?
       Who is available for maintenance?")
     → Single-shot RAG against pre-built knowledge index
     → SOPs, team availability, hardware specs, financial constraints

  Agent synthesizes all three layers → proposes action:
     fleet_underclock(device_id="ASIC-009", target_pct=80,
       reason="BTC $72k. SOP-004 requires underclock-first.
       Efficiency 30% worse than nominal...")
  → Approval gate → operator approves/denies → execution
```

The agent never reads files directly. All data flows through the same governed API used for action proposals. Content-addressed URIs (`@hash.task:output`) resolve pipeline outputs into query containers at execution time. The `session_hash` links agent proposals to pipeline data, so both appear together in the operator dashboard.

The **organizational knowledge index** is built once via a RAG ingest pipeline from a corpus of company documents (SOPs, team roster, hardware inventory, facility specs, financial overview, vendor contacts, safety procedures). When the agent calls `knowledge_query`, a single container embeds the query, performs cosine similarity search over the pre-built index, assembles a prompt with retrieved context, and calls an LLM — all in one round-trip.

This separation is intentional:
- **ML layer**: "device X is CRITICAL with thermal degradation" (deterministic, reproducible)
- **Market layer**: "BTC at $72k, mining yield ~0.00000035 BTC/TH/day" (real-time context)
- **Organizational layer**: "SOP-004 requires underclock-first when no senior technician is on-site. Jean is on leave until May 5." (company-specific context)
- **AI agent**: synthesizes all three → "reduce clock to 80% per SOP-004 — losing 28 TH/s costs $0.71/day but saves $1.40/day in wasted power, net +$0.69/day. No qualified technician available for thermal paste work until May 5, conservative underclock appropriate."
- **Approval gate**: operator confirms or rejects with optional learned policy (auditable, traceable)

### 3.3 Governance Layer

Every action — both ML pipeline tasks and AI agent proposals — flows through the execution engine:

- **Content-addressed execution chain** — SHA-256 workflow hashes link every task output to its inputs, code, and parameters. Model artifacts are resolved via `continue_from` deep context, not filesystem paths.
- **Trust profiles** — three tiers (conservative, standard, power-user) control which actions auto-approve and which require human confirmation. Read-only commands (`fleet_status_query`, `web_search`) auto-approve in standard profile; destructive commands (`fleet_underclock`, `fleet_emergency_shutdown`) always require human confirmation.
- **Learned policies** — operators can create standing rules for recurring action patterns (e.g., "always approve underclock to 80% for this device model when risk > 0.9"), reducing approval fatigue while maintaining auditability.
- **Session unification** — the AI agent passes the pipeline's `session_hash` when submitting proposals, so agent actions and pipeline data appear under a single session in the dashboard. No separate configuration needed.
- **Full audit trail** — every proposal is logged with parameters, approval decision, execution result, and timing.

Each pipeline task runs in a Docker container. Inputs and outputs pass through a shared working directory using Parquet files (telemetry, features, KPI timeseries), JSON (metadata, risk scores, controller actions), and a joblib model artifact. Agent proposals execute in the same container infrastructure — `fleet_status_query` reads pipeline outputs inside a container, `fleet_underclock` would issue MOS RPC calls from a container.

## 4. Data Pipeline

### 4.1 Synthetic Dataset

The full training corpus is generated by composing five scenario-specific simulations via `scripts/generate_training_corpus.py --all`. Each scenario uses the shared physics engine (`scripts/physics_engine.py`) with deterministic seeds. The combined corpus spans ~1.6M rows across heterogeneous fleet sizes, durations, and anomaly mixes. The physics engine implements per-device-per-timestep simulation:

- **CMOS power model**: `P = k × V² × f + P_static(T)` — dynamic power scales quadratically with voltage; static/leakage power grows exponentially with temperature
- **Hashrate**: `H ∝ f × (1 - chip_degradation)` — linear with clock, reduced by chip failure
- **Thermal model**: exponential approach to equilibrium with thermal inertia (τ = 0.4h) and resistance that increases with fouling
- **Cooling**: proportional controller with setpoint at 65°C, fouling increases cooling power by up to 50%
- **Operating modes**: rule-based mode selection (overclock/underclock/idle based on energy price and ambient temperature)
- **Environment**: sinusoidal ambient temperature (seasonal + diurnal, 64.5°N latitude), time-of-use energy pricing

Hardware models supported (heterogeneous fleet, spanning multiple ASIC generations):

| Model | Nominal Hashrate | Nominal Power | Efficiency |
|-------|-----------------|---------------|------------|
| S21-HYD | 335 TH/s | 5025 W | 15.0 J/TH |
| M66S | 298 TH/s | 5370 W | 18.0 J/TH |
| S19XP | 141 TH/s | 3010 W | 21.3 J/TH |
| S19jPro | 104 TH/s | 3068 W | 29.5 J/TH |
| S19kPro | 120 TH/s | 2760 W | 23.0 J/TH |
| A1566 | 185 TH/s | 3420 W | 18.5 J/TH |

Five training scenarios compose the corpus, each testing different failure modes:

| Scenario | Fleet | Duration | Anomaly types injected |
|----------|-------|----------|----------------------|
| baseline | 10 devices (S21/M66/S19XP/S19j) | 30 days | None (healthy reference) |
| summer_heatwave | 12 devices | 90 days | thermal_deg, dust_fouling, thermal_paste_deg |
| psu_degradation | 10 devices | 60 days | psu_instability, capacitor_aging |
| cooling_failure | 12 devices | 90 days | thermal_deg, coolant_loop_fouling, fan_bearing_wear |
| asic_aging | 15 devices (older-gen) | 180 days | hashrate_decay, solder_joint_fatigue, firmware_cliff, capacitor_aging |

Ten anomaly types are supported by the physics engine, each with a distinct physical mechanism and telemetry signature:

| Anomaly type | Physical mechanism | Primary TE component |
|---|---|---|
| thermal_deg | Rising thermal resistance (fouling) | R_cool |
| psu_instability | Voltage noise / ripple | 1/η_v |
| hashrate_decay | Partial ASIC chip failure | TE_base |
| fan_bearing_wear | Reduced airflow, vibration | R_cool |
| capacitor_aging | PSU output degradation | 1/η_v |
| dust_fouling | Heatsink obstruction | R_cool |
| thermal_paste_deg | Increased die-to-heatsink resistance | R_cool |
| solder_joint_fatigue | Intermittent chip contact | TE_base |
| coolant_loop_fouling | Reduced coolant flow (hydro) | R_cool |
| firmware_cliff | Sudden performance drop (bug) | TE_base |

### 4.2 Feature Engineering

The feature engineering strategy follows directly from the TE decomposition. Rather than feeding raw telemetry into the model, we engineer features that isolate the physical mechanisms each TE component exposes: rolling statistics capture gradual degradation at multiple timescales (30m to 7d), fleet-relative z-scores detect single-device deviation against fleet peers, rates of change flag sudden shifts, and interaction terms encode physics relationships (power per GHz, thermal headroom, cooling effectiveness).

75 features are computed per device-timestep by `features.py`. The classifier trains on 50 of these — the remaining 25 are either intermediate computations (rolling stds used to derive z-scores), redundant windows (12h means when 1h and 24h already capture short/long baselines), or reserved for future model iterations (30m hashrate). Additionally, 6 TE decomposition features from `kpi.py` and 8 raw sensor passthroughs bring the classifier input to 50. The quantile regressor adds 8 autoregressive temporal features (lagged TE scores, trend slopes, volatility) for a total of 58. The full feature catalog is in [`feature-catalog.md`](feature-catalog.md).

## 5. Results

### 5.1 Anomaly Detection

XGBoost classifier (`n_estimators=200`, `max_depth=6`, class imbalance handled via `scale_pos_weight`). Training uses 100% of the multi-scenario corpus (~1.6M rows) — there is no internal train/test split. Evaluation happens at inference time: the trained model scores each scenario independently via a 24-hour sliding window, and results are compared against ground-truth labels. This ensures full anomaly coverage during training and evaluation on truly unseen temporal windows.

**Classifier threshold**: 0.3 (biased toward recall — in mining, a missed failure costs far more than an unnecessary inspection). Full threshold analysis in `docs/evaluation-analysis.md`.

Device-level evaluation across all non-baseline scenarios (threshold 0.3):

| Metric | Value |
|--------|-------|
| True Positives | 23 |
| False Positives | 4 |
| True Negatives | 16 |
| False Negatives | 3 |
| Recall | 88% |
| Precision | 85% |

Per-scenario F1 scores (threshold 0.3):

| Scenario | Precision | Recall | F1 |
|----------|-----------|--------|-----|
| summer_heatwave | 1.00 | 0.67 | 0.80 |
| psu_degradation | 0.67 | 1.00 | 0.80 |
| cooling_failure | 0.83 | 0.83 | 0.83 |
| asic_aging | 0.83 | 1.00 | 0.91 |

### 5.2 Fleet Scoring

The scoring task loads the pre-trained model, selects the last 24-hour telemetry window, and produces per-device risk scores with anomaly probabilities. Healthy devices consistently score below the 0.3 threshold; anomalous devices are flagged with probabilities reflecting degradation severity.

### 5.3 Per-Anomaly-Type Feature Importance

Separate classifiers are trained per anomaly type on the full corpus to identify which features are most discriminative for each failure mode. These per-type models are informational — the primary aggregate classifier is what `score.py` uses for inference. Per-type top features are saved in `model_metrics.json` → `per_anomaly_type` → `top_features`.

The TE decomposition makes feature-to-failure-mode mapping structurally predictable from the physics:

| Anomaly type | Expected primary feature | Physical mechanism |
|---|---|---|
| thermal_deg | `temperature_c_fleet_z` | Fouled device runs hotter than fleet peers |
| psu_instability | `voltage_v_std_1h` | Voltage ripple shows as short-term variance |
| hashrate_decay | `hashrate_th_fleet_z` | Degraded chips produce less hash than peers |
| fan_bearing_wear | `cooling_power_w_mean_1h` | Failing bearings increase cooling power draw |
| capacitor_aging | `voltage_v_dev_24h` | Aging capacitors cause slow voltage drift |
| dust_fouling | `temperature_c_mean_24h` | Dust accumulation raises sustained temperatures |

The weakest detection signal is `dust_fouling` (probability barely above healthy devices at threshold 0.3) — this is expected, as dust accumulation manifests similarly to normal ambient temperature variation. Fixing this requires feature engineering (e.g., ambient-conditioned thermal resistance trends), not threshold tuning.

### 5.4 Top Predictive Features

The aggregate model's feature importance confirms the TE decomposition thesis:

1. `true_efficiency` (31.5%) — the composite KPI itself is the strongest signal
2. `voltage_v_std_1h` (15.8%) — PSU instability marker
3. `efficiency_jth_fleet_z` (11.5%) — fleet-relative efficiency deviation
4. `hashrate_th_fleet_z` (6.7%) — fleet-relative hashrate deviation
5. `temperature_c_mean_24h` (5.2%) — thermal baseline drift

The TE decomposition factors collectively account for significant predictive power, validating the KPI design as both an operational metric and a feature engineering strategy.

## 6. Tier Classification & Safety Overrides

The pipeline includes a deterministic classification stage that translates model risk scores into severity tiers and safety flags.

### 6.1 Tier-Based Classification

Each device is assigned a severity tier based on its risk score and health:

| Tier | Condition | Response |
|------|-----------|----------|
| CRITICAL | mean_risk > 0.9 | Clock → 70% stock, immediate inspection, 60s monitoring |
| WARNING | mean_risk > 0.5 | Clock → 85% stock, next-window inspection, 120s monitoring |
| DEGRADED | te_score < 0.8 and risk ≤ 0.5 | Reset frequency to stock, 180s monitoring |
| HEALTHY | otherwise | Hold settings; suggest 5% overclock if thermal headroom > 10°C |

These are deterministic flags — they describe the *observed state*, not the *recommended action*. The AI reasoning agent reads these tiers alongside market data and organizational context to propose specific corrective actions.

### 6.2 Safety Overrides

Four hard safety constraints are evaluated **before** tier logic and supersede any tier-based or agent-proposed command:

- **Thermal hard limit**: `T > 80°C` → force clock to 80% stock. Aligns with MOS PCB critical threshold.
- **Thermal emergency low**: `T < 10°C` → sleep mode + immediate inspection. At the hydro-cooled northern site (64.5°N), sub-10°C temperatures risk coolant viscosity spikes and PCB condensation.
- **Thermal low warning**: `10°C ≤ T < 20°C` → clock to 70% stock. For air-cooled models, fan set to minimum to retain heat.
- **Overvoltage protection**: `V > 110% stock` → reset frequency to stock. MOS does not expose direct voltage control — voltage is coupled to frequency via the ASIC's V/f curve.

### 6.3 Security & Safety Considerations

An autonomous optimization agent that can underclock, overclock, or shut down mining hardware introduces risks that do not exist in a passive monitoring system. The core danger is that an AI agent — whether through model error, stale data, prompt injection, or adversarial input — could issue commands that damage hardware, waste energy, or halt revenue. The system addresses this through defense-in-depth: multiple independent safety layers, any one of which is sufficient to prevent harm.

**1. Separation of perception, reasoning, and action.** No single component can observe a problem and act on it unilaterally. The ML layer only classifies (deterministic, no side effects). The AI agent only proposes (it cannot execute). The governance layer only approves or denies. A failure in any one layer — a false positive from the classifier, a hallucinated recommendation from the LLM, a misconfigured approval rule — is caught by the others.

**2. Fail-closed design.** Every proposal has a timeout. If the approval gate receives no human response, the proposal is denied — not approved. If telemetry input is missing (sensor failure, network partition), the pipeline cannot compute features, so no tier classification is produced and no action is proposed. The system fails toward inaction, not toward intervention. This is the opposite of a fail-open design where loss of oversight leads to autonomous execution.

**3. Hard-coded physical limits.** Clock frequency adjustments are expressed as percentages of manufacturer stock settings, not learned parameters. The safety overrides (§6.2) are deterministic thresholds applied *before* any ML or AI reasoning — they cannot be overridden by model output or agent proposals. The overclock suggestion is capped at 5% and gated on thermal headroom > 10°C. These bounds exist in code, not in prompts.

**4. Container isolation.** Every pipeline task and every agent action executes inside an isolated Docker container with no access to the host filesystem, other containers, or the network (except explicit egress allowlists — e.g., `api.openai.com:443` for RAG queries). API keys are injected at runtime via a secret store, never embedded in code or images. A compromised task container cannot escalate to other system components.

**5. Content-addressed audit chain.** Every workflow execution is identified by a SHA-256 hash computed over its code, parameters, and input data. Every task output is linked to this hash. Every agent proposal is logged with the input state that triggered it (risk scores, TE decomposition, telemetry snapshot), the approval decision, and the execution result. This chain is tamper-evident: changing any input retroactively would produce a different hash, breaking the chain. In MOS integration, this connects to the multi-voter approval system (`reqVotesPos: 2`, `reqVotesNeg: 1`).

**6. Rate limiting and learned policies.** The governance layer enforces per-session rate limits on proposals (e.g., 100 proposals per session for read-only queries, 10 for control actions). Operators can create standing policies for recurring patterns (e.g., "always approve underclock to 80% for S19jPro when risk > 0.9"), reducing approval fatigue while maintaining auditability. Policies are stored in a database, not in agent memory — the agent cannot create or modify its own approval rules.

**7. Adversarial robustness.** Fleet-relative z-score features provide natural resistance to single-device sensor manipulation: a compromised device reporting falsified telemetry will diverge from fleet peers, triggering anomaly detection rather than evading it. The limitation is fleet-wide drift (e.g., systematic sensor calibration error), which would not be detected by relative features alone.

## 7. Growing-Window Simulation & End-to-End Demonstration

### 7.1 Simulation Architecture

The system supports continuous simulation via a growing-window inference loop (`scripts/orchestrate_simulation.py`). This mirrors real-world telemetry accumulation:

**Phase 1 — Data generation**: The physics engine generates all scenario data upfront as a single time series covering the full scenario duration (e.g., 90 days for `summer_heatwave`).

**Phase 2 — Growing-window inference**: Each cycle advances a cutoff timestamp by the configured interval (default: 1 day). The pre-processing task ingests all data up to the cutoff, so rolling windows (30m, 1h, 12h, 24h, 7d) are always fully populated — matching the feature distribution the model was trained on. This avoids the truncated-window problem that would occur with batch-only inference.

```
Cycle 1: [day 0 ──── day 1]           → score → analyze → agent
Cycle 2: [day 0 ──────── day 2]       → score → analyze → agent
Cycle 3: [day 0 ──────────── day 3]   → score → analyze → agent
  ...
Cycle N: [day 0 ─────────────── day N] → score → analyze → agent
```

After each inference cycle, if the gateway URL is configured, the orchestrator pushes a notification to the AI agent with the pipeline session hash and `input_files` references. The agent then follows its reasoning protocol to propose fleet actions.

### 7.2 End-to-End Flow (Demonstrated)

The full pipeline was demonstrated end-to-end on April 10, 2026, using the `summer_heatwave` scenario (12 devices, 90 days, thermal degradation + dust fouling + thermal paste degradation):

1. **Pipeline execution** — `orchestrate_simulation.py` runs Phase 1 (data generation) then iterates Phase 2 cycles. Each cycle triggers `mdk.pre_processing` → `mdk.score` → `mdk.analyze` inside Docker containers via the workflow engine.

2. **Agent activation** — After each cycle, the orchestrator pushes to the agent via `POST /hooks/agent`. The agent gathers three-layer context: fleet risk scores via `fleet_status_query`, current BTC price via `web_search`, and organizational knowledge via `knowledge_query` (SOPs, team availability, hardware specs).

3. **Contextual reasoning** — For each flagged device, the agent composes a cost-benefit analysis incorporating: current BTC price and mining revenue, device-specific efficiency loss vs nominal, estimated power savings from underclocking, hardware replacement cost and lifetime risk, applicable SOPs and maintenance procedures, and team availability. Example reasoning from a live proposal:

   > *"BTC $97,200. ASIC-004 at 67.6°C, efficiency 27% worse than nominal (27.1 vs 21.3 J/TH). SOP-004 prescribes underclock-first for thermal degradation before scheduling maintenance. Underclocking to 90% reduces revenue by ~$0.35/day but saves ~$0.84/day in wasted power. Net benefit: +$0.49/day + extended hardware life. Maintenance window: schedule after Jean returns from leave (May 5)."*

4. **Proposal submission** — The agent submits `fleet_underclock` or `fleet_schedule_maintenance` proposals via the governed API, using the pipeline's session hash so proposals appear in the unified dashboard.

5. **Operator approval** — Proposals appear in the dashboard with status badges, reasoning text, and approve/deny buttons. The operator reviews the AI's economic justification and approves or rejects.

6. **Execution** — Approved proposals execute inside Docker containers. The execution result (success/failure, output) is recorded in the audit trail.

This demonstrates the full three-layer architecture: deterministic ML detection feeding contextual AI reasoning, with human-in-the-loop governance at every step.

## 8. Future Work

- **Real data validation** — the synthetic dataset validates pipeline architecture and model design, but real-world failures are messier (co-occurring modes, sensor drift, non-stationary baselines). The weak `dust_fouling` detection signal indicates where real data and better feature engineering are most needed.
- **MOS RPC integration** — the `fleet_underclock` template currently executes in a container that logs the intended MOS command. Connecting to the actual MOS API (`setFrequency`, `setMode`) requires TLS client certificates and the MOS gateway endpoint — a deployment configuration step, not an architecture change.
- **Streaming inference** — move from 24-hour batch scoring to 5-minute streaming inference with immediate tier reclassification on state changes. The growing-window architecture already handles accumulating history; the bottleneck is inference cycle time (~2 min per cycle), not architecture.
- **Incremental features** — compute rolling features incrementally over a sliding window rather than recomputing on the full history, reducing O(n) feature engineering to O(1) per tick.
- **Approval UX** — inline approval buttons in messaging channels (currently text-based commands). Session-based deduplication to prevent the agent from re-proposing actions already pending approval.
- **Knowledge query provenance** — decompose the monolithic knowledge query (embed → search → LLM in one process) into a multi-task workflow to gain per-step provenance, auditable intermediate artifacts, and a hash chain for tracing which knowledge chunks influenced a fleet control decision.

---

**Repository**: `mining_optimization/`
**Workflows**: `mdk.pre_processing`, `mdk.train`, `mdk.score`, `mdk.analyze`, `mdk.generate_corpus`, `mdk.generate_batch`, `mdk.fleet_simulation`
**Orchestrators**: `orchestrate_training.py`, `orchestrate_inference.py`, `orchestrate_simulation.py`
