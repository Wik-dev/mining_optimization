"""
Full pipeline integration tests — verifies the 8-task pipeline produces correct
artifacts with valid schemas, value ranges, and cross-task consistency.

Uses the session-scoped pipeline_dir fixture from conftest.py (mini dataset,
5 devices, 14 days, run once per session).
"""

import json
import os
import sys

import joblib
import numpy as np
import pandas as pd
import pytest


# ─── Artifact Existence ──────────────────────────────────────────────────────

class TestArtifactExistence:
    """Verify all expected output files exist after the pipeline run."""

    def test_ingest_outputs(self, pipeline_dir):
        assert os.path.exists(os.path.join(pipeline_dir, "telemetry.parquet"))
        assert os.path.exists(os.path.join(pipeline_dir, "fleet_metadata.json"))

    def test_feature_outputs(self, pipeline_dir):
        assert os.path.exists(os.path.join(pipeline_dir, "features.parquet"))

    def test_kpi_outputs(self, pipeline_dir):
        assert os.path.exists(os.path.join(pipeline_dir, "kpi_timeseries.parquet"))

    def test_model_outputs(self, pipeline_dir):
        assert os.path.exists(os.path.join(pipeline_dir, "anomaly_model.joblib"))
        assert os.path.exists(os.path.join(pipeline_dir, "model_metrics.json"))
        assert os.path.exists(os.path.join(pipeline_dir, "model_registry.json"))

    def test_report_output(self, pipeline_dir):
        report_path = os.path.join(pipeline_dir, "report.html")
        assert os.path.exists(report_path)
        # Report should contain embedded charts — at least 10KB
        assert os.path.getsize(report_path) > 10_000


# ─── Schema Validation ───────────────────────────────────────────────────────

class TestSchemaValidation:
    """Verify column schemas match documented contracts."""

    EXPECTED_INGEST_COLUMNS = {
        "timestamp", "device_id", "model",
        "clock_ghz", "voltage_v", "hashrate_th",
        "power_w", "temperature_c", "cooling_power_w",
        "ambient_temp_c", "energy_price_kwh",
        "operating_mode", "efficiency_jth",
        "label_thermal_deg", "label_psu_instability",
        "label_hashrate_decay", "label_any_anomaly",
    }

    TELEMETRY_COLS = [
        "temperature_c", "power_w", "hashrate_th",
        "voltage_v", "cooling_power_w", "efficiency_jth",
    ]

    def test_ingest_schema(self, pipeline_artifacts):
        df = pipeline_artifacts["telemetry"]
        missing = self.EXPECTED_INGEST_COLUMNS - set(df.columns)
        assert not missing, f"Missing ingest columns: {missing}"

    def test_features_schema(self, pipeline_artifacts):
        df = pipeline_artifacts["features"]
        cols = set(df.columns)
        # Check rolling-window columns for each telemetry col
        for base in self.TELEMETRY_COLS:
            for suffix in ["_mean_1h", "_std_1h", "_mean_12h", "_mean_24h"]:
                expected = f"{base}{suffix}"
                assert expected in cols, f"Missing feature column: {expected}"

    def test_kpi_schema(self, pipeline_artifacts):
        df = pipeline_artifacts["kpi_timeseries"]
        required = {"te_base", "true_efficiency", "te_score", "voltage_penalty", "cooling_ratio"}
        missing = required - set(df.columns)
        assert not missing, f"Missing KPI columns: {missing}"

    def test_risk_scores_schema(self, pipeline_artifacts):
        scores = pipeline_artifacts["fleet_risk_scores"]
        assert "device_risks" in scores
        for device in scores["device_risks"]:
            assert "mean_risk" in device
            assert "max_risk" in device
            assert "flagged" in device
            assert "latest_snapshot" in device

    def test_actions_schema(self, pipeline_artifacts):
        actions = pipeline_artifacts["fleet_actions"]
        assert "actions" in actions
        for action in actions["actions"]:
            assert "tier" in action
            assert action["tier"] in ("CRITICAL", "WARNING", "DEGRADED", "HEALTHY")
            assert "commands" in action
            assert isinstance(action["commands"], list)
            for cmd in action["commands"]:
                assert "type" in cmd


# ─── Value Ranges ─────────────────────────────────────────────────────────────

