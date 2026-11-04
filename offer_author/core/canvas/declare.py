"""Counters a compose uses but does not declare, declared by the tools when the model's own blocks settle them.

A counter's declaration is a field the model's blocks already fix, as OBJECT and attribute_level are: when a name the
blocks use as a counter is not in the compose's `counters`, it is declared

- as the model declared it earlier in the run on the same canvas (a whole new tree sent without `counters`), or
- from its use, when every use agrees: the level every use that states one gives (`mta_type_value` on an update,
  `attribute_level` on a condition), and NUMBER when an update raises or lowers it (INCREMENT_BY / DECREMENT_BY).

Anything else (no level stated, levels that disagree, a TEXT or VECTOR counter, whose kind or namespace the use does
not give) stays undeclared, and the checks report it as before. Each declaration is reported back to the model.
"""

from __future__ import annotations

from typing import Any

from ...domain import Block, CounterDecl, Problem
from ..vocabulary import ACTIVITY_COUNTER, OFFER_COUNTER
from .fields import CONDITIONS
from .tree import nodes

LEVELS = (OFFER_COUNTER, ACTIVITY_COUNTER)  # the levels the upload creates counters at
NUMERIC_UPDATES = ("INCREMENT_BY", "DECREMENT_BY")


def declare(
    tops: list[Block], counters: list[CounterDecl], earlier: list[CounterDecl], catalog: Any = None
) -> tuple[list[CounterDecl], list[Problem]]:
    """The compose's counters with those its blocks settle added, and a note per added one.

    Args:
        tops: The canvas's top-level blocks (the model's node form).
        counters: The counters the compose declares.
        earlier: The counters declared earlier in the run on this canvas (or the saved rules'), newest last.
        catalog: The tenant catalog: a condition attribute it has is an attribute, not a counter.

    Returns:
        (counters, notes): the declarations, the given ones first; a `counter_declared_by_tools` note per added one.
    """
    have = {c.get("name") for c in counters}
    before = {c.get("name"): c for c in earlier}
    uses = _uses(tops, catalog)
    added: list[CounterDecl] = []
    notes: list[Problem] = []
    for name in sorted(n for n in uses if n not in have):
        decl, source = (before[name], "earlier") if name in before else (_from_use(name, uses[name]), "use")
        if decl is None:
            continue
        added.append(decl)
        notes.append(
            Problem.of(
                "counter_declared_by_tools",
                name=name,
                level=decl.get("level") or OFFER_COUNTER,
                data_type=decl.get("data_type"),
                source=source,
            )
        )
    return [*counters, *added], notes


def _uses(tops: list[Block], catalog: Any) -> dict[str, list[tuple[str | None, str | None]]]:
    """Per name the blocks use as a counter: (stated level, update operator) of each use."""
    out: dict[str, list[tuple[str | None, str | None]]] = {}
    for n, _, _ in nodes(tops):
        f = n.get("fields") or {}
        t = n.get("type")
        if t == "action" and f.get("action_type") == "update_attribute" and f.get("mta_attribute"):
            out.setdefault(str(f["mta_attribute"]), []).append((f.get("mta_type_value"), f.get("action_operator")))
        elif t in CONDITIONS and f.get("attribute") and not _catalog_attr(catalog, f):
            level = f.get("attribute_level")
            if level in LEVELS or (level is None and f.get("OBJECT") in (None, "member")):
                out.setdefault(str(f["attribute"]), []).append((level, None))
    return out


def _catalog_attr(catalog: Any, f: dict[str, Any]) -> bool:
    """The condition names an attribute the catalog has (under its OBJECT, or under any when none is given)."""
    if catalog is None:
        return False
    objs = [f["OBJECT"]] if f.get("OBJECT") else ["activity", "member", "product", "location", "sponsor"]
    return any(catalog.attr(o, f["attribute"]) for o in objs)


def _from_use(name: str, uses: list[tuple[str | None, str | None]]) -> CounterDecl | None:
    """A NUMBER counter at the one level its uses state, when one update raises or lowers it; else None."""
    levels = {lv for lv, _ in uses if lv}
    if len(levels) != 1 or not levels <= set(LEVELS):
        return None
    if not any(op in NUMERIC_UPDATES for _, op in uses):
        return None
    return {"name": name, "level": next(iter(levels)), "data_type": "NUMBER", "counter_type": "REGULAR"}
