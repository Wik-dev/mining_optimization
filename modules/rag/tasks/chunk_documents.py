"""
Chunk multiple documents into overlapping text segments.

Usage: python modules/rag/tasks/chunk_documents.py <documents_json>

Reads documents.json (array of documents), chunks each one, and writes
a single chunks.json with a flat array of all chunks from all documents.

Env vars (defaults in modules/rag/.env):
    CHUNK_SIZE: target chunk size in chars
    CHUNK_OVERLAP: overlap between chunks in chars
"""
import hashlib
import json
import os
import sys

from modules.rag.chunker import chunk_text
from modules.rag.tasks import load_rag_env


def main():
    load_rag_env()

    if len(sys.argv) < 2:
        print("Usage: python modules/rag/tasks/chunk_documents.py <documents_json>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"ERROR: {input_file} not found", file=sys.stderr)
        sys.exit(1)

    chunk_size = int(os.environ["CHUNK_SIZE"])
    chunk_overlap = int(os.environ["CHUNK_OVERLAP"])

    print(f"Chunking with size={chunk_size}, overlap={chunk_overlap}")
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_chunks = []
    for doc in data["documents"]:
        source_id = doc.get("content_hash", "unknown")
        source_name = doc.get("source_name", "unknown")

        chunks = chunk_text(
            doc["text"],
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            source_id=source_id,
        )

        for c in chunks:
            all_chunks.append({
                "id": c.id,
                "text": c.text,
                "index": c.index,
                "start_char": c.start_char,
                "end_char": c.end_char,
                "metadata": {**c.metadata, "source_name": source_name},
            })

        print(f"  {source_name}: {len(chunks)} chunks")

    chunk_ids = [c["id"] for c in all_chunks]
    manifest = {
        "step_type": "deterministic",
        "chunk_count": len(chunk_ids),
        "chunk_ids": chunk_ids,
        "manifest_hash": hashlib.sha256(
            json.dumps(chunk_ids).encode()
        ).hexdigest()[:16],
    }

    output = {
        "chunks": all_chunks,
        "config": {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        "manifest": manifest,
    }

    with open("chunks.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote chunks.json ({len(all_chunks)} chunks from {len(data['documents'])} documents)")


if __name__ == "__main__":
    main()
