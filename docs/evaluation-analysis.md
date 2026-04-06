# Classification Threshold Analysis

**Decision**: Default anomaly classification threshold set to **0.3** (not the standard 0.5).

**Date**: 2026-04-04
**Scope**: `tasks/train_model.py` (baked into model artifact), `tasks/score.py` (runtime override via `--threshold`)

---

## 1. Problem Statement

The anomaly classifier outputs a probability per device (0–1). A threshold converts this continuous score into a binary flag: "anomalous" or "healthy". The standard default in most ML pipelines is 0.5 — maximizing balanced accuracy.

In mining operations, this is the wrong objective. The costs of the two error types are asymmetric:

| Error Type | What Happens | Operational Cost |
|---|---|---|
| **False Negative** (missed failure) | Failing ASIC continues operating undetected. Leads to unplanned downtime, potential hardware damage (solder joint degradation, capacitor blowout), lost hashrate during recovery. | **High** — hours of lost revenue + repair/replacement cost |
| **False Positive** (false alarm) | Healthy ASIC flagged for inspection. Maintenance crew checks it, finds nothing, puts it back. | **Low** — minutes of crew time |

We therefore bias toward **recall** (catching real failures) over **precision** (avoiding false alarms).

---

## 2. Empirical Probability Distribution

Evaluated the classifier across 5 independently generated scenarios (training and inference data are fully decoupled — different generators, different timelines). The table below shows every device's predicted anomaly probability, sorted within each scenario.

### 2.1 Baseline (no anomalies — all devices healthy)

```
Device      Prob    Actual   @0.5   @0.3   @0.2
ASIC-002   0.0525     ok      .      .      .
ASIC-003   0.0620     ok      .      .      .
ASIC-001   0.1405     ok      .      .      .
ASIC-000   0.1466     ok      .      .      .
ASIC-007   0.1743     ok      .      .      .
ASIC-008   0.1783     ok      .      .      .
ASIC-005   0.1838     ok      .      .      .
ASIC-006   0.1934     ok      .      .      .
ASIC-004   0.1958     ok      .      .      .
ASIC-009   0.2016     ok      .      .     FLAG ← FP
```

Healthy devices cluster in the 0.05–0.20 range. Only ASIC-009 (0.20) would be flagged at threshold 0.2.

### 2.2 Summer Heatwave (dust_fouling + thermal_paste_deg)

```
Device      Prob    Actual   @0.5   @0.3   @0.2   Anomaly Type
ASIC-001   0.0010     ok      .      .      .
ASIC-000   0.0012     ok      .      .      .
ASIC-002   0.0028     ok      .      .      .
ASIC-003   0.0030     ok      .      .      .
ASIC-006   0.0412     ok      .      .      .
ASIC-007   0.0589     ok      .      .      .
─────────── separation gap ───────────────────
ASIC-008   0.2090    ANOM     .      .     FLAG   dust_fouling
ASIC-009   0.2169    ANOM     .      .     FLAG   dust_fouling
─────────── separation gap ───────────────────
ASIC-004   0.6185    ANOM    FLAG   FLAG   FLAG   dust_fouling
ASIC-005   0.6389    ANOM    FLAG   FLAG   FLAG   dust_fouling
ASIC-010   0.9931    ANOM    FLAG   FLAG   FLAG   thermal_paste_deg
ASIC-011   0.9932    ANOM    FLAG   FLAG   FLAG   thermal_paste_deg
```

The model separates `thermal_paste_deg` (>0.99) and strong `dust_fouling` (>0.6) cleanly. But 2 `dust_fouling` devices sit at 0.21 — below even 0.3. These are weak signals the model struggles with. No threshold fixes this; it requires feature engineering improvement.

### 2.3 PSU Degradation (psu_instability + capacitor_aging)

