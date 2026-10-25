"""Intent primitives -> rule spec (deterministic). The LLM only ever produces intents; all counter scaffolding is here.

Counters live in MA_O (offer level): the engine harness showed MA_A is NOT persisted between BITs.
Every one-shot reward gets an explicit "rewarded" guard (Progressive Spend Bonus blueprint double-pays without one).

Naming: counters and rule tags are read by people (the Counters screen, the rule's IDENTIFIER, engine
offer_actions.rule_id), so they are named like variables in code: what they hold or do, in business words from catalog
labels, never raw attribute codes. Counters: "Total spend", "Visits per store", "<reward> given" for one-shot flags;
unique within the offer, at most 50 ASCII characters, no '.' (bolapi rejects it in an MTO name).
"""
import json
import re

AMOUNT = {"act": {"attr": "h_bit_amount", "type": "NUMBER"}}
BIT_YEAR = {"act": {"attr": "h_bit_date", "type": "DATETIME", "dim": "YEAR"}}


# business words for common measures and dimensions; others use the catalog label
WORDS = {"h_bit_amount": "spend", "l_amount": "item spend", "l_quantity": "items bought", "h_location": "store"}
MTO_MAX = 50


class Ctx:
    """Collects counters declared while expanding (UI pre-creates them via member-tracked-attributes API)."""

    def __init__(self, catalog, guard=None):
        self.catalog = catalog
        self.guard = guard  # counters.Guard: the offer's existing counters, when known
        self.counters = {}
        self.shared = {}  # wanted name -> name used, for counters several rules share (the cap)

    def counter(self, name, data_type="NUMBER", counter_type="REGULAR", namespace=None, shared=False):
        """A new counter; a second primitive asking for the same name gets "<name> 2". shared=True reuses one (the cap).
        A name the offer already has is reused only when the guard allows it (exact match, planning offer)."""
        base = mto_name(name)
        if shared and base in self.shared:
            return self.shared[base]
        g = self.guard
        name, i = base, 2
        while name in self.counters or (g and g.taken(name, data_type, counter_type, namespace)):
            name = f"{base[:MTO_MAX - len(str(i)) - 1].rstrip()} {i}"
            i += 1
        if g and name != base and base in g.existing:
            g.renamed.append({"wanted": base, "used": name, "why": g.why(base, data_type, counter_type, namespace)})
        if shared:
            self.shared[base] = name
        self.counters[name] = {"name": name, "level": "MA_O", "data_type": data_type,
                               "counter_type": counter_type, "vector_namespace": namespace, "target_type": "NO TARGET"}
        return name

    def word(self, code):
        """What an attribute code means to a manager: "spend", "store", "Date of Birth"."""
        return WORDS.get(code) or self.catalog.get("labels", {}).get(code) or re.sub(r"^[hl]_", "", code).replace("_", " ")

    def reward(self, reward):
        if "points" in reward:
            return f"{n(reward['points'])} points"
        p = self.catalog["privileges"].get(str(reward["privilege"]), {})
        return p.get("name") or f"privilege {reward['privilege']}"


def mto_name(s):
    """Counter names: ASCII letters, digits, spaces and '-' only (no '.'), at most 50 characters."""
    s = re.sub(r"(\d)\.(\d)", r"\1pt\2", s)
    s = " ".join(re.sub(r"[^A-Za-z0-9 -]+", " ", s).split())
    return (s[:1].upper() + s[1:])[:MTO_MAX].rstrip()


def n(v):
    """1.0 -> "1", 1.5 -> "1.5"."""
    return str(int(v)) if float(v).is_integer() else str(v)


def ordinal(k):
    return f"{k}{'th' if 10 <= k % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(k % 10, 'th')}"


def values_text(values):
    """[[code, label], ...] -> "Electronics or Toys"."""
    return " or ".join(v[1] if len(v) > 1 else v[0] for v in values)


def band_text(ctx, measure, band):
    """{"ge": 100, "lt": 500} -> "spend from 100 under 500"."""
    words = {"gt": "over", "ge": "from", "lt": "under", "le": "up to"}
    return " ".join([ctx.word(measure)] + [f"{w} {n(band[k])}" for k, w in words.items() if k in band])


def earn_text(ctx, measure, p):
    if "multiplier" in p:
        return f"{n(p['multiplier'])}x {ctx.word(measure)}"
    if "rate" in p or "rate_per_unit" in p:
        return f"{n(p.get('rate', p.get('rate_per_unit')))} points per {ctx.word(measure)}"
    return f"{n(p['points'])} points"


