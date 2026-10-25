"""English -> intent (model) -> validate/repair -> expand -> Blockly XML + counters + offer settings.

Always returns a result the UI can show: a draft, a draft with blocking questions, an explanation of what is
unsupported, or an error message. Never an empty response (the Pulsar failure mode).
"""
import json
import time

from . import grammar
from .catalog import norm
from .counters import Guard, notes as counter_notes
from .adapter import Unsupported, to_intent
from .expander import expand
from .llm import LLMError
from .rule_xml import offer_xml
from .tools import SYSTEM
from .validate import Issue, validate

EMPTY_FINISH = {"readback": "", "activity_types": [], "start_date": "", "end_date": "", "max_usage_per_user": 0,
                "frequency": "none", "assumptions": [], "blocking_questions": [], "unsupported": []}


def system_blocks(catalog, account_id=None):
    # static instructions + tenant catalog; the catalog block is cached (same per tenant/sponsor)
    return [{"type": "text", "text": SYSTEM},
            {"type": "text", "text": "Tenant catalog:\n" + catalog.prompt_text(account_id), "cache_control": {"type": "ephemeral"}}]


MAX_LOOKUP_ROUNDS = 3
NOT_APPLIED = ("Not applied yet: the lookups in this turn were answered first. Re-issue ALL add_* calls and finish in "
               "your next turn, using the looked-up codes.")


def run_lookup(catalog, x):
    return catalog.lookup(x.get("object", ""), x.get("attribute", ""), x.get("text", ""))


def lookup_message(catalog, calls, found):
    """Answer every tool_use of a turn that asked for lookups: lookup results, and 'not applied' for the rest."""
    out = []
    for tid, n, x in calls:
        if n == "lookup_values":
            r = run_lookup(catalog, x)
            found.append({"attribute": f"{x.get('object')}.{x.get('attribute')}", "text": x.get("text"),
                          "top": (r.get("candidates") or [{}])[0].get("code"), "ambiguous": [a["code"] for a in r.get("ambiguous", [])]})
            out.append({"type": "tool_result", "tool_use_id": tid, "content": json.dumps(r)})
        else:
            out.append({"type": "tool_result", "tool_use_id": tid, "content": NOT_APPLIED})
    return {"role": "user", "content": out}


def repair_message(calls, issues, catalog=None):
    text = ("Validation failed:\n" + "\n".join(f"- {i}" for i in issues) +
            "\nRe-issue ALL add_* calls and finish, corrected, using only catalog codes.")
    if not calls:
        return {"role": "user", "content": [{"type": "text", "text": text}]}
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": json.dumps(run_lookup(catalog, x))} if n == "lookup_values" and catalog
        else {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": text} for tid, n, x in calls]}


def chosen_values(calls):
    """(object, attribute, [codes]) for every coded value the draft uses: conditions anywhere in a call, product
    values of line_item / collect_all, and the activity types of finish."""
    def walk(o):
        if isinstance(o, dict):
            if {"object", "attribute", "op"} <= set(o):
                codes = o.get("values") if o["op"] in ("in", "not_in") else [o.get("value")]
                yield o["object"], o["attribute"], [v for v in codes or [] if v]
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)
    for _, n, x in calls:
        if n == "finish":
            yield "activity", "h_bit_type", x.get("activity_types") or []
        elif n == "add_line_item":
            yield "product", x.get("product_attribute"), x.get("values") or []
        elif n == "add_collect_all":
            yield "product", x.get("product_attribute"), [v for g in x.get("groups") or [] for v in g]
        yield from walk(x)


def clash_questions(catalog, calls, asked, prompt=""):
    """A picked code shares its label with other codes (stores both named 'Chennai', tier classes GOLD and Gold): the
    manager can't tell which one the draft means from the screen, so ask, unless a question already names them."""
    out = []
    for obj, code, vals in chosen_values(calls):
        a = catalog.attr(obj, code)
        if not vals or (a is None and code != "h_bit_type") or (a is not None and a.type not in ("LOOKUP", "LOV")):
            continue
        for label, group, picked in catalog.label_clashes(obj, code, vals):
            if any(all(g in q for g in group) for q in asked + out):
                continue
            said = f" {norm(prompt)} "
            # the manager named the code itself ("the Chennai HQ store" -> HQ); a code spelled like the shared label
            # ("Gold" vs "GOLD", store "Chennai") is not a distinct name
            if all(norm(p) != norm(label) and f" {norm(p)} " in said for p in picked):
                continue
            name = a.label if a else "Activity type"
            out.append(f"{name} has {len(group)} values labelled '{label}' ({', '.join(group)}); the draft uses "
                       f"{', '.join(picked)}. Is that the right one, or should all of them count?")
    return out


def mto_payloads(counters, offer_id):
    """Counter definitions in the shape POST offers/{id}/member-tracked-attributes/ takes (rules-upload-model.component.ts)."""
    out = []
    for c in counters:
        m = {"offer": offer_id, "name": c["name"], "data_type": c["data_type"], "level": "OFFER", "activity_id": 1,
             "counter_type": c["counter_type"]}
        if c["data_type"] != "TEXT":
            m["target_type"] = c["target_type"]
        if c["counter_type"] == "VECTOR" and c.get("vector_namespace"):
            m["entity_attribute_map"] = {"Activity": [c["vector_namespace"]]}
        out.append(m)
    return out


