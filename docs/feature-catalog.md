# Feature Catalog

Complete reference of all features used in the fleet intelligence pipeline. Referenced from the [Technical Report](technical-report.md) §4.2.

75 features are engineered per device-timestep by `tasks/features.py`. The classifier (`tasks/train_model.py`) uses a curated subset of 50; the quantile regressor adds 8 temporal features for a total of 58. Features marked with **[C]** are used by the classifier; **[R]** indicates regressor-only. Features without a marker are computed but not currently used by any model (available for future iterations).

---

## 1. Rolling Statistics (57 features)

Computed per device over sorted timestamps. Window sizes in samples at 5-min intervals.

| Feature | Window | Computation | Rationale |
|---------|--------|-------------|-----------|
| `{signal}_mean_1h` **[C]** | 12 samples | Rolling mean | Short-term trend; detects acute changes |
| `{signal}_std_1h` **[C]** | 12 samples | Rolling std | Short-term volatility; PSU instability shows as voltage std spike |
| `{signal}_mean_12h` | 144 samples | Rolling mean | Half-day baseline; smooths diurnal variation |
| `{signal}_mean_24h` **[C]** | 288 samples | Rolling mean | Daily baseline; reference for deviation features |
| `{signal}_std_24h` | 288 samples | Rolling std | Daily volatility; intermediate for dev_24h computation |
| `{signal}_dev_24h` **[C]** | 288 samples | `(x - mean_24h) / std_24h` | Z-score vs 24h baseline; flags sudden departure from recent norm |
| `{signal}_mean_7d` **[C]** | 2016 samples | Rolling mean | Weekly baseline; detects gradual degradation invisible in 24h |
| `{signal}_std_7d` | 2016 samples | Rolling std | Weekly volatility; intermediate for dev_7d computation |
| `{signal}_dev_7d` | 2016 samples | `(x - mean_7d) / std_7d` | Z-score vs 7d baseline; flags slow multi-day drift |

Applied to 6 telemetry signals: `temperature_c`, `power_w`, `hashrate_th`, `voltage_v`, `cooling_power_w`, `efficiency_jth` → 6 × 9 = **54 features**.

Plus 3 signal-specific rolling features:

| Feature | Signal | Window | Rationale |
|---------|--------|--------|-----------|
| `hashrate_th_mean_30m` | hashrate | 6 samples | Approximates MOS 30m smoothed hashrate resolution |
| `hashrate_th_std_30m` | hashrate | 6 samples | Short-term hash instability (firmware cliff detection) |
| `voltage_ripple_std_24h` **[C]** | voltage_ripple_mv | 288 samples | PSU capacitor aging: ripple variance increases before mean shifts |

**Subtotal: 57 rolling features.**

## 2. Rates of Change (8 features)

First-order differences per device, capturing speed of change.

| Feature | Computation | Used | Rationale |
|---------|-------------|------|-----------|
| `d_temperature_c` | `diff()` per 5-min step | — | Raw thermal rate |
| `d_power_w` | `diff()` per 5-min step | — | Raw power rate |
| `d_hashrate_th` | `diff()` per 5-min step | — | Raw hash rate change |
| `d_voltage_v` | `diff()` per 5-min step | — | Raw voltage rate |
| `d_temperature_c_smooth` | 1h rolling mean of diff | **[C]** | Smoothed thermal trend; filters 5-min noise |
| `d_power_w_smooth` | 1h rolling mean of diff | **[C]** | Smoothed power trend |
| `d_hashrate_th_smooth` | 1h rolling mean of diff | **[C]** | Smoothed hash trend; detects gradual decay |
| `d_voltage_v_smooth` | 1h rolling mean of diff | **[C]** | Smoothed voltage trend; PSU instability onset |

## 3. Fleet-Relative Z-Scores (4 features)

Per-timestamp, per-model-group normalization. A device's reading is compared to all devices of the same model at the same timestamp.

| Feature | Computation | Rationale |
|---------|-------------|-----------|
| `temperature_c_fleet_z` **[C]** | `(x - group_mean) / group_std` | Thermal outlier within fleet peers |
| `power_w_fleet_z` **[C]** | `(x - group_mean) / group_std` | Power consumption outlier |
| `hashrate_th_fleet_z` **[C]** | `(x - group_mean) / group_std` | Hashrate decay vs peers |
| `efficiency_jth_fleet_z` **[C]** | `(x - group_mean) / group_std` | Efficiency outlier |

These provide natural adversarial robustness: a single compromised device diverges from peers rather than evading detection.

## 4. Interaction Terms (6 features)

Physics-motivated combinations encoding known ASIC relationships.

