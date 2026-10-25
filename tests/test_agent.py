"""The central agent with a scripted model and a fake compile service: the tool loop, what code checks (selections
only, declared counters, Offer Main), the finish gate, the whole-offer result, and the generated context."""
import glob
import json
import os
import re

import pytest

from conftest import GOLDEN, ROOT, SEGMENTS, finish, golden, offer_main, saved_segment
from offer_author.ai import agent, prompt, tool_specs
from offer_author.core import session
from offer_author.core import check, engine, tree, ui
from offer_author.ai.llm import ScriptedLLM

S07 = golden("S07_every_5th_visit")
COUNTER = "Visits toward privilege 501"
FIN = finish(readback="every 5th visit earns a coffee")


def result(llm, k):
    """What the tools answered on the model's turn k (1-based); ScriptedLLM keeps the one, growing message list."""
    return json.loads(llm.calls[-1][2 * k]["content"][0]["content"])


def test_rules_from_selections_get_the_mutations_the_ui_writes(fixture_catalog, fake_ui):
    res = agent.generate("every 5th visit", fixture_catalog, ScriptedLLM([[("compile", S07)], [FIN]]), offer_id=1001)
    assert res["status"] == "draft" and res["rules"]["grammar_problems"] == []
    xml = res["rules"]["xml"]
    assert xml == fake_ui[0][1] and xml.count("<mutation") >= 5  # the tools wrote every mutation
    assert f'"{COUNTER}"' in xml
    # the counters are the ones the stock upload dialog created, for this offer
    assert [(c["name"], c["offer"]) for c in res["rules"]["counters"]] == [(COUNTER, 1001)]
    assert res["rules"]["rule_count"] == xml.count('<block type="rule"') and res["segment"] is None


def test_a_mutation_written_by_the_model_is_sent_back(fixture_catalog, fake_ui):
    own = json.loads(json.dumps(S07))
    own["blocks"]["mutation"] = {"foo": "bar"}
    llm = ScriptedLLM([[("compile", own)], [("compile", S07)], [FIN]])
    res = agent.generate("x", fixture_catalog, llm)
    back = result(llm, 1)
    assert not back["ok"] and "remove 'mutation'" in back["problems"][0] and back["problems"][0].startswith("offer_generic")
    assert len(fake_ui) == 1 and res["status"] == "draft"


def test_counters_are_declared_and_checked_against_the_offer(fixture_catalog, fake_ui):
    undeclared = dict(S07, counters=[])
    clash = {"offer_status": {"status": "live"}, "counters": [{"name": COUNTER, "data_type": "NUMBER", "counter_type": "REGULAR"}]}
    llm = ScriptedLLM([[("validate", undeclared), ("validate", S07)], [FIN]])
    agent.run("x", fixture_catalog, llm, parts=("rules",), existing=clash)
    a, b = (json.loads(r["content"]) for r in llm.calls[-1][2]["content"])
    assert any(f"offer counter '{COUNTER}', which is not declared" in p for p in a["problems"])
    assert any(f"counter '{COUNTER}' already exists on this offer" in p and "live" in p for p in b["problems"])
    vec = dict(S07, counters=[dict(S07["counters"][0], counter_type="VECTOR")])
    assert any("is VECTOR: give its namespace" in p for p in session.Session(fixture_catalog, None).run("validate", vec)["problems"])


def test_finish_waits_for_a_clean_compile(fixture_catalog, fake_ui):
    fake_ui.bad["rules"] = [{"block": "action#b9", "field": "REWARD_TAGS", "in": "NOPE"}]
    out = session.Session(fixture_catalog, None).run("compile", S07)
    # the problem says where the model put the block
    assert not out["ok"] and any("#b9 (offer_generic.activities > activity.RULES > rule " in p for p in out["problems"])

    class Healing(ScriptedLLM):
        def turn(self, *a, **k):
            if len(self.calls) == 2:
                fake_ui.bad.clear()
            return super().turn(*a, **k)
    llm = Healing([[("compile", S07)], [FIN], [], [("compile", S07)], [FIN]])  # a prose turn is told to use the tools
    res = agent.generate("x", fixture_catalog, llm)
    assert "fix these" in result(llm, 2)["error"] and any("REWARD_TAGS" in p for p in result(llm, 2)["problems"])
    assert res["status"] == "draft" and res["rules"]["grammar_problems"] == [] and res["diagnostics"]["repairs"] == 1


def test_no_finish_still_returns_the_last_clean_draft(fixture_catalog, fake_ui, monkeypatch):
    monkeypatch.setattr(agent, "MAX_TURNS", 2)
    res = agent.generate("x", fixture_catalog, ScriptedLLM([[("compile", S07)], [("validate", S07)]]))
    assert res["status"] == "draft" and res["rules"]["xml"] == fake_ui[0][1]
    assert any("ran out of steps" in a for a in res["assumptions"])


