# Fleet Intelligence — Requirements Specification

**AI-Driven Mining Optimization & Predictive Maintenance**

Wiktor Lisowski | April 2026

Last edited: 2026-04-06

### Change Log

| Date | Change |
|------|--------|
| 2026-04-18 | §1.1 AI Reasoning Layer updated: added `knowledge_query` (organizational RAG) alongside `fleet_status_query` and `web_search` |
| 2026-04-18 | DR-CAL-09 corrected: 50→75 features. Fixed stale EXPECTED_TELEMETRY_COLS in features.py (was counting 18 raw telemetry/label cols as engineered features) |
| 2026-04-10 | DR-POR-01 updated: file interface → Validance API (SafeClaw fleet_status_query). Traceability fix: simulation_loop.py → orchestrate_simulation.py |
| 2026-04-06 | Step 2: DR-CAL-02 adds 7d window, DR-CAL-09 updated 43→50 features with 7-day rolling windows + hardware diagnostics |
| 2026-04-06 | Updated DR-CAL-09: 37→43 features with hardware health sensor group |
| 2026-04-05 | Added DR-CAL-09 (feature selection scope), updated traceability matrix |
| 2026-04-05 | Initial version |

---

## 1. Scope & Definitions

### 1.1 System Boundary

This specification covers the **Fleet Intelligence** system: an AI-driven optimization and predictive maintenance pipeline for Bitcoin ASIC mining operations. The system spans three layers:


| Layer                             | Responsibility                                                                                                                                | Boundary                                                        |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **ML Detection Layer**            | Ingest telemetry, engineer features, compute efficiency KPIs, classify anomalies, score fleet risk, detect trends, classify operational tiers | Deterministic, reproducible outputs. No side effects.           |
| **AI Reasoning Layer** (SafeClaw) | Read ML outputs via `fleet_status_query`, market context via `web_search`, organizational knowledge via `knowledge_query` (RAG), propose specific device commands with natural-language rationale | Contextual reasoning. Proposes actions but cannot execute them. |
| **Governance Layer** (Validance)  | Approval gates, learned policies, rate limits, audit trail, content-addressed execution chain                                                 | Human-in-the-loop control. Every action is traceable.           |


**Out of scope**: The SafeClaw plugin and Validance kernel have their own requirements documents (`safeclaw/docs/requirements.md`, `validance-workflow/CLAUDE.md`). This document covers the mining-specific ML pipeline and its integration contract with the reasoning and governance layers.

### 1.2 Requirement Layers


| Layer                  | Abbreviation | Perspective                                           | Boundary test                                                                   |
| ---------------------- | ------------ | ----------------------------------------------------- | ------------------------------------------------------------------------------- |
| **User Requirement**   | UR           | Stakeholder need — what the operator wants            | "Would this appear in a sales conversation?"                                    |
| **System Requirement** | SR           | Testable system behavior — what the system must do    | "If I changed the implementation, would the user notice?"                       |
| **Design Requirement** | DR           | Architectural decision — how the system is structured | "If I changed this, would the architecture break but the user wouldn't notice?" |


### 1.3 Traceability Convention

Every requirement has a hierarchical ID: `UR-##`, `SR-XX-##`, `DR-XX-##`.

- **SR groups**: DP (Data Pipeline), AD (Anomaly Detection), PA (Predictive Analytics), SC (Safety & Control), AR (Action Reasoning), RO (Reporting & Observability), CO (Continuous Operation)
- **DR groups**: REP (Reproducibility), AUD (Auditability), SAF (Safety Bias), INT (Interpretability), POR (Portability), CAL (Calibration)

Forward references (UR → SR → DR) and backward references (DR → SR → UR) are maintained in the traceability matrix (Section 5).

### 1.4 Reference Documents


| Document              | Location                        |
| --------------------- | ------------------------------- |
| Assignment brief      | MDK Plan B (Tether)             |
| Technical report      | `docs/technical-report.md`      |
| System overview       | `docs/system-overview.md`       |
| True Efficiency KPI   | `docs/true-efficiency-kpi.md`   |
| MOS platform audit    | `docs/mos_platform_audit.md`    |
| Evaluation analysis   | `docs/evaluation-analysis.md`   |
| SafeClaw requirements | `safeclaw/docs/requirements.md` |
| SafeClaw architecture | `safeclaw/docs/architecture.md` |


---

## 2. User Requirements (UR)

Stakeholder-level needs derived from the assignment brief, mining domain, and two-layer architecture. Technology-neutral.


