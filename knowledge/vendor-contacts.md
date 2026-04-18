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
