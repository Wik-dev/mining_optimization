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
│  All inner workflows share the dashboard's session_hash            │
│  (passed as trigger parameter → CTX_SESSION_HASH)                  │
└───────────────────────────┬─────────────────────────────────────────┘
                            │
                            ↓
┌─────────────────────────────────────────────────────────────────────┐
│  Validance API  (REST, port 8001)                                   │
│                                                                     │
│  GET /api/executions?session={hash} → all runs for this simulation │
│  GET /api/files/{score_hash}/download → fleet_risk_scores.json     │
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
      training_hash: "fa6d414fd91dd1ab",  // hash of the training run — model resolved via deep context
      api_url: "http://172.17.0.1:8001",  // container reaches host API via Docker bridge
      interval_days: "1",
      session_hash: sessionHash,  // propagated to all inner workflows (→ CTX_SESSION_HASH)
    },
    session_hash: sessionHash,    // also set on the outer workflow execution record
  }),
});
const { workflow_hash } = await res.json();
// All 540+ inner workflows (generate_batch, pre_processing, score, analyze × 180)
// share this session_hash — queryable via GET /api/executions?session=
```

**Backend prerequisite** (run by operator before dashboard use):

```bash
# Train model (all 5 scenarios, ~35 min) — only needed once
# Produces a training hash (e.g., fa6d414fd91dd1ab) used by the dashboard trigger.
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
// Query all runs for this simulation session (ADR-002)
const res = await fetch(`/api/executions?session=${sessionHash}`);
const { workflows } = await res.json();

// Group by workflow type
const scores = workflows.filter(w => w.workflow_name === "mdk.score" && w.status === "SUCCESS");
const completedCycles = scores.length;

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
GET /api/executions?session={sessionHash}

→ {
    "session_hash": "dash_9b320ec6-...",
    "workflows": [
      { "workflow_hash": "aaa...", "workflow_name": "mdk.pre_processing", "status": "SUCCESS",
        "parameters": { "cutoff_timestamp": "2026-04-03T00:00:00", ... } },
      { "workflow_hash": "bbb...", "workflow_name": "mdk.score",          "status": "SUCCESS", ... },
      { "workflow_hash": "ccc...", "workflow_name": "mdk.analyze",        "status": "SUCCESS", ... },
      // cycle 2...
      { "workflow_hash": "ddd...", "workflow_name": "mdk.pre_processing", "status": "SUCCESS",
        "parameters": { "cutoff_timestamp": "2026-04-04T00:00:00", ... } },
      { "workflow_hash": "eee...", "workflow_name": "mdk.score",          "status": "SUCCESS", ... },
      { "workflow_hash": "fff...", "workflow_name": "mdk.analyze",        "status": "SUCCESS", ... },
      ...
    ]
  }
