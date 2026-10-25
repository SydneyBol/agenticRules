"""Blocks mode (OFFER_AUTHOR_ENGINE=blocks) with a scripted model and a fake compile service: the tool loop, the
finish gate, the result shape, and the offline checks driven by the generated block reference."""
import json
import os

import pytest

from conftest import GOLDEN, ROOT
from offer_author.blocks import agent, check, tree, ui
from offer_author.llm import ScriptedLLM
from offer_author.pipeline import generate

TREE = tree.from_xml(open(os.path.join(GOLDEN, "compiled", "S07_every_5th_visit.xml")).read())
GOOD = tree.to_xml(TREE)[0]
FIN = ("finish", {"readback": "every 5th visit earns a coffee", "assumptions": [], "blocking_questions": [], "unsupported": []})
COUNTER = {"offer": 1, "name": "Visits toward privilege 501", "data_type": "NUMBER", "level": "OFFER", "counter_type": "REGULAR"}


def result(llm, k):
    """What the tools answered on the model's turn k (1-based); ScriptedLLM keeps the one, growing message list."""
    return json.loads(llm.calls[-1][2 * k]["content"][0]["content"])


@pytest.fixture
def fake_ui(monkeypatch):
    """compile_xml stand-in: clean for GOOD, a field of block b9 dropped for anything else."""
    seen = []

    def compile_xml(xml, tenant=None, **_):
        seen.append(xml)
        ok = xml == GOOD
        return {"loaded": True, "drl": "rule x end" if ok else "rule y end", "counters_created": [COUNTER] if ok else [],
                "dropped_fields": [] if ok else [{"block": "action#b9", "field": "REWARD_TAGS", "in": "NOPE"}],
                "changed_fields": [], "mutation_diffs": [], "validation_errors": [], "exceptions": []}
    monkeypatch.setattr(ui, "compile_xml", compile_xml)
    return seen


def test_engine_switch_routes_to_blocks_mode(monkeypatch, fixture_catalog, fake_ui):
    monkeypatch.setenv("OFFER_AUTHOR_ENGINE", "blocks")
    res = generate("every 5th visit", fixture_catalog, ScriptedLLM([[("compile", {"blocks": TREE})], [FIN]]), offer_id=1001)
    assert res["diagnostics"]["engine"] == "blocks"
    assert res["status"] == "draft" and res["rules"]["xml"] == GOOD and res["rules"]["grammar_problems"] == []
    # the counters are the ones the stock upload dialog created, for this offer
    assert [(c["name"], c["offer"]) for c in res["rules"]["counters"]] == [("Visits toward privilege 501", 1001)]
    assert res["rules"]["rule_count"] == GOOD.count('<block type="rule"')


def test_finish_after_a_failed_compile_is_sent_back(fixture_catalog, fake_ui):
    bad = dict(TREE, id="other")  # any other tree: the fake UI drops a field
    llm = ScriptedLLM([[("compile", {"blocks": bad})], [FIN], [("compile", {"blocks": TREE})], [FIN]])
    res = agent.generate("every 5th visit", fixture_catalog, llm, offer_id=1001)
    back = result(llm, 2)
    assert "fix them" in back["error"] and any("REWARD_TAGS" in p for p in back["problems"])
    # the problem names where the model put the block
    assert any("#b9 (offer_generic.activities > activity.RULES > rule " in p for p in back["problems"])
    assert res["status"] == "draft" and res["rules"]["xml"] == GOOD and res["diagnostics"]["repairs"] == 1


def test_compile_problems_reach_the_model_with_the_drl(fixture_catalog, fake_ui):
    llm = ScriptedLLM([[("compile", {"blocks": {"fields": {}}})], [("compile", {"blocks": TREE})], [FIN]])
    agent.generate("x", fixture_catalog, llm)
    broken = result(llm, 1)
    assert not broken["ok"] and broken["problems"][0].startswith("not a block tree")
    assert len(fake_ui) == 1  # a broken tree never goes to the UI
    ok = result(llm, 2)
    assert ok["ok"] and ok["drl"] == "rule x end" and ok["counters_created"][0]["name"] == COUNTER["name"]


def test_no_finish_still_returns_the_last_clean_draft(fixture_catalog, fake_ui, monkeypatch):
    monkeypatch.setattr(agent, "MAX_TURNS", 2)
    res = agent.generate("x", fixture_catalog, ScriptedLLM([[("compile", {"blocks": TREE})], [("validate", {"blocks": TREE})]]))
    assert res["status"] == "draft" and res["rules"]["xml"] == GOOD
    assert any("ran out of steps" in a for a in res["assumptions"])


