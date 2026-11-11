"""The fields a card, bank account, subscription or partner condition has on each screen (2026-11-11,
AttributeListCoverage-Plan.md step 3). The oracle: four rule conditions saved by the stock rules screen (the offer
history's shared offers) and the 13 saved segments on cards and subscriptions. The stock screens loaded every good
form below with nothing dropped, and dropped fields from every refused one (2026-11-11, local compile server)."""

import json
import os
import xml.etree.ElementTree as ET

import pytest

from offer_author.core.run import Run
from offer_author.core.tenant.catalog import Catalog
from offer_author.core.vocabulary import ACTIVE_CARD_FIELDS, LEVEL_FIELD_ORDER, LEVEL_FIELDS
from tests.paths import CATALOGS
from tests.support import SEGMENTS, said, saved_segment

KT = os.path.join(os.path.dirname(__file__), "..", "..", "offer_author", "ai", "context", "kt.md")
POINTS = {"type": "action", "action_type": "give_points", "FORMULA": {"type": "static_value", "value": "250"}}
IF = {"type": "if_generic_adaptive", "OBJECT": "member"}
NUM = {"type": "static_value", "value": "3"}
CARD = {**IF, "attribute_level": "MA_CC", "card_main_option": "9", "onlycard_second_option": "ALL_CARDS"}
ACTIVE = {**IF, "attribute_level": "MA_CC", "card_main_option": "ACTIVE_CARDS"}
PLAN = {**IF, "attribute_level": "MA_SP", "selected_subscription_plan": "1"}
SEGMENT_NUM = {"RHS_INPUT": {"type": "static_value", "fields": {"value": "3"}}}


@pytest.fixture(scope="module")
def cat():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


def _rules(cond, where="CONDITION"):
    rule = {"type": "rule", "IDENTIFIER": "R1", "ACTION": [POINTS], **({"CONDITION": [cond]} if cond else {})}
    if where != "CONDITION":
        rule.pop("CONDITION")
    activity = {"type": "activity", "ACTIVITY_CRITERIA": [cond] if where != "CONDITION" else [], "RULES": [rule]}
    return {"canvas": "rules", "counters": [], "blocks": {"type": "offer_generic", "activities": [activity]}}


def _segment(**fields):
    picked, inputs = fields.pop("picked", None), fields.pop("inputs", None)
    c = {"type": "targeting_condition", "fields": fields}
    c |= ({"picked": picked} if picked else {}) | ({"inputs": inputs} if inputs else {})
    return {"canvas": "segment", "blocks": {"type": "targeting_holder", "inputs": {"CONDITIONS": [c]}}}


def _built(cat, canvas, block):
    """(ok, the problems as the model reads them, the block's mutation, its fields in order)."""
    run = Run(cat, None)
    r = run.compose(canvas)
    text = " | ".join(said(r.get("problems") or []))
    xml = getattr(run.canvases.last.get(canvas["canvas"]), "xml", "") or ""
    for b in ET.fromstring(xml).iter() if xml else ():
        if b.tag.split("}")[-1] == "block" and b.get("type") == block:
            mutation = next((dict(c.attrib) for c in b if c.tag.split("}")[-1] == "mutation"), {})
            return r["ok"], text, mutation, [(c.get("name"), c.text) for c in b if c.tag.split("}")[-1] == "field"]
    return r["ok"], text, None, None


SAVED = {"object": "member", "attribute_level": "MA_CC", "payoperators": "null", "iscountertypevector": "no"}