```

Each cycle = 3 sequential workflows. The `cutoff_timestamp` in `mdk.pre_processing` parameters identifies which simulated day this cycle represents — use it to place data points on the timeline.

### File ownership per workflow

**Important**: each workflow type owns different output files. Use the correct `workflow_hash` when downloading.

| Workflow | Task | File | Description |
|----------|------|------|-------------|
| `mdk.pre_processing` | `ingest_telemetry` | `fleet_telemetry.csv` | Raw telemetry (large) |
| `mdk.pre_processing` | `ingest_telemetry` | `fleet_metadata.json` | Device specs (static) |
| `mdk.pre_processing` | `compute_true_efficiency` | `kpi_timeseries.parquet` | Feature data |
| **`mdk.score`** | **`score_fleet`** | **`fleet_risk_scores.json`** | **Risk scores — primary dashboard data** |
| `mdk.analyze` | (none) | — | Outputs are task variables, not files |

```javascript
// To get fleet_risk_scores.json for a cycle:
// 1. Find the mdk.score workflow for that cycle
const scores = workflows.filter(w => w.workflow_name === "mdk.score" && w.status === "SUCCESS");
// 2. Download using the SCORE hash (not analyze hash)
const data = await fetch(`/api/files/${scores[i].workflow_hash}/download?file_name=fleet_risk_scores.json&task_name=score_fleet`);
```

Each cycle's data reflects the full accumulated history up to that day (growing window), not just that day's slice.

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

2. GET /api/executions?session={sessionHash}
   → Get all workflow runs for this simulation session
   → Filter to mdk.score with status=SUCCESS → these are the completed cycles
   → Sort by start_time ascending (oldest first)

3. For each mdk.score hash (parallel, Promise.all):
   GET /api/files/{score_hash}/download?file_name=fleet_risk_scores.json&task_name=score_fleet
   → Parse JSON, extract device_risks[]
   → Map to cutoff_timestamp from the corresponding mdk.pre_processing parameters

4. Build client-side timeline state:
   timeline = [
     { t: "2026-04-03", scoreHash: "aaa...", devices: { "ASIC-001": { risk: 0.05, te: 0.92, tier: "HEALTHY" }, ... } },
     { t: "2026-04-04", scoreHash: "bbb...", devices: { "ASIC-001": { risk: 0.12, te: 0.88, tier: "HEALTHY" }, ... } },
     { t: "2026-04-05", scoreHash: "ccc...", devices: { "ASIC-001": { risk: 0.45, te: 0.75, tier: "WARNING" }, ... } },
     ...
   ]

5. Also download from the LATEST cycle's hashes:
   GET /api/files/{latest_score_hash}/download?file_name=fleet_actions.json&task_name=optimize_fleet
   GET /api/files/{latest_pp_hash}/download?file_name=fleet_metadata.json&task_name=ingest_telemetry
   → fleet_metadata is static (same across cycles — download once)
   → fleet_actions changes per cycle (commands depend on current risk state)
```

### Polling loop — detect new cycles

```javascript
// Poll every 30s — single call gets all simulation runs
setInterval(async () => {
  const res = await fetch(`/api/executions?session=${sessionHash}`);
  const { workflows } = await res.json();

  // Find completed score workflows (each = one cycle with downloadable data)
  const scores = workflows
    .filter(w => w.workflow_name === "mdk.score" && w.status === "SUCCESS")
    .sort((a, b) => a.start_time.localeCompare(b.start_time));

  if (scores.length > timeline.length) {
    // New cycle(s) completed — download new snapshots
    for (const score of scores.slice(timeline.length)) {
      const snapshot = await downloadSnapshot(score.workflow_hash);

      // Get cutoff_timestamp from the corresponding pre_processing run
      const pp = workflows.find(w =>
        w.workflow_name === "mdk.pre_processing" &&
        w.status === "SUCCESS" &&
        w.parameters?.cutoff_timestamp &&
        w.start_time < score.start_time  // pp runs before score in each cycle
      );
      const cutoff = pp?.parameters?.cutoff_timestamp;

      timeline.push({
        t: cutoff || score.end_time,
        scoreHash: score.workflow_hash,
        devices: snapshot,
      });
    }
    renderTimeline();
  }

  // Show spinner if anything is still running
  const running = workflows.filter(w => w.status === "RUNNING");
  if (running.length > 0) {
    showPipelineSpinner(running[running.length - 1]);
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
  "action": "fleet_underclock",
  "parameters": {
    "device_id": "ASIC-011",
    "target_pct": 80,
    "reason": "high temperature — risk score 0.87"
  },
  "session_hash": "<operator_session>",
  "notify_url": "http://<dashboard>/webhook/approval",
  "input_files": {
    "fleet_risk_scores.json": "@<score_hash>.score_fleet:risk_scores",
    "fleet_metadata.json": "@<preproc_hash>.ingest_telemetry:metadata"
  }
}
```

**`input_files`** (optional) — Maps container filename → source reference. The engine resolves each reference, downloads the file (from Azure blob storage), and stages it into the task's working directory before execution. Supported reference formats:

| Format | Example | Resolves via |
|--------|---------|-------------|
| Cross-workflow | `@cff4ee635e7489d1.score_fleet:risk_scores` | `task_variables` table lookup by workflow_hash + task + variable |
| Direct URI | `azure://workflow-data-dev/outputs/.../file.json` | `FileStorage.retrieve_file()` directly |