| ID    | Requirement                                                                                         | Rationale                                                                                                                                                              |
| ----- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UR-01 | The operator shall receive early warning of device degradation before critical failure.             | ASIC repair is the largest single cost line. Failures manifest as gradual degradation detectable days before critical failure.                                         |
| UR-02 | The operator shall understand *why* a device is degrading — root cause, not just "anomaly".         | Actionable maintenance requires knowing whether the problem is thermal, electrical, or mechanical.                                                                     |
| UR-03 | The operator shall receive actionable maintenance recommendations with rationale.                   | Operators need specific commands (underclock, inspect, shutdown) with reasoning, not just alerts.                                                                      |
| UR-04 | The system shall respect physical safety limits regardless of optimization goals.                   | No optimization should risk hardware damage (thermal runaway, coolant freeze, overvoltage).                                                                            |
| UR-05 | The operator shall have visibility into fleet health at a glance.                                   | Fleet-scale operations require dashboard-level awareness, not per-device log inspection.                                                                               |
| UR-06 | Every fleet action shall be traceable from proposal through execution.                              | Regulatory and operational accountability. "Who approved what, when, and why?"                                                                                         |
| UR-07 | The operator shall control the approval policy for fleet actions.                                   | Human approval by default; trust escalation for safe recurring patterns. The operator sets the policy, not the system.                                                 |
| UR-08 | The system shall evaluate device efficiency beyond naive J/TH.                                      | Standard J/TH conflates voltage waste, cooling overhead, and ambient conditions. Cross-device and cross-condition comparison is unreliable without a corrected metric. |
| UR-09 | The system shall operate continuously without manual intervention per cycle.                        | Training, inference, and reporting must run as automated pipelines, not manual scripts.                                                                                |
| UR-10 | The operator shall be able to adapt the system to different fleet compositions and site conditions. | Mining operators run heterogeneous fleets (6+ ASIC models) across sites with varying ambient conditions and energy pricing.                                                 |


---

## 3. System Requirements (SR)

Testable, technology-neutral requirements grouped by concern. No algorithm names, library names, or file formats in this section.

### 3.1 Data Pipeline (SR-DP)


| ID       | Requirement                                                                                                                                           | Traces to    |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| SR-DP-01 | The system shall ingest fleet telemetry and produce typed, validated records with schema enforcement, duplicate removal, and type coercion.           | UR-09        |
| SR-DP-02 | The system shall engineer derived features from raw telemetry using rolling statistics at multiple time horizons (sub-hour, hourly, half-day, daily). | UR-01, UR-10 |
| SR-DP-03 | The system shall compute fleet-relative normalized scores per device within its hardware cohort.                                                      | UR-10        |
| SR-DP-04 | The system shall compute physics-informed interaction features that capture voltage stress, thermal headroom, and cooling quality.                    | UR-02, UR-08 |


### 3.2 Anomaly Detection & Health Assessment (SR-AD)


| ID       | Requirement                                                                                                                                                                              | Traces to    |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| SR-AD-01 | The system shall detect anomalous devices using supervised classification on historical telemetry, producing a per-sample anomaly probability.                                           | UR-01        |
| SR-AD-02 | The system shall classify anomalies by root cause type (thermal, electrical, mechanical degradation mechanisms).                                                                         | UR-02        |
| SR-AD-03 | The system shall compute a per-device health score that normalizes efficiency against each device's nominal baseline.                                                                    | UR-05, UR-08 |
| SR-AD-04 | The system shall compute a corrected efficiency metric that separates voltage inefficiency, cooling overhead, and intrinsic hardware performance into independent diagnostic components. | UR-08        |
| SR-AD-05 | The efficiency metric shall normalize cooling cost to a reference ambient temperature, removing geographic and seasonal bias.                                                            | UR-08, UR-10 |
| SR-AD-06 | The system shall handle class imbalance between healthy and anomalous samples without manual resampling.                                                                                 | UR-01        |


### 3.3 Predictive Analytics (SR-PA)


| ID       | Requirement                                                                                                                                                          | Traces to    |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| SR-PA-01 | The system shall forecast device health at multiple time horizons (immediate, next-shift, planning, strategic) with uncertainty bounds.                              | UR-01, UR-03 |
| SR-PA-02 | The system shall detect regime changes in device behavior (sudden shifts vs. gradual drift).                                                                         | UR-01, UR-02 |
| SR-PA-03 | The system shall project when a device's health will cross degradation and critical thresholds, with a confidence estimate.                                          | UR-01, UR-03 |
| SR-PA-04 | The system shall compute per-device trend vectors at multiple time scales, classifying trend direction (fast decline, declining, stable, recovering, fast recovery). | UR-01, UR-05 |
| SR-PA-05 | The system shall score fleet risk over a sliding time window, aggregating per-sample predictions into per-device risk summaries.                                     | UR-05        |


