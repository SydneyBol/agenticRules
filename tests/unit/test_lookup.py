"""Value lookup for long lists, twin labels, and the catalog prompt at scale (offline)."""

import json
import os

import pytest

from offer_author.ai.agent import generate
from offer_author.ai.prompt import catalog_text
from tests.fakes import ScriptedLLM
from tests.support import GOLDEN, finish, said
from tests.support.scaled_catalog import build


@pytest.fixture(scope="module")
def big():
    return build()


def test_long_value_lists_are_not_in_the_prompt(big):
    text = catalog_text(big)
    assert "507 values, not listed: find codes with lookup_values" in text  # stores
    assert "5000 values, not listed" in text and "SKU-00001" not in text
    assert (
        "Activity types (h_bit_type values): 250, not listed" in text and "Spend = SPEND" not in text
    )  # no guessed hint
    assert "tier | Tier | LOOKUP | " in text  # short lists stay inline
    assert len(text) < 80000  # ~16k tokens for 1000 attributes


def test_lookup_finds_the_store_and_flags_twins(big):
    r = big.lookup("activity", "h_location", "Chennai airport store")
    assert (
        r["candidates"][0]["code"] == "CHE-AIR" and r["candidates"][0].get("lacks", []) == ["store"] and "tied" not in r
    )
    r = big.lookup("activity", "h_location", "Pune Central")  # facts, not a verdict: the model decides to ask
    assert set(r["tied"]) == {"PUN-CEN", "PUN-CEN-2"} and "note" not in r and "ambiguous" not in r
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
    from offer_author.core.canvas.checks.grammar import check

    xml = (
        open(os.path.join(GOLDEN, "compiled", "S03_combined_weekend_gold_locations.xml"))
        .read()
        .replace("L1,L2", "CHENNAI-AIRPORT")
    )
    p = [x for x in said(check(xml, big)) if "h_location" in x]
    assert p and "closest: CHE-AIR" in p[0] and len(p[0]) < 1200


def test_tier_class_gold_twins_on_devintam1(devintam1_catalog):
    r = devintam1_catalog.lookup("member", "tier_class", "Gold")
    assert any({"GOLD", "Gold"} <= set(g) for g in r.get("same_label", [])) or {"GOLD", "Gold"} <= set(
        r.get("tied", [])
    )


def test_lookup_weights_words_by_the_value_list_not_a_word_list(big):
    r = big.lookup("activity", "h_location", "the Chennai airport store")
    assert r["candidates"][0]["code"] == "CHE-AIR"
    assert "the" in r.get("words_in_no_value", [])  # reported as a fact; no stop-word list in code
    from offer_author.core.tenant import catalog

    assert not hasattr(catalog, "GENERIC_WORDS")


class FakeSearch:
    """Meaning as the test states it: nearest gives the docs holding `near`, rerank scores the ones holding `best` high."""

    def __init__(self, near, best):
        self.near, self.best, self.reranked = near, best, []

    def nearest(self, docs, text, n):
        return [i for i, d in enumerate(docs) if self.near in d][:n]

    def rerank(self, text, docs):
        self.reranked.append(list(docs))
        return [0.9 if self.best in d else 0.1 for d in docs]


def test_bm25_counts_a_rare_word_more():
    from offer_author.core.tenant.catalog import bm25

    s = bm25(["Mall Store", "Airport Store", "Downtown Store"])("airport store")
    assert s[1] > s[0] == s[2] > 0


def test_a_value_found_by_meaning_is_ranked_by_the_cross_encoder(devintam1_catalog):
    """'flights' shares no word with the activity type FLIGHT labelled 'Fly': the words alone never found it."""
    assert devintam1_catalog.lookup("activity", "h_bit_type", "flights")["candidates"] == []
    search = FakeSearch(near="(FLIGHT)", best="(FLIGHT)")
    r = devintam1_catalog.lookup("activity", "h_bit_type", "flights", search=search)
    assert r["candidates"][0] == {"code": "FLIGHT", "label": "Fly", "score": 0.9} and len(r["candidates"]) <= 15
    assert r["words_in_no_value"] == ["flights"]  # still a fact: no value holds the word


