"""The sub-prompt DSL v2 (core/canvas/dsl.py, SubpromptDSL-TwoPipeline-Plan.md): rules canvases as lines and back.

The lines must carry exactly what the model's JSON tree carries: every canvas read back from XML, written as lines and
compiled again gives the same tree and counters, and the same XML through compose.
"""

import glob
import json
import os

import pytest

from offer_author.core.canvas import dsl
from offer_author.core.canvas.edit import Doc
from offer_author.core.canvas.tree import compact
from offer_author.core.run import Run
from offer_author.core.tenant.catalog import Catalog
from tests.paths import CATALOGS, DEV, GOLDEN

GRAVTY = os.path.dirname(DEV)


def _norm(x):
    if isinstance(x, list):
        return [_norm(i) for i in x]
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if k != "id" and v != []}
    return x


def _canvas(xml):
    v = Doc(xml).view()
    return _norm([compact(t, v["counters"]) for t in v["tops"]]), v["counters"]


def _round_trip(xml):
    tops, counters = _canvas(xml)
    text = dsl.to_lines(tops, counters)
    back, counters_back, _ = dsl.from_lines(text)
    return tops, counters, _norm(back), counters_back, text


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(GOLDEN, "compiled", "*.xml"))), ids=os.path.basename)
def test_golden_canvases_round_trip(path):
    tops, counters, back, counters_back, _ = _round_trip(open(path).read())
    assert back == tops and counters_back == counters


SAMPLE = """\
counter, "Bonus Points Earned" level=MA_O data_type=NUMBER target_type="NO TARGET" counter_type=REGULAR
Filter, activity h_bit_type == SPEND
Filter, activity h_location in [HYD, KOL]
rule_1 "Gold band", if member [MA_P] tier_class == GLDGLW
rule_1, if activity h_bit_amount between (50) and (99.99)
rule_1 or1.1, if activity h_bit_date >= (date(2026-03-08)) datetime_operator=DATE
rule_1 or1.2, if sponsor business_unit == FNB
rule_1, give_points account=9 tag=BASE (activity.h_bit_amount * 2)
rule_2 Cap inside rule_1, if member "Bonus Points Earned" <= (2000 - member.MA_O."Bonus Points Earned")
rule_2, update_attribute "Bonus Points Earned" INCREMENT_BY (activity.h_bit_amount * 2)
rule_1, give_privilege privilege=13829 policy=10023 validity=product_validity quantity=(1)
payment_rule_3 HDFC, if payment bank_code in [HDFC, HDFC2]
payment_rule_3, give_points account=9 tag=BASE (activity.h_pay_in_amount * 2)
rule_4 Card, if member [MA_CC] card_lookup == NAJM_PLT pick=2 scope=TRANSACTION_CARD
rule_4, for FAMILY_HEAD give_points account=9 tag=BASE 50
event_1,
rule_5 "On event", if member [tier_level] Gold_Programme == Gold tierdefault=name
rule_5, give_points account=9 tag=BASE ((activity.h_bit_date{datetime_operator=MONTH} - 1) * 10)
"""


