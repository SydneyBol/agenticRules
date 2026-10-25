"""Rule spec -> Blockly XML, in the exact shape gravty-ui itself saves.

Shapes are taken from the seeded blueprints and saved offers (see ../corpus, ../corpus_local):
<mutation> first (attribute order mirrors each block's mutationToDom), then <field>s in dependency order,
then <value>/<statement> children. The UI reloads this via appendFromXml and re-serialises it.
"""
from itertools import count
from xml.sax.saxutils import escape, quoteattr

XHTML = "http://www.w3.org/1999/xhtml"
VECTOR_LADDER_PLACEHOLDER = "DUMMY VALUE"  # what gravty-ui itself serialises for vector counters without a target
_ids = count(1)


def _id():
    return f"ai{next(_ids):05d}"


def reset_ids():
    global _ids
    _ids = count(1)


def mutation(attrs):
    """attrs: list of (name, value) in mutationToDom order; None values are skipped."""
    parts = [f"xmlns={quoteattr(XHTML)}"] + [f"{k}={quoteattr(str(v))}" for k, v in attrs if v is not None]
    return f"<mutation {' '.join(parts)}></mutation>"


def field(name, value):
    return f'<field name="{name}">{escape(str(value))}</field>'


def value_input(name, block_xml):
    return f'<value name="{name}">{block_xml}</value>'


def statement(name, blocks):
    chained = chain(blocks)
    return f'<statement name="{name}">{chained}</statement>' if chained else ""


def chain(blocks):
    """Link a list of block xml strings via <next>, innermost last."""
    out = ""
    for b in reversed(blocks):
        if out:
            assert b.endswith("</block>")
            b = b[: -len("</block>")] + f"<next>{out}</next></block>"
        out = b
    return out


def block(btype, *parts, x=None, y=None):
    pos = f' x="{x}" y="{y}"' if x is not None else ""
    return f'<block type="{btype}" id="{_id()}"{pos}>{"".join(p for p in parts if p)}</block>'


# ---------------------------------------------------------------- value expressions
def expr(e):
    """{num} {str} {date} {act:{attr,type,dim?}} {mem:{level,attr,type,dim?}} {arith:[op,l,r]} {dateop:{op,lhs,n,unit}}"""
    if "num" in e:
        return block("static_value", field("value", e["num"]))
    if "str" in e:
        return block("static_string_value", field("value", e["str"]))
    if "date" in e:
        return block("static_date", field("value", e["date"]))
    if "act" in e:
        a = e["act"]
        dim = a.get("dim")
        dim_field = "datetime_operator" if a["type"] == "DATETIME" else "date_operator"
        return block("activity_attribute",
                     mutation([("operators", "null"), ("payoperators", "null"), ("attribute", a["attr"]), ("type", a["type"])]),
                     field("ATTRIBUTE", a["attr"]),
                     field(dim_field, dim) if dim else "")
    if "mem" in e:
        m = e["mem"]
        dim = m.get("dim")
        return block("member_attribute",
                     mutation([("attribute_type_input", m["level"]), ("attribute", m["attr"]),
                               ("iscountertypevector", "no"), ("attr_type", m["type"])]),
                     field("ATTRIBUTE_TYPE", m["level"]), field("ATTRIBUTE", m["attr"]),
                     field("date_operator", dim) if dim else "")
    if "arith" in e:
        op, lhs, rhs = e["arith"]
        return block("arithmetic_generic", field("operator", op), value_input("LHS", expr(lhs)), value_input("RHS", expr(rhs)))
    if "dateop" in e:
        d = e["dateop"]
        return block("arithmetic_date", mutation([("opt", d["op"])]),
                     value_input("LHS", expr(d["lhs"])), field("operator", d["op"]),
                     field("number", d["n"]), field("date_quantity", d.get("unit", "DAYS")))
    raise ValueError(f"unknown expr {e}")


# ---------------------------------------------------------------- conditions
LIST_OPS = ("in", "not_in")


