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

import logging
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from ..core.run import PARTS, Run, error_result, outcome
from ..core.settings import Settings
from ..domain import Problem
from . import graph
from .model import LLMError
from .prompt import intro, system_blocks
from .tools import SCHEMAS, RunContext

if TYPE_CHECKING:
    from ..core.tenant.catalog import Catalog
    from ..core.tenant.history import History
    from ..core.tenant.value_search import ValueSearch

log = logging.getLogger("offer_author.agent")


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

    Returns:
        core.run.outcome, with the model's usage and the run's timing in diagnostics.
    """
    t0 = time.time()
    settings = settings or Settings()
    parts = tuple(p for p in PARTS if p in parts)
    tools = Run(catalog, offer_id, existing, on_offer_main, search=searcher(settings))
    earlier, earlier_s = _earlier(prompt, catalog, settings, offer_id, history, parts)
    ctx = RunContext(llm, tools, _system(catalog, account_id, settings), parts, settings, prompt, cases_llm)
    checked = bool(settings.verifier_url) and "offer_main" in parts and "rules" in parts
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
            _verdict(res, last["verdict"], last.get("cases_info"))
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
    return done(outcome(tools, last.get("fin"), parts, offer_id))


def _verdict(res: dict[str, Any], verdict: dict[str, Any], cases_info: dict[str, Any] | None) -> None:
    """The engine check in the result: in the diagnostics, and for the manager when transactions still disagree."""
    res.setdefault("diagnostics", {})["verify"] = {**verdict, **({"cases": cases_info} if cases_info else {})}
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
