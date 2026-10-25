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

So this module stores the generated XML per offer, serves it in that contract, and shapes it for the upload path:
ASCII only (the upload path deletes every char outside 0x20-0x7E; we use character references), and the
readback/assumptions/questions travel as a Blockly comment on the Offer block, the only place the stock UI shows text.
"""
import base64
import hashlib
import json
import logging
import re
import threading
import time

from flask import Blueprint, jsonify, request

from .pipeline import generate
from .planner import offer_main_metadata, plan_offer, rules_context, segment_metadata
from .segment import generate_segment

log = logging.getLogger("offer_author.pulsar")

HOLD_S = 20  # hold a regenerate=true request this long before answering "Generating" (the popup re-posts at once)
WAIT_CACHED_S = 45  # regenerate=false: the rules screen does not poll, so wait for a running job before giving up
RECENT_S = 120  # a finished job this recent answers the popup's re-post instead of starting a new generation


def ascii_xml(xml):
    return re.sub(r"[^\x20-\x7E]", lambda m: f"&#{ord(m.group(0))};", xml)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def comment_text(res):
    parts = ["AI draft: " + (res.get("readback") or "")]
    if res.get("assumptions"):
        parts.append("Assumptions:\n" + "\n".join("- " + a for a in res["assumptions"]))
    if res.get("blocking_questions"):
        parts.append("Please confirm:\n" + "\n".join("- " + q for q in res["blocking_questions"]))
    if res.get("unsupported"):
        parts.append("Not built:\n" + "\n".join(f"- {u['phrase']}: {u['reason']}" for u in res["unsupported"]))
    if res.get("counter_notes"):
        parts.append("Counters:\n" + "\n".join("- " + n for n in res["counter_notes"]))
    s = res.get("offer_settings") or {}
    settings = ", ".join(f"{k}={v}" for k, v in s.items() if v)
    if settings:
        parts.append("Suggested offer settings (set on Offer Main): " + settings)
    return "\n\n".join(parts)


def ui_xml(res):
    """Pipeline result -> the XML string the stock upload path should receive."""
    xml = res["rules"]["xml"]
    note = f'<comment pinned="true" h="220" w="520">{esc(comment_text(res))}</comment>'
    xml = re.sub(r'(<block type="offer_generic"[^>]*>)', lambda m: m.group(1) + note, xml, count=1)
    return ascii_xml(xml).replace("\n", "&#10;")


def error_message(res):
    if res["status"] == "error":
        return res.get("message") or "The rule generator failed. Please try again."
    lines = []
    if res.get("blocking_questions"):
        lines += res["blocking_questions"]
    for u in res.get("unsupported") or []:
        lines.append(f"Can't build '{u['phrase']}': {u['reason']}" + (f" Try: {u['alternative']}" if u.get("alternative") else ""))
    return " ".join(lines) or "I couldn't turn this into rules. Could you describe who earns what, and when?"


class Store:
    """Per-offer prompts and generation jobs (in memory; a real deployment would use Redis/Dynamo)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.prompts = {}  # (offer_id, stage) -> {prompt, offer_status}
        self.jobs = {}  # (offer_id | kind, prompt_hash) -> {state, result, done_at, event}
        self.latest = {}  # offer_id -> job key of the latest finished rules job
        self.plan_of = {}  # offer_id -> plan job key (the plan of its Offer Main prompt)

    @staticmethod
    def key(offer_id, prompt):
        return (str(offer_id), hashlib.sha256(prompt.strip().encode()).hexdigest()[:16])

    def plan_job(self, prompt, work):
        """Plans are keyed by the prompt only: Offer Main asks for metadata before the offer (and its id) exists."""
        return self.start(self.key("plan", prompt), work, track=False)

    def plan_for(self, offer_id, wait_s=0):
        k = self.plan_of.get(str(offer_id))
        job = self.jobs.get(k) if k else None
        if job:
            job["event"].wait(wait_s)
        return job["result"] if job and job["state"] == "done" and job["result"].get("rules") else None

    def start(self, k, work, track=True):
        with self.lock:
            job = self.jobs.get(k)
            if job and (job["state"] == "running" or time.time() - job.get("done_at", 0) < RECENT_S):
                return job
            job = {"state": "running", "event": threading.Event()}
            self.jobs[k] = job

        def run():
            try:
                job["result"] = work()
            except Exception as e:  # noqa: BLE001
                job["result"] = {"status": "error", "message": f"{type(e).__name__}: {e}"}
            job["state"], job["done_at"] = "done", time.time()
            d = job["result"].get("diagnostics") or {}
            log.info("pulsar job offer=%s status=%s latency_s=%s repairs=%s rules=%s", k[0], job["result"].get("status"),
                     d.get("latency_s"), d.get("repairs"), (job["result"].get("rules") or {}).get("rule_count"))
            if track:
                with self.lock:
                    self.latest[k[0]] = k
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
            xml = ui_xml(res)
            return jsonify({"result": "Generated", "xml": base64.b64encode(xml.encode("ascii")).decode(),
                            "status": res["status"], "readback": res["readback"], "assumptions": res["assumptions"],
                            "blocking_questions": res["blocking_questions"], "unsupported": res["unsupported"],
                            "offer_settings": res["offer_settings"], "counter_report": res["rules"].get("counter_report"),
                            "counter_notes": res.get("counter_notes", [])})
        return err(error_message(res), 503 if res["status"] == "error" else 422)

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
                job = store.start(store.key(offer_id, prompt), lambda: run(body, prompt, auth))
            if not job:
                return jsonify({"xml": ""})
            job["event"].wait(WAIT_CACHED_S)
            if job["state"] != "done" or not job["result"].get("rules"):
                return jsonify({"xml": ""})  # the screen then loads normally; the popup can regenerate
            return answer(job["result"])
        if not prompt:
            return err("Please describe the offer rules.", 400)
        if len(prompt) > 25000:
            return err("Please keep the description under 25000 characters.", 400)
        if offer_id is not None:
            store.prompts[(str(offer_id), "offerrules")] = {"prompt": prompt, "offer_status": "planning"}
        # a different set of existing counters is a different answer (names), so it is part of the job key
        sig = json.dumps(body.get("existing_counters"), sort_keys=True, default=str) if body.get("existing_counters") else ""
        job = store.start(store.key(offer_id, prompt + sig), lambda: run(body, prompt, auth))
        if not job["event"].wait(HOLD_S):
            return jsonify({"result": "Generating"})
        return answer(job["result"])

    def existing_counters(body, auth):
        """The offer's counters before generating: from the request (harness) or bolapi with the caller's JWT."""
        if isinstance(body.get("existing_counters"), dict):
            return body["existing_counters"]
        return counters_for(body.get("id"), auth) if counters_for and body.get("id") is not None else None

    def run(body, prompt, auth):
        catalog = catalog_for(body.get("sponsor_id"), None, auth)
        existing = existing_counters(body, auth)
        plan = store.plan_for(body.get("id"), wait_s=WAIT_CACHED_S) if body.get("id") is not None else None
        if plan and prompt.strip() in (plan["rules"]["prompt"].strip(), plan["master_prompt"].strip()):
            # the Offer Main prompt (pre-warm) or its rules part: build the rules part, told what metadata/segment cover
            res = generate(plan["rules"]["prompt"], catalog, llm, offer_id=body.get("id"), context=rules_context(plan),
                           existing=existing)
            if plan["segment"]["prompt"].strip():
                res["assumptions"] = res.get("assumptions", []) + [
                    f"Who qualifies is the member segment '{plan['segment']['name']}' ({plan['segment']['prompt']}): "
                    "create and select it on Select Members, otherwise every member qualifies."]
            return res
        return generate(prompt, catalog, llm, offer_id=body.get("id"), existing=existing)

    def make_plan(prompt, sponsor_id, auth):
        def work():
            p = plan_offer(prompt, catalog_for(sponsor_id, None, auth), llm)
            p["master_prompt"] = prompt
            return p
        return store.plan_job(prompt, work)

    def pending(job, hold=HOLD_S):
        return not job["event"].wait(hold)

    # ------------------------------------------------------------ Offer Main: metadata (category offer | segment)
    @bp.post("/ai/pulsar/offer/metadata", strict_slashes=False)
    def metadata():
        body = request.get_json(silent=True) or {}
        prompt, category = (body.get("prompt") or "").strip(), body.get("category", "offer")
        if set(body) - {"prompt", "category"}:
            return jsonify(success=False, error={"code": "INVALID_REQUEST_PARAMS", "message": "Unexpected parameters"}), 400
        if category not in ("offer", "segment"):
            return err("Unknown category.", 400)
        if len(prompt.split()) < 4:
            return err("Please describe the offer in a few more words.", 400)
        auth = request.headers.get("Authorization")
        if category == "segment":
            # add-segment screen: the prompt is the segment stage prompt; reuse the plan that wrote it, else build it
            plan = next((j["result"] for j in list(store.jobs.values()) if j["state"] == "done" and j.get("result", {}).get("segment")
                         and j["result"]["segment"]["prompt"].strip() == prompt), None)
            if plan:
                return jsonify(success=True, metadata=segment_metadata(plan))
            seg = segment_job(prompt, None, auth)
            seg["event"].wait(WAIT_CACHED_S)
            r = seg.get("result") or {}
            return jsonify(success=True, metadata={"member_segment_name": r.get("name", ""), "segment_description": r.get("description", "")})
        job = make_plan(prompt, None, auth)
        job["event"].wait(WAIT_CACHED_S)
        plan = job.get("result")
        if not plan or plan.get("status") == "error":
            return err((plan or {}).get("message") or "Offer details are still being generated. Please try again.", 503)
        catalog = catalog_for(None, None, auth)
        return jsonify(success=True, metadata=offer_main_metadata(plan, catalog.today or time.strftime("%Y-%m-%d")),
                       plan={k: plan[k] for k in ("metadata", "segment", "rules", "clauses", "assumptions", "blocking_questions")})

    # ------------------------------------------------------------ Select Members: segment rules
    def segment_job(prompt, sponsor_id, auth):
        return store.start(store.key("segment", prompt), lambda: generate_segment(prompt, catalog_for(sponsor_id, None, auth), llm), track=False)

    @bp.post("/ai/pulsar/segment/rules", strict_slashes=False)
    def segment_rules():
        body = request.get_json(silent=True) or {}
        prompt = (body.get("prompt") or "").strip()
        if len(prompt.split()) < 2:  # "Gold members" is a whole segment prompt (the planner's terse form)
            return err("Please describe the members this offer is for.", 400)
        job = segment_job(prompt, body.get("sponsor_id"), request.headers.get("Authorization"))
        if pending(job):
            return jsonify({"result": "Generating"})
        r = job["result"]
        if r.get("xml"):
            raw = ascii_xml(r["xml"])
            return jsonify({"xml": base64.b64encode(raw.encode("ascii")).decode(), "raw": raw, "rule_id": body.get("rule_id"),
                            "name": r["name"], "description": r["description"], "assumptions": r["assumptions"],
                            "blocking_questions": r["blocking_questions"], "unsupported": r["unsupported"]})
        msg = " ".join(r.get("blocking_questions") or []) or " ".join(f"{u['phrase']}: {u['reason']}" for u in r.get("unsupported") or [])
        return jsonify({"error": {"code": "SEGMENT_NOT_BUILT", "message": msg or r.get("message") or "No member conditions found."}}), \
            503 if r.get("status") == "error" else 422

    @bp.get("/ai/pulsar/<offer_id>/prompt/<stage>", strict_slashes=False)
    def get_prompt(offer_id, stage):
        p = store.prompts.get((str(offer_id), stage))  # saved on that screen (the manager's own edit) wins
        if p is None and stage in ("offerrules", "offersegmentation"):
            plan = store.plan_for(offer_id, wait_s=WAIT_CACHED_S)  # a fresh Offer Main prompt may still be being split
            if plan:
                part = plan["rules"] if stage == "offerrules" else plan["segment"]
                return jsonify({"prompt": part["prompt"], "offer_status": None})
        if p is None and stage != "offersegmentation":
            p = store.prompts.get((str(offer_id), "offermain"))  # stock fallback (get_offer_prompts.py)
        return jsonify({"prompt": p["prompt"] if p else "", "offer_status": p["offer_status"] if p else None})

    @bp.post("/ai/pulsar/<offer_id>/prompt/<stage>", strict_slashes=False)
    def set_prompt(offer_id, stage):
        body = request.get_json(silent=True) or {}
        prompt = body.get("prompt") or ""
        store.prompts[(str(offer_id), stage)] = {"prompt": prompt, "offer_status": body.get("offer_status")}
        if stage == "offermain" and body.get("offer_status") != "update" and prompt.strip():
            # the Offer Main prompt is the prompt for the whole offer: plan it once; the offerrules / offersegmentation
            # stages then read their part of the plan (store_offer_prompts.py fans the prompt out the same way)
            make_plan(prompt.strip(), None, request.headers.get("Authorization"))
            store.plan_of[str(offer_id)] = store.key("plan", prompt.strip())
            for st in ("offerrules", "offersegmentation"):
                store.prompts.pop((str(offer_id), st), None)
        return jsonify({"prompt": prompt})

    return bp
