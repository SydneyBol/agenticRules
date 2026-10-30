"""The test-case branch: the cases a manager would run in Offer Testing, written from the description alone.

It reads the same knowledge and tenant catalog as the run that builds the offer (the same system blocks) and never
sees the canvases, so its cases are an independent reading of the description. Each scenario is one mocked member
(profile attributes, and offer counters when it starts with some) and its transactions in order, with what each
should give. The cases are written once per run; a failed engine check repairs the blocks, never the cases.
"""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools import RunContext, _schema


class Expect(BaseModel):
    """What one transaction gives."""

    model_config = ConfigDict(extra="forbid")  # a key the schema does not have is an error, never dropped

    points: float = Field(0, description="points credited for this transaction (0 for none)")
    privileges: list[str] = Field(default_factory=list, description="privilege ids given (catalog ids)")


class CaseBit(BaseModel):
    """One transaction of a scenario, and what it gives."""

    header: dict[str, Any] = Field(
        description="h_bit_date (ISO date-time, inside the offer's dates), h_bit_amount, h_location (a store code "
        "from the catalog), h_bit_type when the activity is not Spend, and any other header attribute the offer reads"
    )
    expect: Expect | None = Field(description="what this transaction gives under the description; null = nothing")


class Scenario(BaseModel):
    """One mocked member and its transactions, in order."""

    what: str = Field(description="the clause or boundary this scenario tests")
    member: dict[str, Any] = Field(
        description='{"MA_P": profile attributes by code (e.g. tier_class, date_of_birth), "MA_O": offer counters by '
        "name, only when the scenario starts with some (counters otherwise start empty)}"
    )
    bits: list[CaseBit] = Field(description="the member's transactions, in order")


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
    "Offer Testing, with write_cases: each scenario a member and its transactions in order, and what each "
    "transaction should give under this description. Test every clause and the boundaries (just under, at and over "
    "a threshold; the transaction that crosses a cap; the Nth and the next; outside a window; a member the offer "
    "excludes). Use the tenant catalog's codes. Call write_cases once."
)


def write(ctx: RunContext) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """One model turn writing the cases.

    Returns:
        (the scenarios, or None when the turn wrote none that fit the schema; the turn's usage and time).
    """
    t0 = time.time()
    turn = (ctx.cases_llm or ctx.llm).turn(ctx.system, [HumanMessage(content=ctx.prompt + TASK)], tools=[WRITE_CASES])
    info = {"s": round(time.time() - t0, 1), "usage": turn.usage}
    call = next((c for c in turn.message.tool_calls if c["name"] == "write_cases"), None)
    try:
        cases = Cases.model_validate(call["args"]) if call else None
    except ValidationError:
        cases = None
    if cases is None or not cases.scenarios:
        return None, info
    return [s.model_dump() for s in cases.scenarios], info


def mismatch_message(report: dict[str, Any]) -> str:
    """The failed engine check, as the build run reads it: per transaction, the case, what it expects, what it got.

    Never the compiled rules: the scenario, the member, the transaction's header and the two results.
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
                **({"refused_before_the_rules": m["skipped"]} if m.get("skipped") else {}),
                **({"engine_error": s["error"]} if s.get("error") else {}),
            }
        )
    problems = [p for part in (report.get("compile") or {}).values() for p in part or []]
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
