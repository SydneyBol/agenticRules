"""Block trees as the model writes them: numbering, edits by block id, defs, and the compact form.

Node: {"type", "id"?, "fields": {name: value}, "picked"?: "CODE1,CODE2", "inputs": {name: node | [nodes]}}.
The model writes the compact form, fields and inputs directly in the node ({"type", "attribute": ..., "RHS_INPUT":
{...}}): `expand` turns it into the node form and `compact` writes saved rules back in it. A statement input holds a
list (the blocks chained under it, in order); a value input holds one node.
"""

from __future__ import annotations

import json
from typing import Any

from ...domain import Block, Problem
from ..vocabulary import OFFER_COUNTER
from .fields import CONDITIONS, counter_ref, field_text

Holder = dict[str, Any] | list[Any] | None
Located = tuple[Block, Holder, Any]


class TreeError(ValueError):
    """A block tree, edit or def the tools cannot read; carries the Problem that says why."""

    def __init__(self, problem: Problem) -> None:
        """Raised with the Problem the model reads."""
        super().__init__(problem.code)
        self.problem = problem


def nodes(root: Block | list[Block]) -> list[Located]:
    """Every node of a tree (or of a canvas's top-level blocks), depth first, with the input that holds it.

    Returns:
        [(node, container, key)]: the container a dict (a value input) or a list (a statement input), None for a
        top-level block.
    """
    if isinstance(root, list):
        return [x for top in root for x in nodes(top)]
    out: list[Located] = []

    def walk(node: Any, holder: Holder, key: Any) -> None:  # depth first, in the order to_xml writes the blocks
        if not isinstance(node, dict):
            return
        out.append((node, holder, key))
        for name, child in (node.get("inputs") or {}).items():
            if isinstance(child, list):
                for i, c in enumerate(child):
                    walk(c, child, i)
            else:
                walk(child, node["inputs"], name)

    walk(root, None, None)
    return out


def number(root: Any) -> Any:
    """A copy of the tree with an id on every block.

    The model's own ids are kept, the rest get the next free b<n>. Problems name blocks by these ids, and compile edits
    refer to them.
    """
    root = json.loads(json.dumps(root))
    found = [n for n, _, _ in nodes(root)]
    used, k = {str(n["id"]) for n in found if n.get("id")}, 0
    for n in found:  # the root first
        if not n.get("id"):
            k += 1
            while f"b{k}" in used:
                k += 1
            n["id"] = f"b{k}"
            used.add(n["id"])
    return root


EDIT_KEYS = ("id", "fields", "picked", "replace", "remove", "insert_after", "insert_before", "append")


def _as_list(v: Any) -> list[Block]:
    """One block or a list of them, expanded, as a list."""
    return [expand(dict(b)) for b in (v if isinstance(v, list) else [v])]


PLACING = ("insert_before", "insert_after", "append")


def _find(tops: list[Block], bid: Any) -> tuple[Block, dict[str, Any] | list[Any], Any]:
    """The block bid in the top-level blocks, with its holder and key (a top-level block's holder is tops itself).

    Raises:
        TreeError: No block has that id.
    """
    for i, top in enumerate(tops):
        for n, h, k in nodes(top):
            if str(n.get("id")) == str(bid):
                return (n, tops, i) if h is None else (n, h, k)
    raise TreeError(Problem.of("no_block", id=str(bid)))


def _place(e: dict[str, Any], node: Block, holder: Any, key: Any, is_root: bool) -> Any:
    """Applies an edit's insert_before, insert_after and append.

    insert_before / insert_after put blocks next to the block in its statement list; append puts blocks at the end of
    one of its statement inputs.

    Returns:
        The block's key in its holder afterwards.

    Raises:
        TreeError: The block is in no statement list, or the input appended to is a value input.
    """
    for where, at in (("insert_before", 0), ("insert_after", 1)):
        if e.get(where) is not None:
            if not isinstance(holder, list) or is_root:
                raise TreeError(Problem.of("not_in_statement_list", edit=where, id=str(e["id"])))
            new = _as_list(e[where])
            holder[key + at : key + at] = new
            key += len(new) if at == 0 else 0
    for name, blocks in (e.get("append") or {}).items():
        have = node.setdefault("inputs", {}).get(name)
        if have is not None and not isinstance(have, list):
            raise TreeError(Problem.of("append_to_value_input", input=name, id=str(e["id"])))
        node["inputs"][name] = (have or []) + _as_list(blocks)
    return key


