"""The offer author: one agent that builds a REWARD offer from the manager's English, driven by tools.

The model solves the offer: it sets Offer Main and composes the member segment and the rules from toolbox blocks,
choosing only blocks, field values, picked catalog codes and the offer counters it needs. What it reads is in
prompt.py, what it may call in tools.py; every tool runs as code (core/run/), which writes the XML and checks the
blocks' compatibility with data (the block reference, the tenant catalog). The loop is a LangGraph graph (graph.py).

Everything starts in run(): it builds the graph, the system prompt and the run's tools, and nothing is set up when
this module is imported. The entry point (api/service.py, api/cli.py) hands down the Settings it read; a library caller
may leave them out (the code defaults).
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, TypedDict

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..core.run import PARTS, Run, error_result, outcome
from ..core.settings import Settings
from ..domain import Problem
from . import attributes as grounding
from . import graph
from . import plan as plans
from .model import LLMError
from .prompt import intro, offer_main_context, system_blocks
from .tools import SCHEMAS, RunContext

if TYPE_CHECKING:
    from ..core.tenant.catalog import Catalog
    from ..core.tenant.history import History
    from ..core.tenant.value_search import ValueSearch

log = logging.getLogger("offer_author.agent")
OFFER_MAIN_TURNS = 3  # turns the Offer Main run of a whole-offer job may take to get the form accepted


def run(
    prompt: str,
    catalog: Catalog,
    llm: Any,
    offer_id: int | None = None,
    parts: Sequence[str] = PARTS,
    account_id: Any = None,
    context: str | None = None,
    existing: dict[str, Any] | None = None,
    on_offer_main: Callable[[dict[str, Any]], None] | None = None,
    settings: Settings | None = None,
    history: History | None = None,
    cases_llm: Any = None,
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The parts of an offer the job asks for (arguments as _run's).

    A job with Offer Main and other parts runs Offer Main first, on its own (so the Offer Main screen is answered
    before the rules are designed), then the segment and rules with that Offer Main as given (OfferMainFirst.md).
    With settings.attributes on, the data the description's terms refer to is confirmed once, alongside Offer Main,
    and both the build and the cases read it (AttributeGrounding.md); a rules or segment job is given the attributes
    an earlier job of the offer confirmed ({value, prompt}: its diagnostics.attributes), reused when its description
    says nothing new.
    """
    t0 = time.time()
    settings = settings or Settings()
    parts = tuple(p for p in PARTS if p in parts)
    kw: dict[str, Any] = dict(offer_id=offer_id, account_id=account_id, existing=existing, history=history)
    if "offer_main" not in parts or len(parts) == 1:
        return _single(prompt, catalog, llm, parts, context, on_offer_main, settings, cases_llm, attributes, t0, kw)
    rest = tuple(p for p in parts if p != "offer_main")
    planned = settings.shared_plan and bool(settings.verifier_url) and "rules" in rest
    grounded = settings.attributes and bool(set(rest) & {"rules", "segment"})

    def attributes_node(state: JobState) -> dict[str, Any]:
        # beside Offer Main: the side runs' client (the cases', as the plan's), so the two runs never share a script
        attrs, info = _grounding(prompt, catalog, cases_llm or llm, settings, account_id) if grounded else (None, {})
        return {"attributes": attrs, "attributes_info": info}

    def plan_node(state: JobState) -> dict[str, Any]:
        if not planned:
            return {}
        try:
            plan, info = plans.write(cases_llm or llm, _system(catalog, account_id, settings), prompt, catalog)
        except LLMError as e:  # the job goes on as before the plan: each run reads the description alone
            return {"plan_info": {"error": str(e)[:300]}}
        return {"plan": plan, "plan_info": info}

    def earlier_node(state: JobState) -> dict[str, Any]:
        return {"earlier": _earlier(prompt, catalog, settings, offer_id, history, rest)}

    def offer_main_node(state: JobState) -> dict[str, Any]:
        main = _run(
            prompt,
            catalog,
            llm,
            parts=("offer_main",),
            context=context,  # answered at once: the Offer Main popup waits on it, so it does not wait for the plan
            on_offer_main=on_offer_main,
            settings=replace(settings, max_turns=min(OFFER_MAIN_TURNS, settings.max_turns)),
            **kw,
        )
        return {"main": main}

    def build_node(state: JobState) -> dict[str, Any]:
        om = state["main"]["offer_main"]
        hint = grounding.hint(state.get("attributes"), catalog)
        told = f"{context}\n\n{offer_main_context(om)}" if context else offer_main_context(om)
        build = _run(
            prompt,
            catalog,
            llm,
            parts=rest,
            context=_with_plan("\n\n".join(c for c in (told, hint) if c), state.get("plan")),
            settings=settings,
            attributes_hint=hint,
            cases_llm=cases_llm,
            offer_main=om,
            verify=True,
            earlier=state.get("earlier"),
            plan=state.get("plan"),
            **kw,
        )
        return {"build": build}

    def prepared(state: JobState) -> str:
        # not an offer, or a question first; or a term the description leaves open, asked before either branch reads it
        if not state["main"].get("offer_main") or grounding.questions(state.get("attributes")):
            return END
        return "build"

    g = StateGraph(JobState)
    g.add_node("plan", plan_node)
    g.add_node("earlier", earlier_node)
    g.add_node("offer_main", offer_main_node)
    g.add_node("build", build_node)
    g.add_node("attributes", attributes_node)
    # Offer Main (the popup waits on it), the attributes, the plan and the earlier offers at once; the build run (and
    # its test cases) starts from all of them
    g.add_conditional_edges(
        START,
        lambda st: [Send("offer_main", st), Send("attributes", st), Send("plan", st), Send("earlier", st)],
    )
    g.add_node("prepared", lambda st: {})
    g.add_edge(["offer_main", "attributes", "plan", "earlier"], "prepared")
    g.add_conditional_edges("prepared", prepared, ["build", END])
    g.add_edge("build", END)
    job: Any = g.compile()
    out: dict[str, Any] = job.invoke({})
    return _job_answer(out, parts, t0, prompt, catalog, grounded)