### 3.4 Safety & Control (SR-SC)


| ID       | Requirement                                                                                                                          | Traces to    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| SR-SC-01 | The system shall prevent device operation above 80 C chip temperature by forcing an underclock.                                      | UR-04        |
| SR-SC-02 | The system shall detect dangerously low temperatures (below 10 C) and trigger protective shutdown to prevent coolant freeze damage.  | UR-04, UR-10 |
| SR-SC-03 | The system shall detect overvoltage conditions and restore nominal operating parameters.                                             | UR-04        |
| SR-SC-04 | Safety overrides shall take precedence over all optimization-derived tier classifications.                                           | UR-04        |
| SR-SC-05 | The system shall classify devices into operational tiers (critical, warning, degraded, healthy) with deterministic, auditable rules. | UR-01, UR-05 |
| SR-SC-06 | The system shall never schedule all devices of the same hardware model for simultaneous maintenance.                                 | UR-04, UR-09 |
| SR-SC-07 | The system shall enforce minimum fleet hashrate capacity during underclock and maintenance operations.                               | UR-04        |
| SR-SC-08 | The system shall limit the fraction of devices offline for maintenance at any given time.                                            | UR-04, UR-09 |
| SR-SC-09 | Trend-aware escalation shall only promote devices to higher-severity tiers, never de-escalate (conservative bias).                   | UR-04        |


### 3.5 Action Reasoning & Approval (SR-AR)


| ID       | Requirement                                                                                                                                                                                       | Traces to    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| SR-AR-01 | The system shall propose specific device commands (underclock, maintenance, shutdown) with natural-language rationale incorporating risk context, market conditions, and operational constraints. | UR-03        |
| SR-AR-02 | All proposed actions shall pass through an approval gate before execution.                                                                                                                        | UR-06, UR-07 |
| SR-AR-03 | The operator shall be able to approve, deny, or create standing policies for recurring action patterns.                                                                                           | UR-07        |
| SR-AR-04 | Emergency shutdown actions shall require explicit human approval regardless of any learned policy.                                                                                                | UR-04, UR-07 |
| SR-AR-05 | The system shall validate action feasibility (fleet capacity, hardware constraints) before presenting proposals for approval.                                                                     | UR-04        |


### 3.6 Reporting & Observability (SR-RO)


| ID       | Requirement                                                                                                                                | Traces to    |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------ |
| SR-RO-01 | The system shall produce a self-contained visual dashboard showing fleet health, risk rankings, efficiency trends, and controller actions. | UR-05        |
| SR-RO-02 | The system shall report per-anomaly-type detection coverage with feature importance rankings.                                              | UR-02, UR-05 |
| SR-RO-03 | The system shall provide read-only fleet status queries (summary, device detail, tier breakdown, risk ranking) without side effects.       | UR-05        |


### 3.7 Continuous Operation (SR-CO)


| ID       | Requirement                                                                                                                                           | Traces to |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| SR-CO-01 | The pipeline shall execute as composable, independently triggerable workflow stages that can be chained without manual intervention.                  | UR-09     |
| SR-CO-02 | The system shall support continuous simulation loops that alternate training and inference cycles.                                                    | UR-09     |
| SR-CO-03 | The system shall generate synthetic training data from physics-based simulation covering heterogeneous hardware models and diverse failure scenarios. | UR-10     |
| SR-CO-04 | The system shall detect when the predictive model has drifted and recommend retraining.                                                               | UR-09     |


---

## 4. Design Requirements (DR)

Architectural decisions grouped by quality attribute. Each names the decision, the rationale, and what could be swapped without breaking the corresponding SR.

### 4.1 Reproducibility & Determinism (DR-REP)


