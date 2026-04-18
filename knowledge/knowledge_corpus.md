# NordHash Mining — Company Profile

## Overview

NordHash Mining AB is a Bitcoin mining company headquartered in Kiruna, Sweden. Founded in 2021, the company operates a single large-scale mining facility powered primarily by renewable hydroelectric energy from Vattenfall's Porjus plant.

## Mission

Operate the most energy-efficient Bitcoin mining fleet in the Nordic region while maintaining hardware longevity and minimizing environmental impact.

## Facility

- **Location**: Kiruna Industrial Zone, Block 7, Norrbotten County, Sweden
- **Coordinates**: 67.86N, 20.22E
- **Building**: Former iron ore processing hall (retrofitted 2021-2022)
- **Total floor area**: 4,200 m2 (mining floor: 3,500 m2, offices/storage: 700 m2)
- **Power capacity**: 50 MW transformer (Vattenfall grid connection)
- **Current power draw**: ~38 MW average (76% utilization)
- **Cooling**: Evaporative cooling system with Arctic air intake (see facility-specs.md)

## Fleet

- **Total ASICs**: 5,000 units across 4 halls (A, B, C, D)
- **Primary models**: Bitmain Antminer S19j Pro (3,200 units), S19 XP (1,200 units), S21 (600 units)
- **Total nominal hashrate**: ~650 PH/s
- **Current effective hashrate**: ~590 PH/s (accounting for maintenance, underclocking, offline units)

## Organization

- **CEO**: Erik Lindqvist
- **CTO**: Anna Bergström
- **Head of Operations**: Magnus Johansson
- **Team size**: 28 full-time employees (see team-roster.md)
- **Shifts**: 3 shifts (day, evening, night) — 24/7 operations

## Financial Summary

- **Annual revenue** (2025): ~$42M (BTC-denominated, converted at year-avg price)
- **Annual operating cost**: ~$18M (electricity: $14.8M, staff: $2.1M, maintenance: $1.1M)
- **Electricity rate**: $0.045/kWh base contract (Vattenfall industrial tariff, 3-year lock ending Dec 2027)
- **BTC breakeven**: ~$28,000 per BTC (all-in cost including depreciation)
- **Equipment budget**: $50,000/month for replacements and upgrades

## Key Metrics (Current)

- **Power Usage Effectiveness (PUE)**: 1.08 (excellent — Arctic climate advantage)
- **Fleet uptime**: 96.2% (30-day rolling average)
- **Mean Time Between Failures (MTBF)**: 14,200 hours
- **Mean Time To Repair (MTTR)**: 4.2 hours
# NordHash Mining — Team Roster

## Management

| Name | Role | Contact | Notes |
|------|------|---------|-------|
| Erik Lindqvist | CEO | erik@nordhash.se | Based in Stockholm, visits Kiruna monthly |
| Anna Bergström | CTO | anna@nordhash.se | On-site Kiruna, leads R&D + fleet optimization |
| Magnus Johansson | Head of Operations | magnus@nordhash.se | On-site Kiruna, manages all shifts |
| Sofia Eriksson | Finance Manager | sofia@nordhash.se | Remote (Gothenburg), handles vendor payments + budget |

## Operations Team — Day Shift (06:00-14:00 CET)

| Name | Role | Specialization | Status |
|------|------|---------------|--------|
| Lars Andersson | Senior Technician | ASIC repair, PSU diagnostics | Active |
| Jean Dupont | Technician | Thermal paste, fan replacement | On leave April 15 - May 5 (paternity leave) |
| Katarina Nilsson | Technician | Firmware, networking | Active |
| Oscar Svensson | Junior Technician | General maintenance | Active, training until June 2026 |

## Operations Team — Evening Shift (14:00-22:00 CET)

| Name | Role | Specialization | Status |
|------|------|---------------|--------|
| Henrik Holm | Senior Technician | Cooling systems, electrical | Active |
| Maria Lindgren | Technician | ASIC diagnostics, rack management | Active |
| Björn Gustafsson | Junior Technician | Monitoring, basic troubleshooting | Active |

## Operations Team — Night Shift (22:00-06:00 CET)

