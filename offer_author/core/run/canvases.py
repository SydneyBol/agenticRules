"""The compose tool: a canvas the model composed, written as XML and checked for block compatibility.

A canvas (rules or segment) is sent whole or as edits on its last tree. The tools write the XML (canvas/build.py, or
canvas/edit.py when the offer has saved rules to keep) and check it with data (canvas/checks/): the toolbox and the
connections from the block reference, catalog codes, the fields the screen needs, and the counters. Problems the
starting canvas already had are left out: they are not the model's to fix.
"""

from __future__ import annotations

import logging
from typing import Any

from ...domain import Block, Composed, CounterDecl, LooseBlock, Problem, Result, ToolInput, located, renamed
from ..canvas.build import to_xml
from ..canvas.checks.counters import counter_problems
from ..canvas.checks.grammar import ROOTS, check, detach
from ..canvas.checks.screen_fields import unfilled
from ..canvas.declare import declare
from ..canvas.edit import Doc
from ..canvas.tree import TreeError, apply_edits, compact, expand, inline, number
from ..tenant.existing_counters import Guard

log = logging.getLogger("offer_author.canvases")


def _back(xids: dict[str, str]) -> dict[str, str]:
    """The ids the model knows for the blocks of a saved canvas, by their id in its XML."""
    return {x: b for b, x in xids.items()}


def _has_rules(tops: list[Block]) -> bool:
    """The root holds something (an empty root has no rules: not a problem when only loose blocks are added)."""
    return bool(tops and (tops[0].get("inputs") or {}) and any(tops[0]["inputs"].values()))


def _mutations(tree: Any, path: str = "") -> list[str]:
    """Where the model wrote mutation attributes itself: the tools derive them, so these are rejected."""
    if isinstance(tree, list):
        return [p for i, n in enumerate(tree) for p in _mutations(n, f"{path}[{i}]")]
    if not isinstance(tree, dict):
        return []
    here = f"{path} > {tree.get('type')}" if path else str(tree.get("type"))
    out = [here] if "mutation" in tree else []
    for k, v in (tree.get("inputs") or {}).items():
        out += _mutations(v, f"{here}.{k}")
    return out


def _notes(canvas: str, problems: list[Problem], already: set[Problem]) -> Result:
    """What a compose reply adds: how to repair (only the blocks the problems name), the starting canvas's problems."""
    out: Result = {}
    if problems:
        out["fix_with"] = Problem.of("fix_with_edits", canvas=canvas)
    if already:
        out["already_on_the_canvas"] = sorted(already, key=repr)[:5]
    return out


def saved_rules(existing: dict[str, Any] | None) -> Doc | None:
    """The offer's saved rules canvas (existing["rules_xml"]), when it has one the tools can edit."""
    rules_xml = (existing or {}).get("rules_xml")
    if not rules_xml:
        return None
    try:
        doc = Doc(rules_xml)
    except Exception:  # a canvas the tools cannot read is rebuilt, not fatal
        log.exception("the offer's saved rules could not be read")
        return None
    return doc if doc.tops and doc.tops[0].get("type") == ROOTS["rules"] else None


