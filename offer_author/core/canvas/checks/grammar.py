"""Offline checks of model-written canvas XML, all driven by data.

The generated block reference gives the toolbox and the connection checks, the tenant catalog the codes. This answers
fast what the slower stock-UI compile would also reject, and moves blocks that cannot plug in where they sit to the
loose blocks (detach).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ....domain import Block, Problem
from ...vocabulary import ATTRIBUTE_LEVELS, CATALOG_OBJECT
from ..reference import reference

if TYPE_CHECKING:
    from ...tenant.catalog import Catalog

NS = "{https://developers.google.com/blockly/xml}"
ROOTS = {"rules": "offer_generic", "segment": "targeting_holder"}

Spec = dict[str, Any]
Checks = set[str] | None
Slot = tuple[str | None, Checks]


def _checks(variants: Iterable[Any] | None) -> Checks:
    """Union of a connection's check variants; None when any variant accepts anything."""
    out: set[str] = set()
    for v in variants or []:
        if v is None:
            return None
        out |= set(v)
    return out


def _fits(a: Checks, b: Checks) -> bool:
    """Two connection checks fit (either accepts anything, or they share a type)."""
    return a is None or b is None or bool(a & b)


def _tag(el: ET.Element) -> str:
    """An element's tag without its namespace."""
    return el.tag.split("}")[-1]


def disabled(el: ET.Element) -> bool:
    """A block Blockly generates no code for: disabled by the user (or written so for a loose block)."""
    return el.get("disabled") == "true" or "MANUALLY_DISABLED" in (el.get("disabled-reasons") or "").split(",")


class _Walk:
    """One pass of check() over a canvas's blocks, collecting problems."""

    def __init__(self, ref: dict[str, Spec], catalog: Catalog | None, canvas: str) -> None:
        self.ref, self.catalog, self.canvas = ref, catalog, canvas
        self.out: list[Problem] = []

    def block(self, block: ET.Element, slot: Slot, where: str) -> None:
        """One block: in the toolbox, fits its slot, uses catalog codes; then what it holds."""
        t = block.get("type")
        spec = self.ref.get(t or "")
        if spec is None:
            self.out.append(Problem.of("not_in_toolbox", at=where, type=t, canvas=self.canvas))
            return
        conn = spec["connections"]
        mine = _checks(conn["Output"]) if slot[0] == "value" else _checks(conn["PreviousStatement"])
        if slot[0] and not _fits(mine, slot[1]):
            accepts = sorted(slot[1] or [])
            have = sorted(mine or [])
            self.out.append(Problem.of("cannot_plug_here", at=where, type=t, checks=have, accepts=accepts))
        self.out.extend(Problem.of("in_block", at=where, type=t, problem=p) for p in codes(block, self.catalog))
        self.children(block, spec, where)

    def children(self, block: ET.Element, spec: Spec, where: str) -> None:
        """A block's inputs, next block and fields."""
        t, conn = block.get("type"), spec["connections"]
        inputs = {i["name"]: i["check"] for i in spec["statement_inputs"]}
        values = {i["name"]: i["check"] for i in spec["value_inputs"]}
        for ch in block:
            kind = _tag(ch)
            if kind in ("statement", "value"):
                n, slots = ch.get("name"), inputs if kind == "statement" else values
                if n not in slots:
                    self.out.append(Problem.of("no_such_input", at=where, type=t, kind=kind, name=n, has=sorted(slots)))
                for b in ch:
                    if _tag(b) == "block" or (kind == "value" and _tag(b) == "shadow"):
                        self.block(b, (kind, set(slots[n]) if slots.get(n) else None), f"{where}/{t}.{n}")
            elif kind == "next":
                for b in ch:
                    if _tag(b) == "block":
                        self.block(b, ("statement", _checks(conn["NextStatement"])), where)
            elif kind == "field" and spec["fields"] and ch.get("name") not in spec["fields"]:
                self.out.append(Problem.of("no_such_field", at=where, type=t, name=ch.get("name")))


def check(
    xml: str, catalog: Catalog | None = None, ref: dict[str, Spec] | None = None, canvas: str = "rules"
) -> list[Problem]:
    """Checks a canvas's XML as the stock screen would.

    Args:
        xml: The canvas.
        catalog: The tenant catalog (codes are checked only with one).
        ref: The block reference (default: the canvas's).
        canvas: rules or segment.

    Returns:
        The problems; empty when the XML is well-formed, toolbox-only, correctly nested and uses catalog codes.
    """
    ref = ref or reference(canvas)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        return [Problem.of("not_well_formed", error=str(e))]
    walk = _Walk(ref, catalog, canvas)
    top = [b for b in root if _tag(b) == "block"]
    live = [b for b in top if not disabled(b)]
    if [b.get("type") for b in live] != [ROOTS[canvas]]:
        walk.out.append(Problem.of("top_level_blocks", root=ROOTS[canvas], found=[b.get("type") for b in live]))
    for b in top:
        walk.block(b, (None, None), "")
    return walk.out


def _conn(spec: Spec, name: str) -> tuple[str, Checks]:
    """A block's connection: ("none", None) when it has none, else ("some", its check set; None accepts anything)."""
    variants = (spec.get("connections") or {}).get(name)
    return ("none", None) if not variants else ("some", _checks(variants))


