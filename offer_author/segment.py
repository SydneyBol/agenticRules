"""English -> member segment Blockly XML (the Select Members / add-segment canvas), in the shapes the stock UI saves.

Same split as the rules: the model picks conditions through one tool (`build_segment`), validate.py-style checks run
against the tenant catalog (one repair round), and a deterministic serializer writes the XML. The UI's segment
Blockly compiles the DRL on save; the engine runs it on member data only (SegmentProcessor, before any offer rule).

XML shapes copied from segments saved on devintam1 (catalogs/devintam1/segments/segments.json):
  targeting_holder > statement CONDITIONS > targeting_condition (chained with <next>) | or (OPERAND1, OPERAND2)
  MA_P LOOKUP/LOV/TEXT:   mutation value="a,b" visible_value="A, B" (in/not_in) or field value (==, !=, contains...)
  MA_P NUMBER/DATE:       value RHS_INPUT static_value|static_date (+ RHS_VALUE for between), DATE adds date_operator
  MA_T tier name:         tierslevel=<class>, tiermainoption=name, field tierlevelnamevalue (==) or mutation value (in)
An `or` with one operand compiles to `undefined || ...` (seen in a saved segment), so every OR here has two.
"""
import time

import jsonschema

from xml.sax.saxutils import escape

from .llm import LLMError


def esc(s):
    return escape(str(s), {'"': "&quot;"})

SEG_SYSTEM = """You turn a loyalty manager's description of WHO an offer is for into a member segment for the Gravty
loyalty platform. A segment is evaluated on the member's own data only (profile attributes and the tier programme),
before any offer rule runs; it never sees transactions, stores, amounts or dates of activities.

Call build_segment exactly once.
- Use ONLY codes from the tenant catalog. Member attributes are level MA_P; the tier programme is level MA_T (a tier
  class and the tier names it contains). A member attribute that is called "tier" or "tier class" is MA_P, not MA_T:
  pick the one whose values match the manager's words, and if both match, ask a blocking question.
- conditions are ANDed; or_groups are alternatives (any one group), ANDed with conditions.
- If the description contains nothing about who the members are, return no conditions and say so in unsupported.
- Anything about transactions (spend, store, product, amount, activity dates) is not a segment condition: list it in
  unsupported with reason "belongs to the offer rules".
- name: a short segment name (under 50 characters); description: one sentence.
- DATE values are YYYY-MM-DD; date_part is DATE unless the manager compares a part (YEAR, MONTH, DAY_OF_MONTH).
"""

S, B = {"type": "string"}, {"type": "boolean"}
SEG_COND = {"type": "object", "additionalProperties": False,
            "required": ["level", "attribute", "tier_class", "op", "value", "values", "value2", "date_part"],
            "properties": {
                "level": {"type": "string", "enum": ["MA_P", "MA_T"]},
                "attribute": {"type": "string", "description": "MA_P: member attribute code. MA_T: name | tier_start_date | tier_end_date | tier_validity"},
                "tier_class": {"type": "string", "description": "MA_T only: tier class code; empty for MA_P"},
                "op": {"type": "string", "enum": ["==", "!=", ">", ">=", "<", "<=", "in", "not_in", "between", "null", "not_null",
                                                  "contains", "starts_with", "ends_with"]},
                "value": {"type": "string", "description": "single value (numbers as digits, dates YYYY-MM-DD); empty for lists/null"},
                "values": {"type": "array", "items": S, "description": "codes for in/not_in"},
                "value2": {"type": "string", "description": "upper bound for between, else empty"},
                "date_part": {"type": "string", "enum": ["", "DATE", "YEAR", "MONTH", "DAY_OF_MONTH"]}}}
SEG_TOOL = {"name": "build_segment", "description": "The member segment: who the offer is for.",
            "input_schema": {"type": "object", "additionalProperties": False,
                             "required": ["name", "description", "conditions", "or_groups", "assumptions", "blocking_questions", "unsupported"],
                             "properties": {
                                 "name": S, "description": S,
                                 "conditions": {"type": "array", "items": SEG_COND},
                                 "or_groups": {"type": "array", "items": {"type": "array", "items": SEG_COND}},
                                 "assumptions": {"type": "array", "items": S},
                                 "blocking_questions": {"type": "array", "items": S},
                                 "unsupported": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                            "required": ["phrase", "reason"],
                                                                            "properties": {"phrase": S, "reason": S}}}}}}