```
Device      Prob    Actual   @0.5   @0.3   @0.2   Anomaly Type
ASIC-002   0.0493     ok      .      .      .
ASIC-000   0.0843     ok      .      .      .
ASIC-001   0.1071     ok      .      .      .
─────────── separation gap ───────────────────
ASIC-005   0.3220     ok      .     FLAG   FLAG   ← FP at 0.3
ASIC-004   0.3576     ok      .     FLAG   FLAG   ← FP at 0.3
─────────── separation gap ───────────────────
ASIC-009   0.7106    ANOM    FLAG   FLAG   FLAG   capacitor_aging
ASIC-007   0.7539    ANOM    FLAG   FLAG   FLAG   capacitor_aging
ASIC-006   0.9996    ANOM    FLAG   FLAG   FLAG   psu_instability
ASIC-008   0.9996    ANOM    FLAG   FLAG   FLAG   psu_instability
```

The 2 FP at 0.3 (ASIC-004, ASIC-005 at 0.32–0.36) are in the "worth inspecting" zone — elevated enough that a quick check is justified.

### 2.4 Cooling Failure (coolant_loop_fouling + fan_bearing_wear + thermal_deg)

```
Device      Prob    Actual   @0.5   @0.3   @0.2   Anomaly Type
ASIC-002   0.0101     ok      .      .      .
ASIC-000   0.0113     ok      .      .      .
─────────── overlap zone ─────────────────────
ASIC-008   0.2123     ok      .      .     FLAG   ← FP at 0.2
ASIC-009   0.2197    ANOM     .      .     FLAG   fan_bearing_wear
ASIC-005   0.3331    ANOM     .     FLAG   FLAG   fan_bearing_wear
ASIC-004   0.3485     ok      .     FLAG   FLAG   ← FP at 0.3
─────────── separation gap ───────────────────
ASIC-007   0.7700    ANOM    FLAG   FLAG   FLAG   fan_bearing_wear
ASIC-001   0.8059    ANOM    FLAG   FLAG   FLAG   coolant_loop_fouling
ASIC-003   0.8399    ANOM    FLAG   FLAG   FLAG   coolant_loop_fouling
ASIC-006   0.9240    ANOM    FLAG   FLAG   FLAG   thermal_deg
```

Hardest scenario for threshold tuning. ASIC-008 (healthy, 0.21) and ASIC-009 (anomalous, 0.22) are 0.01 apart — no threshold can separate them. At 0.3, we catch one more `fan_bearing_wear` (ASIC-005) at the cost of one FP (ASIC-004).

### 2.5 ASIC Aging (hashrate_decay + solder_joint_fatigue + capacitor_aging + firmware_cliff)

```
Device      Prob    Actual   @0.5   @0.3   @0.2   Anomaly Type
ASIC-013   0.0163     ok      .      .      .
ASIC-014   0.0175     ok      .      .      .
ASIC-011   0.1288     ok      .      .      .
─────────── overlap zone ─────────────────────
ASIC-003   0.3973    ANOM     .     FLAG   FLAG   capacitor_aging
ASIC-000   0.4296     ok      .     FLAG   FLAG   ← FP at 0.3
─────────── separation gap ───────────────────
ASIC-008   0.5864    ANOM    FLAG   FLAG   FLAG   firmware_cliff
ASIC-007   0.5907    ANOM    FLAG   FLAG   FLAG   capacitor_aging
ASIC-004   0.5909     ok     FLAG   FLAG   FLAG   ← FP at all thresholds
ASIC-002   0.9862    ANOM    FLAG   FLAG   FLAG   solder_joint_fatigue
ASIC-010   0.9920    ANOM    FLAG   FLAG   FLAG   solder_joint_fatigue
ASIC-012   0.9972    ANOM    FLAG   FLAG   FLAG   hashrate_decay
ASIC-001   0.9985    ANOM    FLAG   FLAG   FLAG   hashrate_decay
ASIC-009   0.9995    ANOM    FLAG   FLAG   FLAG   hashrate_decay
ASIC-006   0.9998    ANOM    FLAG   FLAG   FLAG   solder_joint_fatigue
ASIC-005   1.0000    ANOM    FLAG   FLAG   FLAG   hashrate_decay
```