| Feature | Computation | Rationale |
|---------|-------------|-----------|
| `power_per_ghz` **[C]** | `power_w / clock_ghz` | Should be constant if V stable; drift signals PSU issue |
| `thermal_headroom_c` **[C]** | `85°C - temperature_c` | Distance to thermal hard limit; informs overclock safety |
| `cooling_effectiveness` **[C]** | `(T_chip - T_amb) / P_cooling` | Thermal gradient per watt; fouling increases this ratio |
| `hashrate_ratio` **[C]** | `hashrate_th / nominal_hashrate` | Actual vs spec; <1.0 = chip degradation |
| `voltage_deviation` **[C]** | `voltage_v - stock_voltage` | Absolute voltage offset from stock operating point |
| `chip_dropout_ratio` **[C]** | `chip_count_active / nominal_chips` | Normalized across models; first signal of hashboard failure |

## 5. TE Decomposition (6 features, from `kpi.py`)

Not counted in the 75 engineered features (computed by a separate pipeline task), but used by the classifier. See [Technical Report](technical-report.md) §2 for the full TE formulation.

| Feature | Computation | Rationale |
|---------|-------------|-----------|
| `te_base` **[C]** | `P_asic / H` | Naive J/TH; isolates chip degradation |
| `voltage_penalty` **[C]** | `1 / η_v` | Voltage waste factor; isolates PSU instability |
| `cooling_ratio` **[C]** | `(P_asic + P_cool_norm) / P_asic` | Cooling overhead; isolates thermal fouling |
| `eta_v` **[C]** | `(V_optimal / V_actual)²` | Voltage efficiency; <1 = overvolting |
| `true_efficiency` **[C]** | `(P_asic + P_cool_norm) / (H × η_v)` | Composite KPI; strongest single predictor (31.5% importance) |
| `te_score` **[C]** | `TE_nominal / TE` | Health score; 1.0 = nominal, <0.9 = investigate |

## 6. Temporal / Autoregressive (8 features, regressor only)

Computed by `train_model.py` from TE score history. Per-device, no cross-device leakage.

| Feature | Computation | Rationale |
|---------|-------------|-----------|
| `te_score_lag_1h` **[R]** | `te_score` shifted 12 samples | Recent TE baseline for short-horizon prediction |
| `te_score_lag_6h` **[R]** | `te_score` shifted 72 samples | Medium-term TE baseline |
| `te_score_lag_24h` **[R]** | `te_score` shifted 288 samples | Daily TE baseline; captures diurnal pattern |
| `te_score_slope_1h` **[R]** | Linear regression slope, 12-sample window | Short-term degradation rate |
| `te_score_slope_6h` **[R]** | Linear regression slope, 72-sample window | Medium-term degradation rate |
| `te_score_slope_24h` **[R]** | Linear regression slope, 288-sample window | Daily-scale trend direction |
| `te_score_slope_7d` **[R]** | Linear regression slope, 2016-sample window | Weekly-scale trend; matches 7d prediction horizon |
| `te_score_volatility_24h` **[R]** | Rolling std over 288 samples | Unstable device behavior (PSU cycling, thermal oscillation) |

## 7. Raw Telemetry Passthroughs (8 features, not engineered)

Raw sensor values and site conditions used directly by the classifier alongside engineered features.

| Feature | Source | Rationale |
|---------|--------|-----------|
| `fan_rpm` **[C]** | Hardware sensor | Fan bearing wear: RPM drops or oscillates |
| `voltage_ripple_mv` **[C]** | Hardware sensor | PSU capacitor aging: ripple amplitude increases |
| `reboot_count` **[C]** | Hardware sensor | Firmware instability or power cycling |
| `chip_count_active` **[C]** | Hardware sensor | Hashboard failure: active chip count drops |
| `hashboard_count_active` **[C]** | Hardware sensor | Board-level failure detection |
| `dust_index` **[C]** | Hardware sensor | Dust accumulation metric |
| `ambient_temp_c` **[C]** | Site sensor | Ambient context; normalizes thermal features |
| `energy_price_kwh` **[C]** | Site data | Economic context; correlates with operating mode |

## Summary

`features.py` computes **75 features** per device-timestep (§1–§4). Not all are used by the model — 25 are intermediate computations (rolling stds that feed z-scores), redundant windows (12h when 1h+24h suffice), or reserved for future iterations (30m hashrate). The classifier's 50 input features are a curated selection from these 75, plus 6 TE decomposition features from `kpi.py` (§5) and 8 raw sensor passthroughs (§7).

| Source | Features | In classifier | In regressor |
|--------|----------|---------------|--------------|
| `features.py` (§1–§4) | 75 | 36 | 36 |
| `kpi.py` (§5) | 6 | 6 | 6 |
| Raw telemetry (§7) | 8 | 8 | 8 |
| `train_model.py` temporal (§6) | 8 | — | 8 |
| **Model input** | | **50** | **58** |
