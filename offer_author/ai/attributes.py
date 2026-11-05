"""The attribute agent: which catalog data the manager's terms refer to, confirmed once for the build and the cases.

The second half of attribute grounding (AttributeGrounding.md). Code first retrieves candidate attributes for the
description (core/tenant/attribute_search.py); the model checks them against the description and the tenant catalog,
searches again with its own words when one is missing, and confirms each term's attribute and value codes. The build
and the test cases both read the result as a hint (the full catalog still applies), and nothing else of it: it names
data, never conditions, windows, limits or rewards, so the engine check stays an independent reading of the logic.

A term that fits two attributes is settled here, once, so the two branches (which run at the same time) can never
read it differently: from the description itself (shown to the manager as an assumption), or asked of the manager.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..core.tenant import attribute_search
from ..core.tenant.catalog import norm
from .tools import _schema

if TYPE_CHECKING:
    from ..core.tenant.catalog import Catalog, Search


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Choice(_Item):
    """One catalog attribute a term may refer to."""

    object: str = Field(description="activity, member, product (or another catalog object), or tier (MA_T)")
    code: str = Field(description="the attribute's code in the tenant catalog (for tier: the tier class)")
    values: list[str] = Field(default_factory=list, description="the value codes the term names, when it names some")


class Term(Choice):
    """One of the manager's terms and the catalog data it refers to."""

    words: str = Field(description="the term, exactly as the description writes it")


class Ambiguous(_Item):
    """A term that fits two or more catalog attributes."""

    words: str = Field(description="the term, exactly as the description writes it")
    candidates: list[Choice] = Field(description="every attribute the term fits, two or more")
    resolved: int | None = Field(
        None,
        description="the index in candidates of the one the description's words or meaning point to; empty only when "
        "it fits two of them equally well",
    )
    reason: str = Field("", description="what in the description points to it (with resolved)")
    question: str = Field("", description="the question for the manager (only when resolved is empty)")


class Attributes(BaseModel):
    """The catalog data the description's terms refer to."""

    model_config = ConfigDict(extra="forbid")

    terms: list[Term] = Field(description="each term that refers to catalog data, with its attribute (unambiguous)")
    ambiguous: list[Ambiguous] = Field(default_factory=list)


FIND = {
    "name": "find_attributes",
    "description": "Searches the tenant catalog for the attributes (and values) some words refer to.",
    "input_schema": {
        "type": "object",
        "properties": {"words": {"type": "string", "description": "a few words, e.g. 'member's birthday'"}},
        "required": ["words"],
    },
}
CONFIRM = {
    "name": "confirm_attributes",
    "description": "The catalog data each of the description's terms refers to, for the runs that build and test the "
    "offer.",
    "input_schema": _schema(Attributes),
}

TASK = (
    "\n\nDo not build the offer and do not write its test cases: two other runs do, and both start from the "
    "attributes you confirm now with confirm_attributes. For each term of the description that refers to data in "
    "this tenant's catalog (the transaction, its line items or payments, the member, a product, the member's tier), "
    "give the catalog attribute and, when the term names values, their value codes. The candidates below were found "
    "by search; the tenant catalog in your instructions is the complete list, and find_attributes searches it with "
    "other words. Use only codes from the catalog. A term that fits two or more attributes goes in ambiguous with "
    "every attribute it fits. When the description's words or their meaning point to one of them, give that one and "
    "what points to it; give a question for the manager only when the description fits two of them equally well. "
    "Name data only: no conditions, compared amounts, windows, limits or rewards."
)
STARTED = (
    "\n\nAlready confirmed for this offer (an earlier description of it, or its saved rules); keep what the "
    "description still refers to:\n{known}"
)
TURNS = 3  # a search turn, the confirming turn, and one more when the confirmation names data the catalog lacks


