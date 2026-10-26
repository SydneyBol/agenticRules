"""The offer author: one agent that builds a REWARD offer from the manager's English, driven by tools.

The model solves the offer: it sets Offer Main and composes the member segment and the rules from toolbox blocks,
choosing only blocks, field values, picked catalog codes and the offer counters it needs. Everything it reads is in
prompt.py, everything it may call in tool_specs.py; every tool runs as code in core/session.py, which writes the
XML, checks it the way the UI does, compiles it in the stock screen and runs it in the real engine.
This file is only the loop, as a LangGraph graph: model turn -> tool calls -> results back, until finish.

    START -> model -> tools -> model ... -> END      (model -> nudge -> model when a turn calls no tool)

One graph run per request and no checkpointer: nothing is kept after the run. The per-run objects (llm, session,
system blocks, parts) come in through config["configurable"]; the state holds only the conversation and counters.
"""
import json
import operator
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from ..core import ui
from ..core.session import PARTS, Session, error_result, finish_blockers, outcome
from .llm import LLMError
from .prompt import intro, system_blocks
from .tool_specs import TOOLS

MAX_TURNS = int(os.environ.get("OFFER_AUTHOR_TURNS", 14))
PARALLEL = {"lookup_values"}  # read only the catalog: one turn's calls run together; the rest run in call order
NUDGE = "Use the tools: compile, simulate, then finish."


class AgentState(TypedDict, total=False):
    messages: Annotated[list, operator.add]  # append-only: earlier turns (and their thinking) are resent unchanged
    calls: list  # the last model turn's tool calls [(id, name, input)]
    turns: int
    usage: dict
    fin: dict | None  # the accepted finish input
    result: dict | None  # an early end (refusal, compiler unavailable)


def _cached(messages):
    """The messages with a sliding cache point on the newest one (a copy: the history itself is never edited)."""
    last = messages[-1]
    blocks = [{"type": "text", "text": last["content"]}] if isinstance(last["content"], str) else list(last["content"])
    blocks[-1] = dict(blocks[-1], cache_control={"type": "ephemeral"})
    return messages[:-1] + [dict(last, content=blocks)]


def _model(state, config):
    c = config["configurable"]
    t0 = time.time()
    turn = c["llm"].turn(c["system"], _cached(state["messages"]), tools=TOOLS)
    c["timing"]["model_s"] += time.time() - t0
    usage = dict(state.get("usage") or {})
    for k, v in turn.usage.items():
        usage[k] = usage.get(k, 0) + (v or 0)
    out = {"turns": state.get("turns", 0) + 1, "usage": usage, "calls": turn.tool_calls,
           "messages": [{"role": "assistant", "content": turn.content}]}
    if turn.stop_reason == "refusal":
        out["result"] = error_result("The model declined this request. Please rephrase the offer description.")
    return out


def _nudge(state, config):
    return {"messages": [{"role": "user", "content": NUDGE}]}