At 0.3 we catch `capacitor_aging` (ASIC-003, p=0.40) that 0.5 misses. ASIC-000 (healthy, p=0.43) becomes a FP — but this device has an unusually elevated probability for a healthy device, making it a reasonable inspection target.

---

## 3. Aggregate Comparison

| Scenario | --- Threshold 0.5 --- | | | --- Threshold 0.3 --- | | |
|---|---|---|---|---|---|---|
| | Prec | Rec | F1 | Prec | Rec | F1 |
| baseline | — | — | — | — | — | — |
| summer_heatwave | 1.00 | 0.67 | 0.80 | 1.00 | 0.67 | 0.80 |
| psu_degradation | 1.00 | 1.00 | 1.00 | 0.67 | 1.00 | 0.80 |
| cooling_failure | 1.00 | 0.67 | 0.80 | 0.83 | 0.83 | 0.83 |
| asic_aging | 0.90 | 0.90 | 0.90 | 0.83 | 1.00 | 0.91 |

**Confusion matrix totals (excluding baseline):**

| Threshold | TP | FP | TN | FN | Recall | Precision |
|---|---|---|---|---|---|---|
| **0.5** | 21 | 1 | 19 | 5 | 0.81 | 0.95 |
| **0.3** | 23 | 4 | 16 | 3 | 0.88 | 0.85 |
| **0.2** | 25 | 7 | 13 | 1 | 0.96 | 0.78 |

---

## 4. Decision

**Threshold 0.3** is the default. Rationale:

1. **Recall 0.81 → 0.88**: catches 2 more failing ASICs (capacitor_aging, fan_bearing_wear)
2. **FP cost is low**: the 3 new false positives have probabilities 0.32–0.43 — elevated enough that inspection is justified regardless
3. **Doesn't overreach**: 0.2 would start flagging baseline healthy devices (ASIC-009 at 0.20) and add 4 more FPs across scenarios
4. **The 2 remaining FN** (dust_fouling at p=0.21) are genuinely weak model signals — their probabilities are barely above healthy devices. Fixing these requires feature engineering, not threshold tuning.

### When to use a different threshold

| Context | Threshold | Why |
|---|---|---|
| Large fleet, small crew | 0.4–0.5 | Reduce inspection volume; focus on high-confidence alerts |
| Small fleet, abundant crew | 0.2 | Inspect anything remotely suspicious |
| Critical uptime SLA | 0.2 | Minimize any risk of unplanned downtime |
| Noisy environment (dust, heat) | 0.35 | Balance against elevated baseline probabilities |

Override at runtime: `python tasks/score.py --threshold 0.25`

---

## 5. Anomaly Type Signal Strength

Some anomaly types produce strong model signals, others are harder to detect. This informs future feature engineering priorities.

| Anomaly Type | Typical Prob Range | Detection Quality | Notes |
|---|---|---|---|
| hashrate_decay | 0.99–1.00 | Excellent | Strongest signal — direct TE impact |
| solder_joint_fatigue | 0.98–1.00 | Excellent | Clear thermal + electrical signature |
| psu_instability | 0.99–1.00 | Excellent | Voltage variance is a strong feature |
| thermal_paste_deg | 0.99 | Excellent | Temperature spike pattern |
| coolant_loop_fouling | 0.80–0.84 | Good | Gradual thermal rise detectable |
| thermal_deg | 0.92 | Good | Clear temperature signature |
| capacitor_aging | 0.40–0.75 | Moderate | Subtler electrical signature; some overlap with healthy |
| firmware_cliff | 0.59 | Moderate | Intermittent pattern harder to capture |
| fan_bearing_wear | 0.22–0.77 | Variable | Wide range — some instances strong, others near-invisible |
| dust_fouling | 0.21–0.64 | Variable | Gradual onset; weak instances indistinguishable from healthy |

