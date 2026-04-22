"""
Build an execution receipt from all previous task outputs.

Usage: python modules/rag/tasks/build_receipt.py <output_json> [<output_json> ...]

Reads each task's output JSON, extracts its manifest (step_type, semantic
hashes, config), and chains them into a single ExecutionReceipt.

This captures RAG domain-level provenance only — timing and file-level
hashes are orchestrator concerns and are not duplicated here.

Env vars:
    WORKFLOW_HASH: workflow hash from orchestrator (used as run_id)
    CTX_WORKFLOW_NAME or WORKFLOW_NAME: workflow name (fallback: "unknown")
"""
import hashlib
import json
import os
import sys

from modules.rag.receipt import StepReceipt, create_receipt
from modules.rag.tasks import load_rag_env

# Map output filename to (step_name, output_hash_key)
# output_hash_key is the manifest field that holds this step's primary hash
STEP_REGISTRY = {
    "document.json": "load_document",
    "documents.json": "load_documents",
    "chunks.json": "chunk_documents",
    "embeddings.json": "embed_chunks",
    "index.json": "build_index",
    "retrieval.json": "retrieve",
    "prompt.json": "assemble_prompt",
    "response.json": "generate",
}


def _extract_step(filepath: str, prev_output_hashes: dict) -> StepReceipt:
    """Extract a StepReceipt from a task output JSON."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    filename = os.path.basename(filepath)
    step_name = STEP_REGISTRY.get(filename, filename)
    manifest = data.get("manifest", {})

    # step_type from manifest
    step_type = manifest.get("step_type", "unknown")

    # output_hashes: all hash fields from manifest (exclude non-hash metadata)
    non_hash_keys = {"step_type", "is_deterministic", "embedding_count", "result_count", "chunk_count", "kb_id"}
    output_hashes = {k: v for k, v in manifest.items() if k not in non_hash_keys}

    # config: from the "config" field if present, plus relevant root/manifest-level fields
    config = dict(data.get("config", {}))
    if "top_k" in data:
        config["top_k"] = data["top_k"]
    if "model" in data:
        config["model"] = data["model"]
    if "kb_id" in manifest:
        config["kb_id"] = manifest["kb_id"]

    # input_hashes: the previous step's output hashes (semantic chain)
    input_hashes = dict(prev_output_hashes)

    return StepReceipt(
        step_name=step_name,
        step_type=step_type,
        input_hashes=input_hashes,
        output_hashes=output_hashes,
        config=config,
    )


def _extract_payload(input_files: list[str]) -> dict | None:
    """Build the payload section from retrieval.json and response.json.

    Returns None if either file is missing from the input list.
    """
    retrieval_path = None
    response_path = None
    for path in input_files:
        basename = os.path.basename(path)
        if basename == "retrieval.json":
            retrieval_path = path
        elif basename == "response.json":
            response_path = path

    if not retrieval_path or not response_path:
        return None

    with open(retrieval_path, "r", encoding="utf-8") as f:
        retrieval = json.load(f)
    with open(response_path, "r", encoding="utf-8") as f:
        response_data = json.load(f)

    query = retrieval.get("query", "")
    answer = response_data.get("response", "")
    sources = [
        {
            "chunk_id": r.get("chunk_id", ""),
            "source_name": r.get("source_name", "unknown"),
            "score": round(r.get("score", 0), 4),
            "text_excerpt": r.get("text", "")[:300],
        }
        for r in retrieval.get("results", [])
    ]

    hash_input = query + answer + json.dumps(sources, sort_keys=True)
    payload_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]

    return {
        "query": query,
        "answer": answer,
        "sources": sources,
        "payload_hash": payload_hash,
    }


def main():
    load_rag_env()

    if len(sys.argv) < 2:
        print("Usage: python modules/rag/tasks/build_receipt.py <output_json> [...]", file=sys.stderr)
        sys.exit(1)

    input_files = sys.argv[1:]
    for path in input_files:
        if not os.path.exists(path):
            print(f"ERROR: {path} not found", file=sys.stderr)
            sys.exit(1)

    run_id = os.environ.get("WORKFLOW_HASH", "unknown")
    workflow_name = os.environ.get("CTX_WORKFLOW_NAME", os.environ.get("WORKFLOW_NAME", "unknown"))

    # Build steps in order, chaining output hashes as next step's input hashes
    steps = []
    prev_output_hashes = {}
    for filepath in input_files:
        step = _extract_step(filepath, prev_output_hashes)
        steps.append(step)
        prev_output_hashes = step.output_hashes

    payload = _extract_payload(input_files)
    receipt = create_receipt(run_id, workflow_name, steps, payload=payload)

    with open("receipt.json", "w", encoding="utf-8") as f:
        f.write(receipt.to_json())

    print(f"Wrote receipt.json ({len(steps)} steps, final_hash={receipt.final_output_hash})")


if __name__ == "__main__":
    main()