class TestValueRanges:
    """Verify physical plausibility of computed values."""

    def test_temperature_range(self, pipeline_artifacts):
        df = pipeline_artifacts["telemetry"]
        temps = df["temperature_c"].dropna()
        # Northern site can have sub-zero ambient (hydro-cooled, 64.5°N)
        assert temps.min() > -30, f"Temperature below -30°C: {temps.min()}"
        assert temps.max() < 120, f"Temperature above 120°C: {temps.max()}"

    def test_hashrate_positive(self, pipeline_artifacts):
        df = pipeline_artifacts["telemetry"]
        running = df[df["operating_mode"] == "normal"]
        assert (running["hashrate_th"] > 0).all(), "RUNNING devices must have positive hashrate"

    def test_te_score_range(self, pipeline_artifacts):
        df = pipeline_artifacts["kpi_timeseries"]
        te = df["te_score"].dropna()
        assert te.min() > 0, f"te_score <= 0: {te.min()}"
        # TE score > 1 means device is more efficient than nominal (cold ambient, etc.)
        # Values up to ~4 are possible with favorable conditions
        assert te.max() < 10, f"te_score > 10: {te.max()}"

    def test_risk_score_range(self, pipeline_artifacts):
        scores = pipeline_artifacts["fleet_risk_scores"]
        for device in scores["device_risks"]:
            assert 0 <= device["mean_risk"] <= 1, (
                f"{device['device_id']}: mean_risk={device['mean_risk']} out of [0, 1]"
            )

    def test_efficiency_plausible(self, pipeline_artifacts):
        df = pipeline_artifacts["kpi_timeseries"]
        eff = df["true_efficiency"].dropna()
        # S21-HYD can achieve ~3.5 J/TH at optimal conditions
        assert eff.min() > 1, f"Efficiency below 1 J/TH: {eff.min()}"
        assert eff.max() < 200, f"Efficiency above 200 J/TH: {eff.max()}"

    def test_economic_margin_sign(self, pipeline_artifacts):
        df = pipeline_artifacts["telemetry"]
        if "economic_margin_usd" in df.columns:
            margins = df["economic_margin_usd"].dropna()
            # At least some devices should be profitable
            assert (margins > 0).any(), "No devices with positive economic margin"


# ─── Cross-Task Consistency ───────────────────────────────────────────────────

class TestCrossTaskConsistency:
    """Verify data flows correctly between tasks."""

    def test_device_count_consistent(self, pipeline_artifacts):
        telemetry_devices = set(pipeline_artifacts["telemetry"]["device_id"].unique())
        features_devices = set(pipeline_artifacts["features"]["device_id"].unique())
        kpi_devices = set(pipeline_artifacts["kpi_timeseries"]["device_id"].unique())
        risk_devices = {
            d["device_id"] for d in pipeline_artifacts["fleet_risk_scores"]["device_risks"]
        }
        action_devices = {
            a["device_id"] for a in pipeline_artifacts["fleet_actions"]["actions"]
        }

        # Telemetry → features → kpi should have the same devices
        assert telemetry_devices == features_devices, "Device mismatch: telemetry vs features"
        assert telemetry_devices == kpi_devices, "Device mismatch: telemetry vs kpi"
        # Scorer only scores active devices in the 24h scoring window;
        # devices that are idle/failed at end of simulation may be excluded.
        # Risk and action devices should be a subset of telemetry devices.
        assert risk_devices <= telemetry_devices, "risk_scores has devices not in telemetry"
        assert action_devices <= telemetry_devices, "actions has devices not in telemetry"
        # Risk devices and action devices should match each other
        assert risk_devices == action_devices, "Device mismatch: risk_scores vs actions"

    def test_row_count_preserved(self, pipeline_artifacts):
        """Ingest row count should match features sample count (no row loss)."""
        n_ingest = len(pipeline_artifacts["telemetry"])
        n_features = len(pipeline_artifacts["features"])
        assert n_ingest == n_features, (
            f"Row count mismatch: ingest={n_ingest}, features={n_features}"
        )

    def test_flagged_devices_get_actions(self, pipeline_artifacts):
        """Devices flagged in risk_scores should have non-HEALTHY tier in actions."""
        flagged_ids = {
            d["device_id"]
            for d in pipeline_artifacts["fleet_risk_scores"]["device_risks"]
            if d["flagged"]
        }
        action_tiers = {
            a["device_id"]: a["tier"]
            for a in pipeline_artifacts["fleet_actions"]["actions"]
        }
        for device_id in flagged_ids:
            tier = action_tiers.get(device_id)
            assert tier is not None, f"Flagged device {device_id} missing from actions"
            assert tier != "HEALTHY", (
                f"Flagged device {device_id} should not be HEALTHY, got {tier}"
            )



# ─── Model Quality ────────────────────────────────────────────────────────────

class TestModelQuality:
    """Verify trained model meets minimum quality bars."""

    def test_classification_f1(self, pipeline_artifacts):
        metrics = pipeline_artifacts["model_metrics"]
        f1 = metrics.get("f1_score", 0)
        assert f1 >= 0.5, f"F1 score {f1} below minimum 0.5 for mini dataset"

    def test_regression_model_exists(self, pipeline_dir):
        import glob
        pattern = os.path.join(pipeline_dir, "regression_model_v*.joblib")
        matches = glob.glob(pattern)
        assert len(matches) >= 1, "No regression model artifact found"

    def test_model_registry_valid(self, pipeline_artifacts, pipeline_dir):
        registry = pipeline_artifacts["model_registry"]
        assert "active_version" in registry, "model_registry.json missing active_version"
        active = registry["active_version"]
        artifact_path = os.path.join(pipeline_dir, f"regression_model_v{active}.joblib")
        assert os.path.exists(artifact_path), (
            f"Active version v{active} artifact not found at {artifact_path}"
        )


# ─── Inference Mode ───────────────────────────────────────────────────────────