def _cond_core(c, kind):
    """Shared by activity_criteria (kind='filter') and if_generic_adaptive (kind='if')."""
    obj, attr, typ, op = c["obj"], c["attr"], c["type"], c["op"]
    level = c.get("level")
    dim = c.get("dim")
    dim_key = "datetime_operator" if typ == "DATETIME" else "date_operator"
    is_list = op in LIST_OPS
    values = c.get("values")  # [[code, label], ...] for list ops
    scalar = c.get("value")  # literal for LOOKUP/LOV/TEXT == or MA_O text flags
    rhs = c.get("rhs")  # expr for NUMBER/DATE comparisons
    val_attr = ",".join(v[0] for v in values) if is_list else scalar
    vis_attr = ",".join(v[1] for v in values) if is_list else None

    attrs = [("object", obj)]
    if level:
        attrs.append(("attribute_level", level))
    attrs += [("attribute", attr), ("operators", op), ("payoperators", "null")]
    counter = c.get("counter")  # {vector_namespace?, target_type, entity_map_level?, ladder?}
    if kind == "if" and is_list:
        attrs += [("visible_value", vis_attr), ("iscountertypevector", "no"), ("value", val_attr)]
    else:
        attrs.append(("iscountertypevector", "yes" if counter and counter.get("vector_namespace") else "no"))
        if counter and counter.get("vector_namespace"):
            attrs += [("vector_counter_namespace", counter["vector_namespace"]),
                      ("attribute_entity_map_level", counter.get("entity_map_level", "Activity"))]
        if dim:
            attrs.append((dim_key, dim))
        if val_attr is not None:
            attrs.append(("value", val_attr))
            if kind == "filter" and is_list:
                attrs.append(("visible_value", vis_attr))
    if obj == "products_attrs":
        attrs.append(("prod_offer_select_val", "products_attrs"))
    attrs.append(("type", typ))
    # UI (if-generic.block.ts updateShapeByAttribute_) only builds the hidden target_type_value field for
    # NUMBER MTOs; TEXT MTO conditions never serialise target_type (corpus agrees), so don't emit it.
    if level in ("MA_O", "MA_TO") and typ != "TEXT":
        attrs.append(("target_type", (counter or {}).get("target_type", "NO TARGET")))
        if (counter or {}).get("target_type") in ("SINGLE", "LADDER"):
            attrs.append(("mto_ladder_value", counter.get("ladder", "")))
        elif counter and counter.get("vector_namespace"):
            # the UI writes this placeholder for vector counters, and the upload path's counter discovery regexes
            # (rules-upload-model.component.ts attribute_type_input19/21) only match when it is present
            attrs.append(("mto_ladder_value", VECTOR_LADDER_PLACEHOLDER))

    fields = [field("OBJECT", obj)]
    if obj == "products_attrs":
        fields.append(field("ProductOrOfferContext", "products_attrs"))
    if level:
        fields.append(field("attribute_level", level))
    fields.append(field("attribute", attr))
    if counter and counter.get("vector_namespace"):
        fields += [field("vector_counter_name", counter.get("vector_name", counter["vector_namespace"])),
                   field("vector_counter_namespace", counter["vector_namespace"])]
    if dim:
        fields.append(field(dim_key, dim))
    fields.append(field("operators", op))
    if val_attr is not None and not is_list:
        fields.append(field("value", val_attr))
    children = value_input("RHS_INPUT", expr(rhs)) if rhs is not None else ""
    return mutation(attrs), "".join(fields), children


def activity_filter(c):
    m, f, ch = _cond_core(c, "filter")
    return block("activity_criteria", m, f, ch)


def condition(c):
    if "any" in c:  # OR of two AND-groups (nest for more)
        groups = c["any"]
        left = [condition(x) for x in groups[0]]
        right = groups[1:]
        right_xml = [condition(x) for x in right[0]] if len(right) == 1 else [condition({"any": right})]
        return block("or", statement("OPERAND1", left), statement("OPERAND2", right_xml))
    m, f, ch = _cond_core(c, "if")
    return block("if_generic_adaptive", m, f, ch)