# ---------------------------------------------------------------- helpers
def num(v):
    return {"num": v}


def mto_num(name, op, rhs, namespace=None):
    c = {"obj": "member", "level": "MA_O", "attr": name, "type": "NUMBER", "op": op, "rhs": rhs}
    if namespace:
        c["counter"] = {"vector_namespace": namespace, "target_type": "NO TARGET"}
    return c


def mto_flag(name, op, value="Yes"):
    return {"obj": "member", "level": "MA_O", "attr": name, "type": "TEXT", "op": op, "value": value}


def set_flag(name, value="Yes"):
    return {"type": "update_attribute", "level": "MA_O", "attr": name, "attr_type": "TEXT", "op": "SET", "value": {"str": value}}


def bump(name, by, op="INCREMENT_BY", namespace=None):
    a = {"type": "update_attribute", "level": "MA_O", "attr": name, "attr_type": "NUMBER", "op": op, "value": by}
    if namespace:
        a["vector_namespace"] = namespace
    return a


def reward_actions(ctx, reward, amount_expr=None):
    cat = ctx.catalog
    if "points" in reward:
        return [{"type": "give_points", "account": cat["account"], "tag": cat["tag"], "formula": num(reward["points"])}]
    if "privilege" in reward:
        p = cat["privileges"][str(reward["privilege"])]
        return [{"type": "give_privilege", "product": p["id"], "usage_policy": p["usage_policy"], "quantity": num(1)}]
    raise ValueError(reward)


def points(ctx, formula):
    return {"type": "give_points", "account": ctx.catalog["account"], "tag": ctx.catalog["tag"], "formula": formula}


def cmp(attr, op, v, typ="NUMBER"):
    return {"obj": "activity", "attr": attr, "type": typ, "op": op, "rhs": num(v)}


def band_conds(measure, band):
    ops = {"gt": ">", "ge": ">=", "lt": "<", "le": "<="}
    return [cmp(measure, ops[k], band[k]) for k in ("gt", "ge", "lt", "le") if k in band]


def band_formula(measure, band):
    m = {"act": {"attr": measure, "type": "NUMBER"}}
    if "multiplier" in band:
        return {"arith": ["*", m, num(band["multiplier"])]}
    if "rate" in band:
        return {"arith": ["*", m, num(band["rate"])]}
    if "points" in band:
        return num(band["points"])
    raise ValueError(band)


# ---------------------------------------------------------------- primitives
def p_earn(ctx, p, cap=None):
    measure = p.get("measure", "h_bit_amount")
    if "rate_per_unit" in p:
        formula = {"arith": ["*", {"act": {"attr": measure, "type": "NUMBER"}}, num(p["rate_per_unit"])]}
    else:
        formula = band_formula(measure, p)
    conds, tag = list(p.get("conditions", [])), p.get("tag", f"Earn {earn_text(ctx, measure, p)}")
    if cap:
        return capped(ctx, tag, conds, formula, cap)
    return [{"tag": tag, "conditions": conds, "actions": [points(ctx, formula)]}]


def p_bonus_if(ctx, p, cap=None):
    tag = p.get("tag", f"Bonus {ctx.reward(p['reward'])}")
    if cap and "points" in p["reward"]:
        return capped(ctx, tag, list(p["conditions"]), num(p["reward"]["points"]), cap)
    return [{"tag": tag, "conditions": p["conditions"], "actions": reward_actions(ctx, p["reward"])}]


def capped(ctx, tag, conds, formula, cap, scope=None):
    """Lifetime cap: partial-fill rule FIRST, then full rule (rules run in order inside one Drools rule;
    putting the full rule first lets the partial rule fire again on the updated counter)."""
    total = ctx.counter(cap["counter"], shared=True)
    cap_v = num(cap["max_points"])
    tot = {"mem": {"level": "MA_O", "attr": total, "type": "NUMBER"}}
    rules = [{"tag": f"{tag} up to cap",
              "conditions": conds + [mto_num(total, ">", {"arith": ["-", cap_v, formula]}), mto_num(total, "<", cap_v)],
              "actions": [points(ctx, {"arith": ["-", cap_v, tot]}), bump(total, cap_v, op="SET")]},
             {"tag": tag,
              "conditions": conds + [mto_num(total, "<=", {"arith": ["-", cap_v, formula]})],
              "actions": [points(ctx, formula), bump(total, formula)]}]
    if scope:
        for r in rules:
            r["scope"] = scope
    return rules


