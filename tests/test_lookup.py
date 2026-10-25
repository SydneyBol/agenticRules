"""Value lookup for long lists, duplicate-label questions, and the catalog prompt at scale (offline)."""
import json

import pytest

from conftest import c, finish, pts
from offer_author.llm import ScriptedLLM
from offer_author.pipeline import generate
from scaled_catalog import build


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


def test_lookup_round_then_draft_with_the_looked_up_code(big):
    look = ("lookup_values", {"object": "activity", "attribute": "h_location", "text": "Chennai airport"})
    bonus = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "CHE-AIR")], "or_groups": [], "reward": pts(100)})
    llm = ScriptedLLM([[look], [bonus, finish(activity_types=["SPEND"])]])
    res = generate("100 points at the Chennai airport store", big, llm)
    assert res["status"] == "draft" and res["diagnostics"]["lookup_rounds"] == 1
    assert res["diagnostics"]["lookups"][0]["top"] == "CHE-AIR"
    answer = llm.calls[1][-1]["content"][0]
    assert answer["type"] == "tool_result" and json.loads(answer["content"])["candidates"][0]["code"] == "CHE-AIR"


def test_calls_made_with_a_lookup_are_held_until_the_next_turn(big):
    look = ("lookup_values", {"object": "activity", "attribute": "h_location", "text": "Goa beach road"})
    bonus = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "GOA-BCH")], "or_groups": [], "reward": pts(50)})
    llm = ScriptedLLM([[look, bonus, finish(activity_types=["SPEND"])], [bonus, finish(activity_types=["SPEND"])]])
    res = generate("50 points at Goa beach road", big, llm)
    held = [b for b in llm.calls[1][-1]["content"] if "Not applied yet" in b["content"]]
    assert len(held) == 2 and res["status"] == "draft"


def test_wrong_code_in_a_long_list_gets_the_closest_codes_not_the_whole_list(big):
    bad = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "CHENNAI-AIRPORT")], "or_groups": [], "reward": pts(10)})
    good = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "CHE-AIR")], "or_groups": [], "reward": pts(10)})
    llm = ScriptedLLM([[bad, finish(activity_types=["SPEND"])], [good, finish(activity_types=["SPEND"])]])
    res = generate("10 points at Chennai airport", big, llm)
    first = res["diagnostics"]["issues_first_pass"][0]
    assert "closest" in first and "CHE-AIR" in first and len(first) < 1200 and res["status"] == "draft"


def test_one_of_two_same_named_stores_asks_which(big):
    bonus = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "PUN-CEN")], "or_groups": [], "reward": pts(100)})
    res = generate("100 points at Pune Central", big, ScriptedLLM([[bonus, finish(activity_types=["SPEND"])]]))
    assert res["status"] == "needs_input" and res["rules"]
    assert any("PUN-CEN, PUN-CEN-2" in q for q in res["blocking_questions"])


def test_tier_class_gold_twins_on_devintam1(devintam1_catalog):
    one = ("add_bonus_if", {"conditions": [c("member", "tier_class", "==", "GOLD")], "or_groups": [], "reward": pts(100)})
    both = ("add_bonus_if", {"conditions": [c("member", "tier_class", "in", values=["GOLD", "Gold"])], "or_groups": [], "reward": pts(100)})
    r1 = generate("100 points for Gold members", devintam1_catalog, ScriptedLLM([[one, finish(activity_types=["SPEND"])]]))
    r2 = generate("100 points for Gold members", devintam1_catalog, ScriptedLLM([[both, finish(activity_types=["SPEND"])]]))
    assert any("GOLD, Gold" in q for q in r1["blocking_questions"]) and not r2["blocking_questions"]


def test_naming_the_code_settles_it(devintam1_catalog):
    hq = ("add_bonus_if", {"conditions": [c("activity", "h_location", "==", "HQ")], "or_groups": [], "reward": pts(100)})
    res = generate("100 points at the Chennai HQ store", devintam1_catalog, ScriptedLLM([[hq, finish(activity_types=["SPEND"])]]))
    assert res["status"] == "draft" and not res["blocking_questions"]


def test_lookup_rounds_are_capped(big):
    look = ("lookup_values", {"object": "activity", "attribute": "h_location", "text": "Pune"})
    bonus = ("add_bonus_if", {"conditions": [], "or_groups": [], "reward": pts(1)})
    llm = ScriptedLLM([[look]] * 4 + [[bonus, finish(activity_types=["SPEND"])]])
    res = generate("1 point anywhere", big, llm)
    assert res["diagnostics"]["lookup_rounds"] == 3 and res["status"] == "draft"