**Which hashes to use:** The dashboard gets workflow hashes from `GET /api/executions?session={simSessionHash}`. Use the `score` workflow hash for `score_fleet:risk_scores` and the `pre_processing` workflow hash for `ingest_telemetry:metadata`. These hashes change each simulation cycle — always use the latest completed cycle's hashes.

**`fleet_actions.json` is optional** — `control_action.py` handles missing fleet actions gracefully (`None`). The analyze tasks in Pattern 5a don't register variables in `task_variables`, so `fleet_actions.json` is not available via cross-workflow reference. Omit it from `input_files`.

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

**Response** (needs human approval — `human-confirm` tier, no matching learned policy):

> **Critical: the POST blocks.** The HTTP connection stays open, polling internally every 2 seconds until the approval is resolved or the request times out. The frontend must handle this with two concurrent operations (see "Concurrency pattern" below).

```json
HTTP 200 (eventually, after approval + execution):
{
  "status": "completed",
  "workflow_hash": "abc123...",
  "result": { "output": "...", "exit_code": 0 }
}

Or, if denied:
{
  "status": "denied",
  "workflow_hash": "abc123...",
  "reason": "Action 'fleet_underclock' was denied"
}
```

#### Concurrency pattern (blocking proposal + approval resolution)

The proposal POST blocks until resolved. The frontend needs **two concurrent operations**:

```
User clicks "Execute Underclock"
  │
  ├─ Background: POST /api/proposals { action, parameters, session_hash }
  │    → HTTP connection stays OPEN (30-120s)
  │    → Returns only after approval resolution + container execution
  │
  ├─ Meanwhile: Poll GET /api/approvals?session={sessionHash}&status=pending
  │    → Returns list of pending approvals (appears within ~1s)
  │    → Show approval dialog for each pending item
  │
  ├─ User clicks Approve/Deny in the dialog
  │    → POST /api/approvals/{approval_id}/resolve
  │      { decision: "approved", remember: true, decided_by: "dashboard" }
  │
  └─ Background request unblocks → returns execution result
       { status: "completed", result: { output: "...", exit_code: 0 } }
```

**Implementation:**
```javascript
const submitWithApproval = async (action, parameters) => {
  // Start polling for pending approvals
  startApprovalPolling();  // polls GET /api/approvals?session=...&status=pending every 2-3s

  try {
    // This request BLOCKS until approval + execution complete
    const result = await submitProposal({
      action,
      parameters,
      session_hash: sessionHash,
    }, { timeout: 120000 }); // 2 min timeout — approval wait + container execution

    if (result.status === 'completed') {
      toast.success(`${action} executed successfully`);
    } else if (result.status === 'denied') {
      toast.warning(`Denied: ${result.reason}`);
    }
  } finally {
    stopApprovalPolling();
  }
};
```

**Auto-approve shortcut:** `fleet_status_query` has `approval_tier: "auto-approve"` — no gate, POST returns immediately. Also, if a learned policy matches (from a previous `remember: true` approval), the gate is skipped and the POST returns immediately. Only `fleet_underclock`, `fleet_schedule_maintenance`, and `fleet_emergency_shutdown` require the approval dialog (unless a learned rule exists).

#### Poll for pending approvals

```
GET /api/approvals?session={sessionHash}&status=pending
```

```json
{
  "approvals": [
    {
      "approval_id": "appr_abc123",
      "template_name": "fleet_underclock",
      "proposal": {
        "action": "fleet_underclock",
        "parameters": { "device_id": "ASIC-011", "target_pct": 80, "reason": "high temp" }
      },
      "session_hash": "dash_...",
      "status": "pending",
      "created_at": "2026-04-08T15:30:00Z"
    }
  ]
}
```

Poll every 2-3s while a proposal is in-flight. Stop when no proposals are pending.

#### Approval resolution dialog

When a pending approval appears, show:

```
┌─────────────────────────────────────────────────┐
│  ⚡ Approval Required                           │
│                                                  │
│  Action:    fleet_underclock                      │
│  Device:    ASIC-011 (S19jPro)                   │
│  Target:    80% clock speed                      │
│  Reason:    high temp                            │
│                                                  │
│  ☐ Remember this decision                        │
│    (auto-approve future fleet_underclock actions) │
│                                                  │
│  [ Deny ]                        [ Approve ✓ ]   │
└─────────────────────────────────────────────────┘
```

#### Resolve pending approval

```
POST /api/approvals/{approval_id}/resolve
Content-Type: application/json

{
  "decision": "approved",
  "reason": "Operator confirmed underclock for thermal safety",
  "decided_by": "dashboard",
  "remember": true,
  "match_pattern": {
    "action": "fleet_underclock"
  }
}
```

**Response**:
```json
{
  "approval_id": "appr_xyz...",
  "status": "approved",
  "decided_by": "dashboard",
  "learned_rule_id": "rule_123..."
}
```

> **`remember` belongs HERE, not on proposal submission.** The `POST /api/proposals` request has no `remember` field. The `remember` flag is exclusively on the approval resolution request. When `remember: true`, the backend creates a **learned policy rule** — future proposals matching the same action are auto-approved (gate skipped entirely).

#### View learned policies
```
GET /api/policies?session_hash=<session>
```

```json
{
  "rules": [
    {
      "rule_id": "rule_123...",
      "template_name": "fleet_underclock",
      "scope": "allow",
      "match_pattern": { "action": "fleet_underclock" },
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

#### Catalog templates (what actions exist)

The backend catalog defines exactly four fleet actions. The frontend should offer these as the available commands:

| Template name | Description | Approval tier | Parameters | Rate limit |
|---|---|---|---|---|
| `fleet_status_query` | Read-only fleet health query | auto-approve | `query_type` (summary/device_detail/tier_breakdown/risk_ranking), `device_id` (optional) | 200/session |
| `fleet_underclock` | Reduce device clock speed | human-confirm | `device_id`, `target_pct` (50-100), `reason` | 50/session |
| `fleet_schedule_maintenance` | Schedule device maintenance | human-confirm | `device_id`, `maintenance_type`, `scheduled_date` | 20/session |
| `fleet_emergency_shutdown` | Immediate device shutdown | human-confirm | `device_id`, `reason` | 5/session |

The `fleet_actions.json` from the ML pipeline contains **recommendations** (tier, commands, rationale, cost projections). These are what the operator reviews to decide which proposal to submit. The ML recommends; the operator (or future AI agent) decides which action to actually propose.

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

### Simulation Management (dev/demo only)

**Purpose**: Browse, attach to, and switch between simulation sessions. This is a development and demo concern — in production there are no simulations or scenarios, just one continuous real-world feed.

**Location in sidebar**: Below "New Simulation" button. These are meta-controls for managing test runs, not operator-facing features.

```
Sidebar:
  Fleet Timeline
  Command Approval
  Pipeline Monitor
  Audit Trail
  Model Performance
  ────────────────
  New Simulation        ← triggers State 1 (scenario picker)
  Simulation History    ← opens this view
```

**Data source**: `GET /api/runs?workflow_name=mdk.fleet_simulation&limit=20`

Each `mdk.fleet_simulation` run = one simulation session. The response includes `session_hash`, `status`, `start_time`, `parameters` (scenario, training hash, interval).

**UI**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Simulation History                                              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ● ASIC Aging — 182/180 cycles          Apr 8, 03:46 AM │    │
│  │   session: dash_fa5e8ac5-28e7...       [Attach]         │    │
│  │   Status: SUCCESS  Duration: 1h 12m                     │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ○ ASIC Aging — 29/180 cycles           Apr 8, 02:58 AM │    │
│  │   session: dash_9b320ec6-a96c...       [Attach]         │    │
│  │   Status: KILLED  Duration: 0h 45m                      │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ◐ Cooling Failure — 12/60 cycles       Apr 8, 06:00 AM │    │
│  │   session: dash_c4f12a01-...           [Attach]  LIVE   │    │
│  │   Status: RUNNING  Duration: 5m (ongoing)               │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  [New Simulation]                                                │
└─────────────────────────────────────────────────────────────────┘
```

