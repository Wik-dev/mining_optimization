"""
MDK Fleet Workflows — Composable Single-Concern Pipelines
===========================================================
Seven composable workflows chained via ``continue_from`` (Pattern 1 from
orchestration-patterns.md).

Architecture:
  - **ML detection layer** (pre_processing → train/score → analyze): deterministic
    anomaly detection, tier classification, safety overrides.
  - **AI reasoning layer** (SafeClaw): reads ML output + market data + operator KB,
    proposes specific MOS commands with rationale, goes through approval gate.
  - **Governance layer** (Validance): every action traceable via content-addressed
    execution chain.

Registered workflows:
  - mdk.pre_processing   — 3-task shared prefix (ingest → features → kpi)
  - mdk.train            — 1-task model training (continue_from pre_processing)
  - mdk.score            — 1-task fleet scoring (continue_from pre_processing)
  - mdk.analyze          — 3-task analysis (trends → optimize → report,
                           continue_from score)
  - mdk.generate_corpus  — 1-task synthetic data generation
  - mdk.generate_batch   — 1-task simulation batch generation (stateful)
  - mdk.fleet_simulation — 1-task Pattern 5a wrapper (triggers growing-window
                           inference loop from inside container, UI-triggerable)

Orchestration scripts chain these:
  Training:   generate_corpus → pre_processing → train
  Inference:  pre_processing → score → analyze
  Simulation: mdk.fleet_simulation triggers:
              generate_batch(full) → [pre_processing(cutoff) → score → analyze] × N cycles

Author: Wiktor (MDK assignment, April 2026)
"""

from validance import Task, Workflow
from workflows.fleet_simulation import create_workflow as create_fleet_simulation_workflow

TASK_IMAGE = "autoregistry.azurecr.io/mdk-fleet-intelligence:latest"


# ── Pre-processing workflow (shared prefix) ──────────────────────────────────

def create_pre_processing_workflow() -> Workflow:
    """3-task shared prefix: ingest → features → kpi.

    Outputs: telemetry.parquet, features.parquet, kpi_timeseries.parquet.
    Used by both training and inference paths.
    """
    wf = Workflow("mdk.pre_processing")

    ingest = Task(
        name="ingest_telemetry",
        command="python /app/tasks/ingest.py",
        docker_image=TASK_IMAGE,
        inputs={
            "fleet_telemetry.csv": "${telemetry_csv_path}",
            "fleet_metadata.json": "${metadata_json_path}",
        },
        output_files={
            "telemetry_parquet": "telemetry.parquet",
            "metadata": "fleet_metadata.json",
        },
        output_vars={
            "row_count": "int",
            "device_count": "int",
            "time_span_days": "float",
        },
        timeout=300,
    )

    features = Task(
        name="engineer_features",
        command="python /app/tasks/features.py",
        docker_image=TASK_IMAGE,
        inputs={
            "telemetry.parquet": "@ingest_telemetry:telemetry_parquet",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "feature_matrix": "features.parquet",
        },
        output_vars={
            "feature_count": "int",
            "sample_count": "int",
        },
        depends_on=["ingest_telemetry"],
        timeout=600,
    )

    kpi = Task(
        name="compute_true_efficiency",
        command="python /app/tasks/kpi.py",
        docker_image=TASK_IMAGE,
        inputs={
            "features.parquet": "@engineer_features:feature_matrix",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "kpi_series": "kpi_timeseries.parquet",
        },
        output_vars={
            "mean_te": "float",
            "worst_device": "str",
            "worst_te_score": "float",
        },
        depends_on=["engineer_features"],
        timeout=600,
    )

    for t in [ingest, features, kpi]:
        wf.add_task(t)

    return wf


# ── Training workflow ────────────────────────────────────────────────────────

def create_train_workflow() -> Workflow:
    """1-task model training. Chain with continue_from pre_processing.

    Trains both the binary classifier (anomaly detection) and multi-horizon
    quantile regressors (TE_score prediction at t+1h/6h/24h/7d).

    Outputs: anomaly_model.joblib, regression_model_v*.joblib,
             model_metrics.json, model_registry.json.
    """
    wf = Workflow("mdk.train")

    train = Task(
        name="train_anomaly_model",
        command="python /app/tasks/train_model.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "model_artifact": "anomaly_model.joblib",
            "model_metrics": "model_metrics.json",
            "regression_artifact": "regression_model_v*.joblib",
            "model_registry": "model_registry.json",
        },
        output_vars={
            "train_samples": "int",
            "anomaly_rate": "float",
            "model_version": "int",
        },
        # Full corpus (1.6M+ rows, 57 devices, 12 quantile regressors) needs ~20 min.
        # Single-scenario runs complete in <1 min.
        timeout=3600,
    )

    wf.add_task(train)
    return wf


# ── Scoring workflow ─────────────────────────────────────────────────────────

def create_score_workflow() -> Workflow:
    """1-task fleet scoring. Chain with continue_from pre_processing.

    Model artifacts resolve via deep context: the training workflow hash must
    be in the continuation chain (e.g., generate_batch → pre_processing → score
    all chained from training via continue_from). This preserves provenance —
    the exact training run is always traceable.

    Outputs: fleet_risk_scores.json.
    """
    wf = Workflow("mdk.score")

    score = Task(
        name="score_fleet",
        command="python /app/tasks/score.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            # Model artifacts from training workflow — resolved via deep context.
            # The training hash must be in the continuation chain (recursive walk).
            "anomaly_model.joblib": "@train_anomaly_model:model_artifact",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            # regression_model and model_registry are optional — score.py
            # handles their absence gracefully (classifier-only fallback).
            # They are NOT declared as inputs because training may not produce
            # regression models (depends on dataset characteristics).
        },
        output_files={
            "risk_scores": "fleet_risk_scores.json",
        },
        output_vars={
            "flagged_devices": "int",
            "scoring_window_hours": "int",
        },
        timeout=300,
    )

    wf.add_task(score)
    return wf