def _job_answer(
    out: dict[str, Any], parts: Sequence[str], t0: float, prompt: str, catalog: Catalog, grounded: bool
) -> dict[str, Any]:
    """A whole-offer job's answer from its graph's final state."""
    main = out["main"]
    attrs, info = out.get("attributes"), out.get("attributes_info") or {}
    if "build" not in out and main.get("offer_main") and (asked := grounding.questions(attrs)):
        return _asked(asked, parts, t0, main=main, attrs_info=_kept(info, attrs, prompt))
    if "build" not in out:  # not an offer, or a question first: that answer is the job's (the attributes are dropped)
        return _whole_job(main, parts, t0)
    res = _merged(main, out["build"], parts, t0)
    if out.get("plan_info") is not None:
        res["diagnostics"]["plan"] = out["plan_info"]
    return _noted(res, attrs, info, prompt, catalog) if grounded else res


def _single(
    prompt: str,
    catalog: Catalog,
    llm: Any,
    parts: tuple[str, ...],
    context: str | None,
    on_offer_main: Callable[[dict[str, Any]], None] | None,
    settings: Settings,
    cases_llm: Any,
    stored: dict[str, Any] | None,
    t0: float,
    kw: dict[str, Any],
) -> dict[str, Any]:
    """A job of one part; a rules or segment job confirms its attributes first (or reuses the offer job's)."""
    grounded = settings.attributes and parts in (("rules",), ("segment",))
    attrs, info = (
        _grounding(prompt, catalog, llm, settings, kw["account_id"], stored, (kw["existing"] or {}).get("rules_xml"))
        if grounded
        else (None, {})
    )
    if asked := grounding.questions(attrs):
        return _asked(asked, parts, t0, attrs_info=_kept(info, attrs, prompt))
    hint = grounding.hint(attrs, catalog)
    res = _run(
        prompt,
        catalog,
        llm,
        parts=parts,
        context="\n\n".join(c for c in (context, hint) if c) or None,
        on_offer_main=on_offer_main,
        settings=settings,
        cases_llm=cases_llm,
        attributes_hint=hint,
        **kw,
    )
    return _noted(res, attrs, info, prompt, catalog) if grounded else res


