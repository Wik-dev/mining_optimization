"""
Build a retrieval index from chunks and embeddings.

Usage: python modules/rag/tasks/build_index.py <chunks_json> <embeddings_json>

Reads both files, merges chunks with their embedding vectors,
and writes index.json.

Env vars (defaults in modules/rag/.env):
    KB_ID: knowledge base identifier
"""
import hashlib
import json
import os
import sys

from modules.rag.tasks import load_rag_env


def main():
    load_rag_env()

    if len(sys.argv) < 3:
        print("Usage: python modules/rag/tasks/build_index.py <chunks_json> <embeddings_json>", file=sys.stderr)
        sys.exit(1)

    chunks_file = sys.argv[1]
    embeddings_file = sys.argv[2]

    for path in (chunks_file, embeddings_file):
        if not os.path.exists(path):
            print(f"ERROR: {path} not found", file=sys.stderr)
            sys.exit(1)

    kb_id = os.environ["KB_ID"]

    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks_data = json.load(f)

    with open(embeddings_file, "r", encoding="utf-8") as f:
        embeddings_data = json.load(f)

    chunks = chunks_data["chunks"]
    embeddings = embeddings_data["embeddings"]

    # Build lookup: chunk_id -> embedding vector
    embedding_map = {e["id"]: e["vector"] for e in embeddings}

    entries = []
    for chunk in chunks:
        vector = embedding_map.get(chunk["id"])
        if vector is None:
            print(f"WARNING: No embedding for chunk {chunk['id']}", file=sys.stderr)
            continue
        entries.append({
            "chunk_id": chunk["id"],
            "text": chunk["text"],
            "vector": vector,
            "metadata": chunk.get("metadata", {}),
        })

    index_hash = hashlib.sha256(
        json.dumps([e["chunk_id"] for e in entries]).encode()
    ).hexdigest()[:16]

    output = {
        "kb_id": kb_id,
        "entries": entries,
        "entry_count": len(entries),
        "manifest": {
            "step_type": "deterministic",
            "index_hash": index_hash,
            "kb_id": kb_id,
        },
    }

    with open("index.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote index.json ({len(entries)} entries, kb={kb_id})")


if __name__ == "__main__":
    main()
