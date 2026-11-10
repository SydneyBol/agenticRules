"""Catalog from live-shaped bolapi responses, and the HTTP API contract."""

import json
import os
import re
from urllib.parse import quote

import httpx
import pytest

from offer_author.ai.prompt import catalog_text
from offer_author.api.service import create_app
from offer_author.core.tenant.catalog import BolapiCatalogSource
from tests.fakes import ScriptedLLM
from tests.paths import CATALOGS
from tests.support import finish, golden

DUMPS = os.path.join(CATALOGS, "devintam1", "bolapi")


def bolapi_replay():
    """httpx transport serving the saved devintam1 responses at the URLs BolapiCatalogSource requests."""
    body = lambda f: json.load(open(os.path.join(DUMPS, f + ".json")))["body"]
    routes = {
        "attributelibrary/activity/": "activity_attributes",
        "attributelibrary/member/": "member_attributes",
        "attributelibrary/custom/": "product_attributes",
        "sponsors/loyaltyaccounts/": "sponsor_loyalty_accounts",
        "product/": "privilege_products",
        "sponsors/1/locations/": "extra_locations1",
        "tier-list/": "extra_tiers",
        "lovs/": "lovs",
    }
    lookups = {"BIT_TYPE": "lookup_BIT_TYPE", "PRODUCT_CATEGORY": "extra_prodcat", "TIER_CLASS": "extra_tierclass"}
    seen = []

    def handler(req):
        seen.append(req)
        if req.headers.get("Authorization") != "JWT test":
            return httpx.Response(401)
        rel = req.url.path.split("/bolapi/v1/")[1]
        if rel == "programs/":  # optional: a dump without it answers 404 and the catalog goes on without it
            f = os.path.join(DUMPS, "programs.json")
            return httpx.Response(200, json=body("programs")) if os.path.exists(f) else httpx.Response(404)
        tags = re.fullmatch(r"loyaltyaccounts/(\d+)/tags/", rel)
        if tags:  # every reward account's tags; one the dump has none of answers 404 (that account lists no tags)
            f = "extra_tags" + tags.group(1)
            return (
                httpx.Response(200, json=body(f))
                if os.path.exists(os.path.join(DUMPS, f + ".json"))
                else httpx.Response(404)
            )
        if rel in ("airlines/airports/", "airlines/airports-distance/"):  # optional: a dump without them answers 404
            f = "airports" if rel == "airlines/airports/" else "airport_pairs"
            return (
                httpx.Response(200, json=body(f))
                if os.path.exists(os.path.join(DUMPS, f + ".json"))
                else httpx.Response(404)
            )
        if rel == "attributelibrary/custom/" and req.url.params["module_type"] != "PRODUCT":
            f = "custom_" + req.url.params["module_type"]  # airline modules: a dump without them answers 404
            return (
                httpx.Response(200, json=body(f))
                if os.path.exists(os.path.join(DUMPS, f + ".json"))
                else httpx.Response(404)
            )
        if rel not in routes and rel != "lookups/":  # a table-backed LOV's rows (optional): its dump, else 404
            lovs = {v["api_url"][len("/api/v1/") :]: v["name"] for v in body("lovs") if v.get("api_url")}
            f = "lov_" + lovs.get(rel, "")
            return (
                httpx.Response(200, json=body(f))
                if os.path.exists(os.path.join(DUMPS, f + ".json"))
                else httpx.Response(404)
            )
        if rel == "lookups/" and req.url.params["type"] not in lookups:  # an attribute's own lookup type (optional)
            f = "lookup_" + quote(req.url.params["type"], safe="")
            return (
                httpx.Response(200, json=body(f))
                if os.path.exists(os.path.join(DUMPS, f + ".json"))
                else httpx.Response(404)
            )
        name = lookups[req.url.params["type"]] if rel == "lookups/" else routes[rel]
        return httpx.Response(200, json=body(name))

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_live_source_matches_dumps_and_caches(devintam1_catalog):
    client, seen = bolapi_replay()
    src = BolapiCatalogSource("https://master.devintam1.gravty.info", http=client)
    live = src.get("devintam1", 1, "12", "JWT test", default_tag="BASE")
    assert catalog_text(live) == catalog_text(devintam1_catalog)
    assert live.account() == devintam1_catalog.account() and live.privileges == devintam1_catalog.privileges
    n = len(seen)
    src.get("devintam1", 1, "12", "JWT test")
    assert len(seen) == n  # cached


def test_live_source_propagates_auth_failure():
    client, _ = bolapi_replay()
    with pytest.raises(httpx.HTTPStatusError):
        BolapiCatalogSource("https://x", http=client).get("t", 1, "12", "JWT wrong")


def test_unknown_account_is_none(devintam1_catalog):
    assert devintam1_catalog.account("999") is None and devintam1_catalog.account("12")["id"] in (12, "12")


# ---------------------------------------------------------------- API
EARN = ("compose", golden("S14_simple_sanity"))


@pytest.fixture
def client(fixture_catalog):
    llm = ScriptedLLM([[EARN], [finish(readback="10 points per dollar")]] * 5)
    return create_app(llm, lambda sponsor, account, auth: fixture_catalog).test_client()


def body(**kw):
    b = {"type": "reward", "sponsor_id": 10, "prompt": "10 points per dollar", "id": 5}
    b.update(kw)
    return b


