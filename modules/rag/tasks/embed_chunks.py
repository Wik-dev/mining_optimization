"""
Generate embeddings for text chunks.

Usage: python modules/rag/tasks/embed_chunks.py <chunks_json>

Reads the chunks JSON, generates embeddings using the embedder module,
and writes embeddings.json.

Env vars (defaults in modules/rag/.env):
    EMBEDDING_MODEL: model name
    OPENAI_API_KEY: injected by executor
"""
import hashlib
import json
import os
import sys

from modules.rag.embedder import Embedder
from modules.rag.tasks import load_rag_env


def main():
    load_rag_env()

    if len(sys.argv) < 2:
        print("Usage: python modules/rag/tasks/embed_chunks.py <chunks_json>", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"ERROR: {input_file} not found", file=sys.stderr)
        sys.exit(1)

    model = os.environ["EMBEDDING_MODEL"]

    with open(input_file, "r", encoding="utf-8") as f:
        chunks_data = json.load(f)

    chunks = chunks_data["chunks"]
    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]

    print(f"Embedding {len(texts)} chunks with model={model}")
    embedder = Embedder(model=model)
    results = embedder.embed_texts(texts, ids)

    embeddings_list = [
        {
            "id": r.id,
            "vector": r.vector,
            "model": r.model,
            "model_version": r.model_version,
        }
        for r in results
    ]

    vectors_hash = hashlib.sha256(
        json.dumps([e["vector"] for e in embeddings_list]).encode()
    ).hexdigest()[:16]

    output = {
        "embeddings": embeddings_list,
        "config": {
            "model": model,
            "config_hash": embedder.get_config_hash(),
        },
        "manifest": {
            "step_type": "deterministic",
            "embedding_count": len(embeddings_list),
            "vectors_hash": vectors_hash,
        },
    }

    with open("embeddings.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote embeddings.json ({len(embeddings_list)} embeddings)")


if __name__ == "__main__":
    main()