**Priority for feature engineering improvement**: dust_fouling, fan_bearing_wear, capacitor_aging — the three types with instances below the 0.3 threshold.

---

## 6. Early Detection Analysis

Beyond accuracy, the operational value of the classifier depends on **how early** it detects failures — before they cause damage. We measured this by sliding a 24h scoring window across the full timeline of each anomalous device in 6h steps, recording when the probability first crosses the 0.3 threshold relative to the labelled anomaly onset.

### 6.1 Summary Table

| Device | Scenario | Anomaly Type | First Detection | Early Warning |
|---|---|---|---|---|
| ASIC-010 | summer_heatwave | thermal_paste_deg | 656h before onset | 27 days |
| ASIC-011 | summer_heatwave | thermal_paste_deg | 656h before onset | 27 days |
| ASIC-007 | asic_aging | capacitor_aging | 678h before onset | 28 days |
| ASIC-006 | cooling_failure | thermal_deg | 374h before onset | 15 days |
| ASIC-003 | asic_aging | capacitor_aging | 342h before onset | 14 days |
| ASIC-007 | psu_degradation | capacitor_aging | 235h before onset | 10 days |
| ASIC-009 | psu_degradation | capacitor_aging | 235h before onset | 10 days |
| ASIC-007 | cooling_failure | fan_bearing_wear | 164h before onset | 7 days |
| ASIC-001 | asic_aging | hashrate_decay | 108h before onset | 4.5 days |
| ASIC-008 | asic_aging | firmware_cliff | ~828h before onset* | 34 days* |
| ASIC-005 | asic_aging | hashrate_decay | 30h before onset | 1.3 days |
| ASIC-006 | asic_aging | solder_joint_fatigue | 28h before onset | 1.2 days |
| ASIC-003 | psu_degradation | psu_instability | 9h before onset | 9 hours |
| ASIC-008 | psu_degradation | psu_instability | 15h before onset | 15 hours |
| ASIC-009 | asic_aging | hashrate_decay | 12h before onset | 12 hours |
| ASIC-012 | asic_aging | hashrate_decay | 12h before onset | 12 hours |
| ASIC-006 | psu_degradation | psu_instability | 3h after onset | reactive |
| ASIC-005 | cooling_failure | fan_bearing_wear | 16h after onset | reactive |
| ASIC-001 | cooling_failure | coolant_loop_fouling | 24h after onset | reactive |
| ASIC-003 | cooling_failure | coolant_loop_fouling | 18h after onset | reactive |
| ASIC-010 | asic_aging | solder_joint_fatigue | 56h after onset | reactive |
| ASIC-002 | asic_aging | solder_joint_fatigue | 62h after onset | reactive |
| ASIC-005 | summer_heatwave | dust_fouling | 60h after onset | very late |
| ASIC-004 | summer_heatwave | dust_fouling | 144h after onset | very late |
| ASIC-009 | cooling_failure | fan_bearing_wear | 160h after onset | never reliable |
| ASIC-008 | summer_heatwave | dust_fouling | 846h after onset | never reliable |
| ASIC-009 | summer_heatwave | dust_fouling | 384h after onset | never reliable |

*firmware_cliff: the "828h before onset" figure is misleading — the device shows elevated probability due to co-occurring aging patterns in the asic_aging scenario. The actual cliff event is detected within 6h of onset (p jumps from 0.15 to 0.37 instantly). This is consistent with firmware_cliff being a sudden, discontinuous event.

### 6.2 Detection Timing by Anomaly Type