| ID        | Decision                                                                                             | Quality attribute                                       | Rationale                                                                                                                                                             | Swappable                                             | Traces to          |
| --------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------ |
| DR-REP-01 | Anomaly detection uses XGBoost gradient boosting (n_estimators=200, max_depth=6, learning_rate=0.1). | Training speed, feature interaction handling            | Gradient boosting handles mixed feature types, captures non-linear interactions between TE components, and trains in minutes on 1.6M rows.                            | Random Forest, LightGBM, LSTM                         | SR-AD-01           |
| DR-REP-02 | Quantile regression uses 12 separate XGBoost regressors (4 horizons x 3 quantiles: p10/p50/p90).     | Uncertainty quantification, horizon flexibility         | Per-quantile models avoid crossing quantile violations inherent in multi-output approaches. Each horizon is independently tunable.                                    | Quantile random forest, conformal prediction, NGBoost | SR-PA-01           |
| DR-REP-03 | Inter-task data passes through Parquet files in a shared working directory.                          | Typed columnar storage, compression, schema enforcement | Parquet preserves types across task boundaries; no serialization ambiguity. Compressed columnar format handles 1.6M-row datasets efficiently.                         | Arrow IPC, Protocol Buffers, SQLite                   | SR-DP-01, SR-CO-01 |
| DR-REP-04 | Classifier is trained on 100% of the corpus with no internal train/test split.                       | Coverage of rare anomaly types                          | Internal splits leak temporal patterns. Evaluation occurs at inference time against independently generated data. All 10 anomaly types get maximum training coverage. | Temporal cross-validation, stratified k-fold          | SR-AD-01, SR-AD-06 |
| DR-REP-05 | Physics engine uses deterministic seeds per scenario.                                                | Reproducibility of synthetic training data              | Identical seeds produce identical corpora across runs, enabling regression testing of model changes.                                                                  | Fixed random state in data loader                     | SR-CO-03           |


### 4.2 Auditability & Traceability (DR-AUD)


| ID        | Decision                                                                                       | Quality attribute           | Rationale                                                                                                                                                                 | Swappable                                            | Traces to          |
| --------- | ---------------------------------------------------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ------------------ |
| DR-AUD-01 | All workflow executions are content-addressed via SHA-256 hash chains.                         | Tamper evidence, provenance | Any modification to inputs or task definitions changes the hash, making silent tampering detectable. This is the core value proposition of Validance as execution engine. | Merkle tree, blockchain-style ledger                 | SR-AR-02, SR-CO-01 |
| DR-AUD-02 | Fleet control actions append to an immutable JSON audit log per action.                        | Operational accountability  | Each action records timestamp, device_id, parameters, result, and fleet impact. Supports post-incident root-cause analysis.                                               | Structured logging (ELK), event sourcing             | SR-AR-02           |
| DR-AUD-03 | Orchestration uses session-scoped hashes linking all workflows in a training or inference run. | Cross-workflow traceability | A single session hash connects corpus generation, preprocessing, training, scoring, and analysis. Audit queries can reconstruct the full execution history.               | Correlation IDs, distributed tracing (OpenTelemetry) | SR-CO-01           |


### 4.3 Safety Bias & Conservatism (DR-SAF)


| ID        | Decision                                                                                         | Quality attribute            | Rationale                                                                                                                                                                    | Swappable                                                                   | Traces to |
| --------- | ------------------------------------------------------------------------------------------------ | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | --------- |
| DR-SAF-01 | Classifier threshold set to 0.3 (biased toward recall over precision).                           | Safety bias                  | False negative cost (missed failure, ~$5k repair) far exceeds false positive cost (unnecessary inspection, ~$150). A 0.3 threshold maximizes recall at acceptable precision. | Any threshold in [0.1, 0.5]; CLI `--threshold` overrides without retraining | SR-AD-01  |
| DR-SAF-02 | Thermal hard limit at 80 C forces underclock to 80% of stock frequency.                          | Hardware protection          | MOS PCB critical threshold. Above 80 C, junction temperatures reach 90-95 C; sustained operation risks solder joint degradation. Source: MOS documentation.                  | Any threshold in [75, 85] C based on hardware specs                         | SR-SC-01  |
| DR-SAF-03 | Emergency low temperature at 10 C triggers sleep mode.                                           | Coolant freeze prevention    | At hydro-cooled sites (64.5 N latitude), coolant viscosity spikes at low temperatures; pump flow drops, causing localized hotspots. Condensation risk on PCBs.               | Adjustable per site based on coolant type and ambient profile               | SR-SC-02  |
| DR-SAF-04 | Trend escalation is one-directional: only promotes to higher-severity tiers, never de-escalates. | Conservative bias            | A device showing recovery might relapse. De-escalation should be a deliberate operator decision, not an automated response.                                                  | Configurable de-escalation with hysteresis band                             | SR-SC-09  |
| DR-SAF-05 | Emergency shutdown carries a policy ceiling — can never be auto-approved via learned policies.   | Irreversibility protection   | Shutdown is the highest-impact action. Even if the operator has approved similar shutdowns before, each instance requires fresh human judgment.                              | Configurable per action severity class                                      | SR-AR-04  |
| DR-SAF-06 | Minimum underclock limit at 50% of stock frequency.                                              | V/f firmware validity        | ASIC V/f lookup tables are only validated above 50% frequency. Below this, efficiency degrades unpredictably.                                                                | Per-model calibration if firmware data available                            | SR-SC-07  |
| DR-SAF-07 | Minimum fleet hashrate capacity enforced at 70% during underclock operations.                    | Revenue protection           | 70% capacity still covers operating costs. Below this, the economic case for continued operation breaks down.                                                                | Configurable based on energy cost and BTC price                             | SR-SC-07  |
| DR-SAF-08 | Maximum 20% of fleet offline for maintenance simultaneously.                                     | Cascading failure prevention | Removing too many devices at once risks overloading remaining hardware and cascading thermal issues.                                                                         | Adjustable based on fleet redundancy and site topology                      | SR-SC-08  |


