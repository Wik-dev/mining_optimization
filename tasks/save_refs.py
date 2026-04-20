"""Save latest pipeline refs to workspace for cross-session access.

Hook sessions (ephemeral, one per simulation cycle) write refs here.
The DM session (long-lived, user-facing) reads them back for manual queries.
This bridges the session isolation gap without sharing file locks.

Reads VALIDANCE_PARAMS (JSON) with session_hash, input_files, cycle, total_cycles.
Writes /workspace/latest_refs.json.
"""

import json
import os
import sys
from datetime import datetime, timezone


def main():
    params_raw = os.environ.get("VALIDANCE_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "error": "Invalid VALIDANCE_PARAMS JSON"}))
        sys.exit(1)

    session_hash = params.get("session_hash", "")
    input_files = params.get("input_files", {})
    cycle = params.get("cycle", "0")
    total_cycles = params.get("total_cycles", "0")

    if not session_hash:
        print(json.dumps({"status": "error", "error": "Missing required parameter: session_hash"}))
        sys.exit(1)

    payload = {
        "session_hash": session_hash,
        "input_files": input_files,
        "cycle": int(cycle),
        "total_cycles": int(total_cycles),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = "/workspace/latest_refs.json"
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)

    print(json.dumps({"status": "ok", "path": out_path, "cycle": payload["cycle"]}))


if __name__ == "__main__":
    main()
