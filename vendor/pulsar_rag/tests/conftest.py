"""Runs the Pulsar RAG modules outside ai-develop: platform modules that are not copied here (AWS, RDS, OpenAI, the
sentence-transformers model, the rest of `general`) are stubs. The encoder is a deterministic bag of words, so
similarity tests are exact. Run from the repo root: python -m pytest pulsar_rag/tests"""
import hashlib
import importlib.abc
import importlib.machinery
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ENVIRONMENT_NAME", "test")
STUBBED = ("general", "pulsar", "apis", "common_module", "boto3", "openai", "jellyfish", "xmlschema",
           "sentence_transformers", "flask", "requests", "jsonl_creator", "test_train_dataset")


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        value = MagicMock(name=f"{self.__name__}.{name}")
        setattr(self, name, value)
        return value


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """A module under a stubbed name that is not a file here becomes a stub module (a package, so submodules work)."""

    def find_spec(self, name, path, target=None):
        if name.split(".")[0] not in STUBBED:
            return None
        if importlib.machinery.PathFinder.find_spec(name, path or [str(ROOT)]) is not None:
            return None  # the real copied file
        return importlib.machinery.ModuleSpec(name, self, is_package=True)

    def create_module(self, spec):
        module = _Stub(spec.name)
        module.__path__ = []
        return module

    def exec_module(self, module):
        pass


sys.meta_path.append(_StubFinder())


def embed(text, dim=64):
    """Bag of words hashed into dim buckets: texts sharing words are similar, unrelated texts are near orthogonal."""
    v = np.zeros(dim, dtype="float32")
    for w in str(text).lower().split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
    return v * 3.0  # not unit length, like MiniLM output


class FakeSentenceTransformer:
    def __init__(self, *_, **__):
        pass

    def encode(self, sentences, **_):
        return np.stack([embed(s) for s in sentences]) if isinstance(sentences, list) else embed(sentences)


def _install(name, **attrs):
    module = sys.modules.get(name) or importlib.import_module(name)
    for k, v in attrs.items():
        setattr(module, k, v)


_install("sentence_transformers", SentenceTransformer=FakeSentenceTransformer)
_install("general.loggerutils", get_logger=lambda *a, **k: logging.getLogger("pulsar-test"))
_install("general.constants", ai_bucket="gravty-ai")
_install("general.secrets_manager_client", get_secret=lambda name: (False, {}))
_install("general.s3_client", read_from_s3=lambda **k: "{}")