**Behavior**:

| Action | What happens |
|--------|-------------|
| **Attach** (completed) | Sets `sessionHash`, navigates to **Fleet Timeline**. Timeline re-fetches all cycle data via `GET /api/executions?session=`. Day banner populates with all completed cycles. State 3 (Complete). |
| **Attach** (running) | Same — navigates to **Fleet Timeline** in State 2 (Running). Polling resumes. New cycles appear live. |
| **New Simulation** | Opens State 1 (scenario picker) in **Fleet Timeline**. Generates a fresh `sessionHash`. On trigger, Fleet Timeline transitions to State 2. |

**Key principle**: Fleet Timeline is the single main view. It always shows data from the active `sessionHash`. "Simulation History" and "New Simulation" are just ways to change which session Fleet Timeline is displaying — there is no separate view for past simulations. This mirrors production, where Fleet Timeline shows the one real feed.

**How "Attach" works**:

```javascript
function attachToSession(sessionHash, totalCycles) {
  // 1. Store as the active session
  window.sessionHash = sessionHash;
  localStorage.setItem("activeSession", sessionHash);

  // 2. Clear current timeline
  timeline = [];

  // 3. Re-run the init flow (same as page load)
  //    GET /api/executions?session={sessionHash}
  //    Download all score snapshots
  //    Build timeline
  await initFromSession(sessionHash);

  // 4. Determine state (running vs complete)
  const state = await determineState();
  transitionTo(state);
}
```

**Building the simulation list**:

```javascript
async function loadSimulationHistory() {
  const res = await fetch("/api/runs?workflow_name=mdk.fleet_simulation&limit=20");
  const { runs } = await res.json();

  return runs.map(run => {
    // Extract scenario name from parameters
    const scenarioPath = run.parameters?.scenario_path || "";
    const scenarioName = scenarioPath.split("/").pop()?.replace(".json", "") || "unknown";

    // Get cycle count from this session
    // (could also fetch /api/executions?session= but that's heavy for a list view)
    return {
      sessionHash: run.session_hash,
      scenario: scenarioName,
      status: run.status,           // RUNNING, SUCCESS, FAILED, KILLED
      startTime: run.start_time,
      endTime: run.end_time,
      workflowHash: run.workflow_hash,
    };
  });
}
```

> **Note**: Cycle count per session requires `GET /api/executions?session={hash}` and counting `mdk.score` SUCCESS entries. For the list view, show status + duration instead. Fetch cycle count only when the user clicks a session (or on attach).

**On page load — session recovery**:

```javascript
// Check localStorage for a previous session
const savedSession = localStorage.getItem("activeSession");
if (savedSession) {
  // Verify it still exists and has data
  const res = await fetch(`/api/executions?session=${savedSession}`);
  const { workflows } = await res.json();
  const scores = workflows.filter(w => w.workflow_name === "mdk.score" && w.status === "SUCCESS");

  if (scores.length > 0) {
    await attachToSession(savedSession);
    return;  // skip State 1
  }
}
// No saved session or no data → State 1 (Setup)
```

This prevents the "29 cycles" problem — if the dashboard reloads or the user returns the next day, it re-attaches to the last active session automatically.

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
| `GET /api/executions?session={hash}` | **All runs for this simulation session (ADR-002)** | Every 30s |
| `GET /api/runs?limit=N&workflow_name=X&status=Y` | All runs across sessions (paginated) | Fallback |
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
2. Clicks into Command Approval → reviews ML rationale + cost projections
3. Clicks "Execute Underclock" for ASIC-011 → `POST /api/proposals` fires (blocks in background)
4. Approval dialog appears (from polling `GET /api/approvals?session=...&status=pending`)
5. Operator reviews parameters, checks "Remember this decision", clicks Approve
6. `POST /api/approvals/{id}/resolve` with `remember: true` → learned rule created
7. Blocked proposal unblocks → container executes → result shown in toast
8. Repeats for next 2 devices — but now `fleet_underclock` has a learned rule, so proposals **auto-approve** (no dialog, immediate execution)
9. Learned policy visible in policy panel → operator can revoke if needed
10. Next cycles: commands are logged but simulation doesn't close the loop (commands don't feed back into physics engine — known limitation)

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