def ground(
    llm: Any,
    system: list[dict[str, Any]],
    prompt: str,
    catalog: Catalog,
    search: Search | None = None,
    start: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The description's attributes: retrieved in code, checked and confirmed by the model.

    Args:
        llm: The chat model.
        system: The run's system prompt blocks (the same cached prefix as every run).
        prompt: The manager's description.
        catalog: The tenant's catalog.
        search: What ranks by meaning (value_search.ValueSearch), or None.
        start: Attributes already confirmed for this offer (an earlier description, or the saved rules' own).

    Returns:
        (the confirmed Attributes as a dict, or None when no turn confirmed any that fit; the turns' usage, time,
        candidates and problems).
    """
    t0 = time.time()
    found = attribute_search.find(catalog, prompt, search, transaction=True)
    text = prompt + TASK + "\n\nCandidates found by search:\n" + _candidates(found)
    if start and (start.get("terms") or start.get("ambiguous")):
        text += STARTED.format(known=render(start, catalog))
    messages: list[Any] = [HumanMessage(content=text)]
    usage: dict[str, int] = {}
    confirmed: Attributes | None = None
    problems: list[str] = []
    n = searches = 0
    for _ in range(TURNS):
        n += 1
        turn = llm.turn(system, messages, tools=[FIND, CONFIRM])
        for k, v in (turn.usage or {}).items():
            usage[k] = usage.get(k, 0) + (v or 0)
        calls = turn.message.tool_calls
        if not calls:
            break
        results = []
        for call in calls:  # every call gets its result (2026-11-05 X01)
            searches += call["name"] == "find_attributes"
            content, got, problems = _answer(call, catalog, prompt, search, problems)
            confirmed = got or confirmed
            results.append(_result(call, content))
        if confirmed is not None:
            break
        messages += [turn.message, *results]
    info = {"s": round(time.time() - t0, 1), "turns": n, "searches": searches, "candidates": len(found), **usage}
    if confirmed is None:
        return None, {**info, "problems": problems}
    out = confirmed.model_dump()
    return out, {**info, "terms": len(out["terms"]), "ambiguous": len(out["ambiguous"]), "problems": []}


def _answer(
    call: dict[str, Any], catalog: Catalog, prompt: str, search: Search | None, problems: list[str]
) -> tuple[str, Attributes | None, list[str]]:
    """One call's result: (its content, the attributes when it confirmed some that fit, the problems so far)."""
    args = call["args"] or {}
    if call["name"] == "find_attributes":
        return json.dumps(attribute_search.find(catalog, str(args.get("words") or ""), search, k=8)), None, problems
    if call["name"] != "confirm_attributes":
        return json.dumps({"ok": False, "error": "unknown tool"}), None, problems
    got: Attributes | None = None
    try:
        got = Attributes.model_validate(args)
    except ValidationError as e:
        problems = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:6]]
    else:
        problems = check(got, catalog, prompt)
    ok = not problems
    return json.dumps({"ok": ok, **({"fix": problems} if problems else {})}), got if ok else None, problems


def _result(call: dict[str, Any], content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call["id"], name=call["name"])


def _candidates(found: list[dict[str, Any]]) -> str:
    lines = []
    for c in found:
        named = ", ".join(f"{v['label']} ({v['code']})" for v in c.get("values") or [])
        sample = ", ".join(c.get("sample") or [])
        lines.append(
            f"- {c['object']}.{c['code']} | {c['label']} | {c['type']} | {c['where']}"
            + (f" | the description names: {named}" if named else f" | e.g. {sample}" if sample else "")
        )
    return "\n".join(lines) or "(none)"


def _known(catalog: Catalog, obj: str, code: str) -> dict[str, str] | None:
    """The attribute's values (empty when it has no list), or None when the catalog has no such attribute."""
    if obj == "tier":
        return catalog.tiers.get(code)
    a = catalog.attr(obj, code)
    return None if a is None else dict(a.values or {})