def p_tiers(ctx, p, cap=None):
    measure = p.get("measure", "h_bit_amount")
    rules = []
    for i, band in enumerate(p["bands"], 1):
        conds, formula = list(p.get("conditions", [])) + band_conds(measure, band), band_formula(measure, band)
        tag = f"Tier {i} {band_text(ctx, measure, band)}, {earn_text(ctx, measure, band)}"
        if cap:
            rules += capped(ctx, tag, conds, formula, cap)
        else:
            rules.append({"tag": tag, "conditions": conds, "actions": [points(ctx, formula)]})
    return rules


def p_line_item(ctx, p, cap=None):
    f = p["filter"]
    cond = {"obj": "products_attrs", "attr": f["attr"], "type": f.get("type", "LOOKUP"), "op": "in", "values": f["values"]}
    if "points_per_unit" in p:
        formula = {"arith": ["*", {"act": {"attr": "l_quantity", "type": "NUMBER"}}, num(p["points_per_unit"])]}
    else:
        formula = {"arith": ["*", {"act": {"attr": p.get("measure", "l_amount"), "type": "NUMBER"}}, num(p["multiplier"])]}
    tag = p.get("tag", f"Bonus on {values_text(f['values'])}")
    if cap:
        return capped(ctx, tag, [cond], formula, cap, scope="line")
    return [{"tag": tag, "scope": "line", "conditions": [cond], "actions": [points(ctx, formula)]}]


def p_nth_activity(ctx, p):
    what = ctx.reward(p["reward"])
    c = ctx.counter(f"Visits toward {what}")
    q = list(p.get("conditions", []))  # which activities count; repeated on the reward rule so only a counting one pays
    rules = [{"tag": "Count qualifying visit", "conditions": q, "actions": [bump(c, num(1))]}]
    acts = reward_actions(ctx, p["reward"])
    if p.get("repeat"):
        acts.append(bump(c, num(0), op="SET"))
        rules.append({"tag": f"Every {ordinal(p['n'])} visit gets {what}", "conditions": q + [mto_num(c, ">=", num(p["n"]))],
                      "actions": acts})
    else:
        done = ctx.counter(f"{what} given", "TEXT")
        rules.append({"tag": f"{ordinal(p['n'])} visit gets {what}", "conditions": q + [mto_num(c, "==", num(p["n"])), mto_flag(done, "!=")],
                      "actions": acts + [set_flag(done)]})
    return rules


def p_milestone(ctx, p):
    m, what = p.get("measure", "h_bit_amount"), ctx.reward(p["reward"])
    total = ctx.counter(f"Total {ctx.word(m)}")
    done = ctx.counter(f"{what} given", "TEXT")
    measure = {"act": {"attr": m, "type": "NUMBER"}}
    q = list(p.get("conditions", []))
    return [{"tag": f"Add to total {ctx.word(m)}", "conditions": q, "actions": [bump(total, measure)]},
            {"tag": f"Total {ctx.word(m)} reaches {n(p['threshold'])}", "conditions": q + [mto_num(total, ">=", num(p["threshold"])), mto_flag(done, "!=")],
             "actions": reward_actions(ctx, p["reward"]) + [set_flag(done)]}]


def p_unique_count(ctx, p):
    dim = p["dimension"]
    word = ctx.word(dim)
    per = ctx.counter(f"Visits per {word}", counter_type="VECTOR", namespace=dim)
    uniq = ctx.counter(f"Different {word}s visited")
    done = ctx.counter(f"{ctx.reward(p['reward'])} given", "TEXT")
    q = list(p.get("conditions", []))
    return [{"tag": f"Count visits per {word}", "conditions": q, "actions": [bump(per, num(1), namespace=dim)]},
            {"tag": f"First visit to a {word}", "conditions": q + [mto_num(per, "==", num(1), namespace=dim)], "actions": [bump(uniq, num(1))]},
            {"tag": f"{n(p['n'])} different {word}s visited", "conditions": q + [mto_num(uniq, ">=", num(p["n"])), mto_flag(done, "!=")],
             "actions": reward_actions(ctx, p["reward"]) + [set_flag(done)]}]


