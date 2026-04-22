"""
Text chunking with content addressing.
Deterministic: same input + params = same output + same hash.
"""
import hashlib
import json
from dataclasses import dataclass
from typing import List


@dataclass
class Chunk:
    id: str           # Content hash
    text: str
    index: int
    start_char: int
    end_char: int
    metadata: dict


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    source_id: str = None
) -> List[Chunk]:
    """
    Split text into overlapping chunks with content-addressed IDs.

    Args:
        text: Input text to chunk
        chunk_size: Target chunk size in characters
        chunk_overlap: Overlap between consecutive chunks
        source_id: Optional source document identifier

    Returns:
        List of Chunk objects with content-addressed IDs
    """
    chunks = []
    start = 0
    index = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk_text_str = text[start:end]

        # Content-addressed ID: hash of text + position
        content_hash = hashlib.sha256(
            f"{chunk_text_str}:{index}:{source_id or ''}".encode()
        ).hexdigest()[:16]

        chunks.append(Chunk(
            id=content_hash,
            text=chunk_text_str,
            index=index,
            start_char=start,
            end_char=end,
            metadata={"source_id": source_id}
        ))

        stride = max(1, chunk_size - chunk_overlap)
        start += stride
        index += 1

    return chunks


def chunks_to_manifest(chunks: List[Chunk]) -> dict:
    """Generate manifest with content hashes for audit trail."""
    return {
        "chunk_count": len(chunks),
        "chunk_ids": [c.id for c in chunks],
        "manifest_hash": hashlib.sha256(
            json.dumps([c.id for c in chunks]).encode()
        ).hexdigest()[:16]
    }