def check(attrs: Attributes, catalog: Catalog, prompt: str) -> list[str]:
    """What the catalog or the description does not have.

    Unknown attributes or values, terms not in the description's own words, ambiguities with one candidate, an
    unknown resolved index, an unsettled one without a question.
    """
    out = []
    said = norm(prompt)

    def choice(where: str, c: Choice) -> None:
        vals = _known(catalog, c.object, c.code)
        if vals is None:
            out.append(f"{where}: {c.object}.{c.code} is not in the tenant catalog")
            return
        out.extend(f"{where}: {v} is not a value of {c.object}.{c.code}" for v in c.values if v not in vals)

    for t in attrs.terms:
        if norm(t.words) not in said:
            out.append(f"term '{t.words}': not the description's own words")
        choice(f"term '{t.words}'", t)
    for a in attrs.ambiguous:
        if norm(a.words) not in said:
            out.append(f"ambiguous '{a.words}': not the description's own words")
        if len(a.candidates) < 2:
            out.append(f"ambiguous '{a.words}': needs two or more candidates (else it is a term)")
        for c in a.candidates:
            choice(f"ambiguous '{a.words}'", c)
        if a.resolved is not None and not 0 <= a.resolved < len(a.candidates):
            out.append(f"ambiguous '{a.words}': resolved {a.resolved} is not a candidate's index")
        if a.resolved is None and not a.question.strip():
            out.append(
                f"ambiguous '{a.words}': give the question for the manager, or what in the description settles it"
            )
    return out


def _named(catalog: Catalog, c: dict[str, Any]) -> str:
    vals = _known(catalog, c["object"], c["code"]) or {}
    shown = ", ".join(f"{v} ({vals[v]})" if v in vals else v for v in c.get("values") or [])
    return f"{c['object']}.{c['code']}" + (f" = {shown}" if shown else "")


def render(attrs: dict[str, Any], catalog: Catalog) -> str:
    """The confirmed attributes as the build and the cases read them (settled ambiguities as their choice)."""
    lines = [f'- "{t["words"]}" -> {_named(catalog, t)}' for t in attrs.get("terms") or []]
    for a in attrs.get("ambiguous") or []:
        if a.get("resolved") is not None:
            pick = a["candidates"][a["resolved"]]
            others = ", ".join(_named(catalog, c) for i, c in enumerate(a["candidates"]) if i != a["resolved"])
            lines.append(f'- "{a["words"]}" -> {_named(catalog, pick)} (not {others}: {a.get("reason") or "settled"})')
    return "\n".join(lines)


def hint(attrs: dict[str, Any] | None, catalog: Catalog) -> str:
    """The text added to the build's context and the cases' task; empty without attributes."""
    body = render(attrs or {}, catalog)
    if not body:
        return ""
    return (
        "The data the description's terms refer to, confirmed against the tenant catalog (the catalog still applies "
        "in full; this names data only, never what the offer does with it):\n" + body
    )


def questions(attrs: dict[str, Any] | None) -> list[str]:
    """The questions for the manager: each ambiguity the description does not settle."""
    return [a["question"] for a in (attrs or {}).get("ambiguous") or [] if a.get("resolved") is None]


def assumptions(attrs: dict[str, Any] | None, catalog: Catalog) -> list[str]:
    """Each settled ambiguity, told to the manager as an assumption."""
    out = []
    for a in (attrs or {}).get("ambiguous") or []:
        if a.get("resolved") is not None:
            pick = a["candidates"][a["resolved"]]
            out.append(f'"{a["words"]}" is read as {_named(catalog, pick)}: {a.get("reason") or "settled"}.')
    return out


def merged(*found: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attributes confirmed separately (an earlier description, the saved rules), as one; None when all are empty."""
    terms: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for f in found:
        for t in (f or {}).get("terms") or []:
            if not any((x["object"], x["code"], x["words"]) == (t["object"], t["code"], t["words"]) for x in terms):
                terms.append(t)
        ambiguous += [a for a in (f or {}).get("ambiguous") or [] if a not in ambiguous]
    return {"terms": terms, "ambiguous": ambiguous} if terms or ambiguous else None


def from_canvas(xml: str | None, catalog: Catalog) -> dict[str, Any] | None:
    """The attributes saved rules already read, as confirmed terms (words: the attribute's label)."""
    terms = []
    for obj, code in attribute_search.read_by(xml):
        a = catalog.attr(obj, code)
        if a is not None:
            terms.append({"object": obj, "code": code, "values": [], "words": a.label})
    return {"terms": terms, "ambiguous": []} if terms else None
