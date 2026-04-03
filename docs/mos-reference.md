# MOS / MDK Reference — Sources & Integration Notes

Reference documentation for MiningOS and the Mining Development Kit, grounded in the open-source worker repositories that define the actual telemetry, control commands, and architecture patterns.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MiningOS Stack                               │
│                                                                     │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────┐             │
│  │ Antminer    │   │ Whatsminer   │   │ Ocean Pool   │  Workers    │
│  │ Worker      │   │ Worker      │   │ Worker       │  (Racks)    │
│  └──────┬──────┘   └──────┬──────┘   └──────┬───────┘             │
│         │                 │                  │                      │
│  ┌──────┴──────┐   ┌──────┴──────┐          │                      │
│  │ Schneider   │   │ Sensor      │          │                      │
│  │ Power Meter │   │ Workers     │          │                      │
│  └──────┬──────┘   └──────┬──────┘          │                      │
│         │                 │                  │                      │
│         └────────┬────────┴──────────┬───────┘                     │
│                  ▼                   │                              │
│         ┌────────────────┐          │                              │
│         │  Orchestrator  │◄─────────┘                              │
│         │  (wrk-ork)     │   Hyperswarm P2P RPC                    │
│         │                │   Action voting / approval              │
│         │  Unified query │   Fleet-wide aggregation                │
│         └───────┬────────┘                                         │
│                 │                                                   │
│                 ▼                                                   │
│         ┌────────────────┐                                         │
│         │  MOS Dashboard │   React UI (via MDK UI Kit)             │
│         │  / MDK Apps    │                                         │
│         └────────────────┘                                         │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Repositories

### Antminer Worker — [tetherto/miningos-wrk-miner-antminer](https://github.com/tetherto/miningos-wrk-miner-antminer)

**The primary source for our pipeline's telemetry model.** Manages Bitmain Antminer devices (S19 XP, S19 XP Hydro, S21, S21 Pro) via HTTP with digest auth.

**Telemetry fields:**

| Field | Description |
|---|---|
| `hashrate_5s`, `hashrate_5m`, `hashrate_30m` | Multi-interval hashrate averages |
| `power_watts` | Real-time power consumption |
| `efficiency_wths` | Computed W/TH/s |
| `temp_pcb_inlet`, `temp_pcb_outlet` | PCB surface temperatures |
| `temp_chip` | Chip junction temperature |
| `temp_ambient` | Ambient temperature |
| `shares_accepted`, `shares_rejected`, `shares_stale` | Pool share stats |
| `hashboard_perf` | Per-hashboard metrics |

**Temperature alert thresholds:**

| Component | Warning | Critical |
|---|---|---|
| PCB (high) | 75°C | 80°C |
| Inlet (high) | 40°C | 45°C |
| Inlet (low) | 25°C | 20°C |

Our pipeline uses 80°C as the thermal hard limit in `optimize.py` — aligned with the PCB critical threshold from this worker. Low-temperature alerts are also implemented: 20°C warning (underclock + fan min for air-cooled) and 10°C emergency (sleep mode + inspection for coolant freeze risk at the 64.5°N site).

**Nominal efficiency (W/TH/s):**

| Model | Nominal |
|---|---|
| S19 XP Hydro | 20.8 |
| S19 XP | 21.0 |
| S21 | 17.5 |
| S21 Pro | 17.0 |

**Control commands** (via `queryThing` RPC):
- `reboot` — device restart
- `setPowerMode` — `"sleep"` or `"normal"`
- `setFrequency` — clock frequency adjustment
- `setLED` — identification blink
- Fan speed configuration
- `setPools` — pool endpoint and credentials

Our controller (`optimize.py`) emits `set_clock`, `set_power_mode`, `set_fan_mode`, and `schedule_inspection` commands. These map to `setFrequency`, `setPowerMode`, `setFanControl`, and operator alerts respectively. No `set_voltage` commands are emitted — voltage is V/f coupled and managed implicitly through frequency adjustment. Every command is annotated with its MOS RPC method via `MOS_COMMAND_MAP`.

### Whatsminer Worker — [tetherto/miningos-wrk-miner-whatsminer](https://github.com/tetherto/miningos-wrk-miner-whatsminer)

