"""
MDK Fleet Intelligence Workflow
===============================
5-task DAG for AI-driven mining optimization and predictive maintenance.

DAG structure:
    ingest_telemetry
         │
    engineer_features
         │
    compute_true_efficiency
         │
    ┌────┴────┐
    │         │
  predict   (future: optimize)
  failures
    │
  generate_report

Author: Wiktor (MDK assignment, April 2026)
"""

from validance.sdk import Task, Workflow

TASK_IMAGE = "python:3.11-slim"


def create_workflow() -> Workflow:
    """Entry point for Validance workflow discovery."""

    wf = Workflow("mdk.fleet_intelligence")

    # ── Task 1: Ingest raw telemetry ─────────────────────────────────────
    # Reads CSV + metadata JSON, validates schema, converts to Parquet.
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
    # Rolling statistics, rate-of-change, cross-device normalization.
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
    # η_v, P_cooling_norm, TE, decomposition factors, TE_score per row.
    # See docs/true-efficiency-kpi.md for the full formulation.
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

    # ── Task 4: Predict failures ─────────────────────────────────────────
    # XGBoost on TE decomposition features + rolling stats.
    # Outputs per-device risk scores and flagged devices.
    predict = Task(
        name="predict_failures",
        command="python tasks/predict.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "predictions": "failure_predictions.json",
        },
        output_vars={
            "flagged_devices": "int",
            "model_accuracy": "float",
            "model_f1": "float",
        },
        depends_on=["compute_true_efficiency"],
        timeout=900,
    )

    # ── Task 5: Generate report ──────────────────────────────────────────
    # Consolidates KPI timeseries + failure predictions into HTML dashboard.
    report = Task(
        name="generate_report",
        command="python tasks/report.py",
        docker_image=TASK_IMAGE,
        inputs={
            "kpi_timeseries.parquet": "@compute_true_efficiency:kpi_series",
            "failure_predictions.json": "@predict_failures:predictions",
            "fleet_metadata.json": "@ingest_telemetry:metadata",
        },
        output_files={
            "dashboard": "report.html",
        },
        depends_on=["predict_failures"],
        timeout=600,
    )

    for t in [ingest, features, kpi, predict, report]:
        wf.add_task(t)

    return wf