### 4.4 Interpretability (DR-INT)


| ID        | Decision                                                                                                                       | Quality attribute               | Rationale                                                                                                                                                                            | Swappable                                                 | Traces to          |
| --------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- | ------------------ |
| DR-INT-01 | True Efficiency decomposes into three independent diagnostic factors: TE_base (hardware), 1/eta_v (voltage), R_cool (cooling). | Failure mode isolation          | Each factor maps to a single physical mechanism. The model trains on *which component is drifting*, not raw correlated signals. This makes predictions interpretable and actionable. | Any decomposition that isolates independent failure modes | SR-AD-04           |
| DR-INT-02 | V/f scaling uses exponent 0.6 for sub-linear voltage-frequency relationship.                                                   | CMOS physics fidelity           | Modern CMOS does not scale V linearly with f. The 0.6 exponent reflects measured sub-linear scaling in 7nm-14nm process nodes used by mining ASICs.                                  | Per-model empirical calibration if V/f data available     | SR-AD-04, SR-AD-05 |
| DR-INT-03 | Cooling cost normalized to 25 C reference ambient.                                                                             | Geographic fairness             | Removes site-specific ambient advantage/penalty. A device at -5 C with free cooling is compared fairly against one at 35 C with active cooling.                                      | Any reference temperature; 25 C is industry standard      | SR-AD-05           |
| DR-INT-04 | CUSUM regime detection uses h=8.0, k=0.5 (Hawkins defaults).                                                                   | Sensitivity/false-alarm balance | Detects approximately 1-sigma shifts at 5-minute sampling resolution. Reference period is first 25% of device history to avoid contaminating the baseline.                           | Adaptive CUSUM, Bayesian changepoint detection            | SR-PA-02           |
| DR-INT-05 | Per-anomaly-type sub-classifiers with feature importance rankings.                                                             | Root cause attribution          | One classifier per anomaly type enables per-type feature importance, showing which telemetry signals drive each diagnosis.                                                           | SHAP values, attention weights (if using neural models)   | SR-AD-02           |


### 4.5 Portability & Decoupling (DR-POR)


| ID        | Decision                                                                                                                    | Quality attribute                           | Rationale                                                                                                                                                                                                | Swappable                                            | Traces to       |
| --------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | --------------- |
| DR-POR-01 | ML layer and reasoning layer are decoupled via Validance API. The AI agent reads ML outputs through SafeClaw `fleet_status_query` (routed to Validance), not direct file access or function calls. | Testability, independent deployment         | Either layer can be tested, replaced, or run independently. The ML layer is deterministic and reproducible; the reasoning layer is contextual and non-deterministic. The API interface (with `input_files` refs) is the contract. | gRPC, message queue, shared database                 | SR-AR-01        |
| DR-POR-02 | Pipeline is split into 5 composable single-concern workflows chained via `continue_from`.                                   | Independent triggering, shared prefix reuse | Pre-processing runs once; both training and inference reuse its outputs. Workflows can be triggered, monitored, and retried independently.                                                               | Monolithic pipeline, DAG scheduler (Airflow)         | SR-CO-01        |
| DR-POR-03 | Each task runs in a Docker container with file-based I/O through a shared working directory.                                | Isolation, reproducibility                  | Container boundaries enforce dependency isolation. Shared `/work` directory provides a simple, debuggable data contract.                                                                                 | Kubernetes pods, serverless functions                | SR-CO-01        |
| DR-POR-04 | Hardware models are parameterized via fleet metadata JSON, not hardcoded.                                                   | Fleet adaptability                          | Adding a new ASIC model requires only a metadata entry (stock clock, voltage, nominal hashrate/power). No code changes.                                                                                  | Database-backed device registry                      | SR-DP-01, UR-10 |
| DR-POR-05 | Voltage is controlled indirectly via frequency (V/f coupling in ASIC firmware).                                             | MOS platform compatibility                  | MOS exposes `setFrequency` as the primary RPC. Voltage is not independently controllable. Reducing frequency implicitly restores the nominal V/f point.                                                  | Direct voltage control if future firmware exposes it | SR-SC-03        |