def test_the_rule_conditions_the_screen_saved_are_built_the_same(cat):
    """Three card conditions as the stock rules screen saved them (their card ids are devintbf2's here)."""
    text = {**CARD, "onlycard_second_option": "TRANSACTION_CARD", "attribute": "card_id", "operators": "not_null"}
    ok, said_, mutation, fields = _built(cat, _rules(text), "if_generic_adaptive")
    assert ok, said_
    assert mutation == {**SAVED, "attribute": "card_id", "operators": "not_null", "type": "TEXT", "cardmainoption": "9"}
    assert [k for k, _ in fields] == [
        "OBJECT",
        "attribute_level",
        "card_main_option",
        "onlycard_second_option",
        "attribute",
        "operators",
    ]
    held = {"cardmainoption": "ACTIVE_CARDS", "activecardsoperator": "==", "attribute": "null", "operators": "null"}
    one = {**ACTIVE, "activecard_mainoperators": "==", "cardvalue": "2"}
    for canvas, block in ((_rules(one), "if_generic_adaptive"),):
        ok, said_, mutation, fields = _built(cat, canvas, block)
        assert ok, said_
        assert mutation == {**SAVED, **held}
        assert fields[2:] == [
            ("card_main_option", "ACTIVE_CARDS"),
            ("activecard_mainoperators", "=="),
            ("cardvalue", "2"),
        ]
    filtered = _rules({**one, "type": "activity_criteria"}, where="ACTIVITY_CRITERIA")
    ok, said_, mutation, _ = _built(cat, filtered, "activity_criteria")
    assert ok and mutation == {**SAVED, **held}, (said_, mutation)


def test_the_cards_picked_for_the_members_active_cards_are_saved_with_their_names(cat):
    picked = {**ACTIVE, "activecard_mainoperators": "in", "picked": "9,2"}
    ok, said_, mutation, _ = _built(cat, _rules(picked), "if_generic_adaptive")
    assert ok, said_
    assert (mutation["value"], mutation["visible_value"]) == ("9,2", "HDFC Sliver, Supra Co-Branded Card")
    ok, said_, mutation, _ = _built(
        cat,
        _segment(attr_level="MA_CC", card_setup_id="ACTIVE_CARDS", operators="in", picked="9,2"),
        "targeting_condition",
    )
    assert ok and mutation["visible_value"] == "HDFC Sliver, Supra Co-Branded Card", said_  # as 49_COBRAND saved it


@pytest.mark.parametrize(
    "cond, type_",
    [
        ({**CARD, "attribute": "member_card_level", "operators": ">=", "RHS_INPUT": NUM}, "NUMBER"),
        ({**CARD, "attribute": "card_lookup", "operators": "==", "value": "NAJM_PLT"}, "LOOKUP"),
        (
            {**IF, "attribute_level": "MA_CA", "card_main_option": "ALL_ACCOUNTS", "attribute": "bank_number"}
            | {"operators": ">", "RHS_INPUT": NUM},
            "NUMBER",
        ),
        ({**IF, "attribute_level": "MA_S", "attribute": "ethnical", "operators": ">", "RHS_INPUT": NUM}, "NUMBER"),
        ({**PLAN, "selected_subscription_attribute": "plan_status", "operators": "==", "value": "ACTIVE"}, None),
    ],
)
def test_a_level_attributes_type_is_saved_as_the_screen_reads_it(cat, cond, type_):
    """The rules screen saves the type of what its `attribute` field names; a subscription keeps its attribute in a
    field of its own, so it saves none and `attribute` is "null"."""
    ok, said_, mutation, _ = _built(cat, _rules(cond), "if_generic_adaptive")
    assert ok, said_
    assert mutation.get("type") == type_
    assert mutation["attribute"] == cond.get("attribute", "null")


