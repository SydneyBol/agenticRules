"""Live: whole-offer prompts (offer_flow.yaml + the hosted combinations) through the planner, then the segment and
rules generators on their own parts, with the devintam1 catalog. Writes tests/.live/offer_flow.json.

Run: AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live tests/test_offer_flow_live.py -s
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from conftest import ROOT
from offer_author.llm import BedrockLLM
from offer_author.pipeline import generate
from offer_author.planner import plan_offer, rules_context
from offer_author.segment import generate_segment

pytestmark = pytest.mark.live
CASES = yaml.safe_load(open(os.path.join(ROOT, "tests", "cases", "offer_flow.yaml")))["cases"] + \
    [{"id": c["id"], "prompt": c["prompt"], "expect": {}} for c in
     yaml.safe_load(open(os.path.join(ROOT, "tests", "cases", "hosted_combinations.yaml")))["cases"]]


def run(cat, llm, case):
    plan = plan_offer(case["prompt"], cat, llm)
    out = {"plan": plan}
    if plan.get("status") == "error":
        return out
    if plan["segment"]["prompt"].strip():
        out["segment"] = generate_segment(plan["segment"]["prompt"], cat, llm)
    out["rules"] = generate(plan["rules"]["prompt"], cat, llm, offer_id=1, context=rules_context(plan))
    return out


@pytest.fixture(scope="module")
def results(devintam1_catalog):
    llm = BedrockLLM()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {c["id"]: pool.submit(run, devintam1_catalog, llm, c) for c in CASES}
        res = {k: f.result() for k, f in futs.items()}
    os.makedirs(os.path.join(ROOT, "tests", ".live"), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, "tests", ".live", "offer_flow.json"), "w"), indent=1, default=str)
    for k, r in res.items():
        p = r["plan"]
        if p.get("status") == "error":
            print(f"\n{k}: PLAN ERROR {p.get('message')}")
            continue
        s = r.get("segment") or {}
        print(f"\n{k} ({p['diagnostics']['latency_s']}s)\n  metadata: {json.dumps(p['metadata'])[:300]}\n  segment:  {p['segment']['prompt']!r} -> "
              f"{s.get('status', '-')} {json.dumps(s.get('conditions') or s.get('or_groups') or s.get('unsupported') or '')[:200]}"
              f"\n  rules:    {p['rules']['prompt']!r} -> {r['rules']['status']} {r['rules']['readback'][:160]!r}"
              f"\n  questions: {p['blocking_questions'] + r['rules']['blocking_questions']}")
    return res


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_split(results, case):
    r, e = results[case["id"]], case["expect"]
    p = r["plan"]
    assert p.get("status") != "error", p.get("message")
    if "segment" in e:
        assert bool(p["segment"]["prompt"].strip()) == e["segment"], p["segment"]
        if e["segment"] and not e.get("question"):
            assert r["segment"]["status"] in e.get("segment_status", ["draft"]), r["segment"]
            assert r["segment"].get("xml") or r["segment"]["status"] == "needs_input", r["segment"]
    for k, v in (e.get("metadata") or {}).items():
        assert p["metadata"][k] == v, (k, p["metadata"])
    low = lambda s: s.lower()
    for w in e.get("rules_mentions", []):
        assert low(w) in low(p["rules"]["prompt"]), (w, p["rules"]["prompt"])
    for w in e.get("rules_not", []):
        assert low(w) not in low(p["rules"]["prompt"]), (w, p["rules"]["prompt"])
    for w in e.get("segment_mentions", []):
        assert low(w) in low(p["segment"]["prompt"]), (w, p["segment"]["prompt"])
    if e.get("question"):
        assert p["blocking_questions"] or r["rules"]["blocking_questions"], p
    assert r["rules"]["status"] in ("draft", "needs_input"), r["rules"].get("unsupported")
    if not e.get("question"):  # no noise: dates and "which activity type is spend" are never asked
        qs = " ".join(p["blocking_questions"] + r["rules"]["blocking_questions"]).lower()
        assert "start and end date" not in qs and "shopping" not in qs, qs
