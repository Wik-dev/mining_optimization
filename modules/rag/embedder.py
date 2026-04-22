"""
Embedding generation with version pinning.
Deterministic given: same text + same model version = same embedding.
"""
import hashlib
import os
from dataclasses import dataclass
from typing import List, Optional

import httpx


@dataclass
class EmbeddingResult:
    id: str              # Chunk ID this embedding belongs to
    vector: List[float]
    model: str
    model_version: str


class Embedder:
    """Embedding client with explicit version tracking."""

    def __init__(
        self,
        provider: str = "openai",
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # Pin version for reproducibility
        self.model_version = f"{provider}:{model}:v1"

    def embed_texts(self, texts: List[str], chunk_ids: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for a list of texts."""
        if self.provider == "openai":
            return self._embed_openai(texts, chunk_ids)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _embed_openai(self, texts: List[str], chunk_ids: List[str], batch_size: int = 200) -> List[EmbeddingResult]:
        """Call OpenAI embeddings API in batches."""
        results = []
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start:start + batch_size]
            batch_ids = chunk_ids[start:start + batch_size]
            print(f"  Embedding batch {start // batch_size + 1} ({len(batch_texts)} chunks)")
            response = httpx.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": batch_texts},
                timeout=120.0
            )
            response.raise_for_status()
            data = response.json()
            results.extend(
                EmbeddingResult(
                    id=batch_ids[i],
                    vector=item["embedding"],
                    model=self.model,
                    model_version=self.model_version
                )
                for i, item in enumerate(data["data"])
            )
        return results

    def get_config_hash(self) -> str:
        """Hash of embedding configuration for manifest."""
        config = f"{self.provider}:{self.model}:{self.model_version}"
        return hashlib.sha256(config.encode()).hexdigest()[:16]