| Name | Role | Specialization | Status |
|------|------|---------------|--------|
| Aleksei Volkov | Senior Technician (Night Lead) | Emergency response, all systems | Active |
| Ingrid Bergman | Technician | Monitoring, thermal management | Active |

**Night shift emergency contact**: Aleksei Volkov, +46-70-555-0142 (direct line)

## Infrastructure & IT

| Name | Role | Contact | Notes |
|------|------|---------|-------|
| David Öberg | Network Engineer | david@nordhash.se | Manages MOS connectivity, VPNs, monitoring |
| Elsa Johansson | Systems Administrator | elsa@nordhash.se | Server infrastructure, backups, security |
| Nils Pettersson | Electrician | nils@nordhash.se | Licensed high-voltage, transformer maintenance |

## Support & Administration

| Name | Role | Notes |
|------|------|-------|
| Frida Lund | Office Manager | Procurement, visitor coordination |
| Karl Wahlström | HSE Officer | Health, safety, environment compliance |
| Lisa Nyström | Logistics Coordinator | Spare parts inventory, vendor shipments |

## Shift Coverage Rules

- **Minimum staffing**: 2 technicians per shift at all times
- **Night shift**: 2 staff (Aleksei + Ingrid). If either is absent, day shift Senior Tech (Lars) is on-call.
- **Weekend coverage**: Rotating, 2 technicians. See monthly schedule posted in breakroom and Slack #shift-schedule.
- **Holiday coverage**: Arranged 4 weeks in advance. Magnus approves all leave requests.
- **Escalation**: Night shift issues beyond Aleksei's scope escalate to Magnus (phone, any hour).

## Certifications

- All senior technicians: Bitmain Certified Service Provider (BCSP) Level 2
- Nils Pettersson: Licensed Electrician (Swedish Elsäkerhetsverket Class A)
- Karl Wahlström: ISO 45001 Internal Auditor
- David Öberg: CCNA, CompTIA Security+

## Upcoming Availability Changes

- **Jean Dupont**: Paternity leave April 15 - May 5, 2026. Day shift drops to 3 technicians (Lars, Katarina, Oscar). Oscar is still in training — complex repairs should be handled by Lars or escalated to evening shift (Henrik).
- **Katarina Nilsson**: Planned 1-week vacation June 16-22, 2026.
- **Annual shutdown**: No planned facility shutdown in 2026. Last shutdown: December 2025 (transformer maintenance, 48h).
# NordHash Mining — Hardware Inventory

## ASIC Fleet Summary

| Model | Count | Halls | Nominal Hashrate (per unit) | Power (per unit) | Efficiency (J/TH) | Deployment Date |
|-------|-------|-------|----------------------------|------------------|--------------------|-----------------|
| Antminer S19j Pro | 3,200 | A, B | 104 TH/s | 3,068 W | 29.5 J/TH | 2022-Q1 to 2023-Q2 |
| Antminer S19 XP | 1,200 | C | 140 TH/s | 3,010 W | 21.5 J/TH | 2023-Q3 to 2024-Q1 |
| Antminer S21 | 600 | D | 200 TH/s | 3,500 W | 17.5 J/TH | 2025-Q1 |

## Batch Details

### S19j Pro Batches

| Batch | Count | Hall | Racks | Purchase Date | Warranty Expiry | Notes |
|-------|-------|------|-------|--------------|-----------------|-------|
| B1 | 1,600 | A | A01-A40 (40 per rack) | 2022-01 | 2025-01 (expired) | Oldest units, higher failure rate. 23 units replaced since warranty expiry. |
| B2 | 1,600 | B | B01-B40 | 2023-02 | 2026-06 | Warranty expires June 2026. File claims before expiry for any units showing degradation. |

### S19 XP Batches

| Batch | Count | Hall | Racks | Purchase Date | Warranty Expiry | Notes |
|-------|-------|------|-------|--------------|-----------------|-------|
| C1 | 1,200 | C | C01-C30 | 2023-09 | 2026-09 | Strong performers. 4 units RMA'd to date. |

### S21 Batches

| Batch | Count | Hall | Racks | Purchase Date | Warranty Expiry | Notes |
|-------|-------|------|-------|--------------|-----------------|-------|
| D1 | 600 | D | D01-D15 | 2025-01 | 2028-01 | Newest units. Under full warranty. |

