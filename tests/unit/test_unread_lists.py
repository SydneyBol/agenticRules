"""What a canvas may name from the screen's member-level lists and pickers (2026-11-11, AttributeListCoverage-Plan.md
steps 0 to 2). The catalog reads the card, bank account, subscription, analytics, product offer and partner lists and
the card, plan, programme, sweepstakes and play & win pickers, active rows only. A list it has no reader for
(individualization, retention), did not read, or whose feature the program has off, gives nothing to name. The model is shown what is held (step 2): the attribute lists as objects of the catalog text
and of lookup_values, the pickers as id and name."""

import json
import os

import pytest

from offer_author.ai.prompt import catalog_text
from offer_author.ai.tools import LookupValues
from offer_author.core.run import Run
from offer_author.core.tenant import attribute_search
from offer_author.core.tenant.catalog import (
    LEVEL_OBJECTS,
    LEVEL_READS,
    UNREAD_LISTS,
    Catalog,
    _rows,
    live,
    lookup_requests,
    lov_requests,
    usable,
)
from offer_author.core.vocabulary import ACTION_LISTS, LEVEL_FIELDS, LEVEL_LISTS, OBJECT_LISTS, PRODUCT_LISTS
from tests.paths import CATALOGS
from tests.support import said, saved_segment

DUMP = os.path.join(CATALOGS, "devintbf2", "bolapi")
STATIC = {"type": "static_value", "value": "1"}
POINTS = {"type": "action", "action_type": "give_points", "FORMULA": {"type": "static_value", "value": "250"}}


@pytest.fixture(scope="module")
def cat():
    return Catalog.from_bolapi_dumps(DUMP, "devintbf2", 1, "9", "BASE")


def _raw(name):
    with open(os.path.join(DUMP, name + ".json")) as f:
        return _rows(json.load(f)["body"])


def _rules(action=POINTS, condition=None):
    rule = {"type": "rule", "IDENTIFIER": "R1", "ACTION": [action], **({"CONDITION": [condition]} if condition else {})}
    activity = {"type": "activity", "ACTIVITY_CRITERIA": [], "RULES": [rule]}
    return {"canvas": "rules", "counters": [], "blocks": {"type": "offer_generic", "activities": [activity]}}


def _if(**fields):
    return {"type": "if_generic_adaptive", "OBJECT": "member", "operators": ">", "RHS_INPUT": STATIC, **fields}


def _active_cards(picked):
    """A rules condition on the member's active cards: its own operator field, the cards picked in the popup."""
    f = {"card_main_option": "ACTIVE_CARDS", "activecard_mainoperators": "in", "picked": picked}
    return {"type": "if_generic_adaptive", "OBJECT": "member", "attribute_level": "MA_CC", **f}


def _segment(level, **fields):
    s = saved_segment("15_Targeted_offers")
    f = s["blocks"]["inputs"]["CONDITIONS"][0]["fields"]
    f.pop("attribute")  # a card or plan condition names its attribute in its own field, as the screen saves it
    f.update(attr_level=level, **fields)
    return s


def _value(**fields):
    return {**POINTS, "FORMULA": {"type": "member_attribute", "fields": fields}}


def _said(cat, x):
    r = Run(cat, None).compose(x)
    return r["ok"], " | ".join(said(r.get("problems") or []))


def test_every_list_a_block_can_name_is_read_or_marked_unread():
    named = {*LEVEL_LISTS.values(), *OBJECT_LISTS.values(), *PRODUCT_LISTS.values()}
    named |= {lst for lst, _ in ACTION_LISTS.values()}
    named |= {x["picks"] for screen in LEVEL_FIELDS.values() for x in screen.values() if "picks" in x}
    always = {"member", "activity", "product", "airport", "airport_pair", "location", "sponsor"}
    assert named <= always | set(LEVEL_READS) | UNREAD_LISTS, named - always - set(LEVEL_READS) - UNREAD_LISTS
    assert {"personalization", "retention"} == UNREAD_LISTS


