"""The Offer Main prompt is the prompt for the whole offer. One model call splits it into the three components the
stock wizard builds, each placed where the engine evaluates it (engine-develop, EligibileOfferStrategyImpl):

  metadata  Offer Main fields. Dates, per-member limit + frequency, total usage limit: checked by the engine BEFORE the
            rules (OfferValidator.checkBitEligibility, AvailmentLimitHelper); a failing offer is skipped silently.
  segment   WHO the offer is for: member-only conditions (profile attributes, tier programme). The engine runs the
            segment DRL on member data before the offer and gates it with member_target_type=segment.
  rules     everything about the activity (type, amount, store, time, products), rewards and counters, plus member
            conditions that change the reward inside the offer (rate by tier) or are OR-ed with activity conditions.

Each component then has its own generation API (pulsar.py): /offer/metadata returns the metadata, the prompt store
fans the component prompts out to the offersegmentation / offerrules stages, /segment/rules and /offer/rules build
from those stage prompts, with this plan as context so nothing is built twice or dropped.
"""
import datetime as dt
import time

import jsonschema

from .llm import LLMError

PLAN_SYSTEM = """You are planning a REWARD offer for the Gravty loyalty platform from the manager's description. The
offer is built in three parts; split the description so every clause lands in exactly the part the engine evaluates:

1. metadata (Offer Main): offer_name (under 50 characters), subtitle, offer_description, terms (list of short terms
   and conditions from the description), start_date / end_date (YYYY-MM-DD, ONLY dates the manager stated, never
   today by default; relative dates like "this weekend" need a blocking question), max_usage_per_user (0 = unlimited)
   with frequency (day | week | month | total | none) = how often ONE member can earn it, num_of_clients (0 =
   unlimited) with offer_frequency_type (day | lifetime | "") = total earnings across all members.
   The engine checks these before any rule runs.
2. segment: WHO the offer is for, only when the WHOLE offer is limited to members by their own data (member
   attributes or the tier programme), e.g. "for Gold members", "members aged over 30". Write segment.prompt as a
   short plain sentence about the members only. Leave it empty when every member can take part.
3. rules: everything about the activity (activity type, amount, store, day/time, products), the rewards, caps and
   counters. Write rules.prompt as a self-contained description of the rules WITHOUT the parts moved to metadata or
   the segment. Keep a member condition in the rules (not the segment) when it changes the reward inside the offer
   ("Gold earn 3x, Silver 2x") or is OR-ed with an activity condition ("Gold at the HQ store, or anyone over $500").

Write segment.prompt and rules.prompt in the manager's own words: do not translate words into codes, activity types
or account names (each part's generator maps words to the catalog itself; "spend" stays "spend").
Never change the logic while rewording: keep every and/or exactly as the manager grouped it, and when nothing moves
out of the rules, copy the manager's sentence into rules.prompt unchanged. A list "A, B, or C" is three alternatives
(A or B or C), never "(A and B) or C".
No dates stated is normal (the offer then starts when it is created and has no end): never ask for dates unless the
manager used a relative date ("this weekend", "next month"). Ask blocking questions only when a part cannot be built.

clauses: every clause of the description, in order, with the part(s) it went to and why. Nothing may be dropped.
Use the tenant catalog only to recognise member attributes, tiers and stores; do not invent codes.
Call plan_offer exactly once."""

S, I = {"type": "string"}, {"type": "integer"}
STRS = {"type": "array", "items": S}


def _obj(props):
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


PLAN_TOOL = {"name": "plan_offer", "description": "Split the offer description into metadata, segment and rules.",
             "input_schema": _obj({
                 "metadata": _obj({"offer_name": S, "subtitle": S, "offer_description": S, "terms": STRS,
                                   "start_date": S, "end_date": S, "max_usage_per_user": I,
                                   "frequency": {"type": "string", "enum": ["none", "day", "week", "month", "total"]},
                                   "num_of_clients": I, "offer_frequency_type": {"type": "string", "enum": ["", "day", "lifetime"]}}),
                 "segment": _obj({"prompt": S, "name": S, "description": S}),
                 "rules": _obj({"prompt": S}),
                 "clauses": {"type": "array", "items": _obj({"text": S, "parts": {"type": "array", "items": {
                     "type": "string", "enum": ["metadata", "segment", "rules"]}}, "why": S})},
                 "assumptions": STRS, "blocking_questions": STRS})}


