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
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from ..core.run import Run, error_result, finish_blockers
from ..domain import Problem, Result, ToolInput
from . import render
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
