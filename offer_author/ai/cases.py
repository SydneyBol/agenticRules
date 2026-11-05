"""The test-case branch: the cases a manager would run in Offer Testing, written from the description alone.

It reads the same knowledge and tenant catalog as the run that builds the offer (the same system blocks) and never
sees the canvases, so its cases are an independent reading of the description. Each scenario is one mocked member
(profile attributes, and offer counters when it starts with some) and its transactions in order, with what each
should give. The cases are written once per run; a failed engine check repairs the blocks, never the cases.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import plan as plans
from .tools import RunContext, _schema


class Expect(BaseModel):
    """What one transaction gives."""

    model_config = ConfigDict(extra="forbid")  # a key the schema does not have is an error, never dropped

    points: float = Field(0, description="points credited for this transaction (0 for none)")
    privileges: list[str] = Field(
        default_factory=list, description="privilege ids given (catalog ids), an id once per unit given"
    )


class CaseBit(BaseModel):
    """One transaction of a scenario, and what it gives."""

    header: dict[str, Any] = Field(
        description="h_bit_date (ISO date-time in the program's own time zone, written without an offset, as Offer "
        "Testing sends it; inside the offer's dates), h_bit_amount, h_location (a store code "
        "from the catalog), h_bit_type when the activity is not Spend, and any other header attribute the offer reads. "
        "The engine processes each test transaction on its own h_bit_date, as a program sends a transaction when it "
        "happens: a segment's current date is that day"
    )
    lines: list[dict[str, Any]] = Field(
        default_factory=list,
        description="the transaction's line items, each {line attribute code (l_*): value}; a line names its "
        "product by l_product_external_id",
    )
    payment_details: list[dict[str, Any]] = Field(
        default_factory=list, description="the transaction's payments, each {payment attribute code: value}"
    )
    expect: Expect | None = Field(description="what this transaction gives under the description; null = nothing")


class Member(BaseModel):
    """The mocked member: profile attributes only.

    Offer counters start empty and change only through the scenario's transactions, as the engine counts them.
    """

    model_config = ConfigDict(extra="forbid")  # no offer counters: their names belong to the offer being built

    MA_P: dict[str, Any] = Field(
        default_factory=dict, description="profile attributes by code (e.g. tier_class, date_of_birth)"
    )


class Scenario(BaseModel):
    """One mocked member and its transactions, in order."""

    aim: Literal["positive", "negative"] = Field(
        description="positive: the member meets the clause and earns its reward; negative: one thing changed so the "
        "clause fails"
    )
    what: str = Field(description="the clause this scenario tests")
    member: Member
    products: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="the products the lines name (l_product_external_id -> {product attribute code: value}), mocked "
        "as the member is",
    )
    bits: list[CaseBit] = Field(description="the member's transactions, in order")
    covers: list[str] = Field(
        default_factory=list,
        description="the ids of the offer's plan this scenario covers (clauses, edges, kinds, what is remembered)",
    )


class Cases(BaseModel):
    """The test cases a loyalty manager would run in Offer Testing for this offer."""

    scenarios: list[Scenario]


WRITE_CASES = {
    "name": "write_cases",
    "description": "The test cases a loyalty manager would run in Offer Testing for this offer: scenarios, each a "
    "member and the transactions it makes in order, with what each transaction should give.",
    "input_schema": _schema(Cases),
}

TASK = (
    "\n\nDo not build the offer: another run builds it. Write the test cases a loyalty manager would run for it in "
    "Offer Testing, with write_cases. Each scenario is positive or negative. A positive scenario is a member whose "
    "transactions meet a clause, and expects the reward the description gives for it. A negative scenario changes one "
    "thing so that a clause fails (the amount just under the threshold, a store or day outside the offer, the "
    "transaction after a cap or limit is reached, a member the offer excludes), and expects what the offer still "
    "gives without that clause, often nothing. Give each clause one positive and one negative scenario; where the "
    "description names a threshold or an Nth transaction, the positive meets it exactly and the negative falls one "
    "short. Where a clause is a span of days, its cases include the first and last day and the day just outside "
    "each end; where the span follows a date that recurs every year, one member's span runs across 31 Dec. Where a "
    "clause reads a member's own date (a birthday, a joining date), the members' dates differ: one near each end of "
    "the year and one in a leap year after 28 Feb, and each of these members also makes a transaction the clause "
    "does not reward. Leave out "
    "any scenario that shows only what another one already shows. A member starts with no offer "
    "history: when a clause depends on what came before (after 4 visits, near a cap, a reward already given), the "
    "scenario makes those earlier transactions first, each with what it gives, and then the one under test. Use the "
    "tenant catalog's codes. Call write_cases once."
)


PLANNED = (
    "\n\nThe offer's plan, written from the same description; the run that builds the offer reads it too:\n{plan}"
    "\n\nCover the plan: every clause with a positive and a negative scenario, every edge from both sides (its "
    "inside and its outside value), every kind, and every remembered item before and after it is set. Each scenario "
    "lists in covers the plan ids it covers. Read every term as the plan's readings say."
)
MISSING = (
    "These plan ids are not covered by any scenario: {ids}. Call write_cases again with only the scenarios that "
    "cover them (the ones you wrote are kept)."
)
KEPT = "Kept."


def write(ctx: RunContext) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """One model turn writing the cases; with a plan, one more turn for the plan ids no scenario covers.

    Returns:
        (the scenarios, or None when the turn wrote none that fit the schema; the turns' usage and time, and the
        plan coverage).
    """
    t0 = time.time()
    llm = ctx.cases_llm or ctx.llm
    task = TASK + (PLANNED.format(plan=plans.text(ctx.plan)) if ctx.plan else "")
    if ctx.attributes:  # the data the terms refer to, confirmed once for the build and the cases (never its logic)
        task += "\n\n" + ctx.attributes
    messages: list[Any] = [HumanMessage(content=ctx.prompt + task)]
    turn = llm.turn(ctx.system, messages, tools=[WRITE_CASES])
    usage = dict(turn.usage or {})
    scenarios = _scenarios(turn)
    info: dict[str, Any] = {}
    if ctx.plan and scenarios is not None:
        missing = _uncovered(ctx.plan, scenarios)
        info["uncovered_first"] = missing
        if missing:
            # every call of the turn gets its result, or the next request is refused (2026-11-05 X01: two
            # write_cases calls in one turn, one result, Bedrock 400); the uncovered ids go in the first
            told = MISSING.format(ids=", ".join(missing))
            messages += [
                turn.message,
                *(
                    ToolMessage(content=told if i == 0 else KEPT, tool_call_id=c["id"], name=c["name"])
                    for i, c in enumerate(turn.message.tool_calls)
                ),
            ]
            more = llm.turn(ctx.system, messages, tools=[WRITE_CASES])
            for k, v in (more.usage or {}).items():
                usage[k] = usage.get(k, 0) + (v or 0)
            added = _scenarios(more)
            scenarios += added or []
            info["uncovered"] = _uncovered(ctx.plan, scenarios)
    info = {"s": round(time.time() - t0, 1), "usage": usage, **info}
    if not scenarios:
        return None, info
    return scenarios, info


def _scenarios(turn: Any) -> list[dict[str, Any]] | None:
    """The scenarios a turn wrote with write_cases, over all its calls (None when no call fits the schema)."""
    found: list[dict[str, Any]] | None = None
    for call in turn.message.tool_calls:
        if call["name"] != "write_cases":
            continue
        try:
            cases = Cases.model_validate(call["args"])
        except ValidationError:
            continue
        found = [*(found or []), *(s.model_dump() for s in cases.scenarios)]
    return found


def _uncovered(plan: dict[str, Any], scenarios: list[dict[str, Any]]) -> list[str]:
    covered = {x for s in scenarios for x in s.get("covers") or []}
    return [x for x in plans.to_cover(plan) if x not in covered]


def _counters_before(scenario: dict[str, Any], bit: int) -> dict[str, Any]:
    """The member's offer counters before a transaction: those after the one before it (none before the first)."""
    bits = scenario.get("bits") or []
    return (bits[bit - 1].get("counters_after") or {}) if 0 < bit <= len(bits) else {}


