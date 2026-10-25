"""Live: whole-offer prompts (offer_flow.yaml + the hosted combinations), each built by ONE agent run (Offer Main, the
segment, the rules) with the devintam1 catalog and the compile service. Writes tests/.live/offer_flow.json.

Run: AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live tests/live/test_offer_flow.py -s
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from tools.paths import CASES, LIVE
from offer_author.ai.agent import run
from offer_author.ai.llm import BedrockLLM

pytestmark = pytest.mark.live
CASES = yaml.safe_load(open(os.path.join(CASES, "offer_flow.yaml")))["cases"] + \
    [{"id": c["id"], "prompt": c["prompt"], "expect": {}} for c in
     yaml.safe_load(open(os.path.join(CASES, "hosted_combinations.yaml")))["cases"]]


@pytest.fixture(scope="module")
def results(devintam1_catalog):
    llm = BedrockLLM()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {c["id"]: pool.submit(run, c["prompt"], devintam1_catalog, llm, offer_id=1) for c in CASES}
        res = {k: f.result() for k, f in futs.items()}
    os.makedirs(LIVE, exist_ok=True)
    json.dump(res, open(os.path.join(LIVE, "offer_flow.json"), "w"), indent=1, default=str)
    for k, r in res.items():
        om = r.get("offer_main") or {}
        print(f"\n{k} {r['status']} ({r['diagnostics']['latency_s']}s)\n  offer main: {json.dumps(om)[:300]}"
              f"\n  segment: {(r.get('segment') or {}).get('name', '-')!r}\n  rules: {(r.get('rules') or {}).get('rule_count')} "
              f"{r['readback'][:160]!r}\n  questions: {r['blocking_questions']}")
    return res


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_split(results, case):
    r, e = results[case["id"]], case["expect"]
    assert r["status"] != "error", r.get("message")
    om = r["offer_main"] or {}
    if "segment" in e:
        assert bool(om.get("segment_prompt", "").strip()) == e["segment"], om
        if e["segment"] and not e.get("question"):
            assert r["segment"] or (e.get("segment_status") and r["blocking_questions"]), r["unsupported"]
    for k, v in (e.get("metadata") or {}).items():
        assert om.get(k) == v, (k, om)
    low = lambda s: (s or "").lower()  # noqa: E731
    for w in e.get("rules_mentions", []):
        assert low(w) in low(om.get("rules_prompt")), (w, om.get("rules_prompt"))
    for w in e.get("rules_not", []):
        assert low(w) not in low(om.get("rules_prompt")), (w, om.get("rules_prompt"))
    for w in e.get("segment_mentions", []):
        assert low(w) in low(om.get("segment_prompt")), (w, om.get("segment_prompt"))
    if e.get("question"):
        assert r["blocking_questions"], r
    else:
        assert r["rules"], r["unsupported"]
        qs = " ".join(r["blocking_questions"]).lower()  # no noise: dates and "which activity type is spend" are never asked
        assert "start and end date" not in qs and "shopping" not in qs, qs