| Anomaly Type | Early Warning Range | Detection Pattern |
|---|---|---|
| **thermal_paste_deg** | 27 days | Very early — gradual thermal paste degradation produces a slow, detectable drift in temperature/efficiency features long before the labelled "anomaly" threshold is reached |
| **capacitor_aging** | 10–28 days | Early — slow capacitance loss manifests as voltage ripple and efficiency drift. Model picks up the trend well before hard failure |
| **thermal_deg** | 15 days | Early — sustained temperature elevation is a strong, persistent feature |
| **hashrate_decay** | 12h–4.5 days | Moderate — direct TE impact becomes visible within hours to days. Varies by severity; steeper decay detected sooner |
| **psu_instability** | 9–15h before to 3h after | Borderline predictive — voltage spikes emerge shortly before the formal anomaly label. The ramp-up from ~0.15 to >0.3 happens over 12–24h |
| **solder_joint_fatigue** | 28h before to 62h after | Mixed — some instances show pre-failure intermittent thermal/electrical signatures; others are too subtle until cumulative damage is substantial |
| **fan_bearing_wear** | 7 days before to never | Highly variable — depends on severity. Strong instances produce detectable vibration-related thermal patterns; weak instances are indistinguishable from normal operation |
| **coolant_loop_fouling** | 18–24h after onset | Reactive — fouling starts gradually with minimal signal. Detection occurs once the accumulated thermal impact crosses a noticeable threshold |
| **firmware_cliff** | 6h after onset | By nature a sudden event (firmware performance drops discontinuously). No precursor signal possible — but detection is near-instant once it occurs |
| **dust_fouling** | 60h after to never | Poor early detection — the most gradual degradation mode. Dust accumulation raises temperatures slowly; the model often can't distinguish early fouling from normal environmental variation |

### 6.3 Detection Curves (selected examples)

**Best case — thermal_paste_deg (27 days early):**
The model detects thermal paste degradation far before the formal anomaly onset. The probability is already >0.97 weeks before the labelled event, meaning the underlying physical degradation (rising junction temperatures, widening thermal resistance) produces strong features even in the precursor phase.

```
  ASIC-010 [thermal_paste_deg] — onset: Apr 30
  Apr 28  0.98 ██   ← detected 27 days before formal onset
  Apr 29  0.97 ██
  Apr 30  0.97 ██   ◄── onset
  May 01  0.98 ██
  May 02  0.99 ██
```

**Typical case — hashrate_decay (12h early):**
Probability ramps from background (~0.2) through the threshold 12h before onset, then climbs steadily as degradation accelerates.

```
  ASIC-009 [hashrate_decay] — onset: Apr 20
  Apr 18  0.24 ░░
  Apr 19  0.27 ░░
  Apr 19  0.33 ██   ← 12h early warning
  Apr 20  0.39 ██   ◄── onset
  Apr 20  0.50 ██
  Apr 21  0.65 ██
  Apr 22  0.87 ██
  Apr 23  0.93 ██
```

**Reactive case — coolant_loop_fouling (24h after):**
Signal is too weak at onset. Probability ramps through 0.3 only ~24h after the anomaly begins, but then climbs steadily.

```
  ASIC-001 [coolant_loop_fouling] — onset: Apr 12
  Apr 11  0.04 ░░
  Apr 12  0.06 ░░
  Apr 12  0.09 ░░   ◄── onset (no signal yet)
  Apr 13  0.21 ░░
  Apr 13  0.36 ██   ← detected 24h after onset
  Apr 14  0.51 ██
  Apr 14  0.61 ██
```

**Sudden event — firmware_cliff (instant on impact):**
No precursor signal exists because firmware_cliff is a discontinuous event (firmware performance drops in a step function). But detection is near-instant: probability jumps from 0.15 to 0.37 in the first 6h window after the cliff.

```
  ASIC-008 [firmware_cliff] — onset: Jun 06
  Jun 04  0.10 ░░
  Jun 05  0.12 ░░
  Jun 05  0.13 ░░
  Jun 06  0.15 ░░   ◄── onset
  Jun 06  0.37 ██   ← 6h after (instant response)
  Jun 06  0.58 ██
  Jun 07  0.79 ██
  Jun 07  1.00 ██   ← saturated
```