REFUSED_RULES = {
    "needs `onlycard_second_option`: one of ALL_CARDS, TRANSACTION_CARD": (
        {**IF, "attribute_level": "MA_CC", "card_main_option": "9", "attribute": "card_id", "operators": "not_null"}
    ),
    "needs `card_main_option`: one of the program's active cobranded cards": (
        {**IF, "attribute_level": "MA_CC", "attribute": "card_id", "operators": "not_null"}
    ),
    "keeps its operator in `activecard_mainoperators`, not `operators`": {**ACTIVE, "operators": "in", "picked": "9"},
    "keeps its compared card in `cardvalue`, not `value`": {**ACTIVE, "activecard_mainoperators": "==", "value": "9"},
    "needs `cardvalue`: one of the program's active cobranded cards": {**ACTIVE, "activecard_mainoperators": "=="},
    "needs `picked`": {**ACTIVE, "activecard_mainoperators": "in"},
    "compares card ids and has no `attribute`": (
        {**ACTIVE, "activecard_mainoperators": "in", "picked": "9", "attribute": "card_id"}
    ),
    "needs `activecard_mainoperators`: one of ==, !=, null, not_null, in, not_in (given '>')": (
        {**ACTIVE, "activecard_mainoperators": ">", "cardvalue": "9"}
    ),
    "needs `card_main_option`: one of ALL_ACCOUNTS, TRANSACTION_ACCOUNT (given '9')": (
        {**IF, "attribute_level": "MA_CA", "card_main_option": "9", "attribute": "bank_number", "operators": "not_null"}
    ),
    "keeps its attribute in `selected_subscription_attribute`, not `attribute`": (
        {**PLAN, "attribute": "plan_status", "operators": "==", "value": "ACTIVE"}
    ),
    "needs `selected_subscription_plan`: one of the program's active subscription plans": (
        {**IF, "attribute_level": "MA_SP", "selected_subscription_attribute": "plan_status", "operators": "not_null"}
    ),
    "needs `selected_subscription_attribute`: its attribute": {**PLAN, "operators": "not_null"},
    "card_lookup values ['GOLDX'] are not codes of card_lookup": (
        {**CARD, "attribute": "card_lookup", "operators": "==", "value": "GOLDX"}
    ),
    "'card_lookup' is coded (give one code in value, not picked": (
        {**CARD, "attribute": "card_lookup", "operators": "==", "picked": "NAJM_PLT"}
    ),
    "'card_datetime' is a DATETIME: its date part goes in datetime_operator, not date_operator": (
        {**CARD, "attribute": "card_datetime", "date_operator": "DATE", "operators": ">="}
        | {"RHS_INPUT": {"type": "static_date", "value": "2026-01-01"}}
    ),
    "card attribute 'card_status' is not in the tenant catalog": (  # a BOOL: not listed, as for the member
        {**CARD, "attribute": "card_status", "operators": "not_null"}
    ),
}


@pytest.mark.parametrize("reason", REFUSED_RULES)
def test_a_rule_condition_the_screen_would_not_build_is_refused(cat, reason):
    ok, said_, _, _ = _built(cat, _rules(REFUSED_RULES[reason]), "if_generic_adaptive")
    assert not ok and reason in said_, said_
    assert "if_generic_adaptive" in said_  # the block is named


REFUSED_SEGMENT = {
    "keeps its attribute in `selected_card_attribute`, not `attribute`": dict(
        attr_level="MA_CC", card_setup_id="9", attribute="member_card_level", operators=">", inputs=SEGMENT_NUM
    ),
    "needs `card_setup_id`: one of the program's active cobranded cards": dict(
        attr_level="MA_CC", selected_card_attribute="member_card_level", operators=">", inputs=SEGMENT_NUM
    ),
    "needs `value`: one of the program's active cobranded cards": dict(
        attr_level="MA_CC", card_setup_id="ACTIVE_CARDS", operators="=="
    ),
    "needs `operators`: one of ==, !=, in, not_in (given 'not_null')": dict(
        attr_level="MA_CC", card_setup_id="ACTIVE_CARDS", operators="not_null"
    ),
    "compares card ids and has no `selected_card_attribute`": dict(
        attr_level="MA_CC", card_setup_id="ACTIVE_CARDS", selected_card_attribute="card_id", operators="in", picked="9"
    ),
    "keeps its attribute in `selected_bank_attribute`, not `attribute`": dict(
        attr_level="MA_CA", attribute="bank_number", operators=">", inputs=SEGMENT_NUM
    ),
    "plan_status values ['LIVE'] are not codes of plan_status": dict(
        attr_level="MA_SP",
        selected_subscription_plan="1",
        selected_subscription_attribute="plan_status",
        operators="==",
        value="LIVE",
    ),
    "LOOKUPWITHSPECIAL values ['X'] are not codes": dict(
        attr_level="MA_S", attribute="LOOKUPWITHSPECIAL", operators="==", value="X"
    ),
}


@pytest.mark.parametrize("reason", REFUSED_SEGMENT)
def test_a_segment_condition_the_screen_would_not_build_is_refused(cat, reason):
    ok, said_, _, _ = _built(cat, _segment(**REFUSED_SEGMENT[reason]), "targeting_condition")
    assert not ok and reason in said_, said_


