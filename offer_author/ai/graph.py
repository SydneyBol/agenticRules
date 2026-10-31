r"""The agent's loop, as a LangGraph graph: a model turn, its tool calls, then the judge, until the run ends.

    START -> model -> tools -> judge -> model ... -> END
                  \-> nudge -> model                      (a turn that calls no tool)

model    one streamed turn; the tool calls the model finishes writing start at once (RunContext.start).
tools    LangGraph's ToolNode: each call validated against its tool's schema and run, in the order written
         (tools.started_or_run waits for a call already started).
judge    reads the turn's results: finish is judged after the turn's other calls.
nudge    a turn that called no tool is told to use them.

One graph run per request and no checkpointer: nothing is kept after the run. What the run's nodes and tools share
(model, tools as code, system prompt, settings, timing) is the run context (tools.RunContext); the state holds the
conversation, the turn count and limit, the usage, and how the run ended.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Send

from ..core import verifier
from ..core.run import Run, error_result, finish_blockers
from ..domain import Problem, Result, ToolInput
from . import cases, render
from .tools import SCHEMAS, TOOLS, Done, RunContext, content, done, schema_error, started_or_run

NUDGE = "Use the tools: compose, then finish."


class AgentState(TypedDict, total=False):
    """The graph's state.

    Attributes:
        messages: The conversation, append-only: earlier turns (and their thinking) are resent unchanged.
        turns: Model turns so far.
        max_turns: The turn limit (Settings.max_turns).
        usage: The model's token counts, summed over the turns.
        fin: The accepted finish fields, once the run may end.
        result: An early end (a refusal).
    """

    messages: Annotated[list[AnyMessage], add_messages]
    turns: int
    max_turns: int
    usage: dict[str, int]
    fin: dict[str, Any] | None
    result: dict[str, Any] | None


def cached(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The messages with a sliding cache point on the newest one (a copy: the history itself is never edited).

    A tool result carries its own tool_result block, so the cache point sits on that block as the API expects.
    """
    last, mark = messages[-1], {"cache_control": {"type": "ephemeral"}}
    if isinstance(last, ToolMessage):
        blocks: list[Any] = [{"type": "tool_result", "tool_use_id": last.tool_call_id, "content": last.content, **mark}]
    else:
        blocks = [{"type": "text", "text": last.content}] if isinstance(last.content, str) else list(last.content)
        blocks[-1] = {**blocks[-1], **mark}
    return [*messages[:-1], last.model_copy(update={"content": blocks})]