## Cycle Visual Dynamics

This section specifies exactly what changes in the dashboard UI when each inference cycle completes. The frontend polls `GET /api/executions?session={hash}` every 30s. When a new `mdk.score` run appears with `status=SUCCESS`, the cycle is complete and the following updates fire.

### Per-cycle update sequence

When cycle N completes (new `mdk.score` SUCCESS detected):

| # | Component | Update | Data source |
|---|-----------|--------|-------------|
| 1 | **Day banner** | New day button appears (appended right). Color = worst tier across all devices this cycle. If user is on "latest" (auto-advance), selection moves to the new day. | `tier` field per device in `fleet_risk_scores.json` |
| 2 | **Tier evolution chart** | New data point appended to each stacked area series. X-axis extends by one tick. | Count of devices per tier this cycle |
| 3 | **Risk heatmap** | New column appended (right edge). Each cell = one device's `mean_risk` this cycle. Heatmap scrolls/grows horizontally. | `mean_risk` per device |
| 4 | **Fleet hashrate line** | New point appended. Shows sum of all devices' `hashrate_th` this cycle. | `latest_snapshot.hashrate_th` summed |
| 5 | **Fleet avg TE line** | New point appended. Shows mean `te_score` across all devices. | `te_score` averaged |
| 6 | **Snapshot cards** | Values update to reflect current cycle: tier counts, worst device, fleet hashrate, pending commands. | Current cycle data |
| 7 | **Command activity log** | New rows appear if this cycle generated new commands (non-`hold_settings` actions). Commands from previous cycles remain in the log. | `fleet_actions.json` from the `mdk.score` hash |
| 8 | **Device detail** (if open) | Risk-over-time and TE-over-time lines extend by one point. Telemetry sparklines update. Current gauges refresh. | Per-device data from this cycle |
| 9 | **Pipeline monitor** (if open) | Previous cycle's DAG turns fully green/red. New cycle's DAG appears (or spinner if next cycle already running). | `GET /api/workflows/{name}/status` |
| 10 | **Progress indicator** | "Cycle N of M" updates. Progress bar advances. | `N = timeline.length`, `M` from scenario metadata |

### Day banner behavior per cycle

```
Cycle 1 completes:
  ┌─────────┐
  │  Day 1  │  ← appears, auto-selected, color from worst tier
  │  [🟢]   │
  └─────────┘

Cycle 2 completes:
  ┌─────────┬─────────┐
  │  Day 1  │  Day 2  │  ← Day 2 appended, auto-selected (user was on latest)
  │  [🟢]   │  [🟢]   │
  └─────────┴─────────┘

Cycle 5 completes (first anomaly):
  ┌─────────┬─────────┬─────────┬─────────┬─────────┐
  │  Day 1  │  Day 2  │  Day 3  │  Day 4  │  Day 5  │
  │  [🟢]   │  [🟢]   │  [🟢]   │  [🟢]   │  [🟡]   │  ← yellow! first DEGRADED device
  └─────────┴─────────┴─────────┴─────────┴─────────┘
                                             ▲ auto-selected

User clicks Day 3 (historical):
  - Day 3 highlighted, all views show Day 3 data
  - New cycles still append to the banner (Days 6, 7, ...)
  - Auto-advance STOPS — user is browsing history
  - "Jump to latest" button appears

User clicks "Jump to latest":
  - Selection jumps to newest day
  - Auto-advance RESUMES
```

### Color assignment per day

