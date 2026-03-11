"""
Vector store utilities – wraps ChromaDB for semantic search over
module Purpose Statements and function descriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

try:
    import chromadb
    from chromadb.config import Settings
    from sentence_transformers import SentenceTransformer

    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("chromadb or sentence-transformers not installed – vector search disabled.")


class SemanticStore:
    """
    Lightweight wrapper around a local ChromaDB collection.
    Stores (id, document, metadata) tuples where:
      - id       = repo-relative file path or qualified function name
      - document = Purpose Statement text
      - metadata = {path, domain, language, ...}
    """

    COLLECTION_NAME = "cartographer_modules"

    def __init__(self, persist_dir: Path) -> None:
        self.available = CHROMA_AVAILABLE
        if not self.available:
            return

        self._client = chromadb.PersistentClient(
            path=str(persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._model = SentenceTransformer(CONFIG.analysis.embedding_model)

    def upsert(self, id: str, document: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.available:
            return
        embedding = self._model.encode(document).tolist()
        self._collection.upsert(
            ids=[id],
            documents=[document],
            embeddings=[embedding],
            metadatas=[metadata or {}],
        )

    def upsert_batch(self, items: list[dict[str, Any]]) -> None:
        """
        items: list of {id, document, metadata}
        """
        if not self.available or not items:
            return
        ids = [i["id"] for i in items]
        docs = [i["document"] for i in items]
        metas = [i.get("metadata", {}) for i in items]
        embeddings = self._model.encode(docs).tolist()
        self._collection.upsert(ids=ids, documents=docs, embeddings=embeddings, metadatas=metas)

    def query(self, text: str, n_results: int = 5) -> list[dict[str, Any]]:
        """Return top-n semantically similar modules."""
        if not self.available:
            return []
        embedding = self._model.encode(text).tolist()
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for i, doc_id in enumerate(results["ids"][0]):
            out.append(
                {
                    "id": doc_id,
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                }
            )
        return out

    def count(self) -> int:
        if not self.available:
            return 0
        return self._collection.count()