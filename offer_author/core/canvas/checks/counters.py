"""The counters a canvas declares, checked.

Each is declared once, a VECTOR counter says what it counts per, and a name the offer already has is reused only when
the upload's match by name would be right.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ....domain import CounterDecl, Problem
from ...tenant.existing_counters import Guard
from ...vocabulary import ACTIVITY_COUNTER, OFFER_COUNTER
from ..fields import counter_ref

DECLARED_LEVELS = (OFFER_COUNTER, ACTIVITY_COUNTER)  # the levels whose counters the upload creates from `counters`


def counter_problems(xml: str, counters: list[CounterDecl], guard: Guard | None) -> list[Problem]:
    """Checks a canvas's counter declarations.

    Every offer or activity counter the blocks refer to is declared once, at the level the blocks use it (the screen
    looks a counter up in its own level's list and leaves the field empty when it is not there), a VECTOR counter
    says what it counts per, and a name that already exists on the offer is reused only when the upload's match by
    name would be right (counters.Guard).

    Args:
        xml: The written canvas.
        counters: The counters the model declared.
        guard: The offer's existing counters, or None when they are not known.

    Returns:
        The counter problems; empty when there are none.
    """
    names = [c.get("name") for c in counters]
    out = [
        Problem.of("counter_declared_twice", name=n) for n in sorted({n for n in names if names.count(n) > 1}, key=str)
    ]
    for c in counters:
        if c.get("counter_type") == "VECTOR" and not c.get("namespace"):
            out.append(Problem.of("vector_without_namespace", name=c.get("name")))
        spec = (
            c.get("name"),
            c.get("data_type"),
            c.get("counter_type", "REGULAR"),
            c.get("namespace"),
            c.get("level") or OFFER_COUNTER,
        )
        if guard and guard.taken(*spec):
            out.append(Problem.of("counter_taken", name=c["name"], why=guard.why(*spec)))
    declared = {(c.get("level") or OFFER_COUNTER, c.get("name")) for c in counters}
    at = {n: lv for lv, n in declared}
    for level, name in sorted(r for r in _refs(xml) if r[0] in DECLARED_LEVELS and r not in declared):
        out.append(Problem.of("undeclared_counter", name=name, level=level, declared=at.get(name)))
    return out + [Problem.of("unique_after_reset", name=n) for n in _unique_after_reset(xml)]


RESETS = ("RESET", "RESET_ALL")


def _unique_after_reset(xml: str) -> list[str]:
    """Vector counters the canvas both resets and reads as `unique`.

    A reset sets the values to 0 and keeps the keys, and `unique` counts keys whatever their values: what such a
    condition reads does not go down at the reset.
    """
    reset: set[str] = set()
    unique: set[str] = set()
    for b in ET.fromstring(xml).iter():
        if b.tag.split("}")[-1] != "block":
            continue
        f = {x.get("name"): x.text for x in b if x.tag.split("}")[-1] == "field"}
        if f.get("action_operator") in RESETS and f.get("mta_attribute"):
            reset.add(str(f["mta_attribute"]))
        if f.get("vector_counter_name") == "unique" and f.get("attribute"):
            unique.add(str(f["attribute"]))
    return sorted(reset & unique)


def used_counters(xml: str, counters: list[CounterDecl]) -> list[CounterDecl]:
    """The declared counters the canvas's blocks refer to: the ones the stock upload creates on the offer."""
    used = {name for _, name in _refs(xml)}
    return [c for c in counters if c.get("name") in used]


def _refs(xml: str) -> set[tuple[str | None, str]]:
    """(level, name) of every counter the blocks refer to."""
    refs = (counter_ref(m.attrib) for m in ET.fromstring(xml).iter() if m.tag.split("}")[-1] == "mutation")
    return {(level, name) for level, name in refs if name}
