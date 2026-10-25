"""Validated tool calls -> expander intent. Attribute types and value labels come from the tenant catalog."""

NO_EXPANDER = {"add_streak": "consecutive-period streaks need an event (scheduled) section, which the prototype does not compile yet"}


class Unsupported(Exception):
    pass


def cond(cat, c):
    a = cat.attr(c["object"], c["attribute"])
    if a is None:
        raise Unsupported(f"unknown attribute {c['object']}.{c['attribute']}")
    obj = {"activity": "activity", "member": "member", "product": "products_attrs"}[c["object"]]
    out = {"obj": obj, "attr": a.code, "type": a.type, "op": c["op"]}
    if obj == "member":
        out["level"] = "MA_P"
    if c["dimension"] != "NONE":
        out["dim"] = c["dimension"]
    if c["op"] in ("in", "not_in"):
        out["values"] = [[v, cat.label(c["object"], a.code, v)] for v in c["values"]]
    elif a.type in ("DATETIME", "DATE") and "dim" not in out:
        # a calendar date (validated YYYY-MM-DD): static_date, compared on the DATE part of a date-time attribute
        out["rhs"] = {"date": c["value"]}
        if a.type == "DATETIME":
            out["dim"] = "DATE"
    elif a.type in ("NUMBER", "DATETIME", "DATE"):
        out["rhs"] = {"num": float(c["value"]) if "." in c["value"] else int(c["value"])}
    else:
        out["value"] = c["value"]
        if a.type in ("LOOKUP", "LOV"):
            out["label"] = cat.label(c["object"], a.code, c["value"])  # kept for the Filter's list form
    return out


def with_or(cat, x):
    """conditions AND (any of or_groups): one group is plain AND, several become one OR condition."""
    conds = [cond(cat, c) for c in x["conditions"]]
    groups = [[cond(cat, c) for c in g] for g in x.get("or_groups") or []]
    if len(groups) == 1:
        conds += groups[0]
    elif groups:
        conds.append({"any": groups})
    return conds


def conds(cat, x):
    return [cond(cat, c) for c in x.get("conditions") or []]


def spend_type(cat):
    """The activity type labelled exactly 'Spend' (code SPEND first): the default when the manager names none."""
    if "SPEND" in cat.activity_types:
        return "SPEND"
    return next((k for k, v in cat.activity_types.items() if v.strip().lower() == "spend"), None)


def reward(r):
    return {"points": r["points"]} if r["kind"] == "points" else {"privilege": r["privilege_id"]}


def band(b):
    out = {("ge" if b["lower_inclusive"] else "gt"): b["lower"]}
    if b["has_upper"]:
        out["le" if b["upper_inclusive"] else "lt"] = b["upper"]
    out["multiplier" if b["factor_kind"] == "multiplier" else "rate"] = b["factor"]
    return out


def to_intent(cat, calls, finish):
    """calls: [(name, input)] without finish -> (intent, unsupported[])"""
    intent, unsupported = {"primitives": [], "notes": []}, []
    acts = finish.get("activity_types") or []
    if not acts and spend_type(cat):
        acts = [spend_type(cat)]
        intent["notes"].append(f"No activity type was named, so only {cat.activity_types[acts[0]]} activities count.")
    if acts:
        intent["activity_filters"] = [{"obj": "activity", "attr": "h_bit_type", "type": cat.attr("activity", "h_bit_type").type,
                                       "op": "in", "values": [[a, cat.activity_types.get(a, a)] for a in acts]}]
    product_vals = lambda attr, vals: [[v, cat.label("product", attr, v)] for v in vals]
    for name, x in calls:
        if name in NO_EXPANDER:
            unsupported.append({"phrase": name[4:], "reason": NO_EXPANDER[name], "alternative": "use nth_activity or milestone within the offer period"})
            continue
        P = intent["primitives"].append
        if name == "add_earn":
            e = {"measure": x["measure"], "conditions": with_or(cat, x)}
            e["multiplier" if x["factor_kind"] == "multiplier" else "rate_per_unit"] = x["factor"]
            P({"earn": e})
        elif name == "add_bonus_if":
            P({"bonus_if": {"conditions": with_or(cat, x), "reward": reward(x["reward"])}})
        elif name == "add_tiers":
            P({"tiers": {"measure": x["measure"], "bands": [band(b) for b in x["bands"]], "conditions": conds(cat, x)}})
        elif name == "add_cap":
            intent["cap"] = {"counter": f"Points toward {x['max_points']} cap", "max_points": x["max_points"]}
        elif name == "add_line_item":
            li = {"filter": {"attr": x["product_attribute"], "type": cat.attr("product", x["product_attribute"]).type,
                             "values": product_vals(x["product_attribute"], x["values"])}}
            li["points_per_unit" if x["factor_kind"] == "points_per_unit" else "multiplier"] = x["factor"]
            P({"line_item": li})
        elif name == "add_collect_all":
            t = cat.attr("product", x["product_attribute"]).type
            P({"collect_all": {"items": [{"attr": x["product_attribute"], "type": t, "values": product_vals(x["product_attribute"], g)}
                                         for g in x["groups"]], "reward": reward(x["reward"])}})
        elif name == "add_nth_activity":
            P({"nth_activity": {"n": x["n"], "repeat": x["repeat"], "conditions": conds(cat, x), "reward": reward(x["reward"])}})
        elif name == "add_milestone":
            P({"milestone": {"measure": x["measure"], "threshold": x["threshold"], "conditions": conds(cat, x),
                             "reward": reward(x["reward"])}})
        elif name == "add_unique_count":
            P({"unique_count": {"dimension": x["dimension"], "n": x["n"], "conditions": conds(cat, x), "reward": reward(x["reward"])}})
        elif name == "add_unlock":
            P({"unlock": {"trigger": [cond(cat, c) for c in x["trigger"]],
                          "then": {"conditions": [cond(cat, c) for c in x["then_conditions"]], "multiplier": x["multiplier"]}}})
        elif name == "add_occasion":
            P({"occasion": {"date_attr": x["date_attribute"], "window_days": x["window_days"], "conditions": conds(cat, x),
                            "reward": reward(x["reward"])}})
        else:
            raise Unsupported(f"no adapter for {name}")
    return intent, unsupported
