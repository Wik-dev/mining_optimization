# Fleet Intelligence — User Guide

**Operations Manual for the Mining Optimization Pipeline**

Wiktor Lisowski | April 2026

Last edited: 2026-04-06

### Change Log

| Date | Change |
|------|--------|
| 2026-04-18 | Added `knowledge_query` template to §5.1, updated §1.1 prerequisites with `rag-tasks` image |
| 2026-04-06 | Update for `continue_from` deep context model resolution (training-hash replaces model-path) |
| 2026-04-05 | Initial version |

---

## Quick Start

From clone to a working report:

```bash
# 0. Clone and set up
git clone <repo-url> && cd mining_optimization
pip install validance-sdk  # provides the Task / Workflow SDK used by workflow definitions

# 1. Register workflows with Validance (~5s)
python scripts/register_validance_workflows.py --api-url http://localhost:8001

# 2. Run the full training chain: generate data → preprocess → train (~45 min)
python scripts/orchestrate_training.py --api-url http://localhost:8001

# 3. Run inference: preprocess → score → analyze → report (~7 min)
#    Use the training hash from step 2 output (model resolved via deep context)
python scripts/orchestrate_inference.py \
  --api-url http://localhost:8001 \
  --telemetry-csv /work/training_telemetry.csv \
  --metadata-json /work/training_metadata.json \
  --training-hash <hash-from-step-2>

# 4. Open the dashboard
open /work/report.html
```