def test_unsupported_without_rules(fixture_catalog, fake_ui):
    fin = finish(readback="", unsupported=[{"phrase": "referrals", "reason": "not a member's own transaction"}])
    res = agent.generate("reward referrals", fixture_catalog, ScriptedLLM([[fin]]))
    assert res["status"] == "unsupported" and res["rules"] is None


def test_compile_service_down_is_an_error(fixture_catalog, monkeypatch):
    def down(*a, **k):
        raise ui.CompileUnavailable("not reachable")
    monkeypatch.setattr(ui, "compile_xml", down)
    res = agent.generate("x", fixture_catalog, ScriptedLLM([[("compile", S07)]]))
    assert res["status"] == "error" and "compiler is unavailable" in res["message"]


def test_whole_offer_in_one_run(fixture_catalog, fake_ui, monkeypatch):
    """Offer Main, the segment and the rules from one prompt; simulate runs with the segment DRL and Offer Main."""
    got, seen_main = {}, []
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "simulate", lambda drl, sc, **kw: got.update(drl=drl, **kw) or {"compile": "ok", "bits": [{"ok": True}]})
    main = offer_main(offer_name="Over-27 coffee", end_date="2026-12-31", max_usage_per_user=2, frequency="week",
                      segment_prompt="Members over 27", rules_prompt="Every 5th visit earns a coffee")
    seg = saved_segment("79_Members_Over_27")
    llm = ScriptedLLM([[main], [("compile", seg), ("compile", S07)], [("simulate", {"bits": [{"expect": {"points": 0}}]})],
                       [finish(segment_name="Members over 27", segment_description="Members older than 27")]])
    res = agent.run("Members over 27 get a coffee every 5th visit until 2026-12-31, twice a week at most", fixture_catalog,
                    llm, offer_id=5, on_offer_main=seen_main.append)
    assert res["status"] == "draft" and seen_main == [res["offer_main"]] and res["offer_main"]["offer_name"] == "Over-27 coffee"
    assert res["offer_settings"] == {"start_date": None, "end_date": "2026-12-31", "max_usage_per_user": 2, "frequency": "week"}
    assert res["segment"]["name"] == "Members over 27" and "targeting_holder" in res["segment"]["xml"]
    assert [c for c, _ in fake_ui] == ["segment", "rules"] and res["rules"]["counters"][0]["offer"] == 5
    assert got["segment_drl"].startswith('rule "S"') and got["offer_main"]["max_usage_per_user"] == 2
    assert any("member segment 'Members over 27'" in a for a in res["assumptions"])


def test_offer_main_is_checked_and_required(fixture_catalog, fake_ui):
    bad = offer_main(offer_name="x" * 60, start_date="2026-12-01", end_date="2026-11-01")
    llm = ScriptedLLM([[bad], [("compile", S07)], [FIN], [offer_main()], [FIN]])
    res = agent.run("x", fixture_catalog, llm, parts=("offer_main", "rules"))
    first = result(llm, 1)["problems"]
    assert any("under 50" in p for p in first) and any("before start_date" in p for p in first)
    assert "set_offer_main" in result(llm, 3)["problems"][-1]
    assert res["status"] == "draft" and res["offer_main"]["offer_name"] == "Test offer"


def test_simulate_needs_a_rules_compile_first(fixture_catalog, fake_ui):
    llm = ScriptedLLM([[("simulate", {"bits": [{"expect": {"points": 1}}]})], [FIN]])
    agent.generate("x", fixture_catalog, llm)
    assert "compile the rules canvas first" in result(llm, 1)["error"]


def test_prompt_carries_both_canvases_kt_examples_and_catalog(fixture_catalog):
    blocks = prompt.system_blocks(fixture_catalog)
    static, cat = blocks[1]["text"], blocks[2]["text"]
    assert "## if_generic_adaptive" in static and "## targeting_condition" in static
    assert "Runtime evaluation" in static and "### Punch Card Rewards" in static and "How each rule wrapper runs" in static
    assert "# Saved member segments" in static and '"type":"targeting_holder"' in static
    # examples are shown in the form the model writes: selections and counters, never XML or mutations
    assert '{"type":"offer_generic"' in static and "<block" not in static and '"mutation"' not in static
    assert "Counters: [" in static
    assert "Tenant catalog" in cat and "h_location" in cat
    assert all(b.get("cache_control") for b in blocks[1:])


RULE_TEXT = re.compile(r"\bDRL\b|readAttribute|\$activity|pointsAction|BitExecutionContext\(|addValidatedSegmentId|rule x end|rule \"S\"")


def test_the_model_never_sees_compiled_rules(fixture_catalog, fake_ui, monkeypatch):
    """The compiled rules (DRL) are private: not in the prompt, the tool schemas, any tool result or the API result."""
    system = json.dumps(prompt.system_blocks(fixture_catalog)) + json.dumps(tool_specs.TOOLS)
    assert not RULE_TEXT.search(system), RULE_TEXT.search(system)
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(engine, "simulate", lambda drl, sc, **kw: {"compile": "error", "detail": f"line 3: {drl} {kw['segment_drl']}"})
    llm = ScriptedLLM([[offer_main()], [("compile", saved_segment("79_Members_Over_27")), ("compile", S07)],
                       [("simulate", {"bits": [{}]})], [finish()]])
    res = agent.run("x", fixture_catalog, llm)
    seen = json.dumps(llm.calls[-1], default=str)
    assert "the engine could not compile" in seen and not RULE_TEXT.search(seen), RULE_TEXT.search(seen)
    assert not RULE_TEXT.search(json.dumps(res, default=str))


