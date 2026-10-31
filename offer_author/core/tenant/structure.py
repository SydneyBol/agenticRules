"""Where each line of Pulsar's sub-prompt sits on the canvas.

Its activity or event, its rule, the rule it is nested in, and the OR branch it is on. Pulsar writes one line per
block (a Filter condition, an If condition, an action) and labels it with a running number (Filter_N, rule_N) that
counts every Filter condition and every rule of the offer. The structure is not in the lines: an OR block's branches
read as conditions chained one under another (AND), a rule inside a rule reads as the next rule, two conditions of one
Filter read as two Filters, and nothing says which On Activity or On Event a rule runs under. So an earlier offer with
an OR, a nested rule or two activities would teach the opposite of what it does. `relabel` keeps each line's text and
gives it the label of its place on the canvas, read from the saved XML.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..canvas.checks.grammar import _tag

RULES = ("rule", "rule_line", "payment_rule")
CONTAINERS = ("activity", "event")
FORM = 2  # the stored sub-prompt's form: 2 = relabelled here (1, or none, = Pulsar's own labels)


@dataclass
class _Place:
    """Where a block sits: its container, its rule (and the rules it is nested in), its OR branches."""

    container: str = ""
    rules: tuple[str, ...] = ()
    ors: tuple[str, ...] = ()


@dataclass
class _Walk:
    """One pass over the canvas, numbering containers, rules and ORs in canvas order."""

    places: dict[str, _Place] = field(default_factory=dict)
    kinds: dict[str, str] = field(default_factory=dict)
    count: dict[str, int] = field(default_factory=dict)

    def next(self, kind: str) -> int:
        self.count[kind] = self.count.get(kind, 0) + 1
        return self.count[kind]

    def block(self, el: ET.Element, at: _Place) -> None:
        kind = el.get("type") or ""
        if kind in CONTAINERS:
            at = _Place(f"{kind}_{self.next(kind)}")
        elif kind in RULES:
            at = _Place(at.container, (*at.rules, f"{kind}_{self.next('rule')}"))
        elif kind == "or":
            n = self.next("or")
            for child in el:
                if _tag(child) == "statement" and child.get("name") in ("OPERAND1", "OPERAND2"):
                    branch = f"OR_{n} branch {child.get('name', '')[-1]}"
                    self.chain(child, _Place(at.container, at.rules, (*at.ors, branch)))
            self.next_of(el, at)
            return
        if el.get("id"):
            self.places[el.get("id", "")], self.kinds[el.get("id", "")] = at, kind
        for child in el:
            if _tag(child) in ("statement", "value"):
                self.chain(child, at)
        self.next_of(el, at)

    def chain(self, slot: ET.Element, at: _Place) -> None:
        for child in slot:
            if _tag(child) == "block":
                self.block(child, at)

    def next_of(self, el: ET.Element, at: _Place) -> None:
        """The block chained under this one: same place, outside this block's own rule or OR."""
        for child in el:
            if _tag(child) == "next":
                self.chain(child, at if (el.get("type") not in RULES + CONTAINERS) else _outer(at, el))


def _outer(at: _Place, el: ET.Element) -> _Place:
    """The place of the block chained under a rule or container: the rule's siblings share its container."""
    if el.get("type") in CONTAINERS:
        return _Place()
    return _Place(at.container, at.rules[:-1], at.ors)


def _label(at: _Place, filter_line: bool, containers: int) -> str:
    where = [at.container] if containers > 1 else []
    if filter_line:
        where.append("Filter")
    elif at.rules:
        where.append(at.rules[-1] + "".join(f" inside {r}" for r in reversed(at.rules[:-1])))
    return " ".join(where) + "".join(f", {o}" for o in at.ors)


def relabel(xml: str, lines: list[tuple[str, str]]) -> str | None:
    """Pulsar's lines with the label of each line's place on the canvas.

    Args:
        xml: The saved rules XML the lines were made from.
        lines: Pulsar's (line, block XML) pairs, as xml_to_subprompt returns them.

    Returns:
        One line per pair, "<label>, <Pulsar's text>": the label is the rule ("rule_2", "rule_3 inside rule_2"), or
        "Filter", preceded by the activity or event when the offer has more than one, followed by the OR branches the
        block is on ("OR_1 branch 2"); None when a block cannot be found in the XML.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    walk = _Walk()
    for top in root:
        if _tag(top) == "block":
            walk.block(top, _Place())
    containers = walk.count.get("activity", 0) + walk.count.get("event", 0)
    out = []
    for line, block in lines:
        m = re.search(r'\bid="([^"]+)"', block)  # the block's own id: the first in its XML
        bid = m.group(1) if m else ""
        if bid not in walk.places:
            return None
        text = line.split(",", 1)[1].strip() if "," in line else line.strip()
        out.append(f"{_label(walk.places[bid], walk.kinds[bid] == 'activity_criteria', containers)}, {text}")
    return "\n".join(out)
