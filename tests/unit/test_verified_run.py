"""The engine-checked whole-offer run (TwoBranchVerify.md): the offer and its test cases in parallel (Send), then the
verifier; on a mismatch one repair of the blocks, never of the cases. The verifier is faked here."""

import json

import pytest

from offer_author.ai import agent
from offer_author.api.text import public
from offer_author.core import verifier
from offer_author.core.settings import Settings
from tests.fakes import ScriptedLLM
from tests.support import finish, golden, offer_main

S07 = golden("S07_every_5th_visit")
FIN = finish(readback="every 5th visit earns a coffee")
CASE = {
    "what": "the 5th visit earns the privilege",
    "member": {"MA_P": {"tier_class": "GOLD"}},
    "bits": [{"header": {"h_bit_date": "2026-11-02T10:00:00", "h_bit_amount": 10, "h_location": "HQ"}, "expect": None}],
}
CASES = [("write_cases", {"scenarios": [CASE]})]
OK = {"ok": True, "compile": {"rules": []}, "scenarios": [{"scenario": 0, "bits": [{}]}], "mismatches": []}
BAD = {
    "ok": False,
    "compile": {"rules": []},
    "scenarios": [{"scenario": 0, "what": CASE["what"], "bits": [{}]}],
    "mismatches": [
        {
            "scenario": 0,
            "what": CASE["what"],
            "bit": 0,
            "header": {"h_bit_date": "2026-11-02T10:00:00", "h_bit_amount": 10, "h_sponsor_id": 1},
            "expect": {"points": 0, "privileges": []},
            "got": {"points": 10, "privileges": []},
            "member_in_segment": None,
        }
    ],
}
SETTINGS = Settings(verifier_url="http://verifier", value_search=False)


@pytest.fixture
def verifier_calls(monkeypatch):
    """The requests sent to the verifier; it answers with the reports queued in `answers` (the last one repeats)."""
    sent, answers = [], []

    def fake(url, body, timeout=120):
        sent.append(body)
        a = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(verifier, "verify", fake)
    return sent, answers


def run(catalog, turns, cases_turns=(CASES,), parts=None, settings=SETTINGS):
    llm, cases_llm = ScriptedLLM(turns), ScriptedLLM(list(cases_turns))
    kw = {"parts": parts} if parts else {}
    res = agent.run("every 5th visit earns a coffee", catalog, llm, settings=settings, cases_llm=cases_llm, **kw)
    return res, llm, cases_llm


def test_cases_are_written_alongside_the_build_and_the_canvases_checked_once(fixture_catalog, verifier_calls):
    sent, answers = verifier_calls
    answers.append(OK)
    res, llm, cases_llm = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN]])
    assert res["status"] == "draft" and len(llm.calls) == 3 and len(cases_llm.calls) == 1
    (body,) = sent
    assert body["rules_xml"] == res["rules"]["xml"] and body["scenarios"][0]["member"] == CASE["member"]
    assert body["offer_main"]["frequency"] and "segment_xml" in body and body["tenant"] == fixture_catalog.tenant
    v = res["diagnostics"]["verify"]
    assert v["ok"] and not v["repaired"] and v["cases"]["scenarios"] == 1
    # the cases branch reads the same knowledge (system blocks) and never the canvases
    assert "Do not build the offer" in json.dumps(cases_llm.calls[0]) and "compose" not in json.dumps(cases_llm.calls)


def test_a_mismatch_sends_one_repair_to_the_blocks_and_the_same_cases_are_checked_again(
    fixture_catalog, verifier_calls
):
    sent, answers = verifier_calls
    answers += [BAD, OK]
    repair = [("compose", {"canvas": "rules", "edits": [{"id": "b1", "fields": {}}]})]
    res, llm, cases_llm = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN], repair, [FIN]])
    assert len(cases_llm.calls) == 1  # the cases are never rewritten
    assert len(sent) == 2 and sent[0]["scenarios"] == sent[1]["scenarios"]
    told = json.dumps(llm.calls[3])
    assert "The test cases are fixed" in told and "engine_gave" in told and "h_sponsor_id" not in told
    assert "rule " not in told.split("did not give")[1][:50]  # transactions and results, no compiled rules
    v = res["diagnostics"]["verify"]
    assert v["ok"] and v["repaired"] and res["status"] == "draft"


def test_a_second_mismatch_is_delivered_with_a_note_for_the_manager(fixture_catalog, verifier_calls):
    sent, answers = verifier_calls
    answers.append(BAD)
    res, _, cases_llm = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN], [FIN]])
    assert len(sent) == 2 and len(cases_llm.calls) == 1 and res["rules"]
    v = res["diagnostics"]["verify"]
    assert not v["ok"] and v["repaired"] and v["mismatches"] == 1
    said = public(res)["by_part"]["rules"]["assumptions"]
    assert any("1 of 1 test transactions did not give what the description implies" in a for a in said)


def test_rules_only_runs_and_runs_without_a_verifier_are_not_checked(fixture_catalog, verifier_calls):
    sent, answers = verifier_calls
    answers.append(OK)
    res, _, cases_llm = run(fixture_catalog, [[("compose", S07)], [FIN]], cases_turns=(), parts=("rules",))
    assert res["status"] == "draft" and not sent and not cases_llm.calls and "verify" not in res["diagnostics"]
    plain = Settings(value_search=False)
    res, _, cases_llm = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN]], (), settings=plain)
    assert res["status"] == "draft" and not sent and not cases_llm.calls


def test_an_unreachable_verifier_leaves_the_offer_as_built(fixture_catalog, verifier_calls):
    _, answers = verifier_calls
    answers.append(verifier.VerifierUnavailableError("verifier at http://verifier: refused"))
    res, llm, _ = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN]])
    assert res["status"] == "draft" and len(llm.calls) == 3 and "refused" in res["diagnostics"]["verify"]["error"]


def test_no_usable_cases_means_no_check(fixture_catalog, verifier_calls):
    sent, answers = verifier_calls
    answers.append(OK)
    res, _, _ = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN]], cases_turns=([],))
    v = res["diagnostics"]["verify"]
    assert res["status"] == "draft" and not sent and v["skipped"] == "no test cases" and v["cases"]["scenarios"] == 0


def test_the_cases_schema_shows_what_a_transaction_gives_and_refuses_other_keys():
    """Full-mode regression (2026-10-31): Expect was a bare $ref in the tool schema, so the model wrote
    {"privilege", "quantity"} and pydantic dropped them: 8 of 26 offers failed their own cases on privileges."""
    from pydantic import ValidationError

    from offer_author.ai.cases import WRITE_CASES, Cases

    shown = json.dumps(WRITE_CASES["input_schema"])
    assert "$ref" not in shown and '"privileges"' in shown and '"points"' in shown
    bit = {"header": {"h_bit_date": "2026-11-02T10:00:00"}, "expect": {"privilege": "13829", "quantity": 1}}
    with pytest.raises(ValidationError):
        Cases.model_validate({"scenarios": [{"what": "x", "member": {}, "bits": [bit]}]})
