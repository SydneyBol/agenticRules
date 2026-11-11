"""The sub-prompt DSL (core/canvas/dsl.py, SubpromptDSL-TwoPipeline-Plan.md): rules canvases as plain lines and back.

The lines carry exactly what the model's JSON tree carries: every canvas read back from XML, written as lines and
compiled again gives the same tree and counters, and the same XML through compose. No level code (MA_P, MA_CC, ...)
is needed in a line; names, types and field layouts come from the catalog and the code.
"""

import glob
import importlib.util
import json
import os
import re

import pytest

from offer_author.core.canvas import dsl
from offer_author.core.canvas.edit import Doc
from offer_author.core.canvas.tree import compact
from offer_author.core.run import Run
from offer_author.core.settings import Settings
from offer_author.core.tenant.catalog import Catalog
from tests.paths import CATALOGS, DEV, GOLDEN
from tests.support import said

GRAVTY = os.path.dirname(DEV)
LEVEL_CODE = re.compile(r"\bM[APR]_[A-Z]{1,2}\b|tier_level")


def _norm(x):
    if isinstance(x, list):
        return [_norm(i) for i in x]
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if k != "id" and v != []}
    return x


def _canvas(xml):
    v = Doc(xml).view()
    return _norm([compact(t, v["counters"]) for t in v["tops"]]), v["counters"]


def _round_trip(xml, catalog=None):
    tops, counters = _canvas(xml)
    text = dsl.to_lines(tops, counters, catalog)
    back, counters_back, _ = dsl.from_lines(text, catalog)
    return tops, counters, _norm(back), counters_back, text


@pytest.mark.parametrize("path", sorted(glob.glob(os.path.join(GOLDEN, "compiled", "*.xml"))), ids=os.path.basename)
def test_golden_canvases_round_trip(path):
    tops, counters, back, counters_back, text = _round_trip(open(path).read())
    assert back == tops and counters_back == counters
    assert not LEVEL_CODE.search(text), text


