"""E2E for the agent's tools against the UNMODIFIED gravty-ui (compile service on :8078, ng serve on :4200) and the real
engine harness, with a scripted model: what the model writes (selections, counters) becomes XML the stock screens load
unchanged, with the DRL and counters of the XML the UI saved itself.

Run: .venv/bin/python -m pytest -m e2e tests/e2e/test_agent_stock_ui.py   (start the compile-server launch config first)
"""
import glob
import json
import os
import re
import urllib.request

import pytest

from tests.support import GOLDEN, SEGMENTS, finish, golden, offer_main, saved_segment
from offer_author.ai import agent
from offer_author.core import session
from offer_author.core import engine, ui
from offer_author.ai.llm import ScriptedLLM

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module", autouse=True)
def service():
    try:
        urllib.request.urlopen(ui.URL + "/health", timeout=5)
    except OSError:
        pytest.skip(f"compile service not running at {ui.URL}")


def seg_drl(d):
    return re.sub(r'(segmentation-|addValidatedSegmentId\(")[\w-]+', r"\1ID", d or "")


@pytest.mark.parametrize("name", [os.path.basename(f)[:-4] for f in sorted(glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")))])
def test_golden_rules_from_selections(name, fixture_catalog):
    saved = ui.compile_xml(open(os.path.join(GOLDEN, "compiled", name + ".xml")).read(), "")
    args = golden(name)
    s = session.Session(fixture_catalog, 1001)
    out = s.run("compile", args)
    assert out["ok"], out["problems"]
    assert s.last["rules"]["res"]["drl"] == saved["drl"]  # compared by code; the model never sees either
    assert sorted(c["name"] for c in out["counters_created"]) == sorted(c["name"] for c in args["counters"])


@pytest.mark.parametrize("name", [os.path.basename(f)[:-4] for f in sorted(glob.glob(os.path.join(SEGMENTS, "*.xml")))])
def test_saved_segments_from_selections(name, devintam1_catalog):
    saved = ui.compile_xml(open(os.path.join(SEGMENTS, name + ".xml")).read(), "devintam1", canvas="segment")
    s = session.Session(devintam1_catalog, None)
    out = s.run("compile", saved_segment(name))
    assert seg_drl(s.last["segment"]["res"].get("drl")) == seg_drl(saved.get("drl"))
    assert out["ok"] == (not ui.problems(saved))  # a segment the stock screen cannot compile fails the same way


def test_whole_offer_runs_in_the_engine_with_segment_and_offer_main(devintam1_catalog):
    if not engine.available():
        pytest.skip("engine harness jar not built")
    rules = json.loads(json.dumps(golden("S14_simple_sanity")).replace('"BONUS"', '"BASE"').replace('"LOYALTY_ACCOUNT": "1"', '"LOYALTY_ACCOUNT": "12"'))
    day = lambda d: {"h_bit_amount": 100, "h_bit_date": f"{d}T10:00:00"}  # noqa: E731
    bits = [{"header": day("2026-10-20"), "expect": {"points": 1000}},
            {"header": day("2026-10-21"), "expect": {"points": 1000}},  # over the per-member limit of 2
            {"header": day("2026-10-22"), "expect": {"points": 0}},
            {"header": day("2027-01-05"), "expect": {"points": 0}}]  # after the end date
    llm = ScriptedLLM([[offer_main(end_date="2026-12-31", max_usage_per_user=2, frequency="total", segment_prompt="Members over 27")],
                       [("compile", saved_segment("79_Members_Over_27")), ("compile", rules)],
                       [("simulate", {"member": {"attributes": {"date_of_birth": "1990-06-15"}}, "bits": bits}),  # age is
                        ("simulate", {"member": {"attributes": {"date_of_birth": "2010-06-15"}}, "bits": bits[:1]})],  # derived
                       [finish(segment_name="Members over 27")]])
    res = agent.run("Members over 27 earn 10 points per $1, twice, until 2026-12-31", devintam1_catalog, llm, offer_id=1)
    assert res["status"] == "draft" and res["segment"] and res["rules"]["grammar_problems"] == []
    over, under = (json.loads(r["content"]) for r in llm.calls[-1][6]["content"])
    assert over["member_in_segment"] is True and [b["points"] for b in over["bits"]] == [1000, 1000, 0, 0]
    assert "limit 2" in over["bits"][2]["skipped"] and "offer dates" in over["bits"][3]["skipped"]
    assert under["member_in_segment"] is False and under["bits"][0]["points"] == 0