def _did(step: dict[str, Any]) -> str:
    """One action of a transaction as the builder reads it: the rule (its canvas IDENTIFIER) and what it did."""
    rule = step.get("rule") or "?"
    if "counter" in step:
        return f"{rule}: {step['counter']} {_num(step.get('old'))} -> {_num(step.get('new'))} ({step.get('operator')})"
    if "privilege" in step:
        return f"{rule}: privilege {step['privilege']}"
    if "points" in step:
        return f"{rule}: {_num(step['points'])} points"
    return f"{rule}: {next((f'{k} {v}' for k, v in step.items() if k != 'rule'), '')}".strip()


def _num(v: Any) -> Any:
    return int(v) if isinstance(v, float) and v.is_integer() else ("empty" if v in (None, "") else v)


def mismatch_message(report: dict[str, Any]) -> str:
    """The failed engine check, as the build run reads it: per transaction, the case, what it expects, what it got.

    Never the compiled rules: the scenario, the member, the transaction's header, the two results, the offer
    counters (the canvas's own names) before and after the transaction, and what each rule did on it, in the order
    the rules ran (their IDENTIFIERs: points, privileges, counter changes).
    """
    rows = []
    by_scenario = {s["scenario"]: s for s in report.get("scenarios") or []}
    for m in (report.get("mismatches") or [])[:12]:
        s = by_scenario.get(m["scenario"], {})
        rows.append(
            {
                "scenario": m.get("what"),
                "member_in_segment": m.get("member_in_segment"),
                "transaction": m["bit"] + 1,
                "header": {
                    k: v for k, v in m["header"].items() if k not in ("h_sponsor_id", "h_program_id", "h_member_id")
                },
                "expected": m["expect"],
                "engine_gave": m["got"],
                "offer_counters_before": _counters_before(s, m["bit"]),
                "offer_counters_after": m.get("counters_after") or {},
                "what_each_rule_did": [_did(x) for x in m.get("rules_ran") or []],
                **({"refused_before_the_rules": m["skipped"]} if m.get("skipped") else {}),
                **({"engine_error": s["error"]} if s.get("error") else {}),
            }
        )
    problems = [p for part in (report.get("compile") or {}).values() for p in part or []]
    if not rows:  # nothing ran: the rules the screen made did not compile in the engine (2026-11-05 C03)
        return (
            "The offer's test cases could not run in the engine on your canvases: the rules the stock screen made from "
            "them do not compile in the engine, though the screen reported "
            + (json.dumps(problems) if problems else "no problem")
            + ". A block reads something the screen leaves empty: a value block naming an attribute or counter its "
            "own list does not have. Fix the blocks (compose with edits on the segment or rules canvas), then finish."
        )
    text = (
        "The offer's test cases were run in the engine on your canvases, with mocked members as Offer Testing runs "
        "them. These transactions did not give what the cases expect:\n"
        + json.dumps(rows, indent=1)
        + (("\nThe stock screen also reported: " + json.dumps(problems)) if problems else "")
        + "\n\nThe test cases are fixed: they will not change. Fix the blocks (compose with edits on the segment or "
        "rules canvas) so the offer does what the description says, then finish. If a case misreads the description, "
        "keep the blocks and name that clause in an assumption, then finish."
    )
    return text