class JobState(TypedDict, total=False):
    """A whole-offer job's graph state: what both runs start from (the plan, the earlier offers), then each run."""

    plan: dict[str, Any] | None
    plan_info: dict[str, Any]
    attributes: dict[str, Any] | None
    attributes_info: dict[str, Any]
    earlier: tuple[dict[str, Any] | None, float]
    main: dict[str, Any]
    build: dict[str, Any]


def _with_plan(context: str | None, plan: dict[str, Any] | None) -> str | None:
    """A run's context with the offer's plan after it, when there is one."""
    if not plan:
        return context
    told = "The offer's plan, written from the same description; its test cases are written from it too:\n"
    return f"{context}\n\n{told}{plans.text(plan)}" if context else told + plans.text(plan)


def _whole_job(main: dict[str, Any], parts: Sequence[str], t0: float) -> dict[str, Any]:
    """The Offer Main run's answer as the whole job's, when it set no Offer Main: its notes stand for every part."""
    by_part = main.get("by_part") or {}
    own = by_part.get("offer_main") or {}
    main["by_part"] = {p: by_part.get(p) or copy.deepcopy(own) for p in parts}
    main["diagnostics"].update(parts=list(parts), latency_s=round(time.time() - t0, 2))
    return main


def _merged(main: dict[str, Any], build: dict[str, Any], parts: Sequence[str], t0: float) -> dict[str, Any]:
    """One job's result from its Offer Main run and its build run: each part from the run that built it."""
    res = dict(build)
    res["offer_main"], res["offer_settings"] = main.get("offer_main"), main.get("offer_settings")
    om_notes = {
        k: [*v, *build["by_part"].get("offer_main", {}).get(k, [])] for k, v in main["by_part"]["offer_main"].items()
    }
    res["by_part"] = {**build["by_part"], "offer_main": om_notes}
    for key in ("assumptions", "blocking_questions", "unsupported"):
        items = [*main.get(key, []), *build.get(key, [])]
        res[key] = [x for i, x in enumerate(items) if x not in items[:i]]  # unsupported items are dicts
    if res["blocking_questions"] and res["status"] == "draft":
        res["status"] = "needs_input"
    res["readback"] = " ".join(r for r in (main.get("readback"), build.get("readback")) if r)
    md, bd = main["diagnostics"], build["diagnostics"]
    usage = dict(bd.get("usage") or {})
    for k, v in (md.get("usage") or {}).items():
        usage[k] = usage.get(k, 0) + (v or 0)
    res["diagnostics"] = {
        **bd,
        "parts": list(parts),
        "latency_s": round(time.time() - t0, 2),
        "turns": md.get("turns", 0) + bd.get("turns", 0),
        "usage": usage,
        "offer_main_run": {k: md.get(k) for k in ("latency_s", "turns", "usage", "timing")},
    }
    return res


