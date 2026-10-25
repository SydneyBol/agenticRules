"""Pulsar-compatible endpoints: the existing gravty-ui loads our XML with no UI change.

What the UI already does (newui develop):
- Offer Main, offer created from a prompt: fire-and-forget POST pulsar/offer/rules/?regenerate=true {prompt, sponsor_id, id, type}
- Rules screen open (planning, no rules yet): GET pulsar/{id}/prompt/offerrules/ -> {prompt}; if non-empty
  POST pulsar/offer/rules/?regenerate=false -> {xml: base64}; decodes with atob and auto-loads it via the upload path
  (offer-rules.component.ts getAiPrompt / getOfferRulesData / calfunctionAfterBlocklyLoaded)
- GenAI popup: POST pulsar/offer/rules?regenerate=true; while the answer is {result: "Generating"} it re-posts at once,
  then hands {xml} to the same upload path (generate-ai.component.ts getOfferRulesData, handlePopupResponse)
- Errors: the popup shows response.error.error.message
- The upload path creates the counters itself from the XML (type comes from each update_attribute mutation's `type`)

The Offer Main prompt is the prompt for the whole offer: ONE agent run (agent.py) builds Offer Main, the segment and
the rules; /offer/metadata answers as soon as the agent has set Offer Main, the prompt stages show the segment and
rules prompts it wrote, and /segment/rules and /offer/rules hand out its canvases. Each screen's answer carries only
its own part (core.session.part_view): the rules answer has the rules' assumptions, questions, unsupported items and
counters (no Offer Main settings), the segment answer the segment's. A prompt the manager edits on a screen gets its
own run for that canvas only, told what the rest of the offer already covers.

So this module stores the results per offer, serves them in that contract, and shapes the XML for the upload path:
ASCII only (the upload path deletes every char outside 0x20-0x7E; we use character references), and the
readback and the rules' own assumptions/questions travel as a Blockly comment on the Offer block, the only place the
stock UI shows text.
"""
import base64
import hashlib
import json
import logging
import re
import threading
import time

from flask import Blueprint, jsonify, request

from ..ai.agent import run as run_agent
from ..ai.prompt import rules_context
from ..core.counters import Guard, normalize, notes as counter_notes
from ..core.session import part_view

log = logging.getLogger("offer_author.pulsar")

HOLD_S = 20  # hold a regenerate=true request this long before answering "Generating" (the popup re-posts at once)
WAIT_CACHED_S = 45  # regenerate=false: the rules screen does not poll, so wait for a running job before giving up
RECENT_S = 120  # a finished job this recent answers the popup's re-post instead of starting a new generation


MEMBER_FREQ = {"day": "Per Day", "week": "Weekly", "month": "Per Month", "total": "Lifetime"}



def offer_main_metadata(om, today_iso):
    """The stock /pulsar/offer/metadata 'offer' response body (generate_offer_metadata.py): the UI fills Offer Main from it."""
    freq = om.get("frequency") if om.get("frequency") not in (None, "none") else ""
    return {"offer_name": om["offer_name"], "subtitle": om.get("subtitle", ""), "offer_description": om.get("offer_description", ""),
            "term_and_condition": "<ul>" + "".join(f"<li>{t}</li>" for t in om.get("terms") or []) + "</ul>",
            "num_of_clients": om.get("num_of_clients") or None, "max_usage_per_user": om.get("max_usage_per_user") or None,
            "offer_frequency_type": om.get("offer_frequency_type", ""),
            "option_value": freq, "option_visible_name": MEMBER_FREQ.get(freq, ""),
            "start_date": (om.get("start_date") or today_iso[:10]) + "T00:00:00",
            "end_date": (om["end_date"] + "T23:59:00") if om.get("end_date") else None}


def ascii_xml(xml):
    return re.sub(r"[^\x20-\x7E]", lambda m: f"&#{ord(m.group(0))};", xml)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def comment_text(res):
    """The note on the rules canvas: the rules part's own items (Offer Main and the segment answer on their screens)."""
    v = part_view(res, "rules")
    parts = ["AI draft: " + (res.get("readback") or "")]
    if v["assumptions"]:
        parts.append("Assumptions:\n" + "\n".join("- " + a for a in v["assumptions"]))
    if v["blocking_questions"]:
        parts.append("Please confirm:\n" + "\n".join("- " + q for q in v["blocking_questions"]))
    if v["unsupported"]:
        parts.append("Not built:\n" + "\n".join(f"- {u['phrase']}: {u['reason']}" for u in v["unsupported"]))
    if res.get("counter_notes"):
        parts.append("Counters:\n" + "\n".join("- " + n for n in res["counter_notes"]))
    return "\n\n".join(parts)