def test_the_cross_encoder_scores_bm25s_and_the_embeddings_candidates(big):
    search = FakeSearch(near="Chennai", best="(CHE-AIR)")
    r = big.lookup("activity", "h_location", "airport store", search=search)
    pool = search.reranked[0]
    assert any("Chennai" in d for d in pool) and any("Airport" in d and "Chennai" not in d for d in pool)  # both sides
    assert len(pool) <= 50 and len(pool) == len(set(pool)) and r["candidates"][0]["code"] == "CHE-AIR"
    assert len(r["candidates"]) == 15


def test_a_failing_search_falls_back_to_the_words(devintam1_catalog):
    class Broken(FakeSearch):
        def nearest(self, *a):
            raise RuntimeError("model not loaded")

    r = devintam1_catalog.lookup("member", "tier_class", "Gold", search=Broken("", ""))
    assert r["candidates"] and "score" in r["candidates"][0]


def test_the_session_hands_its_search_to_lookup_values(devintam1_catalog, monkeypatch):
    from offer_author.ai import agent

    search = FakeSearch(near="(DINING)", best="(DINING)")
    monkeypatch.setattr(agent, "searcher", lambda settings: search)
    llm = ScriptedLLM(
        [
            [("lookup_values", {"object": "activity", "attribute": "h_bit_type", "text": "eating out"})],
            [finish(unsupported=[{"phrase": "x", "reason": "y"}])],
        ]
    )
    res = generate("x", devintam1_catalog, llm)
    assert res["diagnostics"]["lookups"][0]["top"] == "DINING" and search.reranked


def test_exact_twins_stay_tied_under_the_cross_encoder(big):
    """The cross-encoder scored the two 'Pune Central' stores 0.991 and 0.957: no longer tied, the model used both
    instead of asking (scale case SC03, 2026-10-29). An exact code or label match is certain, as in the words' ranking."""

    class Uneven(FakeSearch):
        def rerank(self, text, docs):
            return [0.99 - 0.03 * i for i in range(len(docs))]

    r = big.lookup("activity", "h_location", "Pune Central", search=Uneven("Pune", ""))
    assert [c["code"] for c in r["candidates"][:2]] == ["PUN-CEN", "PUN-CEN-2"] and r["tied"] == [
        "PUN-CEN",
        "PUN-CEN-2",
    ]


def test_a_lower_activity_type_limit_leaves_the_types_to_lookup_values(devintam1_catalog):
    """inline_activity_types (OFFER_AUTHOR_INLINE_ACTIVITY_TYPES): a tenant with fewer types than the default 200 can be
    run as one with more, so its BIT Types are found with lookup_values (hosted value-search cases, 2026-10-29)."""
    from offer_author.ai import agent
    from offer_author.core.settings import Settings

    n = len(devintam1_catalog.activity_types)
    listed = agent._system(devintam1_catalog, None, Settings())[2]["text"]
    hidden = agent._system(
        devintam1_catalog, None, Settings.from_env({"OFFER_AUTHOR_INLINE_ACTIVITY_TYPES": str(n - 1)})
    )[2]["text"]
    assert "FLIGHT=Fly" in listed and "not listed: find codes with lookup_values (activity, h_bit_type)" not in listed
    assert f"Activity types (h_bit_type values): {n}, not listed" in hidden and "FLIGHT=Fly" not in hidden


class Scored(FakeSearch):
    """The cross-encoder's probabilities as the test states them, by a doc's code."""

    def __init__(self, scores):
        super().__init__("", "")
        self.scores = scores

    def nearest(self, docs, text, n):
        return [i for i, d in enumerate(docs) if any(f"({c})" in d for c in self.scores)][:n]

    def rerank(self, text, docs):
        return [next((s for c, s in self.scores.items() if f"({c})" in d), 0.0) for d in docs]


def test_weak_cross_encoder_scores_are_not_all_tied(devintam1_catalog):
    """bge-reranker-v2-m3 scores weak matches near 0 (0.003 against 0.001): the words' fixed 0.02 margin called 15
    values tied and the model asked (hosted case V02, 2026-10-29). Ties are judged on log-odds and scores keep 3
    significant figures."""
    r = devintam1_catalog.lookup(
        "activity", "h_bit_type", "utility invoice", search=Scored({"FLIGHT": 0.003, "DINING": 0.001})
    )
    assert [c["score"] for c in r["candidates"][:2]] == [0.003, 0.001] and "tied" not in r