## Rack Layout

Each hall has 30-40 racks. Each rack holds 40 ASICs.

- **Hall A** (S19j Pro B1): Racks A01-A40, 1,600 units. Oldest hardware, priority monitoring.
- **Hall B** (S19j Pro B2): Racks B01-B40, 1,600 units. Warranty active until June 2026.
- **Hall C** (S19 XP C1): Racks C01-C30, 1,200 units. Best efficiency tier.
- **Hall D** (S21 D1): Racks D01-D15, 600 units. Newest, lowest J/TH.

## Power Supply Units (PSU)

- **S19j Pro**: APW12 (3,300W rated). Known weak point: fan bearing failure after 18+ months continuous use.
- **S19 XP**: APW12 (3,300W rated). Same PSU, similar failure modes.
- **S21**: APW15 (3,800W rated). Improved design, fewer reported issues.

**PSU replacement inventory**: 120 APW12 units, 30 APW15 units in storage room SR-1.

## Fan Inventory

- **Stock**: 50 replacement fans (12cm, 6000 RPM, 4-pin PWM). Compatible with S19j Pro and S19 XP.
- **S21 fans**: 20 replacement fans (14cm, 7000 RPM). Different form factor.
- **Reorder point**: When stock drops below 20 units for any type, Lisa (Logistics) places order with Bitmain.
- **Lead time**: 2-3 weeks from Bitmain Shenzhen warehouse, 1 week from European distributor (Innosilicon EU, higher price).

## Control Boards

- **Spare control boards**: 15 (S19j Pro compatible), 8 (S19 XP compatible), 5 (S21 compatible).
- **Control board failure rate**: ~0.3% per year. Most failures in first 90 days (DOA) or after 24+ months.

## Network Infrastructure

- Each rack has a 48-port Gigabit switch (TP-Link TL-SG1048).
- Uplinks: 10GbE fiber to core switch (Juniper EX4300) per hall.
- MOS agent installed on each ASIC via firmware (standard Bitmain cgminer + MOS overlay).
- DHCP + DNS: David Öberg manages. IP scheme: 10.10.{hall}.{rack*40+unit}.

## Environmental Sensors

- 4 temperature/humidity sensors per hall (Sensirion SHT45, PoE).
- 1 ambient outdoor sensor (rooftop).
- Data logged to Grafana via MQTT bridge (5-second intervals).
- Alerts: Slack #alerts channel if any sensor reads >35°C or humidity >60%.
# NordHash Mining — Maintenance Standard Operating Procedures

## SOP-001: Scheduled Maintenance Window

**Frequency**: Weekly (Tuesdays 06:00-10:00 CET)
**Scope**: Non-critical maintenance accumulated during the week.

1. Magnus reviews maintenance queue (Monday EOD).
2. Prioritize by: safety issues > warranty items > performance items > cosmetic.
3. Day shift executes during Tuesday window.
4. Affected racks powered down sequentially (max 2 racks offline simultaneously).
5. Update maintenance log in MOS after each unit serviced.
6. If maintenance overruns window, resume next Tuesday unless safety-critical.

## SOP-002: Fan Replacement

**Trigger**: Fan RPM < 4,000 or audible bearing noise.
**Time**: ~15 minutes per unit.
**Personnel**: Any technician.

1. Power down ASIC via MOS dashboard (graceful shutdown).
2. Wait 60 seconds for capacitors to discharge.
3. Disconnect power cables.
4. Remove faulty fan (4 Phillips screws, 1 PWM connector).
5. Install replacement fan from inventory SR-1.
6. Reconnect power, boot via MOS.
7. Verify fan RPM in MOS telemetry (expect 5,500-6,000 RPM at idle).
8. Log replacement in maintenance tracker (device_id, old fan serial, new fan serial, date).

## SOP-003: PSU Replacement

**Trigger**: PSU voltage outside 11.8-12.6V range, or intermittent power cycling.
**Time**: ~30 minutes per unit.
**Personnel**: Senior technician or Nils (electrician) for high-voltage rail issues.

