"""The Pulsar endpoints the stock gravty-ui already calls (newui develop), so it loads our XML with no UI change.

- Offer Main, offer created from a prompt: fire-and-forget POST pulsar/offer/rules/?regenerate=true
  {prompt, sponsor_id, id, type}.
- Rules screen open (planning, no rules yet): GET pulsar/{id}/prompt/offerrules/ -> {prompt}; if non-empty
  POST pulsar/offer/rules/?regenerate=false -> {xml: base64}, decoded with atob and loaded through the upload path
  (offer-rules.component.ts getAiPrompt / getOfferRulesData / calfunctionAfterBlocklyLoaded).
- GenAI popup: POST pulsar/offer/rules?regenerate=true; while the answer is {result: "Generating"} it re-posts at
  once, then hands {xml} to the same upload path (generate-ai.component.ts getOfferRulesData, handlePopupResponse).
- Errors: the popup shows response.error.error.message.
- The upload path creates the counters itself from the XML (each update_attribute mutation's `type`).

/offer/metadata answers as soon as the offer run has set Offer Main, the prompt stages show the segment and rules
prompts it wrote, and /segment/rules and /offer/rules hand out its canvases (runs.py); each answer carries only its own
part (payloads.py).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from flask import Blueprint, Response, jsonify, request

from ...core.settings import Settings
from . import jobs
from .jobs import JobStore
from .payloads import error_message, notes, offer_main_metadata, xml_answer
from .runs import CatalogFor, CountersFor, Runs

log = logging.getLogger("offer_author.pulsar")

MAX_PROMPT = 25000

Reply = Response | tuple[Response, int]


def _err(msg: str, status: int) -> tuple[Response, int]:
    """An error the screens show (response.error.message)."""
    return jsonify({"error": {"message": msg}}), status


def _traced(offer_id: Any, part: str, res: dict[str, Any]) -> None:
    """Logs what a run says about the part it answers (the answer itself is Pulsar's: the XML only)."""
    log.info("pulsar answer offer=%s part=%s %s", offer_id, part, json.dumps(notes(res, part), default=str))


def _rules_reply(res: dict[str, Any], body: dict[str, Any]) -> Reply:
    """A rules run's answer: the XML with its rule id (the offer's id, as Pulsar gives it), or why there is none."""
    _traced(body.get("id"), "rules", res)
    if res.get("rules"):
        return jsonify(xml_answer(res["rules"]["xml"], str(body.get("id", f"offer_{uuid.uuid4()}")).strip()))
    return _err(error_message(res, "rules"), 503 if res["status"] == "error" else 422)


class _Routes:
    """The endpoints' views, over one service's runs and store."""

    def __init__(self, runs: Runs, catalog_for: CatalogFor) -> None:
        self.runs, self.store, self.catalog_for = runs, runs.store, catalog_for

    def rules(self) -> Reply:
        """The rules screen (regenerate=false) and the GenAI popup (regenerate=true)."""
        body = request.get_json(silent=True) or {}
        if body.get("type") != "reward":
            return _err("English-to-rules is available for reward offers only.", 400)
        prompt = (body.get("prompt") or "").strip()
        auth = request.headers.get("Authorization")  # forwarded to bolapi for the tenant catalog
        if request.args.get("regenerate", "true") == "false":
            return self._saved_rules(body, prompt, auth)
        return self._regenerate(body, prompt, auth)

    def _saved_rules(self, body: dict[str, Any], prompt: str, auth: str | None) -> Reply:
        """The rules screen opening: the offer's last rules run (or one for the saved prompt), else no XML."""
        k = self.store.latest.get(str(body.get("id")))
        job = self.store.jobs.get(k) if k else None
        if not job and prompt:
            job = self.runs.rules_job(body, prompt, auth, lambda oj: True)
        if not job:
            return jsonify({"xml": ""})
        job.wait(jobs.WAIT_CACHED_S)
        res = self.runs.deliver(job, body, prompt, auth) if job.state == "done" else None
        if not (res and res.get("rules")):
            return jsonify({"xml": ""})  # the screen then loads normally; the popup can regenerate
        return _rules_reply(res, body)

    def _regenerate(self, body: dict[str, Any], prompt: str, auth: str | None) -> Reply:
        """The GenAI popup: rules for the prompt, or "Generating" while they are built."""
        if not prompt:
            return _err("Please describe the offer rules.", 400)
        if len(prompt) > MAX_PROMPT:
            return _err(f"Please keep the description under {MAX_PROMPT} characters.", 400)
        if body.get("id") is not None:
            self.store.prompts[(str(body["id"]), "offerrules")] = {"prompt": prompt, "offer_status": "planning"}
        # the Offer Main pre-warm (or a re-post of it) joins the offer run; a later regenerate builds afresh
        job = self.runs.rules_job(body, prompt, auth, lambda oj: oj.recent())
        res = self.runs.deliver(job, body, prompt, auth) if job.wait(jobs.HOLD_S) else None
        return jsonify({"result": "Generating"}) if res is None else _rules_reply(res, body)

    def metadata(self) -> Reply:
        """Offer Main (category offer) or the add-segment screen (category segment)."""
        body = request.get_json(silent=True) or {}
        prompt, category = (body.get("prompt") or "").strip(), body.get("category", "offer")
        if set(body) - {"prompt", "category"}:
            bad = {"code": "INVALID_REQUEST_PARAMS", "message": "Unexpected parameters"}
            return jsonify(success=False, error=bad), 400
        if category not in ("offer", "segment"):
            return _err("Unknown category.", 400)
        if len(prompt.split()) < (2 if category == "segment" else 4):  # a segment prompt can be "Gold members"
            return _err("Please describe the offer in a few more words.", 400)
        auth = request.headers.get("Authorization")
        return self._segment_names(prompt, auth) if category == "segment" else self._offer_main(prompt, auth)

    def _segment_names(self, prompt: str, auth: str | None) -> Reply:
        """The add-segment screen: the segment's name and description."""
        # the prompt is the segment stage prompt: the offer run that wrote it, else its own run
        job = self.runs.segment_for(prompt, None, auth)
        job.wait(jobs.WAIT_CACHED_S)
        seg = job.result.get("segment") or {}
        names = {"member_segment_name": seg.get("name", ""), "segment_description": seg.get("description", "")}
        return jsonify(success=True, metadata=names)

    def _offer_main(self, prompt: str, auth: str | None) -> Reply:
        """Offer Main: answered as soon as the agent has set it; segment and rules go on."""
        job = self.runs.offer_job(prompt, None, auth)
        job.main.wait(jobs.WAIT_CACHED_S)
        om = job.offer_main
        if not om:
            res = job.result
            if res and res.get("status") != "error":
                return _err(error_message(res, "offer_main"), 422)
            return _err(res.get("message") or "Offer details are still being generated. Please try again.", 503)
        catalog = self.catalog_for(None, None, auth)
        today = catalog.today or time.strftime("%Y-%m-%d")
        return jsonify(success=True, metadata=offer_main_metadata(om, today), offer_main=om)

    def segment_rules(self) -> Reply:
        """Select Members: the segment's rules."""
        body = request.get_json(silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if len(prompt.split()) < 2:  # "Gold members" is a whole segment prompt
            return _err("Please describe the members this offer is for.", 400)
        job = self.runs.segment_for(prompt, body.get("sponsor_id"), request.headers.get("Authorization"))
        if not job.wait(jobs.HOLD_S):
            return jsonify({"result": "Generating"})
        r = job.result
        _traced(body.get("id"), "segment", r)
        if r.get("segment"):
            return jsonify(xml_answer(r["segment"]["xml"], body.get("rule_id")))
        status = 503 if r.get("status") == "error" else 422
        return jsonify({"error": {"code": "SEGMENT_NOT_BUILT", "message": error_message(r, "segment")}}), status

    def get_prompt(self, offer_id: str, stage: str) -> Reply:
        """A screen's prompt: saved on that screen, else written by the offer run, else the Offer Main prompt."""
        self.runs.prewarm(None, request.headers.get("Authorization"))  # an offer screen is open: the popup may follow
        p = self.store.prompts.get((str(offer_id), stage))  # saved on that screen (the manager's own edit) wins
        if p is None and stage in ("offerrules", "offersegmentation") and (written := self._written(offer_id, stage)):
            return written
        if p is None and stage != "offersegmentation":
            p = self.store.prompts.get((str(offer_id), "offermain"))  # stock fallback (get_offer_prompts.py)
        return jsonify({"prompt": p["prompt"] if p else "", "offer_status": p["offer_status"] if p else None})

    def _written(self, offer_id: str, stage: str) -> Reply | None:
        """The screen's prompt the offer run wrote with Offer Main, or None when there is no run or it set none."""
        oj = self.store.offer_for(offer_id)
        if not oj:
            return None
        oj.main.wait(jobs.WAIT_CACHED_S)
        if not oj.offer_main:
            return None
        part = "rules_prompt" if stage == "offerrules" else "segment_prompt"
        return jsonify({"prompt": oj.offer_main.get(part) or "", "offer_status": None})

    def set_prompt(self, offer_id: str, stage: str) -> Reply:
        """Saves a screen's prompt; the Offer Main prompt starts the offer run."""
        body = request.get_json(silent=True) or {}
        prompt = body.get("prompt") or ""
        self.store.prompts[(str(offer_id), stage)] = {"prompt": prompt, "offer_status": body.get("offer_status")}
        if stage == "offermain" and body.get("offer_status") != "update" and prompt.strip():
            # one run builds the whole offer; the offerrules / offersegmentation stages then read their part
            # (store_offer_prompts.py fans the prompt out the same way)
            self.runs.offer_job(prompt.strip(), None, request.headers.get("Authorization"))
            self.store.offer_of[str(offer_id)] = self.store.key("offer", prompt.strip())
            for st in ("offerrules", "offersegmentation"):
                self.store.prompts.pop((str(offer_id), st), None)
        return jsonify({"prompt": prompt})


def pulsar_blueprint(
    llm: Any,
    catalog_for: CatalogFor,
    store: JobStore | None = None,
    counters_for: CountersFor | None = None,
    settings: Settings | None = None,
) -> Blueprint:
    """The Pulsar endpoints.

    Args:
        llm: The chat model.
        catalog_for: (sponsor_id, account_id, authorization) -> the tenant's catalog.
        store: The jobs and saved prompts (default: a new one).
        counters_for: (offer_id, authorization) -> {offer_status, counters} | None
            (existing_counters.BolapiCounterSource.get).
        settings: The service's settings (core.settings.Settings), handed to every agent run.

    Returns:
        The blueprint; its `store` and `prewarm` are exposed for the service and tests.
    """
    bp = Blueprint("pulsar", __name__)
    runs = Runs(llm, catalog_for, store, counters_for, settings)
    bp.store, bp.prewarm = runs.store, runs.prewarm  # type: ignore[attr-defined]
    views = _Routes(runs, catalog_for)
    for rule, view, method in (
        ("/ai/pulsar/offer/rules", views.rules, "POST"),
        ("/ai/pulsar/offer/metadata", views.metadata, "POST"),
        ("/ai/pulsar/segment/rules", views.segment_rules, "POST"),
        ("/ai/pulsar/<offer_id>/prompt/<stage>", views.get_prompt, "GET"),
        ("/ai/pulsar/<offer_id>/prompt/<stage>", views.set_prompt, "POST"),
    ):
        bp.add_url_rule(rule, view.__name__, view, methods=[method], strict_slashes=False)
    return bp
