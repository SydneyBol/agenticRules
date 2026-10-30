import numpy as np
from typing import Literal
from faiss import IndexFlatIP, normalize_L2 as Normalize
import pandas as pd
from sentence_transformers import SentenceTransformer
from general.constants import ai_bucket
from general.loggerutils import get_logger
from os import getenv
from general.errors import Error
from cachetools import TTLCache
logger = get_logger("GRAVTY_AI")
ENVIRONMENT_NAME = getenv('ENVIRONMENT_NAME')
paraphrase_model = SentenceTransformer('paraphrase-MiniLM-L6-v2')
# Cosine similarity below which a stored example is not returned (0: the k nearest, however far)
RAG_MIN_SCORE = float(getenv('PULSAR_RAG_MIN_SCORE', '0'))

# Per process: /refresh-cache reaches one worker only, so entries also expire and every worker reloads within the TTL
rag_cache = TTLCache(maxsize=1000, ttl=int(getenv('PULSAR_RAG_CACHE_TTL_S', '900')))


def _embed(sentences):
    """Unit-length float32 vectors: inner product is then cosine similarity, for the corpus and the queries alike."""
    embeddings = np.asarray(paraphrase_model.encode(sentences), dtype="float32")
    Normalize(embeddings)
    return embeddings


class ContextDB:
    """The program's few-shot examples (vectordb/data.csv: user = prompt, system = sub-prompt lines) with an exact
    cosine index. The corpus is a few hundred rows, so an exact flat index builds faster than a trained IVF-PQ one
    and loses no neighbours. It is rebuilt from data.csv on every load, so a refresh always serves the current data
    (the S3-pickled index could not be read back, and would have been stale after a rebuild if it could)."""

    def __init__(self, data_location: str, data_location_type: Literal["s3"] = "s3",
                 db_type: Literal["offer"] = "offer"):
        # currently only allows csv files from s3
        self.db_type = db_type
        self.data_location_type = data_location_type
        self.data_location = data_location
        if data_location.split(".")[-1].casefold() != "csv":
            raise Error.UnsupportedFileType.value
        self.program_id = data_location.split("/")[3]
        self.DB = pd.read_csv(data_location).reset_index(drop=True)

        self._prepare_index()

    def _prepare_index(self):
        embeddings = _embed(self.DB["user"].tolist())
        self.index = IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)  # ids are row positions in self.DB

    def get_top_context(self, prompt,
                        default_context="You are a bot that produces valid blockly xml for a given prompt"):
        scores, ids = self.index.search(_embed([prompt]), 1)
        score, match = float(scores[0][0]), int(ids[0][0])
        if match < 0 or score < RAG_MIN_SCORE:
            return default_context
        matched_context = self.DB["system"][match]
        logger.info(f"top score: {score} for context: {matched_context}")
        return matched_context

    def search_top_k(self, prompts_list, k, min_score=RAG_MIN_SCORE):
        if not hasattr(self, 'index'):
            logger.warning("Index not prepared, returning empty search results")
            return {}
        search_results = dict()
        scores, matches = self.index.search(_embed(prompts_list), min(k, len(self.DB)))
        for idx, prompt in enumerate(prompts_list):
            search_results[prompt] = {"matches": []}
            for score, match in zip(scores[idx], matches[idx]):
                if match == -1 or score < min_score:
                    break  # best first
                search_results[prompt]["matches"].append({
                    "id": int(match),
                    "score": float(score),
                    "prompt": self.DB["user"][match],
                    "context": self.DB["system"][match],
                    # "expected": self.DB["assistant"][match]
                })

        return search_results


def get_db_by_program(program_id: int) -> ContextDB:
    if rag_cache.get(program_id):
        return rag_cache.get(program_id)
    db_instance = ContextDB(f"s3://{ai_bucket}-{ENVIRONMENT_NAME}/{program_id}/gpt/rag/offer/vectordb/data.csv")
    rag_cache[program_id] = db_instance
    return db_instance
