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
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from ..core.run import PARTS, Run, error_result, outcome
from ..core.settings import Settings
from ..domain import Problem
from . import graph
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
) -> dict[str, Any]:
    """The parts of an offer the job asks for (arguments as _run's).

    A job with Offer Main and other parts runs Offer Main first, on its own (so the Offer Main screen is answered
    before the rules are designed), then the segment and rules with that Offer Main as given (OfferMainFirst.md).
    """
    parts = tuple(p for p in PARTS if p in parts)
    kw: dict[str, Any] = dict(offer_id=offer_id, account_id=account_id, existing=existing, history=history)
    if "offer_main" not in parts or len(parts) == 1:
        return _run(
            prompt,
            catalog,
            llm,
            parts=parts,
            context=context,
            on_offer_main=on_offer_main,
            settings=settings,
            cases_llm=cases_llm,
            **kw,
        )
    t0 = time.time()
    settings = settings or Settings()
    rest = tuple(p for p in parts if p != "offer_main")
    with ThreadPoolExecutor(1) as pool:  # the earlier offers are for the build: looked up while Offer Main is set
        found = pool.submit(_earlier, prompt, catalog, settings, offer_id, history, rest)
        main = _run(
            prompt,
            catalog,
            llm,
            parts=("offer_main",),
            context=context,
            on_offer_main=on_offer_main,
            settings=replace(settings, max_turns=min(OFFER_MAIN_TURNS, settings.max_turns)),
            **kw,
        )
        om = main.get("offer_main")
        if not om:  # not an offer, or a question first: that answer is the job's
            return _whole_job(main, parts, t0)
        given = offer_main_context(om)
        build = _run(
            prompt,
            catalog,
            llm,
            parts=rest,
            context=f"{context}\n\n{given}" if context else given,
            settings=settings,
            cases_llm=cases_llm,
            offer_main=om,
            verify=True,
            earlier_found=found,
            **kw,
        )
    return _merged(main, build, parts, t0)


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
    earlier_found: Future[tuple[dict[str, Any] | None, float]] | None = None,
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
        earlier_found: The earlier-offer lookup, already started (_earlier's result).

    Returns:
        core.run.outcome, with the model's usage and the run's timing in diagnostics.
    """
    t0 = time.time()
    settings = settings or Settings()
    parts = tuple(p for p in PARTS if p in parts)
    tools = Run(catalog, offer_id, existing, on_offer_main, search=searcher(settings))
    if offer_main:
        tools.offer_main.value = dict(offer_main)
    earlier, earlier_s = (
        earlier_found.result() if earlier_found else _earlier(prompt, catalog, settings, offer_id, history, parts)
    )
    ctx = RunContext(llm, tools, _system(catalog, account_id, settings), parts, settings, prompt, cases_llm)
    checked = bool(settings.verifier_url) and "rules" in parts and ("offer_main" in parts or verify)
    current = tools.current() if "rules" in parts else None
    start: dict[str, Any] = {
        "messages": [HumanMessage(content=intro(prompt, parts, context, existing, current, earlier))],
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
                res["diagnostics"]["verify"]["evidence"] = {"cases": last["cases"], "checks": last.get("checks") or []}
        if earlier:  # which earlier offers the model was shown
            res["diagnostics"]["earlier"] = {
                "named": [e.name for e in earlier["named"]],
                "s": round(earlier_s, 2),
                "similar": [[e.id, round(sc, 3)] for sc, e in earlier["similar"]],
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