def test_the_lists_hold_active_rows_only_and_a_feature_off_holds_nothing(cat):
    for lst, module in (
        ("card", "COBRAND_CARD"),
        ("bank_account", "COBRAND_ACCOUNT"),
        ("subscription", "SUBSCRIPTION"),
    ):
        rows = _raw("custom_" + module)
        active = {x["name"] for x in rows if usable(x)}
        assert cat.listed[lst] == active and len(active) < len(rows)
    assert cat.listed["card_list"] == {str(x["id"]) for x in _raw("lov_CARDS_LIST") if live(x)}
    assert cat.listed["subscription_plan"] == {str(x["id"]) for x in _raw("lov_SUBSCRIPTION_PLAN_LIST") if live(x)}
    assert not live({"id": 3, "status": False})  # a card that is no longer issued
    assert cat.listed["partner"] == cat.listed["partner_program"] == frozenset()  # partner_program_management is off
    assert not cat.holds("sweepstakes")  # not in the saved dump: not read
    assert all(not cat.holds(lst) for lst in UNREAD_LISTS)


def test_a_saved_dump_without_a_picker_holds_none_of_it():
    am1 = Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")
    assert am1.holds("card") and not am1.holds("card_list") and not am1.holds("subscription_plan")


@pytest.mark.parametrize("level", sorted(lv for lv, lst in LEVEL_LISTS.items() if lst in UNREAD_LISTS))
def test_a_level_the_catalog_does_not_read_is_refused_wherever_it_is_named(cat, level):
    third = _if(OBJECT="third_party", third_parties_dropdown="FAMILY_HEAD", attribute_level=level, attribute="x")
    uses = {
        "a member condition": _rules(condition=_if(attribute_level=level, attribute="x")),
        "a third party's condition": _rules(condition=third),
        "a member value": _rules(action=_value(ATTRIBUTE_TYPE=level, ATTRIBUTE="x")),
        "a segment condition": _segment(level, attribute="x"),
    }
    for where, x in uses.items():
        ok, text = _said(cat, x)
        assert not ok and "the tenant catalog holds no " in text and "can be built on them here" in text, where


def test_cards_and_plans_are_checked_on_both_screens(cat):
    card, account, analytics = (sorted(cat.listed[k])[0] for k in ("card", "bank_account", "analytics"))
    plan = {"selected_subscription_plan": "1", "selected_subscription_attribute": "plan_status", "operators": "=="}
    plan = {"type": "if_generic_adaptive", "OBJECT": "member", "attribute_level": "MA_SP", **plan}
    good = {
        "a card condition": _rules(
            condition=_if(
                attribute_level="MA_CC", card_main_option="9", onlycard_second_option="ALL_CARDS", attribute=card
            )
        ),
        "the member's active cards": _rules(condition=_active_cards("9,2")),
        "a card value": _rules(action=_value(ATTRIBUTE_TYPE="MA_CC", ATTRIBUTE="9", cardattribute=card)),
        "a plan condition": _rules(condition={**plan, "value": "ACTIVE"}),
        "a segment card condition": _segment("MA_CC", card_setup_id="9", selected_card_attribute=card),
        "a segment plan condition": _segment(
            "MA_SP", selected_subscription_plan="1", selected_subscription_attribute="plan_status"
        ),
        "a bank account condition": _rules(
            condition=_if(attribute_level="MA_CA", card_main_option="ALL_ACCOUNTS", attribute=account)
        ),
        "an analytics condition": _rules(condition=_if(attribute_level="MA_S", attribute=analytics)),
    }
    for where, x in good.items():
        text = _said(cat, x)[1]
        assert "tenant catalog" not in text and "program's active" not in text, (where, text)
    bad = {
        "'1' is not one of the program's active cobranded cards": _rules(condition=_active_cards("9,1")),
        "'99' is not one of the program's active subscription plans": _segment(
            "MA_SP", selected_subscription_plan="99", selected_subscription_attribute="plan_status"
        ),
        "card attribute 'no_such' is not in the tenant catalog": _segment(
            "MA_CC", card_setup_id="9", selected_card_attribute="no_such"
        ),
        "analytics attribute 'no_such' is not in the tenant catalog": _rules(
            condition=_if(attribute_level="MA_S", attribute="no_such")
        ),
        "'1' is not one of the program's active partner programmes": _rules(
            condition=_if(attribute_level="MA_PP", selected_partner_program="1", attribute="x")
        ),
    }
    for reason, x in bad.items():
        ok, text = _said(cat, x)
        assert not ok and reason in text, (reason, text)