def p_collect_all(ctx, p):
    rules, flags = [], []
    for i, item in enumerate(p["items"], 1):
        flag = ctx.counter(f"{values_text(item['values'])} collected", "TEXT")
        flags.append(flag)
        cond = {"obj": "products_attrs", "attr": item["attr"], "type": item.get("type", "LOOKUP"), "op": "in", "values": item["values"]}
        rules.append({"tag": f"Collect {values_text(item['values'])}", "scope": "line", "conditions": [cond], "actions": [set_flag(flag)]})
    done = ctx.counter(f"{ctx.reward(p['reward'])} given", "TEXT")
    rules.append({"tag": "All items collected", "conditions": [mto_flag(f, "==") for f in flags] + [mto_flag(done, "!=")],
                  "actions": reward_actions(ctx, p["reward"]) + [set_flag(done)]})
    return rules


def p_unlock(ctx, p, cap=None):
    then = p["then"]
    what = earn_text(ctx, then.get("measure", "h_bit_amount"), then)
    flag = ctx.counter(f"{what} unlocked", "TEXT")
    formula = band_formula(then.get("measure", "h_bit_amount"), then)
    unlocked = then.get("conditions", []) + [mto_flag(flag, "==")]
    tag = f"Earn {what} once unlocked"
    earn = capped(ctx, tag, unlocked, formula, cap) if cap else \
        [{"tag": tag, "conditions": unlocked, "actions": [points(ctx, formula)]}]
    return [{"tag": f"Unlock {what}", "conditions": p["trigger"], "actions": [set_flag(flag)]}] + earn


def p_occasion(ctx, p):
    """Recurring date window, once per occurrence. CALENDAR_DATE compares 'MM-DD' text (year stripped), so
    year-wrap needs separate rules; the counter stores the occurrence year (YEAR, YEAR-1 or YEAR+1)."""
    attr, days = p["date_attr"], p["window_days"]
    year_ctr = ctx.counter(f"{ctx.word(attr)} reward year")
    dob = {"mem": {"level": "MA_P", "attr": attr, "type": "DATE"}}
    dob_cal = {"mem": {"level": "MA_P", "attr": attr, "type": "DATE", "dim": "CALENDAR_DATE"}}
    lo = {"dateop": {"op": "-", "lhs": dob_cal, "n": days, "unit": "DAYS"}}
    hi = {"dateop": {"op": "+", "lhs": dob_cal, "n": days, "unit": "DAYS"}}
    t = lambda op, rhs: {"obj": "activity", "attr": "h_bit_date", "type": "DATETIME", "dim": "CALENDAR_DATE", "op": op, "rhs": rhs}
    d = lambda op, rhs: {"obj": "member", "level": "MA_P", "attr": attr, "type": "DATE", "dim": "CALENDAR_DATE", "op": op, "rhs": rhs}
    not_null = {"obj": "member", "level": "MA_P", "attr": attr, "type": "DATE", "op": "not_null"}
    # Window fits inside one calendar year when date is in [01-01 + days, 12-31 - days].
    first_safe, last_safe = _md_plus(1, 1, days), _md_plus(12, 31, -days)
    occ = lambda delta: BIT_YEAR if delta == 0 else {"arith": ["+" if delta > 0 else "-", BIT_YEAR, num(abs(delta))]}
    reward = reward_actions(ctx, p["reward"])

    def r(tag, conds, delta):
        return {"tag": f"{ctx.word(attr)} {tag}", "conditions": [not_null] + list(p.get("conditions", [])) + conds + [mto_num(year_ctr, "!=", occ(delta))],
                "actions": reward + [bump(year_ctr, occ(delta), op="SET")]}

    return [
        r("window inside year", [d(">=", {"date": f"2000-{first_safe}"}), d("<=", {"date": f"2000-{last_safe}"}),
                                 t(">=", lo), t("<=", hi)], 0),
        r("late-year date, same year", [d(">", {"date": f"2000-{last_safe}"}), t(">=", lo)], 0),
        r("late-year date, next January", [d(">", {"date": f"2000-{last_safe}"}), t("<=", hi)], -1),
        r("early-year date, previous December", [d("<", {"date": f"2000-{first_safe}"}), t(">=", lo)], +1),
        r("early-year date, same year", [d("<", {"date": f"2000-{first_safe}"}), t("<=", hi)], 0),
    ]


def _md_plus(month, day, delta):
    import datetime as dt
    d = dt.date(2001, month, day) + dt.timedelta(days=delta)  # non-leap reference year
    return f"{d.month:02d}-{d.day:02d}"


