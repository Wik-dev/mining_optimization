# Mining Optimization

AI-driven fleet intelligence for Bitcoin mining operations.

Supervised ML detection + LLM reasoning agent + human-in-the-loop governance. Detects hardware degradation days before failure, proposes cost-justified corrective actions, and enforces operator approval before anything touches hardware.

![Fleet control dashboard — tier evolution, hashrate, TE score, risk heatmap](Screenshot_Fleet_Control.png)

![Per-device commands with tier classification and safety overrides](Screenshot_Command_details.png)

A sample pipeline report (summer_heatwave scenario, final cycle) is available at [`docs/sample-report.html`](docs/sample-report.html).

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for containerized execution via the workflow engine)

### Install dependencies (local development)

```bash
pip install pandas numpy scikit-learn xgboost matplotlib pyarrow scipy joblib requests pytest
```

### Generate synthetic data

```bash
# Single scenario (baseline, 10 devices, 30 days)
python scripts/generate_training_corpus.py --scenario data/scenarios/baseline.json

# Full training corpus (~1.6M rows, 5 scenarios)
python scripts/generate_training_corpus.py --all
```

### Run the pipeline locally (no Docker)

Each task is a standalone Python script that reads from and writes to its working directory (mirroring the `/work/` mount inside containers). All artifacts (parquets, models, JSONs, report) land in the same directory.

```bash
mkdir -p data/pipeline && cd data/pipeline

# Copy training data into the work directory (ingest expects these names)
cp ../training/training_telemetry.csv fleet_telemetry.csv
cp ../training/training_metadata.json fleet_metadata.json

TASKS=../../tasks

# Shared prefix: ingest → features → KPI
python $TASKS/ingest.py
python $TASKS/features.py
python $TASKS/kpi.py

# Training path
python $TASKS/train_model.py

# Inference path (requires trained model)
python $TASKS/score.py

# Analysis
python $TASKS/trend_analysis.py
python $TASKS/optimize.py
python $TASKS/report.py          # → report.html

# Validation report (36 SR checks against requirements)
python ../../scripts/generate_validation_report.py   # → validation-report.html
```

### Run via workflow engine (containerized)

```bash
# Install the Validance SDK (zero dependencies, declaration only)
git clone git@github.com:validance-io/sdk-python.git
cd sdk-python && pip install -e . && cd ..

# Register workflows with the Validance API
python scripts/register_validance_workflows.py

# Training chain: generate_corpus (all scenarios) → pre_processing → train
python scripts/orchestrate_training.py --all

# Inference chain: pre_processing → score → analyze
# --training-hash is the workflow hash from the training run above.
# The engine resolves model artifacts (anomaly_model.joblib, regression_model_v*.joblib)
# from that hash via the continue_from chain — no filesystem paths needed.
python scripts/orchestrate_inference.py --training-hash 09605d5baa372954

# Growing-window simulation (90 cycles)
python scripts/orchestrate_simulation.py --scenario summer_heatwave --training-hash 09605d5baa372954
```

### Run tests

```bash
pytest tests/ -v
```

The test suite (76 tests) generates a mini dataset (5 devices, 14 days), runs the full 8-task pipeline, and validates outputs:

| Test file | Tests | Coverage |
|-----------|-------|----------|
| `test_pipeline_integration.py` | 28 | End-to-end pipeline: data shapes, feature counts, model outputs, report generation |
| `test_trend_analysis.py` | 40 | Trend unit tests: OLS slopes, CUSUM detection, projected crossings, edge cases |
| `test_phase6_tasks.py` | 8 | Fleet control: tier classification, safety overrides, action generation |

---

## Project Structure

```
mining_optimization/
├── tasks/                          # Pipeline tasks (standalone Python scripts)
│   ├── ingest.py                   #   [1] CSV → Parquet, schema validation, dedup
│   ├── features.py                 #   [2] 75 engineered features (rolling, rates, z-scores, interactions)
│   ├── kpi.py                      #   [3] True Efficiency KPI + diagnostic decomposition
│   ├── train_model.py              #   [4a] XGBoost classifier + quantile regressors
│   ├── score.py                    #   [4b] 24h sliding window inference → risk scores
│   ├── trend_analysis.py           #   [5] CUSUM, OLS slopes, projected threshold crossings
│   ├── optimize.py                 #   [6] Tier classification + safety overrides
│   ├── report.py                   #   [7] HTML dashboard with charts
│   ├── fleet_status.py             #   Fleet status query (used by AI agent)
│   ├── control_action.py           #   Fleet control actions (underclock, maintenance)
│   └── generate_batch.py           #   Batch data generation task
│
├── scripts/                        # Orchestrators and standalone tools
│   ├── orchestrate_training.py     #   Training chain (Pattern 1: continue_from)
│   ├── orchestrate_inference.py    #   Inference chain + AI agent notification
│   ├── orchestrate_simulation.py   #   Growing-window simulation loop
│   ├── physics_engine.py           #   CMOS power model, 10 anomaly types, 6 ASIC models
│   ├── simulation_engine.py        #   Per-device per-timestep tick simulation
│   ├── generate_training_corpus.py #   Multi-scenario corpus generator
│   └── register_validance_workflows.py  # Register mdk.* workflows with API
│
├── workflows/                      # Workflow DAG definitions (Validance SDK)
│   ├── fleet_intelligence.py       #   7 composable workflows (pre_processing, train, score, etc.)
│   └── fleet_simulation.py         #   Growing-window simulation wrapper
│
├── knowledge/                      # Organizational knowledge corpus (RAG)
│   ├── company-profile.md          #   Company overview, location, capacity
│   ├── team-roster.md              #   Personnel, shifts, availability
│   ├── hardware-inventory.md       #   ASIC models, batches, warranty
│   ├── maintenance-sops.md         #   Standard operating procedures
│   ├── facility-specs.md           #   Power, cooling, network infrastructure
│   ├── financial-overview.md       #   Energy rates, budget, BTC breakeven
│   ├── vendor-contacts.md          #   Suppliers, SLAs, spare parts
│   ├── safety-procedures.md        #   Emergency protocols, escalation
│   └── knowledge_corpus.md         #   Concatenated corpus (ingested by RAG pipeline)
│
├── tests/                          # Test suite (76 tests)
├── docs/                           # Documentation (see below)
├── data/                           # Generated data + pipeline artifacts (gitignored)
├── project_materials/              # Assignment brief, reference PDFs
├── Dockerfile                      # ML pipeline image (~500 MB)
└── Dockerfile.control              # Fleet control image (~50 MB, stdlib only)
```