def _change(e: dict[str, Any], node: Block, holder: Any, key: Any) -> None:
    """An edit's replace (the block and what hangs under it), fields and inputs (merged), and picked codes."""
    if e.get("replace") is not None:
        node = holder[key] = expand(dict(e["replace"]))
    own = expand({"type": "-", **{k: v for k, v in e.items() if k not in EDIT_KEYS}})
    fields = {**(e.get("fields") or {}), **own.get("fields", {})}
    if fields:
        node["fields"] = {**(node.get("fields") or {}), **fields}
    if own.get("inputs"):
        node["inputs"] = {**(node.get("inputs") or {}), **own["inputs"]}
    # a block's value is either its picked codes or its value field, never both: the XML writes the field as the value
    # and the picked codes' labels, so a stale one left by an edit made them disagree (2026-11-05 X07: value "",
    # labels "Shopping,Spend"); what the edit gives wins
    if "picked" in e:
        node["picked"] = e["picked"]
        (node.get("fields") or {}).pop("value", None)
    elif "value" in fields and node.get("picked") not in (None, ""):
        node["picked"] = field_text(node["fields"].pop("value"))


def apply_edits(root: Block | list[Block], edits: list[Any] | None) -> Any:
    """The last tree with edits applied, by block id.

    {"id", "fields": {...}} merges fields, "picked" replaces the picked codes, "replace": node swaps the block (and what
    hangs under it), "remove": true drops it, "insert_after" / "insert_before": [blocks] puts blocks next to it in its
    statement list, "append": {INPUT: [blocks]} adds blocks to the end of one of its statement inputs. Any other key is
    a field or an input of that block, as in the compact form ({"id": "b9", "operators": ">"}).

    Args:
        root: One root block, or the canvas's top-level blocks [root, loose...] (then a list comes back).
        edits: The edits, in order.

    Returns:
        The edited copy.

    Raises:
        TreeError: An edit without an id, for no block, removing the root, or placing blocks where they cannot go.
    """
    tops = json.loads(json.dumps(root if isinstance(root, list) else [root]))
    for e in edits or []:
        if not isinstance(e, dict) or not e.get("id"):
            raise TreeError(Problem.of("edit_without_id"))
        node, holder, key = _find(tops, e["id"])
        is_root = holder is tops and key == 0
        if e.get("remove"):
            if is_root:
                raise TreeError(Problem.of("remove_root"))
            holder.pop(key)
            continue
        key = _place(e, node, holder, key, is_root)
        if set(e) - {"id", *PLACING} or not any(e.get(k) is not None for k in PLACING):
            _change(e, node, holder, key)
    return tops if isinstance(root, list) else tops[0]


NODE_KEYS = ("type", "id", "picked", "mutation")


def _blocks(v: Any) -> bool:
    """The value is a block or a list of blocks (an input), not a field value."""
    return isinstance(v, dict) or (isinstance(v, list) and all(isinstance(x, dict) for x in v))


def expand(root: Any) -> Any:
    """The model's compact nodes in the node form.

    In a compact node a block or a list of blocks is an input and any other value a field (the action block has a field
    and an input of the same name: the value says which); a list of plain values is one field, comma-separated. A node
    already in the node form is kept as it is.
    """
    if isinstance(root, list):
        return [expand(n) for n in root]
    if not isinstance(root, dict):
        return root
    out = {k: root[k] for k in NODE_KEYS if k in root}
    fields = dict(root["fields"]) if isinstance(root.get("fields"), dict) else {}
    inputs = {k: expand(v) for k, v in root["inputs"].items()} if isinstance(root.get("inputs"), dict) else {}
    for k, v in root.items():
        if k in NODE_KEYS or (k in ("fields", "inputs") and isinstance(v, dict)):
            continue
        if _blocks(v):
            inputs[k] = expand(v)
        else:
            fields[k] = ",".join(map(str, v)) if isinstance(v, list) else v
    if fields:
        out["fields"] = fields
    if inputs:
        out["inputs"] = inputs
    return out


def inline(root: Any, defs: Any = None) -> Any:
    """The tree with each {"use": name} replaced by a copy of defs[name].

    A def is a block, or blocks spliced into a statement list; defs may use other defs. A block written once and used
    in several places (a repeated value or condition) becomes ordinary blocks here, before ids and XML.

    Raises:
        TreeError: defs is not a mapping, names no such def, a def uses itself, or several blocks go in one place.
    """
    defs = defs or {}
    if not isinstance(defs, dict):
        raise TreeError(Problem.of("defs_not_a_mapping"))
    return _Inliner(defs).sub(root, frozenset())


