# Mining Optimization

**Plan B Assignment by Tether** — AI-driven optimization for Bitcoin mining operations.

## Objective

Design and prototype intelligent solutions for two core mining challenges:

1. **Chip-level operation optimization** — find the optimal operating point (frequency, voltage, temperature, power consumption, hashrate) for each ASIC chip, adapting in real-time to environmental conditions (weather, cooling capacity, energy availability).

2. **Predictive maintenance** — detect degradation patterns early and predict chip/machine failures before they occur, reducing repair costs and downtime.

This is a design-thinking exploration — the focus is on problem framing, data structuring, and solution architecture rather than a production-ready product.

## Project Materials

Reference documents in [`project_materials/`](project_materials/):

| File | Contents |
|------|----------|
| `project_assignement.pdf` | Official assignment brief — scope, deliverables, data points |
| `Introduction to Bitcoin Mining (WHY → HOW → WHAT).pdf` | Mining fundamentals — from economic rationale to hardware mechanics |
| `Mining Economics.pptx.pdf` | Profitability analysis — energy costs, network difficulty, efficiency trade-offs |
| `gio_kickoff_transcript.txt` | Kickoff call transcript with Gio Galt (Tether) — context, Q&A, expectations |

## Tether Platform References

### [MOS — Mining Operating System](https://mos.tether.io)
Production-grade, open-source OS for Bitcoin mining operations. Self-hosted, P2P architecture (Holepunch). Provides real-time monitoring dashboards, multi-vendor ASIC management, smart energy orchestration, and site-wide analytics. The live demo shows the full spectrum of on-site data points available (temperature, frequency, voltage, power, hashrate per chip).

### [MOS Documentation](https://docs.mos.tether.io)
Operational guides for MOS — installation, device configuration (Antminer, Avalon, Whatsminer), container management, power monitoring, and dashboard setup. Covers the monitoring and alerting capabilities relevant to understanding what data is available from a mining site.

### [MDK — Mining Development Kit](https://mdk.tether.io)
Open-source full-stack development framework for building mining applications. Three-layer architecture: **Adapters** (device drivers for ASICs, sensors, cooling, pools), **Orchestrator** (lifecycle management, safety protocols, event propagation), and **API layer** (unified interface for all devices). Positioned as the integration layer where optimization agents would plug in. v0.1 releasing end of April 2025.

### [MDK Documentation](https://docs.mdk.tether.io)
Developer reference for MDK — backend SDK (JavaScript, device registration, real-time stats), React UI kit (dashboards, charting components), deployment models (embedded, PM2 microservices, Docker/K8s), and time-series storage (Hyperbee). Covers the APIs and data access patterns relevant to building optimization tooling on top of the mining infrastructure.
