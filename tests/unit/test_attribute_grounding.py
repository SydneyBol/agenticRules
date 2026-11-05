"""Attribute grounding (AttributeGrounding.md): the data the description's terms refer to, retrieved in code and
confirmed by the model once, then read as a hint by both the build and the test cases; never their logic.

Each test names the design's edge case (E1-E18) it covers.
"""

import json
import os

import pytest

from offer_author.ai import agent
from offer_author.ai import attributes as grounding
from offer_author.ai.model import LLMError
from offer_author.api.pulsar import runs as runs_mod
from offer_author.api.pulsar.jobs import Job
from offer_author.core import verifier
from offer_author.core.settings import Settings
from offer_author.core.tenant import attribute_search
from tests.fakes import ScriptedLLM
from tests.paths import GOLDEN
from tests.support import finish, golden, offer_main

S07 = golden("S07_every_5th_visit")
S07_XML = open(os.path.join(GOLDEN, "compiled", "S07_every_5th_visit.xml")).read()
FIN = finish(readback="every 5th visit earns a coffee")
ON = Settings(value_search=False, attributes=True)
CHECKED = Settings(value_search=False, attributes=True, verifier_url="http://verifier")
OK = {"ok": True, "compile": {"rules": []}, "scenarios": [{"scenario": 0, "bits": [{}]}], "mismatches": []}
PROMPT = "every 5th visit at the Mall Store earns a coffee for Gold members"
MALL = {"words": "Mall Store", "object": "activity", "code": "h_location", "values": ["L2"]}
GOLD = {"words": "Gold", "object": "member", "code": "tier", "values": ["GOLD"]}
CONFIRM = ("confirm_attributes", {"terms": [MALL, GOLD], "ambiguous": []})
HINT = "The data the description's terms refer to"


def case():
    bit = {"header": {"h_bit_date": "2026-11-02T10:00:00", "h_bit_amount": 10, "h_location": "L2"}, "expect": None}
    return {"aim": "positive", "what": "the 5th visit earns the coffee", "member": {"MA_P": {}}, "bits": [bit]}


CASES = ("write_cases", {"scenarios": [case()]})


@pytest.fixture
def verifier_calls(monkeypatch):
    sent = []
    monkeypatch.setattr(verifier, "verify", lambda url, body, timeout=120: sent.append(body) or OK)
    return sent


class Recording(ScriptedLLM):
    """A scripted model that also records each request's system blocks and tools."""

    def __init__(self, turns):
        super().__init__(turns)
        self.systems, self.tools = [], []

    def turn(self, system, messages, tools=None, on_tool=None):
        self.systems.append(json.dumps(system, sort_keys=True))
        self.tools.append([t["name"] for t in tools or []])
        return super().turn(system, messages, tools, on_tool)


def said(llm, i):
    return json.dumps(llm.calls[i])


# ------------------------------------------------------------------ retrieval (code, no model)
def test_the_words_find_their_attributes_with_the_values_they_name(fixture_catalog):
    found = {(c["object"], c["code"]): c for c in attribute_search.find(fixture_catalog, "Spend at the Mall Store")}
    assert found[("activity", "h_location")]["values"] == [{"code": "L2", "label": "Mall Store"}]
    assert found[("activity", "h_bit_type")]["values"] == [{"code": "SPEND", "label": "Spend"}]


def test_the_tier_programme_is_searched_too(fixture_catalog):
    found = attribute_search.find(fixture_catalog, "Bronze members earn double")
    tier = [c for c in found if c["object"] == "tier"]
    assert tier and tier[0]["code"] == "Elite_Levels" and tier[0]["values"] == [{"code": "Bronze", "label": "Bronze"}]


def test_numbers_and_function_words_name_no_attribute(fixture_catalog):
    assert not [c for c in attribute_search.find(fixture_catalog, "5 of the") if c.get("values")]
    texts = [d.text for d in attribute_search._docs(fixture_catalog)]
    assert attribute_search._phrases("50 of the birthday", texts) == ["birthday"]


def test_the_last_words_always_get_a_phrase():
    phrases = attribute_search._phrases("alpha bravo charlie delta echo foxtrot golf", ["x"])
    assert phrases[-1].endswith("golf")