1. Power down ASIC via MOS.
2. Disconnect all cables from PSU.
3. Remove PSU from chassis (4 screws + sliding rail).
4. Install replacement APW12/APW15 from inventory SR-1.
5. Reconnect cables. Verify polarity markings.
6. Boot and verify stable voltage in MOS telemetry for 10 minutes.
7. Monitor for 24 hours — if voltage drifts, escalate to Nils.

## SOP-004: Thermal Paste Reapplication

**Trigger**: Chip temperature > 75°C sustained with normal fan operation, or efficiency > 15% worse than nominal.
**Time**: ~45 minutes per unit.
**Personnel**: Senior technician only (Lars or Henrik).

1. Power down and discharge (SOP-002 steps 1-3).
2. Remove heatsink assembly (6 screws, careful with thermal interface).
3. Clean old paste with isopropyl alcohol (99%) and lint-free cloth.
4. Apply new paste (Arctic MX-6) — thin X pattern on each chip.
5. Reinstall heatsink. Torque screws to 0.5 Nm in star pattern.
6. Boot and monitor chip temps for 30 minutes.
7. Expected result: 5-10°C temperature drop.

## SOP-005: Firmware Update

**Trigger**: Bitmain security advisory, or MOS platform update.
**Time**: ~5 minutes per unit (batch-updatable via MOS).
**Personnel**: Katarina (firmware specialist) or David (network).

1. Download firmware from Bitmain support portal (verify SHA256 hash).
2. Test on 3 units in Hall D (newest hardware, fastest recovery if issues).
3. Monitor for 24 hours.
4. If stable, deploy to remaining fleet via MOS batch update (max 100 units per batch).
5. Stagger batches 30 minutes apart to avoid simultaneous reboot impact on hashrate.

## SOP-010: Rack Inspection

**Trigger**: Monthly, or after any environmental alert (temperature, humidity, power).
**Time**: ~20 minutes per rack.
**Personnel**: Any technician.

1. Visual inspection: cable management, dust accumulation, physical damage.
2. Thermal camera scan of all units (look for hot spots > 80°C).
3. Check airflow: no obstructions, all fans spinning.
4. Verify network connectivity (MOS agent responding).
5. Note any anomalies in inspection log.
6. If dust accumulation is significant, schedule compressed air cleaning.

## SOP-012: Thermal Response Protocol

**Trigger**: Any device reporting chip temperature > 70°C sustained for > 10 minutes.
**Severity levels and response**:

### Level 1: Elevated (70-75°C)
1. **First action**: Underclock to 90% via MOS.
2. Monitor for 30 minutes.
3. If temperature stabilizes below 70°C, maintain underclock until next maintenance window.
4. Schedule thermal paste check (SOP-004) for next Tuesday.

### Level 2: High (75-80°C)
1. **First action**: Underclock to 80% via MOS.
2. Check cooling system airflow in the rack (SOP-010 steps 3-4).
3. If temperature doesn't drop below 75°C within 15 minutes, underclock to 70%.
4. Schedule immediate thermal paste reapplication (SOP-004).
5. If no on-site technician qualified for thermal paste (e.g., Jean on leave, only Oscar available), maintain underclock and escalate to next shift with a senior tech.

### Level 3: Critical (>80°C)
1. **Immediate shutdown** via MOS emergency stop.
2. Notify Magnus (any hour) and Karl (HSE).
3. Do not restart until inspected by senior technician.
4. Inspect for: failed fans, blocked airflow, PSU overvoltage, ambient temperature anomaly.
5. After repair, boot at 70% clock speed, ramp up 10% per hour while monitoring.

**Key rule**: When no qualified technician is on-site for thermal paste work, **always underclock first** rather than attempting repairs beyond your certification level. Underclocking is safe and reversible; improper thermal paste application can permanently damage chips.

## SOP-015: Hash Board Replacement

**Trigger**: Hash board producing < 50% of rated hashrate, or control board reports board offline.
**Time**: ~60 minutes per unit.
**Personnel**: Senior technician only.

1. If under warranty, file RMA with Bitmain before opening unit.
2. Power down and discharge.
3. Photograph board layout before removal (for reassembly reference).
4. Remove faulty hash board (ribbon cables + power connectors).
5. Install replacement board.
6. Boot and run calibration (MOS auto-calibrate, ~20 minutes).
7. Verify hashrate within 10% of rated for 1 hour.