OPS = {"MA_P": {"NUMBER": ["==", "!=", ">", ">=", "<", "<=", "between", "null", "not_null", "in", "not_in"],
                "DATE": ["==", "!=", ">", ">=", "<", "<=", "between", "null", "not_null"],
                "DATETIME": ["==", "!=", ">", ">=", "<", "<=", "between", "null", "not_null"],
                "TEXT": ["==", "!=", "contains", "starts_with", "ends_with", "null", "not_null", "in", "not_in"],
                "LOOKUP": ["in", "not_in", "==", "!=", "null", "not_null"], "LOV": ["in", "not_in", "==", "!=", "null", "not_null"]},
       "MA_T": {"name": ["==", "!=", "in", "not_in"], "tier_start_date": ["==", "!=", "<", ">", "<=", ">="],
                "tier_end_date": ["==", "!=", "<", ">", "<=", ">="], "tier_validity": ["==", "!=", "<", ">", "<=", ">="]}}


def _is_date(s):
    import datetime as dt
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def check_cond(cat, c, where):
    op = c["op"]
    if c["level"] == "MA_T":
        tiers = cat.tiers.get(c["tier_class"])
        if tiers is None:
            return [f"{where}: tier class '{c['tier_class']}' not in catalog; valid: {sorted(cat.tiers)}"]
        if op not in OPS["MA_T"].get(c["attribute"], []):
            return [f"{where}: MA_T '{c['attribute']}' takes {OPS['MA_T'].get(c['attribute'], 'name|tier_start_date|tier_end_date|tier_validity')}"]
        if c["attribute"] == "name":
            vals = c["values"] if op in ("in", "not_in") else [c["value"]]
            bad = [v for v in vals if v not in tiers]
            return [f"{where}: {bad} are not tiers of {c['tier_class']}; valid: {sorted(tiers)}"] if bad or not vals else []
        if c["attribute"] == "tier_validity":
            return [] if c["value"].lstrip("-").isdigit() else [f"{where}: tier_validity needs a whole number"]
        return [] if _is_date(c["value"]) else [f"{where}: {c['attribute']} needs a date YYYY-MM-DD"]
    a = cat.attr("member", c["attribute"])
    if a is None:
        return [f"{where}: unknown member attribute '{c['attribute']}'"]
    if op not in OPS["MA_P"].get(a.type, []):
        return [f"{where}: '{a.code}' is {a.type}; operators: {OPS['MA_P'].get(a.type)}"]
    if op in ("null", "not_null"):
        return []
    vals = c["values"] if op in ("in", "not_in") else [c["value"]] + ([c["value2"]] if op == "between" else [])
    if not all(vals) or not vals:
        return [f"{where}: '{a.code}' {op} needs a value"]
    if a.type == "NUMBER":
        bad = [v for v in vals if not v.replace(".", "", 1).lstrip("-").isdigit()]
        return [f"{where}: '{a.code}' is NUMBER, got {bad}"] if bad else []
    if a.type in ("DATE", "DATETIME"):
        part = c["date_part"] or "DATE"
        bad = [v for v in vals if not (_is_date(v) if part == "DATE" else v.isdigit())]
        return [f"{where}: '{a.code}' {part} values must be {'YYYY-MM-DD' if part == 'DATE' else 'numbers'}, got {bad}"] if bad else []
    if a.values and op not in ("contains", "starts_with", "ends_with"):
        bad = [v for v in vals if v not in a.values]
        return [f"{where}: {bad} are not {a.code} codes; valid: {sorted(a.values)[:60]}"] if bad else []
    return []


