"""The central agent with a scripted model: the tool loop, what code checks (selections only, declared counters,
Offer Main), the finish gate, the whole-offer result, and the generated context."""

import glob
import json
import os
import re
import time

import pytest

from offer_author.ai import agent, graph, prompt, tools
from offer_author.core.canvas import build, fields, reference, tree
from offer_author.core.canvas.checks import grammar as check
from offer_author.core.canvas.checks import screen_fields
from offer_author.core.run import STOPPED
from offer_author.core.settings import Settings
from tests.fakes import ScriptedLLM
from tests.support import (
    GOLDEN,
    ROOT,
    SEGMENTS,
    checked,
    finish,
    golden,
    new_run,
    offer_main,
    public,
    said,
    saved_segment,
    selections,
)

S07 = golden("S07_every_5th_visit")
COUNTER = "Visits toward privilege 501"
FIN = finish(readback="every 5th visit earns a coffee")


def result(llm, k):
    """What the tools answered on the model's turn k (1-based); ScriptedLLM keeps the one, growing message list."""
    return json.loads(llm.calls[-1][2 * k]["content"][0]["content"])


def test_rules_from_selections_get_the_mutations_the_ui_writes(fixture_catalog, composed):
    res = agent.generate("every 5th visit", fixture_catalog, ScriptedLLM([[("compose", S07)], [FIN]]), offer_id=1001)
    assert res["status"] == "draft" and res["rules"]["grammar_problems"] == []
    xml = res["rules"]["xml"]
    assert xml == composed[0][1] and xml.count("<mutation") >= 5  # the tools wrote every mutation
    assert f'"{COUNTER}"' in xml
    # the counters are the declared ones the blocks use: the ones the stock upload dialog creates on the offer
    assert [c["name"] for c in res["rules"]["counters"]] == [COUNTER] and res["rules"]["expected_offer_id"] == 1001
    assert res["rules"]["rule_count"] == xml.count('<block type="rule"') and res["segment"] is None


def test_a_mutation_written_by_the_model_is_sent_back(fixture_catalog, composed):
    own = json.loads(json.dumps(S07))
    own["blocks"]["mutation"] = {"foo": "bar"}
    llm = ScriptedLLM([[("compose", own)], [("compose", S07)], [FIN]])
    res = agent.generate("x", fixture_catalog, llm)
    back = result(llm, 1)
    assert (
        not back["ok"]
        and "remove 'mutation'" in back["problems"][0]
        and back["problems"][0].startswith("offer_generic")
    )
    assert len(composed) == 1 and res["status"] == "draft"


def test_counters_are_declared_and_checked_against_the_offer(fixture_catalog):
    undeclared = dict(S07, counters=[])
    clash = {
        "offer_status": {"status": "live"},
        "counters": [{"name": COUNTER, "data_type": "NUMBER", "counter_type": "REGULAR"}],
    }
    llm = ScriptedLLM([[("compose", undeclared), ("compose", S07)], []])  # turn 2: what the model read of turn 1
    agent.run("x", fixture_catalog, llm, parts=("rules",), existing=clash, settings=Settings(max_turns=2))
    a, b = (json.loads(r["content"]) for r in llm.calls[-1][2]["content"])
    # left out of counters, it is declared from its use (MA_O on the blocks, incremented) and checked like any other
    assert a["declared_by_tools"] == [
        f"counter '{COUNTER}' was not in counters: declared MA_O NUMBER, from how the blocks use it"
    ]
    for r in (a, b):
        assert any(f"counter '{COUNTER}' already exists on this offer" in p and "live" in p for p in r["problems"])
    unsettled = said(checked(new_run(fixture_catalog, None), _no_level(dict(S07, counters=[]))))
    assert any(f"'{COUNTER}' is not a declared counter" in p for p in unsettled["problems"])
    vec = dict(S07, counters=[dict(S07["counters"][0], counter_type="VECTOR")])
    assert any(
        "is VECTOR: give its namespace" in p for p in said(checked(new_run(fixture_catalog, None), vec))["problems"]
    )


def _no_level(spec):
    """The tree with no level on the blocks (mta_type_value, attribute_level): its use does not settle a counter."""
    if isinstance(spec, dict):
        return {k: _no_level(v) for k, v in spec.items() if k not in ("mta_type_value", "attribute_level")}
    return [_no_level(v) for v in spec] if isinstance(spec, list) else spec


def test_a_whole_tree_sent_again_without_counters_keeps_the_earlier_declaration(fixture_catalog):
    run_ = new_run(fixture_catalog, None)
    assert said(checked(run_, S07))["ok"]
    again = said(checked(run_, _no_level(dict(S07, counters=[]))))
    assert again["ok"] and again["declared_by_tools"] == [
        f"counter '{COUNTER}' was not in counters: declared MA_O NUMBER, as declared earlier in this run"
    ]


