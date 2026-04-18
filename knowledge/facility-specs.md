# NordHash Mining — Facility Specifications

## Power Infrastructure

### Grid Connection
- **Utility**: Vattenfall AB (industrial tariff)
- **Transformer**: 50 MW dry-type transformer (ABB), installed 2021
- **Voltage**: 20 kV incoming → 400V 3-phase distribution
- **Circuit breakers**: Per-hall main breakers (Hall A: 3,200A, Hall B: 3,200A, Hall C: 2,400A, Hall D: 1,200A)
- **Power factor correction**: Capacitor bank, target PF > 0.95
- **Metering**: Smart meter with 15-minute interval logging, accessible via Vattenfall portal

### Power Distribution
- Each hall has a main distribution panel feeding per-rack PDUs.
- PDU per rack: 2x 63A 3-phase (redundant feed for top/bottom rack halves).
- Total current draw (all halls): ~55,000A at 400V 3-phase (~38 MW).
- **Headroom**: 12 MW unused capacity (for future expansion or S21 fleet growth).

### Backup Power
- **UPS**: 2x Eaton 9395 (200 kVA each) — covers networking, servers, control systems only.
- **UPS runtime**: 5 minutes at full load. Purpose: graceful network shutdown and MOS state save.
- **No generator**: ASICs restart automatically on power restoration. Generator ROI doesn't justify cost for mining operations (unlike data centers, no data loss risk from hard power-off).

## Cooling System

### Primary: Evaporative Cooling
- **System**: Custom evaporative cooling with Arctic air intake
- **Capacity**: Designed for 50 MW heat dissipation
- **Intake**: 4 large air intake ducts (north wall) drawing outside air
- **Exhaust**: 8 exhaust fans (south wall), each 2.5m diameter
- **Water consumption**: ~500 liters/hour at peak (summer), near-zero in winter (dry cold air)
- **Water source**: Municipal supply with softener (prevents mineral buildup on pads)

### Temperature Targets
- **Ambient intake**: Target < 25°C (achieved naturally 9 months/year in Kiruna)
- **Ambient exhaust**: Typically intake + 12-15°C
- **ASIC chip temperature**: Target 55-65°C under load
- **Alert threshold**: Ambient intake > 30°C triggers supplemental cooling
- **Hard limit**: Ambient intake > 35°C triggers fleet-wide underclock to 80%

### Summer Mitigation (June-August)
- Kiruna summer temperatures occasionally reach 25-30°C (rare above 30°C).
- **Supplemental cooling**: 4 portable evaporative coolers (each 50 kW cooling capacity) deployed in Halls A and B (oldest hardware, least heat-tolerant).
- **Summer operating procedure**: If outdoor temp > 28°C for > 2 hours, pre-emptively underclock Hall A to 90%.
- **Historical max**: 32°C (July 2024) — fleet operated at 85% clock, no hardware damage.

### Winter Advantage
- Kiruna winter temperatures: -10°C to -30°C (November-March).
- Free cooling: intake air is below 0°C, no evaporative system needed.
- **Frost protection**: Air intake has electric heater strips to prevent ice buildup on evaporative pads when switching between modes.
- **PUE in winter**: 1.02-1.04 (near theoretical minimum).

## Network Infrastructure

- **Internet**: 2x 10 Gbps fiber (Telia, redundant paths) — sufficient for pool connections and MOS telemetry.
- **Internal**: 10GbE backbone between halls. 1GbE to each rack switch.
- **Mining pool connection**: Braiins Pool (primary), F2Pool (failover). Stratum V2 protocol.
- **Latency to pool**: ~15ms (Stockholm PoP).
- **VPN**: WireGuard VPN for remote access. Managed by David Öberg.
- **Monitoring**: Grafana + Prometheus stack. Dashboards: fleet hashrate, temperatures, power, network.

## Physical Security

- **Access control**: RFID badge (HID iCLASS) on all exterior doors and hall entrances.
- **CCTV**: 24 cameras (Axis P3245-V), 30-day recording retention.
- **Perimeter**: Fenced compound, 2.5m chain-link with barbed wire top.
- **Visitor policy**: Escort required. Log entry in visitor register. No photography in server halls without CTO approval.

## Fire Safety

- **Detection**: VESDA (Very Early Smoke Detection Apparatus) in all halls.
- **Suppression**: Novec 1230 clean agent system (no water damage to electronics).
- **Extinguishers**: CO2 extinguishers at each hall entrance and every 10 racks.
- **Evacuation**: 4 emergency exits (1 per hall). Assembly point: parking lot north.
- **Fire drills**: Quarterly. Next drill: May 15, 2026.

## Environmental

- **Noise**: ~85 dBA inside halls (hearing protection mandatory). ~55 dBA at facility boundary (within municipal limits).
- **Waste heat**: Explored district heating partnership with Kiruna Energi (feasibility study completed Q4 2025, pending contract negotiation).
- **Carbon footprint**: ~95% renewable energy (Vattenfall hydroelectric mix). Annual carbon audit by SGS.
