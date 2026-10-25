"""Value lookup for long lists, twin labels, and the catalog prompt at scale (offline)."""
import json

import pytest

import os

from tests.support import GOLDEN, finish
from offer_author.ai.agent import generate
from offer_author.ai.llm import ScriptedLLM
from tests.support.scaled_catalog import build


@pytest.fixture(scope="module")
def big():
    return build()


def test_long_value_lists_are_not_in_the_prompt(big):
    text = big.prompt_text()
    assert "507 values, not listed: find codes with lookup_values" in text  # stores
    assert "5000 values, not listed" in text and "SKU-00001" not in text
    assert "Activity types (h_bit_type values): 250, not listed" in text and "Spend = SPEND" in text
    assert "tier | Tier | LOOKUP | " in text  # short lists stay inline
    assert len(text) < 80000  # ~16k tokens for 1000 attributes


def test_lookup_finds_the_store_and_flags_twins(big):
    r = big.lookup("activity", "h_location", "Chennai airport store")
    assert r["candidates"][0]["code"] == "CHE-AIR" and "ambiguous" not in r
    r = big.lookup("activity", "h_location", "Pune Central")
    assert {a["code"] for a in r["ambiguous"]} == {"PUN-CEN", "PUN-CEN-2"}
    sku = next(k for k, v in big.attr("product", "sku").values.items() if v.startswith("Amul "))
    r = big.lookup("product", "sku", big.attr("product", "sku").values[sku])
    assert r["candidates"][0]["code"] == sku and r["candidates"][0]["score"] == 1.0


def test_lookup_is_a_tool_answered_before_the_next_turn(big):
    look = ("lookup_values", {"object": "activity", "attribute": "h_location", "text": "Chennai airport"})
    llm = ScriptedLLM([[look], [finish(blocking_questions=["test"])]])
    res = generate("100 points at the Chennai airport store", big, llm)
    assert res["diagnostics"]["lookups"][0]["top"] == "CHE-AIR"
    answer = llm.calls[-1][2]["content"][0]
    assert answer["type"] == "tool_result" and json.loads(answer["content"])["candidates"][0]["code"] == "CHE-AIR"


def test_wrong_code_in_a_long_list_gets_the_closest_codes_not_the_whole_list(big):
    from offer_author.core.check import check
    xml = open(os.path.join(GOLDEN, "compiled", "S03_combined_weekend_gold_locations.xml")).read().replace("L1,L2", "CHENNAI-AIRPORT")
    p = [x for x in check(xml, big) if "h_location" in x]
    assert p and "closest: CHE-AIR" in p[0] and len(p[0]) < 1200


def test_tier_class_gold_twins_on_devintam1(devintam1_catalog):
    r = devintam1_catalog.lookup("member", "tier_class", "Gold")
    assert {a["code"] for a in r["ambiguous"]} >= {"GOLD", "Gold"}
