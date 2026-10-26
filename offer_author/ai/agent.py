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
import logging
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
EARLY = os.environ.get("OFFER_AUTHOR_EARLY_TOOLS", "1") != "0"  # start each tool call as soon as it has streamed
PARALLEL = {"lookup_values"}  # read only the catalog: one turn's calls run together; the rest run in call order
NUDGE = "Use the tools: compile, simulate, then finish."
log = logging.getLogger("offer_author.agent")


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

    def start(tid, name, x):  # a tool call the model has finished writing; finish is judged after the turn, lookups
        if name != "finish" and name not in PARALLEL:  # (catalog reads) run together in the tools step
            c["started"][tid] = c["worker"].submit(_run_tool, c, name, x)

    turn = c["llm"].turn(c["system"], _cached(state["messages"]), tools=TOOLS, on_tool=start if EARLY else None)
    c["timing"]["model_s"] += time.time() - t0
    c["timing"]["turns"].append({"s": round(time.time() - t0, 1), "out": turn.usage.get("output_tokens"),
                                 "calls": [f"{name}:{len(json.dumps(x))}" for _, name, x in turn.tool_calls]})
    usage = dict(state.get("usage") or {})
    for k, v in turn.usage.items():
        usage[k] = usage.get(k, 0) + (v or 0)
    out = {"turns": state.get("turns", 0) + 1, "usage": usage, "calls": turn.tool_calls,
           "messages": [{"role": "assistant", "content": turn.content}]}
    if turn.stop_reason == "refusal":
        out["result"] = error_result("The model declined this request. Please rephrase the offer description.")
    return out


def _run_tool(c, name, x):
    """One tool call on the run's session, timed. A malformed input the tool did not foresee goes back to the model
    as an error and the job goes on; an unavailable compiler ends the run (the tools step reports it)."""
    t = c["timing"]
    t["input_chars"][name] = t["input_chars"].get(name, 0) + len(json.dumps(x))
    t0 = time.time()
    try:
        return c["session"].run(name, x)
    except ui.CompileUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s failed on the model's input", name)
        return {"ok": False, "error": f"{name} could not read this input ({type(e).__name__}: {e}); check its shape "
                                      "against the tool schema and send it again"}
    finally:
        t["tools_s"][name] = t["tools_s"].get(name, 0) + time.time() - t0


def _nudge(state, config):
    return {"messages": [{"role": "user", "content": NUDGE}]}


def _tools(state, config):
    c = config["configurable"]
    s, turns, fin = c["session"], state["turns"], None
    calls = state["calls"]
    disagreed = []  # this turn's simulate mismatches: a finish later in the same turn is sent back
    chars = c["timing"]["input_chars"]
    timed = lambda name, x: _run_tool(c, name, x)  # noqa: E731

    notes = None  # the finish fields given with a simulate that confirmed the offer in this turn
    with ThreadPoolExecutor(4) as pool:
        early = {tid: c["started"].pop(tid) for tid, _, _ in calls if tid in c["started"]}  # started while streaming
        early.update({tid: pool.submit(timed, name, x) for tid, name, x in calls if name in PARALLEL and tid not in early})
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
                if x.get("readback") and confirms(x, out):
                    notes = {k: x[k] for k in FINISH_KEYS if k in x}
            s.trace.append({"turn": turns, "tool": name, **({"canvas": x.get("canvas")} if x.get("canvas") else {}),
                            **{k: out[k] for k in ("ok", "problems", "error") if k in out},
                            **({"sim": [b.get("mismatch") or "ok" for b in out.get("bits", [])]} if name == "simulate" else {})})
            results.append({"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out)[:60000]})
    if fin is None and notes is not None and not disagreed and not finish_blockers(s, c["parts"], notes) \
            and all(s.clean.get(cv) is last for cv, last in s.last.items()):
        fin = dict({"assumptions": [], "blocking_questions": [], "unsupported": []}, **notes)  # no finish turn needed
        s.trace.append({"turn": turns, "tool": "finish", "ok": True, "with": "simulate"})
    return {"messages": [{"role": "user", "content": results}], "fin": fin}


FINISH_KEYS = ("readback", "segment_name", "segment_description", "assumptions", "blocking_questions", "unsupported")


def confirms(x, out):
    """A simulate that confirms the offer: it ran, and every BIT stated what it expects and got it."""
    bits = out.get("bits") or []
    return (out.get("compile") == "ok" and bits and len(bits) == len(x.get("bits") or [])
            and all(isinstance(b, dict) and b.get("expect") for b in x["bits"]) and not any(r.get("mismatch") for r in bits))


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
    timing = {"model_s": 0.0, "tools_s": {}, "input_chars": {}, "turns": []}  # where the run's time went: per tool, per turn
    worker = ThreadPoolExecutor(1)  # tool calls started while the model streams: one at a time, in call order
    config = {"configurable": {"llm": llm, "session": s, "system": system_blocks(catalog, account_id), "parts": parts,
                               "timing": timing, "worker": worker, "started": {}},
              "recursion_limit": 2 * MAX_TURNS + 4}
    last = {"messages": [{"role": "user", "content": intro(prompt, parts, context, existing)}], "turns": 0, "usage": {}}

    def done(res):
        res["diagnostics"].update(model=getattr(llm, "model", "?"), latency_s=round(time.time() - t0, 2),
                                  turns=last.get("turns", 0), usage=last.get("usage") or {},
                                  timing={"model_s": round(timing["model_s"], 2),
                                          "tools_s": {k: round(v, 2) for k, v in timing["tools_s"].items()},
                                          "input_chars": timing["input_chars"], "turns": timing["turns"]})
        return res

    try:
        for last in GRAPH.stream(last, config, stream_mode="values"):
            pass
    except LLMError as e:
        return done(error_result(f"The rule generator is unavailable right now ({e}). Please try again."))
    finally:
        worker.shutdown(wait=True)
    if last.get("result"):
        return done(last["result"])
    return done(outcome(s, last.get("fin"), parts, offer_id))


def generate(prompt, catalog, llm, offer_id=None, account_id=None, context=None, existing=None, **_):
    """The rules of an offer (the GenAI popup, the CLI, /ai/offer-author/generate)."""
    return run(prompt, catalog, llm, offer_id=offer_id, parts=("rules",), account_id=account_id, context=context,
               existing=existing)
