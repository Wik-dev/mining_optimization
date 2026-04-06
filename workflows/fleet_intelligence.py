"""
MDK Fleet Workflows — Composable Single-Concern Pipelines
===========================================================
Five composable workflows chained via ``continue_from`` (Pattern 1 from
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

Orchestration scripts chain these:
  Training:  generate_corpus → pre_processing → train
  Inference: pre_processing → score → analyze

Author: Wiktor (MDK assignment, April 2026)
"""

from validance.sdk import Task, Workflow

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

    For training path: model artifact comes from continue_from train workflow.
    For inference path: model artifact comes from ${model_path} parameter.

    Outputs: fleet_risk_scores.json.
    """
    wf = Workflow("mdk.score")

    score = Task(
        name="score_fleet",
        command="python /app/tasks/score.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "anomaly_model.joblib": "${model_path}",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "regression_model.joblib": "${regression_model_path}",
            "model_registry.json": "${model_registry_path}",
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
            "model_metrics.json": "${model_metrics_path}",
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
        command="python /app/scripts/generate_training_corpus.py --all",
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


# ── WORKFLOWS dict for register_workflow() discovery ─────────────────────────

WORKFLOWS = {
    "pre_processing": create_pre_processing_workflow,
    "train": create_train_workflow,
    "score": create_score_workflow,
    "analyze": create_analyze_workflow,
    "generate_corpus": create_corpus_workflow,
}


def create_workflow() -> Workflow:
    """Default entry point for workflow discovery. Returns pre_processing
    for backward compatibility."""
    return create_pre_processing_workflow()