def test_unsupported_without_rules(fixture_catalog, fake_ui):
    fin = ("finish", {"readback": "", "assumptions": [], "blocking_questions": [],
                      "unsupported": [{"phrase": "referrals", "reason": "not a member's own transaction"}]})
    res = agent.generate("reward referrals", fixture_catalog, ScriptedLLM([[fin]]))
    assert res["status"] == "unsupported" and res["rules"] is None


def test_compile_service_down_is_an_error(fixture_catalog, monkeypatch):
    def down(*a, **k):
        raise ui.CompileUnavailable("not reachable")
    monkeypatch.setattr(ui, "compile_xml", down)
    res = agent.generate("x", fixture_catalog, ScriptedLLM([[("compile", {"blocks": TREE})]]))
    assert res["status"] == "error" and "compiler is unavailable" in res["message"]


def test_simulate_needs_a_compile_first(fixture_catalog, fake_ui):
    llm = ScriptedLLM([[("simulate", {"bits": [{"expect": {"points": 1}}]})], [FIN]])
    agent.generate("x", fixture_catalog, llm)
    assert "compile the XML first" in result(llm, 1)["error"]


def test_prompt_carries_reference_kt_blueprints_and_catalog(fixture_catalog):
    blocks = agent.system_blocks(fixture_catalog)
    static, cat = blocks[1]["text"], blocks[2]["text"]
    assert "## if_generic_adaptive" in static and "Counters the upload dialog creates" in static
    assert "Runtime evaluation" in static and "### Punch Card Rewards" in static and "DRL:" in static
    assert '{"type":"offer_generic"' in static and "<block" not in static  # blueprints shown as trees, like the output
    assert "Tenant catalog" in cat and "h_location" in cat
    assert all(b.get("cache_control") for b in blocks[1:])


def test_reference_is_generated_for_the_whole_reward_toolbox():
    ref = json.load(open(os.path.join(ROOT, "offer_author", "blocks", "assets", "block_reference.json")))
    types = [b["type"] for b in ref["toolbox"]]
    assert len(types) == 25 and None not in types and {"offer_generic", "if_generic_adaptive", "action"} <= set(types)
    assert any(p["attribute_order"][:3] == ["xmlns", "object", "attribute_level"] for p in ref["counter_patterns"])


def test_offline_check_accepts_goldens_and_catches_bad_nesting(fixture_catalog):
    for f in os.listdir(os.path.join(GOLDEN, "compiled")):
        assert check.check(open(os.path.join(GOLDEN, "compiled", f)).read(), fixture_catalog) == [], f
    # a rule placed in the filter slot, an unknown block, a made-up store code
    bad = GOOD.replace('<statement name="ACTIVITY_CRITERIA">', '<statement name="ACTIVITY_CRITERIA"><block type="rule" id="z1"/>', 1)
    p = check.check(bad.replace('type="static_value"', 'type="blueprint_variable"', 1), fixture_catalog)
    assert any("rule cannot plug in here" in x for x in p) and any("blueprint_variable" in x for x in p)
    p = check.check(open(os.path.join(GOLDEN, "compiled", "S14_simple_sanity.xml")).read().replace('value="SPEND"', 'value="SHOPPING"'), fixture_catalog)
    assert any("SHOPPING" in x for x in p)


def test_tree_serializer_writes_mutations_in_the_ui_order():
    """Every golden and blueprint survives XML -> tree -> XML, and mutation attributes come out in the order the UI
    saves them (the upload dialog's counter regexes depend on it) whatever order the model used."""
    import glob
    import re
    sig = lambda x: re.findall(r"<mutation[^>]*>", re.sub(r' xmlns="[^"]*"', "", x))  # noqa: E731
    files = glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")) + glob.glob(os.path.join(ROOT, "offer_author", "blocks", "assets", "blueprints", "*.xml"))
    for f in files:
        x = open(f).read()
        t = tree.from_xml(x)

        def shuffle(n):
            if isinstance(n, list):
                return [shuffle(c) for c in n]
            n = dict(n)
            if n.get("mutation"):
                n["mutation"] = dict(reversed(list(n["mutation"].items())))
            n["inputs"] = {k: shuffle(v) for k, v in (n.get("inputs") or {}).items()}
            return n
        y, paths = tree.to_xml(shuffle(t))
        assert tree.from_xml(y) == t and sig(y) == sig(x), f
    assert paths["b1"] == "offer_generic"


def test_tree_errors_are_reported_not_raised_into_the_loop():
    with pytest.raises(tree.TreeError):
        tree.to_xml({"type": "offer_generic", "inputs": {"activities": [{"fields": {}}]}})
