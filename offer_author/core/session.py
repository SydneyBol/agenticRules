"""The agent's tools, as code: one Session per offer runs each tool call the model makes and keeps what they built.

No model and no prompt here (this package never imports offer_author.ai): every tool takes the model's arguments
and returns plain facts.
  lookup_values   catalog codes for the manager's words (catalog.py)
  set_offer_main  Offer Main settings, checked (dates, lengths, enums)
  validate        selections -> XML (tree.py) + the UI's own checks offline (check.py) + counter declarations (counters.py)
  compile         the same, then the stock screen headless (ui.py): what it keeps, whether it compiles, counters it creates
  simulate        the compiled rules in the real engine (engine.py), with the segment and Offer Main checks
The compiled rules (DRL) stay inside the Session: no tool result carries them, and neither does the outcome.
"""
import datetime as dt
import os
import re
import xml.etree.ElementTree as ET

from . import engine, ui
from .check import check
from .counters import Guard, notes as counter_notes
from .tree import TreeError, apply_edits, counter_ref, expand, inline, number, to_xml, unfilled

DATA = os.path.join(os.path.dirname(__file__), "data")
PARTS = ("offer_main", "segment", "rules")


def compile_tenant(catalog, catalogs):
    """The tenant whose dumps the compile screen answers its catalog calls from ('' = its fixture): only when
    Settings.catalogs holds that tenant's dumps."""
    return catalog.tenant if catalogs and os.path.isdir(os.path.join(catalogs, catalog.tenant or "-", "bolapi")) else ""



def located(problems, paths):
    """'action#b9' in a problem -> 'action#b9 (activity > rule 'X'.ACTION > action)': where the model put that block."""
    def at(p):
        for bid in re.findall(r"#([\w-]+)", p):
            if bid in paths:
                p = p.replace(f"#{bid}", f"#{bid} ({paths[bid]})", 1)
        return p
    return [at(p) for p in problems]



def offer_main_problems(x):
    errs = []
    if len(x.get("offer_name") or "") >= 50:
        errs.append("offer_name must be under 50 characters")
    if not (x.get("offer_name") or "").strip():
        errs.append("offer_name is empty")
    dates = []
    for k in ("start_date", "end_date"):
        if x.get(k):
            try:
                dates.append(dt.date.fromisoformat(x[k]))
            except ValueError:
                errs.append(f"{k} '{x[k]}' is not YYYY-MM-DD")
    if len(dates) == 2 and dates[1] < dates[0]:
        errs.append("end_date is before start_date")
    if x.get("frequency", "none") not in ("none", "day", "week", "month", "total"):
        errs.append("frequency must be none, day, week, month or total")
    if x.get("offer_frequency_type", "") not in ("", "day", "lifetime"):
        errs.append("offer_frequency_type must be '', day or lifetime")
    return errs



def _mutations(tree, path=""):
    """Where the model wrote mutation attributes itself: the tools derive them, so these are rejected."""
    if isinstance(tree, list):
        return [p for i, n in enumerate(tree) for p in _mutations(n, f"{path}[{i}]")]
    if not isinstance(tree, dict):
        return []
    here = f"{path} > {tree.get('type')}" if path else str(tree.get("type"))
    out = [here] if "mutation" in tree else []
    for k, v in (tree.get("inputs") or {}).items():
        out += _mutations(v, f"{here}.{k}")
    return out



def counter_problems(xml, counters, guard):
    """Every counter the blocks refer to is declared once, VECTOR counters say what they count per, and a name that
    already exists on the offer is reused only when the upload's name match would be right (counters.Guard)."""
    out, names = [], [c.get("name") for c in counters]
    for n in {n for n in names if names.count(n) > 1}:
        out.append(f"counter '{n}' is declared twice")
    for c in counters:
        if c.get("counter_type") == "VECTOR" and not c.get("namespace"):
            out.append(f"counter '{c.get('name')}' is VECTOR: give its namespace")
        spec = (c.get("name"), c.get("data_type"), c.get("counter_type", "REGULAR"), c.get("namespace"), c.get("level") or "MA_O")
        if guard and guard.taken(*spec):
            out.append(f"counter '{c['name']}' already exists on this offer and {guard.why(*spec)}: use another name")
    used = set()
    for m in ET.fromstring(xml).iter():
        if m.tag.split("}")[-1] == "mutation":
            level, name = counter_ref(m.attrib)
            if name and level == "MA_O":
                used.add(name)
    out += [f"the blocks refer to offer counter '{n}', which is not declared in counters" for n in sorted(used - set(names))]
    return out



