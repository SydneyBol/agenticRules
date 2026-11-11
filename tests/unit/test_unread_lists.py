"""What a canvas may name from the screen's member-level lists and pickers (2026-11-11, AttributeListCoverage-Plan.md
steps 0 and 1). The catalog reads the card, bank account, subscription, analytics, product offer and partner lists and
the card, plan, programme, sweepstakes and play & win pickers, active rows only. A list it has no reader for
(individualization, retention), did not read, or whose feature the program has off, gives nothing to name."""

import json
import os

import pytest

from offer_author.core.run import Run
from offer_author.core.tenant.catalog import LEVEL_READS, UNREAD_LISTS, Catalog, _rows, live, usable
from offer_author.core.vocabulary import ACTION_LISTS, CONDITION_CODES, LEVEL_LISTS, OBJECT_LISTS, PRODUCT_LISTS
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
    named |= {lst for lst, _ in ACTION_LISTS.values()} | {lst for pairs in CONDITION_CODES.values() for _, lst in pairs}
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
        assert cat.listed[lst] == {x["name"] for x in rows if usable(x)} and len(cat.listed[lst]) < len(rows)
    assert cat.listed["card_list"] == {str(x["id"]) for x in _raw("lov_CARDS_LIST") if live(x)}
    assert cat.listed["subscription_plan"] == {str(x["id"]) for x in _raw("lov_SUBSCRIPTION_PLAN_LIST") if live(x)}
    assert not live({"id": 3, "status": False})  # a card that is no longer issued
    assert cat.listed["partner"] == cat.listed["partner_program"] == frozenset()  # partner_program_management is off
    assert not cat.holds("sweepstakes")  # not in the saved dump: not read
    assert all(not cat.holds(lst) for lst in UNREAD_LISTS)
    assert "card" not in cat.attributes  # held for checks; the model is not shown them yet (step 2)


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
    plan = _if(attribute_level="MA_SP", selected_subscription_plan="1", attribute="plan_status", operators="==")
    good = {
        "a card condition": _rules(condition=_if(attribute_level="MA_CC", card_main_option="9", attribute=card)),
        "the member's active cards": _rules(
            condition=_if(attribute_level="MA_CC", card_main_option="ACTIVE_CARDS", operators="in", picked="9,2")
        ),
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
        "'1' is not one of the program's active cobranded cards": _rules(
            condition=_if(attribute_level="MA_CC", card_main_option="ACTIVE_CARDS", operators="in", picked="9,1")
        ),
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
