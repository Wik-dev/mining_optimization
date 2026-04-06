# Operational Dashboard — Frontend Specification

> **Audience**: Frontend developer building the dashboard UI.
> **Backend**: Validance REST API at `http://20.199.13.38:8001` (dev).
> **Data**: Mining pipeline outputs served through Validance workflow endpoints. New data appears every inference cycle (~1 simulated day at default interval).

---

## Architecture Overview

The system simulates a real mining operation running continuously. The dashboard triggers `mdk.fleet_simulation` (Pattern 5a wrapper), which runs a **growing-window** inference loop: all scenario data is generated upfront, then each cycle runs inference on accumulated history `[t=0 → t=cutoff]`. This matches real-world monitoring where a database accumulates telemetry over time.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Dashboard UI                                                       │
│                                                                     │
│  "Start Simulation" button                                          │
│    → POST /api/workflows/mdk.fleet_simulation/trigger               │
│       { scenario_path, training_hash, api_url, interval_days }      │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│  mdk.fleet_simulation (Pattern 5a — 1-task wrapper)                 │
│  Runs orchestrate_simulation.py inside container                    │
│                                                                     │
│  Phase 1: mdk.generate_batch(full scenario) → single CSV           │
│                                                                     │
│  Phase 2: Growing-window inference loop                             │
│    For each cycle (1 per simulated day):                            │
│      mdk.pre_processing(cutoff=day N) → mdk.score → mdk.analyze   │
│      cutoff grows: day 1, day 2, ... day N                         │
│      Each cycle sees ALL history from t=0 to cutoff                │
│                                                                     │
│  Inner workflows visible as separate runs in GET /api/runs         │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│  Validance API  (REST, port 8001)                                   │
│                                                                     │
│  GET /api/runs → [hash_T0, hash_T1, hash_T2, ...]   ← timeline   │
│  GET /api/files/{hash}/download → JSON snapshot per cycle          │
│  POST /api/proposals → command approval pipeline                    │
│  GET /api/audit → immutable decision history                        │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            │  Poll every 30-60s
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│  Dashboard UI                                                       │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Day Banner: [Day 1] [Day 2] [Day 3] ... [Day N]            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ Fleet    │  │ Device   │  │ Command  │  │ Pipeline         │  │
│  │ Timeline │  │ Detail   │  │ Approval │  │ Monitor          │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│                                                                     │
│  Client-side state: array of snapshots, one per inference cycle    │
│  Time-series built by stitching snapshots together                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Dashboard States & Startup Flow

The dashboard has **3 states**:

### State 1: Setup — Scenario Picker + Start Button

The dashboard lets the operator pick a scenario and start the simulation directly from the UI. Training must still be run from the CLI first (it produces the model artifact).

```
┌─────────────────────────────────────────────────────────────────┐
│  Fleet Intelligence Dashboard                                    │
│                                                                  │
│  ┌─────────────────────────────┐                                │
│  │  Select Scenario            │                                │
│  │                             │                                │
│  │  ● ASIC Aging (180 days)   │  15 devices, progressive       │
│  │  ○ Cooling Failure (60d)   │  10 devices, cooling faults    │
│  │  ○ PSU Degradation (90d)   │  10 devices, power instability │
│  │  ○ Summer Heatwave (90d)   │  12 devices, elevated ambient  │
│  │  ○ Baseline (30 days)     │  10 devices, no anomalies      │
│  │                             │                                │
│  │  [▶ Start Simulation]      │                                │
│  └─────────────────────────────┘                                │
│                                                                  │
│  Prerequisites: model trained (orchestrate_training.py)          │
└─────────────────────────────────────────────────────────────────┘
```

**Scenario durations** (for frontend):

| Scenario | Days | Devices | Total Cycles (1-day interval) | Anomaly Onset |
|----------|------|---------|-------------------------------|---------------|
| asic_aging | 180 | 15 | 180 | Day 10/20/30/60 |
| cooling_failure | 60 | 10 | 60 | Day 8/12/20 |
| psu_degradation | 90 | 10 | 90 | Day 10/15 |
| summer_heatwave | 90 | 12 | 90 | Day 5/20 |
| baseline | 30 | 10 | 30 | None |

**Trigger endpoint** — start simulation from dashboard:

```javascript
// Start simulation from dashboard
const res = await fetch("/api/workflows/mdk.fleet_simulation/trigger", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    parameters: {
      scenario_path: `file:///home/Wik-dev/repos/mining_optimization/data/scenarios/${scenarioName}.json`,
      training_hash: "483379d07426668e",  // hash of the training run — model resolved via deep context
      api_url: "http://172.17.0.1:8001",  // container reaches host API via Docker bridge
      interval_days: "1",
    },
    session_hash: sessionHash,
  }),
});
const { workflow_hash } = await res.json();
// This hash = the outer simulation workflow. Inner cycles appear as separate runs.
```

**Backend prerequisite** (run by operator before dashboard use):

```bash
# Train model (all 5 scenarios, ~35 min) — only needed once
# Produces a training hash (e.g., 483379d07426668e) used by the dashboard trigger.
python scripts/orchestrate_training.py --api-url http://localhost:8001
# The training hash is printed at the end. Update the dashboard config if re-trained.
```

### State 2: Running — Live Dashboard with Day Banner

Once simulation is triggered (or if the dashboard reloads during an active simulation), show the full dashboard with a **day banner** at the top for temporal navigation.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Day 1   Day 2   Day 3   Day 4   Day 5   Day 6   Day 7   ...  ▸    │
│  [G]     [G]     [G]     [Y]     [O]     [R]     [R]    ...       │
│                                           ▲ current                  │
└──────────────────────────────────────────────────────────────────────┘
```

