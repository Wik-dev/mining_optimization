# Mining Optimization

**Plan B Assignment by Tether** — AI-driven optimization for Bitcoin mining operations.

## Objective

Design and prototype intelligent solutions for two core mining challenges:

1. **Chip-level operation optimization** — find the optimal operating point (frequency, voltage, temperature, power consumption, hashrate) for each ASIC chip, adapting in real-time to environmental conditions (weather, cooling capacity, energy availability).

2. **Predictive maintenance** — detect degradation patterns early and predict chip/machine failures before they occur, reducing repair costs and downtime.

This is a design-thinking exploration — the focus is on problem framing, data structuring, and solution architecture rather than a production-ready product.

## Pipeline Architecture

7-task DAG executed through the Validance workflow engine:

```
Telemetry → Features → KPI → Model → Scoring → Controller → Report

[1] ingest_telemetry        CSV + metadata → Parquet (86,400 rows)
[2] engineer_features       53 features: rolling stats, rates, fleet z-scores
[3] compute_true_efficiency TE formula: voltage-normalized, ambient-corrected
[4a] train_anomaly_model    XGBoost → anomaly_model.joblib (F1=92.8%)
[4b] score_fleet            Load model, score last 24h window
[5] optimize_fleet          Tier-based controller → operational commands
[6] generate_report         HTML dashboard with charts and action table
```

## Documentation

| Document | Contents |
|----------|----------|
| [`docs/true-efficiency-kpi.md`](docs/true-efficiency-kpi.md) | True Efficiency KPI formulation, decomposition, health scoring |
| [`docs/data-generation.md`](docs/data-generation.md) | Synthetic data generator: physics model, anomaly injection, configuration |
| [`docs/mos-reference.md`](docs/mos-reference.md) | MOS/MDK source reference: telemetry fields, control commands, architecture mapping |
| [`docs/technical-report.md`](docs/technical-report.md) | Assignment deliverable: 7-section technical report |

## Project Materials

Reference documents in [`project_materials/`](project_materials/):

| File | Contents |
|------|----------|
| `project_assignement.pdf` | Official assignment brief — scope, deliverables, data points |
| `Introduction to Bitcoin Mining (WHY → HOW → WHAT).pdf` | Mining fundamentals — from economic rationale to hardware mechanics |
| `Mining Economics.pptx.pdf` | Profitability analysis — energy costs, network difficulty, efficiency trade-offs |
| `gio_kickoff_transcript.txt` | Kickoff call transcript with Gio Galt (Tether) — context, Q&A, expectations |

## MOS / MDK References

### Platforms

- [mos.tether.io](https://mos.tether.io) — MiningOS product page (open-source, P2P, Apache 2.0)
- [docs.mos.tether.io](https://docs.mos.tether.io) — MOS documentation (architecture, dashboards, device support, alerts)
- [mdk.tether.io](https://mdk.tether.io) — Mining Development Kit (adapters, orchestrator, API layer)
- [docs.mdk.tether.io](https://docs.mdk.tether.io) — MDK developer reference (backend SDK, React UI kit, deployment)

### Key Source Repositories

| Repository | Role | What we used |
|------------|------|--------------|
| [miningos-wrk-miner-antminer](https://github.com/tetherto/miningos-wrk-miner-antminer) | Antminer worker (S19XP, S21, S21 Pro) | Telemetry fields, temperature thresholds, control commands (`setFrequency`, `setPowerMode`), nominal efficiency values |
| [miningos-wrk-miner-whatsminer](https://github.com/tetherto/miningos-wrk-miner-whatsminer) | Whatsminer worker (M30SP, M53S, M63) | Cross-vendor data model validation |
| [miningos-wrk-ork](https://github.com/tetherto/miningos-wrk-ork) | Orchestrator | Action voting/approval system, fleet-wide aggregation, unified query interface |
| [miningos-wrk-powermeter-schneider](https://github.com/tetherto/miningos-wrk-powermeter-schneider) | Schneider power meter | Modbus register map (voltage, current, power factor, energy), site-level power monitoring |
| [miningos-wrk-minerpool-ocean](https://github.com/tetherto/miningos-wrk-minerpool-ocean) | Ocean pool integration | Hashrate at 60s/1h/24h intervals, share stats, earnings |

The Antminer worker and orchestrator repos provided the most actionable data for our pipeline. See [`docs/mos-reference.md`](docs/mos-reference.md) for the full field mapping and architecture analysis.

### Browse & Press

- [github.com/tetherto](https://github.com/tetherto) — 125 repos, look for `miningos-*` prefix
- [Tether Open Sources MOS & Mining SDK](https://tether.io/news/tether-open-sources-the-next-generation-of-bitcoin-mining-infrastructure-with-mos-mining-os-mining-sdk/) — Official announcement