def _use(n: Any) -> bool:
    """The value is a {"use": name} reference to a def."""
    return isinstance(n, dict) and set(n) == {"use"}


class _Inliner:
    """Replaces {"use": name} references with copies of the defs they name (inline)."""

    def __init__(self, defs: dict[str, Any]) -> None:
        self.defs = defs

    def resolve(self, name: str, seen: frozenset[str]) -> Any:
        """A copy of one def, its own references replaced."""
        if name not in self.defs:
            raise TreeError(Problem.of("no_def", name=name, known=list(self.defs)))
        if name in seen:
            raise TreeError(Problem.of("def_uses_itself", name=name))
        return self.sub(json.loads(json.dumps(self.defs[name])), seen | {name})

    def sub(self, n: Any, seen: frozenset[str]) -> Any:
        """The value with every reference replaced: a list def is spliced into a list, a block def takes its place."""
        if isinstance(n, list):
            out = []
            for x in n:
                v = self.resolve(x["use"], seen) if _use(x) else self.sub(x, seen)
                out.extend(v if _use(x) and isinstance(v, list) else [v])
            return out
        if not isinstance(n, dict):
            return n
        if _use(n):
            v = self.resolve(n["use"], seen)
            if isinstance(v, list):
                if len(v) != 1:
                    raise TreeError(Problem.of("def_is_several_blocks", name=n["use"]))
                v = v[0]
            return v
        return {
            k: self.sub(v, seen) if isinstance(v, (dict, list)) and k not in ("fields", "mutation") else v
            for k, v in n.items()
        }


def compact(root: Any, counters: Any = ()) -> Any:
    """The node form in the compact form, without the fields `fill` gives back without the catalog.

    This is the offer's saved rules as the model would send them. A give_points action keeps its account and reward
    tag: they are the offer's choice, not something filled in.
    """
    if isinstance(root, list):
        return [compact(n, counters) for n in root]
    t, f, inputs = root["type"], dict(root.get("fields") or {}), dict(root.get("inputs") or {})
    _drop_filled(t, f, inputs, {c["name"]: c for c in counters})
    out = {
        "type": t,
        **({"id": root["id"]} if root.get("id") else {}),
        **f,
        **({"picked": root["picked"]} if root.get("picked") not in (None, "") else {}),
    }
    out.update({k: compact(v, counters) for k, v in inputs.items()})
    return out


def _drop_filled(t: str, f: dict[str, Any], inputs: dict[str, Any], byname: dict[str, Any]) -> None:
    """Drops, in place, the fields and inputs `fill` gives back.

    These are a condition's OBJECT and attribute_level, give_points' account and tags, and update_attribute's
    mta_type_value.
    """
    if t in CONDITIONS:
        counter = byname.get(f.get("attribute") or "")
        if counter and f.get("attribute_level") == (counter.get("level") or OFFER_COUNTER):
            f.pop("attribute_level")
        if f.get("OBJECT") == "member" and (counter or "attribute_level" in f):
            f.pop("OBJECT")
    elif t == "action" and f.get("action_type") == "update_attribute":
        counter = byname.get(f.get("mta_attribute") or "")
        if counter and f.get("mta_type_value") == (counter.get("level") or OFFER_COUNTER):
            f.pop("mta_type_value")


def select(root: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Blocks read from XML (with their mutations) in the form the model writes.

    Returns:
        (tree, counters): fields, picked codes and inputs per block, and the offer counters the blocks refer to; every
        other mutation attribute is left for `derive`.
    """
    counters: dict[str, dict[str, Any]] = {}

    def walk(n: Any) -> Any:
        if isinstance(n, list):
            return [walk(c) for c in n]
        n = dict(n)
        m = n.pop("mutation", None) or {}
        if "value" in m and "value" not in (n.get("fields") or {}):
            n["picked"] = m["value"]
        level, name = counter_ref(m)
        if name and (m.get("type") or m.get("attr_type")):
            c = counters.setdefault(
                name, {"name": name, "level": level, "data_type": m.get("type") or m.get("attr_type")}
            )
            if m.get("target_type"):
                c["target_type"] = m["target_type"]
            if m.get("vector_counter_namespace"):
                c.update(counter_type="VECTOR", namespace=m["vector_counter_namespace"])
                if m.get("attribute_entity_map_level"):
                    c["entity_level"] = m["attribute_entity_map_level"]
            c.setdefault("counter_type", "REGULAR")
        n["inputs"] = {k: walk(v) for k, v in (n.get("inputs") or {}).items()}
        if not n["inputs"]:
            n.pop("inputs")
        return n

    return walk(root), list(counters.values())
