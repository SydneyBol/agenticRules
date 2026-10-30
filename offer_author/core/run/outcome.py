"""A run's result: what the tools built, with the model's accepted finish, per part of the offer.

Problems in it (questions, assumptions, compose problems) stay Problems; the API words them (api/text.py).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ...domain import Composed, Problem
from ..canvas.checks.counters import used_counters
from .offer_main import offer_settings

if TYPE_CHECKING:
    from .run import Run

NOTE_KEYS = ("assumptions", "blocking_questions", "unsupported")
STOPPED = Problem.of("stopped")
RULE_BLOCKS = re.compile(r'<block type="(?:rule|rule_line|payment_rule)"')

ByPart = dict[str, dict[str, list[Any]]]


def notes_by_part(fin: dict[str, Any], parts: Sequence[str]) -> ByPart:
    """The finish items per part of the job.

    An item naming no part, or a part outside the job, belongs to every part of the job.

    Args:
        fin: The finish fields.
        parts: The parts of the job.

    Returns:
        {part: {assumptions: [text], blocking_questions: [text], unsupported: [{phrase, reason, alternative?}]}}.
    """
    out: ByPart = {p: {k: [] for k in NOTE_KEYS} for p in parts}
    for key in NOTE_KEYS:
        for item in fin.get(key) or []:
            item = {"text": item} if isinstance(item, str) else item
            value = {k: v for k, v in item.items() if k != "part"} if key == "unsupported" else item.get("text", "")
            for p in [item["part"]] if item.get("part") in out else parts:
                out[p][key].append(value)
    return out


def _rules_result(
    xml: str,
    offer_id: int | None,
    counters: Sequence[Any] = (),
    problems: Sequence[Problem] = (),
    report: dict[str, Any] | None = None,
    loose: Sequence[Any] = (),
    edited: bool = False,
) -> dict[str, Any]:
    """The rules part of a result."""
    return {
        "xml": xml,
        "counters": list(counters),
        "expected_offer_id": offer_id,
        "rule_count": len(RULE_BLOCKS.findall(xml)),
        "block_count": xml.count("<block "),
        "grammar_problems": list(problems),
        "counter_report": report,
        "loose": list(loose),
        "edited": edited,
    }


def _counter_report(run: Run, offer_id: int | None, used: list[Any]) -> dict[str, Any] | None:
    """The counter guard's report on the counters the rules use (the ones the stock upload creates).

    {"checked": False} when there was no guard to check an offer's counters with.
    """
    if run.guard:
        return run.guard.report(used)
    return {"checked": False} if offer_id is not None and used else None


def _rules(run: Run, fin: dict[str, Any], parts: Sequence[str], offer_id: int | None, by_part: ByPart) -> Any:
    """The rules the run delivers.

    The last compose when it has rules or loose blocks; the saved canvas as it is when the model finished without
    changing it (a request the saved rules already meet); else None.
    """
    if "rules" not in parts:
        return None
    canvases = run.canvases
    done: Composed | None = canvases.clean.get("rules") or canvases.last.get("rules")
    if done and done.built:
        used = used_counters(done.xml, done.counters)
        by_part["rules"]["assumptions"] += [
            Problem.of("loose_block", type=lb["type"], reason=lb["reason"])
            for lb in done.loose
            if lb["reason"].code != "loose_saved"
        ]
        return _rules_result(
            done.xml,
            offer_id,
            used,
            done.problems,
            _counter_report(run, offer_id, used),
            done.loose,
            canvases.edited("rules"),
        )
    if not done and canvases.doc is not None and not (fin["blocking_questions"] or fin["unsupported"]):
        return _rules_result(canvases.doc.xml, offer_id, report=run.guard.report([]) if run.guard else None)
    return None


def _segment(run: Run, fin: dict[str, Any], parts: Sequence[str], rules: Any, by_part: ByPart) -> Any:
    """The member segment the run delivers (its last compose with blocks), or None."""
    seg = run.canvases.clean.get("segment") or run.canvases.last.get("segment")
    if "segment" not in parts or not (seg and seg.built):
        return None
    segment = {
        "xml": seg.xml,
        "name": fin["segment_name"],
        "description": fin["segment_description"],
        "problems": seg.problems,
    }
    if rules:
        by_part["rules"]["assumptions"].append(Problem.of("select_segment", name=segment["name"]))
    return segment


def _whole_offer(by_part: ByPart, parts: Sequence[str]) -> tuple[list[Any], list[Any], list[Any]]:
    """The whole offer's items (CLI, a full-offer reader), each once, in part order.

    Returns:
        (assumptions, questions, unsupported).
    """

    def items(key: str) -> list[Any]:
        return [x for p in parts for x in by_part[p][key]]

    unsupported = items("unsupported")
    unsupported = [u for i, u in enumerate(unsupported) if u not in unsupported[:i]]  # dicts: not hashable
    return list(dict.fromkeys(items("assumptions"))), list(dict.fromkeys(items("blocking_questions"))), unsupported


def _diagnostics(run: Run, parts: Sequence[str]) -> dict[str, Any]:
    """What the tools did in the run: calls, lookups, the trace, repairs."""
    return {
        "parts": list(parts),
        "tool_calls": run.counts,
        "lookups": run.lookups.made,
        "trace": run.trace,
        "repairs": max(0, run.counts.get("compose", 0) - len(run.canvases.last)),
    }


def outcome(run: Run, fin: dict[str, Any] | None, parts: Sequence[str], offer_id: int | None) -> dict[str, Any]:
    """The run's result from what the tools built and the model's accepted finish.

    Args:
        run: The run.
        fin: The accepted finish fields; None when the run reached the turn limit without one, so nothing it built is
            confirmed and the manager is asked instead of given a draft.
        parts: The parts of the offer the run builds.
        offer_id: The offer the rules are for, or None.

    Returns:
        {status, readback, assumptions, blocking_questions, unsupported (all parts), by_part (each part's own),
        offer_main, offer_settings, segment, rules, diagnostics}.
    """
    finished = fin is not None
    fin = {
        "readback": "",
        "segment_name": "",
        "segment_description": "",
        "assumptions": [],
        "blocking_questions": [],
        "unsupported": [],
        **(fin or {}),
    }
    by_part = notes_by_part(fin, parts)
    if finished:
        rules = _rules(run, fin, parts, offer_id, by_part)
        segment = _segment(run, fin, parts, rules, by_part)
    else:
        rules = segment = None
        for p in parts:
            by_part[p]["blocking_questions"].append(STOPPED)
    assumptions, questions, unsupported = _whole_offer(by_part, parts)
    offer_main = run.offer_main.value
    built = rules if "rules" in parts else segment if "segment" in parts else offer_main
    status = ("needs_input" if questions else "draft") if built else "unsupported" if unsupported else "needs_input"
    if not (built or unsupported or questions):
        questions = [Problem.of("nothing_built")]
        for p in parts:
            by_part[p]["blocking_questions"] += questions
    return {
        "status": status,
        "readback": fin["readback"],
        "assumptions": assumptions,
        "blocking_questions": questions,
        "unsupported": unsupported,
        "by_part": by_part,
        "offer_main": offer_main,
        "offer_settings": offer_settings(offer_main),
        "segment": segment,
        "rules": rules,
        "diagnostics": _diagnostics(run, parts),
    }


def error_result(problem: Problem) -> dict[str, Any]:
    """The result of a run that ended without building anything: problem says why (worded at the API)."""
    return {
        "status": "error",
        "message": problem,
        "readback": "",
        "assumptions": [],
        "blocking_questions": [],
        "unsupported": [],
        "by_part": {},
        "offer_main": None,
        "offer_settings": None,
        "segment": None,
        "rules": None,
        "diagnostics": {},
    }
