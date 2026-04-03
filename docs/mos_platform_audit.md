# MOS (MiningOS) Platform Audit — Integration Reference

> Compiled from docs.mos.tether.io, GitHub repos (tetherto/miningos-wrk-*), and the demo at demo.mos.tether.io.
> The demo is a JavaScript SPA and cannot be scraped statically — this document reconstructs the full data model from the source code and documentation.

---

## 1. Architecture Overview

MOS uses a **worker-based** architecture where every physical device (miner, container, sensor, power meter) is managed by a dedicated worker process. Workers are organized into **racks** (logical groupings), and an **orchestrator** (`miningos-wrk-ork`) aggregates data across all racks.

```
┌──────────────────────────────────────────────┐
│         miningos-wrk-ork (Orchestrator)      │
│  • Unified RPC interface                     │
│  • Action approval & voting                  │
│  • Cross-rack data aggregation               │
│  • Time-series storage (Hyperbee)            │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
    ▼          ▼          ▼
 Rack 1     Rack 2     Rack N
 (miners)  (containers)(sensors/powermeters)
    │          │          │
    ▼          ▼          ▼
 Hardware   Hardware   Hardware
```

**Communication:** Hyperswarm P2P RPC (no centralized server).
**Storage:** Hyperbee (time-series logs, keyed as `thing-5m-id`, `thing-alerts`, `stat-timeframe`).
**Snapshot interval:** Configurable, default `collectSnapsItvMs: 60000` (60s). Our synthetic data uses 5-min intervals — both are realistic.

---

## 2. Telemetry Fields Collected Per Miner Snapshot

Source: `miningos-wrk-miner-antminer` and `miningos-wrk-miner-whatsminer` repos.

### 2a. Hashrate

| Field | Description | Notes |
|---|---|---|
| `hashrate_5s` | Real-time hashrate (5-second average) | Most volatile |
| `hashrate_5m` | 5-minute rolling average | Closest to our `hashrate_th` |
| `hashrate_30m` | 30-minute rolling average | Smoothed trend |
| `hashrate_nominal` | Expected hashrate at stock settings | Per model, stored in config |

**Gap vs our pipeline:** We have a single `hashrate_th` field. MOS provides three temporal resolutions. Consider adding a short-window rolling mean in features.py to approximate the 5s/5m/30m hierarchy, or at minimum document the mapping.

### 2b. Temperature

| Field | Description | Notes |
|---|---|---|
| `temp_pcb_inlet` | PCB inlet temperature | Per hashboard |
| `temp_pcb_outlet` | PCB outlet temperature | Per hashboard |
| `temp_chip` | Chip junction temperature (current) | Hardware reports only instantaneous — avg/max are duplicated |
| `temp_ambient` | Ambient/inlet air temperature | Site-level |

**Alert thresholds from MOS config:**
- PCB: 75°C warning, 80°C critical
- Inlet: 40°C warning, 45°C critical (high); 25°C warning, 20°C critical (low)

**Gap vs our pipeline:** We have `temperature_c` (single value) and `ambient_temp_c`. MOS tracks per-hashboard inlet/outlet temps separately. Our `TEMP_HARD_LIMIT = 80°C` in optimize.py aligns with MOS's critical PCB threshold. We should add the **low-temperature alert** (20°C critical) — hydro-cooled sites at latitude 64.5°N could hit this in winter.

### 2c. Power

| Field | Description | Notes |
|---|---|---|
| `power_w` | Total ASIC power consumption | Available on S19XP-Hydro, S21, S21 Pro (not standard S19XP) |
| `efficiency_wths` | W/TH/s (instantaneous) | Computed from power/hashrate |
| `nominal_efficiency_wths` | Expected efficiency at stock | Per model in config |

**Nominal efficiency values from MOS config:**
- `miner-am-s19xp_h`: 20.8 W/TH/s
- `miner-am-s19xp`: 21.0 W/TH/s
- `miner-am-s21`: 17.5 W/TH/s
- `miner-am-s21pro`: 17.0 W/TH/s

**Gap vs our pipeline:** Our synthetic models (S21-HYD, M66S, S19XP, S19jPro) use J/TH (= W/TH/s ÷ 1000). The nominal values are close but not identical — this is fine for synthetic data, but the report should note the MOS convention is W/TH/s.

### 2d. Hashboard Status

| Field | Description |
|---|---|
| `hashboard_count` | Number of active hashboards |
| `hashboard_status[]` | Per-board health (chips found vs expected) |
| `chip_count` | Total working chips |

**Gap vs our pipeline:** We don't model per-hashboard granularity at all. This is a meaningful omission for the predictive maintenance use case — a board dropping chips is a strong pre-failure signal. Worth mentioning in the report as a future enhancement.

### 2e. Pool & Share Statistics

| Field | Description |
|---|---|
| `shares_accepted` | Accepted shares count |
| `shares_rejected` | Rejected shares count |
| `shares_stale` | Stale shares count |
| `pool_url` | Active pool URL |
| `pool_worker` | Worker name on pool |
| `pool_status` | alive / dead |