def test_saved_rules_ground_the_catalog_attributes_they_read_never_counters_or_computed_values():
    xml = (
        '<block type="activity_criteria" id="a"><mutation object="activity" attribute="h_location"></mutation>'
        '<block type="activity_criteria" id="b"><mutation object="member" attribute_level="MA_P" '
        'attribute="tier"></mutation>'
        '<block type="activity_criteria" id="c"><mutation object="member" attribute_level="MA_O" '
        'attribute="visits"></mutation>'
        '<block type="member_attribute" id="d"><mutation attribute_type_input="MA_A" attribute="key"></mutation>'
        '<block type="action" id="e"><mutation mta_attribute="wk"></mutation>'
        '<block type="member_attribute" id="f"><mutation attribute_type_input="MA_P" attribute="wk"></mutation>'
    )
    assert attribute_search.read_by(xml) == [("activity", "h_location"), ("member", "tier")]


# ------------------------------------------------------------------ the agent's checks
def test_e1_a_search_turn_then_the_confirmation_and_every_call_answered(fixture_catalog):
    llm = ScriptedLLM(
        [[("find_attributes", {"words": "date of birth"}), ("find_attributes", {"words": "store"})], [CONFIRM]]
    )
    attrs, info = grounding.ground(llm, [], PROMPT, fixture_catalog)
    assert attrs["terms"] == [MALL, GOLD] and (info["turns"], info["searches"]) == (2, 2)
    asked = [b["id"] for b in llm.calls[1][-2]["content"] if b.get("type") == "tool_use"]
    answered = [b["tool_use_id"] for b in llm.calls[1][-1]["content"] if b.get("type") == "tool_result"]
    assert len(asked) == 2 and answered == asked and "date_of_birth" in said(llm, 1)


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        ({**GOLD, "object": "member", "code": "visits"}, "member.visits is not in the tenant catalog"),  # E12 counter
        ({**GOLD, "values": ["PLATINUM"]}, "PLATINUM is not a value of member.tier"),  # E12 value
        ({**GOLD, "words": "purchases over 50"}, "not the description's own words"),  # E13 logic as a term
    ],
)
def test_e12_e13_data_the_catalog_or_the_description_lacks_is_sent_back_then_fixed(fixture_catalog, bad, why):
    llm = ScriptedLLM([[("confirm_attributes", {"terms": [bad]})], [CONFIRM]])
    attrs, info = grounding.ground(llm, [], PROMPT, fixture_catalog)
    assert why in said(llm, 1) and attrs["terms"] == [MALL, GOLD] and info["turns"] == 2


def test_e13_the_schema_has_no_place_for_logic(fixture_catalog):
    with_logic = {"terms": [{**MALL, "condition": ">= 50"}]}
    llm = ScriptedLLM([[("confirm_attributes", with_logic)], [("confirm_attributes", with_logic)], []])
    attrs, info = grounding.ground(llm, [], PROMPT, fixture_catalog)
    assert attrs is None and "Extra inputs are not permitted" in json.dumps(info["problems"])


def test_e14_e15_activity_types_and_line_attributes_are_data(fixture_catalog):
    prompt = "a stay with 3 or more line quantity"
    terms = [
        {"words": "stay", "object": "activity", "code": "h_bit_type", "values": ["STAY"]},
        {"words": "line quantity", "object": "activity", "code": "l_quantity", "values": []},
    ]
    attrs, _ = grounding.ground(ScriptedLLM([[("confirm_attributes", {"terms": terms})]]), [], prompt, fixture_catalog)
    assert attrs["terms"] == terms


# ------------------------------------------------------------------ the whole-offer job
def test_the_confirmed_attributes_reach_the_build_and_the_cases_and_never_offer_main(fixture_catalog, verifier_calls):
    llm = ScriptedLLM([[offer_main()], [("compose", S07)], [FIN]])
    side = ScriptedLLM([[CONFIRM], [CASES]])  # the attribute step, then the cases (the side runs' client)
    res = agent.run(PROMPT, fixture_catalog, llm, settings=CHECKED, cases_llm=side)
    assert HINT not in said(llm, 0)  # Offer Main answers at once, without waiting for the attributes
    build, cases = said(llm, 1), said(side, 1)
    for told in (build, cases):
        assert HINT in told and "activity.h_location = L2 (Mall Store)" in told and "member.tier = GOLD" in told
    assert "compose" not in cases  # E18: the cases never see the canvases
    a = res["diagnostics"]["attributes"]
    assert a["value"]["terms"] == [MALL, GOLD] and a["prompt"] == PROMPT and res["status"] == "draft"


