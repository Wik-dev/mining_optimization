# Architecture Diagram — Fleet Intelligence Pipeline

## System Overview

Four-layer architecture: **Tasks** (pure computation) are wrapped by **Scripts** (standalone execution), chained by **Orchestrators** (workflow sequencing), and declared as **Validance Pipelines** (engine-managed DAGs). Above the ML pipeline, an **AI Reasoning** layer reads pipeline data, market context, and organizational knowledge via SafeClaw and proposes actions through a **Governance** gate.

---

## Layer 0 — Shared Physics Library

```
┌─────────────────────────────────────────────────────────────────────────┐
│  physics_engine.py                                                      │
│                                                                         │
│  Device Models (10 ASICs)     Anomaly Models (10 types)     Simulation  │
│  ├─ S21-HYD, M66S, S19XP     ├─ thermal_degradation        ├─ tick()   │
│  ├─ S19jPro, S19kPro, A1566  ├─ psu_instability            ├─ emit()   │
│  └─ stock V/f/H/P specs      ├─ hashrate_decay             └─ state    │
│                               ├─ fan_bearing_wear                      │
│  Site Conditions              ├─ capacitor_aging            Scenarios   │
│  ├─ ambient_temperature()     ├─ dust_fouling               ├─ JSON     │
│  └─ energy_price()            ├─ thermal_paste_degradation  └─ loader   │
│                               ├─ solder_joint_fatigue                  │
│                               ├─ coolant_loop_fouling                  │
│                               └─ firmware_cliff                        │
│                                                                         │
│  Imported by: generate_training_corpus.py, simulation_engine.py,        │
│               simulation_loop.py, conftest.py                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Tasks (pure computation, run inside containers)

Each task reads inputs from `/work/`, writes outputs to `/work/`, and emits `_validance_vars.json`.
Baked into the `mdk-fleet-intelligence` Docker image at `/app/tasks/`.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PIPELINE TASKS (DAG steps — chained by workflows)                      │
│                                                                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │ ingest.py        │  │ features.py      │  │ kpi.py                  │ │
│  │                  │  │                  │  │                         │ │
│  │ CSV → Parquet    │─→│ 75 engineered    │─→│ True Efficiency (TE)    │ │
│  │ schema validate  │  │ features: roll,  │  │ = (P_asic + P_cool) /  │ │
│  │ dedup, type cast │  │ rate, z-score,   │  │   (H × η_v)            │ │
│  │                  │  │ interactions     │  │ decomposition + health  │ │
│  │ → telemetry.pqt  │  │ → features.pqt   │  │ → kpi_timeseries.pqt   │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
│                                                                         │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │ train_model.py               │  │ score.py                         │ │
│  │                              │  │                                  │ │
│  │ XGBoost binary classifier    │  │ 24h sliding window per device    │ │
│  │ + 10 per-anomaly classifiers │  │ risk aggregates: mean/max/pct    │ │
│  │ + 12 quantile regressors     │  │ + horizon predictions (p10/50/   │ │
│  │   (4 horizons × 3 quantiles) │  │   90) at t+1h/6h/24h/7d         │ │
│  │                              │  │ threshold crossing analysis      │ │
│  │ → anomaly_model.joblib       │  │                                  │ │
│  │ → regression_model_vN.joblib │  │ → fleet_risk_scores.json         │ │
│  │ → model_metrics.json         │  │                                  │ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
│                                                                         │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │ trend_analysis.py            │  │ optimize.py                      │ │
│  │                              │  │                                  │ │
│  │ OLS slopes (1h/6h/24h/7d)   │  │ Safety overrides first:          │ │
│  │ EWMA temperature trends     │  │   80°C thermal, 10°C emergency,  │ │
│  │ CUSUM regime change detect   │  │   110% overvoltage               │ │
│  │ forward projections to 0.8/  │  │ Tier classify: CRITICAL →        │ │
│  │   0.6 TE thresholds          │  │   WARNING → DEGRADED → HEALTHY  │ │
│  │ direction classification     │  │ MOS commands: setFrequency,      │ │
│  │                              │  │   setPowerMode, setFanControl    │ │
│  │ → trend_analysis.json        │  │ fleet redundancy constraint      │ │
│  │                              │  │                                  │ │
│  │                              │  │ → fleet_actions.json             │ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
│                                                                         │
│  ┌──────────────────────────────┐                                       │
│  │ report.py                    │                                       │
│  │                              │                                       │
│  │ Self-contained HTML with     │                                       │
│  │ matplotlib charts as base64  │                                       │
│  │ TE timeseries, decomposition │                                       │
│  │ health scores, anomaly       │                                       │
│  │ timeline, risk ranking       │                                       │
│  │                              │                                       │
│  │ → report.html                │                                       │
│  └──────────────────────────────┘                                       │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  CATALOG TASKS (proposal pipeline — invoked on-demand, not in DAG)      │
│  Baked into lightweight fleet-control image (stdlib only, ~50 MB)       │
│                                                                         │
│  ┌──────────────────────────────┐  ┌──────────────────────────────────┐ │
│  │ fleet_status.py              │  │ control_action.py                │ │
│  │                              │  │                                  │ │
│  │ Read-only fleet queries:     │  │ Validated fleet commands:        │ │
│  │ · summary                    │  │ · fleet_underclock               │ │
│  │ · device_detail              │  │ · fleet_schedule_maintenance     │ │
│  │ · tier_breakdown             │  │ · fleet_emergency_shutdown       │ │
│  │ · risk_ranking               │  │                                  │ │
│  │                              │  │ Constraints enforced:            │ │
│  │ Reads /work/fleet/ outputs   │  │ · min underclock 50%             │ │
│  │ No ML deps — fast start      │  │ · fleet hashrate floor 70%      │ │
│  │                              │  │ · max 20% offline (redundancy)   │ │
│  │                              │  │ → agent_actions.json audit log   │ │
│  └──────────────────────────────┘  └──────────────────────────────────┘ │
│                                                                         │
│  ┌──────────────────────────────┐                                       │
│  │ knowledge_query.py           │  Baked into rag-tasks image (~120 MB) │
│  │                              │                                       │
│  │ Single-shot RAG pipeline:    │                                       │
│  │ · Embed query (OpenAI)       │                                       │
│  │ · Cosine similarity search   │                                       │
│  │   over pre-built index       │                                       │
│  │ · Assemble prompt + LLM call │                                       │
│  │                              │                                       │
│  │ Reads /work/index.json       │                                       │
│  │ (staged from rag.ingest)     │                                       │
│  │ → JSON: answer + sources     │                                       │
│  └──────────────────────────────┘                                       │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  STANDALONE MODULE (not yet wired into DAG)                             │
│                                                                         │
│  ┌──────────────────────────────┐                                       │
│  │ retrain_monitor.py           │                                       │
│  │                              │                                       │
│  │ 3 retrain triggers:          │                                       │
│  │ · rolling RMSE drift         │                                       │
│  │ · calibration drift (p10-90) │                                       │
│  │ · fleet regime shift (KS)    │                                       │
│  │ → retrain_decision.json      │                                       │
│  └──────────────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 2 — Scripts (standalone execution)

Scripts that run directly on the host or inside a container. They call into `physics_engine.py` or task modules.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SCRIPTS                                                                │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ generate_training_corpus.py                                        │ │
│  │                                                                    │ │
│  │ Combines 5 scenario JSONs into multi-scenario training datasets    │ │
│  │ Uses physics_engine.py for tick-by-tick generation                 │ │
│  │ --all mode: composes baseline + heatwave + aging + PSU + cooling   │ │
│  │                                                                    │ │
│  │ → training_telemetry.csv / .parquet                                │ │
│  │ → training_metadata.json                                           │ │
│  │ → training_labels.csv                                              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ simulation_engine.py                                               │ │
│  │                                                                    │ │
│  │ Tick-by-tick stateful fleet simulator with speed control            │ │
│  │ Modes: real-time (5min/tick), accelerated (Nx), offline (max)      │ │
│  │ Same physics + telemetry schema as corpus generator                │ │
│  │ State serialization: save_state() / from_state() for cross-       │ │
│  │ invocation continuity across separate task executions              │ │
│  │                                                                    │ │
│  │ → fleet_telemetry.csv (streaming)                                  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ generate_batch.py                                                  │ │
│  │                                                                    │ │
│  │ Single-interval batch generator (standalone, no Validance dep)     │ │
│  │ Reads scenario JSON + optional sim_state.json (state continuity)   │ │
│  │ Used by tasks/generate_batch.py and standalone execution           │ │
│  │                                                                    │ │
│  │ → batch_telemetry.csv, batch_metadata.json, sim_state.json        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ evaluate_predictions.py                                            │ │
│  │                                                                    │ │
│  │ Offline model quality evaluation against ground truth              │ │
│  │ Classifier: accuracy, precision, recall, F1 per device             │ │
│  │ Regressors: RMSE, MAE, calibration coverage per horizon            │ │
│  │ Not in the live pipeline — used for development validation         │ │
│  │                                                                    │ │
│  │ → evaluation_report.json                                           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 3 — Orchestration Scripts (workflow chaining)

Host-side scripts that trigger sequences of Validance workflow runs via REST API.
They poll for completion and pass output URIs between steps via `continue_from`.

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATION SCRIPTS                                                  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ orchestrate_training.py                                            │ │
│  │                                                                    │ │
│  │ Training chain (single session_hash, POLL_TIMEOUT=3600s):          │ │
│  │                                                                    │ │
│  │   mdk.generate_corpus ──→ mdk.pre_processing ──→ mdk.train        │ │
│  │          │                    continue_from ↗       continue_from ↗ │ │
│  │          │  (skippable with --telemetry-csv / --metadata-json)      │ │
│  │          ↓                                                         │ │
│  │   training_telemetry.csv ──→ features.pqt ──→ anomaly_model.joblib │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ orchestrate_inference.py                                           │ │
│  │                                                                    │ │
│  │ Inference chain (single session_hash, POLL_TIMEOUT=1800s):         │ │
│  │                                                                    │ │
│  │   mdk.pre_processing ──→ mdk.score ──→ mdk.analyze                │ │
│  │   continue_from=training ↗  continue_from ↗                        │ │
│  │                                                                    │ │
│  │   Model artifacts resolved via deep context (continue_from chain   │ │
│  │   walks back to training hash → @train_anomaly_model:* refs)       │ │
│  │                                                                    │ │
│  │   telemetry.csv ──→ risk_scores.json ──→ fleet_actions.json        │ │
│  │                                          + report.html             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ write_fleet_summary.py  (LEGACY — no longer in critical path)       │ │
│  │                                                                    │ │
│  │ Previously bridged ML output → AI agent via workspace files.      │ │
│  │ Replaced by: orchestrator push + SafeClaw fleet_status_query.     │ │
│  │ Agent now reads Validance artifacts directly — no filesystem       │ │
│  │ bridge needed. Kept for standalone inference (orchestrate_         │ │
│  │ inference.py) but not used in simulation flow.                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ orchestrate_simulation.py  (growing-window, Pattern 1 / 5a)        │ │
│  │                                                                    │ │
│  │ Two-phase growing-window simulation (requires pre-trained model): │ │
│  │                                                                    │ │
│  │   Phase 1: mdk.generate_batch(full scenario) → single CSV         │ │
│  │   Phase 2: For each cycle (1 per simulated day):                   │ │
│  │     mdk.pre_processing(cutoff=day N) → mdk.score → mdk.analyze   │ │
│  │     cutoff grows: each cycle sees all history [t=0 → t=cutoff]    │ │
│  │                                                                    │ │
│  │   Post-cycle: POST /hooks/agent → OpenClaw gateway (:19001)       │ │
│  │     Pure orchestration — sends hashes + input_files refs only.     │ │
│  │     Agent reads data via SafeClaw fleet_status_query.              │ │
│  │                                                                    │ │
│  │   Supports CLI (Pattern 1) and container (Pattern 5a) invocation  │ │
│  │   Retry: 3 attempts (5s → 15s → 45s exponential backoff)          │ │
│  │   Circuit breaker: 3 consecutive failures → 60s pause              │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Layer 4 — Validance Pipelines (engine-managed workflow DAGs)

Workflow definitions using the Validance SDK. Registered to the engine via REST API.
All tasks run in containers from `mdk-fleet-intelligence:latest` (or `fleet-control:latest` for catalog tasks).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  WORKFLOW DEFINITIONS  (workflows/fleet_intelligence.py)                 │
│                                                                         │
│  ┌─ mdk.pre_processing (3 tasks) ─────────────────────────────────────┐ │
│  │  ingest_telemetry → engineer_features → compute_true_efficiency    │ │
│  │  Shared prefix for both training and inference paths               │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ mdk.generate_corpus (1 task) ─────────────────────────────────────┐ │
│  │  generate_training_data                                            │ │
│  │  Synthetic multi-scenario corpus generation                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ mdk.train (1 task) ───────────────────────────────────────────────┐ │
│  │  train_anomaly_model                                               │ │
│  │  Chained from pre_processing via continue_from                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ mdk.score (1 task) ───────────────────────────────────────────────┐ │
│  │  score_fleet                                                       │ │
│  │  Chained from pre_processing; model resolved via deep context      │ │
│  │  (@train_anomaly_model:model_artifact from continue_from chain)    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ mdk.analyze (3 tasks) ────────────────────────────────────────────┐ │
│  │  analyze_trends → optimize_fleet → generate_report                 │ │
│  │  Post-scoring analysis, tier classification, HTML dashboard        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│                                                                         │
│  ┌─ mdk.generate_batch (1 task) ────────────────────────────────────-┐ │
│  │  generate_batch (stateful simulation batch generation)            │ │
│  │  Called once per simulation (full scenario data, one-shot)        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ mdk.fleet_simulation (1 task, Pattern 5a) ─────────────────────-┐ │
│  │  simulation_orchestrator — ephemeral wrapper                      │ │
│  │  Runs orchestrate_simulation.py inside container                  │ │
│  │  Triggers: generate_batch(full) →                                 │ │
│  │    [pre_processing(cutoff) → score → analyze] × N cycles         │ │
│  │  UI-triggerable via POST /api/workflows/mdk.fleet_simulation/...  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─ rag.ingest (5 tasks) ────────────────────────────────────────────┐ │
│  │  load_documents → chunk_documents → embed_chunks → build_index   │ │
│  │    → build_receipt                                                │ │
│  │  Builds vector index from organizational knowledge corpus         │ │
│  │  (SOPs, team roster, hardware inventory, facility specs, etc.)   │ │
│  │  Output: index.json referenced as @<hash>.build_index:result      │ │
│  │  Run once per corpus update; agent references index via hash      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  REGISTRATION  (scripts/register_validance_workflows.py)                │
│                                                                         │
│  Imports WORKFLOWS dict → converts SDK objects to JSON → POSTs to      │
│  /api/workflows. Registers all 7 composable workflows.                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## How Layers Compose

```
                        LAYER 4: Validance Pipelines
                        (engine-managed, REST API)
                        ┌─────────────────────────┐
  Training path:        │ mdk.generate_corpus      │
  orchestrate_          │       ↓ continue_from     │
  training.py ────────→ │ mdk.pre_processing       │
  (Layer 3)             │       ↓ continue_from     │
                        │ mdk.train                │
                        └─────────────────────────┘

                        ┌─────────────────────────┐
  Inference path:       │ mdk.pre_processing       │
  orchestrate_          │       ↓ continue_from     │
  inference.py ───────→ │ mdk.score                │
  (Layer 3)             │       ↓ continue_from     │
                        │ mdk.analyze              │
                        └─────────────────────────┘

                        ┌─────────────────────────┐
  Simulation:           │ mdk.fleet_simulation     │  ← Pattern 5a wrapper
  Dashboard or CLI      │   ↓ (inside container)   │     (UI-triggerable)
  triggers ───────────→ │ mdk.generate_batch(full) │
                        │   ↓                       │
                        │ [mdk.pre_processing       │  ← growing-window:
                        │     (cutoff=day N)        │     each cycle sees
                        │   ↓ continue_from         │     [t=0 → t=cutoff]
                        │  mdk.score                │
                        │   ↓ continue_from         │
                        │  mdk.analyze              │
                        │   ↓                       │
                        │  POST /hooks/agent        │  ← pure orchestration:
                        │  (hashes + input_files    │     just notify, no
                        │   refs, session_hash)     │     data parsing
                        │ ] × N cycles              │
                        └────────────┬────────────┘
                                     │ Requires pre-trained model.
                                     │
                                     ↓
                        ┌─────────────────────────┐
                        │ OpenClaw Agent (:19001)  │
                        │                          │  Agent receives cycle
                        │ Reads pipeline data via  │  notification with hashes,
                        │ safeclaw():              │  then autonomously:
                        │ · fleet_status_query     │
                        │   (reads Validance       │  1. Queries fleet risk data
                        │    artifacts directly)   │  2. Searches BTC price
                        │ · web_search (BTC price) │  3. Queries org knowledge
                        │ · knowledge_query        │     (SOPs, team, hardware)
                        │   (organizational RAG —  │  4. Reasons about economics
                        │    SOPs, team roster,    │     + operational context
                        │    hardware specs,       │  5. Proposes fleet actions
                        │    facility constraints) │     with justification
                        │                          │
                        │ Proposes via safeclaw(): │  session_hash from notification
                        │ · fleet_underclock       │  → proposals land under same
                        │ · fleet_schedule_maint   │    session as pipeline data
                        │ · fleet_emergency_shut   │
                        └────────────┬────────────┘
                                     │
                                     ↓
                        Validance Governance Layer
                        (approval gate, audit, policies)

  Each workflow step runs a Layer 1 task inside a container.
  Layer 0 (physics_engine) is imported by Layer 2 scripts.
  Layer 2 (generate_batch.py, simulation_loop.py --offline) run standalone.
  AI agent reads Validance artifacts via SafeClaw — no filesystem bridge.
