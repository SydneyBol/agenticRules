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
    "aim": "positive",
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
    assert body["locations"] == sorted(fixture_catalog.attributes["activity"]["h_location"].values)  # unknown → 406
    v = res["diagnostics"]["verify"]
    assert v["ok"] and not v["repaired"] and v["cases"]["scenarios"] == 1
    assert (v["cases"]["positive"], v["cases"]["negative"], v["cases"]["bits"]) == (1, 0, 1)
    assert v["evidence"]["cases"] == body["scenarios"] and "repair" not in v  # kept without a repair too
    assert v["evidence"]["checks"] == [[{"scenario": 0, "bit": None, "header": {}}]]
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
    # the repair's evidence: the cases, what the first check found, the message sent, the canvas it started from
    ev = v["repair"]
    assert v["evidence"]["cases"] == sent[0]["scenarios"] and ev["rules_xml_before"] == sent[0]["rules_xml"]
    assert len(v["evidence"]["checks"]) == 2  # both checks' transactions with the engine's answers
    assert ev["first_check"][0]["got"] == {"points": 10, "privileges": []} and json.dumps(ev["message"])[1:-1] in told
    assert ev["second_check"] == []


def test_the_cases_and_repair_evidence_are_logged_on_their_own_lines_and_kept_out_of_the_job_line(caplog):
    from offer_author.api.pulsar.jobs import _log_finished, job_summary

    ev = {"cases": [CASE], "checks": [[{"scenario": 0, "bit": 0, "got": {"points": 10}, "agree": False}]]}
    verify = {"ok": False, "repaired": True, "repair": {"message": "x"}, "evidence": ev}
    res = {"status": "draft", "diagnostics": {"verify": verify}}
    assert '"repair"' not in job_summary(res) and '"evidence"' not in job_summary(res)
    assert '"repaired":true' in job_summary(res)
    with caplog.at_level("INFO"):
        _log_finished(("1", "rules"), res)
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("pulsar cases offer=1 ") and '"got":{"points":10}' in m for m in lines)
    assert any(m.startswith("pulsar repair offer=1 ") for m in lines)


def test_a_second_mismatch_is_delivered_with_a_note_for_the_manager(fixture_catalog, verifier_calls):
    sent, answers = verifier_calls
    answers.append(BAD)
    res, _, cases_llm = run(fixture_catalog, [[offer_main()], [("compose", S07)], [FIN], [FIN]])
    assert len(sent) == 2 and len(cases_llm.calls) == 1 and res["rules"]
    v = res["diagnostics"]["verify"]
    assert not v["ok"] and v["repaired"] and v["mismatches"] == 1
    assert len(v["repair"]["first_check"]) == len(v["repair"]["second_check"]) == 1  # both checks kept
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
        Cases.model_validate({"scenarios": [{"aim": "positive", "what": "x", "member": {}, "bits": [bit]}]})


def test_each_case_is_positive_or_negative_and_none_is_written_for_its_own_sake():
    """User (2026-10-31): the cases aim at positive and negative outcomes, without unnecessary ones."""
    from pydantic import ValidationError

    from offer_author.ai.cases import TASK, WRITE_CASES, Cases

    assert '"positive"' in json.dumps(WRITE_CASES["input_schema"]) and '"negative"' in json.dumps(
        WRITE_CASES["input_schema"]
    )
    task = " ".join(TASK.split())
    assert "Give each clause one positive and one negative scenario" in task
    assert "Leave out any scenario that shows only what another one already shows" in task
    with pytest.raises(ValidationError):
        Cases.model_validate({"scenarios": [{"aim": "boundary", "what": "x", "member": {}, "bits": []}]})


def test_a_case_member_has_no_offer_counters_so_counters_come_from_its_transactions():
    """Regenerated cases (2026-10-31): the only two that seeded counters guessed names the builder never chose
    ("points_earned" for "Bonus Points Earned"), so the engine started from zero and both disagreed."""
    from pydantic import ValidationError

    from offer_author.ai.cases import TASK, WRITE_CASES, Cases

    assert "MA_O" not in json.dumps(WRITE_CASES["input_schema"])
    seeded = {"aim": "positive", "what": "x", "member": {"MA_P": {}, "MA_O": {"points_earned": 1990}}, "bits": []}
    with pytest.raises(ValidationError):
        Cases.model_validate({"scenarios": [seeded]})
    assert "the scenario makes those earlier transactions first" in " ".join(TASK.split())


