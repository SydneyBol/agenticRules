"""The runtime settings, read once by an entry point (api/service.py, api/cli.py) and handed down to agent.run.

Nothing else reads the environment: library code takes Settings() (the code defaults) unless it is given these.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the repo


@dataclass(frozen=True)
class Settings:
    """What a run may be configured with; each field's comment says what it sets."""

    max_turns: int = 20  # model turns per run (14 until 2026-10-29: sub-prompt runs needed 13-14)
    early_tools: bool = True  # start each tool call as soon as it has streamed
    catalogs: str = ""  # tenant catalog dumps (<tenant>/bolapi/*.json), kept outside the repo; "" = none
    cache_ttl: str = "1h"  # prompt-cache lifetime of the shared prefix (prompt, tenant catalog): "1h" or "5m"
    similar_offers: int = 2  # earlier offers of the tenant shown as reference, at most 2 (0 = off; needs history)
    history: str = ""  # earlier offers per tenant (<tenant>/offers.jsonl), kept outside the repo; "" = none
    embed_model: str = "cohere.embed-multilingual-v3"  # Bedrock embeddings for matching a request to earlier offers
    value_search: bool = (
        True  # lookup_values finds values by meaning too (core/tenant/value_search.py); off = by words only
    )
    value_embed_model: str = "BAAI/bge-small-en-v1.5"  # sentence-transformers bi-encoder for value search
    value_rerank_model: str = "BAAI/bge-reranker-v2-m3"  # sentence-transformers cross-encoder for value search
    trace_inputs: bool = False  # debugging: each tool call's input kept in the run's trace (diagnostics), for the log
    inline_activity_types: int = (
        200  # activity types listed in the prompt up to this many; more: found with lookup_values
    )
    verifier_url: str = ""  # the verifier service (compile + engine, a separate deployable); "" = no engine check
    verify_repair_turns: int = 4  # model turns the one repair after a failed engine check may take

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """OFFER_AUTHOR_<FIELD> for each field (TURNS for max_turns); a bool is off when "0"."""
        env = os.environ if env is None else env
        names = {f.name: "OFFER_AUTHOR_" + ("TURNS" if f.name == "max_turns" else f.name.upper()) for f in fields(cls)}
        cast: dict[type, Callable[[str], object]] = {bool: lambda v: v != "0", int: int, str: str}
        return cls(**{k: cast[type(getattr(cls, k))](env[n]) for k, n in names.items() if n in env})  # type: ignore[arg-type]