def test_e2_an_ambiguity_the_description_leaves_open_is_asked_before_either_branch(fixture_catalog, verifier_calls):
    open_ = {
        "words": "Gold",
        "candidates": [
            {"object": "member", "code": "tier", "values": ["GOLD"]},
            {"object": "tier", "code": "Elite_Levels", "values": []},
        ],
        "question": "Gold: the member's tier GOLD, or an Elite level?",
    }
    llm = ScriptedLLM([[offer_main()]])
    side = ScriptedLLM([[("confirm_attributes", {"terms": [MALL], "ambiguous": [open_]})]])
    res = agent.run(PROMPT, fixture_catalog, llm, settings=CHECKED, cases_llm=side)
    assert res["status"] == "needs_input" and res["rules"] is None and res["offer_main"]["offer_name"] == "Test offer"
    assert res["by_part"]["rules"]["blocking_questions"] == [open_["question"]]
    assert len(llm.calls) == 1 and len(side.calls) == 1 and not verifier_calls  # no build, no cases, no check


def test_e2_an_ambiguity_the_description_settles_is_the_hint_and_an_assumption(fixture_catalog):
    settled = {
        "words": "Gold",
        "candidates": [
            {"object": "tier", "code": "Elite_Levels", "values": []},
            {"object": "member", "code": "tier", "values": ["GOLD"]},
        ],
        "resolved": 1,
        "reason": "the description speaks of members' tier",
    }
    llm = ScriptedLLM([[("confirm_attributes", {"terms": [MALL], "ambiguous": [settled]})], [("compose", S07)], [FIN]])
    res = agent.run(PROMPT, fixture_catalog, llm, parts=("rules",), settings=ON)
    assert "member.tier = GOLD (GOLD) (not tier.Elite_Levels" in said(llm, 1)
    note = '"Gold" is read as member.tier = GOLD (GOLD): the description speaks of members\' tier.'
    assert note in res["assumptions"] and note in res["by_part"]["rules"]["assumptions"]


def test_e3_a_failed_attribute_step_leaves_the_job_as_it_was(fixture_catalog):
    llm = ScriptedLLM([LLMError("throttled"), [("compose", S07)], [FIN]])
    res = agent.run(PROMPT, fixture_catalog, llm, parts=("rules",), settings=ON)
    assert res["status"] == "draft" and res["rules"] and "throttled" in res["diagnostics"]["attributes"]["error"]
    assert HINT not in said(llm, 1)


def test_e5_no_offer_main_ends_the_job_and_drops_the_attributes(fixture_catalog, verifier_calls):
    llm, side = ScriptedLLM([[], [], []]), ScriptedLLM([[CONFIRM]])
    res = agent.run(PROMPT, fixture_catalog, llm, settings=CHECKED, cases_llm=side)
    assert res["rules"] is None and "attributes" not in res["diagnostics"] and not verifier_calls


# ------------------------------------------------------------------ rules and segment jobs, reuse
def test_e9_a_rules_job_with_nothing_earlier_confirms_its_attributes_first(fixture_catalog):
    llm = ScriptedLLM([[CONFIRM], [("compose", S07)], [FIN]])
    res = agent.run(PROMPT, fixture_catalog, llm, parts=("rules",), settings=ON)
    assert "confirm_attributes" in said(llm, 0) and HINT in said(llm, 1) and res["status"] == "draft"
    assert res["diagnostics"]["attributes"]["value"]["terms"] == [MALL, GOLD]


def test_e7_an_edit_that_says_nothing_new_reuses_the_offers_attributes(fixture_catalog):
    stored = {"value": {"terms": [MALL, GOLD], "ambiguous": []}, "prompt": PROMPT}
    llm = ScriptedLLM([[("compose", S07)], [FIN]])  # no attribute turn at all
    edited = PROMPT.replace("5th", "6th")
    res = agent.run(edited, fixture_catalog, llm, parts=("rules",), settings=ON, attributes=stored)
    assert HINT in said(llm, 0) and res["diagnostics"]["attributes"]["reused"] is True