## SOP-020: Emergency Power Loss

**Trigger**: Grid power failure or transformer trip.
**Personnel**: Nils (electrician, primary), Aleksei (night shift lead, if after hours).

1. UPS provides 5 minutes of power to networking and control systems only (not ASICs).
2. ASICs will hard-power-off. This is expected — no graceful shutdown possible.
3. When power returns, wait 5 minutes for voltage stabilization.
4. Boot ASICs in staggered batches (200 units per batch, 2-minute intervals) to avoid inrush current spike.
5. Monitor MOS for units that fail to boot — likely PSU or control board damage from power surge.
6. Log incident in safety register. If >10 units damaged, file insurance claim.
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
# NordHash Mining — Financial Overview

## Revenue Model

NordHash earns Bitcoin by contributing hashrate to mining pools. Revenue depends on:
1. **Hashrate**: Fleet effective TH/s contributed to pool
2. **BTC price**: Determines USD value of mined BTC
3. **Network difficulty**: Determines BTC yield per TH/s
4. **Pool fee**: Braiins Pool charges 2% (FPPS payout scheme)

### Current Mining Economics (April 2026)

| Metric | Value | Notes |
|--------|-------|-------|
| Fleet effective hashrate | ~590 PH/s | After underclocking, maintenance, offline units |
| Daily BTC yield | ~0.21 BTC/PH/day | At current difficulty (~92T) |
| Daily fleet yield | ~124 BTC/day | 590 × 0.21 |
| BTC breakeven price | ~$28,000 | All-in cost per BTC mined (electricity + staff + maintenance + depreciation) |
| Electricity-only breakeven | ~$18,500 | Just power cost per BTC mined |

### Revenue Sensitivity

| BTC Price | Daily Revenue (USD) | Monthly Revenue | Annual Revenue | Margin |
|-----------|-------------------|-----------------|----------------|--------|
| $50,000 | $6,200 | $186,000 | $2.26M | 44% |
| $65,000 | $8,060 | $241,800 | $2.94M | 57% |
| $80,000 | $9,920 | $297,600 | $3.62M | 65% |
| $100,000 | $12,400 | $372,000 | $4.52M | 72% |

## Cost Structure

### Electricity (78% of operating cost)

- **Rate**: $0.045/kWh (Vattenfall industrial tariff, 3-year contract ending December 2027)
- **Monthly consumption**: ~27,360 MWh (38 MW × 24h × 30d)
- **Monthly electricity cost**: ~$1,231,200
- **Annual electricity cost**: ~$14.8M

**Rate risk**: Post-2027 contract renewal may be higher. Nordic spot rates have been $0.03-0.08/kWh in 2025-2026. Budget assumes $0.055/kWh for 2028 planning.

### Staffing (11% of operating cost)

- **Annual payroll**: ~$2.1M (28 employees, Swedish labor costs including social charges ~31.42%)
- **Key roles cost**: Senior technicians ~$55k/year, junior ~$42k/year, management ~$85k/year (all before social charges)

### Maintenance & Repairs (6% of operating cost)

- **Monthly equipment budget**: $50,000 (covers replacement fans, PSUs, control boards, thermal paste, tools)
- **Annual ASIC replacement budget**: $500,000 (for units beyond economical repair)
- **Warranty recovery**: ~$80,000/year from Bitmain RMAs (Batch B2 warranty active until June 2026)

### Other Costs (5% of operating cost)

- **Insurance**: $180,000/year (property + equipment, Trygg-Hansa)
- **Internet**: $24,000/year (2x Telia fiber)
- **Facility lease**: $120,000/year (long-term lease from LKAB, indexed to CPI)
- **Software/monitoring**: $36,000/year (Grafana Cloud, MOS license, Braiins pool premium features)
- **Misc**: $40,000/year (travel, training, office supplies)

## Capital Expenditure

### Recent Investments

| Year | Investment | Amount | Notes |
|------|-----------|--------|-------|
| 2025-Q1 | S21 fleet (600 units) | $3.6M | Hall D buildout, highest efficiency tier |
| 2024-Q3 | Cooling system upgrade | $280,000 | Added 4 supplemental evaporative coolers |
| 2024-Q1 | Network upgrade | $95,000 | 10GbE backbone, Juniper core switches |