From the **Ocean pool worker** (`miningos-wrk-minerpool-ocean`):
- Hashrate monitoring at 60s, 1h, 24h intervals (pool-side view)

**Gap vs our pipeline:** We have no pool/share data at all. Rejected share ratio is a proxy for network issues or hardware instability. Not critical for efficiency KPI, but relevant for a production controller.

### 2f. Network & System

| Field | Description |
|---|---|
| `ip_address` | Device IP |
| `mac_address` | MAC address (used as unique ID when serial unavailable) |
| `firmware_version` | Current firmware |
| `uptime` | Device uptime in seconds |
| `fan_speed` | Fan RPM (air-cooled models) |
| `fan_mode` | Auto / manual / max |
| `led_status` | Blink on/off |

---

## 3. Power Meter Telemetry (Site-Level)

Source: `miningos-wrk-powermeter-schneider` (Schneider PM5340, P3U30) and Satec PM180.

| Field | Description |
|---|---|
| `voltage_L1/L2/L3` | Per-phase voltage (Modbus registers 0x0000–0x0006) |
| `current_L1/L2/L3` | Per-phase current |
| `power_active` | Active power (kW) |
| `power_reactive` | Reactive power (kVAR) |
| `power_apparent` | Apparent power (kVA) |
| `power_factor` | Power factor |
| `frequency_hz` | Grid frequency |
| `energy_total_kwh` | Accumulated energy |

**Gap vs our pipeline:** We have `energy_price_kwh` as a site-level field, but no actual grid-level power metering. In a real MOS deployment, the power meter data enables total site power tracking, which is needed for accurate cooling overhead calculation. Our `cooling_power_w` is synthetic — in production it would come from subtracting ASIC power from total metered power.

---

## 4. Control Commands Available via MOS

These are the RPC methods available through the worker API — the commands our controller (`optimize.py`) should emit.

### 4a. Miner Control

| Command | Method | Parameters | Notes |
|---|---|---|---|
| **Reboot** | `reboot` | `[]` | 2-3 min recovery |
| **Set power mode** | `setPowerMode` | `["normal"]` or `["sleep"]` | Sleep = idle, 10-20s transition |
| **Set pools** | `setPools` | `[pool_config]` | Auto-reboots after change |
| **Set LED** | `setLED` | `[true/false]` | Blink to locate device |
| **Set fan control** | `setFanControl` | fan speed / mode | Air-cooled models only |
| **Set frequency** | `setFrequency` | frequency settings | **This is the clock control** — directly maps to our `set_clock` command |
| **Set network** | `setNetwork` | IP/DNS config | Static or DHCP |
| **Update password** | `setPassword` | new admin password | Security |

**Critical finding:** MOS exposes `setPowerMode` and `setFrequency` as the primary tuning controls. Our controller's `set_clock` and `set_voltage` commands should map to these. However, **MOS does not expose direct voltage control** — voltage is typically coupled to frequency in firmware. Our `set_voltage` command is a modeling simplification. The report should note that in production, frequency adjustment implicitly adjusts voltage via the V/f curve.

### 4b. Orchestrator Actions (Write Operations)

The orchestrator uses a **multi-voter approval system** for write operations:
```json
{
  "reqVotesPos": 2,  // Requires 2 positive votes to approve
  "reqVotesNeg": 1   // 1 negative vote cancels action
}
```

This is a safety mechanism — our controller's commands wouldn't execute instantly but would go through an approval pipeline. Worth mentioning in the security/safety section of the report.

---

## 5. Alert System

MOS has a built-in alert framework with per-model, per-severity rules.

### Temperature Alerts (Antminer)

| Model | Metric | Warning | Critical |
|---|---|---|---|
| S19XP-Hydro | PCB temp | 75°C | 80°C |
| S19XP-Hydro | Inlet temp (high) | 40°C | 45°C |
| S19XP-Hydro | Inlet temp (low) | 25°C | 20°C |
| S21 / S21 Pro | PCB temp | 75°C | 80°C |
| S21 / S21 Pro | Inlet temp (high) | 40°C | 45°C |

### Hardware Alerts

| Code | Description | Severity |
|---|---|---|
| `R:1` | Low hashrate | High |
| `N:1` | High hashrate | Medium |
| `V:1` | Power initialization error | Critical |
| `V:2` | PSU not calibrated | High |
| `J0:8` | Insufficient hashboards | Critical |
| `P:1` | High temperature protection triggered | Critical |
| `P:2` | Low temperature protection triggered | Critical |
| `J[0-7]:4` | EEPROM data error | High |
| `J0:6` | Temperature sensor error | High |
| `M:1` | Memory allocation error | Medium |
| `J[0-2]:2` | Chip insufficiency | High |
| `L[0-2]:1` | Voltage/frequency exceeds limit | Critical |
| `L[0-2]:2` | Voltage/frequency mismatch | High |

