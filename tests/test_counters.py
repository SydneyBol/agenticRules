"""Existing-counter guard: reuse only an exact match on a planning offer, otherwise a free name, and list unused counters."""
import base64

from conftest import finish, pts
from offer_author.counters import BolapiCounterSource, Guard, normalize
from offer_author.llm import ScriptedLLM
from offer_author.pipeline import generate
from offer_author.service import create_app

MILESTONE = ("add_milestone", {"measure": "h_bit_amount", "threshold": 1000, "reward": pts(300), "conditions": []})
UNIQUE = ("add_unique_count", {"dimension": "h_location", "n": 3, "reward": pts(300)})


def mto(name, data_type="NUMBER", counter_type="REGULAR", ns=None):
    m = {"id": 1, "name": name, "data_type": data_type, "counter_type": counter_type, "level": "OFFER"}
    if ns:
        m["entity_attribute_map"] = {"Activity": [ns]}
    return m


def run(fixture_catalog, calls, existing, offer_id=4242):
    return generate("...", fixture_catalog, ScriptedLLM([list(calls) + [finish()]]), offer_id=offer_id, existing=existing)


def names(res):
    return [c["name"] for c in res["rules"]["counters"]]


def test_exact_match_on_planning_offer_is_reused_and_old_ones_listed(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Total spend"), mto("300 points given", "TEXT"), mto("AI visit count")]}
    res = run(fixture_catalog, [MILESTONE], existing)
    rep = res["rules"]["counter_report"]
    assert names(res) == ["Total spend", "300 points given"]
    assert rep["reused"] == ["Total spend", "300 points given"] and rep["renamed"] == [] and rep["created"] == []
    assert rep["unused"] == ["AI visit count"]
    assert any("Reuses the offer's existing counter(s): 'Total spend', '300 points given'" in n for n in res["counter_notes"])
    assert any("'AI visit count'" in n and "Not used by these rules" in n for n in res["counter_notes"])


def test_same_name_other_type_gets_a_free_name_and_says_why(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Total spend", "TEXT"), mto("Total spend 2", "TEXT")]}
    res = run(fixture_catalog, [MILESTONE], existing)
    rep = res["rules"]["counter_report"]
    assert names(res)[0] == "Total spend 3"  # "Total spend 2" is taken by the offer too
    assert rep["renamed"] == [{"wanted": "Total spend", "used": "Total spend 3",
                               "why": "the offer's counter is TEXT REGULAR, these rules need NUMBER REGULAR"}]
    assert "Total spend 3" in res["rules"]["xml"] and ">Total spend<" not in res["rules"]["xml"]
    assert any(n.startswith("Counter 'Total spend 3' is new: 'Total spend' already exists") for n in res["counter_notes"])


def test_vector_namespace_must_match_too(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Visits per store", counter_type="VECTOR", ns="h_bit_type")]}
    res = run(fixture_catalog, [UNIQUE], existing)
    assert names(res)[0] == "Visits per store 2"
    assert "per h_bit_type" in res["rules"]["counter_report"]["renamed"][0]["why"]


def test_exact_match_on_a_launched_offer_is_not_reused(fixture_catalog):
    existing = {"offer_status": {"status": {"code": "launched"}}, "counters": [mto("Total spend")]}
    res = run(fixture_catalog, [MILESTONE], existing)
    rep = res["rules"]["counter_report"]
    assert names(res)[0] == "Total spend 2" and rep["reused"] == []
    assert "members may already hold values" in rep["renamed"][0]["why"]


def test_shared_cap_counter_is_renamed_once_for_every_rule(fixture_catalog):
    band = lambda lo, hi, f: {"lower": lo, "lower_inclusive": True, "has_upper": hi is not None, "upper": hi or 0,
                              "upper_inclusive": False, "factor_kind": "multiplier", "factor": f}
    tiers = ("add_tiers", {"measure": "h_bit_amount", "conditions": [], "bands": [band(0, 1000, 2), band(1000, None, 3)]})
    existing = {"offer_status": "planning", "counters": [mto("Points toward 300 cap", "TEXT")]}
    res = run(fixture_catalog, [tiers, ("add_cap", {"max_points": 300})], existing)
    assert res["status"] == "draft", res["diagnostics"]
    assert names(res) == ["Points toward 300 cap 2"]
    assert len(res["rules"]["counter_report"]["renamed"]) == 1


def test_unknown_counters_are_said_not_checked(fixture_catalog):
    res = run(fixture_catalog, [MILESTONE], None)
    assert res["rules"]["counter_report"] == {"checked": False}
    assert "were not checked" in res["counter_notes"][0]


def test_normalize_reads_bolapi_records():
    assert normalize([mto("Visits per store", counter_type="VECTOR", ns="h_location")]) == \
        [{"name": "Visits per store", "data_type": "NUMBER", "counter_type": "VECTOR", "namespace": "h_location"}]
    assert Guard({"offer_status": {"status": "PLANNING"}, "counters": []}).planning


def test_pulsar_endpoint_takes_existing_counters_and_notes_them_on_the_canvas(fixture_catalog):
    llm = ScriptedLLM([[MILESTONE, finish(activity_types=["SPEND"])]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    body = {"prompt": "300 points at 1000 total spend", "sponsor_id": 10, "id": 31, "type": "reward",
            "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]}}
    j = cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json()
    xml = base64.b64decode(j["xml"]).decode()
    assert j["counter_report"]["renamed"][0]["used"] == "Total spend 2"
    assert "Counters:" in xml and "Total spend 2" in xml


def test_live_source_reads_offer_and_counters_with_the_callers_jwt():
    seen = []

    class R:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Http:
        def get(self, url, headers):
            seen.append((url, headers["Authorization"]))
            return R({"status": "planning"} if url.endswith("/offers/9/") else [mto("Total spend")])

    got = BolapiCounterSource("https://t.example", http=Http()).get(9, "JWT abc")
    assert got["offer_status"] == {"status": "planning"} and got["counters"][0]["name"] == "Total spend"
    assert [u for u, _ in seen] == ["https://t.example/bolapi/v1/offers/9/", "https://t.example/bolapi/v1/offers/9/member-tracked-attributes/"]
    assert BolapiCounterSource("https://t.example", http=Http()).get(9, None) is None