def test_the_repair_message_shows_the_offer_counters_before_and_after_the_transaction():
    from offer_author.ai.cases import mismatch_message

    bits = [{"counters_after": {"Cumulative Spend": 1999}}, {"counters_after": {"Cumulative Spend": 2000}}]
    report = {
        "scenarios": [{"scenario": 0, "what": "reaching 2000", "bits": bits}],
        "mismatches": [
            {
                "scenario": 0,
                "what": "reaching 2000",
                "bit": 1,
                "header": {"h_bit_amount": 1},
                "expect": {"points": 1000, "privileges": []},
                "got": {"points": 0, "privileges": []},
                "counters_after": {"Cumulative Spend": 2000},
            }
        ],
    }
    row = json.loads(mismatch_message(report).split("expect:\n", 1)[1].split("\n\n")[0])[0]
    assert row["offer_counters_before"] == {"Cumulative Spend": 1999}
    assert row["offer_counters_after"] == {"Cumulative Spend": 2000}


def test_the_repair_message_says_what_each_rule_did_on_the_transaction_in_order():
    from offer_author.ai.cases import mismatch_message

    ran = [
        {"rule": "Weekend 2x within cap", "points": 300.0},
        {
            "rule": "Weekend 2x within cap",
            "counter": "Weekend Points",
            "operator": "INCREMENT_BY",
            "old": 0.0,
            "new": 300.0,
        },
        {"rule": "Weekend crossing cap", "points": 200.0},
        {"rule": "Weekend crossing cap", "counter": "Weekend Points", "operator": "SET", "old": 300.0, "new": 400.0},
        {"rule": "Gift", "privilege": "13829"},
    ]
    report = {
        "scenarios": [{"scenario": 0, "what": "weekend 2x", "bits": [{}]}],
        "mismatches": [
            {
                "scenario": 0,
                "what": "weekend 2x",
                "bit": 0,
                "header": {"h_bit_amount": 150},
                "expect": {"points": 300, "privileges": []},
                "got": {"points": 500, "privileges": ["13829"]},
                "rules_ran": ran,
            }
        ],
    }
    text = mismatch_message(report)
    row = json.loads(text.split("expect:\n", 1)[1].split("\n\n")[0])[0]
    assert row["what_each_rule_did"] == [
        "Weekend 2x within cap: 300 points",
        "Weekend 2x within cap: Weekend Points 0 -> 300 (INCREMENT_BY)",
        "Weekend crossing cap: 200 points",
        "Weekend crossing cap: Weekend Points 300 -> 400 (SET)",
        "Gift: privilege 13829",
    ]
    assert 'rule "' not in text and "readAttribute" not in text  # rule names from the canvas, never compiled rules


def _undeclared(spec):
    """The tree with its counters left out and no level on the blocks that use them (the tools cannot fill it)."""
    if isinstance(spec, dict):
        return {
            k: _undeclared(v) for k, v in spec.items() if k not in ("counters", "mta_type_value", "attribute_level")
        }
    return [_undeclared(v) for v in spec] if isinstance(spec, list) else spec


def test_a_refused_compose_in_the_repair_holds_back_the_finish_of_the_same_turn(fixture_catalog, verifier_calls):
    # live C05 (2026-10-31): the repair rebuilt the tree with an undeclared counter and finished in the same turn;
    # the refused compose left the canvas's last compose as it was, so the old blocks were delivered unchanged
    sent, answers = verifier_calls
    answers += [BAD, OK]
    other = json.loads(json.dumps(_undeclared(S07)).replace("Visits toward privilege 501", "Visits so far"))
    turns = [[offer_main()], [("compose", S07)], [FIN], [("compose", other), FIN], [("compose", S07), FIN]]
    res, llm, _ = run(fixture_catalog, turns)
    held = json.dumps(llm.calls[4])
    assert "not finished: fix these and compose again before finish" in held
    assert len(llm.calls) == 5 and len(sent) == 2  # the builder got its turn back and the blocks were checked again
    assert res["diagnostics"]["verify"]["ok"] and res["status"] == "draft"