def test_finish_waits_for_a_clean_compose(fixture_catalog, flagged):
    flagged.append("b9")
    out = said(new_run(fixture_catalog, None).compose(S07))
    # the problem says where the model put the block
    assert not out["ok"] and any("#b9 (offer_generic.activities > activity.RULES > rule " in p for p in out["problems"])

    class Healing(ScriptedLLM):
        def turn(self, *a, **k):
            if len(self.calls) == 2:
                flagged.clear()
            return super().turn(*a, **k)

    llm = Healing([[("compose", S07)], [FIN], [], [("compose", S07)], [FIN]])  # a prose turn is told to use the tools
    res = agent.generate("x", fixture_catalog, llm)
    assert "fix these" in result(llm, 2)["error"] and any("REWARD_TAGS" in p for p in result(llm, 2)["problems"])
    assert res["status"] == "draft" and res["rules"]["grammar_problems"] == [] and res["diagnostics"]["repairs"] == 1


def test_no_finish_by_the_turn_limit_asks_instead_of_a_draft(fixture_catalog):
    res = agent.generate(
        "x", fixture_catalog, ScriptedLLM([[("compose", S07)], [("compose", S07)]]), settings=Settings(max_turns=2)
    )
    assert res["status"] == "needs_input" and res["rules"] is None and res["segment"] is None
    assert res["blocking_questions"] == [STOPPED] and res["by_part"]["rules"]["blocking_questions"] == [STOPPED]


def test_a_finish_on_the_last_turn_is_judged_like_any_other(fixture_catalog, flagged):
    """It used to be taken as it was: a canvas the checks still rejected reached the manager as a draft."""
    flagged.append("b9")
    llm = ScriptedLLM([[("compose", S07)], [FIN]])
    res = agent.generate("x", fixture_catalog, llm, settings=Settings(max_turns=2))
    sent_back = [t for t in res["diagnostics"]["trace"] if t["tool"] == "finish"]
    assert "fix these" in sent_back[0]["error"] and any("REWARD_TAGS" in p for p in sent_back[0]["problems"])
    assert res["status"] == "needs_input" and res["rules"] is None and res["blocking_questions"] == [STOPPED]


def test_unsupported_without_rules(fixture_catalog):
    fin = finish(readback="", unsupported=[{"phrase": "referrals", "reason": "not a member's own transaction"}])
    res = agent.generate("reward referrals", fixture_catalog, ScriptedLLM([[fin]]))
    assert res["status"] == "unsupported" and res["rules"] is None


def test_whole_offer_in_one_run(fixture_catalog, composed):
    """Offer Main, the segment and the rules from one prompt."""
    seen_main = []
    main = offer_main(
        offer_name="Over-27 coffee",
        end_date="2026-12-31",
        max_usage_per_user=2,
        frequency="week",
        segment_prompt="Members over 27",
        rules_prompt="Every 5th visit earns a coffee",
    )
    seg = saved_segment("79_Members_Over_27")
    llm = ScriptedLLM(
        [
            [main],
            [("compose", seg), ("compose", S07)],
            [finish(segment_name="Members over 27", segment_description="Members older than 27")],
        ]
    )
    res = agent.run(
        "Members over 27 get a coffee every 5th visit until 2026-12-31, twice a week at most",
        fixture_catalog,
        llm,
        offer_id=5,
        on_offer_main=seen_main.append,
    )
    assert (
        res["status"] == "draft"
        and seen_main == [res["offer_main"]]
        and res["offer_main"]["offer_name"] == "Over-27 coffee"
    )
    assert res["offer_settings"] == {
        "start_date": None,
        "end_date": "2026-12-31",
        "max_usage_per_user": 2,
        "frequency": "week",
    }
    assert res["segment"]["name"] == "Members over 27" and "targeting_holder" in res["segment"]["xml"]
    assert [c for c, _ in composed] == ["segment", "rules"] and res["rules"]["expected_offer_id"] == 5
    assert any("member segment 'Members over 27'" in a for a in public(res)["assumptions"])


def test_offer_main_is_checked_and_required(fixture_catalog):
    bad = offer_main(offer_name="x" * 60, start_date="2026-12-01", end_date="2026-11-01")
    llm = ScriptedLLM([[bad], [("compose", S07)], [FIN], [offer_main()], [FIN]])
    res = agent.run("x", fixture_catalog, llm, parts=("offer_main", "rules"))
    first = result(llm, 1)["problems"]
    assert any("at most 50" in p for p in first) and any("before start_date" in p for p in first)
    assert "set_offer_main" in result(llm, 3)["problems"][-1]
    assert res["status"] == "draft" and res["offer_main"]["offer_name"] == "Test offer"


def test_prompt_is_knowledge_and_catalog_without_example_offers(fixture_catalog):
    blocks = prompt.system_blocks(fixture_catalog)
    static, cat = blocks[1]["text"], blocks[2]["text"]
    assert "## if_generic_adaptive" in static and "## targeting_condition" in static
    assert (
        "# How offers and blocks run in the engine" in static
        and "**Rule.**" in static
        and "### Punch Card" not in static
    )
    assert "# Usual readings of loyalty terms" in static
    # no example offers or segments: the model works from the block reference and how the engine runs blocks
    assert (
        "# Seeded blueprints" not in static and "# Saved member segments" not in static and "Counters: [" not in static
    )
    assert "<block" not in static and '"mutation"' not in static and len(static) < 50000
    assert "Tenant catalog" in cat and "h_location" in cat
    assert all(b.get("cache_control") for b in blocks[1:])