def test_e8_an_edit_with_new_words_starts_the_agent_from_the_offers_attributes(fixture_catalog):
    stored = {"value": {"terms": [MALL, GOLD], "ambiguous": []}, "prompt": PROMPT}
    llm = ScriptedLLM([[CONFIRM], [("compose", S07)], [FIN]])
    agent.run(
        PROMPT + " at the Harbour Store too", fixture_catalog, llm, parts=("rules",), settings=ON, attributes=stored
    )
    assert "Already confirmed for this offer" in said(llm, 0) and "activity.h_location = L2" in said(llm, 0)


def test_e10_saved_rules_edited_in_place_start_from_what_they_read(fixture_catalog):
    llm = ScriptedLLM([[("confirm_attributes", {"terms": [MALL]})]])
    attrs, _ = agent._grounding("add a weekly cap at the Mall Store", fixture_catalog, llm, ON, None, saved_xml=S07_XML)
    first = said(llm, 0)
    assert "Already confirmed for this offer" in first and attrs == {"terms": [MALL], "ambiguous": []}
    assert all(f"{o}.{c}" in first for o, c in attribute_search.read_by(S07_XML))


def test_e11_a_segment_job_confirms_its_attributes_too(fixture_catalog):
    llm = ScriptedLLM([[("confirm_attributes", {"terms": [GOLD]})], []])
    agent.run(
        "Gold members", fixture_catalog, llm, parts=("segment",), settings=Settings(**{**ON.__dict__, "max_turns": 1})
    )
    assert "confirm_attributes" in said(llm, 0) and HINT in said(llm, 1)


def test_e6_e8_the_rules_api_hands_an_edited_prompt_the_offer_jobs_attributes(fixture_catalog):
    r = runs_mod.Runs(ScriptedLLM([]), lambda s, a, auth: fixture_catalog)
    found = {"value": {"terms": [MALL], "ambiguous": []}, "prompt": PROMPT}
    offer = Job(prompt=PROMPT, state="done", result={"status": "draft", "diagnostics": {"attributes": found}})
    r.store.jobs[r.store.key("offer", PROMPT)] = offer
    r.store.offer_of["7"] = r.store.key("offer", PROMPT)
    got = []
    r.rules_only = lambda body, prompt, auth, ctx, existing, attributes=None: got.append(attributes) or {"status": "x"}
    assert r.rules_job({"id": 7}, PROMPT, None, lambda oj: True) is offer  # E6: the same prompt is the offer job
    r.rules_job({"id": 7}, "every 6th visit earns a coffee", None, lambda oj: True).wait(5)
    assert got == [found]
    assert runs_mod.confirmed(Job(state="running")) is None


# ------------------------------------------------------------------ the cached prefix
def test_e17_the_attribute_step_and_its_hint_never_change_the_cached_system_prefix(fixture_catalog):
    llm = Recording([[CONFIRM], [("compose", S07)], [FIN]])
    agent.run(PROMPT, fixture_catalog, llm, parts=("rules",), settings=ON)
    plain = Recording([[("compose", S07)], [FIN]])
    agent.run(PROMPT, fixture_catalog, plain, parts=("rules",), settings=Settings(value_search=False))
    assert len(set(llm.systems + plain.systems)) == 1
    assert llm.tools[0] == ["find_attributes", "confirm_attributes"]


def test_the_service_log_has_the_attribute_steps_own_line(caplog):
    # 2026-11-05 live check 1: only the build's usage reached the job line, so the step's cost and time were unknown
    from offer_author.api.pulsar import jobs

    found = {"s": 4.2, "turns": 2, "input_tokens": 120, "value": {"terms": [MALL]}, "prompt": PROMPT}
    with caplog.at_level("INFO", logger="offer_author.pulsar"):
        jobs._log_finished(("7", "k"), {"status": "draft", "diagnostics": {"attributes": found}})
    line = next(r.getMessage() for r in caplog.records if "pulsar attributes offer=7" in r.getMessage())
    assert json.loads(line.split(" ", 3)[3]) == found
