"""The shared plan (SharedPlan.md): fanned out with Offer Main (Send) from the description; the build run (in its context)
and the test cases (which cover every plan id) both read it; Offer Main does not wait for it; the cases never see the
canvases."""

import json

import pytest

from offer_author.ai import agent
from offer_author.ai import plan as plans
from offer_author.core import verifier
from offer_author.core.settings import Settings
from tests.fakes import ScriptedLLM
from tests.support import finish, golden, offer_main

S07 = golden("S07_every_5th_visit")
FIN = finish(readback="every 5th visit earns a coffee")
SETTINGS = Settings(verifier_url="http://verifier", value_search=False)
OK = {"ok": True, "compile": {"rules": []}, "scenarios": [{"scenario": 0, "bits": [{}]}], "mismatches": []}
PLAN = {
    "clauses": [{"id": "C1", "text": "every 5th visit earns a coffee", "gives": "a coffee"}],
    "readings": ["a visit is any spend"],
    "inputs": [
        {
            "id": "I1",
            "source": "activity",
            "code": "h_bit_amount",
            "use": "any amount is a visit",
            "clauses": ["C1"],
            "edges": [],
            "kinds": [],
        },
        {
            "id": "I2",
            "source": "history",
            "use": "visits so far",
            "clauses": ["C1"],
            "edges": [{"id": "E1", "at": "the 5th visit", "inside": "5th", "outside": "4th"}],
            "kinds": [],
        },
    ],
    "memory": [{"id": "M1", "what": "visits so far", "starts_again": "after each coffee", "clauses": ["C1"]}],
}


def case(covers, what="the 5th visit earns the coffee"):
    bit = {"header": {"h_bit_date": "2026-11-02T10:00:00", "h_bit_amount": 10, "h_location": "HQ"}, "expect": None}
    return {"aim": "positive", "what": what, "member": {"MA_P": {}}, "bits": [bit], "covers": covers}


@pytest.fixture
def verifier_calls(monkeypatch):
    sent = []

    def fake(url, body, timeout=120):
        sent.append(body)
        return OK

    monkeypatch.setattr(verifier, "verify", fake)
    return sent


def run(catalog, case_turns, settings=SETTINGS, parts=None):
    llm, cases_llm = ScriptedLLM([[offer_main()], [("compose", S07)], [FIN]]), ScriptedLLM(case_turns)
    kw = {"parts": parts} if parts else {}
    res = agent.run("every 5th visit earns a coffee", catalog, llm, settings=settings, cases_llm=cases_llm, **kw)
    return res, llm, cases_llm


def test_the_plan_is_written_alongside_offer_main_and_both_the_build_and_the_cases_read_it(
    fixture_catalog, verifier_calls
):
    covered = [("write_cases", {"scenarios": [case(["C1", "E1", "M1"])]})]
    res, llm, cases_llm = run(fixture_catalog, [[("write_plan", PLAN)], covered])
    assert len(cases_llm.calls) == 2 and "Do not build the offer and do not write its test cases" in json.dumps(
        cases_llm.calls[0]
    )
    offer_main_intro, build_intro = json.dumps(llm.calls[0]), json.dumps(llm.calls[1])
    cases_task = json.dumps(cases_llm.calls[1])
    for told in (build_intro, cases_task):  # the same plan, rendered once, in both branches
        assert "E1: the 5th visit" in told and "a visit is any spend" in told and "M1" in told
    assert "The offer's plan" not in offer_main_intro  # Offer Main answers at once, alongside the plan
    assert "compose" not in cases_task  # the cases still never see the canvases
    p = res["diagnostics"]["plan"]
    assert (p["clauses"], p["inputs"], p["edges"], p["memory"], p["turns"], p["problems"]) == (1, 2, 1, 1, 1, [])
    v = res["diagnostics"]["verify"]
    assert (
        v["ok"]
        and v["evidence"]["plan"] == plans.Plan.model_validate(PLAN).model_dump()
        and v["cases"]["scenarios"] == 1
    )
    assert verifier_calls[0]["scenarios"][0]["covers"] == ["C1", "E1", "M1"]


def test_plan_ids_no_scenario_covers_get_one_more_cases_turn_and_the_first_cases_are_kept(
    fixture_catalog, verifier_calls
):
    first = [("write_cases", {"scenarios": [case(["C1"])]})]
    more = [("write_cases", {"scenarios": [case(["E1", "M1"], what="the 4th visit gives nothing")]})]
    res, _, cases_llm = run(fixture_catalog, [[("write_plan", PLAN)], first, more])
    assert len(cases_llm.calls) == 3 and "E1, M1" in json.dumps(cases_llm.calls[2])
    assert [s["what"] for s in verifier_calls[0]["scenarios"]] == [
        "the 5th visit earns the coffee",
        "the 4th visit gives nothing",
    ]
    info = res["diagnostics"]["verify"]["cases"]
    assert info["scenarios"] == 2


def test_an_unknown_code_gets_one_more_plan_turn_then_the_plan_goes_without_it(fixture_catalog):
    bad = json.loads(json.dumps(PLAN))
    bad["inputs"][0]["code"] = "h_store_grade"
    llm = ScriptedLLM([[("write_plan", bad)], [("write_plan", bad)]])
    plan, info = plans.write(llm, [], "every 5th visit earns a coffee", fixture_catalog)
    assert info["turns"] == 2 and "h_store_grade is not a activity attribute" in json.dumps(llm.calls[1])
    assert [i["id"] for i in plan["inputs"]] == ["I2"] and info["problems"]


def test_ids_used_twice_and_unknown_clauses_are_problems(fixture_catalog):
    bad = json.loads(json.dumps(PLAN))
    bad["inputs"][1]["id"] = "I1"
    bad["memory"][0]["clauses"] = ["C9"]
    problems = plans.check(plans.Plan.model_validate(bad), fixture_catalog)
    assert "id I1 is used more than once" in problems and "M1: clause C9 is not in the plan" in problems


def test_without_a_plan_the_run_goes_on_as_before(fixture_catalog, verifier_calls):
    cases = [("write_cases", {"scenarios": [case([])]})]
    res, _, cases_llm = run(fixture_catalog, [[], cases])  # the plan turn wrote nothing
    assert res["status"] == "draft" and res["diagnostics"]["verify"]["ok"]
    assert "The offer's plan" not in json.dumps(cases_llm.calls[1])
    assert res["diagnostics"]["plan"]["problems"] == []


def test_the_off_switch_and_the_rules_only_run_write_no_plan(fixture_catalog, verifier_calls):
    cases = [("write_cases", {"scenarios": [case([])]})]
    res, _, cases_llm = run(fixture_catalog, [cases], settings=Settings(**{**SETTINGS.__dict__, "shared_plan": False}))
    assert len(cases_llm.calls) == 1 and "plan" not in res["diagnostics"]
    llm, cases_llm = ScriptedLLM([[("compose", S07)], [FIN]]), ScriptedLLM([])
    res = agent.run("every 5th visit earns a coffee", fixture_catalog, llm, parts=("rules",), settings=SETTINGS)
    assert res["status"] == "draft" and "plan" not in res["diagnostics"] and cases_llm.calls == []