@pytest.mark.e2e
def test_migration_writes_lines_that_compile_back(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "migrate_subprompts", os.path.join(DEV, "tools", "migrate_subprompts.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    migrate = module.migrate

    xml = open(sorted(glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")))[0]).read()
    store = tmp_path / "offers.jsonl"
    store.write_text(json.dumps({"id": "A", "xml": xml, "subprompt": "legacy"}) + "\n" + json.dumps({"id": "B"}) + "\n")
    assert migrate(str(store), None, write=False)["migrated"] == 1 and "dsl" not in store.read_text()
    got = migrate(str(store), None, write=True)
    first, second = (json.loads(line) for line in store.read_text().splitlines())
    assert got["failed"] == ["B"] and first["subprompt"] == "legacy" and first["dsl_form"] == "3"
    assert _norm(dsl.from_lines(first["dsl"])[0]) == _canvas(xml)[0] and "dsl" not in second
    assert len(list(tmp_path.glob("offers.jsonl.bak-*"))) == 1


SAMPLE = """\
counter, "Bonus Points Earned" number
counter, "Visits per store" number per h_location of Activity
counter, "Basket total" number transaction_only
counter, Seen text target=LADDER
def, month_key, ((activity.h_bit_date.YEAR * 12) + activity.h_bit_date.MONTH)
Filter, activity h_bit_type == SPEND
Filter, activity h_location in [HYD, KOL]
rule_1 "Gold band", if profile tier_class == GLDGLW
rule_1, if activity h_bit_amount between 50 and 99.99
rule_1 or1.1, if activity h_bit_date.DAY_OF_WEEK >= 6
rule_1 or1.2, if sponsor business_unit == FNB
rule_1, give_points account=9 tag=BASE (activity.h_bit_amount * 2)
rule_2 Cap inside rule_1, if counter "Bonus Points Earned" <= (2000 - counter."Bonus Points Earned")
rule_2, update_attribute "Bonus Points Earned" INCREMENT_BY (activity.h_bit_amount * 2)
rule_2, update_attribute "Visits per store" at h_location INCREMENT_BY 1
rule_2, update_attribute "Basket total" SET $month_key
rule_2, update_attribute profile social_status = INF
rule_1, give_privilege privilege=13829 policy=10023 validity=product_validity quantity=1
payment_rule_3 HDFC, if payment bank_code in [HDFC, HDFC2]
payment_rule_3, give_points (payment.amount * 2)
rule_4 Card, if card 2 transaction card_lookup == NAJM_PLT
rule_4, if card active == 9
rule_4, if subscription 1 plan_status == ACTIVE
rule_4, if bank_account any bank_lookup == HDFC
rule_4, if third_party CORPORATE profile reward_preference == GCP
rule_4, if profile date_of_birth.MONTH == today.MONTH
rule_4, if activity h_bit_date.DATE between 2026-03-08 and (2026-03-08 + 7 DAYS)
rule_4, for FAMILY_HEAD give_points (rate(sponsor_base_rate, 1) * 50)
event_1,
rule_5 "On event", if tier Gold_Programme == Gold
rule_5, give_points 10
"""


def test_each_statement_compiles_to_its_blocks():
    (root,), counters, origin = dsl.from_lines(SAMPLE)
    assert counters == [
        {
            "name": "Bonus Points Earned",
            "level": "MA_O",
            "data_type": "NUMBER",
            "counter_type": "REGULAR",
            "target_type": "NO TARGET",
        },
        {
            "name": "Visits per store",
            "level": "MA_O",
            "data_type": "NUMBER",
            "counter_type": "VECTOR",
            "namespace": "h_location",
            "entity_level": "Activity",
            "target_type": "NO TARGET",
        },
        {"name": "Basket total", "level": "MA_A", "data_type": "NUMBER", "counter_type": "REGULAR"},
        {"name": "Seen", "level": "MA_O", "data_type": "TEXT", "counter_type": "REGULAR", "target_type": "LADDER"},
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
    weekday = group["OPERAND1"][0]
    assert weekday["datetime_operator"] == "DAY_OF_WEEK" and weekday["attribute"] == "h_bit_date"
    assert group["type"] == "or" and group["OPERAND2"][0]["OBJECT"] == "sponsor"
    points, nested, privilege = r1["ACTION"]
    assert points["FORMULA"]["operator"] == "*" and points["LOYALTY_ACCOUNT"] == "9" and points["REWARD_TAGS"] == "BASE"
    cap = nested["CONDITION"][0]  # a declared counter: no level written, the compose step fills it
    assert cap["attribute"] == "Bonus Points Earned" and "attribute_level" not in cap and "OBJECT" not in cap
    assert cap["RHS_INPUT"]["RHS"] == {
        "type": "member_attribute",
        "ATTRIBUTE_TYPE": "MA_O",
        "ATTRIBUTE": "Bonus Points Earned",
    }
    raised, vector, month, profile = nested["ACTION"]
    assert raised == {
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
    assert vector["vector_counter_name"] == vector["vector_counter_namespace"] == "h_location"
    assert month["RHS_INPUT_VALUE"]["LHS"]["LHS"] == {
        "type": "activity_attribute",
        "ATTRIBUTE": "h_bit_date",
        "datetime_operator": "YEAR",
    }
    assert profile == {
        "type": "action",
        "action_type": "update_attribute",
        "mta_type_value": "MA_P",
        "mta_attribute": "social_status",
        "value": "INF",
    }
    assert privilege["SELECTED_PRIVILEGE"] == "13829" and privilege["PRIVILEGE_QUANTITY"]["value"] == "1"
    assert r3["type"] == "payment_rule" and r3["CONDITION"][0] == {
        "type": "if_payment_attribute",
        "payment_attribute": "bank_code",
        "operators": "in",
        "picked": "HDFC,HDFC2",
    }
    assert r3["ACTION"][0]["FORMULA"]["LHS"] == {"type": "payment_attribute", "payment_attribute": "amount"}
    card, active, plan, bank, third, birthday, window = r4["CONDITION"]
    assert card == {  # each part in the rules screen's own card fields
        "type": "if_generic_adaptive",
        "attribute_level": "MA_CC",
        "card_main_option": "2",
        "onlycard_second_option": "TRANSACTION_CARD",
        "attribute": "card_lookup",
        "operators": "==",
        "value": "NAJM_PLT",
    }
    assert active == {
        "type": "if_generic_adaptive",
        "attribute_level": "MA_CC",
        "card_main_option": "ACTIVE_CARDS",
        "activecard_mainoperators": "==",
        "cardvalue": "9",
    }
    assert plan["selected_subscription_plan"] == "1" and plan["selected_subscription_attribute"] == "plan_status"
    assert bank["card_main_option"] == "ALL_ACCOUNTS" and bank["attribute"] == "bank_lookup"
    assert third["OBJECT"] == "third_party" and third["third_parties_dropdown"] == "CORPORATE"
    assert third["attribute_level"] == "MA_P"
    assert birthday["date_operator"] == "MONTH" and birthday["RHS_INPUT"] == {
        "type": "current_date_block",
        "current_date_type": "MONTH",
    }
    assert window["RHS_INPUT"] == {"type": "static_date", "value": "2026-03-08"}
    assert window["RHS_VALUE"]["type"] == "arithmetic_date" and window["RHS_VALUE"]["date_quantity"] == "DAYS"
    family = r4["ACTION"][0]
    assert family["type"] == "action_third_party" and family["third_parties_dropdown"] == "FAMILY_HEAD"
    assert family["FORMULA"]["LHS"] == {
        "type": "currency_converter",
        "conversion_type": "sponsor_base_rate",
        "LOYALTY_ACCOUNT": "1",
    }
    tier = event["RULES"][0]["CONDITION"][0]
    assert {k: tier[k] for k in ("tiers", "opperater", "opperatervalue", "tierdefault")} == {
        "tiers": "Gold_Programme",
        "opperater": "==",
        "opperatervalue": "Gold",
        "tierdefault": "name",
    }
    assert origin["activities[0].RULES[0].ACTION[1]"] == 13 and origin["events[0].RULES[0]"] == 30


def test_the_sample_is_written_as_it_was_read():
    (root,), counters, _ = dsl.from_lines(SAMPLE)
    text = dsl.to_lines([root], counters)
    assert not LEVEL_CODE.search(text) and " block " not in text, text
    again, counters_again, _ = dsl.from_lines(text)
    assert again == [root] and counters_again == counters
    plain = SAMPLE.replace("$month_key", "((activity.h_bit_date.YEAR * 12) + activity.h_bit_date.MONTH)")
    assert text.splitlines() == [line for line in plain.splitlines() if not line.startswith("def,")]


def test_blocks_get_ids_that_name_their_line():
    (root,), _, _ = dsl.from_lines(SAMPLE, ids=True)
    rule = root["activities"][0]["RULES"][0]
    assert root["id"] == "offer" and root["activities"][0]["id"] == "activity_1" and rule["id"] == "rule_1"
    assert rule["CONDITION"][0]["id"] == "L8" and rule["CONDITION"][1]["RHS_VALUE"]["id"] == "L9.3"
    assert rule["CONDITION"][2]["id"] == "or1" and rule["ACTION"][1]["id"] == "rule_2"
    ids = re.findall(r'"id": "([^"]+)"', json.dumps(root))
    assert len(ids) == len(set(ids))


def test_a_def_is_written_once_and_used_by_name():
    (root,), _, _ = dsl.from_lines(
        "def, month_key, ((activity.h_bit_date.YEAR * 12) + activity.h_bit_date.MONTH)\n"
        'counter, "Last Month" number\n'
        'rule_1, if counter "Last Month" < $month_key\n'
        'rule_1, update_attribute "Last Month" SET $month_key'
    )
    cond, (update,) = root["activities"][0]["RULES"][0]["CONDITION"][0], root["activities"][0]["RULES"][0]["ACTION"]
    assert cond["RHS_INPUT"] == update["RHS_INPUT_VALUE"] and cond["RHS_INPUT"]["operator"] == "+"
    assert cond["RHS_INPUT"] is not update["RHS_INPUT_VALUE"]  # each use is its own copy
    with pytest.raises(dsl.DslError, match=r"\$week is not defined"):
        dsl.from_lines("rule_1, give_points $week")


@pytest.mark.parametrize(
    "lines, line, expected",
    [
        ("Filter, activity h_bit_type == SPEND\nrule_1, if activity h_bit_amount >> 5", 2, "expected an operator"),
        ("rule_1, if shop h_bit_amount > 5", 1, "expected a subject"),
        ("rule_1, if card 2 any transaction card_lookup == 5", 1, "expected one of `any` and `transaction`"),
        ("rule_1, if card 2 3 card_lookup == 5", 1, "expected one attribute"),
        ("rule_1, if subscription 1 any plan_status == ACTIVE", 1, "goes with a card or a bank_account"),
        ("rule_2 inside rule_1, give_points 5", 1, "rule_2 is inside rule_1, which has no line before it"),
        (
            "rule_1 Name, if activity a == 1\nrule_1 Other, if activity b == 2",
            2,
            "name and parent go on its first line",
        ),
        ("rule_1 or1.1, give_points 5", 1, "an action cannot sit in an OR branch"),
        ("rule_1, give_points (activity.h_bit_amount * 2) 5", 1, "a value ends the line"),
        ("rule_1 give_points 5", 1, "expected '<label>, <body>'"),
        ("discount, give_points 5", 1, "expected a label"),
        ("counter, Total money", 1, "expected the counter's type"),
        ('rule_1, if activity h_location == "HYD', 1, "cannot read"),
    ],
)
def test_a_line_that_does_not_parse_names_its_number(lines, line, expected):
    with pytest.raises(dsl.DslError) as e:
        dsl.from_lines(lines)
    assert e.value.line == line and expected in e.value.expected, str(e.value)


def test_values_that_look_like_syntax_are_quoted():
    tricky = ["", "with spaces", "a,b", "if", "and", "x=y", "(paren)", "end.", '"q"', "Café", "100", "2026-01-01"]
    tricky += ["card", "profile", "activity.h_bit_amount", "today", "$x", "any", "active"]
    for v in tricky:
        cond = {"type": "if_generic_adaptive", "OBJECT": "activity", "attribute": v, "operators": "==", "value": v}
        update = {"type": "action", "action_type": "update_attribute", "mta_attribute": v, "action_operator": v}
        rule = {"type": "rule", "IDENTIFIER": v, "CONDITION": [cond], "ACTION": [update]}
        tops = [{"type": "offer_generic", "activities": [{"type": "activity", "RULES": [rule]}]}]
        back, _, _ = dsl.from_lines(dsl.to_lines(tops))
        assert back == tops, v


@pytest.fixture(scope="module")
def bf2():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE")


NATURAL = """\
counter, "Bonus Points Earned" number
Filter, activity h_bit_type == Spend
Filter, activity h_bit_amount > 0
rule_1 "Platinum double", if card "Supra Co-Branded Card" transaction card_lookup == Platinum
rule_1, if counter "Bonus Points Earned" < 2000
rule_1, give_points (activity.h_bit_amount * 2)
rule_1, update_attribute "Bonus Points Earned" INCREMENT_BY (activity.h_bit_amount * 2)
rule_2 "Gold Plan cardholders", if subscription "Gold Plan" plan_status == Active
rule_2, if card active == "HDFC Sliver"
rule_2, give_points 100
"""


def _fields(xml):
    return re.findall(r'<field name="([^"]+)">([^<]*)</field>', xml)


@pytest.mark.e2e
def test_plain_lines_build_the_blockly_xml(bf2):
    run = Run(bf2, None)
    out = run.compose({"canvas": "rules", "dsl": NATURAL})
    assert out["ok"], said(out.get("problems") or [])
    fields = _fields(run.canvases.last["rules"].xml)
    for pair in [
        ("value", "SPEND"),  # the name Spend, read as its code
        ("card_main_option", "2"),  # "Supra Co-Branded Card"
        ("onlycard_second_option", "TRANSACTION_CARD"),
        ("value", "NAJM_PLT"),  # Platinum
        ("selected_subscription_plan", "1"),  # "Gold Plan"
        ("selected_subscription_attribute", "plan_status"),
        ("card_main_option", "ACTIVE_CARDS"),
        ("cardvalue", "9"),  # "HDFC Sliver"
        ("LOYALTY_ACCOUNT", "9"),  # filled by compose: the program's account and tag
        ("REWARD_TAGS", "BASE"),
    ]:
        assert pair in fields, pair
    as_tree = Run(bf2, None)
    tops, counters, _ = dsl.from_lines(NATURAL, bf2)
    as_tree.compose({"canvas": "rules", "blocks": tops[0], "counters": counters})
    assert _fields(as_tree.canvases.last["rules"].xml) == fields  # the same XML the JSON tree gives


@pytest.mark.e2e
def test_a_problem_names_the_line(bf2):
    out = Run(bf2, None).compose(
        {"canvas": "rules", "dsl": "rule_1 Bonus, if card 2 transaction card_lookup = Platinum"}
    )
    assert not out["ok"] and "line 1 cannot be read: expected an operator" in said(out["problems"])[0]
    lines = "Filter, activity h_bit_type == SPEND\nFilter, location area in [93, 105]\nrule_1 Bonus, give_points 5"
    out = Run(bf2, None).compose({"canvas": "rules", "dsl": lines})
    assert not out["ok"] and said(out["problems"])[0].startswith("activity_criteria#L2: 'area' is a location attribute")
    out = Run(bf2, None).compose({"canvas": "segment", "dsl": lines})
    assert not out["ok"] and "dsl lines describe the rules canvas" in said(out["problems"])[0]


def _compose(cat, tops, counters):
    run = Run(cat, None)
    out = run.compose({"canvas": "rules", "counters": counters, "blocks": tops[0]})
    written = run.canvases.last.get("rules")
    xml = written.xml if written is not None and written.built else None
    return out["ok"], sorted(said(out.get("problems") or [])), xml


AGENT_BUILT = sorted(glob.glob(os.path.join(DEV, "results", "offers", "devintbf2-*", "*", "rules.xml")))


@pytest.mark.e2e
def test_lines_with_the_catalog_compose_as_the_json_tree(bf2):
    """Names, bare numbers and left-out defaults (brief) give the same XML as the tree, on every agent-built offer."""
    assert len(AGENT_BUILT) > 20
    built = named = 0
    for path in AGENT_BUILT:
        tops, counters = _canvas(open(path).read())
        text = dsl.to_lines(tops, counters, bf2, brief=True)
        back, counters_back, _ = dsl.from_lines(text, bf2)
        expected = _compose(bf2, tops, counters)
        assert _compose(bf2, _norm(back), counters_back) == expected, path
        assert not LEVEL_CODE.search(text), text
        built += expected[2] is not None
        named += "account=" not in text
    assert built > 20 and named > 20


@pytest.mark.e2e
def test_every_stored_and_agent_built_canvas_round_trips():
    paths = [os.path.join(DEV, "history-subprompt", "*", "offers.jsonl")]
    xmls = [json.loads(line)["xml"] for p in glob.glob(paths[0]) for line in open(p)]
    for pattern in ("**/rules.xml", "**/*.generated.xml", "ai-agent/baselines/*/history/*.xml"):
        xmls += [open(p).read() for p in glob.glob(os.path.join(GRAVTY, pattern), recursive=True)]
    assert len(xmls) > 1000
    wrong, coded, lines, json_chars, dsl_chars = [], 0, 0, 0, 0
    for xml in xmls:
        tops, counters, back, counters_back, text = _round_trip(xml)
        if back != tops or counters_back != counters:
            wrong.append(text[:200])
        bodies = [line.split(",", 1)[1] for line in text.splitlines()]
        coded += sum(1 for b in bodies if LEVEL_CODE.search(b) or " block " in b)
        lines += len(bodies)
        json_chars += len(json.dumps(tops, separators=(",", ":"))) + len(json.dumps(counters, separators=(",", ":")))
        dsl_chars += len(text)
    assert not wrong, wrong[:3]
    assert coded <= 10, f"{coded} of {lines} lines still carry a level code or a whole block"
    assert json_chars / dsl_chars > 2.8  # the lines are about a third of the tree the model writes today


# ---- the agent writing the rules as lines (Settings.rules_form "dsl") -------------------------------------------------
def test_compose_takes_lines_only_in_a_dsl_run(fixture_catalog):
    from offer_author.ai import prompt, tools

    props = {form: tools.schemas_for(form)[2]["input_schema"]["properties"] for form in ("json", "dsl")}
    assert "dsl" not in props["json"] and "dsl" in props["dsl"]
    assert {k: v for k, v in props["dsl"].items() if k != "dsl"} == props["json"]
    assert tools.schemas_for("json") is tools.SCHEMAS  # the JSON run's request is what it was, byte for byte
    json_text, dsl_text = prompt.static_prompt(), prompt.static_prompt("dsl")
    assert dsl_text.startswith(json_text) and "# The rules canvas as lines" in dsl_text[len(json_text) :]
    assert prompt.system_blocks(fixture_catalog) == prompt.system_blocks(fixture_catalog, rules_form="json")
    assert Settings.from_env({"OFFER_AUTHOR_RULES_FORM": "dsl"}).rules_form == "dsl" and Settings().rules_form == "json"


def test_a_dsl_run_builds_the_rules_and_repairs_by_line(fixture_catalog):
    from offer_author.ai import agent
    from tests.fakes import ScriptedLLM
    from tests.support import finish

    bad = (
        'Filter, activity h_bit_type == SPEND\nrule_1 "Bonus", if activity h_bit_amount >> 100\nrule_1, give_points 50'
    )
    good = bad.replace(">>", ">=")
    llm = ScriptedLLM(
        [
            [("compose", {"canvas": "rules", "dsl": bad})],
            [("compose", {"canvas": "rules", "dsl": good, "finish": finish(readback="50 points on 100 or more")[1]})],
        ]
    )
    res = agent.generate(
        "50 points on spends of 100 or more", fixture_catalog, llm, settings=Settings(rules_form="dsl")
    )
    first = json.loads(llm.calls[-1][2]["content"][0]["content"])
    assert first["ok"] is False and "line 2 cannot be read: expected an operator" in json.dumps(first)
    assert res["status"] == "draft" and '<field name="operators">&gt;=</field>' in res["rules"]["xml"]
    assert '<field name="IDENTIFIER">Bonus</field>' in res["rules"]["xml"]


def test_an_engine_mismatch_shows_the_lines_and_asks_for_lines():
    from offer_author.ai import cases

    report = {"scenarios": [], "mismatches": [], "compile": {"rules": ["x"]}}
    text = cases.mismatch_message(report, {"rules": {"lines": "rule_1 Bonus, give_points 50"}, "segment": None})
    assert "The rules canvas as you last wrote it (lines):\nrule_1 Bonus, give_points 50" in text
    assert "compose the rules again with dsl" in text and "block ids" not in text
    tree = cases.mismatch_message(report, {"rules": {"root": {"type": "offer_generic", "id": "b1"}}})
    assert "block ids for edits" in tree and "compose with edits by these block ids" in tree