def ui_xml(res):
    """Pipeline result -> the XML string the stock upload path should receive."""
    xml = res["rules"]["xml"]
    note = f'<comment pinned="true" h="220" w="520">{esc(comment_text(res))}</comment>'
    xml = re.sub(r'(<block type="offer_generic"[^>]*>)', lambda m: m.group(1) + note, xml, count=1)
    return ascii_xml(xml).replace("\n", "&#10;")


def error_message(res, part):
    """Why that part was not built, in its own items."""
    if res["status"] == "error":
        return res.get("message") or "The rule generator failed. Please try again."
    v = part_view(res, part)
    lines = list(v["blocking_questions"])
    for u in v["unsupported"]:
        lines.append(f"Can't build '{u['phrase']}': {u['reason']}" + (f" Try: {u['alternative']}" if u.get("alternative") else ""))
    return " ".join(lines) or "I couldn't turn this into rules. Could you describe who earns what, and when?"


class Store:
    """Per-offer prompts and generation jobs (in memory; a real deployment would use Redis/Dynamo)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.prompts = {}  # (offer_id, stage) -> {prompt, offer_status}
        self.jobs = {}  # (offer_id | "offer" | "segment", prompt_hash) -> job
        self.latest = {}  # offer_id -> job key of the latest finished rules job
        self.offer_of = {}  # offer_id -> key of the run of its Offer Main prompt

    @staticmethod
    def key(offer_id, prompt):
        return (str(offer_id), hashlib.sha256(prompt.strip().encode()).hexdigest()[:16])

    def offer_for(self, offer_id):
        k = self.offer_of.get(str(offer_id))
        return self.jobs.get(k) if k else None

    def offer_with_segment(self, prompt):
        """The offer run whose segment prompt this is (the Select Members screen shows it)."""
        return next((j for j in list(self.jobs.values()) if (j.get("offer_main") or {}).get("segment_prompt", "").strip()
                     == prompt.strip() and prompt.strip()), None)

    def start(self, k, work, track=True, prompt=""):
        """work(job) -> result. A job also carries Offer Main as soon as the agent sets it (job['main'])."""
        with self.lock:
            job = self.jobs.get(k)
            if job and (job["state"] == "running" or time.time() - job.get("done_at", 0) < RECENT_S):
                return job
            job = {"state": "running", "event": threading.Event(), "main": threading.Event(), "offer_main": None,
                   "prompt": prompt.strip()}
            self.jobs[k] = job

        def run():
            try:
                job["result"] = work(job)
            except Exception as e:  # noqa: BLE001
                job["result"] = {"status": "error", "message": f"{type(e).__name__}: {e}"}
            job["state"], job["done_at"] = "done", time.time()
            d = job["result"].get("diagnostics") or {}
            log.info("pulsar job offer=%s status=%s latency_s=%s repairs=%s rules=%s", k[0], job["result"].get("status"),
                     d.get("latency_s"), d.get("repairs"), (job["result"].get("rules") or {}).get("rule_count"))
            if track:
                with self.lock:
                    self.latest[k[0]] = k
            job["main"].set()
            job["event"].set()

        threading.Thread(target=run, daemon=True).start()
        return job


def pulsar_blueprint(llm, catalog_for, store=None, counters_for=None):
    """counters_for(offer_id, authorization) -> {offer_status, counters} | None (counters.BolapiCounterSource.get)."""
    bp = Blueprint("pulsar", __name__)
    store = store or Store()
    bp.store = store

    def err(msg, status):
        return jsonify({"error": {"message": msg}}), status

    def answer(res):
        if res.get("rules"):
            xml, v = ui_xml(res), part_view(res, "rules")
            return jsonify({"result": "Generated", "xml": base64.b64encode(xml.encode("ascii")).decode(),
                            "status": v["status"], "readback": res["readback"], "assumptions": v["assumptions"],
                            "blocking_questions": v["blocking_questions"], "unsupported": v["unsupported"],
                            "counter_report": res["rules"].get("counter_report"), "counter_notes": res.get("counter_notes", [])})
        return err(error_message(res, "rules"), 503 if res["status"] == "error" else 422)

    def existing_counters(body, auth):
        """The offer's counters before generating: from the request (harness) or bolapi with the caller's JWT."""
        if isinstance(body.get("existing_counters"), dict):
            return body["existing_counters"]
        return counters_for(body.get("id"), auth) if counters_for and body.get("id") is not None else None

    def offer_job(prompt, sponsor_id, auth):
        """The whole offer from its Offer Main prompt; keyed by the prompt (the offer and its id do not exist yet)."""
        def work(job):
            def on_main(om):
                job["offer_main"] = om
                job["main"].set()
            return run_agent(prompt, catalog_for(sponsor_id, None, auth), llm, on_offer_main=on_main)
        return store.start(store.key("offer", prompt), work, track=False, prompt=prompt)

    def rules_only(body, prompt, auth, context, existing):
        return run_agent(prompt, catalog_for(body.get("sponsor_id"), None, auth), llm, offer_id=body.get("id"),
                         parts=("rules",), context=context, existing=existing)

    def rules_job(body, prompt, auth, reuse_offer):
        """The offer run when this is its prompt or its rules prompt; otherwise a rules-only run, told what the
        offer's Offer Main and segment already cover."""
        oj = store.offer_for(body.get("id")) if body.get("id") is not None else None
        if oj is None and store.key("offer", prompt) in store.jobs and body.get("id") is not None:
            # the Offer Main pre-warm can arrive before its prompt is stored: the run of that prompt is this offer's
            store.offer_of[str(body["id"])] = store.key("offer", prompt)
            oj = store.offer_for(body["id"])
        if oj and reuse_offer(oj) and prompt.strip() in (oj["prompt"], ((oj["offer_main"] or {}).get("rules_prompt") or "").strip()):
            return oj
        ctx = rules_context(oj["offer_main"], (oj.get("result") or {}).get("segment")) if oj and oj["offer_main"] else None
        # a different set of existing counters is a different answer (names), so it is part of the job key
        sig = json.dumps(body.get("existing_counters"), sort_keys=True, default=str) if body.get("existing_counters") else ""
        return store.start(store.key(body.get("id"), prompt + sig),
                           lambda job: rules_only(body, prompt, auth, ctx, existing_counters(body, auth)))

    def deliver(job, body, prompt, auth):
        """The offer run built its rules before the offer existed: check its counter names against the offer's
        counters now, and rebuild the rules only when one of them would be reused wrongly."""
        res = job["result"]
        if job is not store.offer_for(body.get("id")) or not res.get("rules"):
            return res
        existing = existing_counters(body, auth)
        if existing is None:
            return res
        guard, posted = Guard(existing), normalize(res["rules"]["counters"])
        if any(guard.taken(c["name"], c["data_type"], c["counter_type"], c["namespace"]) for c in posted):
            again = store.start(store.key(body.get("id"), prompt + "|counters"),
                                lambda j: rules_only(body, prompt, auth, rules_context(job["offer_main"] or {}, res.get("segment")), existing))
            again["event"].wait(WAIT_CACHED_S)
            return again["result"] if again["state"] == "done" else res
        report = guard.report([c["name"] for c in posted])
        return dict(res, rules=dict(res["rules"], counter_report=report), counter_notes=counter_notes(report))

    @bp.post("/ai/pulsar/offer/rules", strict_slashes=False)
    def rules():
        body = request.get_json(silent=True) or {}
        regenerate = request.args.get("regenerate", "true") != "false"
        if body.get("type") != "reward":
            return err("English-to-rules is available for reward offers only.", 400)
        offer_id, prompt = body.get("id"), (body.get("prompt") or "").strip()
        auth = request.headers.get("Authorization")  # forwarded to bolapi for the tenant catalog
        if not regenerate:
            k = store.latest.get(str(offer_id))
            job = store.jobs.get(k) if k else None
            if not job and prompt:
                job = rules_job(body, prompt, auth, lambda oj: True)
            if not job:
                return jsonify({"xml": ""})
            job["event"].wait(WAIT_CACHED_S)
            if job["state"] != "done" or not job["result"].get("rules"):
                return jsonify({"xml": ""})  # the screen then loads normally; the popup can regenerate
            return answer(deliver(job, body, prompt, auth))
        if not prompt:
            return err("Please describe the offer rules.", 400)
        if len(prompt) > 25000:
            return err("Please keep the description under 25000 characters.", 400)
        if offer_id is not None:
            store.prompts[(str(offer_id), "offerrules")] = {"prompt": prompt, "offer_status": "planning"}
        # the Offer Main pre-warm (or a re-post of it) joins the offer run; a later regenerate builds afresh
        job = rules_job(body, prompt, auth, lambda oj: oj["state"] == "running" or time.time() - oj.get("done_at", 0) < RECENT_S)
        if not job["event"].wait(HOLD_S):
            return jsonify({"result": "Generating"})
        return answer(deliver(job, body, prompt, auth))

    # ------------------------------------------------------------ Offer Main: metadata (category offer | segment)
    @bp.post("/ai/pulsar/offer/metadata", strict_slashes=False)
    def metadata():
        body = request.get_json(silent=True) or {}
        prompt, category = (body.get("prompt") or "").strip(), body.get("category", "offer")
        if set(body) - {"prompt", "category"}:
            return jsonify(success=False, error={"code": "INVALID_REQUEST_PARAMS", "message": "Unexpected parameters"}), 400
        if category not in ("offer", "segment"):
            return err("Unknown category.", 400)
        if len(prompt.split()) < (2 if category == "segment" else 4):  # a segment prompt can be "Gold members"
            return err("Please describe the offer in a few more words.", 400)
        auth = request.headers.get("Authorization")
        if category == "segment":
            # add-segment screen: the prompt is the segment stage prompt; the offer run that wrote it, else its own run
            job = store.offer_with_segment(prompt) or segment_job(prompt, None, auth)
            job["event"].wait(WAIT_CACHED_S)
            seg = (job.get("result") or {}).get("segment") or {}
            return jsonify(success=True, metadata={"member_segment_name": seg.get("name", ""),
                                                   "segment_description": seg.get("description", "")})
        job = offer_job(prompt, None, auth)
        job["main"].wait(WAIT_CACHED_S)  # answered as soon as the agent has set Offer Main; segment and rules go on
        om = job["offer_main"]
        if not om:
            res = job.get("result") or {}
            if res and res.get("status") != "error":
                return err(error_message(res, "offer_main"), 422)
            return err(res.get("message") or "Offer details are still being generated. Please try again.", 503)
        catalog = catalog_for(None, None, auth)
        return jsonify(success=True, metadata=offer_main_metadata(om, catalog.today or time.strftime("%Y-%m-%d")),
                       offer_main=om)

    # ------------------------------------------------------------ Select Members: segment rules
    def segment_job(prompt, sponsor_id, auth):
        return store.start(store.key("segment", prompt), lambda job: run_agent(
            prompt, catalog_for(sponsor_id, None, auth), llm, parts=("segment",)), track=False)

    @bp.post("/ai/pulsar/segment/rules", strict_slashes=False)
    def segment_rules():
        body = request.get_json(silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if len(prompt.split()) < 2:  # "Gold members" is a whole segment prompt
            return err("Please describe the members this offer is for.", 400)
        job = store.offer_with_segment(prompt) or segment_job(prompt, body.get("sponsor_id"), request.headers.get("Authorization"))
        if not job["event"].wait(HOLD_S):
            return jsonify({"result": "Generating"})
        r = job["result"]
        seg, v = r.get("segment"), part_view(r, "segment")
        if seg:
            raw = ascii_xml(seg["xml"])
            return jsonify({"xml": base64.b64encode(raw.encode("ascii")).decode(), "raw": raw, "rule_id": body.get("rule_id"),
                            "name": seg["name"], "description": seg["description"], "status": v["status"],
                            "assumptions": v["assumptions"], "blocking_questions": v["blocking_questions"],
                            "unsupported": v["unsupported"]})
        msg = " ".join(v["blocking_questions"]) or " ".join(f"{u['phrase']}: {u['reason']}" for u in v["unsupported"])
        return jsonify({"error": {"code": "SEGMENT_NOT_BUILT", "message": msg or r.get("message") or "No member conditions found."}}), \
            503 if r.get("status") == "error" else 422

    @bp.get("/ai/pulsar/<offer_id>/prompt/<stage>", strict_slashes=False)
    def get_prompt(offer_id, stage):
        p = store.prompts.get((str(offer_id), stage))  # saved on that screen (the manager's own edit) wins
        if p is None and stage in ("offerrules", "offersegmentation"):
            oj = store.offer_for(offer_id)  # the offer run writes each screen's prompt with Offer Main
            if oj:
                oj["main"].wait(WAIT_CACHED_S)
                if oj["offer_main"]:
                    part = "rules_prompt" if stage == "offerrules" else "segment_prompt"
                    return jsonify({"prompt": oj["offer_main"].get(part) or "", "offer_status": None})
        if p is None and stage != "offersegmentation":
            p = store.prompts.get((str(offer_id), "offermain"))  # stock fallback (get_offer_prompts.py)
        return jsonify({"prompt": p["prompt"] if p else "", "offer_status": p["offer_status"] if p else None})

    @bp.post("/ai/pulsar/<offer_id>/prompt/<stage>", strict_slashes=False)
    def set_prompt(offer_id, stage):
        body = request.get_json(silent=True) or {}
        prompt = body.get("prompt") or ""
        store.prompts[(str(offer_id), stage)] = {"prompt": prompt, "offer_status": body.get("offer_status")}
        if stage == "offermain" and body.get("offer_status") != "update" and prompt.strip():
            # the Offer Main prompt is the prompt for the whole offer: one run builds it; the offerrules /
            # offersegmentation stages then read their part (store_offer_prompts.py fans the prompt out the same way)
            offer_job(prompt.strip(), None, request.headers.get("Authorization"))
            store.offer_of[str(offer_id)] = store.key("offer", prompt.strip())
            for st in ("offerrules", "offersegmentation"):
                store.prompts.pop((str(offer_id), st), None)
        return jsonify({"prompt": prompt})

    return bp