def test_product_offer_context_sweepstakes_and_play_win(cat):
    def offer_context(a):
        return _if(OBJECT="products_attrs", ProductOrOfferContext="product_offer_attributes", attribute=a)

    assert (
        "tenant catalog" not in _said(cat, _rules(condition=offer_context(sorted(cat.listed["product_offer"])[0])))[1]
    )
    ok, text = _said(cat, _rules(condition=offer_context("x")))
    assert not ok and "product_offer attribute 'x' is not in the tenant catalog" in text
    ok, text = _said(cat, _rules(action={"type": "action", "action_type": "assign_sweepstakes", "sweepstakeid": "7"}))
    assert not ok and "holds no sweepstakes" in text  # not read
    play = {
        "type": "action",
        "action_type": "assign_play_win_reward",
        "playwintype": "SCRATCH_CARD",
        "playwinsetup": "3",
    }
    ok, text = _said(cat, _rules(action=play))
    assert not ok and "'3' is not one of the program's active play & win setups" in text  # read: the tenant has none


def test_a_third_partys_condition_reads_the_member_list(cat):
    """Before 2026-11-11: a real member attribute was refused as another object's, and a made-up one passed."""

    def third(attribute):
        f = {"OBJECT": "third_party", "third_parties_dropdown": "FAMILY_HEAD", "attribute_level": "MA_P"}
        return _rules(condition=_if(**f, attribute=attribute))

    assert _said(cat, third("age"))[0]
    ok, text = _said(cat, third("no_such"))
    assert not ok and "no_such" in text and "not in the tenant catalog" in text


def test_the_model_is_shown_the_lists_that_are_held(cat):
    text = catalog_text(cat)
    for lst, module in (
        ("card", "COBRAND_CARD"),
        ("bank_account", "COBRAND_ACCOUNT"),
        ("subscription", "SUBSCRIPTION"),
    ):
        rows = {x["name"]: x for x in _raw("custom_" + module)}
        shown = set(cat.attributes[lst])
        assert shown and all(usable(rows[c]) for c in shown) and shown == cat.listed[lst]
    assert "## COBRANDED CARD attributes (attribute level MA_CC" in text
    assert "plan_status | Plan Status | LOOKUP | ACTIVE=Active" in text
    assert "## PRODUCT OFFER CONTEXT attributes" in text and "sku | Stock Keeping Unit | TEXT" in text
    assert "## The program's active cobranded cards (card id | name)\n2 | Supra Co-Branded Card" in text
    assert "## The program's active subscription plans (plan id | name)" in text and "\n3 | Silver" in text
    # nothing for a list that holds nothing (partner: the switch is off; play & win: no active setup) or was not read
    assert "PARTNER MEMBERSHIP attributes" not in text and "partner" not in cat.attributes
    assert not any(f"The program's active {x}" in text for x in ("partner programmes", "play & win", "sweepstakes"))
    assert attribute_search.where("card", cat.attr("card", "closing_date")) == "cobranded card (MA_CC)"


def test_a_dump_without_the_pickers_shows_the_attributes_and_no_card_or_plan():
    am1 = Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")
    text = catalog_text(am1)
    assert "## COBRANDED CARD attributes" in text and "cobranded cards (card id" not in text and not am1.pickers


def test_lookup_values_reads_a_member_level_list(cat):
    assert set(LEVEL_OBJECTS) <= set(LookupValues.model_json_schema()["properties"]["object"]["enum"])
    r = Run(cat, None).lookup_values({"object": "subscription", "attribute": "plan_status", "text": "cancelled"})
    assert r["candidates"][0]["code"] == "CANCELLED"
    r = Run(cat, None).lookup_values({"object": "card", "attribute": "card_lookup", "text": "platinum"})
    assert r["candidates"][0]["code"] == "NAJM_PLT"


def test_a_refresh_reads_the_value_lists_of_the_member_level_attributes():
    flags = {"enabled": True, "available_for_rules": True}
    rows = [
        {"name": "plan_status", "data_type": "LOOKUP", "lookup_type": "PLAN_STATUS", **flags},
        {"name": "auto_renewal", "data_type": "LOV", "lookup_type": "AUTO_RENEWAL_LIST", **flags},
        {"name": "old", "data_type": "LOOKUP", "lookup_type": "OLD_TYPE", **flags, "enabled": False},
    ]
    lovs = [{"name": "AUTO_RENEWAL_LIST", "api_url": "/api/v1/auto-renewal/", "code": "id"}]
    resp = {"list:subscription": rows, "list:sponsor": rows[2:], "lovs": lovs}
    assert set(lookup_requests(resp)) == {"lookup:PLAN_STATUS"}  # not an inactive attribute's, not a check-only list's
    assert set(lov_requests(resp)) == {"lov:AUTO_RENEWAL_LIST"}