class Session:
    """Tool state for one offer: Offer Main, and per canvas the last compile and the last clean one."""

    def __init__(self, catalog, offer_id, settings, engine, existing=None, on_offer_main=None):
        """settings: core.settings.Settings (the compile service URL). engine: the core.engine.Engine simulate runs on."""
        self.catalog, self.offer_id, self.tenant = catalog, offer_id, compile_tenant(catalog, settings.catalogs)
        self.settings, self.engine = settings, engine
        self.guard = Guard(existing) if existing is not None else None
        self.on_offer_main = on_offer_main
        self.offer_main, self.last, self.clean = None, {}, {}
        self.trees = {}  # canvas -> its last tree, ids filled in (what compile edits apply to)
        self.defs = {}  # canvas -> the defs its trees use by name (kept for later edits and trees)
        self.sims, self.counts, self.lookups, self.trace = [], {}, [], []

    def run(self, name, x):
        self.counts[name] = self.counts.get(name, 0) + 1
        if name == "lookup_values":
            r = self.catalog.lookup(x.get("object", ""), x.get("attribute", ""), x.get("text", ""))
            self.lookups.append({"attribute": f"{x.get('object')}.{x.get('attribute')}", "text": x.get("text"),
                                 "top": (r.get("candidates") or [{}])[0].get("code")})
            return r
        if name == "set_offer_main":
            errs = offer_main_problems(x)
            if errs:
                return {"ok": False, "problems": errs}
            self.offer_main = dict(x)
            if self.on_offer_main:
                self.on_offer_main(self.offer_main)
            return {"ok": True}
        if name in ("validate", "compile"):
            return self.canvas(name, x)
        if name == "simulate":
            return self.simulate(x)
        return {"error": f"unknown tool {name}"}

    def canvas(self, name, x):
        canvas = x.get("canvas") or "rules"
        if canvas not in ("rules", "segment"):
            return {"ok": False, "problems": ["canvas must be rules or segment"]}
        blocks, counters = x.get("blocks"), x.get("counters")
        if blocks is None and x.get("edits") is not None:  # a repair: changes to this canvas's last tree, by block id
            if canvas not in self.trees:
                return {"ok": False, "problems": [f"no earlier {canvas} tree to edit: send blocks"]}
            try:
                blocks = apply_edits(self.trees[canvas], x["edits"])
            except TreeError as e:
                return {"ok": False, "problems": [str(e)]}
            if counters is None:
                counters = (self.last.get(canvas) or {}).get("counters")
        defs = {**self.defs.get(canvas, {}), **(x.get("defs") or {})} if isinstance(x.get("defs") or {}, dict) else x.get("defs")
        try:
            blocks = expand(inline(blocks, defs))
        except TreeError as e:
            return {"ok": False, "problems": [str(e)]}
        self.defs[canvas] = defs
        own = _mutations(blocks)
        if own:
            return {"ok": False, "problems": [f"{p}: remove 'mutation'; give fields, picked codes and counters, the tools "
                                              "write the mutation" for p in own]}
        counters = [c for c in counters or [] if isinstance(c, dict)]
        try:
            blocks = number(blocks)
            missing = unfilled(blocks, self.catalog, {c.get("name"): c for c in counters})
            if missing:
                self.trees[canvas] = blocks
                return {"ok": False, "problems": missing, "compiled": False,
                        "fix_with": 'edits by block id, or the counters, e.g. {"canvas": "%s", "counters": [...]}' % canvas}
            xml, paths = to_xml(blocks, self.catalog, {c.get("name"): c for c in counters}, canvas)
        except (TreeError, TypeError, AttributeError, ValueError) as e:
            return {"ok": False, "problems": [f"not a block tree: {e}"]}
        self.trees[canvas] = blocks
        pre = located(check(xml, self.catalog, canvas=canvas), paths) + counter_problems(xml, counters, self.guard)
        if name == "validate" or any(p.startswith("not well-formed") for p in pre):
            return {"ok": not pre, "problems": pre}
        res = ui.compile_xml(xml, url=self.settings.compile_url, tenant=self.tenant, canvas=canvas)
        problems = pre + located(ui.problems(res), paths)
        last = {"xml": xml, "res": res, "problems": problems, "counters": counters}
        self.last[canvas] = last
        if not problems:
            self.clean[canvas] = last
        out = {"ok": not problems, "problems": problems, "compiled": bool((res.get("drl") or "").strip())}
        if problems:  # a repair needs only the blocks the problems name
            out["fix_with"] = 'edits by block id, e.g. {"canvas": "%s", "edits": [{"id": "b9", "fields": {...}}]}' % canvas
        if canvas == "rules":
            out["counters_created"] = [{k: c.get(k) for k in ("name", "data_type", "counter_type", "target_type", "entity_attribute_map")
                                        if c.get(k) is not None} for c in res.get("counters_created") or []]
        return out

    def simulate(self, x):
        rules = self.last.get("rules")
        if not rules or not (rules["res"].get("drl") or "").strip():
            return {"error": "compile the rules canvas first"}
        if not self.engine.available():
            return {"error": "engine harness not available here; rely on compile"}
        seg = self.last.get("segment")
        # the tenant's privilege products exist for the engine, as they do on the tenant
        priv = {pid: {"name": p.get("name"), "cost": p.get("cost", 0)} for pid, p in self.catalog.privileges.items()}
        r = engine.simulate(self.engine, rules["res"]["drl"], dict(x, privilege_products={**priv, **(x.get("privilege_products") or {})}),
                            sponsor_id=self.catalog.sponsor_id, header_defaults=self.catalog.bit_defaults(), segment_drl=(seg["res"].get("drl") if seg else None) or None,
                            offer_main=self.offer_main)
        if r.get("compile") != "ok":  # the engine's message quotes the compiled rules: never shown to the model
            r = {"compile": "error", "error": "the engine could not compile these rules; simplify or rebuild the blocks"}
        self.sims.append({"input": x, "result": r})
        return r

    def pending(self):
        """Why finish must wait: a canvas whose last compile had problems."""
        return [p for c, last in self.last.items() if last["problems"] and self.clean.get(c) is not last
                for p in [f"{c}: {q}" for q in last["problems"]]]