### Planned Investments

| Timeline | Investment | Est. Cost | Status |
|----------|-----------|-----------|--------|
| 2026-H2 | S21 expansion (400 units) | $2.4M | Approved, pending BTC price > $70k trigger |
| 2026-Q4 | Immersion cooling pilot (1 rack) | $150,000 | Feasibility study in progress |
| 2027-Q1 | Hall A hardware refresh (replace oldest B1 S19j Pro) | $4.8M | Budget allocated, timing depends on S21 pricing |

## BTC Treasury Policy

- **Immediate sell**: 60% of mined BTC sold daily via Bitstamp (auto-sell, covers operating costs)
- **Treasury hold**: 40% of mined BTC held in cold storage (Ledger Enterprise)
- **Current treasury**: ~185 BTC (as of March 2026)
- **Treasury ceiling**: 500 BTC. Above this, excess sold quarterly.
- **Emergency liquidation**: Treasury can cover 3 months of operating costs at $50k BTC price.

## Key Financial Rules

1. **Equipment purchases > $10,000**: Require Sofia (Finance Manager) approval + Erik (CEO) sign-off.
2. **Emergency repairs < $5,000**: Magnus can approve immediately. Log in expense tracker.
3. **Warranty claims**: File within 48 hours of identifying defect. Lisa handles logistics.
4. **Vendor payments**: Net-30 for established vendors. Prepayment required for new vendors.
5. **BTC-denominated contracts**: Avoided. All vendor contracts in SEK or USD.
# NordHash Mining — Vendor Contacts & Supply Chain

## Primary Vendors

### Bitmain (ASIC manufacturer)

- **Account manager**: Wei Zhang, wei.zhang@bitmain.com
- **Support portal**: support.bitmain.com (ticket system)
- **RMA SLA**: 48-hour response to warranty claims, 10 business days for replacement shipment from Shenzhen warehouse
- **European distributor**: Innosilicon EU (Amsterdam) — faster delivery (3-5 business days) but 15-20% markup over direct Bitmain pricing
- **Bulk pricing**: Negotiated 8% discount for orders > 500 units (secured for S21 expansion)
- **Payment terms**: 50% advance, 50% on shipment confirmation

### Vattenfall (electricity)

- **Account manager**: Per Lindström, per.lindstrom@vattenfall.se
- **Contract ID**: VF-IND-2024-0847
- **Contract term**: January 2025 — December 2027 ($0.045/kWh fixed)
- **Billing**: Monthly, due Net-30
- **Emergency contact**: Vattenfall Grid Control, +46-20-820-820 (24/7, for outages)
- **Planned outage notification**: 14 days advance notice (contractual)

### Telia (internet)

- **Account manager**: Karin Ström, karin.strom@telia.se
- **Service**: 2x 10 Gbps fiber, diverse path routing
- **SLA**: 99.95% uptime, 4-hour response for critical issues
- **NOC**: +46-20-755-755 (24/7)

## Spare Parts Suppliers

### Fans & Cooling Components

| Supplier | Product | Lead Time | Notes |
|----------|---------|-----------|-------|
| Bitmain (direct) | OEM replacement fans | 2-3 weeks | Cheapest, longest lead time |
| Innosilicon EU | OEM-compatible fans | 1 week | 25% premium, faster |
| Sunon (direct) | Generic 12cm/14cm PWM fans | 3-4 weeks | Bulk orders only (min 100 units), lowest per-unit cost |
| Arctic MX-6 | Thermal paste (4g tubes) | Amazon Prime 2-day | Keep 20 tubes in stock |

### Electrical Components

| Supplier | Product | Lead Time | Notes |
|----------|---------|-----------|-------|
| ABB (transformer) | Transformer parts, breakers | 4-8 weeks | Via ABB service contract |
| Schneider Electric | PDU, circuit breakers | 1-2 weeks | Stockholm distributor (Ahlsell) |
| Eaton | UPS batteries, modules | 2-3 weeks | Annual battery replacement contract |

### Network Equipment

