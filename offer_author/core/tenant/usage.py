"""Which attributes a tenant's earlier offers read: what its managers actually use, by how often.

A tenant's attribute list holds everything bolapi has, test and automation attributes among it. The stored offers
(core/tenant/history.py) show which attributes real offers read, so the prompt can list those first and, over a size,
leave the rest to a search (ai/prompt.py CatalogShow).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from ..canvas.checks.reads import attribute_reads

_CACHE: dict[tuple[str, float, int, str], dict[tuple[str, str], int]] = {}


def attribute_use(history: str, catalog: Any) -> dict[tuple[str, str], int]:
    """{(list, code): offers that read it} over the tenant's stored offers; empty when it has none.

    Args:
        history: The history folder (<tenant>/offers.jsonl inside it).
        catalog: The tenant catalog: an attribute it does not have is not counted.
    """
    path = os.path.join(history or "", catalog.tenant, "offers.jsonl")
    if not history or not os.path.isfile(path):
        return {}
    stat = os.stat(path)
    key = (path, stat.st_mtime, stat.st_size, f"{catalog.sponsor_id}")
    if key not in _CACHE:
        use: Counter[tuple[str, str]] = Counter()
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    xml = json.loads(line).get("xml")
                    reads = attribute_reads(xml, catalog)
                except (ValueError, TypeError, AttributeError, KeyError):
                    continue
                use.update((r["list"], r["attribute"]) for r in reads)
        _CACHE[key] = dict(use)
    return _CACHE[key]
