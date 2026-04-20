# Mining Optimization

AI-driven fleet intelligence for Bitcoin mining operations.

Supervised ML detection + LLM reasoning agent + human-in-the-loop governance. Detects hardware degradation days before failure, proposes cost-justified corrective actions, and enforces operator approval before anything touches hardware.

**Live dashboard:** [mdk.validance.io](https://mdk.validance.io/)

![Fleet control dashboard — tier evolution, hashrate, TE score, risk heatmap](docs/assets/Screenshot_Fleet_Control.png)

![Per-device commands with tier classification and safety overrides](docs/assets/Screenshot_Command_details.png)

A sample pipeline report (summer_heatwave scenario, final cycle) is available at [`example_output_report/sample-report.html`](example_output_report/sample-report.html).

### AI Agent — Telegram Interaction (Summer Heatwave Simulation)

![Fleet status update — 4 flagged devices as ambient temperature rises](docs/assets/Screenshot_telegram_A.png)

![Maintenance action plan — Tuesday window with staffing assignments](docs/assets/Screenshot_telegram_B.png)

![Economic impact summary with seasonal cooling forecast](docs/assets/Screenshot_telegram_C.png)

The simulation has progressed enough that ambient temperature rose from 15°C to 22°C (the summer heatwave scenario kicking in). The agent adapted its recommendations: the M66S units jumped from 61°C to 74°C and are now flagged, so it shifted from "monitor" to "proactive underclock before SOP-012 triggers." The agent combines real-time fleet telemetry, organizational knowledge (team roster, SOPs, parts inventory via RAG), and live market data (BTC price) into a concrete weekly maintenance schedule with per-device economic justification.

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

The pipeline can be orchestrated by the [Validance](https://github.com/validance-io/sdk-python) workflow engine, adding content-addressed audit trails, container isolation, and artifact management. All orchestration scripts talk to the Validance API at `https://api.validance.io` ([interactive docs](https://api.validance.io/docs#/)), which already holds pre-trained model artifacts.

```bash
# Install the Validance SDK (zero dependencies, declaration only)
git clone git@github.com:validance-io/sdk-python.git
cd sdk-python && pip install -e . && cd ..

# Register workflows with the Validance API
python scripts/register_validance_workflows.py

# Training (optional — a pre-trained model 09605d5baa372954 already exists)
python scripts/orchestrate_training.py

# Inference chain: pre_processing → score → analyze
python scripts/orchestrate_inference.py --training-hash 09605d5baa372954

# With AI agent push (notifies OpenClaw when inference completes):
python scripts/orchestrate_inference.py --training-hash 09605d5baa372954 \
    --gateway-url http://172.18.0.1:19001 --gateway-token fleet-hook-2026

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
│   ├── pipeline_status.py          #   Query Validance API for latest pipeline run refs
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

See [`docs/assets/architecture.svg`](docs/assets/architecture.svg) for the full diagram.

---

## Integration Layers

This pipeline is a **client** of the Validance workflow engine. It does not depend on Validance at runtime — tasks are standalone Python scripts. The integration is at the orchestration level:

| Layer | Repository | Role |
|-------|-----------|------|
| **Workflow Engine** | [`validance-io/sdk-python`](https://github.com/validance-io/sdk-python) | Executes tasks in containers, manages artifacts, content-addressed audit chain |
| **AI Agent Plugin** | [`safeclaw`](https://github.com/Wik-dev/safeclaw) | Bridges the LLM agent to the governance API (approval gate, learned policies) |
| **AI Assistant** | [`openclaw`](https://github.com/openclaw/openclaw) | Personal AI assistant platform (hosts the reasoning agent) |
| **Fleet Dashboard** | [`fleet-health-monitor`](https://github.com/Wik-dev/fleet-health-monitor) | React UI — simulation control, risk heatmaps, tier evolution ([live](https://mdk.validance.io/)) |
| **This repo** | [`mining_optimization`](https://github.com/Wik-dev/mining_optimization) | Pipeline tasks, physics engine, orchestrators, knowledge corpus |

---

## AI Agent Loop

The AI agent connects the ML pipeline to the operator. Two flows keep it in sync: **push** (simulation cycles notify the agent with fresh refs inline) and **pull** (the agent queries the Validance API for the latest pipeline run on manual queries).

```mermaid
sequenceDiagram
    participant UI
    participant Orchestrator
    participant GW as Gateway / Webhook
    participant Agent
    participant API as Validance API
    participant FT as Fleet Tools

    UI->>Orchestrator: trigger mdk.fleet_simulation
    Orchestrator->>Orchestrator: run simulation cycles

    loop each cycle
        Orchestrator->>GW: post cycle notification with refs
        GW->>Agent: deliver webhook event
        Agent->>Agent: extract session_hash + input_files
        Agent->>FT: fleet_status_query(refs)
        FT-->>Agent: risk scores / device status
        Agent->>Agent: reason + propose actions
    end

    Note over Agent: Manual DM (no cycle notification)

    Agent->>FT: fleet_pipeline_status
    FT->>API: GET /api/runs?workflow_name=mdk.score&status=SUCCESS&limit=1
    API-->>FT: latest score run + refs
    FT-->>Agent: session_hash + input_files
    Agent->>FT: fleet_status_query(refs)
    FT-->>Agent: current fleet state
```

**Push path** — each simulation cycle posts a notification (with `session_hash` + `input_files`) to the gateway webhook. The agent extracts the refs and queries fleet status directly.

**Pull path** — on manual DM queries (no cycle notification available), the agent calls `fleet_pipeline_status` which queries the Validance REST API for the latest successful `mdk.score` run and returns the current `session_hash` + `input_files` refs. No filesystem dependency — the agent always gets fresh data regardless of processing speed.

---

## Safety & Security

An autonomous agent that can underclock, overclock, or shut down mining hardware introduces risks that don't exist in passive monitoring. SafeClaw + Validance enforce defense-in-depth: **multiple independent layers, any one of which is sufficient to prevent harm.**

### Separation of concerns

| Layer | Responsibility | Can it act on hardware? |
|-------|---------------|------------------------|
| **ML pipeline** | Classifies device health (deterministic, no side effects) | No |
| **AI agent** | Proposes actions with cost-benefit rationale | No — can only *propose* |
| **Governance (Validance)** | Approves or denies proposals via approval gate | No — gates execution |
| **Worker containers** | Execute approved MOS commands in isolation | Yes — only after approval |

The agent cannot execute on the host, cannot approve its own actions, and cannot bypass the approval gate. Even if prompt injection tricks the agent into *requesting* a harmful action, the attack surface shifts from "injection → execution" to "injection → proposal → human decision."

### Approval gate & learned policies

Every proposal flows through Validance's 7-stage pipeline:

1. **Catalog validation** — action must match a registered template with defined schema
2. **Rate limiting** — per-session, per-action counters prevent runaway loops
3. **Learned policy** — auto-allow/deny based on operator-created rules (`allow-always` / `deny-always`)
4. **Approval gate** — `human-confirm` actions block until explicit operator decision; timeout = **deny** (fail-closed)
5. **Secret injection** — API keys injected at runtime from a secret store, never in LLM context
6. **Container execution** — isolated Docker container with scoped filesystem and network
7. **Result + audit** — every proposal logged with input state, decision, and outcome (tamper-evident chain)

### Hard-coded safety overrides

Before any AI reasoning runs, deterministic physics-based limits are enforced in code:

| Override | Trigger | Action |
|----------|---------|--------|
| Thermal hard limit | T > 80 °C | Force clock to 80% — cannot be overridden by model or agent |
| Cold protection | T < 10 °C | Sleep mode (coolant viscosity / PCB condensation risk) |
| Overvoltage | V > 110% of stock | Reset frequency to stock |
| Fleet redundancy | All same-model devices flagged | Defer lowest-risk device to maintain hashrate floor |

### What the system does NOT protect against

- **Container escape** — a kernel/Docker vulnerability could grant host access (mitigated: containers run as non-root `worker` uid 1000)
- **Compromised Docker images** — the system trusts images referenced in the catalog
- **Fleet-wide sensor drift** — if all sensors drift together, z-score features lose their baseline (acknowledged, out-of-scope for this iteration)

---

## Documentation

### Deliverables

| Document | Contents |
|----------|----------|
| [`docs/technical-report-final.md`](docs/technical-report-final.md) | Technical report (assignment deliverable) |
| [`docs/assets/architecture.svg`](docs/assets/architecture.svg) | End-to-end architecture diagram |
| [`docs/architecture-diagram.md`](docs/architecture-diagram.md) | Architecture diagram (ASCII, detailed) |
| [`tests/validation-report.html`](tests/validation-report.html) | Validation report — 36 SR checks against requirements |

### Reference

| Document | Contents |
|----------|----------|
| [`docs/code-documentation.md`](docs/code-documentation.md) | Per-file code documentation |
| [`docs/feature-catalog.md`](docs/feature-catalog.md) | Complete feature catalog (75 features, computation, rationale) |
| [`docs/user-guide.md`](docs/user-guide.md) | Operational user guide |
| [`docs/requirements.md`](docs/requirements.md) | Functional and non-functional requirements |

### MOS / MDK References

- [mos.tether.io](https://mos.tether.io) — MiningOS (open-source, Apache 2.0)
- [mdk.tether.io](https://mdk.tether.io) — Mining Development Kit
- [github.com/tetherto](https://github.com/tetherto) — MOS source repositories (`miningos-*` prefix)

Key repos: [antminer worker](https://github.com/tetherto/miningos-wrk-miner-antminer) (telemetry fields, control commands), [orchestrator](https://github.com/tetherto/miningos-wrk-ork) (approval system, fleet aggregation).

---

## License

The code and original documentation in this repository are released under the **MIT License** (see [LICENCE](LICENCE)). This license covers the mining optimization pipeline, KPI formulation, physics engine, and architecture documentation authored as part of this assignment.

**Not covered by this license:**

- Contents of `project_materials/` — assignment brief and reference PDFs provided by Tether; copyright of their respective owners
- References to MOS/MDK source repositories — those carry their own Apache-2.0 license at [github.com/tetherto](https://github.com/tetherto)
- OpenClaw and Validance integrations — see the respective repositories for their licensing
