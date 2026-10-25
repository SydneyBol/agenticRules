"""Counters already on the offer: the agent names its counters, code checks each name against the offer (reuse only an
exact match on a planning offer), and the report lists what is reused, created and left unused."""
import base64
import json

import pytest

from tests.support import finish, golden
from offer_author.core.counters import BolapiCounterSource, Guard, normalize
from offer_author.ai.agent import Session, generate
from offer_author.ai.llm import ScriptedLLM
from offer_author.api.service import create_app

pytestmark = pytest.mark.usefixtures("fake_ui")
MILESTONE = ("compile", golden("S08_spend_milestone_once"))  # counters: Total spend (NUMBER), 500 points given (TEXT)
UNIQUE = ("compile", golden("S09_unique_locations"))  # Visits per store: VECTOR per h_location


def mto(name, data_type="NUMBER", counter_type="REGULAR", ns=None):
    m = {"id": 1, "name": name, "data_type": data_type, "counter_type": counter_type, "level": "OFFER"}
    if ns:
        m["entity_attribute_map"] = {"Activity": [ns]}
    return m


def renamed(call, old, new):
    return (call[0], json.loads(json.dumps(call[1]).replace(f'"{old}"', f'"{new}"')))


def run(fixture_catalog, turns, existing, offer_id=4242):
    llm = ScriptedLLM([*turns, [finish()]])
    return generate("...", fixture_catalog, llm, offer_id=offer_id, existing=existing), llm


def problems(llm, k=1):
    return json.loads(llm.calls[-1][2 * k]["content"][0]["content"])["problems"]


def names(res):
    return [c["name"] for c in res["rules"]["counters"]]


def test_exact_match_on_planning_offer_is_reused_and_old_ones_listed(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Total spend"), mto("500 points given", "TEXT"), mto("AI visit count")]}
    res, _ = run(fixture_catalog, [[MILESTONE]], existing)
    rep = res["rules"]["counter_report"]
    assert names(res) == ["Total spend", "500 points given"]
    assert rep["reused"] == ["Total spend", "500 points given"] and rep["renamed"] == [] and rep["created"] == []
    assert rep["unused"] == ["AI visit count"]
    assert any("Reuses the offer's existing counter(s): 'Total spend', '500 points given'" in n for n in res["counter_notes"])
    assert any("'AI visit count'" in n and "Not used by these rules" in n for n in res["counter_notes"])


def test_same_name_other_type_is_sent_back_and_the_model_picks_another(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]}
    res, llm = run(fixture_catalog, [[MILESTONE], [renamed(MILESTONE, "Total spend", "Total spend 2")]], existing)
    assert any("counter 'Total spend' already exists on this offer and the offer's counter is TEXT REGULAR, these rules "
               "need NUMBER REGULAR" in p for p in problems(llm))
    assert names(res)[0] == "Total spend 2" and res["rules"]["counter_report"]["created"] == ["Total spend 2", "500 points given"]
    assert "Total spend 2" in res["rules"]["xml"] and '"Total spend"' not in res["rules"]["xml"]


def test_vector_namespace_must_match_too(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Visits per store", counter_type="VECTOR", ns="h_bit_type")]}
    assert any("per h_bit_type" in p for p in Session(fixture_catalog, 1, existing).run("validate", UNIQUE[1])["problems"])


def test_exact_match_on_a_launched_offer_is_not_reused(fixture_catalog):
    existing = {"offer_status": {"status": {"code": "launched"}}, "counters": [mto("Total spend")]}
    out = Session(fixture_catalog, 1, existing).run("validate", MILESTONE[1])
    assert any("members may already hold values" in p for p in out["problems"])


def test_unknown_counters_are_said_not_checked(fixture_catalog):
    res, _ = run(fixture_catalog, [[MILESTONE]], None)
    assert res["rules"]["counter_report"] == {"checked": False}
    assert "were not checked" in res["counter_notes"][0]


def test_normalize_reads_bolapi_records():
    assert normalize([mto("Visits per store", counter_type="VECTOR", ns="h_location")]) == \
        [{"name": "Visits per store", "data_type": "NUMBER", "counter_type": "VECTOR", "namespace": "h_location"}]
    assert Guard({"offer_status": {"status": "PLANNING"}, "counters": []}).planning


def test_pulsar_endpoint_takes_existing_counters_and_notes_them_on_the_canvas(fixture_catalog):
    llm = ScriptedLLM([[MILESTONE], [renamed(MILESTONE, "Total spend", "Total spend 2")], [finish()]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    body = {"prompt": "500 points at 1000 total spend", "sponsor_id": 10, "id": 31, "type": "reward",
            "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]}}
    j = cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json()
    xml = base64.b64decode(j["xml"]).decode()
    assert "Counters already on this offer" in llm.calls[0][0]["content"][0]["text"]
    assert j["counter_report"]["created"] == ["Total spend 2", "500 points given"] and "Total spend 2" in xml


def test_the_offer_runs_counters_are_checked_when_the_rules_screen_takes_them(fixture_catalog):
    """The whole-offer run names counters before the offer exists; a clash found when the rules are handed out is
    rebuilt as a rules-only run that knows the offer's counters."""
    from tests.support import offer_main
    llm = ScriptedLLM([[offer_main(rules_prompt="500 points at 1000 total spend")], [MILESTONE], [finish()],
                       [renamed(MILESTONE, "Total spend", "Total spend 2")], [finish()]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    prompt = "500 points once a member has spent 1000 in total"
    cl.post("/ai/pulsar/31/prompt/offermain/", json={"prompt": prompt, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": prompt, "category": "offer"})
    body = {"prompt": prompt, "sponsor_id": 10, "id": 31, "type": "reward",
            "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]}}
    j = cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json()
    assert j["counter_report"]["created"] == ["Total spend 2", "500 points given"] and len(llm.calls) == 5


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
