"""
Execution receipt generation for RAG domain-level audit trails.

Captures semantic provenance that the orchestrator cannot see:
step determinism labels, config, content-addressed hashes, model versions.
Timing and file-level hashes are orchestrator concerns — not duplicated here.
"""
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional


@dataclass
class StepReceipt:
    step_name: str
    step_type: str  # "deterministic" or "non_deterministic"
    input_hashes: Dict[str, str]
    output_hashes: Dict[str, str]
    config: Dict[str, Any]


HASHING_SPEC = {
    "algo": "sha256",
    "encoding": "utf-8",
    "truncation": "hex[:16]",
    "canonicalization": "json.dumps(sort_keys=True, separators=(',', ':'))",
}


@dataclass
class ExecutionReceipt:
    run_id: str
    workflow_name: str
    steps: List[StepReceipt]
    final_output_hash: Optional[str]
    payload: Optional[Dict[str, Any]] = None
    hashing: Optional[Dict[str, str]] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def create_receipt(
    run_id: str,
    workflow_name: str,
    steps: List[StepReceipt],
    payload: Optional[Dict[str, Any]] = None,
) -> ExecutionReceipt:
    """Create an execution receipt from completed steps.

    The final_output_hash is taken from the last step's first output hash,
    giving a single hash that represents the workflow's end product.
    """
    final_hash = None
    if steps and steps[-1].output_hashes:
        # Take the first output hash from the last step
        final_hash = next(iter(steps[-1].output_hashes.values()))

    return ExecutionReceipt(
        run_id=run_id,
        workflow_name=workflow_name,
        steps=steps,
        final_output_hash=final_hash,
        payload=payload,
        hashing=HASHING_SPEC,
    )