def test_each_statement_compiles_to_its_blocks():
    (root,), counters, origin = dsl.from_lines(SAMPLE)
    assert counters == [
        {
            "name": "Bonus Points Earned",
            "level": "MA_O",
            "data_type": "NUMBER",
            "target_type": "NO TARGET",
            "counter_type": "REGULAR",
        }
    ]
    (act,), (event,) = root["activities"], root["events"]
    assert act["ACTIVITY_CRITERIA"][1] == {
        "type": "activity_criteria",
        "OBJECT": "activity",
        "attribute": "h_location",
        "operators": "in",
        "picked": "HYD,KOL",
    }
    r1, r3, r4 = act["RULES"]
    assert r1["IDENTIFIER"] == "Gold band" and r1["CONDITION"][0] == {
        "type": "if_generic_adaptive",
        "attribute_level": "MA_P",
        "attribute": "tier_class",
        "operators": "==",
        "value": "GLDGLW",
    }
    between, group = r1["CONDITION"][1:]
    assert between["RHS_INPUT"] == {"type": "static_value", "value": "50"} and between["RHS_VALUE"]["value"] == "99.99"
    assert group["type"] == "or" and group["OPERAND2"][0]["OBJECT"] == "sponsor"
    assert group["OPERAND1"][0]["RHS_INPUT"] == {"type": "static_date", "value": "2026-03-08"}
    points, nested, privilege = r1["ACTION"]
    assert points["FORMULA"]["operator"] == "*" and points["LOYALTY_ACCOUNT"] == "9" and points["REWARD_TAGS"] == "BASE"
    assert nested["type"] == "rule" and nested["IDENTIFIER"] == "Cap"
    assert nested["ACTION"][0] == {
        "type": "action",
        "action_type": "update_attribute",
        "mta_attribute": "Bonus Points Earned",
        "action_operator": "INCREMENT_BY",
        "RHS_INPUT_VALUE": {
            "type": "arithmetic_generic",
            "operator": "*",
            "LHS": {"type": "activity_attribute", "ATTRIBUTE": "h_bit_amount"},
            "RHS": {"type": "static_value", "value": "2"},
        },
    }
    assert privilege["SELECTED_PRIVILEGE"] == "13829" and privilege["PRIVILEGE_QUANTITY"]["value"] == "1"
    assert r3["type"] == "payment_rule" and r3["CONDITION"][0] == {
        "type": "if_payment_attribute",
        "payment_attribute": "bank_code",
        "operators": "in",
        "picked": "HDFC,HDFC2",
    }
    card = r4["CONDITION"][0]  # the uniform form, put in the rules screen's own card fields
    assert card["card_main_option"] == "2" and card["onlycard_second_option"] == "TRANSACTION_CARD"
    assert card["attribute"] == "card_lookup" and "pick" not in card and "scope" not in card
    assert (
        r4["ACTION"][0]["type"] == "action_third_party" and r4["ACTION"][0]["third_parties_dropdown"] == "FAMILY_HEAD"
    )
    tier = event["RULES"][0]["CONDITION"][0]
    assert {k: tier[k] for k in ("tiers", "opperater", "opperatervalue")} == {
        "tiers": "Gold_Programme",
        "opperater": "==",
        "opperatervalue": "Gold",
    }
    assert origin["activities[0].RULES[0].ACTION[1]"] == 9 and origin["events[0].RULES[0]"] == 17


def test_the_sample_reads_back_as_written():
    (root,), counters, _ = dsl.from_lines(SAMPLE)
    again, counters_again, _ = dsl.from_lines(dsl.to_lines([root], counters))
    assert again == [root] and counters_again == counters


def test_active_cards_and_yes_no_forms():
    (root,), _, _ = dsl.from_lines(
        "Filter, member [MA_CC] active_cards == 9 pick=ACTIVE_CARDS\nrule_1, if sponsor woman_entrepreneur not_null\n"
        "rule_1, give_points account=9 tag=BASE 10"
    )
    active = root["activities"][0]["ACTIVITY_CRITERIA"][0]
    assert active == {
        "type": "activity_criteria",
        "attribute_level": "MA_CC",
        "card_main_option": "ACTIVE_CARDS",
        "activecard_mainoperators": "==",
        "cardvalue": "9",
    }
    assert "value" not in root["activities"][0]["RULES"][0]["CONDITION"][0]


def test_a_def_is_written_once_and_used_by_name():
    (root,), _, _ = dsl.from_lines(
        "def, month_key, ((activity.h_bit_date{datetime_operator=YEAR} * 12) + activity.h_bit_date{datetime_operator=MONTH})\n"
        'rule_1, if member "Last Month" < $month_key\n'
        'rule_1, update_attribute "Last Month" SET $month_key'
    )
    cond, (update,) = root["activities"][0]["RULES"][0]["CONDITION"][0], root["activities"][0]["RULES"][0]["ACTION"]
    assert cond["value"] == "$month_key"  # a field value is never a def: a block goes in parentheses
    (root,), _, _ = dsl.from_lines(
        "def, month_key, ((activity.h_bit_date{datetime_operator=YEAR} * 12) + activity.h_bit_date{datetime_operator=MONTH})\n"
        'rule_1, if member "Last Month" < ($month_key)\n'
        'rule_1, update_attribute "Last Month" SET $month_key'
    )
    cond, (update,) = root["activities"][0]["RULES"][0]["CONDITION"][0], root["activities"][0]["RULES"][0]["ACTION"]
    assert cond["RHS_INPUT"] == update["RHS_INPUT_VALUE"] and cond["RHS_INPUT"]["operator"] == "+"
    assert cond["RHS_INPUT"] is not update["RHS_INPUT_VALUE"]  # each use is its own copy
    with pytest.raises(dsl.DslError, match=r"\$week is not defined"):
        dsl.from_lines("rule_1, give_points account=9 tag=BASE $week")