def validate_plan(x):
    errs = [f"{'/'.join(map(str, e.path)) or '(root)'} {e.message}" for e in jsonschema.Draft202012Validator(PLAN_TOOL["input_schema"]).iter_errors(x)]
    if errs:
        return errs
    m = x["metadata"]
    for k in ("start_date", "end_date"):
        if m[k]:
            try:
                dt.date.fromisoformat(m[k])
            except ValueError:
                errs.append(f"metadata.{k} '{m[k]}' is not YYYY-MM-DD")
    if not errs and m["start_date"] and m["end_date"] and m["end_date"] < m["start_date"]:
        errs.append("metadata.end_date is before start_date")
    if len(m["offer_name"]) >= 50:
        errs.append("metadata.offer_name must be under 50 characters")
    if not x["rules"]["prompt"].strip():
        errs.append("rules.prompt is empty: a reward offer needs rules (say what earns what)")
    if x["segment"]["prompt"].strip() and not any("segment" in c["parts"] for c in x["clauses"]):
        errs.append("segment.prompt is set but no clause is assigned to segment")
    if not x["clauses"]:
        errs.append("clauses: list every clause of the description")
    return errs


def plan_offer(prompt, catalog, llm, max_repairs=1):
    """-> {status: planned|needs_input|error, metadata, segment, rules, clauses, assumptions, blocking_questions, diagnostics}"""
    t0 = time.time()
    system = [{"type": "text", "text": PLAN_SYSTEM},
              {"type": "text", "text": "Tenant catalog:\n" + catalog.prompt_text(), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": prompt}]
    repairs, x, errs = 0, None, []
    try:
        while True:
            turn = llm.turn(system, messages, tools=[PLAN_TOOL])
            calls = [c for c in turn.tool_calls if c[1] == "plan_offer"]
            x = calls[0][2] if calls else None
            errs = validate_plan(x) if x is not None else ["call plan_offer exactly once"]
            if not errs or repairs >= max_repairs:
                break
            repairs += 1
            text = "Validation failed:\n" + "\n".join(f"- {e}" for e in errs) + "\nCall plan_offer again, corrected."
            messages += [{"role": "assistant", "content": turn.content},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c[0], "is_error": True, "content": text}
                                                      for c in turn.tool_calls] or text}]
    except LLMError as e:
        return {"status": "error", "message": f"The offer planner is unavailable right now ({e}). Please try again.",
                "diagnostics": {"latency_s": round(time.time() - t0, 2)}}
    diag = {"model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2), "repairs": repairs, "issues_final": errs}
    if x is None or (errs and not x.get("rules")):
        return {"status": "error", "message": "I couldn't read this offer description: " + "; ".join(errs), "diagnostics": diag}
    out = dict(x, diagnostics=diag)
    out["status"] = "needs_input" if x["blocking_questions"] else "planned"
    return out


# ---------------------------------------------------------------- what each component API returns or receives
MEMBER_FREQ = {"day": "Per Day", "week": "Weekly", "month": "Per Month", "total": "Lifetime"}


def offer_main_metadata(plan, today_iso):
    """The stock /pulsar/offer/metadata 'offer' response body (generate_offer_metadata.py): the UI fills Offer Main from it."""
    m = plan["metadata"]
    freq = m["frequency"] if m["frequency"] != "none" else ""
    return {"offer_name": m["offer_name"], "subtitle": m["subtitle"], "offer_description": m["offer_description"],
            "term_and_condition": "<ul>" + "".join(f"<li>{t}</li>" for t in m["terms"]) + "</ul>",
            "num_of_clients": m["num_of_clients"] or None, "max_usage_per_user": m["max_usage_per_user"] or None,
            "offer_frequency_type": m["offer_frequency_type"],
            "option_value": freq, "option_visible_name": MEMBER_FREQ.get(freq, ""),
            "start_date": (m["start_date"] or today_iso[:10]) + "T00:00:00", "end_date": (m["end_date"] + "T23:59:00") if m["end_date"] else None}


def segment_metadata(plan):
    s = plan["segment"]
    return {"member_segment_name": s["name"], "segment_description": s["description"]}


def rules_context(plan):
    """Told to the rules generator with the rules prompt: what the other components already cover."""
    m, s = plan["metadata"], plan["segment"]
    lines = ["Already handled outside the rules (do not build rules for these, do not repeat them as conditions):"]
    settings = [f"{k}={m[k]}" for k in ("start_date", "end_date") if m[k]]
    if m["max_usage_per_user"]:
        settings.append(f"max {m['max_usage_per_user']} per member ({m['frequency']})")
    if m["num_of_clients"]:
        settings.append(f"max {m['num_of_clients']} in total ({m['offer_frequency_type'] or 'lifetime'})")
    lines.append("- Offer Main settings: " + (", ".join(settings) or "none"))
    lines.append("- Member segment (who qualifies): " + (s["prompt"] or "none, every member"))
    return "\n".join(lines)
