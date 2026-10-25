"""Regression against the spike: XML that already passed real Blockly + the real engine (63/64 BIT expectations)."""
import os
import re

import pytest

from conftest import GOLDEN, recorded_runs, replay_calls
from offer_author.expander import expand
from offer_author.llm import ScriptedLLM
from offer_author.pipeline import generate
from offer_author.rule_xml import offer_xml
from tests.fixtures.gold_intents import CATALOG as SPIKE_CATALOG, INTENTS

GOLD_XML = sorted(f[:-4] for f in os.listdir(os.path.join(GOLDEN, "compiled")))
LLM_XML = sorted(f[:-4] for f in os.listdir(os.path.join(GOLDEN, "compiled_llm")))


@pytest.mark.parametrize("sid", GOLD_XML)
def test_gold_intents_compile_byte_identical(sid):
    spec, _ = expand(INTENTS[sid], SPIKE_CATALOG)
    assert offer_xml(spec) == open(os.path.join(GOLDEN, "compiled", sid + ".xml")).read()


def strip_labels(xml):
    # the spike adapter used codes as display labels; the prototype uses catalog labels (as the UI itself saves)
    return re.sub(r' visible_value="[^"]*"', "", xml)


@pytest.mark.parametrize("sid", LLM_XML)
def test_recorded_model_output_reproduces_engine_verified_xml(sid, fixture_catalog):
    rec = next(r for r in recorded_runs() if r["scenario"] == sid and r["run"] == 0)
    res = generate("(replay)", fixture_catalog, ScriptedLLM([replay_calls(rec)]), max_repairs=0)
    assert res["diagnostics"]["issues_final"] == []
    assert res["rules"]["grammar_problems"] == []
    assert strip_labels(res["rules"]["xml"]) == strip_labels(open(os.path.join(GOLDEN, "compiled_llm", sid + ".xml")).read())


@pytest.mark.parametrize("rec", recorded_runs(), ids=lambda r: f"{r['scenario'][:3]}-run{r['run']}")
def test_all_48_recorded_runs_validate_and_never_come_back_empty(rec, fixture_catalog):
    res = generate("(replay)", fixture_catalog, ScriptedLLM([replay_calls(rec)]), max_repairs=0)
    sid = rec["scenario"]
    assert res["diagnostics"]["issues_final"] == [], res["diagnostics"]["issues_final"]
    if sid.startswith("S16"):  # referral on friends' spend: honest refusal, nothing built
        assert res["status"] == "unsupported" and res["rules"] is None and res["unsupported"]
    elif sid.startswith("S11"):  # streak has no expander yet: reported, not silently dropped
        assert any(u["phrase"] == "streak" for u in res["unsupported"])
    elif sid.startswith("S15"):  # ambiguous store: a draft AND a question
        assert res["blocking_questions"]
    else:
        assert res["status"] in ("draft", "needs_input") and res["rules"]["grammar_problems"] == []