```javascript
function dayColor(cycleDevices) {
  const tiers = Object.values(cycleDevices).map(d => d.tier);
  if (tiers.includes("CRITICAL")) return "#dc2626"; // red
  if (tiers.includes("WARNING"))  return "#f59e0b"; // orange
  if (tiers.includes("DEGRADED")) return "#eab308"; // yellow
  return "#22c55e";                                  // green
}
```

### What does NOT change per cycle

| Component | Behavior |
|-----------|----------|
| Fleet metadata (device specs) | Static — downloaded once on init. Same across all cycles. |
| Scenario info (sidebar) | Static — set at simulation start. Duration, device count, anomaly types. |
| Approval pipeline config | Static — catalog templates, trust profiles don't change mid-simulation. |
| Learned policies | Change only on operator action (approve with remember=true), not on cycle completion. |

### First cycle vs subsequent cycles

| Aspect | Cycle 1 | Cycles 2+ |
|--------|---------|-----------|
| Day banner | Created with 1 button | Button appended |
| Charts | Initialized with single data point (no lines yet) | Lines begin to form |
| Heatmap | Single column | Columns accumulate |
| Command log | Usually empty (fleet healthy on Day 1) | Commands appear as anomalies develop |
| Feature windows | Truncated (only 1 day of data) | Progressively fuller (7d window full by Cycle 7) |
| Risk scores | Typically low (model sees limited history) | Increasingly accurate as history grows |

### Transition between dashboard states

```
State 1 (Setup) → State 2 (Running):
  Triggered when: user clicks "Start Simulation" and POST /api/workflows/mdk.fleet_simulation/trigger returns 200
  Visual: setup sidebar slides out, main dashboard area appears with empty charts, spinner shows "Waiting for first cycle..."
  Day banner: empty strip, no buttons yet

State 2 (Running) → first cycle completes:
  Visual: spinner replaced by actual data. Day 1 button appears in banner. All charts render their first data point.

State 2 (Running) → State 3 (Complete):
  Triggered when: mdk.fleet_simulation workflow status = SUCCESS (all cycles done)
  Visual: progress indicator shows "Simulation Complete — N cycles". Auto-advance stops. Day banner fully populated.
  All historical days remain clickable for post-mortem analysis.
```

### Polling frequency guidance

| Phase | Recommended interval | Rationale |
|-------|---------------------|-----------|
| Waiting for first cycle | 10s | Fast feedback after triggering simulation |
| Active simulation (cycles completing) | 30s | Each cycle takes ~20s; 30s catches every cycle within one poll |
| Simulation complete | 60s or stop | No new data expected. Can stop polling entirely. |
| User browsing history | Stop | All data already downloaded. Navigation is client-side only. |

---

## Notes for Frontend Developer

1. **API base URL** — The frontend must use `https://20.199.13.38` as the API base URL (no port number). A Caddy reverse proxy on port 443 (self-signed TLS) forwards to the dev API on port 8001. **Never use `http://` or `:8001` directly** — browsers block mixed HTTP/HTTPS content from an HTTPS-hosted dashboard. On first visit, the browser will show a certificate warning (self-signed) — accept it once.

2. **CORS** — already configured for `https://lovable.dev` and the Lovable project URL. Add your origin to `modules/api.py` `allow_origins` if needed.

3. **No auth on API** — the API is behind network-level access control (Caddy proxy). No token/cookie needed for dev.

4. **Polling, not push** — Status endpoints are polling-friendly (return current state). Poll `GET /api/runs` every 30s to detect new cycles. For live pipeline execution within a cycle, use the SSE stream endpoint. No WebSocket.

5. **File download** — JSON files are served as binary downloads. Parse client-side. Files are typically 10KB–500KB.

6. **Pagination** — Only `/api/runs` supports pagination (`limit`, `offset`, `since`, `until`). Other list endpoints return all results.

7. **Timestamps** — All timestamps are ISO 8601. Pipeline data uses `YYYY-MM-DD HH:MM:SS` format (no timezone — assumed UTC).

