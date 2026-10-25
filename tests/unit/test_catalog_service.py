"""Catalog from live-shaped bolapi responses, and the HTTP API contract."""
import json
import os
import time

import httpx
import pytest

from tests.support import ROOT, finish, golden
from offer_author.core.catalog import BolapiCatalogSource
from offer_author.ai.llm import ScriptedLLM
from offer_author.api.service import create_app

DUMPS = os.path.join(ROOT, "catalogs", "devintam1", "bolapi")


def bolapi_replay():
    """httpx transport serving the saved devintam1 responses at the URLs BolapiCatalogSource requests."""
    body = lambda f: json.load(open(os.path.join(DUMPS, f + ".json")))["body"]
    routes = {"attributelibrary/activity/": "activity_attributes", "attributelibrary/member/": "member_attributes",
              "attributelibrary/custom/": "product_attributes", "sponsors/loyaltyaccounts/": "sponsor_loyalty_accounts",
              "product/": "privilege_products", "sponsors/1/locations/": "extra_locations1", "loyaltyaccounts/12/tags/": "extra_tags12",
              "tier-list/": "extra_tiers"}
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
    assert live.prompt_text() == devintam1_catalog.prompt_text()
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
EARN = ("compile", golden("S14_simple_sanity"))


@pytest.fixture
def client(fixture_catalog, fake_ui):
    llm = ScriptedLLM([[EARN], [finish(readback="10 points per dollar")]] * 5)
    return create_app(llm, lambda sponsor, account, auth: fixture_catalog).test_client()


def body(**kw):
    b = {"offer_type": "reward", "sponsor_id": 10, "prompt": "10 points per dollar", "offer_id": 5}
    b.update(kw)
    return b


def test_async_job_roundtrip(client):
    r = client.post("/ai/offer-author/generate", json=body())
    assert r.status_code == 202
    jid = r.get_json()["job_id"]
    for _ in range(50):
        j = client.get(f"/ai/offer-author/{jid}").get_json()
        if j["state"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert j["state"] == "done" and j["result"]["status"] == "draft" and j["result"]["readback"] == "10 points per dollar"


def test_sync_mode(client):
    r = client.post("/ai/offer-author/generate?wait=true", json=body())
    assert r.status_code == 200 and r.get_json()["rules"]["expected_offer_id"] == 5


@pytest.mark.parametrize("kw,status,code", [
    ({"offer_type": "deal"}, 422, "unsupported_offer_type"),
    ({"offer_type": "enrollment"}, 422, "unsupported_offer_type"),
    ({"prompt": "  "}, 400, "prompt_required"),
    ({"prompt": "x" * 4001}, 400, "prompt_too_long"),
    ({"sponsor_id": "1"}, 400, "sponsor_required"),
    ({"loyalty_account_id": "999"}, 424, "catalog_unavailable"),
])
def test_request_validation(client, kw, status, code):
    r = client.post("/ai/offer-author/generate", json=body(**kw))
    assert r.status_code == status and r.get_json()["code"] == code


def test_unknown_job(client):
    assert client.get("/ai/offer-author/nope").status_code == 404