def misfit(spec: Spec, parent: Spec, name: str, previous: Spec | None = None) -> Problem | None:
    """Why a block cannot plug into an input of its parent, as Blockly would refuse it.

    Args:
        spec: The block's reference entry.
        parent: The parent's reference entry.
        name: The parent's input.
        previous: The reference entry of the block before it in a statement list.

    Returns:
        The problem, or None when it fits.
    """
    inputs = {i["name"]: (True, i["check"]) for i in parent.get("statement_inputs") or []}
    inputs.update({i["name"]: (False, i["check"]) for i in parent.get("value_inputs") or []})
    t, pt = spec["type"], parent.get("type", "?")
    if name not in inputs:
        return Problem.of("no_input", parent=pt, input=name, has=sorted(inputs))
    statement, check_ = inputs[name]
    has, mine = _conn(spec, "PreviousStatement" if statement else "Output")
    if has == "none":
        return Problem.of("not_a_statement_block" if statement else "not_a_value_block", type=t, parent=pt, input=name)
    accepts = set(check_) if check_ is not None else None
    if not _fits(mine, accepts):
        return Problem.of(
            "checks_do_not_fit", type=t, parent=pt, input=name, checks=sorted(mine or []), accepts=sorted(accepts or [])
        )
    if statement and previous is not None:
        phas, pnext = _conn(previous, "NextStatement")
        if phas == "none" or not _fits(pnext, mine):
            return Problem.of("cannot_follow", type=t, previous=previous["type"], parent=pt, input=name)
    return None


class _Detach:
    """One pass of detach(): moves the blocks that do not fit to `moved`."""

    def __init__(self, ref: dict[str, Spec]) -> None:
        self.ref = ref
        self.moved: list[dict[str, Any]] = []

    def walk(self, node: Block) -> None:
        """Detaches the misfits under one block, then walks what stays."""
        spec = self.ref.get(node.get("type") or "")
        if spec is None:
            return
        inputs = dict(node.get("inputs") or {})
        for name, child in list(inputs.items()):
            kids = child if isinstance(child, list) else [child]
            keep = self.fitting(spec, name, kids)
            for kid in keep:
                self.walk(kid)
            if len(keep) == len(kids):
                continue
            if keep:
                inputs[name] = keep if isinstance(child, list) else keep[0]
            else:
                inputs.pop(name)
        if inputs != (node.get("inputs") or {}):
            node["inputs"] = inputs

    def fitting(self, spec: Spec, name: str, kids: list[Any]) -> list[Any]:
        """The blocks of one input that fit it; the others are moved."""
        keep, prev = [], None
        for kid in kids:
            kspec = self.ref.get(kid.get("type") or "") if isinstance(kid, dict) else None
            why = misfit(kspec, spec, name, prev) if kspec else None
            if why:
                self.moved.append({"id": kid.get("id"), "type": kid.get("type"), "reason": why, "node": kid})
                continue
            keep.append(kid)
            prev = kspec or prev
        return keep


def detach(
    tops: list[Block], canvas: str = "rules", ref: dict[str, Spec] | None = None
) -> tuple[list[Block], list[dict[str, Any]]]:
    """Moves every block that cannot plug in where it sits, with what hangs under it, to the loose blocks.

    The blocks after it in a statement list stay there; blocks of unknown type stay too (check reports them).

    Returns:
        (the canvas's top-level blocks, [{id, type, reason}] for each block moved).
    """
    d = _Detach(ref or reference(canvas))
    tops = json.loads(json.dumps(tops))
    for t in tops:
        d.walk(t)
    for m in d.moved:  # what hangs under a moved block may not fit there either
        d.walk(m["node"])
    return tops + [m.pop("node") for m in d.moved], d.moved


def _value_codes(catalog: Catalog, cobj: str, obj: str | None, attr: str, m: ET.Element) -> list[Problem]:
    """An attribute condition's attribute and coded values against the tenant catalog."""
    a = catalog.attr(cobj, attr)
    if a is None:
        return [Problem.of("attribute_not_in_catalog", object=obj, attribute=attr)]
    if not a.values or m.get("operators") not in ("in", "not_in", "==", "!="):
        return []
    bad = [v for v in (m.get("value") or "").split(",") if v and v not in a.values]
    if not bad:
        return []
    near = dict.fromkeys(c["code"] for v in bad for c in (catalog.lookup(cobj, attr, v).get("candidates") or [])[:3])
    return [Problem.of("values_not_codes", attribute=attr, values=bad, closest=list(near))]


def codes(block: ET.Element, catalog: Catalog | None) -> list[Problem]:
    """The catalog codes a condition or action block names that the catalog does not have.

    Attribute codes and coded values (of rules and segment conditions), activity types and privileges are checked.
    Counters are named by the rules themselves, so a counter condition is not looked up.
    """
    m = next((c for c in block if _tag(c) == "mutation"), None) if catalog is not None else None
    if m is None or catalog is None:
        return []
    obj, level, attr = m.get("object"), m.get("attribute_level"), m.get("attribute")
    if obj is None and "attr_level" in m.attrib:  # a segment condition: a member attribute at attr_level
        obj, level = "member", m.get("attr_level")
    cobj = CATALOG_OBJECT.get(obj or "")
    out = _value_codes(catalog, cobj, obj, attr, m) if cobj and attr and level in ATTRIBUTE_LEVELS else []
    if block.get("type") == "action" and m.get("attribute_type_input") == "give_privilege":
        pid = next((f.text for f in block.iter(NS + "field") if f.get("name") == "SELECTED_PRIVILEGE"), None)
        if pid and str(pid) not in catalog.privileges:
            out.append(Problem.of("privilege_not_in_catalog", id=pid))
    return out
