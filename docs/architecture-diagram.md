# Architecture Diagram — Fleet Intelligence Pipeline

**Two-Layer Architecture: ML Detection → AI Reasoning → Approval Gate**

```mermaid
flowchart TD
    subgraph HW["Hardware Layer"]
        FLEET["ASIC Fleet<br/>S21-HYD · M66S · S19XP · S19jPro · S19kPro · A1566<br/>(6 models, 10–15 devices per scenario)"]
        SENSORS["Site Sensors<br/>Ambient temp, energy price"]
        COOLING["Cooling System<br/>Per-device proportional controller"]
    end

    subgraph PP["mdk.pre_processing (shared prefix)"]
        INGEST["[1] ingest_telemetry<br/>CSV → Parquet, schema validation"]
        FE["[2] engineer_features<br/>55 features per sample"]
        KPI["[3] compute_true_efficiency<br/>TE decomposition, health score"]
    end

    subgraph TRAIN_WF["mdk.train"]
        TRAIN["train_anomaly_model<br/>XGBoost + quantile regressors"]
    end

    subgraph SCORE_WF["mdk.score"]
        SCORE["score_fleet<br/>24h window → risk scores"]
    end

    subgraph ANALYZE["mdk.analyze"]
        TRENDS["analyze_trends<br/>CUSUM, slope, regime detection"]
        OPT["optimize_fleet<br/>Tier classification + safety overrides"]
        REPORT["generate_report<br/>HTML dashboard"]
    end

    subgraph REASONING["AI Reasoning Layer (SafeClaw)"]
        AGENT["LLM Agent<br/>Reads: tiers, risk scores, trends<br/>Reads: market data, operator KB"]
        PROPOSE["Proposes: specific MOS commands<br/>with rationale and context"]
    end

    subgraph GOVERNANCE["Governance Layer (Validance)"]
        GATE["Approval Gate<br/>Human review before execution"]
        AUDIT["Audit Trail<br/>Content-addressed execution chain"]
        POLICY["Learned Policies<br/>Rate limits, budget enforcement"]
    end

    subgraph EXEC["Command Execution"]
        MOS_RPC["MOS RPC Commands<br/>setFrequency · setPowerMode<br/>setFanControl · reboot"]
    end

    %% Hardware → Pre-processing
    FLEET -->|"raw telemetry"| INGEST
    SENSORS -->|"ambient, price"| INGEST
    COOLING -->|"cooling power"| INGEST

    %% Pre-processing flow
    INGEST --> FE --> KPI

    %% Training path
    KPI -->|"continue_from"| TRAIN

    %% Inference path
    KPI -->|"continue_from"| SCORE
    TRAIN -->|"model artifact"| SCORE

    %% Analysis
    SCORE -->|"continue_from"| TRENDS
    TRENDS --> OPT
    OPT --> REPORT

    %% ML → AI Reasoning
    OPT -->|"fleet_actions.json<br/>(tiers, safety flags)"| AGENT
    AGENT --> PROPOSE

    %% Reasoning → Governance
    PROPOSE -->|"proposed commands"| GATE
    GATE --> AUDIT
    GATE --> POLICY

    %% Governance → Execution
    GATE -->|"approved commands"| MOS_RPC

    %% Styling
    classDef hw fill:#e8d5b7,stroke:#8b6914,color:#333
    classDef pp fill:#d4e6f1,stroke:#2980b9,color:#333
    classDef train fill:#d5f5e3,stroke:#27ae60,color:#333
    classDef score fill:#fadbd8,stroke:#e74c3c,color:#333
    classDef analyze fill:#f9e79f,stroke:#f39c12,color:#333
    classDef reason fill:#e8daef,stroke:#8e44ad,color:#333
    classDef govern fill:#d6eaf8,stroke:#2471a3,color:#333
    classDef exec fill:#fdebd0,stroke:#d35400,color:#333

    class FLEET,SENSORS,COOLING hw
    class INGEST,FE,KPI pp
    class TRAIN train
    class SCORE score
    class TRENDS,OPT,REPORT analyze
    class AGENT,PROPOSE reason
    class GATE,AUDIT,POLICY govern
    class MOS_RPC exec
```

## Workflow Composition

```
Training path:
  mdk.generate_corpus → mdk.pre_processing → mdk.train
                         (continue_from)      (continue_from)

Inference path:
  mdk.pre_processing → mdk.score → mdk.analyze
  (continue_from)      (continue_from)

Continuous simulation:
  mdk.fleet_simulation (persistent orchestrator)
    Cycle 0:  mdk.pre_processing → mdk.train
    Cycle 1+: mdk.pre_processing → mdk.score → mdk.analyze
```

## Two-Layer Architecture

| Layer | Role | Components | Output |
|-------|------|-----------|--------|
| **ML Detection** | Perceive & classify | pre_processing, train/score, analyze | Tiers, risk scores, safety flags |
| **AI Reasoning** | Decide & propose | SafeClaw agent + operator KB | Specific MOS commands + rationale |
| **Governance** | Approve & audit | Validance approval gate + policies | Approved commands + audit trail |

The ML layer outputs deterministic, reproducible observations. The AI agent adds contextual reasoning (market conditions, maintenance schedules, operator preferences). The governance layer ensures every action is traceable and approved.

## Data Flow Summary

```
Hardware ──→ fleet_telemetry.csv
         ──→ telemetry.parquet
         ──→ features.parquet + kpi_timeseries.parquet
         ──→ anomaly_model.joblib (training) / fleet_risk_scores.json (inference)
         ──→ trend_analysis.json + fleet_actions.json (tiers + safety flags)
         ──→ SafeClaw agent proposes MOS commands
         ──→ Validance approval gate
         ──→ MOS RPC execution + HTML report
```
