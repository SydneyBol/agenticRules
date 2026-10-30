"""The lookup_values tool: catalog codes for the manager's words."""

from __future__ import annotations

from typing import Any

from ...domain import Result, ToolInput


class Lookups:
    """Finds catalog values for one run and keeps what each lookup found first (for the diagnostics).

    Attributes:
        made: One entry per lookup: the attribute, the words looked up, and the top code found.
    """

    def __init__(self, catalog: Any, search: Any = None) -> None:
        """Looks up in this tenant catalog.

        Args:
            catalog: The tenant's core.catalog.Catalog.
            search: What finds values by meaning (ai/value_search.py), or None to find them by their words only.
        """
        self.catalog, self.search = catalog, search
        self.made: list[dict[str, Any]] = []

    def lookup(self, x: ToolInput) -> Result:
        """The catalog's candidates for one value of one attribute."""
        r = self.catalog.lookup(x.get("object", ""), x.get("attribute", ""), x.get("text", ""), search=self.search)
        self.made.append(
            {
                "attribute": f"{x.get('object')}.{x.get('attribute')}",
                "text": x.get("text"),
                "top": (r.get("candidates") or [{}])[0].get("code"),
            }
        )
        return r