# ── Analysis workflow ────────────────────────────────────────────────────────

def create_analyze_workflow() -> Workflow:
    """3-task analysis: trends → optimize (tier-only) → report.

    Chain with continue_from score. Trends provide context (CUSUM, slope,
    projections). Optimize does tier classification + safety overrides only.
    Report visualizes everything.

    Outputs: trend_analysis.json, fleet_actions.json, report.html.
    """
    wf = Workflow("mdk.analyze")

    trends = Task(
        name="analyze_trends",
        command="python /app/tasks/trend_analysis.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
        },
        output_files={
            "trend_analysis": "trend_analysis.json",
        },
        output_vars={
            "devices_with_regime_change": "int",
        },
        depends_on=["score_fleet"],
        timeout=300,
    )

    optimize = Task(
        name="optimize_fleet",
        command="python /app/tasks/optimize.py",
        docker_image=TASK_IMAGE,
        inputs={
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "trend_analysis.json": "@analyze_trends:trend_analysis",
        },
        output_files={
            "fleet_actions": "fleet_actions.json",
        },
        output_vars={
            "actions_issued": "int",
            "devices_underclocked": "int",
            "devices_inspected": "int",
        },
        depends_on=["analyze_trends"],
        timeout=300,
    )

    report = Task(
        name="generate_report",
        command="python /app/tasks/report.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "fleet_actions.json": "@optimize_fleet:fleet_actions",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "trend_analysis.json": "@analyze_trends:trend_analysis",
            # Model metrics from training — resolved via deep context chain.
            "model_metrics.json": "@train_anomaly_model:model_metrics",
        },
        output_files={
            "dashboard": "report.html",
        },
        depends_on=["optimize_fleet"],
        timeout=600,
    )

    for t in [trends, optimize, report]:
        wf.add_task(t)

    return wf


# ── Corpus generation workflow ───────────────────────────────────────────────

def create_corpus_workflow() -> Workflow:
    """1-task synthetic data generation.

    Wraps generate_training_corpus.py --all. Produces a multi-scenario training
    corpus with ground-truth labels.

    Outputs: training_telemetry.csv, training_metadata.json.
    """
    wf = Workflow("mdk.generate_corpus")

    generate = Task(
        name="generate_training_data",
        command="python /app/scripts/generate_training_corpus.py --all --output .",
        docker_image=TASK_IMAGE,
        inputs={},
        output_files={
            "telemetry_csv": "training_telemetry.csv",
            "metadata_json": "training_metadata.json",
        },
        output_vars={
            "row_count": "int",
            "device_count": "int",
            "scenario_count": "int",
        },
        timeout=1200,
    )

    wf.add_task(generate)
    return wf


# ── Batch generation workflow ────────────────────────────────────────────────

def create_generate_batch_workflow() -> Workflow:
    """1-task batch generation. Used by orchestrate_simulation.py (Pattern 1).

    Generates a single interval of simulated telemetry via SimulationEngine.
    State continuity across cycles: the orchestrator passes sim_state_path
    (a host-side URI from get_file_url()) so the engine resumes where it
    left off. First cycle omits sim_state_path for a fresh start.

    Outputs: batch_telemetry.csv, batch_metadata.json, sim_state.json.
    """
    wf = Workflow("mdk.generate_batch")

    # Single task: generate one batch of telemetry.
    # interval_minutes is passed as a trigger parameter → CTX_INTERVAL_MINUTES
    # (ADR-005 §2.3.1). The task reads it from the environment.
    batch = Task(
        name="generate_batch",
        command="python /app/tasks/generate_batch.py",
        docker_image=TASK_IMAGE,
        inputs={
            # scenario.json is always required
            "scenario.json": "${scenario_path}",
            # sim_state.json is optional — absent on first cycle.
            # The orchestrator passes sim_state_path only for cycles 1+.
            # When the parameter is empty/missing, the engine starts fresh.
            "sim_state.json": "${sim_state_path}",
        },
        output_files={
            "telemetry": "batch_telemetry.csv",
            "metadata": "batch_metadata.json",
            "state": "sim_state.json",
        },
        output_vars={
            "sim_timestamp": "str",
            "tick_cursor": "int",
            "batch_index": "int",
        },
        timeout=300,
    )

    wf.add_task(batch)
    return wf


# ── WORKFLOWS dict for register_workflow() discovery ─────────────────────────

WORKFLOWS = {
    "pre_processing": create_pre_processing_workflow,
    "train": create_train_workflow,
    "score": create_score_workflow,
    "analyze": create_analyze_workflow,
    "generate_corpus": create_corpus_workflow,
    "generate_batch": create_generate_batch_workflow,
    "fleet_simulation": create_fleet_simulation_workflow,
}


def create_workflow() -> Workflow:
    """Default entry point for workflow discovery. Returns pre_processing
    for backward compatibility."""
    return create_pre_processing_workflow()