```

---

## End-to-End Data Flow

```
  ┌──────────────────────────┐
  │     HARDWARE LAYER       │
  │                          │
  │  ASIC Fleet (10 models)  │
  │  Site Sensors            │
  │  Cooling Systems         │
  └────────────┬─────────────┘
               │ raw telemetry
               ↓
  ┌──────────────────────────┐     ┌─────────────────────────────────┐
  │   ML DETECTION LAYER     │     │  Artifacts                      │
  │                          │     │                                 │
  │  ingest_telemetry ───────│────→│  telemetry.parquet              │
  │        ↓                 │     │                                 │
  │  engineer_features ──────│────→│  features.parquet (75 features) │
  │        ↓                 │     │                                 │
  │  compute_true_efficiency─│────→│  kpi_timeseries.parquet         │
  │        ↓                 │     │                                 │
  │  ┌─ train_anomaly_model─ │────→│  anomaly_model.joblib           │
  │  │       ↓  (model)      │     │  regression_model_vN.joblib     │
  │  └→ score_fleet ─────────│────→│  fleet_risk_scores.json         │
  │        ↓                 │     │                                 │
  │  analyze_trends ─────────│────→│  trend_analysis.json            │
  │        ↓                 │     │                                 │
  │  optimize_fleet ─────────│────→│  fleet_actions.json             │
  │        ↓                 │     │  (tiers + safety flags + cmds)  │
  │  generate_report ────────│────→│  report.html                    │
  └──────────┬───────────────┘     └─────────────────────────────────┘
             │ fleet_risk_scores.json + fleet_actions.json
             │ (stored as Validance artifacts, accessible via API)
             ↓
  ┌──────────────────────────┐    ┌────────────────────────────────┐
  │  AI REASONING LAYER      │    │  Trigger: orchestrator push     │
  │  (SafeClaw / LLM Agent)  │    │                                │
  │                          │    │  POST /hooks/agent with:        │
  │  Three-layer context:    │    │  · session_hash                 │
  │                          │←───│  · input_files refs             │
  │  · fleet_status_query    │    │    (@hash.task:artifact)        │
  │    (risk_ranking,        │    │                                │
  │     device_detail —      │    │  Pure orchestration — no data   │
  │     reads Validance      │    │  parsing, just hashes.          │
  │     artifacts directly)  │    │  Agent reads data via SafeClaw. │
  │  · web_search            │    └────────────────────────────────┘
  │    (BTC price for        │
  │     economic reasoning)  │    Dashboard (fleet-health-monitor)
  │  · knowledge_query       │    shows both ML commands and AI
  │    (organizational RAG:  │    proposals under same session.
  │     SOPs, team roster,   │
  │     hardware inventory,  │    session_hash override: agent
  │     facility specs,      │    passes pipeline session_hash
  │     financial data)      │    from notification → proposals
  │  · HEARTBEAT.md          │    land alongside pipeline data.
  │    (agent instructions)  │
  │                          │
  │  Proposes via safeclaw():│
  │  · fleet_underclock      │
  │  · fleet_schedule_maint  │
  │  · fleet_emergency_shut  │
  │  caller_id: "safeclaw"   │
  └──────────┬───────────────┘
             │ proposed commands
             ↓
  ┌──────────────────────────┐
  │  GOVERNANCE LAYER        │
  │  (Validance)             │
  │                          │
  │  · Approval Gate         │
  │    (human review)        │
  │  · Learned Policies      │
  │    (rate limits, budget)  │
  │  · Audit Trail           │
  │    (content-addressed,   │
  │     caller_id attributed)│
  └──────────┬───────────────┘
             │ approved commands
             ↓
  ┌──────────────────────────┐
  │  COMMAND EXECUTION       │
  │                          │
  │  MOS RPC:                │
  │  · setFrequency          │
  │  · setPowerMode          │
  │  · setFanControl         │
  │  · reboot                │
  └──────────────────────────┘

  Key design principle: the AI agent accesses pipeline data, market context,
  and organizational knowledge through the same channel it uses to propose
  actions (SafeClaw → Validance API). No filesystem bridge — reads and
  writes go through one governed path. Three information layers (ML
  perception, market awareness, organizational context) feed into a single
  reasoning chain that produces economically and operationally justified
  proposals.
