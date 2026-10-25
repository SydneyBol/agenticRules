"""Validate the model's tool calls: JSON schema, then every code against the tenant catalog.

Errors are fed back to the model in one repair round (pipeline.py); anything still invalid after that is dropped
from the draft and reported as unsupported, so a bad primitive never reaches the compiler.
"""
import datetime as dt
import re
from dataclasses import dataclass

import jsonschema

from .catalog import DATES, LISTY, NUMERIC
from .tools import SCHEMAS

DIM_RANGE = {"DAY_OF_WEEK": (1, 7), "HOUR_OF_DAY": (0, 23), "MONTH": (1, 12), "DAY_OF_MONTH": (1, 31), "YEAR": (1900, 2100)}
LIST_OPS = ("in", "not_in")


@dataclass
class Issue:
    tool_id: str
    tool: str
    message: str

    def __str__(self):
        return f"{self.tool}: {self.message}"


def _num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def check_condition(cat, c, where):
    errs = []
    a = cat.attr(c["object"], c["attribute"])
    if a is None:
        return [f"{where}: unknown {c['object']} attribute '{c['attribute']}' (use a code from the catalog)"]
    if c["object"] == "product" or c["attribute"].startswith("l_"):
        return [f"{where}: product and l_* attributes apply per line item; use add_line_item or add_collect_all instead"]
    dim, op = c["dimension"], c["op"]
    vals = c["values"] if op in LIST_OPS else [c["value"]]
    if op in LIST_OPS and not c["values"]:
        errs.append(f"{where}: op {op} needs a non-empty values list")
    if dim != "NONE":
        if a.type not in DATES:
            errs.append(f"{where}: dimension {dim} only applies to DATE/DATETIME attributes, '{a.code}' is {a.type}")
        else:
            lo, hi = DIM_RANGE[dim]
            bad = [v for v in vals if not (_num(v) and lo <= float(v) <= hi)]
            if bad:
                errs.append(f"{where}: {dim} values must be numbers {lo}-{hi}, got {bad}")
        return errs
    if a.type in NUMERIC:
        if op in LIST_OPS:
            errs.append(f"{where}: '{a.code}' is NUMBER; use a comparison operator, not {op}")
        elif not _num(c["value"]):
            errs.append(f"{where}: '{a.code}' is NUMBER, value '{c['value']}' is not a number")
    elif a.type in DATES:
        if op in LIST_OPS:
            errs.append(f"{where}: '{a.code}' is {a.type}; compare a dimension or a date, not {op}")
        elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", c["value"]):
            errs.append(f"{where}: '{a.code}' is {a.type}; value must be a calendar date YYYY-MM-DD "
                        f"(times of day go through dimension HOUR_OF_DAY), got '{c['value']}'")
    elif a.type in LISTY:
        if op not in LIST_OPS + ("==", "!="):
            errs.append(f"{where}: '{a.code}' is {a.type}; only ==, !=, in, not_in apply")
        known = cat.activity_types if (c["object"], a.code) == ("activity", "h_bit_type") else a.values
        if known:
            unknown = [v for v in vals if v not in known]
            if unknown:
                errs.append(f"{where}: {unknown} are not {a.code} codes; " + closest(cat, c["object"], a.code, known, unknown))
    return errs


def closest(cat, obj, code, known, unknown):
    """Short lists: every code. Long lists: the nearest codes for each wrong one (lookup_values' own scoring)."""
    if len(known) <= 60:
        return f"valid codes: {sorted(known)}"
    near = {u: [x["code"] for x in cat.lookup(obj, code, u, k=5).get("candidates", [])] for u in unknown}
    return f"{len(known)} codes; closest: {near} (or call lookup_values)"


def check_reward(cat, r, where):
    if r["kind"] == "points":
        return [] if r["points"] and r["points"] > 0 else [f"{where}: points reward needs points > 0"]
    if r["privilege_id"] not in cat.privileges:
        return [f"{where}: privilege_id '{r['privilege_id']}' not in catalog; valid: {sorted(cat.privileges)}"]
    return []


def check_measure(cat, m, where):
    a = cat.attr("activity", m)
    if a and m.startswith("l_"):
        return [f"{where}: measure '{m}' is a line-item attribute; use add_line_item for per-item rewards"]
    return [] if a and a.type in NUMERIC else [f"{where}: measure '{m}' must be a NUMBER activity attribute"]


def check_product_values(cat, attr, values, where):
    a = cat.attr("product", attr)
    if a is None:
        return [f"{where}: unknown product attribute '{attr}'"]
    bad = [v for v in values if a.values and v not in a.values]
    return [f"{where}: {bad} are not {attr} codes; " + closest(cat, "product", attr, a.values, bad)] if bad else []


def check_bands(bands, where):
    errs, spans = [], []
    for i, b in enumerate(bands, 1):
        hi = b["upper"] if b["has_upper"] else float("inf")
        if hi < b["lower"] or (hi == b["lower"] and not (b["lower_inclusive"] and b["upper_inclusive"])):
            errs.append(f"{where} band {i}: empty range")
        spans.append((b["lower"], b["lower_inclusive"], hi, b["upper_inclusive"] if b["has_upper"] else False, i))
    spans.sort()
    for (l1, li1, h1, hi1, i), (l2, li2, h2, hi2, j) in zip(spans, spans[1:]):
        if h1 > l2 or (h1 == l2 and hi1 and li2):
            errs.append(f"{where}: bands {i} and {j} overlap; tiers must be exclusive")
    return errs