| Supplier | Product | Lead Time | Notes |
|----------|---------|-----------|-------|
| TP-Link | Rack switches (TL-SG1048) | 3-5 days | Amazon Business, keep 5 spares |
| Juniper | Core switches, SFP+ modules | 1-2 weeks | Via Dustin (Nordic IT distributor) |
| Ubiquiti | Access points, cameras | 1 week | Dustin |

## Current Spare Parts Inventory

| Item | Quantity | Location | Reorder Point |
|------|----------|----------|---------------|
| Replacement fans (12cm, S19j Pro/XP) | 50 | SR-1, Shelf A | 20 |
| Replacement fans (14cm, S21) | 20 | SR-1, Shelf A | 10 |
| APW12 PSU (S19j Pro/XP) | 120 | SR-1, Shelf B | 30 |
| APW15 PSU (S21) | 30 | SR-1, Shelf B | 10 |
| Control boards (S19j Pro) | 15 | SR-1, Shelf C | 5 |
| Control boards (S19 XP) | 8 | SR-1, Shelf C | 3 |
| Control boards (S21) | 5 | SR-1, Shelf C | 2 |
| Thermal paste (Arctic MX-6, 4g) | 35 tubes | SR-1, Shelf D | 10 |
| Network cables (Cat6, 2m) | 200 | SR-1, Shelf E | 50 |
| SFP+ modules (10GbE) | 8 | SR-1, Shelf E | 4 |

**Inventory management**: Lisa Nyström tracks via spreadsheet (SharePoint). Monthly audit by shift leads.

## Service Contracts

| Vendor | Service | Annual Cost | Coverage |
|--------|---------|-------------|----------|
| ABB | Transformer maintenance | $45,000 | Annual inspection + emergency callout (8h response) |
| Eaton | UPS maintenance | $12,000 | Battery replacement + quarterly check |
| Trygg-Hansa | Property + equipment insurance | $180,000 | Fire, flood, theft, equipment breakdown (excess: $50,000) |
| SGS | Carbon audit | $8,000 | Annual sustainability report + certification |

## Vendor Evaluation Criteria

For new vendors (evaluated by Sofia + Magnus):
1. **Reliability**: On-time delivery rate > 95%
2. **Price**: Within 15% of cheapest alternative
3. **Support**: Must offer English-language technical support
4. **Payment terms**: Net-30 minimum (no prepayment for established relationships)
5. **Nordic presence**: Preferred (reduces shipping time and import complexity)
# NordHash Mining — Safety Procedures & Emergency Protocols

## General Safety Rules

1. **Hearing protection** mandatory in all mining halls (>85 dBA). Approved models: 3M Peltor X5A or equivalent NRR 31+.
2. **ESD wristband** mandatory when handling any electronic components.
3. **No food or drink** in mining halls (spill risk to equipment).
4. **Two-person rule** for any work involving high-voltage (>400V) or heavy lifting (>25 kg).
5. **Lock-Out/Tag-Out (LOTO)** required before any electrical maintenance. Nils (electrician) manages LOTO keys.

## Emergency Contact Chain

### Escalation Matrix

| Severity | Who to Contact | Response Time | Examples |
|----------|---------------|---------------|----------|
| **P1 — Critical** | Magnus (any hour) + Karl (HSE) | Immediate | Fire, electrical shock, chip temp >80°C, transformer trip |
| **P2 — High** | Shift lead + Magnus (business hours) | 30 minutes | PSU fire smell (no flame), cooling system failure, multiple units offline |
| **P3 — Medium** | Shift lead | Next shift start | Single unit failure, fan noise, network issues |
| **P4 — Low** | Maintenance queue | Next Tuesday window | Cosmetic damage, cable management, labeling |

### Emergency Phone Numbers

| Contact | Phone | Role |
|---------|-------|------|
| Magnus Johansson | +46-70-555-0101 | Head of Operations (24/7 for P1) |
| Karl Wahlström | +46-70-555-0102 | HSE Officer (P1 + workplace injuries) |
| Aleksei Volkov | +46-70-555-0142 | Night Shift Lead |
| Nils Pettersson | +46-70-555-0103 | Electrician (electrical emergencies) |
| Vattenfall Grid Control | +46-20-820-820 | Power outages |
| Kiruna Fire Department | 112 | Fire emergency |
| Kiruna Hospital | +46-980-731-00 | Medical emergency (25 min drive) |

