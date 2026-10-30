"""Bedrock text embeddings for matching a request to a tenant's earlier offers (history.py takes any embed)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

import numpy as np

from .history import Histories


class BedrockEmbedder:
    """embed(texts, kind) -> unit vectors (n, dims).

    kind is "document" for stored offers, "query" for a request. Cohere models (batches of 96, input_type by kind) or
    Titan v2 (one text per call).
    """

    def __init__(
        self, model: str = "cohere.embed-multilingual-v3", region: str | None = None, client: Any = None
    ) -> None:
        self.model = model
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region or os.environ.get("AWS_REGION", "us-west-2"))
        self.client = client

    def _call(self, body: dict[str, Any]) -> Any:
        """One invoke_model call's decoded body."""
        res = self.client.invoke_model(modelId=self.model, body=json.dumps(body))
        return json.loads(res["body"].read())

    def __call__(self, texts: list[str], kind: str = "document") -> np.ndarray:
        """The texts' unit vectors, one row per text."""
        if self.model.startswith("cohere."):
            kind = "search_query" if kind == "query" else "search_document"
            vecs = [
                v
                for i in range(0, len(texts), 96)
                for v in self._call({"texts": [t[:2048] for t in texts[i : i + 96]], "input_type": kind})["embeddings"]
            ]
        else:
            vecs = [
                self._call({"inputText": t[:8000], "dimensions": 512, "normalize": True})["embedding"] for t in texts
            ]
        v = np.asarray(vecs, dtype="float32").reshape(len(texts), -1)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


@lru_cache(maxsize=4)
def histories(root: str, model: str) -> Histories:
    """The process's Histories for a history folder and embedding model."""
    return Histories(root, BedrockEmbedder(model))
