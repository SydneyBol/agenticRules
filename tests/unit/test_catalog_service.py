"""Catalog from live-shaped bolapi responses, and the HTTP API contract."""

import json
import os

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
        "loyaltyaccounts/12/tags/": "extra_tags12",
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
