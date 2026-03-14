"""
Vector store utilities – wraps FAISS for semantic search over
module Purpose Statements and function descriptions.

Storage layout (under persist_dir/):
  faiss.index     – the FAISS flat L2 index (float32 vectors)
  id_map.json     – list[str] mapping vector position → module path (the "id")
  metadata.json   – list[dict] of arbitrary per-entry metadata

Design:
- `SemanticStore.upsert(id, document, metadata)` encodes the document text
  with SentenceTransformer and adds it to the index.
- `SemanticStore.upsert_batch(items)` is the efficient batch path used by
  the Orchestrator after the Semanticist completes.
- `SemanticStore.query(text, n_results)` encodes the query, runs FAISS kNN,
  and returns the top-n matches with ids, documents, distances, and metadata.
- The index is persisted to disk after every mutation.
- If the index file already exists on construction it is loaded and extended
  (upsert semantics: existing entry for the same id is replaced in-place).
"""

from __future__ import annotations

import json
import numpy as np
from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    import faiss  # type: ignore
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("faiss-cpu not installed – FAISS vector search disabled.")

try:
    from sentence_transformers import SentenceTransformer
    ST_AVAILABLE = True
except ImportError:
    ST_AVAILABLE = False
    logger.warning("sentence-transformers not installed – vector search disabled.")

_AVAILABLE = FAISS_AVAILABLE and ST_AVAILABLE

# ---------------------------------------------------------------------------
# File names inside the persist directory
# ---------------------------------------------------------------------------

_INDEX_FILE = "faiss.index"
_ID_MAP_FILE = "id_map.json"
_META_FILE = "metadata.json"
_DOC_FILE = "documents.json"   # raw text corpus (needed for query results)