# what the model reads is the screen and how blocks run, as a manager knows them: no rule code, backend or UI source
BACKEND = re.compile(
    r"\bDRL\b|Drools|KieBase|KieSession|\bJava\b|\.java\b|\.ts\b|bolapi|Kinesis|Firehose|DynamoDb|"
    r"\bS3\b|\bLambda\b|\bJVM\b|MVEL|compiled rules|mutation|readAttribute|updateMTA|pointsAction|"
    r"\b[A-Z][a-z]+[A-Za-z]*(Impl|Service|Processor|Validator|Controller|Strategy|Helper|Generator)\b",
    re.I,
)


def test_what_the_model_reads_names_no_rule_code_backend_or_ui_source(fixture_catalog):
    texts = (
        [b["text"] for b in prompt.system_blocks(fixture_catalog, history=True)]
        + [prompt.SYSTEM]
        + [json.dumps(spec) for spec in tools.SCHEMAS]
    )
    found = sorted({m.group(0) for text in texts for m in BACKEND.finditer(text)})
    assert found == [], found


RULE_TEXT = re.compile(
    r"\bDRL\b|readAttribute|\$activity|pointsAction|BitExecutionContext\(|addValidatedSegmentId|rule x end|rule \"S\""
)


def test_the_model_never_sees_compiled_rules(fixture_catalog):
    """No compiled rules (DRL) exist here: none in the prompt, the tool schemas, any tool result or the API result."""
    system = json.dumps(prompt.system_blocks(fixture_catalog)) + json.dumps(tools.SCHEMAS)
    assert not RULE_TEXT.search(system), RULE_TEXT.search(system)
    llm = ScriptedLLM(
        [
            [offer_main()],
            [("compose", saved_segment("79_Members_Over_27")), ("compose", S07)],
            [finish(segment_name="Members over 27")],
        ]
    )
    res = agent.run("x", fixture_catalog, llm)
    seen = json.dumps(llm.calls[-1], default=str)
    assert res["status"] == "draft" and not RULE_TEXT.search(seen), RULE_TEXT.search(seen)
    assert not RULE_TEXT.search(json.dumps(res, default=str))


def test_reference_is_generated_for_both_toolboxes():
    ref = json.load(open(os.path.join(ROOT, "offer_author", "core", "data", "block_reference.json")))
    types = [b["type"] for b in ref["canvases"]["rules"]["toolbox"]]
    assert len(types) == 25 and None not in types and {"offer_generic", "if_generic_adaptive", "action"} <= set(types)
    assert {"targeting_holder", "targeting_condition"} <= {b["type"] for b in ref["canvases"]["segment"]["toolbox"]}
    assert any(p["attribute_order"][:3] == ["xmlns", "object", "attribute_level"] for p in ref["counter_patterns"])


def test_offline_check_accepts_goldens_and_catches_bad_nesting(fixture_catalog):
    for f in glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")):
        assert said(check.check(open(f).read(), fixture_catalog)) == [], f
    good = build.to_xml(S07["blocks"], fixture_catalog, {c["name"]: c for c in S07["counters"]})[0]
    bad = good.replace(
        '<statement name="ACTIVITY_CRITERIA">', '<statement name="ACTIVITY_CRITERIA"><block type="rule" id="z1"/>', 1
    )
    p = said(check.check(bad.replace('type="static_value"', 'type="blueprint_variable"', 1), fixture_catalog))
    assert any("rule cannot plug in here" in x for x in p) and any("blueprint_variable" in x for x in p)
    p = said(
        check.check(
            open(os.path.join(GOLDEN, "compiled", "S14_simple_sanity.xml"))
            .read()
            .replace('value="SPEND"', 'value="SHOPPING"'),
            fixture_catalog,
        )
    )
    assert any("SHOPPING" in x for x in p)
    p = said(
        check.check(
            open(os.path.join(GOLDEN, "compiled", "S03_combined_weekend_gold_locations.xml"))
            .read()
            .replace("L1,L2", "L1,DOWNTOWN"),
            fixture_catalog,
        )
    )
    assert any("not codes of h_location; closest: " in x for x in p)


DISPLAY_ONLY = {"visible_value", "privilegetype", "attr_type"}  # labels the UI fills in itself when it loads