def offer_settings(fin):
    return {"start_date": fin["start_date"] or None, "end_date": fin["end_date"] or None,
            "max_usage_per_user": fin["max_usage_per_user"] or None, "frequency": None if fin["frequency"] == "none" else fin["frequency"]}


def generate(prompt, catalog, llm, offer_id=None, account_id=None, tag=None, max_repairs=1, context=None, existing=None):
    """context: what the offer plan already placed in Offer Main / the segment (planner.rules_context).
    existing: {offer_status, counters} already on the offer (counters.py); None = not known."""
    t0 = time.time()
    system = system_blocks(catalog, account_id)
    messages = [{"role": "user", "content": prompt + (f"\n\n{context}" if context else "")}]
    usage, repairs, first_issues, lookups, found = {}, 0, None, 0, []
    try:
        while True:
            turn = llm.turn(system, messages)
            for k, v in turn.usage.items():
                usage[k] = usage.get(k, 0) + (v or 0)
            if turn.stop_reason == "refusal":
                return _error("The model declined this request. Please rephrase the offer description.", llm, t0, usage, repairs)
            if any(n == "lookup_values" for _, n, _ in turn.tool_calls) and lookups < MAX_LOOKUP_ROUNDS:
                lookups += 1
                messages += [{"role": "assistant", "content": turn.content}, lookup_message(catalog, turn.tool_calls, found)]
                continue
            calls = [c for c in turn.tool_calls if c[1] != "lookup_values"]
            issues = validate(catalog, calls) if calls else []
            if not calls:
                issues = [Issue("", "finish", "no tool calls were made; call the add_* tools and finish")]
            if first_issues is None:
                first_issues = [str(i) for i in issues]
            if not issues or repairs >= max_repairs:
                break
            repairs += 1
            messages += [{"role": "assistant", "content": turn.content}, repair_message(turn.tool_calls, issues, catalog)]
    except LLMError as e:
        return _error(f"The rule generator is unavailable right now ({e}). Please try again.", llm, t0, usage, repairs)

    # anything still invalid after the repair round is dropped from the draft and reported
    bad_ids = {i.tool_id for i in issues if i.tool_id}
    finishes = [x for tid, n, x in calls if n == "finish" and tid not in bad_ids]
    fin = dict(EMPTY_FINISH, **(finishes[0] if finishes else {}))
    assumptions, unsupported = list(fin["assumptions"]), list(fin["unsupported"])
    if not finishes:
        assumptions.append("Offer settings (dates, limits) could not be read from the description; set them on Offer Main.")
    for tid, n, x in calls:
        if tid in bad_ids and n != "finish":
            unsupported.append({"phrase": n[4:], "reason": "could not be validated: " + "; ".join(i.message for i in issues if i.tool_id == tid),
                                "alternative": "rephrase this part, or add it on the canvas"})
    prims = [(n, x) for tid, n, x in calls if n != "finish" and tid not in bad_ids]

    rules = None
    try:
        intent, more_unsupported = to_intent(catalog, prims, fin)
        assumptions += intent.pop("notes", [])
        unsupported += more_unsupported
        if intent["primitives"]:
            guard = Guard(existing) if existing is not None else None
            spec, counters = expand(intent, catalog.expander_catalog(account_id, tag), guard)
            xml = offer_xml(spec)
            report = guard.report([c["name"] for c in counters]) if guard else ({"checked": False} if offer_id is not None and counters else None)
            rules = {"xml": xml, "counters": mto_payloads(counters, offer_id), "expected_offer_id": offer_id,
                     "rule_count": len(spec["rules"]), "block_count": xml.count("<block "), "grammar_problems": grammar.check(xml),
                     "counter_report": report}
    except (Unsupported, ValueError, KeyError) as e:
        intent = None
        unsupported.append({"phrase": "rules", "reason": f"could not compile: {e}", "alternative": "build these rules on the canvas"})

    questions = list(fin["blocking_questions"])
    if rules:
        questions += clash_questions(catalog, [c for c in calls if c[0] not in bad_ids], questions, prompt)
    if rules:
        status = "needs_input" if questions else "draft"
    elif unsupported:
        status = "unsupported"
    else:
        status = "needs_input"
        questions = questions or ["I couldn't turn this into rules. Could you describe who earns what, and when?"]
    return {"status": status, "readback": fin["readback"], "assumptions": assumptions, "blocking_questions": questions,
            "unsupported": unsupported, "offer_settings": offer_settings(fin), "intent": intent, "rules": rules,
            "counter_notes": counter_notes(rules["counter_report"]) if rules else [],
            "diagnostics": {"model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2), "repairs": repairs,
                            "issues_first_pass": first_issues or [], "issues_final": [str(i) for i in issues], "usage": usage,
                            "lookup_rounds": lookups, "lookups": found, "calls": [[n, x] for _, n, x in calls]}}


def _error(message, llm, t0, usage, repairs):
    return {"status": "error", "message": message, "readback": "", "assumptions": [], "blocking_questions": [], "unsupported": [],
            "offer_settings": None, "intent": None, "rules": None,
            "diagnostics": {"model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2), "repairs": repairs, "usage": usage}}
