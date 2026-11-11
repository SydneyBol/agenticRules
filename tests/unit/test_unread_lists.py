"""A canvas may not name anything from a screen list the catalog does not read (2026-11-11,
AttributeListCoverage-Plan.md step 0): such a code can be neither filled nor checked, and the stock screen keeps only
an entry of its own list. The model is told so by the compose and reports the phrase as unsupported."""

import json
import os

import pytest

from offer_author.core.run import Run
from offer_author.core.tenant.catalog import UNREAD_LISTS, Catalog
from offer_author.core.vocabulary import ACTION_LISTS, LEVEL_LISTS, OBJECT_LISTS, PRODUCT_LISTS
from tests.paths import CATALOGS
from tests.support import said, saved_segment

STATIC = {"type": "static_value", "value": "1"}
POINTS = {"type": "action", "action_type": "give_points", "FORMULA": {"type": "static_value", "value": "250"}}


@pytest.fixture(scope="module")
def cat():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


def _rules(action=POINTS, condition=None):
    rule = {"type": "rule", "IDENTIFIER": "R1", "ACTION": [action], **({"CONDITION": [condition]} if condition else {})}
    activity = {"type": "activity", "ACTIVITY_CRITERIA": [], "RULES": [rule]}
    return {"canvas": "rules", "counters": [], "blocks": {"type": "offer_generic", "activities": [activity]}}


def _if(**fields):
    return {"type": "if_generic_adaptive", "operators": ">", "RHS_INPUT": STATIC, **fields}


def _segment(level, attribute="x"):
    s = saved_segment("15_Targeted_offers")
    s["blocks"]["inputs"]["CONDITIONS"][0]["fields"].update(attr_level=level, attribute=attribute)
    return s


def _said(cat, x):
    r = Run(cat, None).compose(x)
    return r["ok"], " | ".join(said(r.get("problems") or []))


def test_every_list_a_block_can_name_is_read_or_marked_unread(cat):
    named = {*LEVEL_LISTS.values(), *OBJECT_LISTS.values(), *PRODUCT_LISTS.values()}
    named |= {lst for lst, _ in ACTION_LISTS.values()}
    held = set(cat.attributes) | set(cat.listed)
    assert named - UNREAD_LISTS <= held, named - UNREAD_LISTS - held
    assert not UNREAD_LISTS & held and all(not cat.holds(lst) for lst in UNREAD_LISTS)


@pytest.mark.parametrize("level", sorted(lv for lv, lst in LEVEL_LISTS.items() if lst in UNREAD_LISTS))
def test_a_level_the_catalog_does_not_read_is_refused_wherever_it_is_named(cat, level):
    member = {"type": "member_attribute", "fields": {"ATTRIBUTE_TYPE": level, "ATTRIBUTE": "x"}}
    uses = {
        "a member condition": _rules(condition=_if(OBJECT="member", attribute_level=level, attribute="x")),
        "a third party's condition": _rules(
            condition=_if(
                OBJECT="third_party", third_parties_dropdown="FAMILY_HEAD", attribute_level=level, attribute="x"
            )
        ),
        "a member value": _rules(action={**POINTS, "FORMULA": member}),
        "a segment condition": _segment(level),
    }
    for where, x in uses.items():
        ok, text = _said(cat, x)
        assert not ok and "the tenant catalog holds no " in text and "can be built on them here" in text, where


def test_product_offer_context_sweepstakes_and_play_win_are_refused(cat):
    offer_context = _if(OBJECT="products_attrs", ProductOrOfferContext="product_offer_attributes", attribute="x")
    value = {
        "type": "product_attribute",
        "fields": {"ATTRIBUTE_PRODUCTS": "product_offer_attributes", "ATTRIBUTE": "x"},
    }
    uses = {
        "product offer context attributes": _rules(condition=offer_context),
        "product offer context attributes ": _rules(action={**POINTS, "FORMULA": value}),
        "sweepstakes": _rules(action={"type": "action", "action_type": "assign_sweepstakes", "sweepstakeid": "7"}),
        "play & win setups": _rules(
            action={"type": "action", "action_type": "assign_play_win_reward", "playwintype": "SCRATCH_CARD"}
        ),
    }
    for label, x in uses.items():
        ok, text = _said(cat, x)
        assert not ok and f"holds no {label.strip()}" in text, (label, text)
        assert "is not in the tenant catalog" not in text  # one reason, not two


def test_a_third_partys_condition_reads_the_member_list(cat):
    """Before: a real member attribute was refused as another object's, and a made-up one passed."""
    third = lambda attribute: _rules(
        condition=_if(
            OBJECT="third_party", third_parties_dropdown="FAMILY_HEAD", attribute_level="MA_P", attribute=attribute
        )
    )
    assert _said(cat, third("age"))[0]
    ok, text = _said(cat, third("no_such"))
    assert not ok and "no_such" in text and "not in the tenant catalog" in text


def test_what_the_catalog_reads_still_passes(cat):
    uses = {
        "member condition": _rules(condition=_if(OBJECT="member", attribute_level="MA_P", attribute="age")),
        "product condition": _rules(
            condition=_if(OBJECT="products_attrs", attribute=next(iter(cat.attributes["product"])))
        ),
        "segment condition": _segment("MA_P", "age"),
        "offer counter value": _rules(
            action={
                **POINTS,
                "FORMULA": {"type": "member_attribute", "fields": {"ATTRIBUTE_TYPE": "MA_O", "ATTRIBUTE": "c"}},
            }
        ),
    }
    for where, x in uses.items():
        assert "holds no" not in json.dumps(_said(cat, x)), where