## Thermal Emergency Protocol

### Chip Temperature > 80°C — Immediate Shutdown Required

**This is a P1 emergency.** Sustained operation above 80°C risks:
- Solder joint degradation (BGA connections)
- Permanent chip damage
- Potential PCB delamination
- Fire risk (rare but non-zero)

**Procedure:**

1. **Immediately** issue emergency shutdown via MOS for the affected device(s).
2. **Do NOT** attempt to underclock first — at >80°C, shutdown is the only safe action.
3. Notify shift lead and Magnus.
4. Physically inspect the device after 15-minute cooldown:
   - Check fans (spinning? correct RPM?)
   - Check airflow path (obstructions? neighboring units?)
   - Check ambient temperature sensor for the hall
5. If ambient temperature is normal and issue is device-specific, likely causes:
   - Failed fan(s) → replace per SOP-002
   - Thermal paste degradation → reapply per SOP-004
   - Heatsink contact issue → reseat heatsink
6. If ambient temperature is elevated (>35°C), trigger fleet-wide underclock per facility-specs.md.
7. **Do not restart** the device until root cause is identified and fixed.
8. After repair, restart at 70% clock speed. Ramp up 10% per hour while monitoring.
9. Log incident in safety register with: device_id, peak temperature, root cause, corrective action.

### Chip Temperature 75-80°C — Underclock Required

1. Underclock to 80% via MOS immediately.
2. Monitor for 15 minutes.
3. If temperature drops below 70°C, schedule maintenance per SOP-012 Level 2.
4. If temperature remains >75°C at 80% clock, underclock to 70%.
5. If still >75°C at 70% clock, proceed with shutdown protocol above.

## Electrical Safety

### High-Voltage Work

- **Only Nils Pettersson** is authorized for work on the 20kV incoming supply or transformer.
- **Senior technicians** may work on 400V distribution with LOTO and buddy system.
- **All technicians** may work on 12V DC (ASIC level) without LOTO.

### Electrical Fire

1. **Do NOT use water.** Use CO2 extinguisher (located at each hall entrance and every 10 racks).
2. If fire is beyond a single extinguisher: evacuate, call 112, activate Novec suppression (red pull station at each hall entrance).
3. Cut power to affected hall via emergency stop button (red mushroom button, one at each hall entrance).
4. Do not re-enter until fire department clears the area.

### Arc Flash

- All distribution panels have arc flash labels with incident energy (cal/cm2) and required PPE level.
- **PPE requirement**: Category 2 arc flash suit for work inside any panel >100A.
- Arc flash suits stored in electrical room ER-1 (2 sets, sizes M and XL).

## Environmental Incidents

### Water Leak

1. Identify source (cooling system, roof, plumbing).
2. If water is near electrical equipment: cut power to affected area immediately.
3. Deploy portable water barriers (stored in each hall, behind east doors).
4. Call Magnus + Karl.
5. If cooling system leak: isolate the section, switch to passive airflow.

### Gas Detection

- **CO detectors** in all halls (carbon monoxide from potential PSU fires).
- **Alarm threshold**: 35 ppm (STEL) or 25 ppm (TWA-8h).
- If CO alarm triggers: evacuate hall, ventilate, identify source (likely smoldering PSU).

## Incident Reporting

All incidents (P1-P4) must be logged in the safety register within 24 hours.

**Required fields:**
- Date, time, location (hall/rack)
- Incident type (thermal, electrical, mechanical, environmental, personnel)
- Severity (P1-P4)
- Personnel involved
- Root cause (if known)
- Corrective action taken
- Follow-up required (yes/no, assigned to whom)

**P1 and P2 incidents** additionally require:
- Written incident report (Karl's template) within 72 hours
- Review meeting with Magnus + Karl within 1 week
- Corrective action verification within 30 days

## Safety Training

- **New employee orientation**: 2-day safety induction by Karl (mandatory before hall access).
- **Annual refresher**: 1-day training for all operations staff (next session: September 2026).
- **First aid**: Lars and Aleksei are certified first responders (Swedish Red Cross).
- **First aid kits**: Located at each hall entrance and in the breakroom.
- **AED (defibrillator)**: Located in main corridor between Halls B and C.