@pytest.mark.parametrize(
    "files,canvas", [(os.path.join(GOLDEN, "compiled", "*.xml"), "rules"), (os.path.join(SEGMENTS, "*.xml"), "segment")]
)
def test_selections_rebuild_the_saved_mutations(files, canvas, fixture_catalog, devintam1_catalog):
    """Saved XML -> what the model writes -> XML: every mutation attribute comes back, in the order the UI saves them
    (the upload dialog's counter regexes depend on it), up to display labels. The stock UI gives the same DRL for all
    of them (harness/ui/batch/segment-run.mjs, tools/qa/check_offer_flow.py)."""
    cat = fixture_catalog if canvas == "rules" else devintam1_catalog
    mut = lambda x: [
        dict(re.findall(r'(\w+)="([^"]*)"', m))
        for m in re.findall(r"<mutation([^>]*)>", re.sub(r' xmlns="[^"]*"', "", x))
    ]
    for f in glob.glob(files):
        x = open(f).read()
        blocks, counters = selections(x)
        y, paths = build.to_xml(blocks, cat, {c["name"]: c for c in counters}, canvas)
        for a, b in zip(mut(x), mut(y), strict=False):
            keep = [k for k in a if k not in DISPLAY_ONLY]
            assert {k: a[k] for k in keep} == {k: b.get(k) for k in keep}, f
            assert [k for k in b if k in keep] == keep, f
        assert paths["b1"] == check.ROOTS[canvas]


def test_tree_errors_are_reported_not_raised_into_the_loop(fixture_catalog):
    with pytest.raises(tree.TreeError):
        build.to_xml({"type": "offer_generic", "inputs": {"activities": [{"fields": {}}]}})
    out = said(new_run(fixture_catalog, None).compose({"canvas": "rules", "blocks": {"fields": {}}}))
    assert not out["ok"] and out["problems"][0].startswith("not a block tree")


@pytest.mark.parametrize("level,has_target", [("MA_O", True), ("MA_TO", True), ("MA_A", False)])
def test_only_offer_counters_carry_a_target_type(level, has_target):
    """The UI adds the hidden target_type field for MA_O / MA_TO counters only; an MA_A attribute saved with one is
    changed by the screen (live A01-A07, SO06)."""
    spec = reference.reference()["if_generic_adaptive"]
    node = {
        "type": "if_generic_adaptive",
        "fields": {"OBJECT": "member", "attribute_level": level, "attribute": "visits"},
    }
    mut = fields.derive(
        node, spec, None, {"visits": {"name": "visits", "data_type": "NUMBER", "counter_type": "REGULAR"}}
    )
    assert mut.get("attribute_level") == level and ("target_type" in mut) == has_target, mut


def test_the_agent_is_a_langgraph_graph_of_model_tools_judge_and_nudge():
    nodes = set(graph.build().get_graph().nodes)
    assert {"model", "tools", "judge", "nudge"} <= nodes


def test_the_history_is_resent_unedited_with_one_sliding_cache_point(fixture_catalog):
    llm = ScriptedLLM(
        [
            [("lookup_values", {"object": "activity", "attribute": "h_location", "text": "downtown"})],
            [],
            [("compose", S07)],
            [FIN],
        ]
    )
    res = agent.generate("x", fixture_catalog, llm)
    assert res["status"] == "draft" and res["diagnostics"]["turns"] == 4
    first, later = llm.calls[0], llm.calls[-1]
    assert isinstance(later[0]["content"], str) and later[0]["content"] == first[0]["content"][0]["text"]
    marked = [
        i
        for i, m in enumerate(later)
        if isinstance(m["content"], list) and any(isinstance(b, dict) and b.get("cache_control") for b in m["content"])
    ]
    assert marked == [len(later) - 1]  # only the newest message carries the cache point
    assert graph.NUDGE in [m["content"] for m in later if m["role"] == "user"]  # the empty turn was nudged


def test_one_turns_lookups_run_concurrently(fixture_catalog, monkeypatch):
    import threading
    import time as t

    seen = set()
    real = fixture_catalog.lookup

    def slow(*a, **kw):
        seen.add(threading.get_ident())
        t.sleep(0.3)
        return real(*a, **kw)

    monkeypatch.setattr(fixture_catalog, "lookup", slow)
    look = [
        ("lookup_values", {"object": "activity", "attribute": "h_location", "text": w})
        for w in ("downtown", "mall", "airport")
    ]
    llm = ScriptedLLM([look, [("compose", S07)], [FIN]])
    t0 = t.time()
    res = agent.generate("x", fixture_catalog, llm)
    assert res["status"] == "draft" and len(seen) == 3 and t.time() - t0 < 0.8
    assert [r["tool_use_id"] for r in llm.calls[1][2]["content"]] == [
        f"toolu_1_{i}" for i in range(3)
    ]  # results in call order


def test_vector_counter_field_is_written_as_the_stock_dropdown_value(fixture_catalog):
    """The H11 repair round: the model wrote the namespace label, the screen rewrote it to the attribute code."""
    counters = {
        "Stores visited": {
            "name": "Stores visited",
            "data_type": "NUMBER",
            "counter_type": "VECTOR",
            "namespace": "h_location",
        }
    }
    node = {
        "type": "if_generic_adaptive",
        "fields": {
            "OBJECT": "member",
            "attribute_level": "MA_O",
            "attribute": "Stores visited",
            "vector_counter_name": " Location ",
            "operators": ">=",
        },
    }
    spec = reference.reference("rules")["if_generic_adaptive"]
    mut = fields.derive(node, spec, fixture_catalog, counters)
    assert fields.ui_fields(node, mut, counters, fixture_catalog)["vector_counter_name"] == "h_location"
    for mode in ("unique", "across"):
        assert (
            fields.ui_fields(
                dict(node, fields=dict(node["fields"], vector_counter_name=mode)), mut, counters, fixture_catalog
            )["vector_counter_name"]
            == mode
        )


