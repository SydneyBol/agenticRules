"""Block tree -> Blockly XML: the XML the UI saves, mutations derived, loose blocks written disabled."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape, quoteattr

from ...domain import Block, Problem
from .fields import Counters, Spec, derive, field_text, fill, ui_fields
from .reference import reference
from .tree import TreeError

if TYPE_CHECKING:
    from ..tenant.catalog import Catalog

XMLNS = "https://developers.google.com/blockly/xml"
MUT_NS = "http://www.w3.org/1999/xhtml"
LOOSE_ATTRS = 'disabled-reasons="MANUALLY_DISABLED"'  # generates no code (drl.js), so the DRL stays as it is

Existing = Callable[[Block, list[Block], str], str | None]
Keep = Callable[[str, Any], str | None]


def loose_attrs(i: int) -> str:
    """Start-tag attributes of the i-th loose block: disabled, to the right of the rules, one under the other."""
    return f' x="900" y="{40 + 160 * i}" {LOOSE_ATTRS}'


def own_xml(
    node: Block, spec: Spec, catalog: Catalog | None = None, counters: Counters | None = None
) -> tuple[Block, list[str]]:
    """A block's own XML as the UI writes it: its mutation and fields, after fill, derive and ui_fields.

    Returns:
        (the node with its fields as written, [xml parts]).
    """
    mut = dict(node.get("mutation") or {})
    if spec:
        filled = fill(node, catalog, counters)
        if list(filled.items()) != list((node.get("fields") or {}).items()):  # order counts: read in order
            node = dict(node, fields=filled)
        mut = derive(node, spec, catalog, counters)
        fixed = ui_fields(node, mut, counters, catalog)
        if fixed is not (node.get("fields") or {}):
            node = dict(node, fields=fixed)
            mut = derive(node, spec, catalog, counters)
    parts = []
    if mut:
        order = spec.get("mutation_order") or []
        keys = [k for k in order if k in mut] + [k for k in mut if k not in order]
        parts.append(
            f'<mutation xmlns="{MUT_NS}"'
            + "".join(f" {k}={quoteattr(field_text(mut[k]))}" for k in keys)
            + "></mutation>"
        )
    for k, v in (node.get("fields") or {}).items():
        parts.append(f"<field name={quoteattr(k)}>{escape(field_text(v))}</field>")
    return node, parts


def label(node: Block, where: str) -> str:
    """Where a block sits, as problems name it: its parent's path, its type and its rule name if it has one."""
    t = node.get("type")
    here = str(t) + (f" '{node['fields']['IDENTIFIER']}'" if (node.get("fields") or {}).get("IDENTIFIER") else "")
    return f"{where} > {here}" if where else here


class Writer:
    """Writes blocks the way to_xml does.

    Attributes:
        paths: Every block id written, with where it sits.
    """

    def __init__(
        self,
        catalog: Catalog | None = None,
        counters: Counters | None = None,
        canvas: str = "rules",
        existing: Existing | None = None,
    ) -> None:
        """Writes for one canvas.

        Args:
            catalog: The tenant catalog.
            counters: The offer counters the canvas declares, by name.
            canvas: rules or segment.
            existing: May answer a block and its chain itself, (node, rest, where) -> text or None (the saved
                canvas's own text, canvas/edit.py).
        """
        self.ref, self.catalog, self.counters, self.existing = reference(canvas), catalog, counters, existing
        self.paths: dict[str, str] = {}
        self.n = 0

    def bid(self, node: Block) -> str:
        """The block's id: its own, else b<n> in writing order."""
        self.n += 1
        return str(node.get("id") or f"b{self.n}")

    def chain(self, nodes: list[Block], where: str, attrs: str = "") -> str:
        """A chain of blocks, each the next of the one before."""
        if not nodes:
            return ""
        if self.existing:
            done = self.existing(nodes[0], nodes[1:], where)
            if done is not None:
                return done
        head = self.block(nodes[0], where, attrs)
        rest = self.chain(nodes[1:], where)
        return head + (f"<next>{rest}</next>" if rest else "") + "</block>"

    def block(self, node: Block, where: str, attrs: str = "") -> str:
        """The block's start tag, own parts and inputs, without its closing tag.

        Raises:
            TreeError: The node is not a block with a type.
        """
        if not isinstance(node, dict) or not node.get("type"):
            raise TreeError(Problem.of("block_without_type", at=where))
        t = node["type"]
        spec = self.ref.get(t, {})
        bid = self.bid(node)
        here = label(node, where)
        self.paths[bid] = here
        node, parts = own_xml(node, spec, self.catalog, self.counters)
        return (
            f"<block type={quoteattr(t)} id={quoteattr(bid)}{attrs}>" + "".join(parts) + self.inputs(node, spec, here)
        )

    def inputs(self, node: Block, spec: Spec, here: str, keep: Keep | None = None) -> str:
        """The block's statement and value inputs; keep(name, content) may answer one input's saved text.

        Raises:
            TreeError: A value input holds more than one block.
        """
        statements, out = {i["name"] for i in spec.get("statement_inputs") or []}, []
        for name, child in (node.get("inputs") or {}).items():
            kept = keep(name, child) if keep else None
            if kept is not None:
                out.append(kept)
                continue
            kids = child if isinstance(child, list) else [child]
            if name in statements:
                out.append(f"<statement name={quoteattr(name)}>{self.chain(kids, f'{here}.{name}')}</statement>")
            else:
                if len(kids) != 1:
                    raise TreeError(Problem.of("value_input_takes_one", at=here, input=name))
                out.append(f"<value name={quoteattr(name)}>" + self.chain(kids, f"{here}.{name}") + "</value>")
        return "".join(out)


def to_xml(
    root: Block | list[Block], catalog: Catalog | None = None, counters: Counters | None = None, canvas: str = "rules"
) -> tuple[str, dict[str, str]]:
    """A canvas as Blockly XML.

    Args:
        root: The root block, or the canvas's top-level blocks [root, loose...]; loose blocks are written disabled.
        catalog: The tenant catalog.
        counters: The offer counters the rules use, {name: {data_type, counter_type, target_type?, namespace?,
            entity_level?}}.
        canvas: rules or segment.

    Returns:
        (xml, paths): paths maps every block id to where it sits, for readable problem reports.

    Raises:
        TreeError: No root, or a block the writer cannot write.
    """
    tops = root if isinstance(root, list) else [root]
    if not tops:
        raise TreeError(Problem.of("no_root"))
    w = Writer(catalog, counters, canvas)
    body = w.chain([tops[0]], "") + "".join(w.chain([b], "loose", loose_attrs(i)) for i, b in enumerate(tops[1:]))
    return f'<xml xmlns="{XMLNS}">{body}</xml>', w.paths
