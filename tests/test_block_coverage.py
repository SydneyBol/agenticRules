"""Live block coverage: does the model reach every block / condition object / action / counter the compiler can emit,
alone and combined, and does it flag (not fake) offers that need a block the compiler cannot emit?

The reward-offer toolbox (gravty-ui offer-rules.config.ts) has 25 block types. The compiler emits 15 of them; the
other 10 (payment, third-party, currency, airport, product/payment attribute, current date, date-time) must come back
as unsupported or a question, never as XML.

Run:  AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live tests/test_block_coverage.py
Writes tests/.live/block_coverage.json (+ one .xml per case under tests/.live/block_coverage/) for the UI load check.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import ROOT
from offer_author.llm import BedrockLLM
from offer_author.pipeline import generate
from offer_author.pulsar import ui_xml

pytestmark = pytest.mark.live
LIVE = os.path.join(ROOT, "tests", ".live")

TOOLBOX = {  # offer-rules.config.ts: Offer Level, Rule Level, Attributes & Variables
    "offer_generic", "activity", "activity_criteria", "event", "or",
    "rule", "rule_line", "payment_rule", "if_generic_adaptive", "if_payment_attribute", "action", "action_third_party",
    "static_value", "static_string_value", "arithmetic_generic", "arithmetic_date", "current_date_block", "static_date",
    "static_date_time", "currency_converter", "activity_attribute", "member_attribute", "product_attribute",
    "payment_attribute", "airport_attribute"}
NOT_COMPILED = {"payment_rule", "if_payment_attribute", "action_third_party", "current_date_block", "static_date_time",
                "currency_converter", "product_attribute", "payment_attribute", "airport_attribute"}

# id: (prompt, features the XML must contain). Features: block:<type>, obj:<condition object>, act:<action type>,
# op:<operator>, counter:<REGULAR|VECTOR>
SINGLE = {
    "B01_earn_per_dollar": ("2 points for every $1 spent.",
                            {"block:activity", "block:activity_criteria", "block:rule", "block:action", "block:arithmetic_generic",
                             "block:activity_attribute", "block:static_value", "act:give_points"}),
    "B02_location_filter": ("Double points on spends at the Downtown or Mall store.", {"obj:activity", "op:in", "act:give_points"}),
    "B03_tiers": ("Spends of $100 or more earn 300 points; spends from $50 up to $99.99 earn 100 points.",
                  {"block:if_generic_adaptive", "op:>=", "op:<"}),
    "B04_member_tier": ("Gold tier members earn 50 bonus points on every spend.", {"obj:member", "act:give_points"}),
    "B05_or": ("100 bonus points on any spend at the Airport T1 store, or on any spend over $500 anywhere.", {"block:or"}),
    "B06_privilege": ("Spend over $30 and get a Free Coffee voucher.", {"act:give_privilege"}),
    "B07_line_item": ("5 points for every Snacks item bought.", {"block:rule_line", "obj:products_attrs"}),
    "B08_collect_all": ("Buy from Snacks, Drinks and Bakery during the offer to earn 500 points, once.",
                        {"block:rule_line", "block:static_string_value", "act:update_attribute"}),
    "B09_nth": ("Every 3rd stay earns 200 points.", {"act:update_attribute", "counter:REGULAR"}),
    "B10_milestone": ("Earn 1000 bonus points once, when the member's total spend in the offer reaches $2000.",
                      {"block:static_string_value", "act:update_attribute"}),
    "B11_unique": ("Spend at 3 different stores to earn 300 points, once.", {"counter:VECTOR"}),
    "B12_unlock": ("After any booking, the member gets 2x points on all later spends.", {"act:update_attribute", "act:give_points"}),
    "B13_cap": ("3x points on all spends, capped at 1500 bonus points per member.", {"act:give_points", "act:update_attribute"}),
    "B14_birthday": ("200 points on a spend within 5 days of the member's birthday, once a year.",
                     {"block:arithmetic_date", "block:static_date", "block:member_attribute"}),
    "B15_weekend": ("Double points on weekend spends.", {"act:give_points"}),
    "B16_happy_hour": ("3x points on spends between 5pm and 7pm.", {"act:give_points"}),
}
COMBO = {
    "C01_tier_store_tiers_cap": ("Gold members spending $100 or more at the Downtown store earn 3x points, $50 to $99.99 earns 2x, "
                                 "with a maximum of 2000 bonus points per member.",
                                 {"obj:member", "obj:activity", "op:in", "op:>=", "act:give_points", "act:update_attribute"}),
    "C02_line_plus_header": ("5 points per Drinks item, plus 50 bonus points when the whole spend is over $40.",
                             {"block:rule_line", "block:rule", "obj:products_attrs", "obj:activity"}),
    "C03_anniversary_store_privilege": ("A Free Coffee voucher for a spend at the Mall store within 7 days of the member's "
                                        "enrolment anniversary, once a year.",
                                        {"block:arithmetic_date", "block:member_attribute", "act:give_privilege", "op:in"}),
    "C04_nth_tier_privilege": ("Every 5th spend by Silver members earns a Free Coffee voucher.",
                               {"obj:member", "act:give_privilege", "counter:REGULAR"}),
    "C05_weekend_or_store_privilege": ("On weekends, spends over $60 or any spend at the Harbour store earn a Free Coffee voucher.",
                                       {"block:or", "act:give_privilege"}),
    "C06_unlock_tier": ("Gold members who make a booking unlock 2x points on their later spends.", {"obj:member", "act:update_attribute"}),
}
NEEDS_MISSING_BLOCK = {
    "U01_streak": "Earn 500 points for spending in each of 4 consecutive weeks.",
    "U02_payment_card": "Pay with a Visa credit card and get 2x points.",
    "U03_third_party": "When a member spends over $100, send them an SMS and notify the partner's API.",
    "U04_currency": "Convert the spend from USD to INR and give 1 point per rupee.",
    "U05_airport_pair": "Flights from BLR to DEL earn 500 bonus points.",
}


def features(xml, counters):
    f = {"block:" + t for t in re.findall(r'<block type="([a-z_]+)"', xml)}
    f |= {"obj:" + o for o in re.findall(r'\bobject="([a-z_]+)"', xml)}
    f |= {"act:" + a for a in re.findall(r'attribute_type_input="(give_points|give_privilege|update_attribute)"', xml)}
    f |= {"op:" + o.replace("&gt;", ">").replace("&lt;", "<") for o in re.findall(r'\boperators="([^"]+)"', xml) if o != "null"}
    f |= {"counter:" + c["counter_type"] for c in counters}
    return f


@pytest.fixture(scope="module")
def results(fixture_catalog):
    prompts = {k: v[0] for k, v in {**SINGLE, **COMBO}.items()} | NEEDS_MISSING_BLOCK
    llm = BedrockLLM()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {k: pool.submit(generate, p, fixture_catalog, llm, offer_id=1001) for k, p in prompts.items()}
        res = {k: f.result() for k, f in futs.items()}
    out = os.path.join(LIVE, "block_coverage")
    os.makedirs(out, exist_ok=True)
    for k, r in res.items():
        if (r.get("rules") or {}).get("xml"):
            open(os.path.join(out, k + ".xml"), "w").write(ui_xml(r))
    json.dump(res, open(os.path.join(LIVE, "block_coverage.json"), "w"), indent=1)
    return res


def xml_of(r):
    return (r.get("rules") or {}).get("xml", "")


@pytest.mark.parametrize("cid", sorted({**SINGLE, **COMBO}))
def test_reaches_the_blocks(results, cid):
    r = results[cid]
    want = {**SINGLE, **COMBO}[cid][1]
    assert r["status"] in ("draft", "needs_input") and xml_of(r), (r["status"], r.get("message"), r.get("unsupported"))
    got = features(xml_of(r), r["rules"]["counters"])
    assert want <= got, f"missing {sorted(want - got)}; got {sorted(got)}"
    blocks = {f[6:] for f in got if f.startswith("block:")}
    assert blocks <= TOOLBOX and not r["rules"]["grammar_problems"], (blocks - TOOLBOX, r["rules"]["grammar_problems"])


@pytest.mark.parametrize("cid", sorted(NEEDS_MISSING_BLOCK))
def test_flags_what_it_cannot_build(results, cid):
    r = results[cid]
    flagged = r["status"] in ("unsupported", "needs_input") or r.get("unsupported") or r.get("blocking_questions")
    assert flagged, (r["status"], r.get("readback"))
    assert not re.findall(r'<block type="(%s)"' % "|".join(NOT_COMPILED), xml_of(r))


def test_coverage_summary(results):
    """Prints the matrix: which toolbox blocks the live outputs reached, and latency."""
    seen = set()
    for k, r in results.items():
        x = xml_of(r)
        seen |= set(re.findall(r'<block type="([a-z_]+)"', x))
        d = r.get("diagnostics") or {}
        print(f"{k:34s} {r['status']:12s} {d.get('latency_s')}s repairs={d.get('repairs')} "
              f"blocks={(r.get('rules') or {}).get('block_count')}")
    print("toolbox blocks reached:", sorted(seen & TOOLBOX))
    print("compiled but not reached:", sorted(TOOLBOX - NOT_COMPILED - seen))
    print("not compiled (flagged instead):", sorted(NOT_COMPILED))
    assert TOOLBOX - NOT_COMPILED - seen <= {"event"}  # the streak (event section) primitive is not built yet