def _run(
    prompt: str,
    catalog: Catalog,
    llm: Any,
    offer_id: int | None = None,
    parts: Sequence[str] = PARTS,
    account_id: Any = None,
    context: str | None = None,
    existing: dict[str, Any] | None = None,
    on_offer_main: Callable[[dict[str, Any]], None] | None = None,
    settings: Settings | None = None,
    history: History | None = None,
    cases_llm: Any = None,
    offer_main: dict[str, Any] | None = None,
    verify: bool = False,
    earlier: tuple[dict[str, Any] | None, float] | None = None,
    plan: dict[str, Any] | None = None,
    attributes_hint: str = "",
) -> dict[str, Any]:
    """One agent run for the given parts of an offer.

    A whole-offer run (Offer Main and rules) with a verifier configured is engine-checked: its test cases are written
    in parallel and its canvases are run against them (graph.build_verified, TwoBranchVerify.md).

    Args:
        prompt: The manager's English.
        catalog: The tenant's catalog.
        llm: The chat model (model.ChatLLM, or a scripted one in tests).
        offer_id: The offer the rules are for, or None.
        parts: The parts of the offer to build (offer_main, segment, rules).
        account_id: The loyalty account the offer is for, when the catalog has several.
        context: What is already handled elsewhere (prompt.rules_context).
        existing: What the offer already has: offer_status, counters, rules_xml (core/tenant/existing_counters.py);
            None when not known.
        on_offer_main: Called with Offer Main as soon as it is set.
        settings: The run's settings (default: the code defaults).
        history: The tenant's earlier offers (default: from settings.history when settings.similar_offers is on,
            else none).
        cases_llm: The chat model of the test-case branch (default: llm).
        offer_main: Offer Main already accepted for this offer (an earlier run of the same job): the run holds it
            without setting it again, and the engine check applies its dates and limits.
        verify: Engine-check the rules although Offer Main is not one of the parts (it was set by an earlier run).
        earlier: The earlier-offer lookup, already made (_earlier's result).
        plan: The offer's plan (plan.py): the test cases cover it; the build run was given it in context.
        attributes_hint: The confirmed attributes (attributes.hint): the test cases read them; the build run was given
            them in context.

    Returns:
        core.run.outcome, with the model's usage and the run's timing in diagnostics.
    """
    t0 = time.time()
    settings = settings or Settings()
    parts = tuple(p for p in PARTS if p in parts)
    tools = Run(catalog, offer_id, existing, on_offer_main, search=searcher(settings))
    if offer_main:
        tools.offer_main.value = dict(offer_main)
    found, earlier_s = earlier or _earlier(prompt, catalog, settings, offer_id, history, parts)
    ctx = RunContext(
        llm, tools, _system(catalog, account_id, settings), parts, settings, prompt, cases_llm, plan, attributes_hint
    )
    checked = bool(settings.verifier_url) and "rules" in parts and ("offer_main" in parts or verify)
    current = tools.current() if "rules" in parts else None
    start: dict[str, Any] = {
        "messages": [HumanMessage(content=intro(prompt, parts, context, existing, current, found))],
        "turns": 0,
        "max_turns": settings.max_turns,
        "usage": {},
    }
    last: dict[str, Any] = start  # the newest state

    def done(res: dict[str, Any]) -> dict[str, Any]:
        res["diagnostics"].update(
            model=getattr(llm, "model", "?"),
            latency_s=round(time.time() - t0, 2),
            turns=last.get("turns", 0),
            usage=last.get("usage") or {},
            timing=ctx.timing.as_dict(),
        )
        if last.get("verdict"):
            _verdict(res, last["verdict"], last.get("cases_info"), last.get("repair"))
            if last.get("cases"):  # the agent's test cases and every check's answers, whatever the verdict
                res["diagnostics"]["verify"]["evidence"] = {
                    "cases": last["cases"],
                    "checks": last.get("checks") or [],
                    **({"plan": plan} if plan else {}),
                }
        if found:  # which earlier offers the model was shown
            res["diagnostics"]["earlier"] = {
                "named": [e.name for e in found["named"]],
                "s": round(earlier_s, 2),
                "similar": [[e.id, round(sc, 3)] for sc, e in found["similar"]],
            }
        return res

    config: RunnableConfig = {"recursion_limit": graph.recursion_limit(settings.max_turns), "max_concurrency": 1}
    run_graph: Any = graph.build()
    if checked:  # the build run and the test cases at once; the build run's own steps stay one at a time
        config = {"recursion_limit": 12, "max_concurrency": 2}
        run_graph = graph.build_verified()
    try:
        for state in run_graph.stream(start, config, context=ctx, stream_mode="values"):
            last = state
    except LLMError as e:
        return done(error_result(Problem.of("generator_unavailable", error=str(e))))
    finally:
        ctx.close()
    if last.get("result"):
        return done(last["result"])
    # notes the model tags for an Offer Main it was given land on that part, not on every part
    return done(outcome(tools, last.get("fin"), ("offer_main", *parts) if offer_main else parts, offer_id))