### 4.6 Calibration & Tuning (DR-CAL)


| ID        | Decision                                                                                                                                                                         | Quality attribute            | Rationale                                                                                                                                                        | Swappable                                    | Traces to |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- | --------- |
| DR-CAL-01 | Prediction horizons at 1h, 6h, 24h, 7d map to operator planning windows.                                                                                                         | Operational relevance        | 1h = immediate response, 6h = shift planning, 24h = daily scheduling, 7d = strategic maintenance windows.                                                        | Any horizon set matching operator workflow   | SR-PA-01  |
| DR-CAL-02 | Rolling feature windows at 30m, 1h, 12h, 24h, 7d (6, 12, 144, 288, 2016 samples at 5-min intervals).                                                                              | Multi-scale pattern capture  | Short windows catch transient events; long windows capture gradual drift. 30m approximates MOS's native hashrate averaging window. 7d captures week-scale degradation (fan bearing wear, PSU capacitor aging, thermal paste degradation, dust fouling). | Window sizes tunable per deployment          | SR-DP-02  |
| DR-CAL-03 | Tier thresholds: critical > 0.9 risk, warning > 0.5 risk, degraded < 0.8 health score.                                                                                           | Operational severity mapping | Three thresholds create four tiers with distinct response protocols. Thresholds derived from mining economics (inspection cost vs. failure cost).                | Per-site calibration based on cost structure | SR-SC-05  |
| DR-CAL-04 | Trend direction thresholds: fast decline < -0.02/h, decline < -0.005/h, stable within +/-0.005/h.                                                                                | Noise floor separation       | At 5-min sampling, slopes within +/-0.005/h are indistinguishable from measurement noise.                                                                        | Calibrate against site-specific noise floor  | SR-PA-04  |
| DR-CAL-05 | Synthetic training data generated from physics-based simulation (CMOS power model, thermal inertia, cooling controller) across 5 scenarios, 10 anomaly types, 6 hardware models. | Testability, coverage        | Real fleet data was not available for this assignment. Physics-based simulation produces labeled data with known ground truth. The simulator is the test oracle. | Real fleet telemetry when available          | SR-CO-03  |
| DR-CAL-06 | Class imbalance handled via `scale_pos_weight = n_negative / n_positive` in the classifier.                                                                                      | Training stability           | Avoids oversampling artifacts (SMOTE) or undersampling information loss. The classifier sees weighted loss proportional to class rarity.                         | SMOTE, class-weighted loss, focal loss       | SR-AD-06  |
| DR-CAL-07 | Retraining triggered when rolling RMSE exceeds 2x baseline for 3 consecutive cycles, or calibration drift exceeds 30%, or KS-test detects fleet regime shift.                    | Model freshness              | Multiple trigger conditions catch different drift modes: prediction accuracy degradation, uncertainty miscalibration, and distributional shift.                  | Online learning, sliding-window retraining   | SR-CO-04  |
| DR-CAL-08 | Synthetic telemetry noise uses 0.5% Gaussian on hashrate, 1 mV Gaussian on voltage ripple, 20 RPM Gaussian on fan speed. Power has no additive sensor noise (deterministic CMOS model). | Training data realism        | Conservative noise floor ensures the classifier learns degradation patterns, not sensor artifacts. At 0.5% hashrate noise, a 2% efficiency drift is clearly separable; at 5% it would not be. | Lognormal noise, site-calibrated noise profiles from real MOS telemetry | SR-CO-03  |
| DR-CAL-09 | 75 engineered features by domain grouping: rolling statistics (6 cols × 9 windows = 54), hashrate 30m (2), voltage ripple std 24h (1), rates of change (4 cols × 2 = 8), fleet z-scores (4), interaction features (6). No automated feature selection (RFE, LASSO, ablation). | Interpretability, robustness | With synthetic training data, automated selection risks overfitting to generator artifacts. Domain-grouped selection ensures every feature has a stated physical or operational justification. `energy_price_kwh` is a known exception — included for controller context but lacks health-detection justification. Hardware health sensors (6) are raw telemetry passthroughs providing early warning for fan bearing wear, PSU capacitor aging, solder fatigue, and dust fouling. 7-day rolling windows (5) provide multi-day baselines for gradual degradation invisible in 24h windows (`notes_mining_data.md` line 42). Hardware diagnostics (2): `voltage_ripple_std_24h` captures PSU capacitor aging (variance precedes mean shift); `chip_dropout_ratio` normalizes chip health across models. | Recursive Feature Elimination, SHAP-based selection, or L1-regularized pre-filter — appropriate once real MOS telemetry is available for validation | SR-AD-01  |


