"""A saved canvas, edited without re-serialising it: every block the model does not change keeps its saved text.

Re-serialising saved Blockly XML (ElementTree) changed what the stock screen loaded: a condition lost its fields. So
the saved text is parsed with byte offsets into elements that keep their original start tag, end tag and the text
between their children, and an element nothing touches is written back as those bytes.

    Doc(xml).view()  -> {tops, counters}: the top-level blocks [root, loose...] in node form, short ids b1.. (document
                        order), and the offer counters the blocks refer to: what the model edits
    Doc(xml).write(tops, ...) -> the canvas with the edited tree: unchanged blocks, inputs and chains as saved, changed
                        blocks' own parts (mutation, fields) and new blocks written as build.to_xml writes them, new
                        loose blocks disabled at the top level
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from xml.parsers import expat

from ...domain import Block
from ..vocabulary import OFFER_COUNTER
from .build import Writer, label, loose_attrs, own_xml
from .fields import Counters
from .reference import reference
from .tree import nodes, select

if TYPE_CHECKING:
    from ..tenant.catalog import Catalog

OWN = ("mutation", "field")  # a block's own parts, regenerated when its fields change
INPUTS = ("statement", "value")


class El:
    """One element: its original start tag, end tag ('' when self-closing) and the text between its children."""

    __slots__ = ("attrs", "chars", "end", "gaps", "kids", "start", "tag")

    def __init__(self, tag: str, attrs: dict[str, str], start: str) -> None:
        """An element read from its start tag; the parser adds the rest."""
        self.tag, self.attrs, self.start, self.end = tag, attrs, start, ""
        self.kids: list[El] = []
        self.gaps: list[str] = []
        self.chars: list[str] = []

    def text(self) -> str:
        """The element as saved."""
        if not self.end:
            return self.start
        out = [self.start, self.gaps[0]]
        for k, g in zip(self.kids, self.gaps[1:], strict=True):
            out += [k.text(), g]
        return "".join(out) + self.end

    def open_tag(self) -> str:
        """The start tag of an element written with children (a saved `<block .../>` opens)."""
        return self.start[:-2].rstrip() + ">" if self.start.endswith("/>") else self.start

    def close_tag(self) -> str:
        """The end tag (written for a saved self-closing element that now has children)."""
        return self.end or f"</{self.tag_raw()}>"

    def tag_raw(self) -> str:
        """The tag name as written, with its prefix."""
        return self.start[1:].split(None, 1)[0].rstrip("/>")

    def kid(self, tag: str, name: str | None = None) -> El | None:
        """The first child with this tag (and name attribute, when given)."""
        return next((k for k in self.kids if k.tag == tag and (name is None or k.attrs.get("name") == name)), None)


def _local(name: str) -> str:
    """A tag name without its namespace or prefix."""
    return name.split(":")[-1].split("}")[-1]


class _Parser:
    """expat callbacks that keep each element's original text (parse)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.p = expat.ParserCreate()
        self.stack: list[tuple[El, list[tuple[int, int | None]], int, int]] = []
        self.spans: list[tuple[int, int]] = []
        self.root: El | None = None
        self.p.StartElementHandler, self.p.EndElementHandler = self.start, self.end
        self.p.CharacterDataHandler = self.chars

    def tag_end(self, i: int) -> int:
        """The offset just after the start tag that begins at i (quoted '>' skipped)."""
        q = None
        while True:
            c = self.data[i : i + 1]
            if not c:
                raise ValueError("unterminated start tag")
            if q:
                q = None if c == q else q
            elif c in (b'"', b"'"):
                q = c
            elif c == b">":
                return i + 1
            i += 1

    def start(self, name: str, attrs: dict[str, str]) -> None:
        """An element starts: kept with its start tag's text."""
        s = self.p.CurrentByteIndex
        e = self.tag_end(s)
        el = El(_local(name), attrs, self.data[s:e].decode("utf-8"))
        if self.stack:
            self.stack[-1][0].kids.append(el)
            self.stack[-1][1].append((s, None))
        else:
            self.root = el
        self.stack.append((el, [], e, s))

    def end(self, name: str) -> None:
        """An element ends: its end tag and the text between its children are kept."""
        el, kid_spans, content_start, s = self.stack.pop()
        if el.start.endswith("/>"):
            stop = content_start
        else:
            e = self.p.CurrentByteIndex
            stop = self.data.index(b">", e) + 1
            el.end = self.data[e:stop].decode("utf-8")
            # gaps: text between the start tag, the children and the end tag
            pos, gaps = content_start, []
            for a, b in kid_spans:
                gaps.append(self.data[pos:a].decode("utf-8"))
                pos = b  # type: ignore[assignment]
            gaps.append(self.data[pos:e].decode("utf-8"))
            el.gaps = gaps
        if self.stack:
            parent_spans = self.stack[-1][1]
            parent_spans[-1] = (parent_spans[-1][0], stop)
        else:
            self.spans.append((s, stop))

    def chars(self, text: str) -> None:
        """Character data: kept on the element it is in (a field's value)."""
        if self.stack:
            self.stack[-1][0].chars.append(text)