def validate_segment(cat, x):
    errs = [f"{'/'.join(map(str, e.path)) or '(root)'} {e.message}" for e in jsonschema.Draft202012Validator(SEG_TOOL["input_schema"]).iter_errors(x)]
    if errs:
        return errs
    for k, c in enumerate(x["conditions"], 1):
        errs += check_cond(cat, c, f"condition[{k}]")
    for g, grp in enumerate(x["or_groups"], 1):
        if not grp:
            errs.append(f"or_group{g}: empty")
        for k, c in enumerate(grp, 1):
            errs += check_cond(cat, c, f"or_group{g}[{k}]")
    if len(x["or_groups"]) == 1:
        errs.append("or_groups: a single group is not an alternative; put its conditions in conditions")
    return errs


# ---------------------------------------------------------------- XML
class _Ids:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"seg{self.n:05d}"


def _value_block(ids, typ, v):
    btype = "static_date" if typ == "date" else "static_value"
    return f'<block type="{btype}" id="{ids()}"><field name="value">{esc(v)}</field></block>'


def cond_xml(cat, c, ids, nxt=""):
    op = esc(c["op"])
    nxt = f"<next>{nxt}</next>" if nxt else ""
    if c["level"] == "MA_T":
        m = (f'attr_level="MA_T" operators="{op}" iscountertypevector="no" tierslevel="{esc(c["tier_class"])}" '
             f'tiermainoption="{esc(c["attribute"])}"')
        f = (f'<field name="attr_level">MA_T</field><field name="tierslevel">{esc(c["tier_class"])}</field>'
             f'<field name="tiermainoption">{esc(c["attribute"])}</field>')
        rhs = ""
        if c["attribute"] == "name":
            if c["op"] in ("in", "not_in"):
                m += f' value="{esc(",".join(c["values"]))}" visible_value="{esc(", ".join(c["values"]))}"'
                f += f'<field name="operators">{op}</field>'
            else:
                f += f'<field name="operators">{op}</field><field name="tierlevelnamevalue">{esc(c["value"])}</field>'
        elif c["attribute"] == "tier_validity":
            f += f'<field name="operators">{op}</field>'
            rhs = f'<value name="RHS_INPUT">{_value_block(ids, "num", c["value"])}</value>'
        else:
            m += ' date_time_operator="DATE"'
            f += f'<field name="datetime_operator">DATE</field><field name="operators">{op}</field>'
            rhs = f'<value name="RHS_INPUT">{_value_block(ids, "date", c["value"])}</value>'
        return f'<block type="targeting_condition" id="{ids()}"><mutation {m}></mutation>{f}{rhs}{nxt}</block>'
    a = cat.attr("member", c["attribute"])
    m = f'attr_level="MA_P" attribute="{esc(a.code)}" operators="{op}"'
    f = f'<field name="attr_level">MA_P</field><field name="attribute">{esc(a.code)}</field>'
    rhs = ""
    if a.type in ("DATE", "DATETIME"):
        part = c["date_part"] or "DATE"
        m += f' iscountertypevector="no" date_operator="{part}"'
        f += f'<field name="date_operator">{part}</field><field name="operators">{op}</field>'
        if c["op"] not in ("null", "not_null"):
            kind = "date" if part == "DATE" else "num"
            rhs = f'<value name="RHS_INPUT">{_value_block(ids, kind, c["value"])}</value>'
            if c["op"] == "between":
                rhs += f'<value name="RHS_VALUE">{_value_block(ids, kind, c["value2"])}</value>'
    elif a.type == "NUMBER" and c["op"] not in ("in", "not_in"):
        m += ' iscountertypevector="no"'
        f += f'<field name="operators">{op}</field>'
        if c["op"] not in ("null", "not_null"):
            rhs = f'<value name="RHS_INPUT">{_value_block(ids, "num", c["value"])}</value>'
            if c["op"] == "between":
                rhs += f'<value name="RHS_VALUE">{_value_block(ids, "num", c["value2"])}</value>'
    elif c["op"] in ("in", "not_in"):
        labels = [(a.values or {}).get(v, v) for v in c["values"]]
        m += f' value="{esc(",".join(c["values"]))}" visible_value="{esc(", ".join(labels))}" iscountertypevector="no"'
        f += f'<field name="operators">{op}</field>'
    elif c["op"] in ("null", "not_null"):
        m += ' iscountertypevector="no"'
        f += f'<field name="operators">{op}</field>'
    else:  # single value on LOOKUP/LOV/TEXT
        m += f' value="{esc(c["value"])}" visible_value="null" iscountertypevector="no"'
        f += f'<field name="operators">{op}</field><field name="value">{esc(c["value"])}</field>'
    return f'<block type="targeting_condition" id="{ids()}"><mutation {m}></mutation>{f}{rhs}{nxt}</block>'