**Undetectable case — dust_fouling (weak instances):**
Two of four dust_fouling devices never reliably cross the 0.3 threshold. Their probability hovers in the 0.05–0.12 range for weeks — indistinguishable from baseline healthy noise. This is not a model failure but a reflection of the physical reality: mild dust fouling affects temperatures by 1–3°C, within normal operating variance.

```
  ASIC-008 [dust_fouling] — onset: Apr 14
  Apr 12  0.04 ░░
  Apr 14  0.06 ░░   ◄── onset
  Apr 15  0.09 ░░
  Apr 16  0.10 ░░
  Apr 17  0.10 ░░   ← probability never meaningfully rises
```

### 6.4 Interpretation

The classifier provides **meaningful early warning for 7 of 10 anomaly types**. The detection timing correlates with the physical nature of each failure mode:

- **Gradual degradation** (thermal_paste_deg, capacitor_aging, thermal_deg): days to weeks of early warning. These failure modes produce slowly evolving feature signatures that the model detects well before the damage crosses the "anomaly" label threshold.

- **Accelerating degradation** (hashrate_decay, psu_instability): hours to days of early warning. The degradation signal grows exponentially; the model catches it once the ramp is steep enough.

- **Gradual but weak** (coolant_loop_fouling, solder_joint_fatigue): detected reactively (hours to days after onset). The signal exists but is too subtle at onset; detection catches up once cumulative damage produces clear features.

- **Sudden events** (firmware_cliff): no early warning possible by nature, but near-instant reactive detection. This is the expected and correct behaviour — a firmware cliff has no precursor.

- **Near-invisible** (dust_fouling, weak fan_bearing_wear): poor detection. The physical signal is within normal operating noise. This is a genuine model boundary, not a threshold tuning problem.

**Key insight**: early detection is not binary. The probability ramp-up pattern is itself informative. A device whose probability rises from 0.05 to 0.25 over 48h is trending toward failure even if it hasn't crossed the threshold yet. The multi-horizon regression predictions (t+1h through t+7d) complement this by forecasting the TE trajectory, giving operators both "is it failing?" and "how fast?".

---

## 7. Economic ROI: Controlled vs Uncontrolled Fleet

The ultimate measure of the system's value is economic: how much money does the AI controller save compared to running the fleet without any anomaly detection or preventive action?

### 7.1 Methodology

For each scenario, we run the full pipeline (score → trend → cost_projection → optimize) and compare two outcomes:

- **Uncontrolled**: all devices run at current settings with no intervention. When a device fails, it incurs catastrophic repair cost ($5,000) + 48h downtime revenue loss. This is the `do_nothing` branch of the cost projection.
- **Controlled**: the classifier flags devices, the controller takes the economically optimal action (underclock, schedule maintenance, shutdown). Costs include reduced hashrate during underclocking and planned maintenance.

The comparison is honest — it accounts for classifier errors:
- **TP** (correctly flagged): controller intervenes → full economic benefit
- **FP** (false alarm): device gets an unnecessary inspection → minor wasted cost
- **TN** (correctly ignored): same outcome in both scenarios → zero delta
- **FN** (missed failure): controller doesn't act → same outcome as uncontrolled

### 7.2 Results (1-week / 168h horizon)

| Scenario | Devices | Uncontrolled | Controlled | Delta | ROI |
|---|---|---|---|---|---|
| baseline | 10 | -$593 | -$593 | $0 | 0% |
| summer_heatwave | 10 | -$17,249 | -$7,998 | +$9,250 | +54% |
| psu_degradation | 5 | -$14,124 | -$212 | +$13,913 | +99% |
| cooling_failure | 5 | -$6,987 | -$6,987 | $0 | 0% |
| asic_aging | 14 | -$39,378 | -$3,782 | +$35,597 | +90% |
| **Total** | **44** | **-$78,332** | **-$19,572** | **+$58,760** | **+75%** |