def parse(xml: str) -> tuple[str, El, str]:
    """Saved XML as elements that keep their original text.

    Returns:
        (the text before the root, the root element, the text after it).
    """
    data = xml.encode("utf-8")
    p = _Parser(data)
    p.p.Parse(data, True)
    s, e = p.spans[0]
    assert p.root is not None
    return data[:s].decode("utf-8"), p.root, data[e:].decode("utf-8")


def _field_text(el: El) -> str:
    return "".join(el.chars)


def _sig(v: Any) -> str:
    """A value's signature, to tell whether an edit changed it."""
    return json.dumps(v, sort_keys=True, separators=(",", ":"))


def _own(node: Block) -> str:
    """The signature of a block's own parts (type, fields, picked codes)."""
    return _sig({k: node.get(k) for k in ("type", "fields", "picked")})


def _decl(c: dict[str, Any]) -> tuple[Any, ...]:
    """What a block's mutation copies from a counter's declaration (fields._counter_attrs), with its defaults."""
    vector = bool(c.get("namespace"))
    return (
        c.get("level") or OFFER_COUNTER,
        c.get("data_type"),
        c.get("target_type") or "NO TARGET",
        c.get("namespace") or None,
        (c.get("entity_level") or "Activity") if vector else None,
        (c.get("ladder") or "DUMMY VALUE") if vector else None,
    )


def _names(node: Block) -> set[Any]:
    """The attribute or counter a block names."""
    return {(node.get("fields") or {}).get(k) for k in ("attribute", "mta_attribute")}