Prerequisites: Docker running, Validance API at `:8001`, task images built. See [Getting Started](#1-getting-started) if any step fails.

---

## 1. Getting Started

### 1.1 Prerequisites

| Component | Required | Check |
|-----------|----------|-------|
| Docker | Running on host | `docker ps` |
| Validance API | Running at `:8001` (dev) or `:8000` (prod) | `curl -s http://localhost:8001/api/health` |
| Task images | `mdk-fleet-intelligence`, `fleet-control`, `rag-tasks` | `docker images \| grep -E 'mdk\|fleet-control\|rag-tasks'` |
| Python 3.11+ | In dev container or host | `python3 --version` |
| Validance SDK | Installed in current Python env | `python -c "from validance.sdk import Task, Workflow"` |

Set up the environment:

```bash
cd mining_optimization
pip install validance-sdk
```

### 1.2 Register Workflows (Once)

Register the pipeline workflows with Validance. Run this once after cloning, or after changing workflow definitions.

```bash
python scripts/register_validance_workflows.py --api-url http://localhost:8001
```

Expected output:

```
Registering workflows against http://localhost:8001...
  API status: ok
  mdk.pre_processing: registered (3 tasks, hash=abc12345...)
  mdk.train: registered (1 task, hash=def67890...)
  mdk.score: registered (1 task, hash=ghi11111...)
  mdk.analyze: registered (3 tasks, hash=jkl22222...)
  mdk.generate_corpus: registered (1 task, hash=mno33333...)
  mdk.fleet_simulation: registered (1 task, hash=pqr44444...)
```

If you see `ERROR: Cannot reach API`, verify the Validance dev container is running (`docker ps | grep workflow`).

### 1.3 Generate Training Data

Generate synthetic fleet telemetry from physics-based simulation:

```bash
# All 5 scenarios (recommended for first run)
python scripts/generate_training_corpus.py --all --output data/training/

# Single scenario
python scripts/generate_training_corpus.py --scenario data/scenarios/baseline.json
```

Output appears in `data/training/`:
- `training_telemetry.csv` — 35-column telemetry (~430k rows for all scenarios)
- `training_metadata.json` — provenance, fleet specs, label distribution

The label distribution at the end of output tells you how many anomalous samples of each type were generated. All 10 anomaly types should have non-zero counts.

---

## 2. Training

Run the training chain when: (a) first setting up the system, or (b) the retrain monitor flags model drift.

### 2.1 Run the Training Chain

```bash
python scripts/orchestrate_training.py --api-url http://localhost:8001
```

This chains three workflows automatically:
1. **generate_corpus** — synthetic data generation (~45s)
2. **pre_processing** — ingest, features, KPI (~1-2 min)
3. **train** — XGBoost classifier + quantile regressors (~41 min)

Total: ~45 minutes.

To skip corpus generation and use existing data:

```bash
python scripts/orchestrate_training.py \
  --api-url http://localhost:8001 \
  --telemetry-csv /work/training_telemetry.csv \
  --metadata-json /work/training_metadata.json
```

### 2.2 Training Artifacts

After training completes, these files appear in `/work/`:

| File | What it is |
|------|------------|
| `anomaly_model.joblib` | Binary classifier (XGBoost, threshold=0.3) |
| `regression_model_v1.joblib` | Quantile regressors (4 horizons x 3 quantiles) |
| `model_registry.json` | Model metadata, version, feature list |
| `model_metrics.json` | F1 score, per-anomaly detection coverage, feature importance |

### 2.3 Verify the Model

Check `model_metrics.json` for quality indicators:

| Metric | Good | Concerning |
|--------|------|------------|
| F1 score (binary) | > 0.85 | < 0.75 |
| Recall | > 0.90 | < 0.80 |
| Precision | > 0.70 | < 0.60 |
| Per-anomaly coverage | All 10 types detected | Any type with 0 detections |

For detailed evaluation against held-out data:

```bash
python scripts/evaluate_predictions.py \
  --kpi data/pipeline/kpi_timeseries.parquet \
  --model data/pipeline/anomaly_model.joblib \
  --regression-model data/pipeline/regression_model_v1.joblib \
  --output evaluation_report.json
```

This prints a confusion matrix, per-device predictions vs ground truth, and per-horizon regression accuracy. Review `evaluation_report.json` for the full breakdown.

---

## 3. Inference

The main operational loop. Run this every cycle (recommended: 30 min to 1 hour) against fresh telemetry.

### 3.1 Run the Inference Chain

```bash
python scripts/orchestrate_inference.py \
  --api-url http://localhost:8001 \
  --telemetry-csv /work/fleet_telemetry.csv \
  --metadata-json /work/fleet_metadata.json \
  --training-hash 09605d5baa372954
```

Model artifacts (anomaly_model.joblib, regression_model, model_metrics) are resolved automatically via Validance's `continue_from` deep context chain — no explicit file paths needed. The training hash is printed at the end of the training chain (§2.1).

This chains three workflows:
1. **pre_processing** — ingest, features, KPI (~3-4 min)
2. **score** — classifier + regression predictions (~1-2 min)
3. **analyze** — trends, tier classification, report (~2-3 min)

Total: ~7 minutes.

### 3.2 Inference Outputs

All outputs appear in `/work/`:

| File | What it tells you |
|------|-------------------|
| `fleet_risk_scores.json` | Per-device anomaly probability and multi-horizon predictions |
| `fleet_actions.json` | Tier assignments (CRITICAL/WARNING/DEGRADED/HEALTHY) and recommended commands |
| `trend_analysis.json` | Per-device trend direction, regime changes, projected threshold crossings |
| `report.html` | Visual dashboard — open in browser |

### 3.3 Quick Fleet Status (No Report)

For a fast check without running the full analysis chain:

```bash
# Fleet summary
python tasks/fleet_status.py --query summary

# Single device detail
python tasks/fleet_status.py --query device_detail --device-id ASIC-007

# Tier breakdown
python tasks/fleet_status.py --query tier_breakdown

# Risk ranking (all devices, sorted by risk)
python tasks/fleet_status.py --query risk_ranking
```

These read existing pipeline outputs and return JSON. No side effects, no container execution.

### 3.4 Continuous Simulation

For automated inference cycles against evolving simulated telemetry (demo or testing). Requires a pre-trained model — run the training chain first (§2).

**Growing-window approach**: All scenario data is generated upfront (Phase 1), then each inference cycle processes the accumulated history from `t=0` to `t=cutoff` (Phase 2). This ensures rolling feature windows (6h, 24h, 7d) are properly populated, matching real-world monitoring where a database accumulates telemetry over time.

**From the dashboard** (recommended for demos):

Click "Start Simulation" in the dashboard sidebar, select a scenario, and the dashboard triggers `mdk.fleet_simulation` via the API. Inner cycles appear as separate workflow runs.

**From the CLI**:

```bash
python scripts/orchestrate_simulation.py \
  --scenario data/scenarios/asic_aging.json \
  --training-hash 09605d5baa372954 \
  --api-url http://localhost:8001
```

Model artifacts are resolved via `continue_from` deep context — the training hash establishes provenance. The number of cycles is derived from the scenario's `duration_days` divided by `--interval-days` (default: 1). For example, `asic_aging.json` (180 days) at 1-day intervals = 180 cycles.

```bash
# 7-day intervals → fewer cycles (26 instead of 180), faster demo
python scripts/orchestrate_simulation.py \
  --scenario data/scenarios/asic_aging.json \
  --training-hash 09605d5baa372954 \
  --interval-days 7
```

**Scenario durations**:

| Scenario | Days | Devices | Cycles (1-day) | Cycles (7-day) |
|----------|------|---------|----------------|----------------|
| asic_aging | 180 | 15 | 180 | 26 |
| cooling_failure | 60 | 10 | 60 | 9 |
| psu_degradation | 90 | 10 | 90 | 13 |
| summer_heatwave | 90 | 12 | 90 | 13 |
| baseline | 30 | 10 | 30 | 5 |

Results accumulate in `data/simulation/simulation_metrics.json`.

Each cycle produces visible workflow runs in the Validance API — the dashboard tracks progress per-cycle and provides a day banner for temporal navigation.

**Standalone batch generation** (no Validance needed):

```bash
# Generate a single 24h batch:
python scripts/generate_batch.py \
  --scenario data/scenarios/asic_aging.json \
  --interval 1440 \
  --output-dir data/pipeline_run

# Continue with 1h batches (state carries over):
python scripts/generate_batch.py \
  --scenario data/scenarios/asic_aging.json \
  --interval 60 \
  --state data/pipeline_run/sim_state.json \
  --output-dir data/pipeline_run
```

**Offline simulation** (no API, just batch generation):

```bash
python scripts/simulation_loop.py \
  --scenario data/scenarios/baseline.json \
  --cycles 12 \
  --offline
```

This mode still works for standalone testing without Validance.

---

## 4. Reading the Report

Open [`example_output_report/sample-report.html`](../example_output_report/sample-report.html) in a browser for a reference example. The dashboard has these sections, top to bottom.

### 4.1 Metrics Cards

Six cards across the top:

| Card | What to look for |
|------|------------------|
| **Mean True Efficiency** | Fleet average TE in J/TH. Lower is better. Compare against nominal (~15-20 J/TH depending on fleet mix). |
| **Active Devices** | Count of operational devices. Drop = devices in maintenance or failed. |
| **Training Samples** | How many rows the model trained on. Should be >100k for reliable detection. |
| **Critical Devices** | Red count. Any number > 0 requires immediate attention. |
| **Warning Devices** | Orange count. These need action within the current shift. |
| **Worst Health Score** | The device most in need of attention. Click through to the risk table for details. |

### 4.2 Fleet True Efficiency Over Time

Line chart, one line per device, hourly granularity.

- **Flat lines near nominal**: fleet is healthy.
- **Upward drift** (TE increasing = worse efficiency): degradation in progress. Check which device and cross-reference with the TE decomposition.
- **Sudden jump**: regime change — could be ambient temperature swing, firmware update, or hardware event.
- **Diverging lines**: heterogeneous degradation. Some devices aging faster than others.

### 4.3 TE Decomposition by Device

Grouped bar chart showing where each device's efficiency loss comes from:

| Component | What it means | Action if elevated |
|-----------|---------------|--------------------|
| **TE_base** (hardware) | Intrinsic chip efficiency. Rising = hashrate decay or chip failure. | Schedule inspection. Check chip count, hashboard count. |
| **Cooling overhead** | Extra energy spent on cooling. Rising = thermal fouling, fan degradation, dust. | Check fan RPM, clean filters, inspect coolant loop. |
| **Voltage penalty** | Wasted power from operating above minimum stable voltage. Rising = PSU instability. | Check voltage ripple, PSU capacitor health. Reset frequency to stock. |

This is the "why" chart. When a device shows high total TE, this tells you which physical mechanism is responsible.

### 4.4 Device Health Score Heatmap

2D grid: devices (rows) x days (columns). Green = healthy (1.0), red = critical (<0.6).

- **Horizontal red band**: one device degrading over time. Inspect that device.
- **Vertical red band**: fleet-wide event on that day. Check ambient conditions, energy pricing, firmware rollout.
- **Gradual green-to-red fade**: slow degradation. The trend analysis will have the slope and projected crossing time.
- **Red-to-green recovery**: device recovered — check if maintenance was performed.

### 4.5 Risk Ranking

Horizontal bar chart, devices sorted by `mean_risk` (highest first).

- **Red bars (>0.9)**: CRITICAL tier. These devices need immediate underclock and inspection.
- **Orange bars (0.5-0.9)**: WARNING tier. Underclock to 85%, schedule inspection.
- **Short green bars (<0.3)**: HEALTHY. No action needed.

The corresponding **risk table** below the chart shows exact numbers: mean risk, max risk, % of samples flagged, and the device's latest TE score.

### 4.6 Controller Actions Table

The actionable output. Each row is a device with recommended commands.

| Column | How to read it |
|--------|----------------|
| **Tier** | CRITICAL (red), WARNING (orange), DEGRADED (yellow), HEALTHY (green) |
| **Commands** | What the system recommends: `set_clock`, `schedule_inspection`, `set_monitoring_interval` |
| **MOS Methods** | The actual MOS RPC to execute: `setFrequency`, `setPowerMode`, etc. |
| **MOS Codes** | Relevant alert codes (P:1 = thermal, R:1 = low hashrate, V:1 = power error) |
| **Rationale** | Human-readable explanation of *why* this tier and these commands |

Safety overrides are called out explicitly in the rationale (e.g., "SAFETY: temperature 68.4C > 80C hard limit").

### 4.7 Prediction Fan Charts (if regression model present)

Per-device TE_score forecasts at +1h, +6h, +24h, +7d with uncertainty bands.

- **Blue band**: 80% confidence interval (p10 to p90). Wider = more uncertain.
- **Black line**: median forecast (p50). This is the "most likely" trajectory.
- **Red dashed line**: threshold crossings (0.8 = DEGRADED, 0.6 = severe).

Read it as: "ASIC-007 median forecast drops below 0.8 in ~8 hours. With 80% confidence, it will be between 0.46 and 0.57 in 24 hours."

### 4.8 Trend Trajectories (if trend analysis present)

Per-device TE_score with linear regression overlay.

- **Red downward slope**: device is declining. Steeper = faster degradation.
- **Green upward slope**: device is recovering.
- **Flat gray**: stable. No trend detected above noise floor.

The trend direction classification:

| Direction | Slope (per hour) | Meaning |
|-----------|-------------------|---------|
| Falling fast | < -0.02 | Crosses a 0.2 TE_score band in <10h. Urgent. |
| Declining | -0.02 to -0.005 | Gradual degradation. Schedule maintenance. |
| Stable | -0.005 to +0.005 | Within noise floor. No action needed. |
| Recovering | +0.005 to +0.02 | Improvement after maintenance or environmental change. |
| Recovering fast | > +0.02 | Rapid recovery. Verify it's genuine, not a sensor artifact. |

### 4.9 Feature Importance

Horizontal bar chart showing which telemetry signals most influence the anomaly classifier.

This is a transparency chart. If `te_score_lag_1h` (recent TE history) dominates, the model is doing what you'd expect. If an unexpected feature appears (e.g., `ambient_temp_c`), investigate whether the model is picking up a genuine signal or a data artifact.

---

## 5. Fleet Control via SafeClaw

When the ML pipeline flags devices, the AI reasoning agent (SafeClaw) reads the outputs, proposes specific MOS commands, and routes them through Validance's approval gate.

### 5.1 What SafeClaw Reads

SafeClaw reads the inference outputs to build context:

| File | What SafeClaw extracts |
|------|----------------------|
| `fleet_actions.json` | Tier assignments, recommended commands, safety flags |
| `fleet_risk_scores.json` | Risk scores, predictions, threshold crossings |
| `trend_analysis.json` | Trend direction, regime changes, projected crossing times |

SafeClaw combines this with real-time context (BTC price, energy costs, maintenance crew availability) to propose actions with natural-language rationale.

**`knowledge_query`** (auto-approve):
- Queries organizational knowledge base via RAG (retrieval-augmented generation).
- The agent uses this to check SOPs, team availability, hardware specs, and financial constraints before proposing actions.
- Reads a pre-built vector index (built from `knowledge/` corpus via `rag.ingest` workflow).
- Parameters: `query` (natural language question), `input_files` (must include `index.json` reference).

### 5.2 What a Proposal Looks Like

When SafeClaw proposes an action, you see:

```
Action requires approval: fleet_underclock
{
  "device_id": "ASIC-007",
  "target_pct": 85,
  "reason": "WARNING tier: risk 0.687, declining trend (slope -0.008/h).
             BTC price stable, maintenance crew available Thursday.
             Underclock preserves hardware while scheduling inspection."
}

To approve: /sc-approve abc123 allow-once
To always approve this pattern: /sc-approve abc123 allow-always
To deny: /sc-approve abc123 deny
```

### 5.3 Approval Decisions

Three choices for each proposal:

| Command | Effect |
|---------|--------|
| `/sc-approve {id} allow-once` | Approve this specific action. Executes in sandbox container, result returned. |
| `/sc-approve {id} allow-always` | Approve and create a learned policy. Future matching proposals auto-approve. |
| `/sc-approve {id} deny` | Reject. SafeClaw will not retry the same action. |

**Policy ceiling**: Emergency shutdowns can never be auto-approved via learned policies. Every shutdown requires fresh human approval, regardless of past decisions.

Proposals expire after 10 minutes if not resolved.

### 5.4 Trust Profiles

Trust profiles control which actions need approval:

| Profile | Auto-approved | Needs approval |
|---------|---------------|----------------|
| **Conservative** | Nothing | Everything |
| **Standard** (default) | File ops, web search, fleet status queries, read-only shell commands (`ls`, `cat`, `grep`) | `exec`, `browser`, `message`, `fleet_underclock`, `cron` |
| **Power-user** | Everything except emergency actions | Shutdown, browser `evaluate`, `submit_form` |

### 5.5 Fleet-Specific Templates

Two SafeClaw catalog templates are specific to fleet operations:

**`fleet_status_query`** (auto-approve):
- Read-only queries: summary, device detail, tier breakdown, risk ranking.
- SafeClaw uses this to check fleet state before proposing actions.

**`fleet_underclock`** (human-confirm, auto-approve in power-user):
- Reduces device clock frequency. Parameters: `device_id`, `target_pct` (50-100%), `reason`.
- Server-side validation: rejects if fleet hashrate would drop below 70% of nominal, or if minimum underclock (50%) is violated.

Shutdown actions go through the `exec` template with `setPowerMode("sleep")`. Always requires human approval.

### 5.6 After Approval

Once approved, the action executes in a sandboxed container. The result includes:

- **MOS command**: The exact RPC call (`setFrequency`, `setPowerMode`)
- **Fleet impact**: Pre/post fleet hashrate percentage
- **Audit record**: Timestamp, device, parameters, result, fleet impact

All actions are logged to `agent_actions.json` for post-incident review.

---

## 6. Reference

### 6.1 Workflows

| Name | Tasks | Description | When to run |
|------|-------|-------------|-------------|
| `mdk.pre_processing` | 3 | ingest → features → kpi | Shared prefix for training and inference |
| `mdk.train` | 1 | Train classifier + regressors | After pre_processing (training path) |
| `mdk.score` | 1 | Score fleet with pre-trained model | After pre_processing (inference path) |
| `mdk.analyze` | 3 | trends → optimize → report | After score |
| `mdk.generate_corpus` | 1 | Generate synthetic training data | Before pre_processing (training path) |
| `mdk.generate_batch` | 1 | Generate simulation batch (stateful) | Used by simulation orchestrator |
| `mdk.fleet_simulation` | 1 | Pattern 5a growing-window simulation wrapper | Dashboard trigger or CLI |

Orchestration chains:
- **Training**: `generate_corpus → pre_processing → train`
- **Inference**: `pre_processing → score → analyze`
- **Simulation**: `mdk.fleet_simulation` triggers: `generate_batch(full) → [pre_processing(cutoff) → score → analyze] × N cycles`

### 6.2 Pipeline Tasks

| Task | Script | Input | Output |
|------|--------|-------|--------|
| Ingest | `tasks/ingest.py` | CSV + metadata JSON | `telemetry.parquet` |
| Features | `tasks/features.py` | `telemetry.parquet` | `features.parquet` (75 features/device-timestep) |
| KPI | `tasks/kpi.py` | `features.parquet` | `kpi_timeseries.parquet` |
| Train | `tasks/train_model.py` | `kpi_timeseries.parquet` | `anomaly_model.joblib`, `model_metrics.json` |
| Score | `tasks/score.py` | `kpi_timeseries.parquet` + model | `fleet_risk_scores.json` |
| Trends | `tasks/trend_analysis.py` | `kpi_timeseries.parquet` | `trend_analysis.json` |
| Optimize | `tasks/optimize.py` | risk scores + trends + metadata | `fleet_actions.json` |
| Report | `tasks/report.py` | All above | `report.html` |

### 6.3 Tier Thresholds

| Tier | Trigger | Response |
|------|---------|----------|
| **CRITICAL** | risk > 0.9 | Clock → 70%, immediate inspection, 60s monitoring |
| **WARNING** | risk > 0.5 | Clock → 85%, next-window inspection, 120s monitoring |
| **DEGRADED** | TE_score < 0.8 | Reset frequency to stock, 180s monitoring |
| **HEALTHY** | default | Hold settings; suggest 5% overclock if headroom > 10C |

### 6.4 Safety Overrides

Applied before tier logic. Always win.

| Override | Trigger | Action |
|----------|---------|--------|
| Thermal hard limit | T > 80C | Clock → 80% stock (CRITICAL) |
| Thermal emergency | T < 10C | Sleep mode + immediate inspection |
| Thermal low warning | 10C <= T < 20C | Clock → 70% + fan min (air-cooled only) |
| Overvoltage | V > 110% stock | Reset frequency to stock |
| Fleet redundancy | All same-model flagged | Defer lowest-risk device |

### 6.5 Key File Locations

| Path | Purpose |
|------|---------|
| `tasks/` | Pipeline task scripts |
| `workflows/` | Workflow definitions |
| `scripts/` | Orchestration and utility scripts |
| `data/scenarios/` | Scenario definitions for data generation |
| `data/training/` | Training corpus output |
| `/work/` | Pipeline shared directory (in containers) |
| `report.html` | HTML dashboard (open in browser) |
| `fleet_actions.json` | Tier assignments + recommended commands |
| `fleet_risk_scores.json` | Per-device anomaly scores + predictions |
| `trend_analysis.json` | Trends, regime changes, projections |
| `model_metrics.json` | Classifier F1, per-anomaly coverage, feature importance |

### 6.6 Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | — | Required for `embed_chunks` and `knowledge_query` (see `modules/rag/.env.example`) |
| `CTX_API_URL` | `http://localhost:8000` | Validance API URL (container context) |
| `CTX_CUTOFF_TIMESTAMP` | — | Growing-window cutoff (ISO 8601). Set by orchestrate_simulation.py per cycle. Filters ingest data to `[start, cutoff]`. |
| `CTX_TRAINING_HASH` | — | Training workflow hash. Used by Pattern 5a simulation wrapper. |
| `CTX_INTERVAL_DAYS` | `1` | Simulated days per inference cycle (Pattern 5a). |
| `WORKFLOW_API_URL` | `http://localhost:8000` | Validance API URL (fallback) |

### 6.7 MOS Alert Codes

| Code | Description | Severity |
|------|-------------|----------|
| P:1 | High temperature protection | Critical |
| P:2 | Low temperature protection | Critical |
| R:1 | Low hashrate | High |
| V:1 | Power initialization error | Critical |
| V:2 | PSU not calibrated | High |
| J0:8 | Insufficient hashboards | Critical |
| L0:1 | Voltage/frequency exceeds limit | Critical |
| L0:2 | Voltage/frequency mismatch | High |
| J0:2 | Chip insufficiency | High |
| J0:6 | Temperature sensor error | High |

### 6.8 Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot reach API at http://localhost:8001` | Validance dev container not running | `docker ps`, restart dev container |
| `Workflow not found: mdk.pre_processing` | Workflows not registered | Run `register_validance_workflows.py` |
| `No such file: anomaly_model.joblib` | Model not trained or training hash not in continuation chain | Run the training chain first; pass `--training-hash` to inference/simulation |
| `Fleet hashrate would drop to 68%` | Underclock rejected by fleet capacity check | Reduce `target_pct` or bring other devices back online first |
| `Container exited with code 2` | Missing `request_loop.py` in image | Rebuild task image with `scripts/request_loop.py` baked in |
| Task timeout after 30 min | Container hung or resource exhaustion | Check `docker logs` for the task container |
| `Regression model not found` | Optional; classifier-only mode | Regression model auto-resolved from training if present; run without predictions otherwise |
| `feature_names mismatch` | Inference data has fewer features than training | Normal for single-scenario simulation; score.py pads missing features with 0 |