---

## 5. Traceability Matrix


| UR    | SR       | DR                              | Code module                                                        | Test                                                                                   |
| ----- | -------- | ------------------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| UR-01 | SR-AD-01 | DR-REP-01, DR-SAF-01, DR-CAL-09 | `tasks/train_model.py:38-84`, `tasks/score.py`                     | `test_classification_f1`, `test_risk_score_range`                                      |
| UR-01 | SR-PA-01 | DR-REP-02, DR-CAL-01            | `tasks/train_model.py` (Phase 5), `tasks/score.py`                 | `test_regression_model_exists`, `test_model_registry_valid`                            |
| UR-01 | SR-PA-02 | DR-INT-04                       | `tasks/trend_analysis.py`                                          | `test_step_change_detect`, `test_gradual_drift`, `test_stationary_no_alarm`            |
| UR-01 | SR-PA-03 | DR-INT-04                       | `tasks/trend_analysis.py`                                          | `test_exact_crossing_time`, `test_moving_away`, `test_already_below`                   |
| UR-01 | SR-PA-04 | DR-CAL-04                       | `tasks/trend_analysis.py`                                          | `test_falling_fast`, `test_declining`, `test_stable`, `test_recovering`                |
| UR-01 | SR-PA-05 | DR-REP-01                       | `tasks/score.py`                                                   | `test_risk_scores_schema`, `test_risk_score_range`                                     |
| UR-02 | SR-AD-02 | DR-INT-05                       | `tasks/train_model.py` (per-type classifiers)                      | `test_classification_f1`                                                               |
| UR-02 | SR-AD-04 | DR-INT-01, DR-INT-02            | `tasks/kpi.py`                                                     | `test_kpi_schema`, `test_te_score_range`                                               |
| UR-03 | SR-AR-01 | DR-POR-01                       | `tasks/optimize.py`, `safeclaw/src/meta-tool.ts`                   | `test_actions_schema`, `test_flagged_devices_get_actions`                              |
| UR-04 | SR-SC-01 | DR-SAF-02                       | `tasks/optimize.py`                                                | `test_optimize_without_trends`, `test_temperature_range`                               |
| UR-04 | SR-SC-02 | DR-SAF-03                       | `tasks/optimize.py`                                                | `test_optimize_without_trends`                                                         |
| UR-04 | SR-SC-03 | DR-POR-05                       | `tasks/optimize.py`                                                | `test_optimize_without_trends`                                                         |
| UR-04 | SR-SC-04 | DR-SAF-02, DR-SAF-03            | `tasks/optimize.py`                                                | `test_optimize_without_trends`                                                         |
| UR-04 | SR-SC-06 | DR-SAF-08                       | `tasks/optimize.py`, `tasks/control_action.py`                     | `test_maintenance_max_offline_constraint`                                              |
| UR-04 | SR-SC-07 | DR-SAF-06, DR-SAF-07            | `tasks/control_action.py`                                          | `test_underclock_accepted`, `test_underclock_rejected_below_minimum`                   |
| UR-04 | SR-SC-08 | DR-SAF-08                       | `tasks/control_action.py`                                          | `test_maintenance_max_offline_constraint`                                              |
| UR-04 | SR-SC-09 | DR-SAF-04                       | `tasks/optimize.py`                                                | `test_scenario_recovering_no_deescalation`                                             |
| UR-04 | SR-AR-04 | DR-SAF-05                       | `safeclaw/catalog/default.json`                                    | `test_shutdown_always_proceeds`                                                        |
| UR-05 | SR-AD-03 | DR-INT-01                       | `tasks/kpi.py`                                                     | `test_te_score_range`, `test_efficiency_plausible`                                     |
| UR-05 | SR-RO-01 | —                               | `tasks/report.py`                                                  | `test_report_output`, `test_report_without_model_metrics`                              |
| UR-05 | SR-RO-03 | —                               | `tasks/fleet_status.py`                                            | `test_fleet_summary`, `test_device_detail`, `test_tier_breakdown`, `test_risk_ranking` |
| UR-06 | SR-AR-02 | DR-AUD-01, DR-AUD-02            | `safeclaw/src/approval-handler.ts`, `tasks/control_action.py`      | SafeClaw E2E (`safeclaw/tests/test_api_e2e.py`)                                        |
| UR-07 | SR-AR-03 | DR-AUD-01                       | `safeclaw/src/index.ts` (`/sc-approve`, `/sc-policies`)            | SafeClaw E2E                                                                           |
| UR-08 | SR-AD-04 | DR-INT-01, DR-INT-02, DR-INT-03 | `tasks/kpi.py`                                                     | `test_kpi_schema`, `test_te_score_range`, `test_efficiency_plausible`                  |
| UR-08 | SR-AD-05 | DR-INT-03                       | `tasks/kpi.py`                                                     | `test_kpi_schema`                                                                      |
| UR-09 | SR-CO-01 | DR-POR-02, DR-POR-03, DR-AUD-03 | `workflows/fleet_intelligence.py`, `scripts/orchestrate_*.py`      | `test_ingest_outputs` through `test_report_output`                                     |
| UR-09 | SR-CO-02 | DR-POR-02                       | `workflows/fleet_simulation.py`, `scripts/orchestrate_simulation.py` | (Validance E2E)                                                                        |
| UR-10 | SR-DP-03 | —                               | `tasks/features.py`                                                | `test_features_schema`, `test_device_count_consistent`                                 |
| UR-10 | SR-CO-03 | DR-CAL-05, DR-CAL-08            | `scripts/physics_engine.py`, `scripts/generate_training_corpus.py` | `test_ingest_schema`, `test_row_count_preserved`                                       |
| UR-09 | SR-CO-04 | DR-CAL-07                       | `tasks/retrain_monitor.py`                                         | —                                                                                      |


