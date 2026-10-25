"""The offer author: one agent that builds a REWARD offer from the manager's English, driven by tools.

The model solves the offer: it sets Offer Main and composes the member segment and the rules from toolbox blocks,
choosing only blocks, field values, picked catalog codes and the offer counters it needs. Everything it reads is in
prompt.py, everything it may call in tool_specs.py; every tool runs as code in core/session.py, which writes the
XML, checks it the way the UI does, compiles it in the stock screen and runs it in the real engine.
This file is only the loop: model turn -> tool calls -> results back, until finish.
"""
import json
import os
import time

from ..core import ui
from ..core.session import PARTS, Session, error_result, finish_blockers, outcome
from .llm import LLMError
from .prompt import intro, system_blocks
from .tool_specs import TOOLS

MAX_TURNS = int(os.environ.get("OFFER_AUTHOR_TURNS", 14))


def _cache_last(messages):
    """Sliding cache point on the newest message, so each turn re-reads the conversation so far from cache."""
    for m in messages:
        for b in m["content"] if isinstance(m["content"], list) else []:
            if isinstance(b, dict):
                b.pop("cache_control", None)
    last = messages[-1]
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    last["content"][-1]["cache_control"] = {"type": "ephemeral"}


def run(prompt, catalog, llm, offer_id=None, parts=PARTS, account_id=None, context=None, existing=None, on_offer_main=None):
    """One agent run for the given parts of an offer.
    context: what is already handled elsewhere (prompt.rules_context). existing: {offer_status, counters} already on
    the offer (core/counters.py); None = not known. on_offer_main(settings): called as soon as Offer Main is set.
    -> core.session.outcome, with the model's usage in diagnostics."""
    t0 = time.time()
    parts = tuple(p for p in PARTS if p in parts)
    system = system_blocks(catalog, account_id)
    messages = [{"role": "user", "content": intro(prompt, parts, context, existing)}]
    s, usage, fin, turns = Session(catalog, offer_id, existing, on_offer_main), {}, None, 0

    def done(res):
        res["diagnostics"].update(model=getattr(llm, "model", "?"), latency_s=round(time.time() - t0, 2), turns=turns, usage=usage)
        return res

    try:
        while turns < MAX_TURNS and fin is None:
            turns += 1
            _cache_last(messages)
            turn = llm.turn(system, messages, tools=TOOLS)
            for k, v in turn.usage.items():
                usage[k] = usage.get(k, 0) + (v or 0)
            if turn.stop_reason == "refusal":
                return done(error_result("The model declined this request. Please rephrase the offer description."))
            if not turn.tool_calls:
                messages += [{"role": "assistant", "content": turn.content},
                             {"role": "user", "content": "Use the tools: compile, simulate, then finish."}]
                continue
            results = []
            for tid, name, x in turn.tool_calls:
                if name == "finish":
                    wait = finish_blockers(s, parts, x) if turns < MAX_TURNS - 1 else []
                    if wait:
                        out = {"error": "not finished: fix these and compile again before finish", "problems": wait}
                    else:
                        fin, out = x, {"ok": True}
                else:
                    try:
                        out = s.run(name, x)
                    except ui.CompileUnavailable as e:
                        return done(error_result(f"The rules compiler is unavailable right now ({e})."))
                s.trace.append({"turn": turns, "tool": name, **({"canvas": x.get("canvas")} if x.get("canvas") else {}),
                                **{k: out[k] for k in ("ok", "problems", "error") if k in out},
                                **({"sim": [b.get("mismatch") or "ok" for b in out.get("bits", [])]} if name == "simulate" else {})})
                results.append({"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out)[:60000]})
            messages += [{"role": "assistant", "content": turn.content}, {"role": "user", "content": results}]
    except LLMError as e:
        return done(error_result(f"The rule generator is unavailable right now ({e}). Please try again."))
    return done(outcome(s, fin, parts, offer_id))


def generate(prompt, catalog, llm, offer_id=None, account_id=None, context=None, existing=None, **_):
    """The rules of an offer (the GenAI popup, the CLI, /ai/offer-author/generate)."""
    return run(prompt, catalog, llm, offer_id=offer_id, parts=("rules",), account_id=account_id, context=context,
               existing=existing)