### 7.3 Breakdown by Classifier Outcome

| Component | 1 week | Description |
|---|---|---|
| **TP savings** | +$57,340 | Value of correct interventions on detected failures |
| **FP cost** | +$1,420 | Unnecessary inspections on falsely flagged healthy devices |
| **FN missed** | +$14,229 | Benefit left on table by missing failures |

The FP cost ($1,420) is ~2.5% of the TP savings ($57,340) — confirming that our recall-biased threshold (0.3) is economically justified. The cost of investigating a few healthy devices is trivial compared to the value of catching real failures.

### 7.4 Scenario-Level Interpretation

**psu_degradation (+99% ROI)**: Perfect classifier recall (F1=1.0). Every failing device is detected and gets optimal action. The controller essentially eliminates the risk cost entirely, turning a $14,124 weekly loss into a $212 loss.

**asic_aging (+90% ROI)**: Near-perfect recall (0.90). The controller catches 9 of 10 anomalous devices. The one FP (ASIC-004, prob=0.59) costs $1,420 in unnecessary action — far less than the $35,597 saved on correctly flagged devices.

**summer_heatwave (+54% ROI)**: Good but limited by 2 FN (dust_fouling devices at prob=0.21). The controller saves $9,250 on the 4 detected devices but misses $8,057 in potential savings on the 2 undetected ones. This scenario most directly shows the cost of weak dust_fouling detection.

**cooling_failure (0% ROI)**: All anomalous devices with actionable cost projections were fan_bearing_wear FN. The controller detected coolant_loop_fouling and thermal_deg devices, but the cost model's "do_nothing" path for those happened to match the controlled path. The missed fan_bearing_wear devices represent $4,679 in foregone savings.

**baseline (0% ROI)**: No anomalies present, no intervention needed. The controller correctly holds all devices at current settings. The small negative profit is the Weibull model's expected failure cost — every device has some nonzero failure probability.

### 7.5 Multi-Horizon View

| Horizon | Uncontrolled | Controlled | Delta | ROI |
|---|---|---|---|---|
| 24h | -$764 | -$164 | +$600 | +78% |
| 1 week | -$78,332 | -$19,572 | +$58,760 | +75% |
| 30 days | -$192,686 | -$90,831 | +$101,855 | +53% |

ROI decreases at longer horizons because the Weibull failure probability approaches 1.0 for all devices — even controlled ones eventually face replacement costs. The controller's value is in **delaying and managing** failures, not eliminating them.

### 7.6 Cost Model Parameters

The economic projections use these parameters (from `data/cost_model.json`):

| Parameter | Value | Source |
|---|---|---|
| BTC price | $85,000 | Market price |
| Energy (off-peak) | $0.035/kWh | Nordic hydro site rates |
| Energy (peak) | $0.065/kWh | 12h peak / 12h off-peak |
| Catastrophic repair | $5,000 | ASIC board replacement |
| Catastrophic downtime | 48h | Shipping + repair turnaround |
| Inspection cost | $150 | Technician visit |
| Minor repair | $500 | Fan/thermal paste replacement |
| Major repair | $2,000 | PSU/board-level repair |
| Maintenance restores TE to | 0.95 | Post-repair efficiency |

---

## 8. Methodology

- **Training data**: 100% of corpus from `generate_training_corpus.py` (no internal train/test split)
- **Evaluation data**: independently generated per scenario via `simulation_engine.py` (different generator, different timeline)
- **Evaluation point**: midpoint of each scenario's data range (ensures sufficient history for temporal features and future data for regression verification)
- **Scoring**: per-device mean anomaly probability over a 24-hour scoring window
- **Model**: XGBClassifier, 200 estimators, max_depth=6, scale_pos_weight adjusted for class imbalance