def test_reference_is_generated_for_both_toolboxes():
    ref = json.load(open(os.path.join(ROOT, "offer_author", "core", "data", "block_reference.json")))
    types = [b["type"] for b in ref["canvases"]["rules"]["toolbox"]]
    assert len(types) == 25 and None not in types and {"offer_generic", "if_generic_adaptive", "action"} <= set(types)
    assert {"targeting_holder", "targeting_condition"} <= {b["type"] for b in ref["canvases"]["segment"]["toolbox"]}
    assert any(p["attribute_order"][:3] == ["xmlns", "object", "attribute_level"] for p in ref["counter_patterns"])


def test_offline_check_accepts_goldens_and_catches_bad_nesting(fixture_catalog):
    for f in glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")):
        assert check.check(open(f).read(), fixture_catalog) == [], f
    good = tree.to_xml(S07["blocks"], fixture_catalog, {c["name"]: c for c in S07["counters"]})[0]
    bad = good.replace('<statement name="ACTIVITY_CRITERIA">', '<statement name="ACTIVITY_CRITERIA"><block type="rule" id="z1"/>', 1)
    p = check.check(bad.replace('type="static_value"', 'type="blueprint_variable"', 1), fixture_catalog)
    assert any("rule cannot plug in here" in x for x in p) and any("blueprint_variable" in x for x in p)
    p = check.check(open(os.path.join(GOLDEN, "compiled", "S14_simple_sanity.xml")).read().replace('value="SPEND"', 'value="SHOPPING"'), fixture_catalog)
    assert any("SHOPPING" in x for x in p)
    p = check.check(open(os.path.join(GOLDEN, "compiled", "S03_combined_weekend_gold_locations.xml")).read().replace("L1,L2", "L1,DOWNTOWN"), fixture_catalog)
    assert any("not codes of h_location; closest: " in x for x in p)


DISPLAY_ONLY = {"visible_value", "privilegetype", "attr_type"}  # labels the UI fills in itself when it loads


@pytest.mark.parametrize("files,canvas", [(os.path.join(GOLDEN, "compiled", "*.xml"), "rules"), (os.path.join(SEGMENTS, "*.xml"), "segment")])
def test_selections_rebuild_the_saved_mutations(files, canvas, fixture_catalog, devintam1_catalog):
    """Saved XML -> what the model writes -> XML: every mutation attribute comes back, in the order the UI saves them
    (the upload dialog's counter regexes depend on it), up to display labels. The stock UI gives the same DRL for all
    of them (harness/ui-harness/segment-run.mjs, tools/check_offer_flow.py)."""
    cat = fixture_catalog if canvas == "rules" else devintam1_catalog
    mut = lambda x: [dict(re.findall(r'(\w+)="([^"]*)"', m)) for m in re.findall(r"<mutation([^>]*)>", re.sub(r' xmlns="[^"]*"', "", x))]  # noqa: E731
    for f in glob.glob(files):
        x = open(f).read()
        blocks, counters = tree.selections(x)
        y, paths = tree.to_xml(blocks, cat, {c["name"]: c for c in counters}, canvas)
        for a, b in zip(mut(x), mut(y)):
            keep = [k for k in a if k not in DISPLAY_ONLY]
            assert {k: a[k] for k in keep} == {k: b.get(k) for k in keep}, f
            assert [k for k in b if k in keep] == keep, f
        assert paths["b1"] == check.ROOTS[canvas]


def test_tree_errors_are_reported_not_raised_into_the_loop(fixture_catalog):
    with pytest.raises(tree.TreeError):
        tree.to_xml({"type": "offer_generic", "inputs": {"activities": [{"fields": {}}]}})
    out = session.Session(fixture_catalog, None).run("compile", {"canvas": "rules", "blocks": {"fields": {}}})
    assert not out["ok"] and out["problems"][0].startswith("not a block tree")


@pytest.mark.parametrize("level,has_target", [("MA_O", True), ("MA_TO", True), ("MA_A", False)])
def test_only_offer_counters_carry_a_target_type(level, has_target):
    """The UI adds the hidden target_type field for MA_O / MA_TO counters only; an MA_A attribute saved with one is
    changed by the screen (live A01-A07, SO06)."""
    spec = tree.reference()["if_generic_adaptive"]
    node = {"type": "if_generic_adaptive", "fields": {"OBJECT": "member", "attribute_level": level, "attribute": "visits"}}
    mut = tree.derive(node, spec, None, {"visits": {"name": "visits", "data_type": "NUMBER", "counter_type": "REGULAR"}})
    assert mut.get("attribute_level") == level and ("target_type" in mut) == has_target, mut