def test_a_repair_sends_edits_by_block_id_not_the_whole_tree(fixture_catalog, composed):
    ident = next(n for n, _, _ in tree.nodes(tree.number(S07["blocks"])) if (n.get("fields") or {}).get("IDENTIFIER"))
    llm = ScriptedLLM(
        [
            [("compose", S07)],
            [("compose", {"canvas": "rules", "edits": [{"id": "b404", "fields": {"IDENTIFIER": "x"}}]})],
            [
                (
                    "compose",
                    {"canvas": "rules", "edits": [{"id": ident["id"], "fields": {"IDENTIFIER": "Renamed rule"}}]},
                )
            ],
            [FIN],
        ]
    )
    res = agent.generate("x", fixture_catalog, llm)
    assert "no block b404" in result(llm, 2)["problems"][0]
    assert result(llm, 3)["ok"], result(llm, 3)  # counters kept from the first compose: still declared
    assert len(composed) == 2 and "Renamed rule" in composed[1][1] and "Renamed rule" in res["rules"]["xml"]
    assert composed[0][1].count("<block") == composed[1][1].count("<block")


def test_edits_replace_and_remove_blocks():
    root = tree.number({"type": "a", "inputs": {"S": [{"type": "b"}, {"type": "c", "inputs": {"V": {"type": "d"}}}]}})
    ids = {n["type"]: n["id"] for n, _, _ in tree.nodes(root)}
    assert ids == {"a": "b1", "b": "b2", "c": "b3", "d": "b4"}  # root first, in the order the XML is written
    out = tree.apply_edits(root, [{"id": "b2", "remove": True}, {"id": "b4", "replace": {"type": "e"}}])
    assert [n["type"] for n in out["inputs"]["S"]] == ["c"] and out["inputs"]["S"][0]["inputs"]["V"]["type"] == "e"
    assert root["inputs"]["S"][0]["type"] == "b"  # the last tree itself is not changed


def test_compact_blocks_expand_to_the_node_form():
    flat = {
        "type": "action",
        "action_type": "give_points",
        "LOYALTY_ACCOUNT": "9",
        "id": "b7",
        "FORMULA": {"type": "static_value", "value": "5"},
        "RULES": [{"type": "rule", "IDENTIFIER": "R"}],
    }
    assert tree.expand(flat) == {
        "type": "action",
        "id": "b7",
        "fields": {"action_type": "give_points", "LOYALTY_ACCOUNT": "9"},
        "inputs": {
            "FORMULA": {"type": "static_value", "fields": {"value": "5"}},
            "RULES": [{"type": "rule", "fields": {"IDENTIFIER": "R"}}],
        },
    }
    # the same name as an input: the value says which; a list of codes is one field; the node form is kept as it is
    assert tree.expand({"type": "action", "LOYALTY_ACCOUNT": {"type": "x"}})["inputs"] == {
        "LOYALTY_ACCOUNT": {
            "type": "x",
        }
    }
    assert tree.expand({"type": "c", "value": ["A", "B"]})["fields"] == {"value": "A,B"}
    node = {
        "type": "rule",
        "fields": {"IDENTIFIER": "R"},
        "inputs": {"ACTION": [{"type": "action", "fields": {"action_type": "x"}}]},
    }
    assert tree.expand(node) == node


def test_the_tools_fill_the_fields_the_model_leaves_out(fixture_catalog):
    counters = {
        "Visits": {"name": "Visits", "level": "MA_A", "data_type": "NUMBER", "counter_type": "REGULAR"},
        "Total": {"name": "Total", "data_type": "NUMBER", "counter_type": "REGULAR"},
    }
    fill = lambda n: list(fields.fill(tree.expand(n), fixture_catalog, counters).items())
    assert fill({"type": "if_generic_adaptive", "attribute": "Visits", "operators": ">="}) == [
        ("OBJECT", "member"),
        ("attribute_level", "MA_A"),
        ("attribute", "Visits"),
        ("operators", ">="),
    ]
    assert fill({"type": "activity_criteria", "attribute": "h_bit_amount", "operators": ">"})[0] == (
        "OBJECT",
        "activity",
    )
    assert fill({"type": "action", "action_type": "give_points"}) == [
        ("action_type", "give_points"),
        ("LOYALTY_ACCOUNT", fixture_catalog.default_account),
        ("REWARD_TAGS", fixture_catalog.reward_tag()),
    ]
    assert fill({"type": "action", "action_type": "update_attribute", "mta_attribute": "Total"})[1] == (
        "mta_type_value",
        "MA_O",
    )
    # what the model gave stays; an attribute the tools cannot place gets nothing
    assert fill({"type": "if_generic_adaptive", "OBJECT": "sponsor", "attribute": "x"}) == [
        ("OBJECT", "sponsor"),
        ("attribute", "x"),
    ]
    assert fill({"type": "if_generic_adaptive", "attribute": "no_such_attribute"}) == [
        ("attribute", "no_such_attribute")
    ]


