"""Fields the stock screen would leave empty or reject, found before the screen is asked.

Codes in a value field, a date part in the wrong field, a compared number with no value block, a missing counter or
attribute, a rule with no action, a condition with no operator or no value, a tier the catalog does not have, an OR
with an empty side. The segment's condition (targeting_condition) is checked as a member condition at its attr_level.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ....domain import BlockRef, Problem
from ...vocabulary import CATALOG_OBJECT, COUNTER_LEVELS
from ..fields import CONDITIONS, data_type, fill
from ..reference import picks_by_operator
from ..tree import nodes

if TYPE_CHECKING:
    from ...tenant.catalog import Catalog

Form = tuple[str, dict[str, Any]]


def _value_form(t: str | None, f: dict[str, Any], picked: bool, catalog: Catalog | None) -> Form | None:
    """A coded value given in the form its operator does not take.

    For a coded attribute (LOOKUP / LOV), the UI takes codes from the popup (picked) for in / not_in and from the value
    dropdown for any other operator: the other form is dropped or replaced by the dropdown's first code.
    """
    if t not in picks_by_operator() or catalog is None or f.get("OBJECT") not in CATALOG_OBJECT:
        return None
    a = catalog.attr(CATALOG_OBJECT[f["OBJECT"]], f.get("attribute") or "")
    if a is None or a.type not in ("LOOKUP", "LOV") or f.get("operators") in (None, "", "null", "not_null"):
        return None
    if f["operators"] in ("in", "not_in"):
        return ("codes_belong_in_picked", {}) if "value" in f and not picked else None
    return ("code_belongs_in_value", {"operator": f["operators"]}) if picked else None


DATE_PART_FIELD = {"DATE": "date_operator", "DATETIME": "datetime_operator"}  # the field the screen builds per type
NO_RHS = ("", "null", "not_null", "in", "not_in", "between", "contains")  # operators with no single value input
SEGMENT_CONDITION = "targeting_condition"  # a Select Members condition: a member attribute at attr_level
CHECKED = (*CONDITIONS, SEGMENT_CONDITION)
VALUE_FIELDS = ("value", "tierlevelnamevalue")  # a condition's compared value, besides RHS_INPUT and picked codes
TIER_LEVEL = "MA_T"  # the tier programme (tier class, tier name, start date, validity)
# A TEXT attribute compared with = / != takes a typed text in `value`, and a TEXT counter takes typed text with every
# operator it has (str_conditional_operators: no match / not_match): the screen builds no RHS_INPUT socket for them
# (if-generic.block.ts updateShapeByOperator_), so a block there is dropped with every block after it (verticals
# 2026-11-04: V_RT2 it5 and it7, V_RT3 it6, "BLOCK MISSING"). A text attribute's match / not_match take a value block.
TYPED_TEXT_OPERATORS = ("==", "!=")
# The condition operators' codes (block-definition.service.ts operator menus); anything else selects nothing ("If
# Block --> operator is not selected", verticals it7 V_RT2: "doesn't match").
OPERATOR_CODES = (
    "==", "!=", ">", ">=", "<", "<=", "between", "in", "not_in", "null", "not_null",
    "contains", "not_contains", "starts_with", "ends_with", "match", "not_match",
)  # fmt: skip


def _as_condition(t: str | None, f: dict[str, Any]) -> dict[str, Any]:
    """A segment condition's fields read as a rules condition's: its object is the member, its level attr_level."""
    if t != SEGMENT_CONDITION:
        return f
    return dict(f, OBJECT="member", **({"attribute_level": f["attr_level"]} if "attr_level" in f else {}))


def _incomplete(n: Any, f: dict[str, Any]) -> str | None:
    """A condition with no operator, or with a single-value operator and nothing to compare with.

    The screen keeps such a condition empty and it compiles to a bare read (live 2026-10-30: C12).
    """
    op = f.get("operators")
    if not op:
        return "condition_without_operator"
    if op in NO_RHS or any(f.get(k) not in (None, "") for k in VALUE_FIELDS) or n.get("picked") not in (None, ""):
        return None
    return None if (n.get("inputs") or {}).get("RHS_INPUT") else "condition_without_value"


def _tier(f: dict[str, Any], catalog: Catalog | None) -> Form | None:
    """A segment tier condition's tier class and tier name against the tenant's tier programme."""
    if f.get("attr_level") != TIER_LEVEL or catalog is None or not catalog.tiers:
        return None
    cls = f.get("tierslevel")
    if cls not in catalog.tiers:
        return "tier_class_not_in_catalog", {"given": cls, "known": sorted(catalog.tiers)}
    tiers = catalog.tiers[cls]
    name = f.get("tierlevelnamevalue")
    if name not in (None, "") and name not in tiers and name not in tiers.values():
        return "tier_not_in_class", {"given": name, "tier_class": cls, "known": sorted(tiers.values())}
    return None


def _attr_type(t: str | None, f: dict[str, Any], catalog: Catalog | None, counters: dict[str, Any] | None) -> Any:
    """The data type of the attribute or counter a condition or attribute block reads; None if unknown."""
    if t in CHECKED:
        code, obj, level = f.get("attribute"), f.get("OBJECT"), f.get("attribute_level")
    elif t == "member_attribute":
        code, obj, level = f.get("ATTRIBUTE"), "member", f.get("ATTRIBUTE_TYPE")
    elif t == "activity_attribute":
        code, obj, level = f.get("ATTRIBUTE"), "activity", None
    else:
        return None
    if level in COUNTER_LEVELS:
        return ((counters or {}).get(code or "") or {}).get("data_type")
    return data_type(catalog, obj, level, code)


def _date_form(t: str | None, f: dict[str, Any], typ: str | None) -> Form | None:
    """A date part or a compared value in a field the attribute's type does not have.

    A date part goes in the field the screen builds for the attribute's type (date_operator for a DATE,
    datetime_operator for a DATETIME), and a condition's number or date is compared through RHS_INPUT: the screen has
    no value field for those and drops what is written in the other one (live 2026-10-30: C07 10 turns, C09 8 turns).
    """
    given = [k for k in DATE_PART_FIELD.values() if k in f]
    want = DATE_PART_FIELD.get(typ or "")
    if want and given and given != [want]:
        return "date_part_field", {"type": typ, "want": want, "given": given[0]}
    if want and not given and t in ("member_attribute", "activity_attribute"):
        # an empty date part compiles as CALENDAR_DATE (day and month in a fixed year): flow D03, 2026-11-03
        return "date_value_without_part", {"type": typ, "want": want}
    if typ == "DATE" and want and f.get(want) in ("HOUR_OF_DAY", "MINUTE_OF_HOUR"):
        return "date_has_no_time", {"part": f[want]}
    if (
        t in CHECKED
        and "value" in f
        and typ in ("NUMBER", "DATE", "DATETIME")
        and f.get("operators") not in (None, *NO_RHS)
    ):
        return "no_value_field", {"type": typ}
    return None


def _shape(n: Any, f: dict[str, Any], catalog: Catalog | None, block: BlockRef, name: Any) -> list[Problem]:
    """Conditions, ORs and attribute values the screen cannot make whole.

    A condition with no operator or nothing to compare with, a segment tier the catalog does not have, an OR with an
    empty side, a member_attribute naming an attribute its level does not list (_level_form), an activity_attribute
    naming something that is not a transaction attribute of the catalog (the screen compiles it to an empty read, and
    the offer's rules then do not compile: 2026-11-05 C03, an offer counter read with activity_attribute).
    """
    t, out = n.get("type"), []
    if t in CHECKED and (code := _incomplete(n, f)):
        out.append(Problem.of(code, block, name=name or f.get("tiermainoption")))
    if t == SEGMENT_CONDITION and (form := _tier(f, catalog)):
        out.append(Problem.of(form[0], block, **form[1]))
    if t == "or" and not all((n.get("inputs") or {}).get(k) for k in ("OPERAND1", "OPERAND2")):
        out.append(Problem.of("or_operand_empty", block))
    if form := _level_form(t, f, catalog):
        out.append(Problem.of(form[0], block, name=f.get("ATTRIBUTE"), **form[1]))
    attr = f.get("ATTRIBUTE")
    if t == "activity_attribute" and catalog is not None and attr and catalog.attr("activity", str(attr)) is None:
        out.append(Problem.of("not_a_transaction_attribute", block, name=attr))
    return out


def _operator_form(n: Any, f: dict[str, Any], typ: str | None) -> Form | None:
    """An operator the screen has no code for, or a TEXT comparison given a block where the screen takes typed text."""
    op = f.get("operators")
    if op not in (None, "", *OPERATOR_CODES):
        return "operator_not_a_code", {"given": op, "codes": list(OPERATOR_CODES)}
    counter = f.get("attribute_level") in COUNTER_LEVELS
    if typ == "TEXT" and "RHS_INPUT" in (n.get("inputs") or {}) and (op in TYPED_TEXT_OPERATORS or counter):
        return "text_compared_with_block", {"operator": op}
    return None


VALUE_LEVELS = {"MA_P": "member", "MA_A": "activity"}  # member_attribute ATTRIBUTE_TYPE: the catalog list it offers


def _level_form(t: str | None, f: dict[str, Any], catalog: Catalog | None) -> Form | None:
    """A member_attribute naming an attribute its ATTRIBUTE_TYPE does not list.

    The screen fills ATTRIBUTE from the chosen level's list and drops a code that is not in it, so the value reads
    nothing (2026-11-04 S12: ATTRIBUTE_TYPE MP_S with date_of_birth, a Program-Level attribute).
    """
    level, attr = f.get("ATTRIBUTE_TYPE"), f.get("ATTRIBUTE")
    if t != "member_attribute" or catalog is None or not attr or level in COUNTER_LEVELS:
        return None
    owner = next((lv for lv, obj in VALUE_LEVELS.items() if catalog.attr(obj, attr)), None)
    if owner is None or owner == level:
        return None
    return "attribute_not_of_level", {"given": level, "owner": owner}


FILTER = "ACTIVITY_CRITERIA"  # the On Activity filter: its conditions compile into the rule's when-clause


def _filter_problems(root: Any) -> list[Problem]:
    """Rule conditions (if_generic_adaptive) in an On Activity filter that read the transaction date as CALENDAR_DATE.

    The screen writes such a condition with bitHeader, a variable of the rule's actions
    (activity-attribute-drl.service.ts checkforlineattribute): in the filter the rules do not compile (2026-11-04 X01:
    "Unable to Analyse Expression").
    The filter's own condition (activity_criteria) reads it with readAttributeS and compiles (B1).
    """
    return [
        Problem.of("bit_calendar_date_in_filter", BlockRef(str(m.get("type")), str(m.get("id"))))
        for n, _, _ in nodes(root)
        if (n.get("inputs") or {}).get(FILTER)
        for m, _, _ in nodes(n["inputs"][FILTER])
        if m.get("type") == "if_generic_adaptive"
        and (m.get("fields") or {}).get("attribute") == "h_bit_date"
        and (m.get("fields") or {}).get("datetime_operator") == "CALENDAR_DATE"
    ]


TAGGED = ("rule", "rule_line", "payment_rule")  # rule blocks with an IDENTIFIER: the rule's tag
DEFAULT_TAG = "tag"  # what the screen writes when the field is left empty (rule.block.ts FieldTextInput)


def _tag_problems(root: Any) -> list[Problem]:
    """Rule blocks whose tag (IDENTIFIER) is empty, the screen's default, or another rule's.

    The tag names the rule on the canvas and in the compiled rules (ruleTag), and names what each rule did in the
    engine check's report; an empty one is saved as the default "tag".
    """
    out, seen = [], set()
    for n, _, _ in nodes(root):
        if n.get("type") not in TAGGED:
            continue
        tag = str((n.get("fields") or {}).get("IDENTIFIER") or "").strip()
        block = BlockRef(str(n.get("type")), str(n.get("id")))
        if tag.lower() in ("", DEFAULT_TAG):
            out.append(Problem.of("rule_without_tag", block))
        elif tag.lower() in seen:
            out.append(Problem.of("rule_tag_repeated", block, name=tag))
        seen.add(tag.lower())
    return out


def unfilled(root: Any, catalog: Catalog | None = None, counters: dict[str, Any] | None = None) -> list[Problem]:
    """Problems the screen would cause that code can tell before any compile, by block.

    A rule with no action (it can never reward or count anything), the conditions and ORs _shape reports, fields the
    model left out that `fill` cannot work out (a counter that is not declared, an attribute the catalog does not
    have), a coded value given in the form its operator does not take (_value_form), and a date part or a compared
    number in a field the attribute's type does not have (_date_form), a transaction date read as CALENDAR_DATE in the
    filter (_filter_problems), a rule without a tag of its own (_tag_problems).
    """
    out = _filter_problems(root) + _tag_problems(root)
    for n, _, _ in nodes(root):
        t, f = n.get("type"), _as_condition(n.get("type"), fill(n, catalog, counters))
        name = f.get("attribute") if t in CHECKED else f.get("mta_attribute")
        block = BlockRef(str(t), str(n.get("id")))
        if form := _value_form(t, f, n.get("picked") not in (None, ""), catalog):
            out.append(Problem.of(form[0], block, name=name, **form[1]))
        if form := _date_form(t, f, _attr_type(t, f, catalog, counters)):
            out.append(Problem.of(form[0], block, name=f.get("attribute") or f.get("ATTRIBUTE"), **form[1]))
        out += _shape(n, f, catalog, block, name)
        if t in CONDITIONS and (form := _operator_form(n, f, _attr_type(t, f, catalog, counters))):
            out.append(Problem.of(form[0], block, name=name, **form[1]))
        if t == "rule" and not (n.get("inputs") or {}).get("ACTION"):
            out.append(Problem.of("rule_without_action", block, name=f.get("IDENTIFIER")))
        elif t == SEGMENT_CONDITION and "attr_level" not in f:
            out.append(Problem.of("segment_condition_without_level", block, name=name))
        elif t in CONDITIONS and ("OBJECT" not in f or (f["OBJECT"] == "member" and "attribute_level" not in f)):
            out.append(Problem.of("unknown_condition_attribute", block, name=name))
        elif (
            t == "action"
            and f.get("action_type") == "give_points"
            and catalog is not None
            and "REWARD_TAGS" not in f
            and "REWARD_TAGS" not in (n.get("inputs") or {})
        ):
            out.append(Problem.of("no_reward_tag", block))
        elif t == "action" and f.get("action_type") == "update_attribute" and not f.get("mta_attribute"):
            out.append(Problem.of("update_without_counter", block))
        elif t == "action" and f.get("action_type") == "update_attribute" and "mta_type_value" not in f:
            out.append(Problem.of("undeclared_update_counter", block, name=name))
        elif (
            t == "action"
            and f.get("action_type") == "update_attribute"
            and not re.fullmatch(r"M[A-Z]_[A-Z]{1,3}", f["mta_type_value"])
        ):
            out.append(Problem.of("level_field_not_a_level", block, given=f["mta_type_value"]))
    return out
