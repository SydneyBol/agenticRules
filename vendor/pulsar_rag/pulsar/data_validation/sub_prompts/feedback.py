"""What the manager changed in Pulsar's draft before saving the offer: the fine-tune rows of failed_blocks_filter.

Pulsar compared the two offers' sub-prompt lines as one list of texts (prompt_validator.failed_blocks_filter): a line
counted as produced by the model when it occurred twice anywhere (so a line the saved offer repeats in two rules was
never a miss), labels were dropped (a block in the wrong rule or OR branch counted as right) and lines the model wrote
in excess were not kept. Here each line is keyed by its place on the canvas, read from the XML: its On Activity / On
Event, Filter or rule (and the rules it is nested in), and its OR branches. Units (a Filter, a rule) are matched
between the two offers by their lines, whatever their order, and lines are compared as counts, so a repeated line is a
miss as often as it is missing.
"""
import re
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Dict, List, Optional, Tuple

RULES = ("rule", "rule_line", "payment_rule")
CONTAINERS = ("activity", "event")

Pair = Tuple[str, str]  # (Pulsar's line, the block's XML), as xml_to_subprompt returns them
Place = Tuple[int, str, int, Tuple[str, ...]]  # (container no., "Filter" | rule kind, nesting depth, OR branches)


def _tag(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


class _Walk:
    """Every block's place: its container, its rule path, its OR branches (numbered within its unit)."""

    def __init__(self):
        self.places: Dict[str, Tuple[int, Tuple[str, ...], Tuple[Tuple[int, int], ...]]] = {}
        self.containers = 0
        self.rules = 0
        self.ors = 0

    def block(self, el, container, rules, ors):
        kind = el.get("type") or ""
        if kind in CONTAINERS:
            self.containers += 1
            container, rules, ors = self.containers, (), ()
        elif kind in RULES:
            self.rules += 1
            rules, ors = (*rules, f"{kind}#{self.rules}"), ()
        elif kind == "or":
            self.ors += 1
            n = self.ors
            for child in el:
                if _tag(child) == "statement" and child.get("name") in ("OPERAND1", "OPERAND2"):
                    self.chain(child, container, rules, (*ors, (n, int(child.get("name")[-1]))))
            self.next_of(el, container, rules, ors)
            return
        if el.get("id"):
            self.places[el.get("id")] = (container, rules, ors)
        for child in el:
            if _tag(child) in ("statement", "value"):
                self.chain(child, container, rules, ors)
        self.next_of(el, container, rules, ors)

    def chain(self, slot, container, rules, ors):
        for child in slot:
            if _tag(child) == "block":
                self.block(child, container, rules, ors)

    def next_of(self, el, container, rules, ors):
        for child in el:
            if _tag(child) == "next":
                if el.get("type") in CONTAINERS:
                    self.chain(child, 0, (), ())
                elif el.get("type") in RULES:
                    self.chain(child, container, rules[:-1], ors)
                else:
                    self.chain(child, container, rules, ors)


def _text(line: str) -> str:
    """The line without Pulsar's running label (Filter_N,,, / rule_N,,,)."""
    return line.split(",,,", 1)[1].strip() if ",,," in line else line.strip()


def units(xml: str, pairs: List[Pair]) -> Optional[Dict[Tuple, Counter]]:
    """The offer's units (each Filter, each rule) with the count of each (OR branches, line text) in it.

    A unit is keyed by (container, "Filter") or (container, rule path); None when the XML cannot be read or a line's
    block is not in it.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    walk = _Walk()
    for top in root:
        if _tag(top) == "block":
            walk.block(top, 0, (), ())
    out: Dict[Tuple, Counter] = {}
    order: Dict[Tuple, Dict[int, int]] = {}
    for line, block in pairs:
        m = re.search(r'\bid="([^"]+)"', block)
        bid = m.group(1) if m else ""
        if bid not in walk.places:
            return None
        container, rules, ors = walk.places[bid]
        unit = (container, "Filter") if 'type="activity_criteria"' in block[:80] else (container, rules)
        seen = order.setdefault(unit, {})  # OR numbers within the unit, by first appearance: offers number them apart
        branches = tuple((seen.setdefault(n, len(seen) + 1), b) for n, b in ors)
        out.setdefault(unit, Counter())[(branches, _text(line))] += 1
    return out


def _shape(unit: Tuple) -> Tuple:
    """What two units must share to be the same unit: the container, and Filter or the rule's kind and depth."""
    container, where = unit
    return (container, where) if where == "Filter" else (container, tuple(r.split("#")[0] for r in where))


def compare(generated: Dict[Tuple, Counter], saved: Dict[Tuple, Counter]) -> Tuple[List[Tuple], List[Tuple]]:
    """(missing, extra): the (unit, branches, text) the saved offer has and the draft lacks, and the reverse.

    Each saved unit is paired with the draft unit of the same shape that shares most lines with it (each draft unit
    once); an unpaired unit's lines are all missing (saved) or all extra (draft).
    """
    free = dict(generated)
    missing, extra = [], []
    for unit, lines in sorted(saved.items(), key=lambda kv: -sum(kv[1].values())):
        best = max((u for u in free if _shape(u) == _shape(unit)),
                   key=lambda u: sum((free[u] & lines).values()), default=None)
        mine = free.pop(best) if best is not None else Counter()
        missing += [(unit, *k) for k, n in (lines - mine).items() for _ in range(n)]
        extra += [(best, *k) for k, n in (mine - lines).items() for _ in range(n)]
    for unit, lines in free.items():
        extra += [(unit, *k) for k, n in lines.items() for _ in range(n)]
    return missing, extra


def place_label(unit: Tuple, branches: Tuple) -> str:
    """A readable place: "activity 1 Filter, OR 1 branch 2" or "activity 1 rule inside rule"."""
    container, where = unit
    head = f"activity {container}" if container else "offer"
    body = "Filter" if where == "Filter" else " inside ".join(r.split("#")[0] for r in reversed(where))
    return f"{head} {body}" + "".join(f", OR {n} branch {b}" for n, b in branches)