def test_saved_rules_round_trip_through_the_compact_form(fixture_catalog):
    """The compact form the examples show, expanded and filled, writes the same XML as the saved tree."""
    same = 0
    for f in sorted(glob.glob(os.path.join(GOLDEN, "compiled", "*.xml"))):
        blocks, counters = selections(open(f).read())
        actions = [n for n, _, _ in tree.nodes(blocks) if (n.get("fields") or {}).get("action_type") == "give_points"]
        if any(
            (n["fields"].get("LOYALTY_ACCOUNT"), n["fields"].get("REWARD_TAGS"))
            != (fixture_catalog.default_account, fixture_catalog.reward_tag())
            for n in actions
        ):
            continue  # another account or tag: the model gives those itself
        by = {c["name"]: c for c in counters}
        assert (
            build.to_xml(tree.expand(tree.compact(blocks, counters)), fixture_catalog, by)[0]
            == build.to_xml(blocks, fixture_catalog, by)[0]
        ), f
        same += 1
    assert same >= 5, same


def test_a_malformed_tool_input_is_sent_back_not_a_failed_job(fixture_catalog):
    llm = ScriptedLLM([[("compose", {"canvas": "offer"})], [("compose", S07)], [FIN]])
    res = agent.generate("x", fixture_catalog, llm)
    assert "compose input does not match its schema (canvas:" in result(llm, 1)["error"]
    assert res["status"] == "draft", res.get("message")


def test_a_tool_failing_on_a_valid_input_is_sent_back_not_a_failed_job(fixture_catalog, monkeypatch):
    from offer_author.core.run.canvases import Canvases

    real, calls = Canvases.compose, []

    def broken_once(self, x):
        calls.append(1)
        if len(calls) == 1:
            raise AttributeError("'str' object has no attribute 'get'")
        return real(self, x)

    monkeypatch.setattr(Canvases, "compose", broken_once)
    llm = ScriptedLLM([[("compose", S07)], [("compose", S07)], [FIN]])
    res = agent.generate("x", fixture_catalog, llm)
    assert "compose could not read this input" in result(llm, 1)["error"]
    assert res["status"] == "draft", res.get("message")


def test_an_edit_may_name_fields_and_inputs_directly():
    root = tree.number({"type": "a", "fields": {"operators": "=="}, "inputs": {"V": {"type": "d"}}})
    out = tree.apply_edits(root, [{"id": "b1", "operators": ">", "V": {"type": "e", "value": "5"}}])
    assert out["fields"] == {"operators": ">"} and out["inputs"]["V"] == {"type": "e", "fields": {"value": "5"}}


def test_tool_calls_start_while_the_turn_streams_and_keep_their_order(fixture_catalog, composed):
    seen = []

    class Streaming(ScriptedLLM):  # reports each call as it "streams", then keeps writing for a moment
        def turn(self, system, messages, tools=None, on_tool=None):
            t = super().turn(system, messages, tools, on_tool)
            for _ in range(200):
                if composed:
                    break
                time.sleep(0.01)
            seen.append(len(composed))  # the compose ran while the turn was still being written
            return t

    res = agent.generate("x", fixture_catalog, Streaming([[("compose", S07), FIN]]))
    assert seen == [1]
    assert res["status"] == "draft" and res["diagnostics"]["turns"] == 1  # finish judged after the compose, in order


def test_defs_are_written_once_and_copied_in_where_used():
    week = {
        "type": "arithmetic_generic",
        "operator": "+",
        "LHS": {"type": "static_value", "value": "1"},
        "RHS": {"use": "two"},
    }
    defs = {
        "week": week,
        "two": {"type": "static_value", "value": "2"},
        "checks": [
            {"type": "if_generic_adaptive", "attribute": "A"},
            {"type": "if_generic_adaptive", "attribute": "B"},
        ],
    }
    root = {
        "type": "rule",
        "CONDITION": [{"use": "checks"}, {"type": "x", "RHS_INPUT": {"use": "week"}}],
        "ACTION": [{"use": "two"}],
    }
    out = tree.inline(root, defs)
    assert [c.get("attribute") or c["type"] for c in out["CONDITION"]] == ["A", "B", "x"]  # a list def is spliced in
    assert out["CONDITION"][2]["RHS_INPUT"]["RHS"] == {"type": "static_value", "value": "2"}  # defs use defs
    for bad, why in (
        ({"use": "nope"}, "no def 'nope'"),
        ({"use": "loop"}, "uses itself"),
        ({"use": "checks"}, "several blocks"),
    ):
        with pytest.raises(tree.TreeError) as e:
            tree.inline({"type": "a", "V": bad}, {**defs, "loop": {"type": "b", "V": {"use": "loop"}}})
        assert why in said(e.value.problem)


