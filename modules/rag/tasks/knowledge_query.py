#!/usr/bin/env python3
"""
Knowledge Query — Single-shot RAG for organizational knowledge
===============================================================
Self-contained script invoked by the ``knowledge_query`` catalog template.
Reads a pre-built vector index from /work/index.json, embeds the query,
retrieves top-K relevant chunks, assembles a prompt, and calls an LLM.

Returns structured JSON to stdout (captured by the engine).

Dependencies: httpx, numpy (installed in rag-tasks image).
No imports from modules.rag/ — follows fleet_status.py self-contained pattern.
"""

import hashlib
import json
import os
import sys
import time

import httpx
import numpy as np

# --- Configuration (env vars set via catalog `environment` field) ---
TOP_K = int(os.environ.get("TOP_K", "5"))
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

SYSTEM_PROMPT = (
    "You are a mining operations knowledge assistant for a Bitcoin mining company. "
    "Answer questions using ONLY the provided context documents. "
    "Rules: use only the provided context, be concise, provide specific numbers/names/dates "
    "when available, say explicitly if the context is insufficient to answer. "
    "Default response length: 1-4 sentences."
)

# Relative path — the engine sets CWD to /work/<task_name>/ where input files
# are staged.  Using an absolute /work/ path would miss the task subdirectory.
INDEX_PATH = os.environ.get("INDEX_PATH", "index.json")


def load_index(path: str) -> list[dict]:
    """Load the pre-built vector index (list of chunk dicts with embeddings)."""
    if not os.path.exists(path):
        print(json.dumps({
            "status": "error",
            "error": f"Index file not found: {path}",
        }))
        sys.exit(1)
    with open(path) as f:
        data = json.load(f)
    # Index formats: list of chunks, or dict with "chunks" or "entries" key
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("chunks", "entries"):
            if key in data:
                return data[key]
    print(json.dumps({
        "status": "error",
        "error": f"Invalid index format: expected list or dict with chunks/entries key, got keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}",
    }))
    sys.exit(1)


def embed_query(query: str) -> list[float]:
    """Embed a single query string via OpenAI embeddings API."""
    resp = httpx.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"input": query, "model": EMBEDDING_MODEL},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    dot = np.dot(va, vb)
    norm = np.linalg.norm(va) * np.linalg.norm(vb)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def retrieve(query_embedding: list[float], chunks: list[dict], top_k: int) -> list[dict]:
    """Retrieve top-K chunks by cosine similarity to the query embedding."""
    scored = []
    for chunk in chunks:
        embedding = chunk.get("embedding") or chunk.get("vector")
        if not embedding:
            continue
        sim = cosine_similarity(query_embedding, embedding)
        scored.append((sim, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "text": c.get("text", c.get("content", "")),
            "source": c.get("source", c.get("metadata", {}).get("source", "unknown")),
            "score": round(s, 4),
        }
        for s, c in scored[:top_k]
    ]


def generate_answer(query: str, context_chunks: list[dict]) -> dict:
    """Call OpenAI chat completions with retrieved context."""
    context_text = "\n\n---\n\n".join(
        f"[Source: {c['source']}]\n{c['text']}" for c in context_chunks
    )
    user_message = (
        f"Context documents:\n\n{context_text}\n\n---\n\n"
        f"Question: {query}"
    )
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "response": data["choices"][0]["message"]["content"],
        "model": data["model"],
        "usage": data.get("usage", {}),
    }


def main():
    # Read parameters from VALIDANCE_PARAMS env var (JSON)
    params_raw = os.environ.get("VALIDANCE_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        print(json.dumps({"status": "error", "error": "Invalid VALIDANCE_PARAMS JSON"}))
        sys.exit(1)

    query = params.get("query")
    if not query:
        print(json.dumps({"status": "error", "error": "Missing required parameter: query"}))
        sys.exit(1)

    if not OPENAI_API_KEY:
        print(json.dumps({"status": "error", "error": "OPENAI_API_KEY not set"}))
        sys.exit(1)

    t0 = time.time()

    # Load pre-built index
    chunks = load_index(INDEX_PATH)

    # Embed query
    query_embedding = embed_query(query)

    # Retrieve relevant chunks
    results = retrieve(query_embedding, chunks, TOP_K)

    # Generate answer
    answer = generate_answer(query, results)

    elapsed = round(time.time() - t0, 2)

    # Compute response hash for provenance
    response_hash = hashlib.sha256(answer["response"].encode()).hexdigest()[:16]

    output = {
        "status": "ok",
        "query": query,
        "response": answer["response"],
        "sources": [{"source": r["source"], "score": r["score"]} for r in results],
        "model": answer["model"],
        "usage": answer["usage"],
        "elapsed_seconds": elapsed,
        "manifest": {
            "step_type": "non_deterministic",
            "response_hash": response_hash,
            "embedding_model": EMBEDDING_MODEL,
            "llm_model": LLM_MODEL,
            "top_k": TOP_K,
            "index_chunks": len(chunks),
            "retrieved_chunks": len(results),
        },
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
