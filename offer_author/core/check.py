"""Offline checks of model-written rule XML, all driven by data: the generated block reference (toolbox, connection
checks) and the tenant catalog (codes). It answers fast what the slower stock-UI compile would also reject."""
import json
import os
import xml.etree.ElementTree as ET

REF = os.path.join(os.path.dirname(__file__), "data", "block_reference.json")
NS = "{https://developers.google.com/blockly/xml}"
CATALOG_OBJECT = {"activity": "activity", "member": "member", "products_attrs": "product"}


def reference(canvas="rules"):
    return {b["type"]: b for b in json.load(open(REF))["canvases"][canvas]["toolbox"]}


def _checks(variants):
    """Union of a connection's check variants; None when any variant accepts anything."""
    out = set()
    for v in variants or []:
        if v is None:
            return None
        out |= set(v)
    return out


def _fits(a, b):
    return a is None or b is None or bool(a & b)


def _tag(el):
    return el.tag.split("}")[-1]


ROOTS = {"rules": "offer_generic", "segment": "targeting_holder"}


def check(xml, catalog=None, ref=None, canvas="rules"):
    """-> [problem strings]; empty when the XML is well-formed, toolbox-only, correctly nested and uses catalog codes."""
    ref = ref or reference(canvas)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        return [f"not well-formed XML: {e}"]
    out = []
    top = [b for b in root if _tag(b) == "block"]
    if [b.get("type") for b in top] != [ROOTS[canvas]]:
        out.append(f"the workspace must hold exactly one top-level {ROOTS[canvas]} block, found {[b.get('type') for b in top]}")

    def walk(block, slot_check, where):
        t = block.get("type")
        spec = ref.get(t)
        if spec is None:
            out.append(f"{where}: block type {t!r} is not in the {canvas} toolbox")
            return
        conn = spec["connections"]
        mine = _checks(conn["Output"]) if slot_check[0] == "value" else _checks(conn["PreviousStatement"])
        if slot_check[0] and not _fits(mine, slot_check[1]):
            out.append(f"{where}: {t} cannot plug in here (its checks {sorted(mine or [])}, the input accepts {sorted(slot_check[1] or [])})")
        out.extend(f"{where}: {t}: {p}" for p in codes(block, catalog))
        inputs = {i["name"]: i["check"] for i in spec["statement_inputs"]}
        values = {i["name"]: i["check"] for i in spec["value_inputs"]}
        for ch in block:
            kind = _tag(ch)
            if kind == "statement":
                n = ch.get("name")
                if n not in inputs:
                    out.append(f"{where}: {t} has no statement input {n!r} (has {sorted(inputs)})")
                for b in ch:
                    if _tag(b) == "block":
                        walk(b, ("statement", set(inputs[n]) if inputs.get(n) else None), f"{where}/{t}.{n}")
            elif kind == "value":
                n = ch.get("name")
                if n not in values:
                    out.append(f"{where}: {t} has no value input {n!r} (has {sorted(values)})")
                for b in ch:
                    if _tag(b) in ("block", "shadow"):
                        walk(b, ("value", set(values[n]) if values.get(n) else None), f"{where}/{t}.{n}")
            elif kind == "next":
                for b in ch:
                    if _tag(b) == "block":
                        walk(b, ("statement", _checks(conn["NextStatement"])), where)
            elif kind == "field" and spec["fields"] and ch.get("name") not in spec["fields"]:
                out.append(f"{where}: {t} has no field {ch.get('name')!r}")

    for b in top:
        walk(b, (None, None), "")
    return out


def codes(block, catalog):
    """Catalog codes a condition/action block names: attribute codes and coded values."""
    if catalog is None:
        return []
    m = next((c for c in block if _tag(c) == "mutation"), None)
    if m is None:
        return []
    out = []
    obj, level, attr = m.get("object"), m.get("attribute_level"), m.get("attribute")
    cobj = CATALOG_OBJECT.get(obj)
    counter = level in ("MA_O", "MA_A", "MA_TO")  # counters are named by the rules themselves
    if cobj and attr and not counter and level in (None, "", "MA_P", "null"):
        a = catalog.attr(cobj, attr)
        if a is None and not (cobj == "activity" and attr == "h_bit_type"):
            out.append(f"{obj} attribute {attr!r} is not in the tenant catalog")
        elif a is not None and a.values and m.get("operators") in ("in", "not_in", "==", "!="):
            vals = [v for v in (m.get("value") or "").split(",") if v]
            bad = [v for v in vals if v not in a.values]
            if bad:
                near = [c["code"] for v in bad for c in (catalog.lookup(cobj, attr, v).get("candidates") or [])[:3]]
                out.append(f"{attr} values {bad} are not codes of {attr}" + (f"; closest: {', '.join(dict.fromkeys(near))}" if near else "")
                           + " (use lookup_values)")
    if cobj == "activity" and attr == "h_bit_type" and m.get("value"):
        bad = [v for v in m.get("value").split(",") if v and v not in catalog.activity_types]
        if bad:
            out.append(f"activity types {bad} are not in the catalog")
    if block.get("type") == "action" and m.get("attribute_type_input") == "give_privilege":
        pid = next((f.text for f in block.iter(NS + "field") if f.get("name") == "SELECTED_PRIVILEGE"), None)
        if pid and str(pid) not in catalog.privileges:
            out.append(f"privilege {pid!r} is not in the catalog")
    return out
