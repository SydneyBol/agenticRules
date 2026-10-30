"""Which agent run answers a screen's prompt.

The Offer Main prompt is the prompt for the whole offer: ONE agent run builds Offer Main, the segment and the rules,
and each screen reads its own part of it. A prompt the manager edits on a screen gets its own run for that canvas
only, told what the rest of the offer already covers. The offer run builds its rules before the offer exists, so its
counters are checked against the offer's when the rules screen asks for them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from ...ai.agent import run as run_agent
from ...ai.agent import warm as warm_cache
from ...ai.prompt import rules_context
from ...core.settings import Settings
from ...core.tenant.existing_counters import Guard, decode_rules_xml, normalize
from ...core.tenant.harvest import PromptLog
from .. import text
from . import jobs
from .jobs import Job, JobStore

log = logging.getLogger("offer_author.pulsar")

CatalogFor = Callable[[Any, Any, Any], Any]
CountersFor = Callable[[Any, Any], dict[str, Any] | None]


class Runs:
    """Starts and finds the agent runs behind the Pulsar screens.

    Attributes:
        store: The jobs and saved prompts.
    """

    def __init__(
        self,
        llm: Any,
        catalog_for: CatalogFor,
        store: JobStore | None = None,
        counters_for: CountersFor | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Runs the agent with this service's model and settings.

        Args:
            llm: The chat model.
            catalog_for: (sponsor_id, account_id, authorization) -> the tenant's catalog.
            store: The jobs and saved prompts (default: a new, empty one).
            counters_for: (offer_id, authorization) -> the offer's status and counters (counters.BolapiCounterSource),
                or None when they cannot be read.
            settings: The service's settings.
        """
        self.llm, self.catalog_for, self.counters_for = llm, catalog_for, counters_for
        self.settings = settings or Settings()
        self.store = store or JobStore()
        self.prompts = PromptLog(self.settings.history)  # no history folder: nothing kept
        self._warmed: dict[tuple[Any, Any], float] = {}  # (tenant, sponsor) -> when its prompt cache was last written
        self._warm_lock = threading.Lock()
        self._warm_life = 3300 if self.settings.cache_ttl == "1h" else 240  # re-warm a little before it expires

    def agent(self, prompt: str, catalog: Any, **kw: Any) -> dict[str, Any]:
        """One agent run, its result worded for the manager."""
        return text.public(run_agent(prompt, catalog, self.llm, settings=self.settings, **kw))

    def prewarm(self, sponsor_id: Any, auth: str | None) -> None:
        """Writes the tenant's prompt prefix to the prompt cache while the manager is on an offer screen.

        In the background and at most once per cache lifetime: the popup's first run then starts warm instead of
        paying the cold write.
        """

        def work() -> None:
            try:
                catalog = self.catalog_for(sponsor_id, None, auth)
                key = (catalog.tenant, catalog.sponsor_id)
                with self._warm_lock:
                    if time.time() - self._warmed.get(key, 0) < self._warm_life:
                        return
                    self._warmed[key] = time.time()
                log.info("prompt cache warmed for %s: %s", key, warm_cache(self.llm, catalog, settings=self.settings))
            except Exception:  # a cold first run is slower, not wrong
                log.exception("prompt cache warm-up failed")

        threading.Thread(target=work, daemon=True).start()

    def existing_counters(self, body: dict[str, Any], auth: str | None) -> dict[str, Any] | None:
        """The offer's counters (and saved rules canvas) before generating.

        From the request (the harness sends existing_counters, current_rules_xml) or from bolapi with the
        caller's JWT.
        """
        out: dict[str, Any] | None
        if isinstance(body.get("existing_counters"), dict):
            out = dict(body["existing_counters"])
        else:
            out = self.counters_for(body.get("id"), auth) if self.counters_for and body.get("id") is not None else None
        if saved := decode_rules_xml(body.get("current_rules_xml")):
            out = dict(out or {}, rules_xml=saved)
        return out

    def offer_job(self, prompt: str, sponsor_id: Any, auth: str | None) -> Job:
        """The whole offer from its Offer Main prompt; keyed by the prompt (the offer and its id do not exist yet)."""

        def work(job: Job) -> dict[str, Any]:
            def on_main(om: dict[str, Any]) -> None:
                job.offer_main = om
                job.main.set()

            return self.agent(prompt, self.catalog_for(sponsor_id, None, auth), on_offer_main=on_main)

        return self.store.start(self.store.key("offer", prompt), work, track=False, prompt=prompt)

    def segment_job(self, prompt: str, sponsor_id: Any, auth: str | None) -> Job:
        """A segment-only run for a segment prompt no offer run wrote."""
        return self.store.start(
            self.store.key("segment", prompt),
            lambda job: self.agent(prompt, self.catalog_for(sponsor_id, None, auth), parts=("segment",)),
            track=False,
        )

    def segment_for(self, prompt: str, sponsor_id: Any, auth: str | None) -> Job:
        """The offer run that wrote this segment prompt, else a run of its own."""
        return self.store.offer_with_segment(prompt) or self.segment_job(prompt, sponsor_id, auth)

    def rules_only(
        self,
        body: dict[str, Any],
        prompt: str,
        auth: str | None,
        context: str | None,
        existing: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """A rules-only run for the rules screen's prompt."""
        catalog = self.catalog_for(body.get("sponsor_id"), None, auth)
        self.prompts.add(catalog.tenant, body.get("id"), prompt)  # paired with the offer's saved rules by core.harvest
        return self.agent(
            prompt, catalog, offer_id=body.get("id"), parts=("rules",), context=context, existing=existing
        )

    def rules_job(self, body: dict[str, Any], prompt: str, auth: str | None, reuse_offer: Callable[[Job], bool]) -> Job:
        """The job whose rules answer this prompt.

        The offer run when this is its prompt or its rules prompt (and reuse_offer allows it); otherwise a rules-only
        run, told what the offer's Offer Main and segment already cover.
        """
        store, offer_id = self.store, body.get("id")
        oj = store.offer_for(offer_id) if offer_id is not None else None
        if oj is None and store.key("offer", prompt) in store.jobs and offer_id is not None:
            # the Offer Main pre-warm can arrive before its prompt is stored: the run of that prompt is this offer's
            store.offer_of[str(offer_id)] = store.key("offer", prompt)
            oj = store.offer_for(offer_id)
        if (
            oj
            and reuse_offer(oj)
            and prompt.strip() in (oj.prompt, ((oj.offer_main or {}).get("rules_prompt") or "").strip())
        ):
            return oj
        ctx = rules_context(oj.offer_main, oj.result.get("segment")) if oj and oj.offer_main else None
        # a different set of existing counters (names) or saved canvas is a different answer: part of the job key
        sig = (
            json.dumps(body["existing_counters"], sort_keys=True, default=str) if body.get("existing_counters") else ""
        )
        if body.get("current_rules_xml"):
            sig += "|" + hashlib.sha1(str(body["current_rules_xml"]).encode()).hexdigest()
        return store.start(
            store.key(offer_id, prompt + sig),
            lambda job: self.rules_only(body, prompt, auth, ctx, self.existing_counters(body, auth)),
        )

    def deliver(self, job: Job, body: dict[str, Any], prompt: str, auth: str | None) -> dict[str, Any] | None:
        """A finished job's result for the rules screen.

        The offer run built its rules before the offer existed: its counter names are checked against the offer's
        counters now, and the rules are rebuilt only when one of them would be reused wrongly.

        Returns:
            The result, or None while that rebuild still runs (its rules are not ready, and the first ones must not be
            uploaded).
        """
        res = job.result
        if job is not self.store.offer_for(body.get("id")) or not res.get("rules"):
            return res
        existing = self.existing_counters(body, auth)
        if existing is None:
            return res
        guard, posted = Guard(existing), normalize(res["rules"]["counters"])
        if any(guard.taken(c["name"], c["data_type"], c["counter_type"], c["namespace"], c["level"]) for c in posted):
            context = rules_context(job.offer_main or {}, res.get("segment"))
            again = self.store.start(
                self.store.key(body.get("id"), prompt + "|counters"),
                lambda j: self.rules_only(body, prompt, auth, context, existing),
            )
            again.wait(jobs.WAIT_CACHED_S)
            return again.result if again.state == "done" else None
        return dict(res, rules=dict(res["rules"], counter_report=guard.report(posted)))