class Doc:
    """A saved canvas the model edits: read as blocks with short ids, written back keeping every unchanged byte.

    Attributes:
        xml: The saved canvas.
        tops: Its top-level blocks [root, loose...] in node form, short ids b1.. in document order.
        counters: The offer counters its blocks refer to.
        xid: Each short id's Blockly id in the saved XML.
    """

    def __init__(self, xml: str, canvas: str = "rules") -> None:
        """Reads a saved canvas.

        Args:
            xml: The saved canvas.
            canvas: rules or segment.
        """
        self.xml, self.canvas = xml, canvas
        self.prefix, self.root, self.suffix = parse(xml)
        self.el: dict[str, El] = {}  # short id -> block element
        self.xid: dict[str, str] = {}  # short id -> its saved Blockly id
        self.tops, self.counters = self._read()
        self.own = {bid: _own(n) for bid, n in self._all()}
        self.chain_sig: dict[str, str] = {}
        self.input_sig: dict[tuple[str, str], str] = {}
        self._stale: set[str] = set()
        for top in self.tops:
            self._sigs([top])

    # ---- reading -------------------------------------------------------------------------------------------------
    def _read(self) -> tuple[Any, list[dict[str, Any]]]:
        k = [0]

        def node(el: El) -> Block:
            k[0] += 1
            bid = f"b{k[0]}"
            self.el[bid], self.xid[bid] = el, el.attrs.get("id") or bid
            out: Block = {"type": el.attrs.get("type"), "id": bid}
            for ch in el.kids:
                if ch.tag == "mutation":
                    out["mutation"] = dict(ch.attrs)
                elif ch.tag == "field":
                    out.setdefault("fields", {})[ch.attrs.get("name")] = _field_text(ch)
                elif ch.tag == "statement":
                    out.setdefault("inputs", {})[ch.attrs.get("name")] = chain(ch.kid("block"))
                elif ch.tag == "value":
                    kid = ch.kid("block") or ch.kid("shadow")
                    if kid is not None:
                        out.setdefault("inputs", {})[ch.attrs.get("name")] = node(kid)
            return out

        def chain(el: El | None) -> list[Block]:
            out = []
            while el is not None:
                out.append(node(el))
                nxt = el.kid("next")
                el = nxt.kid("block") if nxt is not None else None
            return out

        return select([node(b) for b in self.root.kids if b.tag == "block"])

    def _all(self) -> list[tuple[str, Block]]:
        return [(n["id"], n) for n, _, _ in nodes(self.tops)]

    def _sigs(self, chain: list[Block]) -> None:
        """Signatures of every saved chain suffix and input content, to tell what an edit left as it was."""
        for i, n in enumerate(chain):
            self.chain_sig[n["id"]] = _sig(chain[i:])
            for name, child in (n.get("inputs") or {}).items():
                self.input_sig[(n["id"], name)] = _sig(child)
                self._sigs(child if isinstance(child, list) else [child])

    def view(self) -> dict[str, Any]:
        """The saved canvas as the model reads it: {tops, counters} (copies)."""
        return {"tops": json.loads(json.dumps(self.tops)), "counters": json.loads(json.dumps(self.counters))}

    # ---- writing -------------------------------------------------------------------------------------------------
    def write(
        self, tops: list[Block], catalog: Catalog | None = None, counters: Counters | None = None
    ) -> tuple[str, dict[str, str], dict[str, str]]:
        """The edited canvas.

        Args:
            tops: The edited top-level blocks [root, loose...].
            catalog: The tenant catalog.
            counters: The counters the canvas declares now, by name (a changed declaration rewrites the blocks that
                use it).

        Returns:
            (xml, paths, xml ids): where each block sits, and each short id's id in the XML.
        """
        saved = {c["name"]: c for c in self.counters}
        self._stale = {n for n, c in (counters or {}).items() if n in saved and _decl(c) != _decl(saved[n])}
        if not self._stale and _sig(tops) == _sig(self.tops):
            return self.xml, self._paths(tops), dict(self.xid)
        w = Writer(catalog, counters, self.canvas, existing=self._existing)
        self._w, self._catalog, self._counters = w, catalog, counters
        saved_loose = {t["id"] for t in self.tops[1:]}
        body, new_loose = [w.chain([tops[0]], "")], 0
        for t in tops[1:]:
            if t.get("id") in saved_loose and t.get("id") in self.el:
                body.append(w.chain([t], "loose"))
            else:
                body.append(w.chain([t], "loose", loose_attrs(new_loose)))
                new_loose += 1
        others = "".join(k.text() for k in self.root.kids if k.tag != "block")  # <variables> and the like, as saved
        xml = self.prefix + self.root.open_tag() + others + "".join(body) + self.root.close_tag() + self.suffix
        ids = {bid: self.xid.get(bid, bid) for bid in w.paths}
        return xml, w.paths, ids

    def _paths(self, tops: list[Block]) -> dict[str, str]:
        w = Writer(None, None, self.canvas)
        w.chain([tops[0]], "")
        for t in tops[1:]:
            w.chain([t], "loose")
        return w.paths

    def _stale_in(self, blocks: list[Block]) -> bool:
        """A block among blocks (or under them) names a counter whose declaration changed: its mutation is stale."""
        return any(_names(n) & self._stale for n, _, _ in nodes(blocks))

    def _existing(self, node: Block, rest: list[Block], where: str) -> str | None:
        """The saved text for a block, when there is some to keep.

        A chain unchanged from this block on is written as saved; a saved block whose own parts or inputs changed keeps
        its element, with the changed parts rewritten; a block naming a counter whose declaration changed gets its own
        parts rewritten. None for a new block.
        """
        bid = node.get("id")
        el = self.el.get(bid or "")
        if el is None or el.attrs.get("type") != node.get("type"):
            return None
        w = self._w
        if self.chain_sig.get(bid or "") == _sig([node, *rest]) and not self._stale_in([node, *rest]):
            self._label_chain([node, *rest], where)
            return el.text()
        here = label(node, where)
        w.paths[str(bid)] = here
        spec = reference(self.canvas).get(node["type"], {})
        if _own(node) == self.own.get(bid or "") and not _names(node) & self._stale:
            own = "".join(k.text() for k in el.kids if k.tag in OWN)
        else:
            _, parts = own_xml(node, spec, self._catalog, self._counters)
            own = "".join(parts)
        others = "".join(k.text() for k in el.kids if k.tag not in (*OWN, *INPUTS, "next"))  # comment, data, ...

        def keep(name: str, content: Any) -> str | None:
            kids = content if isinstance(content, list) else [content]
            if self.input_sig.get((str(bid), name)) == _sig(content) and not self._stale_in(kids):
                saved = el.kid("statement", name) or el.kid("value", name)
                if saved is not None:
                    self._label_chain(kids, f"{here}.{name}")
                    return saved.text()
            return None

        inputs = w.inputs(node, spec, here, keep=keep)
        nxt = w.chain(rest, where)
        return el.open_tag() + own + others + inputs + (f"<next>{nxt}</next>" if nxt else "") + el.close_tag()

    def _label_chain(self, chain: list[Block], where: str) -> None:
        """Paths for blocks written from saved text (problem reports name them)."""
        for n in chain:
            here = label(n, where)
            self._w.paths[n["id"]] = here
            for name, child in (n.get("inputs") or {}).items():
                self._label_chain(child if isinstance(child, list) else [child], f"{here}.{name}")