def offer_settings(om):
    if not om:
        return None
    return {"start_date": om.get("start_date") or None, "end_date": om.get("end_date") or None,
            "max_usage_per_user": om.get("max_usage_per_user") or None,
            "frequency": None if om.get("frequency", "none") == "none" else om["frequency"]}



def finish_blockers(s, parts, x):
    """Why the model's finish is sent back: a canvas whose last compile had problems, or Offer Main not set when it
    is part of the job (unless the model is asking or reporting something it cannot build)."""
    out = s.pending()
    if "offer_main" in parts and s.offer_main is None and not (x.get("blocking_questions") or x.get("unsupported")):
        out.append("Offer Main is not set: call set_offer_main")
    return out


NOTE_KEYS = ("assumptions", "blocking_questions", "unsupported")


def notes_by_part(fin, parts):
    """The finish items per part of the job: {part: {assumptions: [text], blocking_questions: [text],
    unsupported: [{phrase, reason, alternative?}]}}. An item naming no part, or a part outside the job, belongs to
    every part of the job."""
    out = {p: {k: [] for k in NOTE_KEYS} for p in parts}
    for key in NOTE_KEYS:
        for item in fin.get(key) or []:
            item = {"text": item} if isinstance(item, str) else item
            value = {k: v for k, v in item.items() if k != "part"} if key == "unsupported" else item.get("text", "")
            for p in [item["part"]] if item.get("part") in out else parts:
                out[p][key].append(value)
    return out


def part_view(res, part):
    """What one screen's API returns: its part's assumptions, questions and unsupported items, and its status (built:
    draft, or needs_input when that part asks something; not built: unsupported or needs_input)."""
    notes = (res.get("by_part") or {}).get(part) or {k: res.get(k) or [] for k in NOTE_KEYS}
    if res.get("status") == "error":
        return dict(notes, status="error")
    built = {"offer_main": res.get("offer_main"), "segment": res.get("segment"), "rules": res.get("rules")}[part]
    status = ("needs_input" if notes["blocking_questions"] else "draft") if built else \
        "unsupported" if notes["unsupported"] else "needs_input"
    return dict(notes, status=status)