def _date(s):
    if not s:
        return True
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def check_semantics(cat, name, x):
    w = name
    e = []
    conds = lambda cs, label: [m for k, c in enumerate(cs, 1) for m in check_condition(cat, c, f"{w} {label}[{k}]")]
    if name == "add_earn":
        e += check_measure(cat, x["measure"], w) + conds(x.get("conditions") or [], "condition")
        for g, grp in enumerate(x["or_groups"], 1):
            e += conds(grp, f"or_group{g}")
        if x["factor"] <= 0:
            e.append(f"{w}: factor must be > 0")
    elif name == "add_bonus_if":
        e += conds(x.get("conditions") or [], "condition") + check_reward(cat, x["reward"], w)
        for g, grp in enumerate(x.get("or_groups") or [], 1):
            e += conds(grp, f"or_group{g}")
    elif name == "add_tiers":
        e += check_measure(cat, x["measure"], w) + check_bands(x["bands"], w) + conds(x.get("conditions") or [], "condition")
    elif name == "add_cap":
        e += [] if x["max_points"] > 0 else [f"{w}: max_points must be > 0"]
    elif name == "add_line_item":
        e += check_product_values(cat, x["product_attribute"], x["values"], w)
    elif name == "add_collect_all":
        for g, grp in enumerate(x["groups"], 1):
            e += check_product_values(cat, x["product_attribute"], grp, f"{w} group{g}")
        e += check_reward(cat, x["reward"], w) + ([] if len(x["groups"]) >= 2 else [f"{w}: needs at least 2 groups"])
    elif name in ("add_nth_activity", "add_streak"):
        e += check_reward(cat, x["reward"], w) + ([] if x["n"] >= 1 else [f"{w}: n must be >= 1"])
        e += conds(x.get("conditions") or [], "condition")
    elif name == "add_milestone":
        e += check_measure(cat, x["measure"], w) + check_reward(cat, x["reward"], w) + conds(x.get("conditions") or [], "condition")
    elif name == "add_unique_count":
        a = cat.attr("activity", x["dimension"])
        e += [] if a and a.type in LISTY else [f"{w}: dimension '{x['dimension']}' must be a LOOKUP/LOV/TEXT activity attribute"]
        e += check_reward(cat, x["reward"], w) + conds(x.get("conditions") or [], "condition")
    elif name == "add_unlock":
        e += conds(x["trigger"], "trigger") + conds(x["then_conditions"], "then")
        e += [] if x["trigger"] else [f"{w}: trigger conditions required"]
    elif name == "add_occasion":
        a = cat.attr("member", x["date_attribute"])
        e += [] if a and a.type == "DATE" else [f"{w}: date_attribute '{x['date_attribute']}' must be a member DATE attribute"]
        e += check_reward(cat, x["reward"], w) + ([] if 0 < x["window_days"] <= 180 else [f"{w}: window_days must be 1-180"])
        e += conds(x.get("conditions") or [], "condition")
    elif name == "finish":
        bad = [a for a in x["activity_types"] if a not in cat.activity_types]
        if bad:
            e.append(f"finish: activity_types {bad} not in catalog")
        for k in ("start_date", "end_date"):
            if not _date(x[k]):
                e.append(f"finish: {k} '{x[k]}' is not YYYY-MM-DD")
        if x["start_date"] and x["end_date"] and _date(x["start_date"]) and _date(x["end_date"]) and x["end_date"] < x["start_date"]:
            e.append("finish: end_date is before start_date")
    return e


def validate(cat, blocks):
    """blocks: [(tool_use_id, name, input)] -> [Issue]. lookup_values calls are answered by the pipeline, not validated."""
    issues = []
    blocks = [b for b in blocks if b[1] != "lookup_values"]
    for tid, name, x in blocks:
        if name not in SCHEMAS:
            issues.append(Issue(tid, name, f"unknown tool {name}"))
            continue
        schema_errs = [f"{'/'.join(map(str, er.path)) or '(root)'} {er.message}" for er in jsonschema.Draft202012Validator(SCHEMAS[name]).iter_errors(x)]
        issues += [Issue(tid, name, m) for m in schema_errs]
        if not schema_errs:
            issues += [Issue(tid, name, m) for m in check_semantics(cat, name, x)]
    finishes = [b for b in blocks if b[1] == "finish"]
    if len(finishes) != 1:
        issues.append(Issue("", "finish", f"finish must be called exactly once (called {len(finishes)} times)"))
    caps = [b for b in blocks if b[1] == "add_cap"]
    if caps and not any(b[1] in ("add_tiers", "add_earn", "add_line_item", "add_unlock") or
                        (b[1] == "add_bonus_if" and b[2].get("reward", {}).get("kind") == "points") for b in blocks):
        issues.append(Issue(caps[0][0], "add_cap", "cap needs a points reward to limit (earn, tiers, line_item, unlock or a points bonus_if)"))
    return issues