**Integration opportunity:** Our anomaly labels (`label_thermal_deg`, `label_psu_instability`, `label_hashrate_decay`) map loosely to MOS error codes:
- `thermal_deg` → `P:1`, `P:2`, high PCB temp alerts
- `psu_instability` → `V:1`, `V:2`, `L[0-2]:1/2`
- `hashrate_decay` → `R:1`, `J[0-2]:2`, `J0:8`

The controller should emit alerts using MOS-compatible error codes in production.

### Pool Alerts

- Dead pool detection
- Incorrect pool configuration
- Worker name mismatch

---

## 6. Supported Hardware Models

### Antminer (Bitmain)
- S19 XP (air-cooled)
- S19 XP Hydro (liquid-cooled, with power monitoring)
- S21
- S21 Pro

### Whatsminer (MicroBT)
- M30SP, M30SPP
- M53S
- M56S
- M63

### Power Meters
- Schneider PM5340 (Modbus TCP)
- Schneider P3U30 (protection relay)
- Satec PM180

### Our synthetic fleet mapping

| Our model | Closest MOS equivalent | Notes |
|---|---|---|
| S21-HYD | `miner-am-s21` (hydro variant) | Our nominal 15 J/TH ≈ MOS's 17.5 W/TH/s |
| M66S | `miner-wm-m63` (closest Whatsminer) | M66S doesn't exist in MOS yet — fictional model |
| S19XP | `miner-am-s19xp` | Direct match, 21 J/TH vs 21 W/TH/s |
| S19jPro | Not in MOS | Older gen, not currently supported |

---

## 7. Dashboard / UI Structure

Source: `miningos-app-ui` repo and docs.

The MOS dashboard provides:

1. **Fleet overview** — total hashrate, device count, active/idle/error states
2. **Explorer** — drill-down per device with full telemetry history
3. **Alerts panel** — active alerts by severity with dismiss/acknowledge
4. **Pool management** — pool configuration, share statistics
5. **Container view** — physical layout (rack/shelf/position) mapped to logical IDs
6. **Power monitoring** — site-level power from power meter workers
7. **Time-series charts** — hashrate, temperature, power, efficiency over time
8. **Historical data** — `tailLog` RPC for accessing stored snapshots

**Visual alignment for our report.html:**
- Our TE timeseries chart maps to MOS's time-series hashrate/efficiency view
- Our health score heatmap has no direct MOS equivalent — this is value-add
- Our controller actions table maps to MOS's action approval queue
- **Missing from our report:** per-hashboard status, share rejection ratio, pool health

---

## 8. Summary — What We Should Integrate

### Already aligned with MOS

- ✅ Hashrate, power, temperature telemetry (core fields match)
- ✅ 60s–5min snapshot intervals (compatible)
- ✅ Nominal efficiency per model (we have it, MOS has it)
- ✅ Temperature alert thresholds (our 80°C aligns with MOS critical)
- ✅ Power mode control (MOS `setPowerMode` ≈ our `set_clock` + idle mode)
- ✅ Rack-based device organization

### Should add or adjust

- ✅ **Low-temperature alert** — Two-tier system in `optimize.py`: T < 10°C → sleep mode + immediate inspection (coolant freeze risk); 10–20°C → underclock 70% + minimize fan (air-cooled only). Hydro vs air-cooled distinction enforced.
- ✅ **Map `set_voltage` to `setFrequency`** — All `set_voltage` commands eliminated from `optimize.py`. Replaced with `set_clock` + V/f coupling notes. `MOS_COMMAND_MAP` added; `annotate_mos_methods()` stamps every command with its MOS RPC method. `fleet_actions.json` never contains a command type without a known MOS mapping.
- ✅ **MOS error codes** — `MOS_ALERT_CODES` dict and `TIER_ANOMALY_MAP` added to `optimize.py`. Each action annotated with `mos_alert_codes`. Report includes MOS Codes column in actions table and a reference table. Note: tier-based mapping is approximate; production would use per-anomaly-type classifier output.
- ✅ **Action approval system** — Orange callout banner in `report.html` documenting the MOS multi-voter approval system (`reqVotesPos: 2`, `reqVotesNeg: 1`). Commands are presented as recommendations entering an approval queue.
- 🟡 **Hashrate resolution** — Added `WINDOW_VERY_SHORT = 6` (30 min) in `features.py` with `hashrate_th_mean_30m` and `hashrate_th_std_30m`. Approximates MOS hashrate_30m hierarchy, but 5-min sampling cannot replicate 5s intra-sample granularity. Features available for future model iterations (noted in `train_model.py`).
- ✅ **Use W/TH/s convention** — Equivalence footnote added to report after TE decomposition section: "1 J/TH = 1 W/TH/s".

### Future enhancements (mention in report)

- 🔲 Per-hashboard telemetry (chip count, board-level temperature)
- 🔲 Pool share rejection ratio as anomaly signal
- 🔲 Grid power meter integration for true site-level cooling overhead
- 🔲 Uptime/reboot tracking as device health indicator
- 🔲 Firmware version tracking for fleet homogeneity