def test_the_popup_answers_the_rules_xml(client):
    r = client.post("/ai/pulsar/offer/rules/?regenerate=true", json=body())
    assert r.status_code == 200 and set(r.get_json()) == {"xml", "raw", "rule_id"} and r.get_json()["rule_id"] == "5"


@pytest.mark.parametrize(
    "kw,message",
    [
        ({"type": "deal"}, "reward offers only"),
        ({"type": "enrollment"}, "reward offers only"),
        ({"prompt": "  "}, "describe the offer rules"),
        ({"prompt": "x" * 25001}, "keep the description under"),
    ],
)
def test_request_validation(client, kw, message):
    r = client.post("/ai/pulsar/offer/rules/?regenerate=true", json=body(**kw))
    assert r.status_code == 400 and message in r.get_json()["error"]["message"]


def test_the_catalog_names_the_program_time_zone_scale_and_tier_ranks():
    """ContextAudit (2026-11-03): dates are read in the program time zone and points kept at its decimal scale, and
    "Gold and above" needs the tiers' order; the catalog said none of these."""
    from offer_author.core.tenant.catalog import Catalog

    cat = Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")
    text = catalog_text(cat)
    # the scale is the tenant's own setting, read as saved (2 until the 2026-11-10 dump, 3 since)
    assert cat.point_decimal_scale is not None and f"point decimal scale: {cat.point_decimal_scale}" in text
    assert "program time zone: Asia/Dubai" in text
    assert "lowest first" in text and list(cat.tiers["test_tier"]) == [
        "tier_gold",
        "tier_dimond",
        "testdsfa",
        "test12345",
    ]


def test_every_lookup_type_the_attributes_use_is_read():
    """2026-11-10: the catalog read three lookup types (BIT_TYPE, PRODUCT_CATEGORY, TIER_CLASS); devintbf2's
    attributes use about 35 more (gender, currency, an airport's zone), each left without a value list."""
    from offer_author.core.tenant.catalog import lookup_requests

    attr = lambda name, kind, lt, **kw: {"name": name, "data_type": kind, "lookup_type": lt, **kw}
    responses = {
        "member_attributes": [
            attr("gender", "LOOKUP", "GENDER"),
            attr("tier_class", "LOOKUP", "TIER_CLASS"),
            attr("city", "LOV", "CITY_LIST"),
            attr("old", "LOOKUP", "OLD", enabled=False),
            attr("hidden", "LOOKUP", "HIDDEN", available_for_rules=False),
            attr("free", "LOOKUP", None),
        ],
        "airport_attributes": {"results": [attr("lookup_code", "LOOKUP", "Flying class")]},
        "lookup:TIER_CLASS": [],
    }
    assert lookup_requests(responses) == {
        "lookup:Flying class": "lookups/?type=Flying%20class&active=True&page_size=1000",
        "lookup:GENDER": "lookups/?type=GENDER&active=True&page_size=1000",
    }


def test_the_live_source_reads_the_other_lookup_types_and_goes_on_without_one(devintam1_catalog):
    client, seen = bolapi_replay()
    rows = [{"code": "F", "name": "Female"}, {"code": "M", "name": "Male"}]

    class Http:
        def get(self, url, headers):
            if "lookups/?type=GENDER&" in url:
                return httpx.Response(200, json={"results": rows}, request=httpx.Request("GET", url))
            return client.get(url, headers=headers)

    live = BolapiCatalogSource("https://master.devintam1.gravty.info", http=Http()).get(
        "devintam1", 1, "12", "JWT test"
    )
    asked = {r.url.params["type"] for r in seen if r.url.path.endswith("/lookups/")}
    assert {"BIT_TYPE", "TIER_CLASS", "CURRENCY", "MARITAL_STATUS"} <= asked  # the dump has no CURRENCY: 404, goes on
    gender = next(a for a in live.attributes["member"].values() if a.lookup_type == "GENDER")
    assert gender.values == {"F": "Female", "M": "Male"}
    assert f"{gender.code} | {gender.label} | LOOKUP | F=Female, M=Male" in catalog_text(live)
    currency = next(a for a in live.attributes["activity"].values() if a.lookup_type == "CURRENCY")
    assert currency.values is None
    same = next(a for a in devintam1_catalog.attributes["member"].values() if a.lookup_type == "GENDER")
    assert same.values is None  # as saved before: the dump holds no such file


def test_a_dump_gives_every_lookup_type_it_saved(tmp_path):
    """The dump script names a lookup file by its type, URL-quoted ("Flying class" is a devintbf2 type)."""
    import shutil

    from offer_author.core.tenant.catalog import Catalog

    d = shutil.copytree(os.path.join(CATALOGS, "devintbf2", "bolapi"), tmp_path / "bolapi")
    save = lambda name, rows: (d / f"{name}.json").write_text(json.dumps({"status": 200, "body": {"results": rows}}))
    save("lookup_AIRPORT_ZONE", [{"code": "start", "name": "Start zone"}, {"code": "far", "name": "Far zone"}])
    save("lookup_Flying%20class", [{"code": "J", "name": "Business"}])
    cat = Catalog.from_bolapi_dumps(str(d), "devintbf2", 1, "9", "BASE")
    assert cat.attr("airport", "zone").values == {"start": "Start zone", "far": "Far zone"}
    assert cat.attr("airport", "lookup_code").values == {"J": "Business"}
    zone = cat.lookup("airport", "zone", "start zone")["candidates"][0]
    assert (zone["code"], zone["airports"]) == ("start", ["BLR", "VAR"])
    assert len(cat.activity_types) == 139  # BIT_TYPE as before
