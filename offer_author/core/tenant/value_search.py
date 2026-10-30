"""Finding a catalog value by meaning, for lookup_values (catalog.py Catalog.lookup takes this as `search`).

The manager's words and the tenant's labels often differ ("flights" for the activity type "Fly", "eating out" for
"Dining"). A sentence-transformers bi-encoder finds the values nearest the words by meaning, and a cross-encoder
scores each candidate (BM25's and the embeddings') against the words. Both models run in this process, loaded once;
each value list is embedded once and kept. Measured 2026-10-29 on devintam1 activity types and a 5,000-SKU list:
13 phrasings, 12 right first (words only: 4); 164 ms per lookup at p50, 156 ms on 5,000 SKUs, warm.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from .catalog import Catalog

BGE_QUERY = "Represent this sentence for searching relevant passages: "  # the bge embeddings' query instruction


class ValueSearch:
    """The bi-encoder and cross-encoder, with the value lists embedded so far (the last `lists` of them)."""

    def __init__(self, embed_model: str, rerank_model: str, device: str = "cpu", lists: int = 64) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self.bi, self.ce = _load(SentenceTransformer, embed_model, device), _load(CrossEncoder, rerank_model, device)
        self.query = BGE_QUERY if "bge-" in embed_model else ""
        self.vectors: OrderedDict[int, Any] = OrderedDict()  # value list -> its vectors
        self.lists, self.lock = lists, threading.Lock()

    def _vectors(self, docs: list[str]) -> Any:
        """A value list's vectors, embedded on first use."""
        key = hash(tuple(docs))
        with self.lock:
            if key not in self.vectors:
                self.vectors[key] = self.bi.encode(docs, normalize_embeddings=True, batch_size=128)
                if len(self.vectors) > self.lists:
                    self.vectors.popitem(last=False)
            self.vectors.move_to_end(key)
            return self.vectors[key]

    def nearest(self, docs: list[str], text: str, k: int) -> list[int]:
        """The indexes of the k docs nearest the text by meaning, nearest first."""
        vectors = self._vectors(docs)
        with self.lock:
            q = self.bi.encode([self.query + text], normalize_embeddings=True)[0]
        return [int(i) for i in np.argsort(-(vectors @ q))[:k]]

    def rerank(self, text: str, docs: list[str]) -> list[float]:
        """Each doc's relevance to the text, 0..1."""
        with self.lock:
            return [float(s) for s in self.ce.predict([(text, d) for d in docs], batch_size=64)]

    def warm(self, catalog: Catalog) -> ValueSearch:
        """Embeds every value list of the catalog and runs the cross-encoder once, so no lookup pays a first call."""
        for obj, attrs in catalog.attributes.items():
            for code, a in attrs.items():
                if a.values:
                    self._vectors(catalog.value_docs(obj, code)[1])
        self.rerank("warm-up", ["warm-up"])
        return self


def _load(kind: Any, model: str, device: str) -> Any:
    """The model from the local Hugging Face cache, else downloaded once (no hub check on every start)."""
    try:
        return kind(model, device=device, local_files_only=True)
    except OSError:
        return kind(model, device=device)


@lru_cache(maxsize=2)
def value_search(embed_model: str, rerank_model: str) -> ValueSearch:
    """The process's ValueSearch for these models."""
    return ValueSearch(embed_model, rerank_model)