def _grounding(
    prompt: str,
    catalog: Catalog,
    llm: Any,
    settings: Settings,
    account_id: Any,
    stored: dict[str, Any] | None = None,
    saved_xml: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The description's attributes (attributes.ground), or the stored ones when the description says nothing new.

    Stored ones ({value, prompt}: an earlier job of this offer) are reused as they are when this description adds no
    word to the one they were confirmed for (an edited amount or date adds none); otherwise, and for saved rules
    edited in place, the agent starts from them (and from what the saved rules read). Never fails the run: without
    attributes it goes on as before (the hint is only a hint).

    Returns:
        (the attributes or None, info for the diagnostics).
    """
    before = (stored or {}).get("value")
    if before and not saved_xml and not _new_words(prompt, (stored or {}).get("prompt") or ""):
        return before, {"reused": True, "s": 0.0}
    start = grounding.merged(before, grounding.from_canvas(saved_xml, catalog))
    try:
        return grounding.ground(llm, _system(catalog, account_id, settings), prompt, catalog, searcher(settings), start)
    except LLMError as e:
        return None, {"error": str(e)[:300]}
    except Exception as e:  # the build and the cases read the description alone, as without the step
        log.exception("attribute step failed")
        return None, {"error": f"{type(e).__name__}: {e}"[:300]}


def _new_words(prompt: str, before: str) -> set[str]:
    """The description's telling words the earlier one lacked.

    Numbers aside (any word with a digit: an amount, an ordinal "5th", a date), and function words.
    """
    from ..core.tenant.attribute_search import function_words
    from ..core.tenant.catalog import norm

    def telling(text: str) -> set[str]:
        return {w for w in norm(text).split() if not _numeric(w) and w not in function_words()}

    return telling(prompt) - telling(before)


def _numeric(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def _kept(info: dict[str, Any], attrs: dict[str, Any] | None, prompt: str) -> dict[str, Any]:
    """The attribute step in the diagnostics: its info, and what a later rules or segment job may reuse."""
    return {**info, **({"value": attrs, "prompt": prompt} if attrs else {})}


def _noted(
    res: dict[str, Any], attrs: dict[str, Any] | None, info: dict[str, Any], prompt: str, catalog: Catalog
) -> dict[str, Any]:
    """The result with the attribute step in its diagnostics and each settled ambiguity as an assumption."""
    res.setdefault("diagnostics", {})["attributes"] = _kept(info, attrs, prompt)
    notes = grounding.assumptions(attrs, catalog)
    if notes and res.get("status") != "error":
        res["assumptions"] = [*res.get("assumptions", []), *notes]
        for part in ("rules", "segment"):
            if part in (res.get("by_part") or {}):
                res["by_part"][part]["assumptions"] = [*res["by_part"][part].get("assumptions", []), *notes]
    return res


def _asked(
    questions: list[str],
    parts: Sequence[str],
    t0: float,
    main: dict[str, Any] | None = None,
    attrs_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The job's answer when a term the description leaves open must be asked first: nothing built from it.

    Offer Main (already answered on its screen) is kept; the segment and the rules ask the manager.
    """
    rest = [p for p in parts if p != "offer_main"]
    res: dict[str, Any] = {
        "status": "needs_input",
        "readback": "",
        "assumptions": [],
        "blocking_questions": list(questions),
        "unsupported": [],
        "by_part": {p: {"assumptions": [], "blocking_questions": list(questions), "unsupported": []} for p in rest},
        "offer_main": None,
        "offer_settings": None,
        "segment": None,
        "rules": None,
        "diagnostics": {"parts": list(parts), "attributes": attrs_info or {}},
    }
    if main:
        res.update(offer_main=main.get("offer_main"), offer_settings=main.get("offer_settings"))
        res["readback"] = main.get("readback") or ""
        res["assumptions"] = list(main.get("assumptions") or [])
        if "offer_main" in (main.get("by_part") or {}):
            res["by_part"]["offer_main"] = main["by_part"]["offer_main"]
        md = main.get("diagnostics") or {}
        res["diagnostics"]["offer_main_run"] = {k: md.get(k) for k in ("latency_s", "turns", "usage", "timing")}
    res["diagnostics"]["latency_s"] = round(time.time() - t0, 2)
    return res


def _verdict(
    res: dict[str, Any], verdict: dict[str, Any], cases_info: dict[str, Any] | None, repair: dict[str, Any] | None
) -> None:
    """The engine check in the result.

    In the diagnostics (with the repair's evidence when there was one), and for the manager when transactions still
    disagree.
    """
    res.setdefault("diagnostics", {})["verify"] = {
        **verdict,
        **({"cases": cases_info} if cases_info else {}),
        **({"repair": repair} if repair else {}),
    }
    if verdict.get("mismatches") and res.get("rules"):
        note = Problem.of("verify_mismatch", count=verdict["mismatches"], bits=verdict.get("bits"))
        res["assumptions"] = [*res.get("assumptions", []), note]
        res.setdefault("by_part", {}).setdefault("rules", {}).setdefault("assumptions", []).append(note)


def warm(llm: Any, catalog: Catalog, account_id: Any = None, settings: Settings | None = None) -> dict[str, int] | None:
    """Writes a run's shared prefix (tools, system prompt, tenant catalog) to the prompt cache ahead of the run.

    So the first request after an idle spell does not pay the cold write (2-6 s, measured 2026-10-27). Loads the
    tenant's earlier offers too when they are on (embedding them takes ~3.5 s once), and the value search. A client
    without warm() (scripted tests) is left alone.

    Returns:
        The warm call's usage, or None.
    """
    settings = settings or Settings()
    if not hasattr(llm, "warm"):
        return None
    warm_history(catalog, settings)
    warm_values(catalog, settings)
    return llm.warm(_system(catalog, account_id, settings), SCHEMAS)


def warm_history(catalog: Catalog, settings: Settings | None = None) -> None:
    """Embeds the tenant's earlier offers and matches one request, ahead of the first run (when they are on).

    The first match after start-up took 8.4-9.2 s (the offers' embeddings and the embeddings client), later ones
    ~0.4 s (iterations 7-9, 2026-11-04).
    """
    settings = settings or Settings()
    if not (settings.similar_offers and settings.history):
        return
    from ..core.tenant.embed import histories

    try:
        history = histories(settings.history, settings.embed_model).get(catalog.tenant, catalog)
        if history is not None:
            history.similar("warm-up", k=1)
    except Exception:  # reference only: the run loads it again, or goes without
        log.exception("earlier offers not loaded")


def searcher(settings: Settings) -> ValueSearch | None:
    """What lookup_values finds values by meaning with (core/tenant/value_search.py).

    None when it is off in the settings, or the models cannot be loaded (then values are found by their words).
    """
    if not settings.value_search:
        return None
    try:
        from ..core.tenant.value_search import value_search

        return value_search(settings.value_embed_model, settings.value_rerank_model)
    except Exception:
        log.exception("value search unavailable; lookup_values ranks by words only")
        return None


def warm_values(catalog: Catalog, settings: Settings | None = None) -> None:
    """Loads the value-search models and embeds the tenant's value lists ahead of the first lookup."""
    search = searcher(settings or Settings())
    if search is not None:
        try:
            search.warm(catalog)
        except Exception:
            log.exception("value search warm-up failed")


def _system(catalog: Catalog, account_id: Any, settings: Settings) -> list[dict[str, Any]]:
    """The run's system prompt (prompt.system_blocks) as the settings set it."""
    return system_blocks(
        catalog,
        account_id,
        settings.cache_ttl,
        bool(settings.similar_offers and settings.history),
        settings.inline_activity_types,
    )


def _earlier(
    prompt: str,
    catalog: Catalog,
    settings: Settings,
    offer_id: int | None,
    history: History | None,
    parts: Sequence[str],
) -> tuple[dict[str, Any] | None, float]:
    """The tenant's earlier offers for this request, when settings.similar_offers is on and the rules are built.

    Returns:
        ({named, similar} or None, the seconds it took).
    """
    t = time.time()
    if not settings.similar_offers or "rules" not in parts:
        return None, 0.0
    try:
        if history is None and settings.history:
            from ..core.tenant.embed import histories

            history = histories(settings.history, settings.embed_model).get(catalog.tenant, catalog)
        found = history and history.earlier(prompt, settings.similar_offers, offer_id)
    except Exception:  # reference only: a run without it is still a run
        log.exception("earlier offers unavailable")
        found = None
    return found or None, time.time() - t


def generate(
    prompt: str,
    catalog: Catalog,
    llm: Any,
    offer_id: int | None = None,
    account_id: Any = None,
    context: str | None = None,
    existing: dict[str, Any] | None = None,
    settings: Settings | None = None,
    history: History | None = None,
    **_: Any,
) -> dict[str, Any]:
    """The rules of an offer (the GenAI popup and the CLI)."""
    return run(
        prompt,
        catalog,
        llm,
        offer_id=offer_id,
        parts=("rules",),
        account_id=account_id,
        context=context,
        existing=existing,
        settings=settings,
        history=history,
    )
