"""lookup_values with the real value-search models (ai/value_search.py, sentence-transformers, run locally): phrasings
that share no word with the tenant's label, and the 2 s budget per lookup."""

import os
import time

import pytest

from offer_author.core.settings import Settings
from offer_author.core.tenant.catalog import Catalog
from offer_author.core.tenant.value_search import value_search
from tests.paths import CATALOGS
from tests.support.scaled_catalog import build

pytestmark = pytest.mark.e2e
SAID = {
    "flights": "FLIGHT",
    "air travel": "FLIGHT",
    "plane ticket": "FLIGHT",
    "flying": "FLIGHT",
    "restaurant meal": "DINING",
    "eating out": "DINING",
    "buys something": "PURCHASE",
    "watching videos": "VIDEO-WATCHING",
}
# devintbf2's activity types (139) in words its labels lack: the hosted cases V01-V03 (2026-10-29). bge-reranker-base
# ranked Payments first for every utility-bill phrasing; the case V02 then asked instead of saving.
SAID_BF2 = {
    "utility invoice": "BILL_PAYMENT",
    "utility invoice settled": "BILL_PAYMENT",
    "pays a utility bill": "BILL_PAYMENT",
    "settles an electricity bill": "BILL_PAYMENT",
    "trip booking": "Travel",
    "every trip they book": "Travel",
    "friend recommend referral": "MEMBER_REFERRAL",
    "recommends a friend": "MEMBER_REFERRAL",
    "hotel stay": "STAY",
    "money back for a returned item": "RETURN",
}


@pytest.fixture(scope="module")
def search(devintam1_catalog):
    s = Settings()
    return value_search(s.value_embed_model, s.value_rerank_model).warm(devintam1_catalog)


@pytest.fixture(scope="module")
def devintbf2_catalog():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


@pytest.mark.parametrize("text", SAID)
def test_a_phrasing_without_the_labels_words_finds_the_value(devintam1_catalog, search, text):
    r = devintam1_catalog.lookup("activity", "h_bit_type", text, search=search)
    assert r["candidates"][0]["code"] == SAID[text], r["candidates"][:3]


@pytest.mark.parametrize("text", SAID_BF2)
def test_devintbf2_activity_types_found_by_meaning(devintbf2_catalog, search, text):
    r = devintbf2_catalog.lookup("activity", "h_bit_type", text, search=search)
    assert r["candidates"][0]["code"] == SAID_BF2[text], r["candidates"][:3]


def test_a_lookup_in_5000_skus_stays_inside_2_seconds(search):
    big = build()
    search.warm(big)
    t0 = time.time()
    r = big.lookup("product", "sku", "the 500ml cola", search=search)
    assert r["candidates"] and time.time() - t0 < 2