- Each button = one inference cycle (= one simulated day at default interval)
- Color = worst tier across all devices that day:
  - All HEALTHY → green (#22c55e)
  - Any DEGRADED → yellow (#eab308)
  - Any WARNING → orange (#f59e0b)
  - Any CRITICAL → red (#dc2626)
- Selected day has a highlight/underline
- Auto-scrolls right as new days complete
- When on latest day: auto-advances when next day completes
- When viewing a historical day: stays pinned (manual navigation)
- "Cycle X of N" progress indicator

New data points appear as each inference cycle completes (~1-2 minutes apart in wall-clock time, representing 1 simulated day each).

**Detecting simulation progress**:

```javascript
// Count completed analyze runs to track cycle progress
const analyzeRes = await fetch("/api/runs?workflow_name=mdk.analyze&status=SUCCESS&limit=200");
const analyzeData = await analyzeRes.json();
const completedCycles = analyzeData.total;

// Total cycles = scenario duration_days / interval_days
// (known from the scenario selected in State 1)
showCycleProgress(completedCycles, totalCycles);
```

### State 3: Complete — Simulation Finished

Same as State 2 but with a "Simulation Complete" banner and no auto-advance. All days are navigable. The operator can review any day's data at leisure.

### State Transitions

```javascript
async function determineState() {
  // 1. Check for any completed analyze runs (= inference data exists)
  const runs = await fetch("/api/runs?workflow_name=mdk.analyze&status=SUCCESS&limit=1");
  const runData = await runs.json();

  if (runData.total > 0) {
    // Data exists → check if simulation is still running
    const simRuns = await fetch("/api/runs?workflow_name=mdk.fleet_simulation&status=RUNNING&limit=1");
    const simData = await simRuns.json();

    if (simData.total > 0) {
      return "running";   // State 2
    }
    return "complete";    // State 3
  }

  // 2. Check if simulation was just triggered (no data yet, but running)
  const simRuns = await fetch("/api/runs?workflow_name=mdk.fleet_simulation&status=RUNNING&limit=1");
  const simData = await simRuns.json();
  if (simData.total > 0) {
    return "running";     // State 2 (waiting for first cycle)
  }

  // 3. No data, no simulation → State 1 (Setup)
  return "setup";
}
```

---

## Temporal Model

### Growing-Window Architecture

The simulation uses a **growing-window** approach that mirrors real-world monitoring:

1. **Phase 1** (one-shot): All scenario data is generated upfront via a single `mdk.generate_batch` run covering the full scenario duration. This produces one large CSV with all telemetry.

2. **Phase 2** (per cycle): Each inference cycle filters the full dataset to `[t=0 → t=cutoff]` using `CTX_CUTOFF_TIMESTAMP`. The cutoff grows by `interval_days` each cycle.

This ensures rolling feature windows (6h, 24h, 7d) are properly populated — the model sees the same feature distributions it was trained on. Without this, each cycle would only see one day of data, truncating all windows.

| Cycle | Cutoff | Data visible | Rolling windows |
|-------|--------|-------------|-----------------|
| 1 | Day 1 | Day 0–1 | 1d only (truncated) |
| 2 | Day 2 | Day 0–2 | Up to 2d |
| 7 | Day 7 | Day 0–7 | Full 7d window |
| 30 | Day 30 | Day 0–30 | All windows full |

Each inference cycle triggers: `mdk.pre_processing(cutoff) → mdk.score → mdk.analyze`.

### What the dashboard sees

```
GET /api/runs?workflow_name=mdk.analyze&limit=200

→ [
    { "workflow_hash": "fff...", "end_time": "...", "status": "SUCCESS" },  ← Day N (newest)
    { "workflow_hash": "eee...", "end_time": "...", "status": "SUCCESS" },  ← Day N-1
    ...
    { "workflow_hash": "aaa...", "end_time": "...", "status": "SUCCESS" },  ← Day 1 (oldest)
  ]
```

Each hash → download `fleet_risk_scores.json` → extract per-device `mean_risk`, `te_score`, `tier`, etc. → stitch into time-series arrays client-side. Each cycle's data reflects the full accumulated history up to that day, not just that day's slice.

### Demo scenario

The operator trains the model (CLI), then starts the simulation from the dashboard. With `asic_aging` scenario (180 days, 15 devices):
- **Days 1–10**: Fleet healthy, all devices green in day banner
- **Days 10–30**: Progressive degradation onset → first yellow/orange days appear
- **Days 30–60**: Multiple devices affected → commands appear in queue
- **Days 60–180**: Operator approves underclocking → system learns → auto-approves similar actions

The story: *"Watch the fleet degrade over months and the system respond — navigate any day to see the full history."*

---

## What This Adds Over MOS Demo

| MOS Demo (demo.mos.tether.io) | Our Dashboard | Delta |
|------|------|-------|
| Static fleet snapshot | **Live timeline** — fleet health evolving cycle by cycle | Temporal awareness |
| Fleet hashrate / device count / state | Same **+** fleet-wide TE health score, anomaly risk distribution | AI-derived health layer |
| Per-device telemetry time-series | Same **+** anomaly probability overlay, TE score trend, regime change markers | Predictive context |
| Static threshold alerts (T > 80°C) | ML-driven anomaly detection with 10 failure mode types + trend-based escalation | Proactive vs reactive |
| Manual overclock/underclock per device | AI-generated commands with tier logic, safety overrides, rationale, MOS RPC mapping | Automated reasoning |
| No approval workflow | Full approval gate: human confirms/denies commands, system learns preferences over time | Governance layer |
| No audit trail | Immutable hash-chain audit: every decision traceable to actor + timestamp | Compliance |
| No pipeline visibility | Full workflow DAG: task status, duration, logs, artifacts | Operational transparency |
| No predictive maintenance | Multi-horizon forecasts (1h/6h/24h/7d) with quantile uncertainty bands | Forward-looking |

---

## Dashboard Init & Polling

### On page load — build the timeline from history

```
1. GET /api/health
   → Verify backend is up

2. GET /api/runs?workflow_name=mdk.analyze&status=SUCCESS&limit=50
   → Get all historical inference run hashes + timestamps
   → Sort by end_time ascending (oldest first)

3. For each run hash (parallel, Promise.all):
   GET /api/files/{hash}/download?file_name=fleet_risk_scores.json&task_name=score_fleet
   → Parse JSON, extract device_risks[]

4. Build client-side timeline state:
   timeline = [
     { t: "T+1h", hash: "aaa...", devices: { "ASIC-001": { risk: 0.05, te: 0.92, tier: "HEALTHY" }, ... } },
     { t: "T+2h", hash: "bbb...", devices: { "ASIC-001": { risk: 0.12, te: 0.88, tier: "HEALTHY" }, ... } },
     { t: "T+3h", hash: "ccc...", devices: { "ASIC-001": { risk: 0.45, te: 0.75, tier: "WARNING" }, ... } },
     ...
   ]

5. Also download from the LATEST hash only:
   GET /api/files/{latest_hash}/download?file_name=fleet_actions.json&task_name=optimize_fleet
   GET /api/files/{latest_hash}/download?file_name=fleet_metadata.json&task_name=ingest_telemetry
   GET /api/files/{latest_hash}/download?file_name=trend_analysis.json&task_name=analyze_trends
   → These are only needed for the current cycle (not historical)
```

### Polling loop — detect new cycles

```javascript
// Poll every 30s
setInterval(async () => {
  const runs = await fetch("/api/runs?workflow_name=mdk.analyze&status=SUCCESS&limit=1");
  const data = await runs.json();
  const latest = data.runs?.[0];

  if (latest && latest.workflow_hash !== lastKnownHash) {
    // New inference cycle completed!
    const snapshot = await downloadSnapshot(latest.workflow_hash);
    timeline.push({ t: latest.end_time, hash: latest.workflow_hash, devices: snapshot });
    lastKnownHash = latest.workflow_hash;

    // Also refresh current-cycle data (actions, trends)
    refreshCurrentCycleData(latest.workflow_hash);

    // Re-render charts
    renderTimeline();
  }

  // Also check if any pipeline is currently running
  const running = await fetch("/api/runs?status=RUNNING&limit=1");
  const runningData = await running.json();
  if (runningData.runs?.length > 0) {
    showPipelineSpinner(runningData.runs[0]);
  }
}, 30_000);
```

---

## Dashboard Views

### View 1: Fleet Timeline (landing page)

**Purpose**: Show fleet health evolving over time. This is the centerpiece — the evaluator sees the fleet degrade and the system respond.

**Panels**:

| Panel | Data source | Description |
|-------|------------|-------------|
| **Tier evolution** (stacked area) | `tier` per device per cycle | Y = device count, X = cycle time. Colors: green/yellow/orange/red stacked. Shows tier migration over time. |
| **Risk heatmap** | `mean_risk` per device per cycle | Rows = devices, columns = cycles, color = risk (green→red). Anomaly onset appears as color shift. |
| **Fleet hashrate** (line) | `latest_snapshot.hashrate_th` summed per cycle | Total fleet TH/s over time. Drops when devices get underclocked. |
| **Fleet avg TE** (line) | Avg `te_score` across devices per cycle | Fleet efficiency trend. |
| **Current snapshot cards** | Latest cycle only | Tier counts, worst device, hashrate utilization, pending commands count. |
| **Command activity log** | `fleet_actions.json` from each cycle | Table: time, device, command, tier, status (pending/approved/executed). Grows over cycles. |

**How to build the tier evolution chart**:

```javascript
// For each cycle in timeline[]
const tierCounts = timeline.map(cycle => {
  const counts = { CRITICAL: 0, WARNING: 0, DEGRADED: 0, HEALTHY: 0 };
  for (const device of Object.values(cycle.devices)) {
    counts[device.tier]++;
  }
  return { t: cycle.t, ...counts };
});

// Render as stacked area chart (recharts, d3, chart.js, etc.)
// X axis = time, Y axis = device count, stacked by tier
```

**How to build the risk heatmap**:

```javascript
// Collect all device IDs from first cycle
const deviceIds = Object.keys(timeline[0].devices);

// Build matrix: rows = devices, cols = cycles
const matrix = deviceIds.map(id =>
  timeline.map(cycle => cycle.devices[id]?.mean_risk ?? 0)
);

// Render as heatmap (green 0 → yellow 0.3 → red 1.0)
```

---

### View 2: Device Detail (drill-down)

**Purpose**: Full inspection of a single device, including its history across cycles.

**Panels**:

| Panel | Data | Notes |
|-------|------|-------|
| Device header | `fleet_metadata.fleet[]` filtered by device_id | Model, stock specs (clock, voltage, hashrate, power, chip count) |
| **Risk over time** (line) | `mean_risk` for this device across all cycles | Shows when anomaly appeared and worsened |
| **TE over time** (line) | `te_score` for this device across all cycles | Shows efficiency degradation |
| **Telemetry sparklines** | `latest_snapshot` fields across cycles | Temp, hashrate, power mini-charts |
| Risk gauge (current) | Latest cycle `mean_risk` | Gauge widget, color-coded |
| TE gauge (current) | Latest cycle `te_score` | Gauge widget with thresholds 0.8/0.6 |
| Live telemetry | Latest `latest_snapshot` | Temp, voltage, hashrate, power, ambient, operating mode |
| Predictions | Latest `predictions` | Fan chart: p10/p50/p90 bands across 1h/6h/24h/7d horizons |
| Trend analysis | `trend_analysis.json` from latest cycle | Direction, slope, regime change, CUSUM, projections |
| Controller tier | `fleet_actions.actions[]` | Tier badge, command list, rationale, MOS alert codes |
| Command history | Commands for this device across cycles | Shows what was proposed and when |

**Device risk over time** — built from timeline:

```javascript
const deviceHistory = timeline.map(cycle => ({
  t: cycle.t,
  risk: cycle.devices[deviceId].mean_risk,
  te: cycle.devices[deviceId].te_score,
  temp: cycle.devices[deviceId].temperature_c,
  hashrate: cycle.devices[deviceId].hashrate_th,
}));
// Render as multi-line chart with risk threshold lines at 0.3, 0.5, 0.9
```

**Trend analysis schema** (from latest cycle only):
```json
{
  "device_id": "asic_aging_ASIC-000",
  "te_trends": {
    "1h":  { "slope_per_hour": -0.002, "r_squared": 0.02, "direction": "stable" },
    "6h":  { "slope_per_hour": 0.001, "r_squared": 0.15, "direction": "stable" },
    "24h": { "slope_per_hour": 0.003, "r_squared": 0.11, "direction": "stable" },
    "7d":  { "slope_per_hour": -0.004, "r_squared": 0.32, "direction": "falling_fast" }
  },
  "regime": {
    "change_detected": true,
    "change_index": 106,
    "direction": "increasing",
    "max_cusum_pos": 123.6,
    "max_cusum_neg": 322.73
  },
  "projections": {
    "0.8": { "hours_to_crossing": null, "confidence": 0.11, "will_cross": false },
    "0.6": { "hours_to_crossing": null, "confidence": 0.11, "will_cross": false }
  }
}
```

---

### View 3: Command Approval Flow (the governance differentiator)

**Purpose**: Human-in-the-loop approval of AI-generated mining commands. This is the key UX improvement over MOS — the AI proposes, the human governs. Over time, the system learns operator preferences.

**Temporal aspect**: New commands appear as each inference cycle detects new or worsening anomalies. The command queue grows over cycles. Approved commands (with `remember: true`) train the system to auto-approve similar future actions.

**Interaction flow**:

```
1. Inference cycle N completes → new fleet_actions.json
     ↓
2. Dashboard detects new cycle → shows new/changed commands in queue
     ↓
3. Operator reviews command + rationale + device context
     ↓
4. Operator clicks "Execute" → POST /api/proposals
     ↓
5. Validance pipeline: catalog → rate-limit → learned-policy → approval gate
     ↓
6a. Auto-approved (learned policy matches) → executes in container → result
6b. Needs approval → operator confirms → executes → result
     ↓
7. Result displayed on dashboard. If "Remember" was checked, similar
   future commands auto-approve.
```

**Key endpoints**:

#### Submit a command as a proposal
```
POST /api/proposals
Content-Type: application/json

{
  "action": "set_clock",
  "parameters": {
    "device_id": "asic_aging_ASIC-001",
    "value_ghz": 0.945,
    "mos_method": "setFrequency"
  },
  "session_hash": "<operator_session>",
  "notify_url": "http://<dashboard>/webhook/approval"
}
```

**Response** (auto-approved or no gate):
```json
{
  "status": "completed",
  "workflow_hash": "abc123...",
  "result": {
    "output": "Frequency set to 0.945 GHz for asic_aging_ASIC-001",
    "exit_code": 0
  },
  "duration_seconds": 2.3
}
```

**Response** (needs human approval):
```json
{
  "status": "pending_approval",
  "approval_id": "appr_xyz...",
  "workflow_hash": "abc123..."
}
```

#### Resolve pending approval
```
POST /api/approvals/{approval_id}/resolve
Content-Type: application/json

{
  "decision": "approved",
  "reason": "Operator confirmed underclock for thermal safety",
  "decided_by": "operator@site",
  "remember": true,
  "match_pattern": {
    "action": "set_clock",
    "device_model": "S19XP"
  }
}
```

**Response**:
```json
{
  "approval_id": "appr_xyz...",
  "status": "approved",
  "decided_by": "operator@site",
  "learned_rule_id": "rule_123..."
}
```

> When `remember: true`, the system creates a **learned policy rule** so future identical commands auto-approve. This is the "system learns operator preferences" feature.

#### View learned policies
```
GET /api/policies?session_hash=<session>
```

```json
{
  "rules": [
    {
      "rule_id": "rule_123...",
      "template_name": "set_clock",
      "scope": "allow",
      "match_pattern": { "action": "set_clock", "device_model": "S19XP" },
      "created_at": "2026-04-06T...",
      "reason": "Operator confirmed underclock for thermal safety"
    }
  ]
}
```

#### Revoke a learned policy
```
DELETE /api/policies/{rule_id}
```

**UI**: Show learned rules as a "policy dashboard" — list of auto-approve/deny rules the operator has taught the system, with a "Revoke" button for each. Over cycles, this list grows as the operator approves more command types.

---

### View 4: Pipeline Monitor

**Purpose**: Show the data pipeline execution status. During the simulation, the operator can watch each inference cycle's pipeline progress in real time.

**Temporal aspect**: Each cycle is a separate workflow run. The run history table grows as cycles complete. The DAG view shows the current (or most recent) cycle's task progress.

**Endpoints**:

| Panel | Endpoint | What it shows |
|-------|----------|---------------|
| Workflow list | `GET /api/workflows` | Registered workflows: `mdk.pre_processing`, `mdk.train`, `mdk.score`, `mdk.analyze`, `mdk.generate_corpus`, `mdk.generate_batch`, `mdk.fleet_simulation` |
| Run history | `GET /api/runs?limit=20` | All cycles, newest first. Shows status, timing. |
| Current/selected run DAG | `GET /api/workflows/{name}/topology?workflow_hash={hash}` | Visual DAG with task status colors |
| Run detail | `GET /api/workflows/{name}/status?workflow_hash={hash}` | Per-task status, duration, errors |
| Task logs | `GET /api/logs/{workflow_hash}?task_name={task}` | Stdout/stderr from container execution |
| Artifacts | `GET /api/files/{workflow_hash}?file_type=output` | Output files per cycle |

**Topology response** (for DAG visualization):
```json
{
  "workflow_name": "mdk.analyze",
  "workflow_hash": "abc123...",
  "nodes": [
    { "id": "ingest_telemetry", "label": "ingest_telemetry", "type": "task", "status": "success" },
    { "id": "engineer_features", "label": "engineer_features", "type": "task", "status": "success" },
    { "id": "compute_true_efficiency", "label": "compute_true_efficiency", "type": "task", "status": "running" },
    { "id": "score_fleet", "label": "score_fleet", "type": "task", "status": "pending" },
    { "id": "analyze_trends", "label": "analyze_trends", "type": "task", "status": "pending" },
    { "id": "optimize_fleet", "label": "optimize_fleet", "type": "task", "status": "pending" },
    { "id": "generate_report", "label": "generate_report", "type": "task", "status": "pending" }
  ],
  "edges": [
    { "source": "ingest_telemetry", "target": "engineer_features" },
    { "source": "engineer_features", "target": "compute_true_efficiency" },
    { "source": "compute_true_efficiency", "target": "score_fleet" },
    { "source": "score_fleet", "target": "analyze_trends" },
    { "source": "score_fleet", "target": "optimize_fleet" },
    { "source": "analyze_trends", "target": "optimize_fleet" },
    { "source": "optimize_fleet", "target": "generate_report" }
  ]
}
```

> Color-coded nodes: green=success, blue=running, gray=pending, red=failed. During a cycle, the operator watches nodes light up in sequence.

---

### View 5: Audit Trail

**Purpose**: Full traceability for compliance and debugging. Every command approval, policy creation, and pipeline execution is recorded with actor + timestamp in an immutable hash chain.

**Endpoints**:

```
GET /api/audit/{entity_id}              → Event history for an entity
GET /api/audit/{entity_id}/verify       → Verify hash chain integrity
GET /api/audit/stats                    → Global event count
```

**Audit event schema**:
```json
{
  "id": 42,
  "event_type": "approval_resolved",
  "entity_type": "proposal",
  "entity_id": "proposal_abc12345",
  "actor": "operator@site",
  "timestamp": "2026-04-06T10:30:00Z",
  "details": {
    "decision": "approved",
    "reason": "Confirmed underclock",
    "learned_rule_id": "rule_123"
  },
  "event_hash": "sha256:...",
  "previous_event_hash": "sha256:..."
}
```

**Chain verification**:
```json
{
  "entity_id": "proposal_abc12345",
  "valid": true,
  "integrity_check": "passed",
  "events_verified": 5
}
```

**UI**: Timeline view per entity, with a "Verify Integrity" button. Over the course of the demo, the audit log fills with decisions the operator made — demonstrating the governance trail.

---

### View 6: Model Performance (read-only)

**Purpose**: Show ML model quality metrics for evaluator/stakeholder trust.

**Data source**: `model_metrics.json` (download from training workflow artifacts).

```json
{
  "model": "XGBClassifier",
  "train_samples": 1507827,
  "anomaly_rate": 0.4121,
  "devices": 57,
  "feature_count": 50,
  "threshold": 0.3,
  "top_features": [
    { "feature": "te_score", "importance": 0.1465 },
    { "feature": "efficiency_jth_mean_7d", "importance": 0.0844 }
  ],
  "per_anomaly_type": {
    "thermal_deg": { "train_positives": 5823, "devices_affected": 1 },
    "psu_instability": { "train_positives": 95232, "devices_affected": 4 },
    "hashrate_decay": { "train_positives": 144536, "devices_affected": 4 }
  }
}
```

**UI**: Feature importance bar chart, anomaly type breakdown table, training stats card.

---

## JSON Schemas (per-cycle snapshots)

These are the files downloaded per inference cycle. The frontend parses them client-side.

### fleet_risk_scores.json

Top-level:
```json
{
  "scoring_window_hours": 24,
  "window_start": "2026-09-27 23:55:00",
  "window_end": "2026-09-28 23:55:00",
  "samples_scored": 4032,
  "threshold": 0.3,
  "device_risks": [...],
  "model_versions": { "classifier": "anomaly_model.joblib" }
}
```

Per device in `device_risks[]`:
```json
{
  "device_id": "asic_aging_ASIC-001",
  "model": "S19XP",
  "mean_risk": 1.0,
  "max_risk": 1.0,
  "pct_flagged": 1.0,
  "last_risk": 1.0,
  "flagged": true,
  "latest_snapshot": {
    "timestamp": "2026-09-28 23:55:00",
    "te_score": 0.7169,
    "true_efficiency": 32.76,
    "temperature_c": 19.36,
    "voltage_v": 0.33,
    "hashrate_th": 79.52,
    "power_w": 2241.7,
    "cooling_power_w": 464.0,
    "ambient_temp_c": -9.73,
    "operating_mode": "underclock"
  },
  "predictions": {
    "te_score_1h": { "p10": 0.64, "p50": 0.70, "p90": 0.72 },
    "te_score_6h": { "p10": 0.61, "p50": 0.68, "p90": 0.71 },
    "te_score_24h": { "p10": 0.55, "p50": 0.65, "p90": 0.70 },
    "te_score_7d": { "p10": 0.40, "p50": 0.58, "p90": 0.67 }
  }
}
```

### fleet_actions.json

Top-level:
```json
{
  "controller_version": "2.0-tier-only",
  "scoring_window": { "start": "...", "end": "..." },
  "tier_counts": { "CRITICAL": 9, "WARNING": 5 },
  "actions": [...],
  "safety_constraints_applied": ["fleet_redundancy_per_model", "thermal_low_limit_20C"]
}
```

Per device in `actions[]`:
```json
{
  "device_id": "asic_aging_ASIC-001",
  "model": "S19XP",
  "tier": "CRITICAL",
  "risk_score": 1.0,
  "te_score": 0.7169,
  "commands": [
    {
      "type": "set_clock",
      "value_ghz": 0.945,
      "priority": "HIGH",
      "mos_method": "setFrequency",
      "note": "V/f coupled — voltage adjusts implicitly with frequency"
    },
    {
      "type": "set_fan_mode",
      "value": "min",
      "priority": "HIGH",
      "mos_method": "setFanControl"
    },
    {
      "type": "schedule_inspection",
      "urgency": "immediate",
      "priority": "HIGH",
      "mos_method": null,
      "mos_note": "Operational — no direct MOS RPC equivalent"
    }
  ],
  "rationale": [
    "SAFETY: temperature 19.4°C < 20.0°C — low-temp warning...",
    "Risk 1.00, TE_score 0.717 → tier CRITICAL"
  ],
  "trend_context": {
    "direction": "stable",
    "slope_per_hour": 0.00072,
    "r_squared": 0.0246,
    "regime_change": true
  },
  "mos_alert_codes": ["P:1", "V:1", "R:1"]
}
```

### fleet_metadata.json (static — download once)

```json
{
  "parameters": { "num_devices": 57, "scenario_count": 5 },
  "fleet": [
    {
      "device_id": "asic_aging_ASIC-000",
      "model": "S19XP",
      "stock_clock_ghz": 1.35,
      "stock_voltage_v": 0.35,
      "nominal_hashrate_th": 141.0,
      "nominal_power_w": 3010.0,
      "nominal_efficiency_jth": 21.3,
      "nominal_chip_count": 342,
      "nominal_hashboard_count": 3
    }
  ]
}
```

### trend_analysis.json (from latest cycle)

Per device in `devices[]`:
```json
{
  "device_id": "asic_aging_ASIC-000",
  "current_state": { "te_score": 0.8859, "temperature_c": 30.7, "mean_risk": 0.0505 },
  "te_trends": {
    "1h":  { "slope_per_hour": -0.002, "r_squared": 0.02, "direction": "stable", "n_samples": 12 },
    "6h":  { "slope_per_hour": 0.001, "r_squared": 0.15, "direction": "stable", "n_samples": 72 },
    "24h": { "slope_per_hour": 0.003, "r_squared": 0.11, "direction": "stable", "n_samples": 288 },
    "7d":  { "slope_per_hour": -0.004, "r_squared": 0.32, "direction": "falling_fast", "n_samples": 2016 }
  },
  "temp_trends": {
    "6h":  { "slope_per_hour": 3.94, "r_squared": 0.86, "last_ewma": 30.73 },
    "24h": { "slope_per_hour": 1.2, "r_squared": 0.45, "last_ewma": 29.5 }
  },
  "regime": {
    "change_detected": true,
    "change_index": 106,
    "direction": "increasing",
    "max_cusum_pos": 123.6,
    "max_cusum_neg": 322.73
  },
  "projections": {
    "0.8": { "hours_to_crossing": null, "confidence": 0.11, "will_cross": false },
    "0.6": { "hours_to_crossing": null, "confidence": 0.11, "will_cross": false }
  },
  "primary_direction": "stable",
  "primary_slope_per_hour": 0.003
}
```

---

## Command Types Reference

| Type | MOS method | Parameters | Meaning |
|------|-----------|-----------|---------|
| `set_clock` | `setFrequency` | `value_ghz: float` | Change ASIC frequency (voltage V/f-coupled) |
| `set_fan_mode` | `setFanControl` | `value: "min" \| "normal" \| "max"` | Fan speed (air-cooled only) |
| `set_power_mode` | `setPowerMode` | `value: "sleep" \| "normal"` | Sleep mode = no heat generation |
| `schedule_inspection` | `null` | `urgency: "immediate" \| "next_window" \| "deferred"` | Operational flag (not a device RPC) |
| `set_monitoring_interval` | `null` | `value_seconds: int` | Internal pipeline config |
| `hold_settings` | `null` | — | No-op (device is healthy) |
| `suggest_overclock` | `setFrequency` | `value_ghz: float` | Performance suggestion |

---

## Tier Definitions

| Tier | Condition | Color | Hex | Action |
|------|-----------|-------|-----|--------|
| CRITICAL | mean_risk > 0.9 | Red | `#dc2626` | Underclock 70%, immediate inspection, monitor every 60s |
| WARNING | mean_risk > 0.5 | Orange | `#f59e0b` | Underclock 85%, next-window inspection, monitor every 120s |
| DEGRADED | te_score < 0.8 (risk ≤ 0.5) | Yellow | `#eab308` | Minor tuning, monitor every 180s |
| HEALTHY | Otherwise | Green | `#22c55e` | Hold settings, suggest mild overclock if thermal headroom |

Risk score color gradient: `green (0) → yellow (0.3) → orange (0.5) → red (0.9) → dark red (1.0)`

---

## Endpoint Quick Reference

### Read data (GET)

| Endpoint | Purpose | Polling frequency |
|----------|---------|-------------------|
| `GET /api/health` | System health check | On load |
| `GET /api/runs?limit=N&workflow_name=X&status=Y` | **Discover workflow hashes (timeline)** | Every 30s |
| `GET /api/files/{hash}/download?file_name=X&task_name=Y` | **Download JSON snapshot per cycle** | On new hash |
| `GET /api/workflows/{name}/status?workflow_hash={hash}` | Workflow execution detail | On click / on new hash |
| `GET /api/workflows/{name}/topology?workflow_hash={hash}` | DAG for visualization | On click |
| `GET /api/workflows` | List registered workflows | On load |
| `GET /api/files/{workflow_hash}` | List output files | On click |
| `GET /api/variables/{workflow_hash}?variable_name=X` | Get task output variables | On click |
| `GET /api/logs/{workflow_hash}?task_name=X` | Task stdout/stderr | On click |
| `GET /api/policies?session_hash=X` | Learned policy rules | On approval |
| `GET /api/audit/{entity_id}` | Audit event history | On click |
| `GET /api/audit/{entity_id}/verify` | Verify audit chain | On click |
| `GET /api/audit/stats` | Global audit count | On load |

### Write / action (POST/DELETE)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/proposals` | Submit command for execution (goes through approval pipeline) |
| `POST /api/workflows/{name}/trigger` | Trigger a pipeline run manually |
| `POST /api/approvals/{id}/resolve` | Approve or deny a pending command |
| `DELETE /api/policies/{rule_id}` | Revoke a learned policy rule |
| `DELETE /api/workflows/{hash}/cancel` | Cancel a running workflow |
| `DELETE /api/sessions/{hash}` | Clean up session containers |

### Real-time

| Endpoint | Type | Purpose |
|----------|------|---------|
| `GET /api/workflows/{hash}/stream` | SSE (text/event-stream) | Token streaming from running tasks |

---

## Interaction Scenarios (temporal)

### Scenario A: "Fleet degrades over time"
1. Simulation starts → dashboard shows all green (cycle 1)
2. Cycles 2-4: Operator watches passively — fleet still healthy
3. Cycle 5: First device crosses `mean_risk > 0.3` → turns yellow in heatmap
4. Cycle 7: Device worsens to WARNING → orange → commands appear in queue
5. Cycle 9: More devices affected → tier evolution chart shows green shrinking, orange/red growing
6. Operator can scroll the timeline back to see when degradation started

### Scenario B: "Operator intervenes"
1. Operator sees 3 CRITICAL devices at cycle 8
2. Clicks into command queue → reviews AI rationale
3. Approves underclock for all 3 with `remember: true`
4. `POST /api/proposals` × 3 → commands execute
5. Next cycles: if simulation modeled command effects, risk would drop (currently simulation doesn't close the loop — commands are logged but don't feed back into physics engine)
6. Learned policy visible in policy dashboard → future similar commands auto-approve

### Scenario C: "Watch pipeline in real time"
1. During an active cycle, operator opens Pipeline Monitor
2. DAG shows tasks lighting up: ingest_telemetry (green) → engineer_features (green) → compute_true_efficiency (blue, running) → score_fleet (gray, pending)
3. Operator clicks running task → sees live logs via `GET /api/logs/{hash}?task_name=compute_true_efficiency`
4. When cycle completes, dashboard auto-refreshes fleet view with new data

### Scenario D: "Post-mortem audit"
1. After the demo, operator opens Audit Trail
2. Searches for a specific device command
3. Sees full chain: command_proposed → policy_checked → approval_pending → operator_approved → command_executed
4. Clicks "Verify Integrity" → green checkmark (SHA-256 hash chain valid)
5. Every decision is traceable — this is the compliance story

---

## Notes for Frontend Developer

1. **API base URL** — The frontend must use `https://20.199.13.38` as the API base URL (no port number). A Caddy reverse proxy on port 443 (self-signed TLS) forwards to the dev API on port 8001. **Never use `http://` or `:8001` directly** — browsers block mixed HTTP/HTTPS content from an HTTPS-hosted dashboard. On first visit, the browser will show a certificate warning (self-signed) — accept it once.

2. **CORS** — already configured for `https://lovable.dev` and the Lovable project URL. Add your origin to `modules/api.py` `allow_origins` if needed.

3. **No auth on API** — the API is behind network-level access control (Caddy proxy). No token/cookie needed for dev.

4. **Polling, not push** — Status endpoints are polling-friendly (return current state). Poll `GET /api/runs` every 30s to detect new cycles. For live pipeline execution within a cycle, use the SSE stream endpoint. No WebSocket.

5. **File download** — JSON files are served as binary downloads. Parse client-side. Files are typically 10KB–500KB.

6. **Pagination** — Only `/api/runs` supports pagination (`limit`, `offset`, `since`, `until`). Other list endpoints return all results.

7. **Timestamps** — All timestamps are ISO 8601. Pipeline data uses `YYYY-MM-DD HH:MM:SS` format (no timezone — assumed UTC).

8. **The proposal pipeline is the integration point** — When the operator clicks "Execute" on a command, it goes through `POST /api/proposals`. Catalog validation, rate limiting, learned policies, and approval gates all happen server-side. The frontend doesn't implement any of that logic.

9. **Session management** — Use a consistent `session_hash` per operator session. This groups proposals, learned policies, and audit events. Generate it client-side (e.g., SHA-256 of `dashboard:<random>`).

10. **Client-side state** — The timeline is built client-side by downloading one JSON per cycle and stitching them into arrays. No server-side aggregation needed. For 12-20 cycles this is ~2-5 MB total.

11. **Simulation doesn't close the loop** — When the operator approves a command (e.g., underclock), the simulation engine doesn't currently feed that back into the physics model. Commands are logged and audited, but the simulated telemetry continues on its predetermined path. This is a known limitation — in production, commands would execute via MOS RPC and affect real hardware.

12. **Simulation is UI-triggerable** — The dashboard triggers `POST /api/workflows/mdk.fleet_simulation/trigger` to start a simulation. This runs `orchestrate_simulation.py` inside a container (Pattern 5a), which in turn triggers inner workflow runs. Inner cycles appear as separate `mdk.analyze` runs in `GET /api/runs`, providing full pipeline visibility.

13. **`scenario_path` uses `file://` URI** — The trigger parameter `scenario_path` must be a `file:///` URI pointing to the host filesystem path (e.g., `file:///home/Wik-dev/repos/mining_optimization/data/scenarios/asic_aging.json`). The engine resolves this on the host and copies it into the container's `/work/` directory.

14. **Growing-window data model** — Each cycle's data includes ALL history from day 0 to that day (not just that day's batch). Time-series charts will show growing history, not independent snapshots. Rolling feature windows (6h, 24h, 7d) are properly populated.

15. **Day banner** — New UI component: horizontal strip of day buttons at top, color-coded by worst tier that day (green/yellow/orange/red), clickable for any snapshot, auto-advances to latest completed day.

16. **Cycle = 1 simulated day** (at default interval). Cycle count comes from `scenario.duration_days / interval_days`, not user input. Scenario durations: asic_aging=180d, cooling_failure=60d, psu_degradation=90d, summer_heatwave=90d, baseline=30d.

17. **Container `api_url`** — When the simulation runs inside a container (Pattern 5a), it must reach the host API via `http://172.17.0.1:8001` (Docker bridge gateway), not `localhost`. The dashboard should pass this as the `api_url` trigger parameter. The frontend itself uses `https://20.199.13.38` (Caddy), but the container-to-host path is different.