CAPPABLE = {"earn": p_earn, "tiers": p_tiers, "bonus_if": p_bonus_if, "line_item": p_line_item, "unlock": p_unlock}
PRIMITIVES = {"earn": p_earn, "bonus_if": p_bonus_if, "line_item": p_line_item, "nth_activity": p_nth_activity,
              "milestone": p_milestone, "unique_count": p_unique_count, "collect_all": p_collect_all,
              "unlock": p_unlock, "occasion": p_occasion}


def expand(intent, catalog, guard=None):
    """intent: {activity_filters:[cond], primitives:[{name: params}], cap?:{...}} -> (rule_spec, counters).
    guard: counters.Guard with the offer's existing counters (records reuses and renames)."""
    ctx = Ctx(catalog, guard)
    rules = []
    for prim in intent["primitives"]:
        (name, params), = prim.items()
        if name in CAPPABLE:
            rules += CAPPABLE[name](ctx, params, cap=intent.get("cap"))
        else:
            rules += PRIMITIVES[name](ctx, params)
    seen = {}
    for r in rules:  # two primitives with the same tag (two earn rules): number them so each rule_id is distinct
        r["tag"] = r["tag"][:1].upper() + r["tag"][1:]
        seen[r["tag"]] = seen.get(r["tag"], 0) + 1
        if seen[r["tag"]] > 1:
            r["tag"] = f"{r['tag']} {seen[r['tag']]}"
    spec = {"activity_filters": list(intent.get("activity_filters", [])), "rules": rules}
    hoist_filters(spec)
    return spec, list(ctx.counters.values())


def _filterable(c):
    """Conditions the stock Filter block (activity_criteria) builds with the same fields as a rule condition:
    activity attributes and member profile attributes. Not OR groups, counters or product/line attributes."""
    return "any" not in c and not c.get("counter") and (c["obj"] == "activity" or (c["obj"] == "member" and c.get("level") == "MA_P"))


def _codes(c):
    """The value codes an equality / membership condition accepts, else None."""
    if c.get("dim") or c.get("rhs") is not None:
        return None
    if c["op"] == "in":
        return {v[0] for v in c["values"]}
    if c["op"] == "==" and c.get("value") is not None:
        return {c["value"]}
    return None


def _implies(f, c):
    """Filter f lets through only values condition c accepts, so c is always true under f."""
    if "any" in c or "any" in f or (f["obj"], f["attr"], f.get("level")) != (c["obj"], c["attr"], c.get("level")):
        return False
    fs, cs = _codes(f), _codes(c)
    return fs is not None and cs is not None and fs <= cs


def hoist_filters(spec):
    """Coding standard: a condition every On Activity rule needs is global, so it goes in the activity's Filter once
    instead of being repeated in each rule. The Filter is AND-ed into every rule under it, so the DRL means the same."""
    rules = spec["rules"]
    if not rules or any(r.get("scope", "header") not in ("header", "line") for r in rules):
        return
    key = lambda c: json.dumps(c, sort_keys=True)
    for r in rules:  # a rule condition the Filter already implies (Filter "type in Spend", rule "type == Spend") goes
        r["conditions"] = [c for c in r.get("conditions", []) if not any(_implies(f, c) for f in spec["activity_filters"])]
    common = [c for c in rules[0].get("conditions", []) if _filterable(c)
              and all(key(c) in {key(x) for x in r.get("conditions", [])} for r in rules[1:])]
    have = {key(c) for c in spec["activity_filters"]}
    for c in common:
        k = key(c)
        for r in rules:
            r["conditions"] = [x for x in r["conditions"] if key(x) != k]
        f = as_filter(c)
        if key(f) not in have and not any(_implies(x, f) for x in spec["activity_filters"]):
            spec["activity_filters"].append(f)
            have.add(key(f))


def as_filter(c):
    """The Filter block rebuilds a one-value == / != on a LOOKUP or LOV as a dropdown from its own lookup list and drops
    a value it can't find ("Filter Block --> tier_class is empty"); its in / not in form keeps codes and labels as
    written, so a one-value list is used, which means the same."""
    if c["type"] in ("LOOKUP", "LOV") and c["op"] in ("==", "!=") and c.get("value") is not None and not c.get("dim"):
        f = {k: v for k, v in c.items() if k not in ("value", "label")}
        f.update(op="in" if c["op"] == "==" else "not_in", values=[[c["value"], c.get("label", c["value"])]])
        return f
    return c