def outcome(s, fin, parts, offer_id):
    """The run's result from what the tools built and the model's finish (None: it never finished).
    -> {status, readback, assumptions, blocking_questions, unsupported (all parts), by_part (each part's own),
        offer_main, offer_settings, segment, rules, counter_notes, diagnostics}"""
    finished = fin is not None
    fin = dict({"readback": "", "segment_name": "", "segment_description": "", "assumptions": [], "blocking_questions": [],
                "unsupported": []}, **(fin or {}))
    by_part = notes_by_part(fin, parts)
    if not finished:
        for p in parts:
            by_part[p]["assumptions"].append("The generator ran out of steps before finishing; review the draft carefully.")

    rules = segment = None
    done = s.clean.get("rules") or s.last.get("rules")
    if "rules" in parts and done and (done["res"].get("drl") or "").strip():
        xml, posted = done["xml"], done["res"].get("counters_created") or []
        names = [c["name"] for c in posted]
        report = s.guard.report(names) if s.guard else ({"checked": False} if offer_id is not None and posted else None)
        rules = {"xml": xml, "counters": [dict(c, offer=offer_id) for c in posted], "expected_offer_id": offer_id,
                 "rule_count": len(re.findall(r'<block type="(?:rule|rule_line|payment_rule)"', xml)),
                 "block_count": xml.count("<block "), "grammar_problems": done["problems"], "counter_report": report}
    seg = s.clean.get("segment") or s.last.get("segment")
    if "segment" in parts and seg and (seg["res"].get("drl") or "").strip():
        segment = {"xml": seg["xml"], "name": fin["segment_name"] or "Offer members",
                   "description": fin["segment_description"], "problems": seg["problems"]}
        if rules:
            by_part["rules"]["assumptions"].append(f"Who qualifies is the member segment '{segment['name']}': create and "
                                                   "select it on Select Members, otherwise every member qualifies.")
    # a canvas that never compiled clean (the run stopped before its problems were fixed) is not a draft: the stock
    # screen would drop or reject parts of it, so the manager is asked instead
    for canvas, built_canvas in (("rules", rules), ("segment", segment)):
        if built_canvas and not s.clean.get(canvas):
            problems = s.last[canvas]["problems"]
            by_part[canvas]["blocking_questions"].append(
                f"I could not finish the {canvas} without problems the stock screen reports ("
                + "; ".join(map(str, problems[:3])) + "). Please review the draft or describe the offer again.")
    # the whole offer's items (CLI, a full-offer reader): each once, in part order
    assumptions = list(dict.fromkeys(a for p in parts for a in by_part[p]["assumptions"]))
    questions = list(dict.fromkeys(q for p in parts for q in by_part[p]["blocking_questions"]))
    unsupported = []
    for p in parts:
        unsupported += [u for u in by_part[p]["unsupported"] if u not in unsupported]
    built = rules if "rules" in parts else segment if "segment" in parts else s.offer_main
    if built:
        status = "needs_input" if questions else "draft"
    elif unsupported:
        status = "unsupported"
    else:
        status = "needs_input"
        if not questions:
            questions = ["I couldn't turn this into an offer. Could you describe who earns what, and when?"]
            for p in parts:
                by_part[p]["blocking_questions"] += questions
    sims = [b for sm in s.sims for b in (sm["result"].get("bits") or [])]
    return {"status": status, "readback": fin["readback"], "assumptions": assumptions, "blocking_questions": questions,
            "unsupported": unsupported, "by_part": by_part, "offer_main": s.offer_main,
            "offer_settings": offer_settings(s.offer_main), "segment": segment, "rules": rules, "counter_notes": counter_notes(rules["counter_report"]) if rules else [],
            "diagnostics": {"parts": list(parts), "tool_calls": s.counts, "lookups": s.lookups,
                            "simulated_bits": len(sims), "simulated_ok": sum(1 for b in sims if b.get("ok")),
                            "last_sim": s.sims[-1] if s.sims else None, "trace": s.trace,
                            "repairs": max(0, s.counts.get("compile", 0) - len(s.last))}}


def error_result(message):
    return {"status": "error", "message": message, "readback": "", "assumptions": [], "blocking_questions": [], "unsupported": [],
            "by_part": {}, "offer_main": None, "offer_settings": None, "segment": None, "rules": None, "counter_notes": [], "diagnostics": {}}