def test_a_levels_fields_are_written_in_the_order_the_screen_builds_them(cat):
    """A dropdown set before the one it hangs on is cleared: the card before its attribute, both before the operator."""
    late = {"operators": ">=", "attribute": "member_card_level", "onlycard_second_option": "ALL_CARDS"}
    late |= {"card_main_option": "9", "attribute_level": "MA_CC", "OBJECT": "member", "RHS_INPUT": NUM}
    ok, said_, _, fields = _built(cat, _rules({"type": "if_generic_adaptive", **late}), "if_generic_adaptive")
    assert ok, said_
    assert [k for k, _ in fields] == [
        "OBJECT",
        "attribute_level",
        "card_main_option",
        "onlycard_second_option",
        "attribute",
        "operators",
    ]
    seg = _segment(
        operators=">",
        selected_card_attribute="member_card_level",
        card_setup_id="9",
        attr_level="MA_CC",
        inputs=SEGMENT_NUM,
    )
    ok, said_, _, fields = _built(cat, seg, "targeting_condition")
    assert ok, said_
    assert [k for k, _ in fields] == ["attr_level", "card_setup_id", "selected_card_attribute", "operators"]


def _blocks(xml):
    out = []
    for b in ET.fromstring(xml).iter():
        if b.tag.split("}")[-1] == "block":
            mutation = next((dict(c.attrib) for c in b if c.tag.split("}")[-1] == "mutation"), {})
            # what the tools do not copy: a single code's label ("null" in every saved segment) and a value block's
            # attr_type, which the screen saved as "undefined"
            mutation = {
                k: v
                for k, v in mutation.items()
                if (k, v) not in (("visible_value", "null"), ("attr_type", "undefined"))
            }
            out.append(
                (b.get("type"), mutation, [(c.get("name"), c.text) for c in b if c.tag.split("}")[-1] == "field"])
            )
    return out


CARD_AND_PLAN_SEGMENTS = ["49_COBRAND", "54_city", "59_tested", "60_tests", "61_testss", "63_testsed", "64_tst"]
CARD_AND_PLAN_SEGMENTS += [
    "66_tsted",
    "68_testd",
    "70_teested",
    "72_testedd",
    "73_testtt",
]  # 57_DOB: another tier class


@pytest.mark.parametrize("name", CARD_AND_PLAN_SEGMENTS)
def test_a_saved_card_or_plan_segment_is_rebuilt_as_the_screen_saved_it(cat, name):
    """Read as the model writes it and composed again: every block's mutation and fields equal the saved XML's
    (devintam1's cards 1 and 9 read as devintbf2's 9 and 2, whose names differ)."""

    def here(text):
        for was, now in (('card_setup_id="1"', 'card_setup_id="9"'), ('"card_setup_id": "1"', '"card_setup_id": "9"')):
            text = text.replace(was, now)
        text = text.replace('"card_setup_id">1<', '"card_setup_id">9<').replace("1,9", "9,2")
        return text.replace("ICICI Amazon Pay, MEDALLION", "HDFC Sliver, Supra Co-Branded Card")

    with open(os.path.join(SEGMENTS, name + ".xml")) as f:
        saved = here(f.read())
    run = Run(cat, None)
    r = run.compose(json.loads(here(json.dumps(saved_segment(name)))))
    assert r["ok"], said(r.get("problems") or [])
    assert _blocks(run.canvases.last["segment"].xml) == _blocks(saved)


def test_the_knowledge_names_every_field_of_these_levels():
    with open(KT) as f:
        kt = f.read()
    fields = {v for screen in LEVEL_FIELDS.values() for x in screen.values() for k, v in x.items() if k != "picks"}
    fields = {x for v in fields for x in ((v,) if isinstance(v, str) else v)}
    fields |= {x[k] for x in ACTIVE_CARD_FIELDS.values() for k in ("operator", "value")} | set(LEVEL_FIELD_ORDER)
    assert not sorted(f for f in fields if f"`{f}`" not in kt)
    assert "needs none of them" in kt and "the card, subscription and partner" not in kt