def model(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """One model turn: tool calls start as soon as each is written, a turn's lookups together once it is done."""
    ctx = runtime.context
    t0 = time.time()
    on_tool = ctx.start if ctx.settings.early_tools else None
    turn = ctx.llm.turn(ctx.system, cached(state["messages"]), tools=SCHEMAS, on_tool=on_tool)
    ctx.invalid |= turn.invalid
    calls = turn.message.tool_calls
    ctx.start_lookups(calls)
    ctx.timing.model_s += time.time() - t0
    ctx.timing.turns.append(
        {
            "s": round(time.time() - t0, 1),
            "out": turn.usage.get("output_tokens"),
            "calls": [f"{c['name']}:{len(json.dumps(c['args']))}" for c in calls],
        }
    )
    usage = dict(state.get("usage") or {})
    for k, v in turn.usage.items():
        usage[k] = usage.get(k, 0) + (v or 0)
    out: dict[str, Any] = {"turns": state.get("turns", 0) + 1, "usage": usage, "messages": [turn.message]}
    if turn.stop_reason == "refusal":
        out["result"] = error_result(Problem.of("model_declined"))
    return out


def nudge(state: AgentState) -> dict[str, Any]:
    """A turn that called no tool is told to use them."""
    return {"messages": [HumanMessage(content=NUDGE)]}


def judge_finish(run: Run, parts: Sequence[str], x: ToolInput) -> tuple[dict[str, Any] | None, Result]:
    """A finish call, judged after the turn's other calls (on the last turn too).

    Returns:
        (the accepted finish fields or None, the finish call's result).
    """
    wait = render.reply(finish_blockers(run, parts, x))
    if wait:
        return None, {"error": "not finished: fix these and compose again before finish", "problems": wait}
    return x, {"ok": True}


def traced(turn: int, name: str, x: ToolInput, out: Result, inputs: bool = False) -> dict[str, Any]:
    """One tool call in the run's trace (diagnostics)."""
    return {
        "turn": turn,
        "tool": name,
        **({"canvas": x["canvas"]} if x.get("canvas") else {}),
        **({"input": x} if inputs else {}),
        **{k: out[k] for k in ("ok", "problems", "error") if k in out},
    }


def _turn_results(messages: Sequence[BaseMessage]) -> tuple[AIMessage, dict[str, ToolMessage]]:
    """The newest model turn and its tool results, by tool call id."""
    i = max(i for i, m in enumerate(messages) if isinstance(m, AIMessage))
    results = {m.tool_call_id: m for m in messages[i + 1 :] if isinstance(m, ToolMessage)}
    return messages[i], results  # type: ignore[return-value]


def judge(state: AgentState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """Reads the turn's results: judges finish, traces every call."""
    ctx = runtime.context
    ai, results = _turn_results(state["messages"])
    fin = None
    judged: list[ToolMessage] = []
    for call in sorted(ai.tool_calls, key=lambda c: c["name"] == "finish"):
        msg = results[call["id"] or ""]
        x, out = done(msg)
        out = out or {}
        name = call["name"]
        if name == "finish":
            accepted, out = (
                judge_finish(ctx.run, ctx.parts, x)
                if isinstance(msg.artifact, Done)
                else (None, {"error": out.get("error")})
            )
            fin = fin if accepted is None else accepted
            judged.append(
                ToolMessage(
                    id=msg.id, content=content(out), tool_call_id=msg.tool_call_id, name=name, artifact=Done(x, out)
                )
            )
        ctx.run.trace.append(traced(state["turns"], name, x, out, ctx.settings.trace_inputs))
    return {"messages": judged, "fin": fin}


def _after_model(state: AgentState) -> str:
    if state.get("result"):
        return END
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else "nudge"


def _after_judge(state: AgentState) -> str:
    done_ = state.get("result") or state.get("fin") is not None or state["turns"] >= state["max_turns"]
    return END if done_ else "model"


def _after_nudge(state: AgentState) -> str:
    return END if state["turns"] >= state["max_turns"] else "model"


def build() -> CompiledStateGraph[AgentState, RunContext, Any, Any]:
    """The agent's graph (a few ms): built by each run."""
    g = StateGraph(AgentState, context_schema=RunContext)
    g.add_node("model", model)
    g.add_node("tools", ToolNode(TOOLS, wrap_tool_call=started_or_run, handle_tool_errors=schema_error))
    g.add_node("judge", judge)
    g.add_node("nudge", nudge)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", _after_model, ["tools", "nudge", END])
    g.add_edge("tools", "judge")
    g.add_conditional_edges("judge", _after_judge, ["model", END])
    g.add_conditional_edges("nudge", _after_nudge, ["model", END])
    return g.compile()


def recursion_limit(max_turns: int) -> int:
    """Graph steps a run of max_turns turns may take: model, tools and judge per turn, and a margin."""
    return 3 * max_turns + 4


# ---------------------------------------------------------------- the engine-checked run (TwoBranchVerify.md)
r"""The whole-offer run with an engine check: the offer and its test cases in parallel, then the engine.

    START -Send-> build  (the agent graph above: Offer Main, segment, rules) -> verify -> END
          -Send-> cases  (one turn: write_cases, from the description)       -/    |
                                                       mismatch, first time:  build (repair turn) -> verify -> END

Both branches read the same knowledge (the run's system blocks). verify sends the canvases, Offer Main and the cases to
the verifier service (compile + engine, mocked members). On a mismatch the build run gets one repair: it fixes the
blocks; the cases are never rewritten. A second mismatch, an unreachable verifier or no cases: the run ends with the
verdict recorded.
"""


class VerifiedState(AgentState, total=False):
    """The engine-checked run's state: the agent's, the cases, the verifier's verdict, and whether it repaired."""

    cases: list[dict[str, Any]] | None
    cases_info: dict[str, Any]
    verdict: dict[str, Any] | None
    repaired: bool


def fan_out(state: VerifiedState) -> list[Send]:
    """START: the build run and the test-case branch at once."""
    return [Send("build", state), Send("cases", state)]


def build_node(state: VerifiedState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """The agent graph, run to its end (or, after a failed check, on from where it ended)."""
    agent_state = {k: state[k] for k in ("messages", "turns", "max_turns", "usage", "fin", "result") if k in state}  # type: ignore[literal-required]
    config: RunnableConfig = {"recursion_limit": recursion_limit(state["max_turns"]), "max_concurrency": 1}
    out = build().invoke(agent_state, config, context=runtime.context)
    return {k: out[k] for k in ("messages", "turns", "usage", "fin", "result") if k in out}


def cases_node(state: VerifiedState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """The test-case branch: one turn writing the cases."""
    scenarios, info = cases.write(runtime.context)
    sc = scenarios or []
    aims = {a: sum(s.get("aim") == a for s in sc) for a in ("positive", "negative")}
    size = {"scenarios": len(sc), **aims, "bits": sum(len(s.get("bits") or []) for s in sc)}
    return {"cases": scenarios, "cases_info": {"s": info["s"], **size, **info["usage"]}}


def verify_node(state: VerifiedState, runtime: Runtime[RunContext]) -> dict[str, Any]:
    """The engine check; on the first mismatch, the repair message for the build run."""
    ctx = runtime.context
    if state.get("result") or state.get("fin") is None or not state.get("cases"):
        why = "the run did not finish" if state.get("fin") is None or state.get("result") else "no test cases"
        return {"verdict": {"skipped": why}}
    body = verifier.request(ctx.run, state["cases"] or [])
    if body is None:
        return {"verdict": {"skipped": "no rules"}}
    t0 = time.time()
    try:
        report = verifier.verify(ctx.settings.verifier_url, body)
    except verifier.VerifierUnavailableError as e:
        return {"verdict": {"error": str(e)[:300]}}
    ctx.timing.tools_s["verify"] = round(ctx.timing.tools_s.get("verify", 0) + time.time() - t0, 2)
    repaired = bool(state.get("repaired"))
    verdict = verifier.verdict(report, repaired)
    if report.get("ok") or repaired:
        return {"verdict": verdict}
    return {
        "verdict": verdict,
        "repaired": True,
        "fin": None,
        "max_turns": state["turns"] + ctx.settings.verify_repair_turns,
        "messages": [HumanMessage(content=cases.mismatch_message(report))],
    }


def _after_verify(state: VerifiedState) -> str:
    return "build" if state.get("fin") is None and state.get("repaired") and not _verified(state) else END


def _verified(state: VerifiedState) -> bool:
    """The check has its final word: it passed, or it already sent the build run its one repair and checked again."""
    v = state.get("verdict") or {}
    return bool(v.get("ok") or v.get("repaired") or "skipped" in v or "error" in v)


def build_verified() -> CompiledStateGraph[VerifiedState, RunContext, Any, Any]:
    """The engine-checked run's graph."""
    g = StateGraph(VerifiedState, context_schema=RunContext)
    g.add_node("build", build_node)
    g.add_node("cases", cases_node)
    g.add_node("verify", verify_node)
    g.add_conditional_edges(START, fan_out, ["build", "cases"])
    g.add_edge("build", "verify")
    g.add_edge("cases", "verify")
    g.add_conditional_edges("verify", _after_verify, ["build", END])
    return g.compile()