---

## 6. Out of Scope / Future

Items acknowledged but not delivered in this assignment iteration:


| Item                           | Status | Notes                                                                                                                                                                   |
| ------------------------------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Real fleet data validation** | Future | System designed and tested against synthetic physics-based data. Validation against live MOS telemetry is a deployment milestone, not a design gap.                     |
| **Streaming inference**        | Future | Current pipeline is batch-oriented (24h scoring window). Streaming would require incremental feature computation and online model serving.                              |
| **Incremental model updates**  | Future | Current approach is full retraining. Online learning or fine-tuning on recent batches would reduce retraining cost.                                                     |
| **Multi-site orchestration**   | Future | Current scope is single-site fleet. Multi-site would require site-aware ambient normalization and cross-site redundancy policies.                                       |
| **Direct MOS RPC execution**   | Future | Control actions generate MOS command payloads but do not execute them via MOS API. Execution requires MOS API credentials and network access to miners.                 |
| **Per-device V/f calibration** | Future | V/f scaling exponent (0.6) is a global estimate. Per-device calibration from firmware V/f tables would improve accuracy.                                                |
| **Adaptive threshold tuning**  | Future | Classifier threshold (0.3) and tier thresholds are static. Adaptive tuning based on observed false positive/negative rates in production would improve operational fit. |
| **Automatic de-escalation**    | Future | Tier escalation is one-directional by design (DR-SAF-04). Controlled de-escalation with hysteresis is a candidate enhancement.                                          |

---

## 7. Validation & Verification

The validation report ([`tests/validation-report.html`](../tests/validation-report.html)) verifies 36 system requirements (SR-*) against the implemented pipeline. It is generated by `scripts/generate_validation_report.py`, which runs the full pipeline on the multi-scenario training corpus and checks each requirement programmatically.

| Scope | Method | Coverage |
|-------|--------|----------|
| Data pipeline (SR-DAT-*) | Schema checks, row counts, dedup verification | 8 requirements |
| Feature engineering (SR-CAL-*) | Feature count, rolling window validation, z-score ranges | 9 requirements |
| Model performance (SR-MOD-*) | Precision, recall, F1 at device and row level | 6 requirements |
| Safety & control (SR-SAF-*) | Tier classification, hard limits, override logic | 7 requirements |
| Portability (SR-POR-*) | API interface, container isolation, artifact traceability | 6 requirements |

The unit and integration test suite (`tests/`, 76 tests) complements the validation report with regression coverage. See the [README](../README.md#run-tests) for details.
