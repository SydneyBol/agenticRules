"""An attribute rules may name is one bolapi marks active for rules (enabled and available for rules): the stock
screen lists no other. Checked in every spot of a canvas that names an attribute (2026-11-11)."""

import json
import os

import pytest

from offer_author.core.run import Run
from offer_author.core.tenant.catalog import SCREEN_LISTS, Catalog, _rows, usable
from tests.paths import CATALOGS
from tests.support import golden, saved_segment

DUMP = os.path.join(CATALOGS, "devintbf2", "bolapi")
STATIC = {"type": "static_value", "value": "250"}


@pytest.fixture(scope="module")
def cat():
    return Catalog.from_bolapi_dumps(DUMP, "devintbf2", 1, "9", "BASE")


def _raw(name):
    with open(os.path.join(DUMP, name + ".json")) as f:
        return _rows(json.load(f)["body"])


def _inactive(name, taken=()):
    """A code of that saved list bolapi does not mark active for rules, and no active attribute elsewhere has."""
    return next(x["name"] for x in _raw(name) if not usable(x) and x["name"] not in taken)


def _points(formula):
    return {"type": "action", "action_type": "give_points", "FORMULA": formula}


def _offer(criteria, action):
    rule = {"type": "rule", "IDENTIFIER": "R1", "ACTION": [action]}
    return {
        "canvas": "rules",
        "counters": [],
        "blocks": {
            "type": "offer_generic",
            "activities": [{"type": "activity", "ACTIVITY_CRITERIA": criteria, "RULES": [rule]}],
        },
    }


def _condition(obj, attribute, **kw):
    return {"type": "activity_criteria", "OBJECT": obj, "attribute": attribute, "operators": "==", "value": "X", **kw}


def _codes(cat, x):
    return json.dumps(Run(cat, None).compose(x), default=str)


def test_usable_is_both_flags_as_the_stock_screen_reads_them():
    row = {"name": "a", "enabled": True, "available_for_rules": True}
    assert usable(row)
    assert not usable({**row, "enabled": False}) and not usable({**row, "available_for_rules": False})
    assert not usable({"name": "a"})  # the screen's `available_for_rules && enabled`: a missing flag lists nothing


def test_the_catalog_holds_no_inactive_attribute_and_knows_the_screens_other_lists(cat):
    files = {"activity": "activity_attributes", "member": "member_attributes", "product": "product_attributes"}
    files.update({"airport": "custom_AIRPORT", "airport_pair": "custom_AIRPORT_DISTANCE"})
    for obj, name in files.items():
        off = {x["name"] for x in _raw(name) if not usable(x)}
        assert off or obj == "airport"  # the saved tenant has inactive attributes in each list but the airports'
        assert not off & set(cat.attributes[obj])
        assert all(cat.active(obj, code) is False for code in off)
    for lst, module in SCREEN_LISTS.items():
        rows = _raw("custom_" + module)
        assert cat.listed[lst] == {x["name"] for x in rows if usable(x)}
        assert set(cat.attributes[lst]) == cat.listed[lst]  # shown to the model too (2026-11-11)
    assert len(cat.listed["sponsor"]) < len(_raw("custom_SPONSOR"))  # the saved tenant's inactive ones are left out
    assert cat.active("payment_method", "payment_method")  # a transaction attribute at payment level
    assert cat.active("retention", "anything") is None  # a list the catalog was not given: not checked


def test_an_inactive_attribute_is_refused_wherever_a_canvas_names_one(cat):
    act, mem = _inactive("activity_attributes"), _inactive("member_attributes")
    prod = _inactive("product_attributes", cat.attributes["activity"])
    pair = _inactive("custom_AIRPORT_DISTANCE")
    known = {c for attrs in cat.attributes.values() for c in attrs}
    loc, spo = _inactive("custom_SPONSORLOCATION", known), _inactive("custom_SPONSOR", known)
    value = lambda t, **f: _offer([], _points({"type": t, "fields": f}))
    update = {"type": "action", "action_type": "update_attribute", "mta_type_value": "MA_P", "mta_attribute": mem}
    vector = golden("S09_unique_locations")
    vector["counters"] = [dict(c, namespace=act) if c.get("namespace") else c for c in vector["counters"]]
    segment = saved_segment("15_Targeted_offers")
    segment["blocks"]["inputs"]["CONDITIONS"][0]["fields"]["attribute"] = mem
    uses = {
        "a condition on the transaction": (_offer([_condition("activity", act)], _points(STATIC)), act),
        "a condition on the member": (
            _offer([_condition("member", mem, attribute_level="MA_P")], _points(STATIC)),
            mem,
        ),
        "a condition on the location": (_offer([_condition("location", loc)], _points(STATIC)), loc),
        "a condition on the sponsor": (_offer([_condition("sponsor", spo)], _points(STATIC)), spo),
        "a transaction value": (value("activity_attribute", ATTRIBUTE=act), act),
        "a member value": (value("member_attribute", ATTRIBUTE_TYPE="MA_P", ATTRIBUTE=mem), mem),
        "a product value": (value("product_attribute", ATTRIBUTE_PRODUCTS="products_attrs", ATTRIBUTE=prod), prod),
        "a payment value": (value("payment_attribute", payment_attribute=act), act),
        "an airport pair value": (value("airport_attribute", airportpair_attribute=pair), pair),
        "an attribute update": (_offer([], {**update, "action_operator": "SET", "RHS_INPUT_VALUE": STATIC}), mem),
        "what a vector counter counts per": (vector, act),
        "a segment condition": (segment, mem),
    }
    for where, (x, code) in uses.items():
        said = _codes(cat, x)
        named = "not_a_transaction_attribute" if where == "a transaction value" else "attribute_not_in_catalog"
        named = "namespace_not_in_catalog" if "counter" in where else named
        assert '"ok": false' in said and named in said and code in said, where


