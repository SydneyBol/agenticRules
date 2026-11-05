"""The offer's plan: one reading of the description that the build run and the test cases both start from.

Written once per engine-checked run, from the description alone, while Offer Main is set (SharedPlan.md). It says what
the offer reads and where its outcome changes, never how to build it: the clauses, how each loose term is read, every
input the outcome depends on (its catalog code, its edges, the kinds of values that behave differently) and what must
be remembered across transactions. The build run reads it as a checklist; the test cases cover every item of it.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools import _schema

if TYPE_CHECKING:
    from ..core.tenant.catalog import Catalog


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Clause(_Item):
    """One clause of the description."""

    id: str = Field(description="C1, C2, ...")
    text: str = Field(description="the clause, in the manager's words")
    gives: str = Field(description="what it gives (points, a privilege, nothing but a change to what is remembered)")


class Edge(_Item):
    """A value where the offer's outcome changes."""

    id: str = Field(description="E1, E2, ... (unique in the plan)")
    at: str = Field(description="the value or point where the outcome changes, e.g. 'amount 50', '15 days before'")
    inside: str = Field(description="a value just on the side the clause holds")
    outside: str = Field(description="a value just on the side it does not")


class Kind(_Item):
    """A group of values that behaves differently, or is easy to get wrong."""

    id: str = Field(description="K1, K2, ... (unique in the plan)")
    what: str = Field(description="the group, e.g. 'a birthday in a leap year after 28 Feb', 'no value at all'")


class Input(_Item):
    """A value the offer's outcome depends on."""

    id: str = Field(description="I1, I2, ...")
    source: Literal["activity", "line", "payment", "member", "product", "time", "history"] = Field(
        description="where the value comes from: the transaction's header, its line items or payments, the member's "
        "profile, a product, the transaction's date and time, or what earlier transactions did"
    )
    code: str = Field("", description="the catalog attribute code it reads; empty for time and history")
    use: str = Field(description="what the description does with it")
    clauses: list[str] = Field(description="the clause ids that read it")
    edges: list[Edge] = Field(default_factory=list)
    kinds: list[Kind] = Field(default_factory=list)


class Memory(_Item):
    """Something that must be remembered from one transaction to the next."""

    id: str = Field(description="M1, M2, ...")
    what: str = Field(description="what is remembered, e.g. 'the reward was given for this birthday'")
    starts_again: str = Field(description="when it starts again (each birthday, each calendar month, never)")
    clauses: list[str] = Field(description="the clause ids that use it")


class Plan(BaseModel):
    """What the offer reads and where its outcome changes, as the description says it."""

    model_config = ConfigDict(extra="forbid")

    clauses: list[Clause]
    readings: list[str] = Field(
        default_factory=list, description="how each loose term of the description is read, one sentence each"
    )
    inputs: list[Input]
    memory: list[Memory] = Field(default_factory=list)


WRITE_PLAN = {
    "name": "write_plan",
    "description": "The offer's plan: its clauses, how each loose term is read, every value its outcome depends on "
    "(with the edges where the outcome changes and the kinds of values that behave differently), and what must be "
    "remembered from one transaction to the next.",
    "input_schema": _schema(Plan),
}

TASK = (
    "\n\nDo not build the offer and do not write its test cases: two other runs do, and both start from the plan "
    "you write now with write_plan. Read the description as the offer's whole statement and write what it reads and "
    "where its outcome changes. Split it into its clauses. Write one reading for each term a manager could mean in "
    "more than one way. List every value the outcome depends on: the transaction's attributes, its line items and "
    "payments, the member's own attributes, the transaction's date and time, and what earlier transactions did; "
    "give each attribute by its code in this tenant's catalog. For each value, give every edge where the outcome "
    "changes, with a value just inside and just outside it, and the kinds of values that behave differently or are "
    "easy to get wrong. List what must be remembered from one transaction to the next and when it starts again. "
    "Name no blocks, counters or rules. Call write_plan once."
)
TURNS = 2  # the plan's turns: one, and one more when its codes are not the catalog's