def test_the_session_keeps_a_canvas_defs_for_later_composes(fixture_catalog, composed):
    blocks, counters = selections(open(os.path.join(GOLDEN, "compiled", "S07_every_5th_visit.xml")).read())
    flat = tree.compact(blocks, counters)
    shared, defs = dict(flat, activities=[{"use": "activity"}]), {"activity": flat["activities"][0]}
    llm = ScriptedLLM(
        [
            [("compose", {"canvas": "rules", "blocks": shared, "defs": defs, "counters": counters})],
            [("compose", {"canvas": "rules", "blocks": shared, "counters": counters})],  # defs kept
            [("compose", {"canvas": "rules", "blocks": {"type": "offer_generic", "activities": [{"use": "x"}]}})],
            [FIN],
        ]
    )
    agent.generate("x", fixture_catalog, llm)
    assert result(llm, 1)["ok"] and result(llm, 2)["ok"] and composed[0][1] == composed[1][1]
    assert "no def 'x'" in result(llm, 3)["problems"][0]


def test_a_level_the_tools_cannot_fill_is_reported_before_any_xml(fixture_catalog, composed):
    rules = {
        "type": "offer_generic",
        "activities": [
            {
                "type": "activity",
                "RULES": [
                    {
                        "type": "rule",
                        "IDENTIFIER": "R",
                        "ACTION": [
                            {
                                "type": "action",
                                "action_type": "update_attribute",
                                "mta_attribute": "In Window",
                                "action_operator": "SET",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    llm = ScriptedLLM([[("compose", {"canvas": "rules", "blocks": rules})], [FIN]])
    agent.generate("x", fixture_catalog, llm)
    out = result(llm, 1)
    assert not out["ok"] and "'In Window' is not a declared counter" in out["problems"][0] and not composed


def test_segment_needs_a_name_from_the_model(fixture_catalog):
    llm = ScriptedLLM(
        [
            [offer_main()],
            [("compose", saved_segment("79_Members_Over_27")), ("compose", S07)],
            [finish()],
            [finish(segment_name="Members over 27")],
        ]
    )
    res = agent.run("x", fixture_catalog, llm)
    assert "segment_name is empty" in json.dumps(result(llm, 3)) and res["segment"]["name"] == "Members over 27"


def test_reward_tag_is_the_models_choice_when_the_tenant_configured_none(fixture_catalog):
    import dataclasses

    cat = dataclasses.replace(fixture_catalog, default_tag=None)  # the account has BASE and BONUS
    assert cat.reward_tag() is None and "no configured reward tag" in prompt.system_blocks(cat)[2]["text"]
    probs = said(
        screen_fields.unfilled(
            tree.expand(tree.inline(S07["blocks"], S07.get("defs"))),
            cat,
            {c["name"]: c for c in S07.get("counters", [])},
        )
    )
    assert any("give REWARD_TAGS" in p for p in probs) == ('"give_points"' in json.dumps(S07))


def test_offer_main_form_rules_come_from_the_ui_and_bolapi_sources():
    """core/data/offer_main_form.json is generated (tools/build/offer_main_form.py); regenerating gives the same."""
    from offer_author.core.run import offer_main_problems
    from offer_author.core.vocabulary import OFFER_MAIN_FORM
    from tools.build import offer_main_form

    ui, bolapi = (
        os.path.expanduser("~/Downloads/newui-develop/gravty-ui"),
        os.path.expanduser("~/Downloads/bolapi-develop"),
    )
    if not (os.path.isdir(ui) and os.path.isdir(bolapi)):
        pytest.skip("UI / bolapi sources not here")
    assert offer_main_form.build(ui, bolapi) == OFFER_MAIN_FORM
    assert offer_main_problems({"offer_name": "x" * 50, "frequency": "week"}) == []
    assert said(offer_main_problems({"offer_name": "x", "frequency": "year"})) == [
        "frequency must be one of 'none', 'day', 'month', 'total', 'week'"
    ]


@pytest.mark.parametrize(
    "leaf, problem",
    [
        ({"operators": "==", "picked": "SPEND"}, "give one code in value, not picked"),
        ({"operators": "in", "value": "SPEND"}, "give the codes in picked, not value"),
        ({"operators": "==", "value": "SPEND"}, None),
        ({"operators": "in", "picked": "SPEND"}, None),
    ],
)
def test_a_coded_value_in_the_form_its_operator_does_not_take_is_reported_before_compile(
    fixture_catalog, leaf, problem
):
    """filter.block.ts updateShapeByOperator_: in / not_in open the popup, other operators a dropdown; the other form
    is dropped by the screen or replaced by the dropdown's first code."""
    node = {"type": "activity_criteria", "id": "b3", "OBJECT": "activity", "attribute": "h_bit_type", **leaf}
    got = [p for p in said(screen_fields.unfilled(tree.expand(node), fixture_catalog, {})) if "coded" in p]
    assert (problem in got[0]) if problem else not got


def test_the_block_reference_states_which_operators_take_picked_codes():
    ref = reference.reference("rules")
    assert ref["activity_criteria"]["picks_by_operator"] and ref["if_generic_adaptive"]["picks_by_operator"]
    assert "operators in, not_in take them in `picked`" in prompt.static_prompt()


def test_the_knowledge_says_a_transaction_date_is_the_bit_date_not_the_current_date():
    """C02 full flow (2026-10-31): the birthday month was compared with the current date block (the engine's
    processing time), so test BITs dated ahead never matched."""
    text = prompt.static_prompt()
    assert "The current date block is the moment the engine processes the BIT" in text
    assert "compares a part of `h_bit_date`" in text
    assert "the BIT date (`h_bit_date`), never the current date block" in text


def test_a_finish_is_judged_after_the_turns_other_calls(fixture_catalog, flagged):
    """A finish written before a compose in the same turn used to be accepted on the earlier clean compose."""

    class BadSecondCompose(ScriptedLLM):
        def turn(self, *a, **k):
            if self.calls:
                flagged.append("b9")
            return super().turn(*a, **k)

    llm = BadSecondCompose([[("compose", S07)], [FIN, ("compose", S07)]])
    res = agent.generate("x", fixture_catalog, llm, settings=Settings(max_turns=2, early_tools=False))
    sent_back = [t for t in res["diagnostics"]["trace"] if t["tool"] == "finish"]
    assert "fix these" in sent_back[0]["error"] and res["rules"] is None


def test_tool_calls_keep_the_order_the_model_wrote_them():
    """An unparseable call was moved after every valid one, so a finish could be judged before it."""
    from langchain_core.messages import AIMessage

    from offer_author.ai.model import _turn, ordered_calls
    from tests.fakes import wire

    msg = AIMessage(
        content=[
            {"type": "tool_use", "id": "a", "name": "compose", "input": {}},
            {"type": "tool_use", "id": "b", "name": "finish", "input": {}},
        ],
        tool_calls=[{"id": "b", "name": "finish", "args": {}, "type": "tool_call"}],
        invalid_tool_calls=[{"id": "a", "name": "compose", "args": "{bad", "error": None, "type": "invalid_tool_call"}],
    )
    assert [c[0] for c in ordered_calls(msg)] == ["a", "b"]
    turn = _turn(msg, "tool_use", {})  # every call on the message, in order; the unparsed one sent as before
    assert [c["id"] for c in turn.message.tool_calls] == ["a", "b"] and turn.invalid == {"a"}
    assert wire([turn.message]) == wire([msg])


def test_a_call_whose_input_is_not_json_gets_its_schema_error(fixture_catalog):
    from langchain_core.messages import AIMessage

    from offer_author.ai.model import _turn

    class Garbled(ScriptedLLM):
        def turn(self, system, messages, tools=None, on_tool=None):
            t = super().turn(system, messages, tools, on_tool)
            if len(self.calls) == 1:
                bad = AIMessage(
                    content=[],
                    invalid_tool_calls=[
                        {"id": "z", "name": "compose", "args": "{bad", "error": None, "type": "invalid_tool_call"}
                    ],
                )
                return _turn(bad, "tool_use", {})
            return t

    llm = Garbled([[], [FIN]])
    agent.generate("x", fixture_catalog, llm)
    err = json.loads(llm.calls[-1][2]["content"][0]["content"])
    assert (
        err["ok"] is False
        and "compose input does not match its schema (input: Input should be a valid dictionary" in err["error"]
    )


def test_problem_ids_map_back_whole_ids_only():
    from offer_author.domain import BlockRef, Problem, renamed

    back = {"b1": "b5", "b12": "b12"}  # saved block b5 carries Blockly id "b1"
    got = renamed(
        [Problem.of("no_reward_tag", BlockRef("action", "b1")), Problem.of("no_reward_tag", BlockRef("rule", "b12"))],
        back,
    )
    assert [p.split(":")[0] for p in said(got)] == ["action#b5", "rule#b12"]


def test_a_rule_with_no_action_is_reported(fixture_catalog):
    """C08 (2026-10-30): the "every $1000 mark" rule was written with a condition and no action, so it never paid."""
    rule = {"type": "rule", "id": "b4", "IDENTIFIER": "Bonus per 1000 mark", "inputs": {"CONDITION": []}}
    assert said(screen_fields.unfilled(tree.expand(rule), fixture_catalog, {})) == [
        "rule 'Bonus per 1000 mark' has no action: it never rewards or counts anything"
    ]
    s07 = tree.expand(tree.inline(S07["blocks"], S07.get("defs")))
    assert not any("has no action" in p for p in said(screen_fields.unfilled(s07, fixture_catalog, {})))


def test_the_conventions_read_an_offer_as_its_own_reward_and_reaches_as_at_least():
    """TwoBranchVerify measurement (2026-10-31): H05 and H10 test cases misread these two."""
    text = prompt.static_prompt()
    assert "a transaction that does not qualify earns nothing from this offer" in text
    assert '"reaches X", "hits X", "once the total is X": greater or equal (>=)' in text
