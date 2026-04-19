"""Query the Validance API for the latest mdk.score pipeline run.

Returns session_hash, input_files refs, and cycle info. Read-only,
no workspace needed — the container calls the Validance REST API directly.

Uses stdlib only (urllib.request). Runs in the fleet-control image.

API flow:
  1. GET /api/runs?workflow_name=mdk.score&status=SUCCESS&limit=1
     → latest score run (workflow_hash, session_hash, parameters)
  2. GET /api/variables/{score_hash}
     → score_fleet:risk_scores file ref
  3. parameters.continued_from → pp_hash
     → ingest_telemetry:metadata file ref (from pre_processing run)
"""

import json
import os
import sys
import urllib.request
import urllib.error


def api_get(base_url: str, path: str) -> dict:
    """GET a JSON endpoint. Raises on HTTP or network error."""
    url = f"{base_url}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(json.dumps({
            "status": "error",
            "error": f"HTTP {e.code} from {path}",
            "detail": body[:500],
        }))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({
            "status": "error",
            "error": f"Cannot reach Validance API at {base_url}: {e.reason}",
        }))
        sys.exit(1)


def main():
    base_url = os.environ.get("VALIDANCE_API_URL", "http://host.docker.internal:8000")
    base_url = base_url.rstrip("/")

    # Optional session_hash filter from VALIDANCE_PARAMS
    params_raw = os.environ.get("VALIDANCE_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        params = {}
    filter_session = params.get("session_hash", "")

    # 1. Get latest successful mdk.score run
    query = "/api/runs?workflow_name=mdk.score&status=SUCCESS&limit=1"
    if filter_session:
        query += f"&session_hash={urllib.request.quote(filter_session)}"

    data = api_get(base_url, query)
    runs = data.get("runs", [])
    if not runs:
        print(json.dumps({
            "status": "no_runs",
            "message": "No successful mdk.score runs found",
        }))
        return

    run = runs[0]
    score_hash = run["workflow_hash"]
    session_hash = run.get("session_hash", "")
    run_params = run.get("parameters", {})

    # Extract cycle info (set by orchestrator in run parameters or task outputs)
    cycle = run_params.get("cycle", "")
    total_cycles = run_params.get("total_cycles", "")

    # 2. Get score run variables → risk_scores file ref
    score_vars = api_get(base_url, f"/api/variables/{score_hash}")
    risk_scores_ref = None
    for v in score_vars.get("variables", []):
        if v["task_name"] == "score_fleet" and v["variable_name"] == "risk_scores":
            # Build @hash.task:var reference format
            risk_scores_ref = f"@{score_hash}.score_fleet:risk_scores"
            break

    # 3. Get pre_processing hash via continue_from chain
    pp_hash = run_params.get("continued_from", "")
    metadata_ref = None
    if pp_hash:
        pp_vars = api_get(base_url, f"/api/variables/{pp_hash}")
        for v in pp_vars.get("variables", []):
            if v["task_name"] == "ingest_telemetry" and v["variable_name"] == "metadata":
                metadata_ref = f"@{pp_hash}.ingest_telemetry:metadata"
                break

    # Build input_files map (same format the orchestrator notification uses)
    input_files = {}
    if risk_scores_ref:
        input_files["fleet_risk_scores.json"] = risk_scores_ref
    if metadata_ref:
        input_files["fleet_metadata.json"] = metadata_ref

    result = {
        "status": "ok",
        "session_hash": session_hash,
        "cycle": cycle,
        "total_cycles": total_cycles,
        "input_files": input_files,
        "workflow_hash": score_hash,
        "completed_at": run.get("end_time", ""),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