class SemanticStore:
    """
    Lightweight FAISS-backed semantic store.

    Stores (id, document, metadata) tuples where:
      - id       = repo-relative file path or qualified function name
      - document = Purpose Statement text
      - metadata = {path, domain, language, ...}

    API is intentionally kept identical to the previous ChromaDB wrapper so
    callers (Orchestrator, Navigator) need zero changes.
    """

    def __init__(self, persist_dir: Path) -> None:
        self.persist_dir = persist_dir
        self.available = _AVAILABLE

        # Internal state
        self._ids: list[str] = []          # position → id string
        self._docs: list[str] = []         # position → raw document text
        self._metas: list[dict] = []       # position → metadata dict
        self._index: Any = None            # faiss.Index or None
        self._model: Any = None            # SentenceTransformer or None
        self._dim: int = 0

        if not self.available:
            return

        persist_dir.mkdir(parents=True, exist_ok=True)
        self._model = SentenceTransformer(CONFIG.analysis.embedding_model)
        self._dim = self._model.get_sentence_embedding_dimension()

        # Load existing index if present
        index_path = persist_dir / _INDEX_FILE
        id_map_path = persist_dir / _ID_MAP_FILE
        doc_path = persist_dir / _DOC_FILE
        meta_path = persist_dir / _META_FILE

        if index_path.exists() and id_map_path.exists():
            try:
                self._index = faiss.read_index(str(index_path))
                self._ids = json.loads(id_map_path.read_text(encoding="utf-8"))
                self._docs = json.loads(doc_path.read_text(encoding="utf-8")) if doc_path.exists() else [""] * len(self._ids)
                self._metas = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else [{}] * len(self._ids)
                logger.info(f"[SemanticStore] Loaded existing FAISS index ({self._index.ntotal} vectors) from {persist_dir}")
            except Exception as exc:
                logger.warning(f"[SemanticStore] Could not load existing index, starting fresh: {exc}")
                self._reset_index()
        else:
            self._reset_index()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_index(self) -> None:
        """Create a new flat L2 index."""
        if not self.available:
            return
        # IndexFlatL2: exact nearest-neighbour, no approximation.
        # For larger corpora switch to faiss.IndexHNSWFlat(dim, 32).
        self._index = faiss.IndexFlatL2(self._dim)
        self._ids = []
        self._docs = []
        self._metas = []

    def _encode(self, texts: list[str]) -> np.ndarray:
        """Encode a list of strings to float32 embeddings."""
        vecs = self._model.encode(texts, show_progress_bar=False)
        return np.array(vecs, dtype="float32")

    def _save(self) -> None:
        """Persist the index and companion files to disk."""
        if not self.available or self._index is None:
            return
        faiss.write_index(self._index, str(self.persist_dir / _INDEX_FILE))
        (self.persist_dir / _ID_MAP_FILE).write_text(
            json.dumps(self._ids, indent=2), encoding="utf-8"
        )
        (self.persist_dir / _DOC_FILE).write_text(
            json.dumps(self._docs, indent=2), encoding="utf-8"
        )
        (self.persist_dir / _META_FILE).write_text(
            json.dumps(self._metas, indent=2), encoding="utf-8"
        )

    def _find_existing(self, id: str) -> int:
        """Return the list index of an existing id, or -1."""
        try:
            return self._ids.index(id)
        except ValueError:
            return -1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, id: str, document: str, metadata: dict[str, Any] | None = None) -> None:
        """
        Add or replace a single entry.

        FAISS doesn't natively support in-place replacement, so we
        rebuild the index from scratch when an id already exists.
        For single-entry upserts this is acceptable; use upsert_batch
        for bulk operations.
        """
        if not self.available:
            return

        existing = self._find_existing(id)
        if existing >= 0:
            # Replace in the list buffers
            self._docs[existing] = document
            self._metas[existing] = metadata or {}
            # Rebuild the FAISS index from all stored docs
            self._rebuild_index()
        else:
            vec = self._encode([document])
            self._index.add(vec)
            self._ids.append(id)
            self._docs.append(document)
            self._metas.append(metadata or {})

        self._save()

    def upsert_batch(self, items: list[dict[str, Any]]) -> None:
        """
        Efficiently upsert a batch of items.

        items: list of {id, document, metadata}

        Strategy: partition into new vs. existing, update existing in-place
        in the list buffers, then do a single FAISS rebuild.
        """
        if not self.available or not items:
            return

        new_items = []
        for item in items:
            id_ = item["id"]
            doc = item["document"]
            meta = item.get("metadata", {})
            pos = self._find_existing(id_)
            if pos >= 0:
                self._docs[pos] = doc
                self._metas[pos] = meta
            else:
                new_items.append((id_, doc, meta))

        if new_items:
            ids, docs, metas = zip(*new_items)
            vecs = self._encode(list(docs))
            self._index.add(vecs)
            self._ids.extend(ids)
            self._docs.extend(docs)
            self._metas.extend(metas)

        # If any entries were updated in-place we need to rebuild
        # (only if existing entries changed; new-only path skips this)
        updated = [i for i in items if self._find_existing(i["id"]) >= 0 and i not in [{"id": x[0], "document": x[1], "metadata": x[2]} for x in (new_items if new_items else [])]]
        if updated:
            self._rebuild_index()

        self._save()
        logger.info(f"[SemanticStore] Upserted {len(items)} entries → {self._index.ntotal} total vectors")

    def query(self, text: str, n_results: int = 5) -> list[dict[str, Any]]:
        """
        Return top-n semantically similar entries.

        Returns a list of dicts with keys: id, document, metadata, distance.
        Distance is L2; smaller = more similar.
        """
        if not self.available or self._index is None or self._index.ntotal == 0:
            return []

        k = min(n_results, self._index.ntotal)
        vec = self._encode([text])
        distances, indices = self._index.search(vec, k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._ids):
                continue
            results.append(
                {
                    "id": self._ids[idx],
                    "document": self._docs[idx],
                    "metadata": self._metas[idx],
                    "distance": float(dist),
                }
            )
        return results

    def count(self) -> int:
        """Return the number of vectors in the index."""
        if not self.available or self._index is None:
            return 0
        return self._index.ntotal

    # ------------------------------------------------------------------
    # Internal: full rebuild (needed after in-place updates)
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Drop and recreate the FAISS index from the current doc list."""
        self._index = faiss.IndexFlatL2(self._dim)
        if self._docs:
            vecs = self._encode(self._docs)
            self._index.add(vecs)