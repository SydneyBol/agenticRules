"""Live: Claude Opus 5.5 on Bedrock through the whole prototype pipeline.

Run:  AWS_CONFIG_FILE=~/.aws/config AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 \
      .venv/bin/python -m pytest -m live
Writes tests/.live/<catalog>.json (consumed by test_e2e.py).
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from conftest import FIX, ROOT
from offer_author.llm import BedrockLLM
from offer_author.pipeline import generate

pytestmark = pytest.mark.live
LIVE = os.path.join(ROOT, "tests", ".live")
SCEN = {s["id"]: s for s in yaml.safe_load(open(os.path.join(FIX, "scenarios.yaml")))["scenarios"]}

# devintam1 prompts in the tenant's own business words; the model has to find the codes in the real catalog
DEVINTAM1 = {
    "D01_gold_weekend_stores": "Gold tier members get 100 bonus points on weekend spends of at least $50 at the Chennai HQ store or the online store.",
    "D02_every_5th_yoga": "Every 5th purchase earns a free 2 hours of yoga session.",
    "D03_coffee_per_item": "5 points for every coffee item bought.",
    "D04_birthday_voucher": "Members get a complimentary voucher on a spend within 7 days of their birthday, once a year.",
    "D05_cap_tiers": "Spends of $200 or more earn 3x points, below that 1x, with a lifetime cap of 2000 bonus points.",
}


def run_all(prompts, catalog):
    llm = BedrockLLM()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {k: pool.submit(generate, p, catalog, llm, offer_id=1001) for k, p in prompts.items()}
        return {k: f.result() for k, f in futs.items()}


@pytest.fixture(scope="module")
def fixture_results(fixture_catalog):
    res = run_all({k: s["prompt"] for k, s in SCEN.items()}, fixture_catalog)
    os.makedirs(LIVE, exist_ok=True)
    json.dump(res, open(os.path.join(LIVE, "fixture.json"), "w"), indent=1)
    return res


@pytest.fixture(scope="module")
def devintam1_results(devintam1_catalog):
    res = run_all(DEVINTAM1, devintam1_catalog)
    os.makedirs(LIVE, exist_ok=True)
    json.dump(res, open(os.path.join(LIVE, "devintam1.json"), "w"), indent=1)
    return res


def xml(r):
    return (r.get("rules") or {}).get("xml", "")


@pytest.mark.parametrize("sid", sorted(SCEN))
def test_fixture_scenarios(fixture_results, sid):
    r = fixture_results[sid]
    assert r["status"] != "error", r.get("message")
    assert r["diagnostics"]["issues_final"] == [], r["diagnostics"]["issues_final"]
    if sid.startswith("S16"):
        assert r["unsupported"] and r["rules"] is None
    elif sid.startswith("S11"):
        assert any(u["phrase"] == "streak" for u in r["unsupported"])
    elif sid.startswith("S15"):
        assert r["blocking_questions"] and "AP1" in json.dumps(r["blocking_questions"]) + xml(r)
    else:
        assert r["rules"] and r["rules"]["grammar_problems"] == [] and r["readback"]


def test_fixture_reliability_and_cost(fixture_results):
    lat = sorted(r["diagnostics"]["latency_s"] for r in fixture_results.values())
    assert not [k for k, r in fixture_results.items() if r["status"] == "error"]
    assert lat[len(lat) // 2] < 30
    cached = [r["diagnostics"]["usage"].get("cache_read_input_tokens", 0) for r in fixture_results.values()]
    assert max(cached) > 0, "tenant catalog block should be served from the prompt cache"


def test_d01_real_codes_from_business_words(devintam1_results):
    r, x = devintam1_results["D01_gold_weekend_stores"], xml(devintam1_results["D01_gold_weekend_stores"])
    assert r["rules"], r
    assert 'value="HQ,WEB"' in x or 'value="WEB,HQ"' in x
    # the tenant has two tier codes labelled Gold (GOLD and Gold): match one or both (and say so), or ask
    tier = re.search(r'attribute="tier_class"[^>]*value="([^"]*)"', x)
    assert (tier and set(tier.group(1).split(",")) & {"GOLD", "Gold"}) or r["blocking_questions"]
    assert r["blocking_questions"] or any("Gold" in a for a in r["assumptions"])


def test_d02_privilege_by_name(devintam1_results):
    x = xml(devintam1_results["D02_every_5th_yoga"])
    assert '<field name="SELECTED_PRIVILEGE">37</field>' in x and '<field name="PRIVILEGE_USAGEPOLICY">37</field>' in x


def test_d03_product_category(devintam1_results):
    x = xml(devintam1_results["D03_coffee_per_item"])
    assert "rule_line" in x and "COFFEE" in x


def test_d04_occasion_with_real_attribute_and_privilege(devintam1_results):
    r, x = devintam1_results["D04_birthday_voucher"], xml(devintam1_results["D04_birthday_voucher"])
    assert r["rules"]["rule_count"] == 5 and "date_of_birth" in x and '<field name="SELECTED_PRIVILEGE">64</field>' in x


def test_d05_cap_counter(devintam1_results):
    r = devintam1_results["D05_cap_tiers"]
    assert r["rules"] and any(c["name"].startswith("Points toward") for c in r["rules"]["counters"])
    assert all(rr["diagnostics"]["issues_final"] == [] for rr in devintam1_results.values())
