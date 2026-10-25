"""Block tree <-> Blockly XML, generically: the model describes blocks as data and this writes the XML the UI saves.

Node: {"type", "id"?, "mutation": {attr: value}, "fields": {name: value}, "inputs": {name: node | [nodes]}}
A statement input holds a list (the blocks chained under it, in order); a value input holds one node. Which is which,
and the order of mutation attributes (the upload dialog finds counters by regex over that order), come from the block
reference generated from the UI source. No block is special-cased here.
"""
import json
import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

REF = os.path.join(os.path.dirname(__file__), "assets", "block_reference.json")
XMLNS = "https://developers.google.com/blockly/xml"
MUT_NS = "http://www.w3.org/1999/xhtml"

_REF = None


def reference():
    global _REF
    if _REF is None:
        _REF = {b["type"]: b for b in json.load(open(REF))["toolbox"]}
    return _REF


class TreeError(ValueError):
    pass


def to_xml(root):
    """-> (xml, paths): paths maps every block id to where it sits, for readable problem reports."""
    ref, paths, n = reference(), {}, [0]
    if isinstance(root, list):
        if len(root) != 1:
            raise TreeError("the tree must have exactly one root block (offer_generic)")
        root = root[0]

    def block(node, where):
        if not isinstance(node, dict) or not node.get("type"):
            raise TreeError(f"{where}: every block needs a type")
        t = node["type"]
        spec = ref.get(t, {})
        n[0] += 1
        bid = str(node.get("id") or f"b{n[0]}")
        label = t + (f" '{node['fields']['IDENTIFIER']}'" if (node.get("fields") or {}).get("IDENTIFIER") else "")
        here = f"{where} > {label}" if where else label
        paths[bid] = here
        parts = [f"<block type={quoteattr(t)} id={quoteattr(bid)}>"]
        mut = node.get("mutation") or {}
        if mut:
            order = spec.get("mutation_order") or []
            keys = [k for k in order if k in mut] + [k for k in mut if k not in order]
            parts.append(f'<mutation xmlns="{MUT_NS}"' + "".join(f" {k}={quoteattr(_text(mut[k]))}" for k in keys) + "></mutation>")
        for k, v in (node.get("fields") or {}).items():
            parts.append(f"<field name={quoteattr(k)}>{escape(_text(v))}</field>")
        statements = {i["name"] for i in spec.get("statement_inputs") or []}
        for name, child in (node.get("inputs") or {}).items():
            kids = child if isinstance(child, list) else [child]
            if name in statements:
                parts.append(f"<statement name={quoteattr(name)}>{chain(kids, f'{here}.{name}')}</statement>")
            else:
                if len(kids) != 1:
                    raise TreeError(f"{here}: value input {name} takes exactly one block")
                parts.append(f"<value name={quoteattr(name)}>" + "".join(block(kids[0], f"{here}.{name}")) + "</block></value>")
        return parts

    def chain(nodes, where):
        if not nodes:
            return ""
        head = block(nodes[0], where)
        rest = chain(nodes[1:], where)
        return "".join(head) + (f"<next>{rest}</next>" if rest else "") + "</block>"

    body = chain([root], "")
    return f'<xml xmlns="{XMLNS}">{body}</xml>', paths


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def from_xml(xml):
    """Blockly XML -> the root block as a tree (ids and canvas positions dropped)."""
    root = ET.fromstring(xml)
    tag = lambda e: e.tag.split("}")[-1]  # noqa: E731

    def node(b):
        out = {"type": b.get("type")}
        for ch in b:
            k = tag(ch)
            if k == "mutation":
                out["mutation"] = dict(ch.attrib)
            elif k == "field":
                out.setdefault("fields", {})[ch.get("name")] = ch.text or ""
            elif k == "statement":
                out.setdefault("inputs", {})[ch.get("name")] = chain(next((c for c in ch if tag(c) == "block"), None))
            elif k == "value":
                kid = next((c for c in ch if tag(c) in ("block", "shadow")), None)
                if kid is not None:
                    out.setdefault("inputs", {})[ch.get("name")] = node(kid)
        return out

    def chain(b):
        out = []
        while b is not None:
            out.append(node(b))
            nxt = next((c for c in b if tag(c) == "next"), None)
            b = next((c for c in nxt if tag(c) == "block"), None) if nxt is not None else None
        return out

    tops = [b for b in root if tag(b) == "block"]
    return node(tops[0]) if len(tops) == 1 else [node(b) for b in tops]
