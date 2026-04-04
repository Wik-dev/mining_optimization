"""
MDK Fleet Intelligence Workflow
===============================
9-task DAG for AI-driven mining optimization and predictive maintenance.

Two registered workflows:
  - mdk.fleet_intelligence          (training) — full 9-task DAG including model training
  - mdk.fleet_intelligence.inference (inference) — 8-task DAG, skips training,
    loads a pre-trained model from ${model_path} parameter

Training DAG:
    [1] ingest
     │
    [2] features
     │
    [3] kpi
     │
    [4a] train ──────────┐
                         │
                    [4b] score
                         │
                    [5a] trends ← rolling window + regime detection
                         │
                    [5b] cost projection ← consumes trend data
                         │
                    [5c] optimize ← trend-aware controller
                         │
                    [6] report ← also reads kpi, train, trends outputs

Inference DAG (same but no train_anomaly_model):
    [1] ingest → [2] features → [3] kpi → [4b] score → [5a] trends →
    [5b] cost → [5c] optimize → [6] report

Author: Wiktor (MDK assignment, April 2026)
"""

from validance.sdk import Task, Workflow

TASK_IMAGE = "autoregistry.azurecr.io/mdk-fleet-intelligence:latest"


# ── Shared task definitions ──────────────────────────────────────────────────
# Extracted to avoid duplication between training and inference workflows.

def _ingest_task() -> Task:
    return Task(
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


def _features_task() -> Task:
    return Task(
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


def _kpi_task() -> Task:
    return Task(
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


def _trends_task() -> Task:
    """Phase 3: per-device trend vectors, CUSUM regime detection,
    projected threshold crossings. Transforms controller from reactive
    to predictive."""
    return Task(
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


def _cost_task() -> Task:
    """Phase 4: 6 actions × 3 horizons per device via Weibull failure model,
    BTC revenue, time-of-use energy, maintenance costs. Trend data informs
    failure rate adjustments via slope-based Weibull shape modulation."""
    return Task(
        name="project_costs",
        command="python /app/tasks/cost_projection.py",
        docker_image=TASK_IMAGE,
        inputs={
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "cost_model.json": "${cost_model_path}",
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "trend_analysis.json": "@analyze_trends:trend_analysis",
        },
        output_files={
            "cost_projections": "cost_projections.json",
        },
        output_vars={
            "fleet_hourly_profit_usd": "float",
            "devices_with_negative_profit": "int",
            "avg_horizon_24h_net_usd": "float",
        },
        depends_on=["analyze_trends"],
        timeout=300,
    )


def _optimize_task() -> Task:
    return Task(
        name="optimize_fleet",
        command="python /app/tasks/optimize.py",
        docker_image=TASK_IMAGE,
        inputs={
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "cost_projections.json": "@project_costs:cost_projections",
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
        depends_on=["project_costs"],
        timeout=300,
    )


def _report_task(has_model_metrics: bool = True) -> Task:
    """Generate report task. In inference mode, model_metrics.json comes from
    a parameter instead of the training task (and report.py handles its absence)."""
    inputs = {
        "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
        "fleet_risk_scores.json": "@score_fleet:risk_scores",
        "fleet_actions.json": "@optimize_fleet:fleet_actions",
        "fleet_metadata.json": "@ingest_telemetry:metadata",
        "cost_projections.json": "@project_costs:cost_projections",
        "trend_analysis.json": "@analyze_trends:trend_analysis",
    }
    if has_model_metrics:
        # Training mode: get metrics from the training task
        inputs["model_metrics.json"] = "@train_anomaly_model:model_metrics"
    else:
        # Inference mode: metrics from parameter (optional — report.py handles missing)
        inputs["model_metrics.json"] = "${model_metrics_path}"

    return Task(
        name="generate_report",
        command="python /app/tasks/report.py",
        docker_image=TASK_IMAGE,
        inputs=inputs,
        output_files={
            "dashboard": "report.html",
        },
        depends_on=["optimize_fleet"],
        timeout=600,
    )


# ── Training workflow (full DAG) ─────────────────────────────────────────────

def create_training_workflow() -> Workflow:
    """Full 9-task DAG including model training (offline/batch path)."""
    wf = Workflow("mdk.fleet_intelligence")

    ingest = _ingest_task()
    features = _features_task()
    kpi = _kpi_task()

    # Task 4a: Train anomaly model + regression (offline)
    # Trains both the binary classifier (anomaly detection) and multi-horizon
    # quantile regressors (Phase 5: TE_score prediction at t+1h/6h/24h/7d).
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
            "model_accuracy": "float",
            "model_f1": "float",
            "train_samples": "int",
            "test_samples": "int",
            "regression_rmse_1h": "float",
            "regression_rmse_24h": "float",
            "calibration_80_avg": "float",
            "model_version": "int",
        },
        depends_on=["compute_true_efficiency"],
        timeout=900,
    )

    # Task 4b: Score fleet — depends on training to get the model artifact
    score = Task(
        name="score_fleet",
        command="python /app/tasks/score.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "anomaly_model.joblib": "@train_anomaly_model:model_artifact",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
            "regression_model.joblib": "@train_anomaly_model:regression_artifact",
            "model_registry.json": "@train_anomaly_model:model_registry",
        },
        output_files={
            "risk_scores": "fleet_risk_scores.json",
        },
        output_vars={
            "flagged_devices": "int",
            "scoring_window_hours": "int",
        },
        depends_on=["train_anomaly_model"],
        timeout=300,
    )

    trends = _trends_task()
    cost = _cost_task()
    optimize = _optimize_task()
    report = _report_task(has_model_metrics=True)

    for t in [ingest, features, kpi, train, score, trends, cost, optimize, report]:
        wf.add_task(t)

    return wf


# ── Inference workflow (skip training) ───────────────────────────────────────

def create_inference_workflow() -> Workflow:
    """8-task DAG for inference mode — uses a pre-trained model.

    The model artifact is provided via the ${model_path} parameter instead of
    being produced by a training task. This enables the continuous simulation
    loop (Phase 2) to run inference cycles without retraining.

    Required parameters:
      - telemetry_csv_path, metadata_json_path (same as training)
      - model_path: path to pre-trained anomaly_model.joblib
      - model_metrics_path: path to model_metrics.json (optional — report
        handles its absence gracefully)
      - cost_model_path: cost model JSON
    """
    wf = Workflow("mdk.fleet_intelligence.inference")

    ingest = _ingest_task()
    features = _features_task()
    kpi = _kpi_task()

    # Task 4b: Score fleet — loads pre-trained model from ${model_path} parameter.
    # --model-path tells score.py where to find the classifier artifact.
    score = Task(
        name="score_fleet",
        command="python /app/tasks/score.py --model-path anomaly_model.joblib",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "anomaly_model.joblib": "${model_path}",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "risk_scores": "fleet_risk_scores.json",
        },
        output_vars={
            "flagged_devices": "int",
            "scoring_window_hours": "int",
        },
        depends_on=["compute_true_efficiency"],
        timeout=300,
    )

    trends = _trends_task()
    cost = _cost_task()
    optimize = _optimize_task()
    report = _report_task(has_model_metrics=False)

    for t in [ingest, features, kpi, score, trends, cost, optimize, report]:
        wf.add_task(t)

    return wf


# ── WORKFLOWS dict for register_workflow() discovery ─────────────────────────

WORKFLOWS = {
    "training": create_training_workflow,
    "inference": create_inference_workflow,
}


def create_workflow() -> Workflow:
    """Default entry point for workflow discovery. Returns the training workflow
    for backward compatibility."""
    return create_training_workflow()
