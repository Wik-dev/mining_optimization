"""
MDK Fleet Intelligence Workflow
===============================
7-task DAG for AI-driven mining optimization and predictive maintenance.

Offline (batch) path:
    ingest_telemetry → engineer_features → compute_true_efficiency → train_anomaly_model

Online (per-interval) path:
    score_fleet → optimize_fleet → generate_report

DAG:
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
                    [5] optimize
                         │
                    [6] report ← also reads kpi, train, ingest outputs

Author: Wiktor (MDK assignment, April 2026)
"""

from validance.sdk import Task, Workflow

TASK_IMAGE = "python:3.11-slim"


def create_workflow() -> Workflow:
    """Entry point for workflow discovery."""

    wf = Workflow("mdk.fleet_intelligence")

    # ── Task 1: Ingest raw telemetry ─────────────────────────────────────
    ingest = Task(
        name="ingest_telemetry",
        command="python tasks/ingest.py",
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

    # ── Task 2: Engineer features ────────────────────────────────────────
    features = Task(
        name="engineer_features",
        command="python tasks/features.py",
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

    # ── Task 3: Compute True Efficiency KPI ──────────────────────────────
    kpi = Task(
        name="compute_true_efficiency",
        command="python tasks/kpi.py",
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

    # ── Task 4a: Train anomaly model (offline) ───────────────────────────
    train = Task(
        name="train_anomaly_model",
        command="python tasks/train_model.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "model_artifact": "anomaly_model.joblib",
            "model_metrics": "model_metrics.json",
        },
        output_vars={
            "model_accuracy": "float",
            "model_f1": "float",
            "train_samples": "int",
            "test_samples": "int",
        },
        depends_on=["compute_true_efficiency"],
        timeout=900,
    )

    # ── Task 4b: Score fleet (online inference simulation) ───────────────
    score = Task(
        name="score_fleet",
        command="python tasks/score.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "anomaly_model.joblib": "@train_anomaly_model:model_artifact",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
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

    # ── Task 5: Optimize fleet (controller) ──────────────────────────────
    optimize = Task(
        name="optimize_fleet",
        command="python tasks/optimize.py",
        docker_image=TASK_IMAGE,
        inputs={
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "fleet_actions": "fleet_actions.json",
        },
        output_vars={
            "actions_issued": "int",
            "devices_underclocked": "int",
            "devices_inspected": "int",
        },
        depends_on=["score_fleet"],
        timeout=300,
    )

    # ── Task 6: Generate report ──────────────────────────────────────────
    report = Task(
        name="generate_report",
        command="python tasks/report.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_risk_scores.json": "@score_fleet:risk_scores",
            "model_metrics.json": "@train_anomaly_model:model_metrics",
            "fleet_actions.json": "@optimize_fleet:fleet_actions",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "dashboard": "report.html",
        },
        depends_on=["optimize_fleet"],
        timeout=600,
    )

    for t in [ingest, features, kpi, train, score, optimize, report]:
        wf.add_task(t)

    return wf