def test_an_active_attribute_passes_in_the_same_spots(cat):
    number = lambda obj: next(a.code for a in cat.attributes[obj].values() if a.type == "NUMBER")
    value = lambda t, **f: _offer([], _points({"type": t, "fields": f}))
    update = {"type": "action", "action_type": "update_attribute", "mta_type_value": "MA_P"}
    shared = next(c for c in sorted(cat.listed["location"]) if cat.attr("member", c) or cat.attr("product", c))
    uses = {
        "a location attribute": _offer([_condition("location", sorted(cat.listed["location"])[0])], _points(STATIC)),
        "a location attribute another list also has": _offer([_condition("location", shared)], _points(STATIC)),
        "a sponsor attribute": _offer([_condition("sponsor", sorted(cat.listed["sponsor"])[0])], _points(STATIC)),
        "a member value": value("member_attribute", ATTRIBUTE_TYPE="MA_P", ATTRIBUTE=number("member")),
        "a product value": value("product_attribute", ATTRIBUTE_PRODUCTS="products_attrs", ATTRIBUTE=number("product")),
        "a payment method attribute": value(
            "payment_attribute", payment_attribute=sorted(cat.listed["payment_method"])[0]
        ),
        "a payment-level transaction attribute": value("payment_attribute", payment_attribute="payment_method"),
        "an airport pair attribute": value(
            "airport_attribute", airportpair_attribute=next(iter(cat.attributes["airport_pair"]))
        ),
        "the airport pair block's own option": value("airport_attribute", airportpair_attribute="award_price"),
        "an attribute update": _offer(
            [], {**update, "mta_attribute": number("member"), "action_operator": "SET", "RHS_INPUT_VALUE": STATIC}
        ),
        "a vector counter per store": golden("S09_unique_locations"),
    }
    for where, x in uses.items():
        assert '"ok": true' in _codes(cat, x), where


def test_a_catalog_without_the_screens_other_lists_leaves_them_unchecked(fixture_catalog):
    """The hand-written fixture has no sponsor, location or payment list: what it cannot tell is not refused."""
    assert fixture_catalog.listed == {} and fixture_catalog.active("location", "x") is None
    x = _offer([_condition("location", "store_size")], _points(STATIC))
    assert "attribute_not_in_catalog" not in _codes(fixture_catalog, x)


def test_no_value_list_holds_a_row_no_longer_in_use(cat):
    """2026-11-11, the user: inactive must not be loaded. The table-backed lists kept their inactive rows: 5 of 8
    airports (HYD), 10 of 31 payment methods, 42 of 100 loyalty accounts, 900 of 1000 sponsors (terminated)."""
    from offer_author.core.tenant.catalog import live

    assert not live({"active": False}) and not live({"status": "Inactive"}) and not live({"status": "T"})
    assert live({"active": True}) and live({"status": "A"}) and live({"status": True}) and live("code")
    for lov, (obj, code) in {
        "AIRPORT_CODE_LIST": ("airport", "airport_code"),
        "PAYMENT_METHOD_LIST": ("activity", "payment_method"),
        "LOYALTY_ACCOUNT_LIST": ("activity", "h_from_loyalty_account"),
        "SPONSOR_LIST": ("activity", "h_sponsor_id"),
    }.items():
        rows = _raw("lov_" + lov)
        lists = {x["name"]: x for x in _rows(json.load(open(os.path.join(DUMP, "lovs.json")))["body"])}
        key = lists[lov]["code"]
        off = {str(r[key]) for r in rows if not live(r)}
        assert off and not off & set(cat.attr(obj, code).values), lov
        assert set(cat.attr(obj, code).values) == {str(r[key]) for r in rows if live(r)}, lov


def test_a_privilege_whose_active_window_ended_is_not_loaded():
    pol = [{"id": 9, "policy_name": "Default", "default": True, "active": True}]
    prod = lambda i, **kw: {"id": i, "name": f"P{i}", "privilege_type": "Fixed-Product", "usage_policy": pol, **kw}
    responses = {
        "activity_attributes": [],
        "member_attributes": [],
        "product_attributes": [],
        "sponsor_loyalty_accounts": [],
        "privilege_products": [
            prod(1, active_end="2099-01-01T00:00:00Z"),
            prod(2, active_end="2020-01-01T00:00:00Z"),
            prod(3),
            prod(4, usage_policy=[dict(pol[0], active=False)]),
        ],
    }
    cat = Catalog.from_bolapi("t", 1, 9, responses)
    assert sorted(cat.privileges) == ["1", "3"]