```

---

## Container Images

| Image | Contents | Size | Used by |
|-------|----------|------|---------|
| `mdk-fleet-intelligence:latest` | Python 3.11 + pandas, numpy, scikit-learn, XGBoost, matplotlib, scipy. All `tasks/` + `scripts/` at `/app/` | ~500 MB | Pipeline tasks (ingest, features, kpi, train, score, trends, optimize, report) |
| `fleet-control:latest` | Python 3.11 + stdlib only. `fleet_status.py` + `control_action.py` at `/app/tasks/` | ~50 MB | Catalog tasks (fleet queries, control actions via proposal pipeline) |
| `rag-tasks:latest` | Python 3.11 + httpx, numpy, azure-storage-blob. `knowledge_query.py` at `/app/scripts/`, RAG modules at `/work/modules/rag/` | ~120 MB | Knowledge query (single-shot RAG) and RAG ingest pipeline (rag.ingest workflow) |

## Scenario Data

Five physics scenarios drive the corpus generator and simulation:

| Scenario | Focus |
|----------|-------|
| `baseline.json` | Normal fleet operation — reference performance |
| `summer_heatwave.json` | Elevated ambient temperatures |
| `asic_aging.json` | Progressive device degradation |
| `psu_degradation.json` | Power supply instability patterns |
| `cooling_failure.json` | Cooling system partial/full failures |

## Test Coverage

| Suite | Tests | Scope |
|-------|-------|-------|
| `test_pipeline_integration.py` | 74 | Full pipeline: artifacts, schemas, value ranges, cross-task consistency, model quality |
| `test_trend_analysis.py` | 40 | Pure functions: OLS, EWMA, CUSUM, projections, direction classification |
| `test_phase6_tasks.py` | 8 | Catalog tasks: 4 query types, 3 control actions, constraint validation |