@pytest.mark.parametrize(
    "lines, line, expected",
    [
        ("Filter, activity h_bit_type == SPEND\nrule_1, if activity h_bit_amount >> 5", 2, "expected an operator"),
        ("rule_1, if shop h_bit_amount > 5", 1, "expected an object"),
        ("rule_2 inside rule_1, give_points 5", 1, "rule_2 is inside rule_1, which has no line before it"),
        (
            "rule_1 Name, if activity a == 1\nrule_1 Other, if activity b == 2",
            2,
            "name and parent go on its first line",
        ),
        ("rule_1 or1.1, give_points 5", 1, "an action cannot sit in an OR branch"),
        ("rule_1, give_points account=9 (activity.h_bit_amount * 2) 5", 1, "a value block ends the line"),
        ("rule_1 give_points 5", 1, "expected '<label>, <body>'"),
        ("discount, give_points 5", 1, "expected a label"),
        ('rule_1, if activity h_location == "HYD', 1, "cannot read"),
    ],
)
def test_a_line_that_does_not_parse_names_its_number(lines, line, expected):
    with pytest.raises(dsl.DslError) as e:
        dsl.from_lines(lines)
    assert e.value.line == line and expected in e.value.expected, str(e.value)


def test_values_that_look_like_syntax_are_quoted():
    tricky = ["", "with spaces", "a,b", "if", "and", "x=y", "(paren)", "end.", '"q"', "Café"]
    for v in tricky:
        cond = {
            "type": "if_generic_adaptive",
            "OBJECT": "activity",
            "attribute": "h_note",
            "operators": "==",
            "value": v,
        }
        rule = {
            "type": "rule",
            "IDENTIFIER": v,
            "CONDITION": [cond],
            "ACTION": [{"type": "action", "action_type": "x"}],
        }
        tops = [{"type": "offer_generic", "activities": [{"type": "activity", "RULES": [rule]}]}]
        back, _, _ = dsl.from_lines(dsl.to_lines(tops))
        assert back == tops, v


@pytest.fixture(scope="module")
def bf2():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


def _compose(cat, tops, counters):
    run = Run(cat, None)
    out = run.compose({"canvas": "rules", "counters": counters, "blocks": tops[0]})
    written = run.canvases.last["rules"]
    assert written.built and written.xml.startswith("<xml")
    return out["ok"], sorted(str(p) for p in out.get("problems") or []), written.xml, written.counters


LIVE_RUN = os.path.join(DEV, "results", "offers", "devintbf2-20261111-1901")


@pytest.mark.e2e
@pytest.mark.parametrize(
    "path", sorted(glob.glob(os.path.join(LIVE_RUN, "*", "rules.xml"))), ids=lambda p: p.split("/")[-2]
)
def test_compiled_lines_compose_as_the_json_tree(bf2, path):
    tops, counters, back, counters_back, _ = _round_trip(open(path).read())
    assert _compose(bf2, back, counters_back) == _compose(bf2, tops, counters)


@pytest.mark.e2e
def test_every_stored_and_agent_built_canvas_round_trips():
    paths = [os.path.join(DEV, "history-subprompt", "*", "offers.jsonl")]
    xmls = [json.loads(line)["xml"] for p in glob.glob(paths[0]) for line in open(p)]
    for pattern in ("**/rules.xml", "**/*.generated.xml", "ai-agent/baselines/*/history/*.xml"):
        xmls += [open(p).read() for p in glob.glob(os.path.join(GRAVTY, pattern), recursive=True)]
    assert len(xmls) > 1000
    wrong, json_chars, dsl_chars = [], 0, 0
    for xml in xmls:
        tops, counters, back, counters_back, text = _round_trip(xml)
        if back != tops or counters_back != counters:
            wrong.append(text[:200])
        json_chars += len(json.dumps(tops, separators=(",", ":"))) + len(json.dumps(counters, separators=(",", ":")))
        dsl_chars += len(text)
    assert not wrong, wrong[:3]
    assert json_chars / dsl_chars > 2.3  # the lines are much shorter than the tree the model writes today