def test_close_cross_encoder_scores_are_tied(devintam1_catalog):
    r = devintam1_catalog.lookup(
        "activity", "h_bit_type", "a night out", search=Scored({"FLIGHT": 0.5, "DINING": 0.45})
    )
    assert r["tied"] == ["FLIGHT", "DINING"]


@pytest.fixture(scope="module")
def airline():
    """devintbf2 as saved: 8 airports (3 active), their attribute library and the city, state and country lists."""
    from offer_author.core.tenant.catalog import Catalog
    from tests.paths import CATALOGS

    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


def test_airport_values_are_looked_up_and_name_the_airports_that_have_them(airline):
    """2026-11-10 review (3.3 item 4): the catalog text said "332 values, not listed: find codes with lookup_values"
    for airport.city, and the tool took activity, member or product only. The program has two cities named
    Bengaluru and two countries named India; the airport setup tells which one the airports are in."""
    from pydantic import ValidationError

    from offer_author.ai import tools

    assert "city | City | LOV | 332 values, not listed: find codes with lookup_values" in catalog_text(airline)
    for obj in ("airport", "airport_pair"):
        assert tools.LookupValues(object=obj, attribute="city", text="Bengaluru").object == obj
    with pytest.raises(ValidationError):
        tools.LookupValues(object="location", attribute="city", text="Bengaluru")
    city = airline.lookup("airport", "city", "Bengaluru")
    assert {c["code"]: c["airports"] for c in city["candidates"]} == {"131": [], "190": ["BLR"]}
    assert city["same_label"] == [["131", "190"]]
    country = airline.lookup("airport", "country", "India")
    assert {c["code"]: c["airports"] for c in country["candidates"]} == {"255": [], "6": ["BLR", "VAR"]}
    name = airline.lookup("airport", "airport_name", "Zurich")["candidates"][0]
    assert (name["code"], name["airports"]) == ("ZRH", ["ZRH"])
    # an inactive airport is in the screen's list and in no transaction's data
    assert {c["code"]: c["airports"] for c in airline.lookup("airport", "airport_code", "HYD")["candidates"]} == {
        "HYD": []
    }
    # a lookup type's values (read since 2026-11-10): the manager's "North" zone is the code `start`
    zone = airline.lookup("airport", "zone", "North")["candidates"][0]
    assert zone == {"code": "start", "label": "North", "score": 1.0, "airports": ["BLR", "VAR"]}
    # other objects' values carry no such fact; an attribute without a value list still says so
    assert "airports" not in airline.lookup("member", "tier_class", "Gold")["candidates"][0]
    assert said([airline.lookup("airport", "latitude", "13")["error"]]) == ["airport.latitude has no value list"]
    # the same fact when the values are found by meaning and scored by the cross-encoder
    found = airline.lookup("airport", "city", "Bangalore", search=FakeSearch(near="(190)", best="(190)"))
    assert found["candidates"][0] == {"code": "190", "label": "Bengaluru", "score": 0.9, "airports": ["BLR"]}


def test_an_airport_lookup_is_a_tool_call_like_any_other(airline):
    look = ("lookup_values", {"object": "airport", "attribute": "city", "text": "Bengaluru"})
    llm = ScriptedLLM([[look], [finish(blocking_questions=["test"])]])
    res = generate("Double points on flights from Bengaluru", airline, llm)
    assert res["diagnostics"]["lookups"][0] == {"attribute": "airport.city", "text": "Bengaluru", "top": "131"}
    answer = json.loads(llm.calls[-1][2]["content"][0]["content"])
    assert [c for c in answer["candidates"] if c["airports"]] == [
        {"code": "190", "label": "Bengaluru", "score": 1.0, "airports": ["BLR"]}
    ]


def test_an_airport_value_that_is_not_a_code_is_a_compose_problem(airline):
    import xml.etree.ElementTree as ET

    from offer_author.core.canvas.checks import grammar

    def codes(value):
        block = (
            f'<block xmlns="{grammar.NS[1:-1]}" type="if_generic"><mutation object="airport" attribute="city" '
            f'operators="==" value="{value}"></mutation></block>'
        )
        return grammar.codes(ET.fromstring(block), airline)

    assert codes("190") == []
    (bad,) = codes("Bengaluru")
    assert bad.code == "values_not_codes" and set(bad.fact("closest")) == {"131", "190"}