def write(
    llm: Any, system: list[dict[str, Any]], prompt: str, catalog: Catalog
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The plan, written in one turn (a second when a code is not the catalog's).

    Returns:
        (the plan, or None when no turn wrote one that fits the schema; the turns' usage, time and problems).
    """
    t0 = time.time()
    messages: list[Any] = [HumanMessage(content=prompt + TASK)]
    usage: dict[str, int] = {}
    plan, problems, n = None, [], 0
    for _ in range(TURNS):
        n += 1
        turn = llm.turn(system, messages, tools=[WRITE_PLAN])
        for k, v in (turn.usage or {}).items():
            usage[k] = usage.get(k, 0) + (v or 0)
        call = next((c for c in turn.message.tool_calls if c["name"] == "write_plan"), None)
        if call is None:
            break
        try:
            got = Plan.model_validate(call["args"])
        except ValidationError as e:
            problems = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()[:6]]
        else:
            plan, problems = got, check(got, catalog)
            if not problems:
                break
        messages += [turn.message, ToolMessage(content=_redo(problems), tool_call_id=call["id"], name="write_plan")]
    info = {"s": round(time.time() - t0, 1), "turns": n, **usage}
    if plan is None:
        return None, {**info, "problems": problems}
    kept = _without_unknown(plan, catalog)
    out = kept.model_dump()
    size = {"clauses": len(kept.clauses), "inputs": len(kept.inputs), "memory": len(kept.memory)}
    return out, {**info, **size, "edges": len(edge_ids(out)), "kinds": len(kind_ids(out)), "problems": problems}


def _redo(problems: list[str]) -> str:
    return json.dumps({"ok": False, "error": "fix these and call write_plan again: " + "; ".join(problems)})


SOURCES = {"activity": "activity", "line": "activity", "payment": "activity", "member": "member", "product": "product"}


def check(plan: Plan, catalog: Catalog) -> list[str]:
    """What in the plan the catalog or the plan itself does not have: unknown codes, ids used twice, unknown clauses."""
    out = []
    clause_ids = {c.id for c in plan.clauses}
    ids = [c.id for c in plan.clauses] + [i.id for i in plan.inputs] + [m.id for m in plan.memory]
    ids += [e.id for i in plan.inputs for e in i.edges] + [k.id for i in plan.inputs for k in i.kinds]
    out += [f"id {x} is used more than once" for x in sorted({x for x in ids if ids.count(x) > 1})]
    for i in plan.inputs:
        if i.code and i.source in SOURCES and catalog.attr(SOURCES[i.source], i.code) is None:
            out.append(f"{i.id}: {i.code} is not a {i.source} attribute of the catalog")
        out += [f"{i.id}: clause {c} is not in the plan" for c in i.clauses if c not in clause_ids]
    out += [f"{m.id}: clause {c} is not in the plan" for m in plan.memory for c in m.clauses if c not in clause_ids]
    return out


def _without_unknown(plan: Plan, catalog: Catalog) -> Plan:
    """The plan without the inputs whose code the catalog does not have (after the turn that could fix them)."""
    keep = [
        i
        for i in plan.inputs
        if not (i.code and i.source in SOURCES and catalog.attr(SOURCES[i.source], i.code) is None)
    ]
    return plan.model_copy(update={"inputs": keep})


def edge_ids(plan: dict[str, Any]) -> list[str]:
    """The ids of the plan's edges."""
    return [e["id"] for i in plan.get("inputs") or [] for e in i.get("edges") or []]


def kind_ids(plan: dict[str, Any]) -> list[str]:
    """The ids of the plan's kinds."""
    return [k["id"] for i in plan.get("inputs") or [] for k in i.get("kinds") or []]


def to_cover(plan: dict[str, Any]) -> list[str]:
    """Every id the test cases must cover: clauses, edges, kinds and what is remembered."""
    return (
        [c["id"] for c in plan.get("clauses") or []]
        + edge_ids(plan)
        + kind_ids(plan)
        + [m["id"] for m in plan.get("memory") or []]
    )


def text(plan: dict[str, Any]) -> str:
    """The plan as both runs read it."""
    lines = ["Clauses:"]
    lines += [f"- {c['id']}: {c['text']} -> {c['gives']}" for c in plan.get("clauses") or []]
    if plan.get("readings"):
        lines += ["Readings:", *[f"- {r}" for r in plan["readings"]]]
    lines.append("Inputs:")
    for i in plan.get("inputs") or []:
        code = f" {i['code']}" if i.get("code") else ""
        lines.append(f"- {i['id']} ({i['source']}{code}, {', '.join(i.get('clauses') or [])}): {i['use']}")
        lines += [f"  - edge {e['id']}: {e['at']} (inside: {e['inside']}; outside: {e['outside']})" for e in i["edges"]]
        lines += [f"  - kind {k['id']}: {k['what']}" for k in i.get("kinds") or []]
    if plan.get("memory"):
        lines.append("Remembered across transactions:")
        lines += [
            f"- {m['id']} ({', '.join(m.get('clauses') or [])}): {m['what']}; starts again: {m['starts_again']}"
            for m in plan["memory"]
        ]
    return "\n".join(lines)