class TestInferenceMode:
    """Run score.py with a pre-trained model (inference DAG, no train step)."""

    def test_inference_with_pretrained_model(self, pipeline_dir):
        """score.py runs with --model-path pointing to training output."""
        import shutil
        import tempfile

        inference_dir = tempfile.mkdtemp(prefix="inference_")
        try:
            # Copy required inputs (not train artifacts — just model + data)
            for f in ["kpi_timeseries.parquet", "fleet_metadata.json",
                       "anomaly_model.joblib", "model_registry.json"]:
                src = os.path.join(pipeline_dir, f)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(inference_dir, f))

            # Copy regression model
            import glob
            for reg in glob.glob(os.path.join(pipeline_dir, "regression_model_v*.joblib")):
                shutil.copy2(reg, os.path.join(inference_dir, os.path.basename(reg)))

            orig_dir = os.getcwd()
            orig_argv = sys.argv
            try:
                os.chdir(inference_dir)
                sys.argv = ["score"]
                from tasks.score import main as score_main
                score_main()
            finally:
                os.chdir(orig_dir)
                sys.argv = orig_argv

            # Verify output
            scores_path = os.path.join(inference_dir, "fleet_risk_scores.json")
            assert os.path.exists(scores_path)
            with open(scores_path) as f:
                scores = json.load(f)
            assert len(scores["device_risks"]) > 0
        finally:
            shutil.rmtree(inference_dir, ignore_errors=True)

    def test_inference_graceful_without_regression(self, pipeline_dir):
        """score.py works when regression model files are absent (classifier-only)."""
        import shutil
        import tempfile

        inference_dir = tempfile.mkdtemp(prefix="inference_noreg_")
        try:
            # Copy only classifier + data (no regression model)
            for f in ["kpi_timeseries.parquet", "fleet_metadata.json",
                       "anomaly_model.joblib"]:
                shutil.copy2(
                    os.path.join(pipeline_dir, f),
                    os.path.join(inference_dir, f),
                )

            orig_dir = os.getcwd()
            orig_argv = sys.argv
            try:
                os.chdir(inference_dir)
                sys.argv = ["score"]
                from tasks.score import main as score_main
                score_main()
            finally:
                os.chdir(orig_dir)
                sys.argv = orig_argv

            scores_path = os.path.join(inference_dir, "fleet_risk_scores.json")
            assert os.path.exists(scores_path)
            with open(scores_path) as f:
                scores = json.load(f)
            # Should produce scores, just without predictions
            assert len(scores["device_risks"]) > 0
            # No regression → no predictions key (or empty)
            assert "model_versions" not in scores
        finally:
            shutil.rmtree(inference_dir, ignore_errors=True)


# ─── Graceful Fallbacks ──────────────────────────────────────────────────────

class TestGracefulFallbacks:
    """Verify pipeline handles missing optional inputs."""

    def test_optimize_without_trends(self, pipeline_dir):
        """optimize.py runs when trend_analysis.json is absent (static tier-only mode)."""
        import shutil
        import tempfile

        fallback_dir = tempfile.mkdtemp(prefix="opt_no_trend_")
        try:
            for f in ["fleet_risk_scores.json", "fleet_metadata.json",
                       "kpi_timeseries.parquet"]:
                shutil.copy2(
                    os.path.join(pipeline_dir, f),
                    os.path.join(fallback_dir, f),
                )
            # Deliberately skip trend_analysis.json

            orig_dir = os.getcwd()
            orig_argv = sys.argv
            try:
                os.chdir(fallback_dir)
                sys.argv = ["optimize"]
                from tasks.optimize import main as optimize_main
                optimize_main()
            finally:
                os.chdir(orig_dir)
                sys.argv = orig_argv

            actions_path = os.path.join(fallback_dir, "fleet_actions.json")
            assert os.path.exists(actions_path)
            with open(actions_path) as f:
                data = json.load(f)
            # Should run tier-only controller
            assert "tier-only" in data["controller_version"] or "2.0" in data["controller_version"]
        finally:
            shutil.rmtree(fallback_dir, ignore_errors=True)

    def test_report_without_model_metrics(self, pipeline_dir):
        """report.py runs when model_metrics.json is absent (inference mode)."""
        import shutil
        import tempfile

        fallback_dir = tempfile.mkdtemp(prefix="report_no_metrics_")
        try:
            for f in ["kpi_timeseries.parquet", "fleet_risk_scores.json",
                       "fleet_actions.json", "fleet_metadata.json"]:
                shutil.copy2(
                    os.path.join(pipeline_dir, f),
                    os.path.join(fallback_dir, f),
                )
            # Deliberately skip model_metrics.json

            orig_dir = os.getcwd()
            orig_argv = sys.argv
            try:
                os.chdir(fallback_dir)
                sys.argv = ["report"]
                from tasks.report import main as report_main
                report_main()
            finally:
                os.chdir(orig_dir)
                sys.argv = orig_argv

            report_path = os.path.join(fallback_dir, "report.html")
            assert os.path.exists(report_path)
            assert os.path.getsize(report_path) > 5_000
        finally:
            shutil.rmtree(fallback_dir, ignore_errors=True)