### Docker Images

| Image | Dockerfile | Size | Purpose |
|-------|-----------|------|---------|
| `mdk-fleet-intelligence` | `Dockerfile` | ~500 MB | Full ML pipeline (pandas, XGBoost, scikit-learn, matplotlib) |
| `fleet-control` | `Dockerfile.control` | ~50 MB | Fleet status queries + control actions (stdlib only) |
| `rag-tasks` | (in validance-workflow) | ~120 MB | Knowledge query via RAG (httpx, numpy) |

---

## Three-Layer Architecture

```
① Hardware telemetry (5-min intervals)
        ↓
② ML Detection Pipeline          7-task DAG, containerized
   ingest → features → KPI       75 features, True Efficiency KPI
   → train/score → trends        XGBoost + quantile regressors
   → optimize → report           tier classification + safety overrides
        ↓
③ AI Reasoning Agent              LLM with three context layers:
   fleet_status_query             · ML perception (risk scores, tiers)
   web_search                     · Market context (BTC price)
   knowledge_query                · Organizational context (SOPs, team, specs)
        ↓
④ Governance Layer                approval gate, learned policies,
                                  rate limits, content-addressed audit
        ↓
⑤ MOS Command Execution          setFrequency, setPowerMode, reboot
```

See [`docs/architecture.svg`](docs/architecture.svg) for the full diagram.

---

## Integration Layers

This pipeline is a **client** of the Validance workflow engine. It does not depend on Validance at runtime — tasks are standalone Python scripts. The integration is at the orchestration level:

| Layer | Repository | Role |
|-------|-----------|------|
| **Workflow Engine** | `validance-workflow` | Executes tasks in containers, manages artifacts, content-addressed audit chain |
| **AI Agent Plugin** | `safeclaw` | Bridges the LLM agent to the governance API (approval gate, learned policies) |
| **AI Assistant** | `openclaw` | Personal AI assistant platform (hosts the reasoning agent) |
| **This repo** | `mining_optimization` | Pipeline tasks, physics engine, orchestrators, knowledge corpus |

---

## Documentation

### Deliverables

| Document | Contents |
|----------|----------|
| [`docs/technical-report.md`](docs/technical-report.md) | Technical report (assignment deliverable) |
| [`docs/architecture.svg`](docs/architecture.svg) | End-to-end architecture diagram |
| [`docs/architecture-diagram.md`](docs/architecture-diagram.md) | Architecture diagram (ASCII, detailed) |
| [`docs/validation-report.html`](docs/validation-report.html) | Validation report — 36 SR checks against requirements |

### Reference

| Document | Contents |
|----------|----------|
| [`docs/system-overview.md`](docs/system-overview.md) | System overview, data flow, controller tiers, tech stack |
| [`docs/code-documentation.md`](docs/code-documentation.md) | Per-file code documentation |
| [`docs/feature-catalog.md`](docs/feature-catalog.md) | Complete feature catalog (75 features, computation, rationale) |
| [`docs/true-efficiency-kpi.md`](docs/true-efficiency-kpi.md) | True Efficiency KPI formulation and decomposition |
| [`docs/evaluation-analysis.md`](docs/evaluation-analysis.md) | Model evaluation, threshold analysis |
| [`docs/user-guide.md`](docs/user-guide.md) | Operational user guide |
| [`docs/requirements.md`](docs/requirements.md) | Functional and non-functional requirements |

### MOS / MDK References

- [mos.tether.io](https://mos.tether.io) — MiningOS (open-source, Apache 2.0)
- [mdk.tether.io](https://mdk.tether.io) — Mining Development Kit
- [github.com/tetherto](https://github.com/tetherto) — MOS source repositories (`miningos-*` prefix)

Key repos: [antminer worker](https://github.com/tetherto/miningos-wrk-miner-antminer) (telemetry fields, control commands), [orchestrator](https://github.com/tetherto/miningos-wrk-ork) (approval system, fleet aggregation).
