"""
RAG modules — Retrieval-Augmented Generation pipeline.

Core modules shipped with the mining_optimization delivery:
- chunker:  Text chunking with content addressing
- embedder: Embedding generation (OpenAI text-embedding-3-small)
- receipt:  Provenance receipt generation

The single-shot ``knowledge_query`` task script (under ``tasks/``) is
self-contained and does not import from this package.

Imports are lazy so task entry points that only need a subset of modules
do not pull in heavy dependencies (httpx, numpy) at import time.
"""


def __getattr__(name):
    if name in ("Chunk", "chunk_text", "chunks_to_manifest"):
        from modules.rag.chunker import Chunk, chunk_text, chunks_to_manifest
        return {"Chunk": Chunk, "chunk_text": chunk_text, "chunks_to_manifest": chunks_to_manifest}[name]

    if name in ("Embedder", "EmbeddingResult"):
        from modules.rag.embedder import Embedder, EmbeddingResult
        return {"Embedder": Embedder, "EmbeddingResult": EmbeddingResult}[name]

    if name in ("StepReceipt", "ExecutionReceipt", "create_receipt"):
        from modules.rag.receipt import StepReceipt, ExecutionReceipt, create_receipt
        return {"StepReceipt": StepReceipt, "ExecutionReceipt": ExecutionReceipt, "create_receipt": create_receipt}[name]

    raise AttributeError(f"module 'modules.rag' has no attribute {name!r}")


__all__ = [
    "Chunk",
    "chunk_text",
    "chunks_to_manifest",
    "Embedder",
    "EmbeddingResult",
    "StepReceipt",
    "ExecutionReceipt",
    "create_receipt",
]
