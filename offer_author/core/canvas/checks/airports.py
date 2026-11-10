"""Airport conditions that name a value no active airport of the program has.

A fact for the model, not a problem: the screen accepts the value (it is in the attribute's list) and the canvas is
written with it. The airport setup tells what the lists cannot: an inactive airport, a city or zone no airport is in.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ....domain import Problem

COMPARED = ("==", "!=", "in", "not_in")  # operators whose value is one code, or codes separated by commas


def airport_notes(xml: str, catalog: Any) -> list[Problem]:
    """Per airport condition of the canvas: the values it names that no active airport has.

    Only an attribute the airports carry is judged (Catalog.airports_with): for another one nothing is known.

    Args:
        xml: The written canvas.
        catalog: The tenant catalog, or None.

    Returns:
        A `no_active_airport` note per condition with such values (its side, attribute, those values and the
        program's active airports), each once.
    """
    if catalog is None:
        return []
    out: dict[Problem, None] = {}
    for b in ET.fromstring(xml).iter():
        m = (
            next((c for c in b if c.tag.split("}")[-1] == "mutation"), None)
            if b.tag.split("}")[-1] == "block"
            else None
        )
        if m is None or m.get("object") != "airport" or m.get("operators") not in COMPARED:
            continue
        attribute = m.get("attribute") or ""
        at = catalog.airports_with("airport", attribute)
        if at is None:
            continue
        without = [v for v in (m.get("value") or "").split(",") if v and not at.get(v)]
        if without:
            active = sorted({a for airports in at.values() for a in airports})
            note = Problem.of(
                "no_active_airport", attribute=attribute, side=m.get("airporttypes"), values=without, active=active
            )
            out[note] = None
    return list(out)
