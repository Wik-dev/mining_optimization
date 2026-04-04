"""
Phase 6 task tests — fleet_status and control_action.

These tasks run outside the DAG as agent-invoked catalog templates. They read
from a fleet data directory and write JSON to stdout. Tested via subprocess
with VALIDANCE_PARAMS env var.
"""

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_fleet_status(fleet_dir: str, params: dict) -> dict:
    """Run fleet_status.py with given VALIDANCE_PARAMS, return parsed JSON."""
    env = os.environ.copy()
    env["FLEET_DATA_DIR"] = fleet_dir
    env["VALIDANCE_PARAMS"] = json.dumps(params)

    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "tasks", "fleet_status.py")],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"fleet_status failed: {result.stderr}"
    return json.loads(result.stdout)


def run_control_action(fleet_dir: str, action: str, params: dict,
                       expect_exit: int = 0) -> dict:
    """Run control_action.py with given action and VALIDANCE_PARAMS."""
    env = os.environ.copy()
    env["FLEET_DATA_DIR"] = fleet_dir
    env["VALIDANCE_PARAMS"] = json.dumps(params)

    result = subprocess.run(
        [sys.executable, os.path.join(PROJECT_ROOT, "tasks", "control_action.py"),
         "--action", action],
        capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == expect_exit, (
        f"Expected exit {expect_exit}, got {result.returncode}. "
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return json.loads(result.stdout)


class TestFleetStatus:
    """Test fleet_status.py query types against pipeline artifacts."""

    def test_fleet_summary(self, pipeline_dir):
        result = run_fleet_status(pipeline_dir, {"query_type": "summary"})
        assert result["status"] == "ok"
        assert result["query_type"] == "summary"
        assert "fleet_size" in result
        assert result["fleet_size"] > 0
        assert "tier_counts" in result
        assert "flagged_count" in result
        assert isinstance(result["avg_te_score"], float)

    def test_device_detail(self, pipeline_artifacts, pipeline_dir):
        # Pick the first device from risk scores
        device_id = pipeline_artifacts["fleet_risk_scores"]["device_risks"][0]["device_id"]
        result = run_fleet_status(pipeline_dir, {
            "query_type": "device_detail",
            "device_id": device_id,
        })
        assert result["status"] == "ok"
        assert result["device_id"] == device_id
        assert "risk_assessment" in result
        assert "latest_snapshot" in result
        assert "controller" in result

    def test_tier_breakdown(self, pipeline_dir):
        result = run_fleet_status(pipeline_dir, {"query_type": "tier_breakdown"})
        assert result["status"] == "ok"
        assert "tiers" in result
        # At least one tier should have devices
        total = sum(len(devs) for devs in result["tiers"].values())
        assert total > 0

    def test_risk_ranking(self, pipeline_dir):
        result = run_fleet_status(pipeline_dir, {"query_type": "risk_ranking"})
        assert result["status"] == "ok"
        assert "devices" in result
        assert len(result["devices"]) > 0
        # Should be sorted by mean_risk descending
        risks = [d["mean_risk"] for d in result["devices"]]
        assert risks == sorted(risks, reverse=True)


class TestControlAction:
    """Test control_action.py actions and safety constraints."""

    def test_underclock_accepted(self, pipeline_artifacts, pipeline_dir):
        # Pick a device with higher risk for underclocking
        risks = pipeline_artifacts["fleet_risk_scores"]["device_risks"]
        device = max(risks, key=lambda d: d["mean_risk"])

        result = run_control_action(pipeline_dir, "underclock", {
            "device_id": device["device_id"],
            "target_pct": 80,
            "reason": "integration test",
        })
        assert result["status"] == "executed"
        assert result["action"] == "underclock"
        assert result["device_id"] == device["device_id"]
        assert "fleet_impact" in result

    def test_underclock_rejected_below_minimum(self, pipeline_artifacts, pipeline_dir):
        """Underclock below MIN_UNDERCLOCK_PCT (50%) should be rejected."""
        device_id = pipeline_artifacts["fleet_risk_scores"]["device_risks"][0]["device_id"]

        result = run_control_action(pipeline_dir, "underclock", {
            "device_id": device_id,
            "target_pct": 40,
            "reason": "test below min",
        }, expect_exit=1)
        assert result["status"] == "rejected"
        assert "50" in result["reason"]  # MIN_UNDERCLOCK_PCT referenced

    def test_shutdown_always_proceeds(self, pipeline_artifacts, pipeline_dir):
        """Shutdown always proceeds (human-approved policy ceiling)."""
        device_id = pipeline_artifacts["fleet_risk_scores"]["device_risks"][0]["device_id"]

        result = run_control_action(pipeline_dir, "shutdown", {
            "device_id": device_id,
            "reason": "integration test shutdown",
            "schedule_inspection": True,
        })
        assert result["status"] == "executed"
        assert result["action"] == "shutdown"
        assert "fleet_impact" in result
        assert "capacity_warning" in result["fleet_impact"]