# ---------------------------------------------------------------- actions
def action(a):
    t = a["type"]
    if t == "give_points":
        m = mutation([("iscountertypevector", "no"), ("loyaltyaccountid", a["account"]), ("attribute_type_input", "give_points")])
        fs = field("action_type", "give_points") + field("LOYALTY_ACCOUNT", a["account"]) + field("REWARD_TAGS", a["tag"])
        if a.get("pending_days") is not None:
            fs += field("PENDING_DAYS_VALUE", a["pending_days"])
        return block("action", m, fs, value_input("FORMULA", expr(a["formula"])))
    if t == "give_privilege":
        m = mutation([("iscountertypevector", "no"), ("attribute_type_input", "give_privilege")])
        fs = (field("action_type", "give_privilege") + field("SELECTED_PRIVILEGE", a["product"]) +
              field("PRIVILEGE_USAGEPOLICY", a["usage_policy"]) + field("previlege_Validity", a.get("validity", "product_validity")))
        vals = value_input("PRIVILEGE_QUANTITY", expr(a.get("quantity", {"num": 1})))
        if a.get("value") is not None:
            vals = value_input("PRIVILEGE", expr(a["value"])) + vals
        return block("action", m, fs, vals)
    if t == "update_attribute":
        typ = a.get("attr_type", "NUMBER")
        ns = a.get("vector_namespace")
        attrs = [("iscountertypevector", "yes" if ns else "no")]
        if ns:
            attrs.append(("vector_counter_namespace", ns))
        attrs += [("attribute_type_input", "update_attribute"), ("mta_type_value", a["level"]),
                  ("mta_attribute", a["attr"]), ("type", typ)]
        numeric = typ == "NUMBER"
        if numeric:
            if ns:
                attrs.append(("attribute_entity_map_level", "Activity"))
            if a["level"] in ("MA_O", "MA_TO"):
                attrs.append(("target_type", a.get("target_type", "NO TARGET")))
                if a.get("target_type") in ("SINGLE", "LADDER"):
                    attrs.append(("mto_ladder_value", a.get("ladder", "")))
                elif ns:  # same placeholder as above (discovery regexes attribute_type_input5_1/5_2)
                    attrs.append(("mto_ladder_value", VECTOR_LADDER_PLACEHOLDER))
            attrs.append(("action_operator", a["op"]))
        fs = field("action_type", "update_attribute") + field("mta_type_value", a["level"]) + field("mta_attribute", a["attr"])
        if ns:
            fs += field("vector_counter_name", a.get("vector_name", ns)) + field("vector_counter_namespace", ns)
        if numeric:
            fs += field("action_operator", a["op"])
            rhs = "" if a["op"] == "RESET" else value_input("RHS_INPUT_VALUE", expr(a["value"]))
        else:
            rhs = value_input("RHS_INPUT", expr(a["value"]))
        return block("action", mutation(attrs), fs, rhs)
    raise ValueError(f"unknown action {t}")


# ---------------------------------------------------------------- rules & offer
def rule(r):
    btype = {"header": "rule", "line": "rule_line", "payment": "payment_rule"}[r.get("scope", "header")]
    acts = [action(a) for a in r["actions"]] + [rule(n) for n in r.get("nested", [])]
    return block(btype, field("IDENTIFIER", r["tag"]),
                 statement("CONDITION", [condition(c) for c in r.get("conditions", [])]),
                 statement("ACTION", acts))


def offer_xml(spec):
    """spec: {activity_filters:[cond], rules:[rule], events:[[rule]]}"""
    reset_ids()
    activity = block("activity",
                     statement("ACTIVITY_CRITERIA", [activity_filter(c) for c in spec.get("activity_filters", [])]),
                     statement("RULES", [rule(r) for r in spec["rules"]]))
    events = [block("event", statement("RULES", [rule(r) for r in ev])) for ev in spec.get("events", [])]
    root = block("offer_generic", statement("activities", [activity]), statement("events", events), x=50, y=50)
    return f'<xml xmlns="https://developers.google.com/blockly/xml">{root}</xml>'
