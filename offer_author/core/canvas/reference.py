"""The block reference: each toolbox block's inputs, fields and mutation sources.

Generated from the stock UI's block sources (tools/build) into data/block_reference.json; also lists the condition
blocks whose values are picked codes.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

REF = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "block_reference.json")


def reference(canvas: str = "rules") -> dict[str, dict[str, Any]]:
    """Each toolbox block's generated spec, by block type, for one canvas ('rules' or 'segment')."""
    with open(REF) as f:
        return {b["type"]: b for b in json.load(f)["canvases"][canvas]["toolbox"]}


@lru_cache(maxsize=1)
def picks_by_operator() -> frozenset[str]:
    """Condition block types whose value follows the operator (block_reference.json picks_by_operator)."""
    with open(REF) as f:
        return frozenset(
            b["type"] for c in json.load(f)["canvases"].values() for b in c["toolbox"] if b.get("picks_by_operator")
        )