def chain_xml(cat, conds, ids):
    """AND chain of conditions (each condition's <next> is the following one)."""
    xml = ""
    for c in reversed(conds):
        xml = cond_xml(cat, c, ids, xml)
    return xml


def or_xml(cat, groups, ids, nxt=""):
    left = chain_xml(cat, groups[0], ids)
    right = chain_xml(cat, groups[1], ids) if len(groups) == 2 else or_xml(cat, groups[1:], ids)
    nxt = f"<next>{nxt}</next>" if nxt else ""
    return (f'<block type="or" id="{ids()}"><statement name="OPERAND1">{left}</statement>'
            f'<statement name="OPERAND2">{right}</statement>{nxt}</block>')


def segment_xml(cat, x):
    ids = _Ids()
    body = chain_xml(cat, x["conditions"], ids)
    if x["or_groups"]:
        body = or_xml(cat, x["or_groups"], ids, body) if body else or_xml(cat, x["or_groups"], ids)
    return ('<xml xmlns="https://developers.google.com/blockly/xml"><block type="targeting_holder" id="seg_root" x="18" y="8">'
            f'<statement name="CONDITIONS">{body}</statement></block></xml>')


# ---------------------------------------------------------------- pipeline
def generate_segment(prompt, catalog, llm, max_repairs=1):
    """-> {status: draft|needs_input|unsupported|error, xml?, name, description, assumptions, blocking_questions, unsupported, diagnostics}"""
    t0 = time.time()
    system = [{"type": "text", "text": SEG_SYSTEM},
              {"type": "text", "text": "Tenant catalog:\n" + catalog.prompt_text(), "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": prompt}]
    repairs, x, errs = 0, None, []
    try:
        while True:
            turn = llm.turn(system, messages, tools=[SEG_TOOL])
            calls = [c for c in turn.tool_calls if c[1] == "build_segment"]
            x = calls[0][2] if calls else None
            errs = validate_segment(catalog, x) if x is not None else ["call build_segment exactly once"]
            if not errs or repairs >= max_repairs:
                break
            repairs += 1
            text = "Validation failed:\n" + "\n".join(f"- {e}" for e in errs) + "\nCall build_segment again, corrected."
            messages += [{"role": "assistant", "content": turn.content},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c[0], "is_error": True, "content": text}
                                                      for c in turn.tool_calls] or text}]
    except LLMError as e:
        return {"status": "error", "message": f"The segment generator is unavailable right now ({e}). Please try again.",
                "diagnostics": {"latency_s": round(time.time() - t0, 2)}}
    diag = {"model": getattr(llm, "model", "?"), "latency_s": round(time.time() - t0, 2), "repairs": repairs, "issues_final": errs}
    if x is None or errs:
        return {"status": "unsupported", "name": "", "description": "", "assumptions": [], "blocking_questions": [],
                "unsupported": [{"phrase": "segment", "reason": "could not be validated: " + "; ".join(errs)}], "diagnostics": diag}
    has = bool(x["conditions"] or x["or_groups"])
    status = ("needs_input" if x["blocking_questions"] else "draft") if has else ("needs_input" if x["blocking_questions"] else "unsupported")
    out = {k: x[k] for k in ("name", "description", "assumptions", "blocking_questions", "unsupported")}
    out.update(status=status, conditions=x["conditions"], or_groups=x["or_groups"], diagnostics=diag)
    if has:
        out["xml"] = segment_xml(catalog, x)
    return out