class Canvases:
    """The run's canvases: per canvas its last tree, its last compose and its last clean compose.

    When the offer already has saved rules, the rules canvas starts from them: edits keep every block they do not
    touch as saved (canvas_xml.Doc), and problems the saved canvas already had are not the model's to fix.

    Attributes:
        last: Each canvas's last compose.
        clean: Each canvas's last compose without problems.
        doc: The offer's saved rules canvas, or None.
        saved_counters: The counters the saved rules declare.
    """

    def __init__(self, catalog: Any, guard: Guard | None = None, saved: Doc | None = None) -> None:
        """Starts with no compose; the rules canvas starts from the saved rules when there are some.

        Args:
            catalog: The tenant catalog.
            guard: The offer's existing counters, or None when they are not known.
            saved: The offer's saved rules (saved_rules), or None.
        """
        self.catalog, self.guard = catalog, guard
        self.trees: dict[str, list[Block]] = {}  # each canvas's last top-level blocks [root, loose...], ids filled in
        self.defs: dict[str, Any] = {}  # each canvas's defs by name, kept for later edits and trees
        self.last: dict[str, Composed] = {}
        self.clean: dict[str, Composed] = {}
        self.rebuilt: set[str] = set()  # canvases sent as a whole new tree (the saved canvas no longer applies)
        self.base: dict[str, set[Problem]] = {}
        self.asked_loose: dict[str, set[str]] = {}  # ids of the loose blocks the model asked for, per canvas
        self.doc, self.saved_counters = saved, []
        if saved is not None:
            view = saved.view()
            self.trees["rules"], self.saved_counters = view["tops"], view["counters"]

        self.declared: dict[str, dict[Any, CounterDecl]] = {  # every counter declared so far, per canvas (declare.py)
            "rules": {c.get("name"): c for c in self.saved_counters}
        }

    def current(self) -> dict[str, Any] | None:
        """The saved rules canvas as the model reads it (compact blocks with ids, counters), or None."""
        if self.doc is None:
            return None
        tops = self.doc.view()["tops"]
        return {
            "root": compact(tops[0], self.saved_counters),
            "loose": [compact(t, self.saved_counters) for t in tops[1:]],
            "counters": self.saved_counters,
        }

    def edited(self, canvas: str) -> bool:
        """The canvas is the saved one, edited (its saved text is kept where unchanged)."""
        return canvas == "rules" and self.doc is not None and canvas not in self.rebuilt

    def compose(self, x: ToolInput) -> Result:
        """Writes the canvas a compose call describes and checks it.

        Args:
            x: The compose input: a whole tree (blocks), or edits and loose blocks on the canvas's last tree.

        Returns:
            ok, the problems (blocks named by the ids the model knows, with where they sit), how to repair, the
            starting canvas's own problems, and the loose blocks.
        """
        canvas = x.get("canvas") or "rules"
        if canvas not in ROOTS:
            return {"ok": False, "problems": [Problem.of("unknown_canvas")]}
        try:
            tops, asked, counters = self._read(canvas, x)
        except TreeError as e:
            return {"ok": False, "problems": [e.problem]}
        if own := [p for t in tops + asked for p in _mutations(t)]:
            return {"ok": False, "problems": [Problem.of("mutation_given", at=p) for p in own]}
        counters = [c for c in counters or [] if isinstance(c, dict)]
        counters, by_tools = declare(tops + asked, counters, list(self.declared.get(canvas, {}).values()), self.catalog)
        self.declared.setdefault(canvas, {}).update({c.get("name"): c for c in counters})
        noted: Result = {"declared_by_tools": by_tools} if by_tools else {}
        byname: dict[str, Any] = {c["name"]: c for c in counters if c.get("name")}
        try:
            tops = number(tops + asked)
            self.asked_loose.setdefault(canvas, set()).update(t["id"] for t in tops[len(tops) - len(asked) :])
            tops, moved = detach(tops, canvas)
            if missing := unfilled(tops, self.catalog, byname):
                self.trees[canvas] = tops
                return {
                    "ok": False,
                    "problems": missing,
                    "fix_with": Problem.of("fix_with_counters", canvas=canvas),
                    **noted,
                }
            xml, paths, xids = (
                self.doc.write(tops, self.catalog, byname)
                if self.edited(canvas) and self.doc is not None
                else (*to_xml(tops, self.catalog, byname, canvas), {})
            )
        except TreeError as e:
            return {"ok": False, "problems": [Problem.of("not_a_block_tree", problem=e.problem)]}
        except (TypeError, AttributeError, ValueError) as e:
            return {"ok": False, "problems": [Problem.of("not_a_block_tree", error=str(e))]}
        self.trees[canvas] = tops
        written = Composed(xml, [], counters, self._loose(canvas, tops, moved))
        return {**self._check(canvas, tops, written, paths, xids), **noted}

    def pending(self) -> list[Problem]:
        """Why finish must wait: a canvas whose last compose had problems."""
        return [
            Problem.of("canvas_not_clean", canvas=c, problem=q)
            for c, last in self.last.items()
            if last.problems and self.clean.get(c) is not last
            for q in last.problems
        ]

    def all_clean(self) -> bool:
        """Every canvas's last compose is its clean one."""
        return all(self.clean.get(c) is last for c, last in self.last.items())

    def _checks(self, xml: str, counters: list[CounterDecl], canvas: str) -> list[Problem]:
        """Block grammar and catalog codes, and the counters against the offer's."""
        return check(xml, self.catalog, canvas=canvas) + counter_problems(xml, counters, self.guard)

    def _baseline(self, canvas: str, root: Block) -> set[Problem]:
        """Problems of the starting canvas (the saved rules, or an empty root), which are not the model's.

        Computed once per canvas, id-mapped like the model's own.
        """
        if canvas not in self.base:
            if self.edited(canvas) and self.doc is not None:
                xml, xids, counters = self.doc.xml, self.doc.xid, self.saved_counters
            else:
                (xml, _), xids, counters = to_xml([root], self.catalog, {}, canvas), {}, []
            self.base[canvas] = set(renamed(self._checks(xml, counters, canvas), _back(xids)))
        return self.base[canvas]

    def _read(self, canvas: str, x: ToolInput) -> tuple[list[Block], list[Block], list[Any] | None]:
        """The top-level blocks, the loose blocks asked for and the counters of a call.

        Defs are inlined and blocks expanded; the defs are kept for later calls.

        Raises:
            TreeError: The call does not describe a tree the tools can build.
        """
        tops, counters = self._start(canvas, x)
        new = x.get("defs") or {}
        defs = {**self.defs.get(canvas, {}), **new} if isinstance(new, dict) else new
        tops = [expand(inline(t, defs)) for t in tops]
        asked = [expand(inline(b, defs)) for b in x.get("loose") or []]
        self.defs[canvas] = defs
        return tops, asked, counters

    def _start(self, canvas: str, x: ToolInput) -> tuple[list[Block], list[Any] | None]:
        """The top-level blocks and counters a call builds on.

        A whole new tree, or edits and loose blocks on the canvas's last tree (the saved one at first, an empty
        root when there is none).

        Raises:
            TreeError: Nothing to compose, or edits with no tree to apply them to.
        """
        if x.get("blocks") is not None:
            if canvas == "rules" and self.doc is not None:
                self.rebuilt.add(canvas)  # a whole new tree replaces the saved canvas
            return [x["blocks"]], x.get("counters")
        if x.get("edits") is None and x.get("loose") is None:
            raise TreeError(Problem.of("nothing_to_compose"))
        if canvas not in self.trees and x.get("edits"):
            raise TreeError(Problem.of("no_tree_to_edit", canvas=canvas))
        tops = apply_edits(self.trees.get(canvas) or [{"type": ROOTS[canvas], "id": "b1"}], x.get("edits") or [])
        counters = x.get("counters")
        if counters is None:
            last = self.last.get(canvas)
            counters = (last.counters if last else None) or (self.saved_counters if self.edited(canvas) else None)
        return tops, counters

    def _check(
        self, canvas: str, tops: list[Block], written: Composed, paths: dict[str, str], xids: dict[str, str]
    ) -> Result:
        """The written canvas through the checks, kept as the canvas's last compose."""
        base = self._baseline(canvas, tops[0]) if self.edited(canvas) or not _has_rules(tops) else set()
        own = renamed(self._checks(written.xml, written.counters, canvas), _back(xids))
        written.problems = located([p for p in own if p not in base], paths)
        self.last[canvas] = written
        if not written.problems:
            self.clean[canvas] = written
        return {
            "ok": not written.problems,
            "problems": written.problems,
            **_notes(canvas, written.problems, base),
            **({"loose": written.loose} if written.loose else {}),
        }

    def _loose(self, canvas: str, tops: list[Block], moved: list[dict[str, Any]]) -> list[LooseBlock]:
        """The canvas's loose blocks and why each is loose: detached by the tools, asked for, or already saved so."""
        why = {m["id"]: m["reason"] for m in moved}
        saved = {t["id"] for t in self.doc.tops[1:]} if self.edited(canvas) and self.doc is not None else set()
        asked = self.asked_loose.get(canvas, set())
        return [
            {
                "id": t["id"],
                "type": t.get("type") or "",
                "reason": why.get(t["id"])
                or Problem.of(
                    "loose_saved" if t["id"] in saved else "loose_asked" if t["id"] in asked else "loose_not_attached"
                ),
            }
            for t in tops[1:]
        ]
