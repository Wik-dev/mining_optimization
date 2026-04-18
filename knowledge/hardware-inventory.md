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
