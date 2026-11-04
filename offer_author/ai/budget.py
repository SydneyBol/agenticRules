"""The hard limit on Bedrock model calls.

User, 2026-11-04: "Do not exceed 200 LLM calls ... hardcode this limit", then "increase 200 limit to 400 max", then
(to measure the latency changes on the 18 vertical offers) "raise the hard limit to 500".

Every request the chat model sends to Bedrock (each turn, each retry, each cache warm) first takes one call from a
ledger file shared by every process on the machine (backend, CLI, live tests). The ledger counts up and is never reset
by code; once MAX_LLM_CALLS calls are taken, every further request fails with LLMBudgetError before it is sent.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

MAX_LLM_CALLS = 500
DEFAULT_LEDGER = Path.home() / ".offer_author" / "llm_calls.json"


class LLMBudgetError(Exception):
    """The ledger has reached MAX_LLM_CALLS: no further model call is sent."""


class Ledger:
    """A count of model calls in a JSON file, taken under an exclusive file lock.

    Attributes:
        path: The ledger file ({"calls": n, "limit": MAX_LLM_CALLS, "last": [{"at", "kind"}...]}).
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.environ.get("OFFER_AUTHOR_LLM_LEDGER") or DEFAULT_LEDGER)

    def calls(self) -> int:
        """The calls taken so far."""
        try:
            return int(json.loads(self.path.read_text()).get("calls", 0))
        except (OSError, ValueError):
            return 0

    def take(self, kind: str) -> int:
        """Takes one call; returns the count including it.

        Raises:
            LLMBudgetError: MAX_LLM_CALLS calls were already taken.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                data = json.loads(f.read() or "{}")
            except ValueError:
                data = {}
            n = int(data.get("calls", 0))
            if n >= MAX_LLM_CALLS:
                raise LLMBudgetError(f"LLM call budget spent: {n} of {MAX_LLM_CALLS} calls ({self.path})")
            last = [*data.get("last", []), {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}][-20:]
            f.seek(0)
            f.truncate()
            json.dump({"calls": n + 1, "limit": MAX_LLM_CALLS, "last": last}, f)
            return n + 1
