"""One offer's run, as code: the tools the model calls and what they built.

No model and no prompt here (core never imports offer_author.ai): each tool takes the model's input and returns facts
and Problems.

    lookup_values   catalog codes for the manager's words (lookups.py)
    set_offer_main  the Offer Main form, checked (offer_main.py)
    compose         a canvas written as XML and checked for block compatibility (canvases.py)

finish is judged against what they built (policy.py); the run's result is made from it (outcome.py).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from ...domain import Result, ToolInput
from ..tenant.existing_counters import Guard
from .canvases import Canvases, saved_rules
from .lookups import Lookups
from .offer_main import OfferMain

PARTS = ("offer_main", "segment", "rules")


class Run:
    """The tools of one offer's run and their state.

    Attributes:
        catalog: The tenant catalog.
        offer_id: The offer the rules are for, or None.
        guard: The offer's existing counters (counters.Guard), or None when they are not known.
        lookups: lookup_values.
        offer_main: set_offer_main.
        canvases: compose.
        counts: How often each tool was called.
        trace: One entry per tool call, for the diagnostics.
    """

    def __init__(
        self,
        catalog: Any,
        offer_id: int | None,
        existing: dict[str, Any] | None = None,
        on_offer_main: Callable[[dict[str, Any]], None] | None = None,
        search: Any = None,
    ) -> None:
        """Sets up the tools for one offer.

        Args:
            catalog: The tenant's core.catalog.Catalog.
            offer_id: The offer the rules are for, or None.
            existing: What the offer already has: offer_status, counters, rules_xml; None when not known.
            on_offer_main: Called with Offer Main as soon as it is set.
            search: What lookup_values finds values by meaning with (ai/value_search.py), or None.
        """
        self.catalog, self.offer_id = catalog, offer_id
        has_counters = existing is not None and ("counters" in existing or "offer_status" in existing)
        self.guard = Guard(existing) if existing is not None and has_counters else None
        self.lookups = Lookups(catalog, search)
        self.offer_main = OfferMain(on_offer_main)
        self.canvases = Canvases(catalog, self.guard, saved_rules(existing))
        self.counts: Counter[str] = Counter()
        self.trace: list[dict[str, Any]] = []

    def current(self) -> dict[str, Any] | None:
        """The saved rules canvas as the model reads it, or None."""
        return self.canvases.current()

    def lookup_values(self, x: ToolInput) -> Result:
        """lookup_values: catalog codes for the manager's words."""
        self.counts["lookup_values"] += 1
        return self.lookups.lookup(x)

    def set_offer_main(self, x: ToolInput) -> Result:
        """set_offer_main: the Offer Main form."""
        self.counts["set_offer_main"] += 1
        return self.offer_main.set(x)

    def compose(self, x: ToolInput) -> Result:
        """compose: a canvas written as XML and checked."""
        self.counts["compose"] += 1
        return self.canvases.compose(x)
