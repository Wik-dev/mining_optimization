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
