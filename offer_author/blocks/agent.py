"""Blocks mode (OFFER_AUTHOR_ENGINE=blocks): one agent that solves the offer and composes the blocks itself.

It gets the toolbox as the UI defines it (assets/block_reference.md, generated from the gravty-ui source), how rules
execute (assets/kt.md), the product's seeded blueprints (as block trees, with the DRL the UI made of them) and the
tenant catalog. It describes blocks as a tree; the tools write the XML (tree.py), check it like the UI's own connection
checks do (check.py), run it through the stock rules screen (compile: what it keeps, the DRL, the counters it creates)
and the real engine (simulate). No business logic here: this file runs the tool loop and returns the same result
shape as pipeline.generate.
"""
import glob
import json
import os
import re
import time

from ..counters import Guard, notes as counter_notes
from ..llm import LLMError
from ..pipeline import EMPTY_FINISH, offer_settings, run_lookup
from . import engine, ui
from .check import check
from .tree import TreeError, from_xml, to_xml

ASSETS = os.path.join(os.path.dirname(__file__), "assets")
CATALOGS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "catalogs")
MAX_TURNS = int(os.environ.get("OFFER_AUTHOR_BLOCKS_TURNS", 12))

SYSTEM = """You author the rules of a Gravty loyalty REWARD offer for the stock gravty-ui offer-rules screen, from a loyalty
manager's English description. You solve the offer and compose it from the reward toolbox blocks; the tools turn your
block tree into the XML the screen's own Upload Rules path loads, the screen generates the Drools DRL the engine runs
and creates the offer counters (MA_O) your blocks refer to.

You have, below: the block reference (generated from the UI source: inputs, connection checks, fields, mutation
attributes, the options the live screen offers, and the shapes saved blueprints use), how rules execute in the engine
(KT), the product's seeded blueprints (block trees, and the DRL the UI generated from them), and the tenant catalog.

Block tree: {"type": ..., "mutation": {attr: value}, "fields": {name: value}, "inputs": {name: [blocks] for a statement
input (chained in order) | block for a value input}}, optional "id". The root is one offer_generic block. You never
write XML: the tools order mutation attributes, assign ids and namespaces as the UI does.

Work like this:
1. Find codes: use only codes from the tenant catalog. Long value lists are not written out; call lookup_values with the
   manager's words (one phrase per call, several calls per turn are fine) and use only codes it returns.
2. Build the block tree and call compile. It runs the stock screen: fix every problem it reports (a dropped or changed field
   means the screen did not accept that value or shape) and read the DRL to confirm each rule does what was asked.
3. Call simulate with transactions that exercise every rule: qualifying and non-qualifying ones, boundaries, repeats
   for anything that counts across transactions, and the expected outcome of each. If the engine disagrees, decide
   whether the rules or your expectation is wrong, fix, compile and simulate again.
4. Call finish once, after the last compile of the tree you want to deliver was clean.

Block conventions:
- Give each block the mutation attributes and fields the saved blueprints give that kind of block ("saved mutation"
  lines); the upload dialog creates a counter only when a counter's mutation has one of the listed attribute sets.
- blueprint_variable blocks in the blueprints are template placeholders: never emit them; put concrete values
  (static_value, static_string_value, catalog codes, the loyalty account and tag from the catalog) in their place.
- Points go to the loyalty account and reward tag the catalog names.

Offer semantics:
- Every restriction the manager states (store, day, time, tier, amount, activity, product) must be in the rules. A
  condition every rule needs belongs once in the On Activity filter (activity_criteria), not repeated per rule.
- "spend", "spends", "spending" mean the activity type labelled exactly "Spend"; use another type only when the
  manager names it. No activity named: use Spend and record that as an assumption.
- State that must survive from one transaction to the next (counts, totals, "once", caps, unlocks, distinct values)
  lives in offer counters (MA_O); MA_A values do not persist across transactions.
- Offer start/end dates, per-member usage limits and frequency are Offer Main settings the engine checks before any
  rule runs: put them in finish, never in the rules. Only dates the manager stated; never ask for dates.
- "Nx points" means points = measure x N in total; record that as an assumption. "over X" is strict; "at least X" is >=.
- DAY_OF_WEEK numbers: Mon=1 ... Sun=7. HOUR_OF_DAY is 0-23.
- When the message lists what is "already handled outside the rules", build only the rest.
- assumptions: interpretations the manager should glance at. An assumption never drops or widens something the manager
  stated: a stated restriction or measure the toolbox or catalog cannot express goes in `unsupported`, with a reason.
- blocking_questions: ONLY when no sensible draft is possible without the answer, or when a phrase matches several
  codes (then ask even if the draft uses all of them). Still build everything else.
- Something that cannot be expressed on a single member's own transactions goes in `unsupported` with an alternative.
- Always end with finish: rules, a blocking question, or an unsupported entry. Do not answer in prose.
"""