def _tools(state, config):
    c = config["configurable"]
    s, turns, fin = c["session"], state["turns"], None
    calls = state["calls"]
    disagreed = []  # this turn's simulate mismatches: a finish later in the same turn is sent back
    tools_s = c["timing"]["tools_s"]

    chars = c["timing"]["input_chars"]

    def timed(name, x):
        chars[name] = chars.get(name, 0) + len(json.dumps(x))
        t0 = time.time()
        try:
            return s.run(name, x)
        finally:
            tools_s[name] = tools_s.get(name, 0) + time.time() - t0

    with ThreadPoolExecutor(4) as pool:
        early = {tid: pool.submit(timed, name, x) for tid, name, x in calls if name in PARALLEL}
        results = []
        for tid, name, x in calls:
            if name == "finish":
                chars["finish"] = chars.get("finish", 0) + len(json.dumps(x))
                wait = finish_blockers(s, c["parts"], x) if turns < MAX_TURNS - 1 else []
                if disagreed and turns < MAX_TURNS - 1:
                    wait.append(f"the engine disagreed with your expectations in this turn ({'; '.join(disagreed[:3])}): "
                                "fix the blocks or the expectation, compile and simulate again")
                if wait:
                    out = {"error": "not finished: fix these and compile again before finish", "problems": wait}
                else:
                    fin, out = x, {"ok": True}
            else:
                try:
                    out = early[tid].result() if tid in early else timed(name, x)
                except ui.CompileUnavailable as e:
                    return {"result": error_result(f"The rules compiler is unavailable right now ({e}).")}
            if name == "simulate":
                disagreed += [str(bit["mismatch"]) for bit in out.get("bits") or [] if bit.get("mismatch")]
            s.trace.append({"turn": turns, "tool": name, **({"canvas": x.get("canvas")} if x.get("canvas") else {}),
                            **{k: out[k] for k in ("ok", "problems", "error") if k in out},
                            **({"sim": [b.get("mismatch") or "ok" for b in out.get("bits", [])]} if name == "simulate" else {})})
            results.append({"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out)[:60000]})
    return {"messages": [{"role": "user", "content": results}], "fin": fin}


def _after_model(state):
    return END if state.get("result") else ("tools" if state.get("calls") else "nudge")


def _after_tools(state):
    return END if state.get("result") or state.get("fin") is not None or state["turns"] >= MAX_TURNS else "model"


def _after_nudge(state):
    return END if state["turns"] >= MAX_TURNS else "model"


def build():
    g = StateGraph(AgentState)
    g.add_node("model", _model)
    g.add_node("tools", _tools)
    g.add_node("nudge", _nudge)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", _after_model, ["tools", "nudge", END])
    g.add_conditional_edges("tools", _after_tools, ["model", END])
    g.add_conditional_edges("nudge", _after_nudge, ["model", END])
    return g.compile()


GRAPH = build()


def run(prompt, catalog, llm, offer_id=None, parts=PARTS, account_id=None, context=None, existing=None, on_offer_main=None):
    """One agent run for the given parts of an offer.
    context: what is already handled elsewhere (prompt.rules_context). existing: {offer_status, counters} already on
    the offer (core/counters.py); None = not known. on_offer_main(settings): called as soon as Offer Main is set.
    -> core.session.outcome, with the model's usage in diagnostics."""
    t0 = time.time()
    parts = tuple(p for p in PARTS if p in parts)
    s = Session(catalog, offer_id, existing, on_offer_main)
    timing = {"model_s": 0.0, "tools_s": {}, "input_chars": {}}  # where the run's time went, per tool name
    config = {"configurable": {"llm": llm, "session": s, "system": system_blocks(catalog, account_id), "parts": parts,
                               "timing": timing},
              "recursion_limit": 2 * MAX_TURNS + 4}
    last = {"messages": [{"role": "user", "content": intro(prompt, parts, context, existing)}], "turns": 0, "usage": {}}

    def done(res):
        res["diagnostics"].update(model=getattr(llm, "model", "?"), latency_s=round(time.time() - t0, 2),
                                  turns=last.get("turns", 0), usage=last.get("usage") or {},
                                  timing={"model_s": round(timing["model_s"], 2),
                                          "tools_s": {k: round(v, 2) for k, v in timing["tools_s"].items()},
                                          "input_chars": timing["input_chars"]})
        return res

    try:
        for last in GRAPH.stream(last, config, stream_mode="values"):
            pass
    except LLMError as e:
        return done(error_result(f"The rule generator is unavailable right now ({e}). Please try again."))
    if last.get("result"):
        return done(last["result"])
    return done(outcome(s, last.get("fin"), parts, offer_id))


def generate(prompt, catalog, llm, offer_id=None, account_id=None, context=None, existing=None, **_):
    """The rules of an offer (the GenAI popup, the CLI, /ai/offer-author/generate)."""
    return run(prompt, catalog, llm, offer_id=offer_id, parts=("rules",), account_id=account_id, context=context,
               existing=existing)