8. **The proposal pipeline is the integration point** — When the operator clicks "Execute" on a command, it goes through `POST /api/proposals`. Catalog validation, rate limiting, learned policies, and approval gates all happen server-side. The frontend doesn't implement any of that logic. **Important:** The `POST /api/proposals` request **blocks** (HTTP connection stays open) until the approval is resolved and the container finishes executing. For `human-confirm` actions, the frontend must poll `GET /api/approvals?session=...&status=pending` concurrently and show an approval dialog. See View 3 for the full concurrency pattern.

    **`remember` placement:** The `remember` flag does NOT exist on `POST /api/proposals`. It belongs exclusively on `POST /api/approvals/{id}/resolve`. The proposal submits the action; the approval resolution is where the operator decides whether to teach the system to auto-approve similar actions in the future.

9. **Session hash semantics (important)** — `session_hash` is a correlation primitive: it groups all workflows belonging to one logical simulation run. The engine stores it and queries by it but does not interpret it — the frontend decides when to create a new one vs reuse.

    | Action | Session hash |
    |--------|-------------|
    | "Start Simulation" clicked | **Always generate new**: `dash_${crypto.randomUUID()}` |
    | Page reload during a running sim | **Reuse** from `localStorage` (same logical run) |
    | "Attach" to a past simulation | **Use that simulation's** existing hash |
    | "Resume" / "Retry failed cycle" | **Reuse** (continuity — audit trail grows, cycles append) |

    **Rule: every "Start Simulation" = new `sessionHash`.** Never reuse a session hash across separate simulation triggers. Reusing contaminates the data — cycles from different runs get mixed into the same `/api/executions?session=` response, and the dashboard can't tell them apart. The same hash is only reused for resuming or reloading the same logical run.

    The `sessionHash` must appear in **two places** on the trigger request:
    ```javascript
    {
      parameters: { session_hash: sessionHash },  // → CTX_SESSION_HASH in container → inner workflows
      session_hash: sessionHash,                   // → outer workflow execution record
    }
    ```

10. **Client-side state** — The timeline is built client-side by downloading one JSON per cycle and stitching them into arrays. No server-side aggregation needed. For 12-20 cycles this is ~2-5 MB total.

11. **Simulation doesn't close the loop** — When the operator approves a command (e.g., underclock), the simulation engine doesn't currently feed that back into the physics model. Commands are logged and audited, but the simulated telemetry continues on its predetermined path. This is a known limitation — in production, commands would execute via MOS RPC and affect real hardware.

12. **Simulation is UI-triggerable** — The dashboard triggers `POST /api/workflows/mdk.fleet_simulation/trigger` to start a simulation. This runs `orchestrate_simulation.py` inside a container (Pattern 5a), which in turn triggers inner workflow runs. Inner cycles appear as separate `mdk.analyze` runs in `GET /api/runs`, providing full pipeline visibility.

13. **`scenario_path` uses `file://` URI** — The trigger parameter `scenario_path` must be a `file:///` URI pointing to the host filesystem path (e.g., `file:///home/Wik-dev/repos/mining_optimization/data/scenarios/asic_aging.json`). The engine resolves this on the host and copies it into the container's `/work/` directory.

14. **Growing-window data model** — Each cycle's data includes ALL history from day 0 to that day (not just that day's batch). Time-series charts will show growing history, not independent snapshots. Rolling feature windows (6h, 24h, 7d) are properly populated.

15. **Day banner** — New UI component: horizontal strip of day buttons at top, color-coded by worst tier that day (green/yellow/orange/red), clickable for any snapshot, auto-advances to latest completed day.

16. **Cycle = 1 simulated day** (at default interval). Cycle count comes from `scenario.duration_days / interval_days`, not user input. Scenario durations: asic_aging=180d, cooling_failure=60d, psu_degradation=90d, summer_heatwave=90d, baseline=30d.

17. **Container `api_url`** — When the simulation runs inside a container (Pattern 5a), it must reach the host API via `http://172.17.0.1:8001` (Docker bridge gateway), not `localhost`. The dashboard should pass this as the `api_url` trigger parameter. The frontend itself uses `https://20.199.13.38` (Caddy), but the container-to-host path is different.