S, STRS = {"type": "string"}, {"type": "array", "items": {"type": "string"}}
XML_ARG = {"blocks": {"type": "object", "description": "The block tree: one offer_generic root"}}


def tool(name, desc, props, required=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": list(props) if required is None else required}}


BIT = {"type": "object", "properties": {
    "header": {"type": "object", "description": "BIT header attributes (h_bit_type, h_bit_amount, h_bit_date "
                                                "YYYY-MM-DDTHH:MM:SS, h_location, ...); defaults: SPEND, 10, one day apart"},
    "lines": {"type": "array", "items": {"type": "object"}, "description": "line items: l_product_external_id, l_quantity, l_amount"},
    "expect": {"type": "object", "description": "what the manager's offer should do on this BIT: points (REWARD_POINTS "
                                                "total), privilege (id or null), mto {counter: value after}, mta {...}"}}}
TOOLS = [
    tool("lookup_values", "Find catalog codes for the manager's words, for an attribute whose values are not listed.",
         {"object": {"type": "string", "enum": ["activity", "member", "product"]}, "attribute": S,
          "text": {"type": "string", "description": "The manager's words for ONE value"}}),
    tool("validate", "Fast offline check: well-formed, toolbox blocks only, connection checks, catalog codes.", XML_ARG),
    tool("compile", "Load the XML in the stock rules screen through Upload Rules. Returns what it dropped or changed, "
                    "validation messages, the counters it created, and the DRL it generated.", XML_ARG),
    tool("simulate", "Run the DRL of the last compile in the real engine, one member, BITs in order (offer counters "
                     "persist across them). Returns per BIT: points, privileges, counters, and mismatches with expect.",
         {"member": {"type": "object", "description": "{attributes: {date_of_birth, ...}, tier}"},
          "products": {"type": "object", "description": "SKU -> product attributes, e.g. {\"SNK-1\": {\"category\": \"SNACKS\"}}"},
          "bits": {"type": "array", "items": BIT}}, required=["bits"]),
    tool("finish", "Call once, last. Delivers the blocks of the last clean compile.",
         {"readback": {"type": "string", "description": "Plain-English description of what the offer will do"},
          "start_date": {"type": "string", "description": "YYYY-MM-DD the manager stated, else empty"},
          "end_date": {"type": "string", "description": "YYYY-MM-DD the manager stated (last day included), else empty"},
          "max_usage_per_user": {"type": "integer", "description": "0 if unlimited"},
          "frequency": {"type": "string", "enum": ["none", "day", "week", "month", "total"]},
          "assumptions": STRS, "blocking_questions": STRS,
          "unsupported": {"type": "array", "items": {"type": "object", "properties": {"phrase": S, "reason": S, "alternative": S},
                                                      "required": ["phrase", "reason"]}}},
         required=["readback", "assumptions", "blocking_questions", "unsupported"]),
]


def _compact_drl(d):
    return "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in d.splitlines() if ln.strip())


def blueprints():
    out = []
    for f in sorted(glob.glob(os.path.join(ASSETS, "blueprints", "*.xml"))):
        name = os.path.basename(f)[:-4]
        drl = f[:-4] + ".drl"
        tree = json.dumps(from_xml(open(f).read()), separators=(",", ":"))
        out.append(f"### {name.replace('_', ' ')}\nBlocks:\n{tree}\n" +
                   (f"DRL:\n{_compact_drl(open(drl).read())}\n" if os.path.exists(drl) else ""))
    return "\n".join(out)


_STATIC = None


def static_prompt():
    global _STATIC
    if _STATIC is None:
        _STATIC = "\n\n".join([open(os.path.join(ASSETS, "block_reference.md")).read(), open(os.path.join(ASSETS, "kt.md")).read(),
                               "# Seeded blueprints (product offer templates, as block trees)\n\n" + blueprints()])
    return _STATIC


def system_blocks(catalog, account_id=None):
    return [{"type": "text", "text": SYSTEM},
            {"type": "text", "text": static_prompt(), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Tenant catalog:\n" + catalog.prompt_text(account_id) + _account_line(catalog, account_id),
             "cache_control": {"type": "ephemeral"}}]


def _account_line(catalog, account_id):
    acc = catalog.account(account_id)
    tags = [t["code"] for t in (acc or {}).get("tags") or []]
    tag = catalog.default_tag or (tags[0] if tags else None)
    return f"\nReward tag for points: {tag}" + (f" (tags on this account: {', '.join(tags)})" if tags else "") if acc else ""


def compile_tenant(catalog):
    return catalog.tenant if os.path.isdir(os.path.join(CATALOGS, catalog.tenant or "-", "bolapi")) else ""


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


def located(problems, paths):
    """'action#b9' in a problem -> 'action#b9 (activity > rule 'X'.ACTION > action)': where the model put that block."""
    def at(p):
        for bid in re.findall(r"#([\w-]+)", p):
            if bid in paths:
                p = p.replace(f"#{bid}", f"#{bid} ({paths[bid]})", 1)
        return p
    return [at(p) for p in problems]


class Session:
    """Tool state for one generation: the last compile (its XML, DRL, counters, problems) and what was simulated."""

    def __init__(self, catalog, offer_id):
        self.catalog, self.offer_id, self.tenant = catalog, offer_id, compile_tenant(catalog)
        self.last, self.clean, self.sims, self.counts, self.lookups, self.trace = None, None, [], {}, [], []

    def run(self, name, x):
        self.counts[name] = self.counts.get(name, 0) + 1
        if name == "lookup_values":
            r = run_lookup(self.catalog, x)
            self.lookups.append({"attribute": f"{x.get('object')}.{x.get('attribute')}", "text": x.get("text"),
                                 "top": (r.get("candidates") or [{}])[0].get("code")})
            return r
        if name in ("validate", "compile"):
            try:
                xml, paths = to_xml(x.get("blocks"))
            except (TreeError, TypeError, AttributeError) as e:
                return {"ok": False, "problems": [f"not a block tree: {e}"]}
            if name == "validate":
                p = located(check(xml, self.catalog), paths)
                return {"ok": not p, "problems": p}
            return self.compile(xml, paths)
        if name == "simulate":
            if not self.last or not (self.last["res"].get("drl") or "").strip():
                return {"error": "compile the XML first"}
            if not engine.available():
                return {"error": "engine harness not available here; rely on the DRL"}
            # the tenant's privilege products exist for the engine, as they do on the tenant
            priv = {pid: {"name": p.get("name"), "cost": p.get("cost", 0)} for pid, p in self.catalog.privileges.items()}
            r = engine.simulate(self.last["res"]["drl"], dict(x, privilege_products={**priv, **(x.get("privilege_products") or {})}),
                                sponsor_id=self.catalog.sponsor_id)
            self.sims.append({"input": x, "result": r})
            return r
        return {"error": f"unknown tool {name}"}

    def compile(self, xml, paths=None):
        pre = check(xml, self.catalog)
        if pre and any(p.startswith("not well-formed") for p in pre):
            return {"ok": False, "problems": pre}
        res = ui.compile_xml(xml, self.tenant)
        problems = located(pre + ui.problems(res), paths or {})
        self.last = {"xml": xml, "res": res, "problems": problems}
        if not problems:
            self.clean = self.last
        return {"ok": not problems, "problems": problems,
                "counters_created": [{k: c.get(k) for k in ("name", "data_type", "counter_type", "target_type", "entity_attribute_map")
                                      if c.get(k) is not None} for c in res.get("counters_created") or []],
                "drl": res.get("drl", "")}


def generate(prompt, catalog, llm, offer_id=None, account_id=None, tag=None, context=None, existing=None, **_):
    t0 = time.time()
    system = system_blocks(catalog, account_id)
    have = existing and existing.get("counters")
    intro = prompt + (f"\n\n{context}" if context else "")
    if have:
        intro += ("\n\nCounters already on this offer (reuse one only when its type fits; the upload reuses counters by "
                  "name): " + "; ".join(f"{c['name']} ({c.get('data_type')} {c.get('counter_type')})" for c in existing["counters"]))
    messages = [{"role": "user", "content": intro}]
    s, usage, fin, turns = Session(catalog, offer_id), {}, None, 0
    try:
        while turns < MAX_TURNS and fin is None:
            turns += 1
            _cache_last(messages)
            turn = llm.turn(system, messages, tools=TOOLS)
            for k, v in turn.usage.items():
                usage[k] = usage.get(k, 0) + (v or 0)
            if turn.stop_reason == "refusal":
                return _error("The model declined this request. Please rephrase the offer description.", llm, t0, usage)
            if not turn.tool_calls:
                messages += [{"role": "assistant", "content": turn.content},
                             {"role": "user", "content": "Use the tools: compile, simulate, then finish."}]
                continue
            results = []
            for tid, name, x in turn.tool_calls:
                if name == "finish":
                    if s.last and s.last["problems"] and s.clean is not s.last and turns < MAX_TURNS - 1:
                        out = {"error": "the last compile had problems; fix them and compile again before finish",
                               "problems": s.last["problems"]}
                    else:
                        fin, out = x, {"ok": True}
                else:
                    try:
                        out = s.run(name, x)
                    except ui.CompileUnavailable as e:
                        return _error(f"The rules compiler is unavailable right now ({e}).", llm, t0, usage)
                s.trace.append({"turn": turns, "tool": name, **{k: out[k] for k in ("ok", "problems", "error") if k in out},
                                **({"sim": [b.get("mismatch") or "ok" for b in out.get("bits", [])]} if name == "simulate" else {})})
                results.append({"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out)[:60000]})
            messages += [{"role": "assistant", "content": turn.content}, {"role": "user", "content": results}]
    except LLMError as e:
        return _error(f"The rule generator is unavailable right now ({e}). Please try again.", llm, t0, usage)

    finished = fin is not None
    fin = dict(EMPTY_FINISH, **(fin or {}))
    assumptions, unsupported, questions = list(fin["assumptions"]), list(fin["unsupported"]), list(fin["blocking_questions"])
    if not finished:
        assumptions.append("The generator ran out of steps before finishing; review the draft carefully.")
    done = s.clean or s.last
    rules = None
    if done and (done["res"].get("drl") or "").strip():
        xml = done["xml"]
        posted = done["res"].get("counters_created") or []
        guard = Guard(existing) if existing is not None else None
        names = [c["name"] for c in posted]
        report = guard.report(names) if guard else ({"checked": False} if offer_id is not None and posted else None)
        rules = {"xml": xml, "counters": [dict(c, offer=offer_id) for c in posted], "expected_offer_id": offer_id,
                 "rule_count": len(re.findall(r'<block type="(?:rule|rule_line|payment_rule)"', xml)),
                 "block_count": xml.count("<block "), "grammar_problems": done["problems"], "counter_report": report,
                 "drl": done["res"]["drl"]}
    if rules:
        status = "needs_input" if questions else "draft"
    elif unsupported:
        status = "unsupported"
    else:
        status = "needs_input"
        questions = questions or ["I couldn't turn this into rules. Could you describe who earns what, and when?"]
    sims = [b for sm in s.sims for b in (sm["result"].get("bits") or [])]
    return {"status": status, "readback": fin["readback"], "assumptions": assumptions, "blocking_questions": questions,
            "unsupported": unsupported, "offer_settings": offer_settings(fin), "intent": None, "rules": rules,
            "counter_notes": counter_notes(rules["counter_report"]) if rules else [],
            "diagnostics": {"engine": "blocks", "model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2),
                            "turns": turns, "tool_calls": s.counts, "usage": usage, "lookups": s.lookups,
                            "simulated_bits": len(sims), "simulated_ok": sum(1 for b in sims if b.get("ok")),
                            "last_sim": s.sims[-1] if s.sims else None, "trace": s.trace, "repairs": max(0, s.counts.get("compile", 0) - 1)}}


def _error(message, llm, t0, usage):
    return {"status": "error", "message": message, "readback": "", "assumptions": [], "blocking_questions": [], "unsupported": [],
            "offer_settings": None, "intent": None, "rules": None,
            "diagnostics": {"engine": "blocks", "model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2), "usage": usage}}
