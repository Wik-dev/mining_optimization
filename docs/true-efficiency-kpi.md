# True Efficiency (TE) — KPI Specification

## Why Naive J/TH Is Insufficient

The standard mining efficiency metric is **J/TH** (Joules per Terahash):

```
J/TH_naive = P_asic / H
```

This ignores three things that dominate real-world operating costs:

1. **Cooling overhead** — cooling systems consume 10-20% of total site power. A miner running at 15 J/TH but requiring 1kW of cooling is not equivalent to one achieving 15 J/TH with 400W of cooling.
2. **Voltage efficiency** — CMOS power scales as V². Overclocking with +30mV costs disproportionately more power per hash. The naive metric hides whether you're operating on the efficient part of the V/f curve.
3. **Ambient conditions** — a miner at -5°C ambient naturally needs less cooling than one at +20°C. Raw J/TH conflates hardware quality with geography.

True Efficiency (TE) separates what the operator controls from what the environment gives.

## Formulation

### True Efficiency (TE)

```
TE = (P_asic + P_cooling_norm) / (H × η_v)     [J/TH]
```

Where:
- `P_asic` — ASIC power consumption (W), from telemetry `power_w`
- `P_cooling_norm` — cooling power normalized to reference ambient (see below)
- `H` — observed hashrate (TH/s), from telemetry `hashrate_th`
- `η_v` — voltage efficiency factor (dimensionless, see below)

**Lower TE = better.** The metric is in J/TH like the naive version, but accounts for the full system cost and penalizes inefficient voltage operation.

### Component 1: Voltage Efficiency Factor (η_v)

For CMOS circuits, minimum stable voltage scales with frequency:

```
V_optimal(f) = V_stock × (f / f_stock)^α       α = 0.6
```

The exponent α = 0.6 reflects that voltage scales sub-linearly with frequency in modern process nodes. At stock frequency, V_optimal = V_stock by definition.

The efficiency factor is the ratio of optimal-to-actual power at the current frequency:

```
η_v = (V_optimal(f_actual) / V_actual)²
```

| Condition | V_actual vs V_optimal | η_v | Interpretation |
|---|---|---|---|
| Stock settings | V_actual = V_stock | 1.0 | Baseline — no penalty |
| Clean overclock | V_actual ≈ V_optimal(f_high) | ~1.0 | Higher power, but proportional to frequency — efficient |
| Over-volted | V_actual > V_optimal | < 1.0 | Wasting power — penalty applied |
| PSU instability | V_actual fluctuates | < 1.0 on average | Ripple wastes power on peaks |
| Undervolted | V_actual < V_optimal | > 1.0 | Operating below stability margin — risky but efficient |

**Why this matters for Gio:** The PSU instability anomaly (A2) will show up as η_v degradation before any other metric flags it. Thermal degradation (A1) won't affect η_v directly — it shows up in cooling normalization instead. This separation is diagnostic.

### Component 2: Cooling Power Normalization

Cooling power depends on the thermal gradient between chip and ambient. To compare across different weather conditions, normalize to a reference ambient T_ref = 25°C:

```
P_cooling_norm = P_cooling × (T_chip - T_ref) / max(T_chip - T_ambient, 1.0)
```

The `max(..., 1.0)` floor prevents division by zero when chip temperature is near ambient (idle/cold startup).

| Condition | Effect on P_cooling_norm |
|---|---|
| Cold ambient (T_amb < T_ref) | P_cooling_norm > P_cooling — "what cooling *would* cost at 25°C" |
| Hot ambient (T_amb > T_ref) | P_cooling_norm < P_cooling — discounts the environmental penalty |
| Thermal fouling (A1) | P_cooling rises AND T_chip rises → P_cooling_norm rises on both axes |

**Why this matters:** A site in Iceland at -5°C ambient looks artificially efficient in raw J/TH because cooling is nearly free. TE normalization levels the playing field. Conversely, thermal degradation (A1) will cause P_cooling_norm to rise *faster* than raw P_cooling because the fouling increases thermal resistance independent of ambient.

### Edge Cases

| Condition | Handling |
|---|---|
| Idle mode (H = 0) | TE = NaN — excluded from aggregations |
| T_chip ≈ T_ambient | Floor at max(T_chip - T_ambient, 1.0) in cooling normalization |
| Negative P_cooling_norm | Clamp to 0 — shouldn't occur with floor, but defensive |

## Decomposition — Why Is TE Bad?

TE decomposes into three independent factors, each pointing to a different root cause:

```
TE = TE_base × (1/η_v) × R_cool
```

Where:
- `TE_base = P_asic / H` — naive J/TH (hardware intrinsic)
- `1/η_v` — voltage penalty (>1 when over-volted or PSU unstable)
- `R_cool = (P_asic + P_cooling_norm) / P_asic` — cooling overhead ratio (always ≥1)

| Factor rising | Root cause | Anomaly detected |
|---|---|---|
| TE_base ↑ | Chip degradation — same power, less hashrate | A3 (hashrate decay) |
| 1/η_v ↑ | Voltage inefficiency or instability | A2 (PSU instability) |
| R_cool ↑ | Cooling system overloaded or fouled | A1 (thermal degradation) |

This decomposition is the diagnostic backbone of the predictive maintenance model — rather than training on raw telemetry, we train on *which TE component is drifting*.

## Device Health Score (TE_score)

For cross-device comparison and dashboard display, normalize TE against each device's nominal baseline:

```
TE_score = TE_nominal / TE
```

Where `TE_nominal` is the device's TE at stock settings with T_ambient = T_ref (computed once from fleet metadata).

- `TE_score = 1.0` — performing at nominal
- `TE_score > 1.0` — better than nominal (e.g., undervolted, cold ambient advantage after normalization)
- `TE_score < 1.0` — degraded — the decomposition tells you why

## Economic Extension (EE)

For cost-based decisions (overclock vs underclock), extend TE with energy pricing:

```
EE = TE × (energy_price / 1000)     [$/TH·s per hour]
```

Or as a profitability signal:

```
margin_rate = revenue_per_th - EE
```

Where `revenue_per_th` comes from pool payout rate and BTC price. This connects the physics-level KPI to Gio's economics slides — the operator should overclock when `margin_rate` increases (cheap energy + cold ambient), and underclock when it decreases.

## Mapping to Telemetry Fields

All inputs come directly from `fleet_telemetry.csv`:

| TE component | Telemetry fields used |
|---|---|
| P_asic | `power_w` |
| P_cooling | `cooling_power_w` |
| H | `hashrate_th` |
| V_actual | `voltage_v` |
| f_actual | `clock_ghz` |
| T_chip | `temperature_c` |
| T_ambient | `ambient_temp_c` |
| energy_price | `energy_price_kwh` |

Device-level constants (V_stock, f_stock) come from `fleet_metadata.json`.

## Constants

| Symbol | Value | Source |
|---|---|---|
| T_ref | 25°C | Industry standard reference ambient |
| α (V/f exponent) | 0.6 | CMOS process node scaling (sub-linear V/f) |
| Floor(T_chip - T_amb) | 1.0°C | Numerical stability guard |

## Implementation Notes

The `kpi.py` task script will:
1. Load `features.parquet` and `fleet_metadata.json`
2. Join device constants (V_stock, f_stock) to telemetry
3. Compute η_v, P_cooling_norm, TE, decomposition factors, TE_score per row
4. Output `kpi_timeseries.parquet` with original fields + all TE components
5. Output summary vars: `mean_te` (fleet average), `worst_device` (lowest mean TE_score)
