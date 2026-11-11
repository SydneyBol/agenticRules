"""Store, sponsor and payment method attributes and yes/no (BOOL) attributes reach the model (2026-11-11,
AttributeCoverage-Remaining-Plan.md). Every good form below loaded in the local stock rules screen with nothing
dropped or changed; every refused one lost its value there (or compiled to "null" / "undefined")."""

import os

import pytest

from offer_author.ai.prompt import catalog_text
from offer_author.ai.tools import LookupValues
from offer_author.core.run import Run
from offer_author.core.tenant import attribute_search
from offer_author.core.tenant.catalog import SCREEN_LISTS, Catalog
from tests.paths import CATALOGS
from tests.support import said

POINTS = {"type": "action", "action_type": "give_points", "FORMULA": {"type": "static_value", "value": "250"}}
IF = {"type": "if_generic_adaptive"}
CARD = {
    **IF,
    "OBJECT": "member",
    "attribute_level": "MA_CC",
    "card_main_option": "9",
    "onlycard_second_option": "ALL_CARDS",
}


@pytest.fixture(scope="module")
def cat():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


def _said(cat, cond, rule="rule"):
    rule_ = {"type": rule, "IDENTIFIER": "R1", "ACTION": [POINTS], "CONDITION": [cond]}
    activity = {"type": "activity", "ACTIVITY_CRITERIA": [], "RULES": [rule_]}
    r = Run(cat, None).compose(
        {"canvas": "rules", "counters": [], "blocks": {"type": "offer_generic", "activities": [activity]}}
    )
    return r["ok"], " | ".join(said(r.get("problems") or []))


def test_store_sponsor_and_payment_attributes_are_shown_and_looked_up(cat):
    text = catalog_text(cat)
    for lst in SCREEN_LISTS:
        assert cat.attributes[lst] and set(cat.attributes[lst]) == cat.listed[lst]
        assert lst in LookupValues.model_json_schema()["properties"]["object"]["enum"]
    assert "## LOCATION attributes (OBJECT location" in text and "## SPONSOR attributes" in text
    assert "## PAYMENT METHOD attributes" in text and "bank_code | " in text
    assert "area | " in text and "values, not listed: find codes with lookup_values (location, area)" in text
    assert cat.lookup("payment_method", "bank_code", "HDFC")["candidates"][0]["code"] == "HDFC"
    assert (
        attribute_search.where("location", cat.attr("location", "area")) == "the transaction's store (OBJECT location)"
    )


def test_yes_no_attributes_are_kept(cat):
    assert {"card_status", "woman_entrepreneur", "active", "as_privilege"} <= {
        a.code for attrs in cat.attributes.values() for a in attrs.values() if a.type == "BOOL"
    }


@pytest.mark.parametrize(
    "cond",
    [
        {**IF, "OBJECT": "sponsor", "attribute": "woman_entrepreneur", "operators": "not_null"},
        {**CARD, "attribute": "card_status", "operators": "null"},
        {**CARD, "attribute": "card_status", "operators": "==", "RHS_INPUT": {"type": "static_value", "value": "1"}},
        {**IF, "OBJECT": "location", "attribute": "area", "operators": "==", "value": "93"},
        {**IF, "OBJECT": "sponsor", "attribute": "annual_revenue", "operators": "!=", "value": "0 - 100K"},
        {**IF, "OBJECT": "location", "attribute": "contact_name", "operators": "==", "value": "Ali"},
    ],
)
def test_what_the_screen_keeps_is_built(cat, cond):
    ok, text = _said(cat, cond)
    assert ok, text


def test_payment_method_attributes_are_built_in_a_payment_rule(cat):
    for cond in (
        {"type": "if_payment_attribute", "payment_attribute": "bank_code", "operators": "in", "picked": "HDFC,HDFC2"},
        {"type": "if_payment_attribute", "payment_attribute": "bill_no", "operators": "not_null"},
    ):
        ok, text = _said(cat, cond, rule="payment_rule")
        assert ok, text


REFUSED = {
    "'woman_entrepreneur' is a yes/no attribute: the screen tests only whether it is set (operators null, not_null); "
    "'==' is not built": {**IF, "OBJECT": "sponsor", "attribute": "woman_entrepreneur", "operators": "=="}
    | {"RHS_INPUT": {"type": "static_string_value", "value": "true"}},
    "or compares it with a value block (==, !=); '==' is not built": (
        {**CARD, "attribute": "card_status", "operators": "==", "value": "true"}
    ),
    "'area' is a location attribute: its condition keeps one code": (
        {**IF, "OBJECT": "location", "attribute": "area", "operators": "in", "picked": "93,105"}
    ),
    "'annual_revenue' is a sponsor attribute: its condition keeps one code": (
        {**IF, "OBJECT": "sponsor", "attribute": "annual_revenue", "operators": "not_in", "picked": "0 - 100K"}
    ),
    "area values ['Dubai Marina'] are not codes of area": (
        {**IF, "OBJECT": "location", "attribute": "area", "operators": "==", "value": "Dubai Marina"}
    ),
}


@pytest.mark.parametrize("reason", REFUSED)
def test_what_the_screen_drops_is_refused(cat, reason):
    ok, text = _said(cat, REFUSED[reason])
    assert not ok and reason in text, text