Same data model as Antminer, different brand. Covers MicroBT Whatsminer M30SP, M30SPP, M53S, M56S, M63. Confirms the telemetry field set is cross-vendor.

### Orchestrator — [tetherto/miningos-wrk-ork](https://github.com/tetherto/miningos-wrk-ork)

**Central coordination layer.** Registers distributed workers (racks), aggregates metrics fleet-wide, and gates write operations through a voting system.

**Core concepts:**

```
Rack (worker) ──manages──► Thing (device)
                              │
Orchestrator ◄──registers─────┘
     │
     ├── listThings()     MongoDB-style fleet queries
     ├── pushAction()     Propose a write operation
     ├── voteAction()     Approve / reject
     └── tailLog()        Time-series queries (Hyperbee)
```

**Action voting state machine:**
```
voting → ready → executing → done
         ↑ reqVotesPos met
voting → cancelled
         ↑ any single negative vote (fail-closed)
```

This maps directly onto our pipeline: the orchestrator's `pushAction` + `voteAction` is analogous to an approval gate. Our controller's tier-based commands would flow through this voting system in production.

**Fleet aggregation operations:** `sum`, `avg`, `obj_concat`, `arr_concat`, `alerts_aggr` — enabling cross-rack queries like "all miners with efficiency > 20 W/TH/s and temp > 70°C" in a single call.

### Power Meter Worker — [tetherto/miningos-wrk-powermeter-schneider](https://github.com/tetherto/miningos-wrk-powermeter-schneider)

Monitors Schneider PM5340/P3U30 industrial power meters via Modbus TCP/IP.

**Key fields:**
- Voltage (L-N, L-L per phase), current (per phase + neutral)
- Power: active, reactive, apparent
- Power factor, frequency
- Energy import/export
- Total harmonic distortion (THD) per phase

**Collection intervals:** 5s RTD + 60s snapshots. The 5-second granularity enables real-time power anomaly detection.

Our `energy_price_kwh` and `power_w` telemetry fields model the data this worker provides in production.

### Ocean Pool Worker — [tetherto/miningos-wrk-minerpool-ocean](https://github.com/tetherto/miningos-wrk-minerpool-ocean)

Polls Ocean.xyz mining pool API for hashrate, share stats, earnings, and block data.

**Collection schedule:**

| Interval | Data |
|---|---|
| 1 min | Hashrate (60s/1h/24h averages), earnings, balances |
| 5 min | Per-worker stats (persisted) |
| Daily | Transactions, block data, pool luck |

The 1h vs 24h hashrate divergence is a fleet-level signal for pool performance anomalies.

## Mapping to Our Pipeline

```
MOS Worker Fields              Our Synthetic Telemetry       Pipeline Stage
──────────────────────         ────────────────────────      ──────────────
hashrate_5m                →   hashrate_th                   ingest
power_watts                →   power_w                       ingest
efficiency_wths            →   efficiency_jth                ingest
temp_chip                  →   temperature_c                 ingest
temp_ambient               →   ambient_temp_c                ingest
(power meter active W)     →   cooling_power_w               ingest
(pool hashrate averages)   →   (future: pool integration)    —

setFrequency               ←   set_clock command             optimize
setPowerMode               ←   set_power_mode (sleep/normal) optimize
setFanControl              ←   set_fan_mode (air-cooled)     optimize
(operator alert)           ←   schedule_inspection           optimize
pushAction + voteAction    ←   approval gate                 workflow
(alert codes P:1, R:1...)  ←   mos_alert_codes annotation    optimize
```

## Browse All Repos

- **GitHub org:** [github.com/tetherto](https://github.com/tetherto) — 125 repos, `miningos-*` prefix
- **Official announcement:** [Tether Open Sources MOS & Mining SDK](https://tether.io/news/tether-open-sources-the-next-generation-of-bitcoin-mining-infrastructure-with-mos-mining-os-mining-sdk/)

## Documentation

- [docs.mos.tether.io](https://docs.mos.tether.io) — MOS architecture, dashboard, device support, alerts
- [mos.tether.io](https://mos.tether.io) — Product landing page
- [docs.mdk.tether.io](https://docs.mdk.tether.io) — MDK developer reference (backend SDK, React UI kit)
- [mdk.tether.io](https://mdk.tether.io) — MDK landing page
