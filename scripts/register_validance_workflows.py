#!/usr/bin/env python3
"""Register fleet intelligence workflows with the Validance engine via REST API.

Registers 7 composable workflows:
  - mdk.pre_processing    (3 tasks: ingest → features → kpi)
  - mdk.train             (1 task: train anomaly model)
  - mdk.score             (1 task: score fleet)
  - mdk.analyze           (3 tasks: trends → optimize → report)
  - mdk.generate_corpus   (1 task: synthetic data generation)
  - mdk.generate_batch    (1 task: simulation batch generation)
  - mdk.fleet_simulation  (1 task: Pattern 5a growing-window simulation wrapper)

Usage:
    python scripts/register_validance_workflows.py [--api-url http://localhost:8001]
"""

import argparse
import json
import sys
import os

import requests

# Add project root so we can import the workflow definitions
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows.fleet_intelligence import WORKFLOWS


def workflow_to_api_json(wf):
    """Convert an SDK Workflow object to the API JSON format."""
    tasks = []
    for task in wf.tasks.values():
        t = {
            "name": task.name,
            "command": task.command,
            "docker_image": task.docker_image,
            "inputs": dict(task.inputs),
            "output_files": dict(task.output_files),
            "output_vars": dict(task.output_vars),
            "depends_on": list(task.depends_on),
            "environment": dict(task.environment) if hasattr(task, 'environment') and task.environment else {},
            "timeout": task.timeout,
        }
        # Include v2 extension fields when non-default
        if getattr(task, 'persistent', False):
            t["persistent"] = True
        if getattr(task, 'gate', 'auto-approve') != 'auto-approve':
            t["gate"] = task.gate
        if getattr(task, 'secret_refs', None):
            t["secret_refs"] = list(task.secret_refs)
        tasks.append(t)
    return tasks


def register(api_url, wf, description):
    """POST workflow definition to the Validance API."""
    tasks = workflow_to_api_json(wf)
    payload = {
        "name": wf.name,
        "description": description,
        "tasks": tasks,
        "version": "1.0",
    }
    resp = requests.post(f"{api_url}/api/workflows", json=payload)
    resp.raise_for_status()
    result = resp.json()
    print(f"  {wf.name}: {result['action']} ({result['task_count']} tasks, hash={result['definition_hash'][:12]}...)")
    return result


def main():
    parser = argparse.ArgumentParser(description="Register MDK workflows with Validance")
    parser.add_argument("--api-url", default="http://localhost:8001",
                        help="Validance API base URL (default: http://localhost:8001)")
    args = parser.parse_args()

    print(f"Registering workflows against {args.api_url}...")

    # Verify API is reachable
    try:
        health = requests.get(f"{args.api_url}/api/health", timeout=5).json()
        print(f"  API status: {health['status']}")
    except Exception as e:
        print(f"  ERROR: Cannot reach API at {args.api_url}: {e}")
        sys.exit(1)

    # Register all composable workflows
    descriptions = {
        "pre_processing": "3-task shared prefix (ingest → features → kpi)",
        "train": "1-task model training (continue_from pre_processing)",
        "score": "1-task fleet scoring (continue_from pre_processing)",
        "analyze": "3-task analysis (trends → optimize → report, continue_from score)",
        "generate_corpus": "1-task synthetic data generation",
        "generate_batch": "1-task simulation batch generation (stateful)",
        "fleet_simulation": "1-task Pattern 5a growing-window simulation wrapper (UI-triggerable)",
    }
    for key, factory in WORKFLOWS.items():
        register(args.api_url, factory(), descriptions[key])

    # Verify
    resp = requests.get(f"{args.api_url}/api/workflows")
    workflows = resp.json()
    print(f"\nRegistered workflows ({len(workflows)}):")
    for w in workflows:
        name = w.get("name", w) if isinstance(w, dict) else w
        print(f"  - {name}")


if __name__ == "__main__":
    main()
