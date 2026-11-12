"""The complete DSL through Pulsar's own converter (vendor/pulsar_rag xml_to_subprompt, form "dsl") and its feedback
comparison: the same (line, block) pairs, each line with its place."""

import json
import os

import pytest

pytest.importorskip("pandas")
pytest.importorskip("xmlschema")

from offer_author.api import harvest
from offer_author.core.canvas import dsl
from offer_author.core.canvas.edit import Doc
from offer_author.core.canvas.tree import compact


def xml_of(name: str, catalog=None) -> str:
    with open(os.path.join(os.path.dirname(__file__), "..", "golden", "compiled", name + ".xml")) as f:
        return f.read()


def test_the_converter_gives_the_complete_lines_with_each_lines_block(fixture_catalog):
    xml = xml_of("S04_happy_hour_or", fixture_catalog)
    pairs = harvest.dsl_pairs(xml)
    view = Doc(xml).view()
    tops = [compact(t, view["counters"]) for t in view["tops"]]
    assert "\n".join(line for line, _ in pairs) == dsl.to_lines(tops, view["counters"])
    back, counters, _ = dsl.from_lines("\n".join(line for line, _ in pairs))
    assert json.dumps(back) == json.dumps(dsl._without_ids(tops)) and counters == view["counters"]
    assert any(" or1.1," in line for line, _ in pairs) and any(" or1.2," in line for line, _ in pairs)
    for line, block in pairs:  # each line's block is the block the line says
        label, body = dsl.split_label(line)
        if label.startswith("Filter"):
            assert block.startswith('<block type="activity_criteria"')
        elif body.startswith("if "):
            assert block.startswith('<block type="if_generic_adaptive"')
        elif body:
            assert block.startswith('<block type="action"')


def test_a_draft_differing_in_one_condition_gives_one_missing_and_one_extra_line(fixture_catalog):
    draft = xml_of("S01_tiered_exclusive")
    saved = draft.replace('id="ai00015"><field name="value">500<', 'id="ai00015"><field name="value">100<')
    assert saved != draft
    got = harvest.changes(draft, saved)
    assert [m["line"] for m in got["missing"]] == ["if activity h_bit_amount > 100"]
    assert [e["line"] for e in got["extra"]] == ["if activity h_bit_amount > 500"]
    assert got["missing"][0]["place"] == "activity 1 rule"
    assert harvest.changes(saved, saved) == {"missing": [], "extra": []}


def test_a_renamed_rule_or_rules_in_another_order_is_no_change(fixture_catalog):
    saved = xml_of("S01_tiered_exclusive", fixture_catalog)
    renamed = saved.replace('<field name="IDENTIFIER">', '<field name="IDENTIFIER">x ', 1)
    assert renamed != saved and harvest.changes(renamed, saved) == {"missing": [], "extra": []}


def test_the_pulsar_form_is_what_it_was(fixture_catalog):
    xml = xml_of("S01_tiered_exclusive", fixture_catalog)
    harvest._pulsar()
    import contextlib

    from pulsar.data_validation.sub_prompts.blockwise_subprompt import xml_to_subprompt

    with contextlib.chdir(harvest.PULSAR):
        legacy = xml_to_subprompt(xml)
    assert legacy and all(",,," in line for line, _ in legacy)


def test_a_harvest_stores_the_lines_and_a_dsl_run_is_shown_them(fixture_catalog, tmp_path):
    from offer_author.ai import prompt
    from offer_author.core.tenant import history
    from offer_author.core.tenant.harvest import harvest

    xml = xml_of("S04_happy_hour_or")
    store = tmp_path / "offers.jsonl"
    store.write_text(json.dumps({"id": "7", "name": "Happy hour", "prompt": "double at happy hour", "xml": xml}) + "\n")
    from offer_author.api.harvest import dsl_pairs

    harvest([], str(store), None, None, lambda x: "legacy lines", lambda x: "\n".join(ln for ln, _ in dsl_pairs(x)))
    r = json.loads(store.read_text())
    assert r["dsl_form"] == dsl.FORM and r["subprompt"] == "legacy lines" and " or1.1," in r["dsl"]
    assert dsl.from_lines(r["dsl"])[0]  # the stored lines compile
    e = history.from_record(r)
    shown = {"named": [e], "similar": []}
    as_lines, as_legacy = prompt.earlier_offers(shown, "dsl"), prompt.earlier_offers(shown)
    assert r["dsl"].splitlines()[0] in as_lines and "legacy lines" not in as_lines and "Lines:" in as_lines
    assert (
        "legacy lines" in as_legacy and "Sub-prompt:" in as_legacy and as_legacy == prompt.earlier_offers(shown, "json")
    )
    old = history.from_record({k: v for k, v in r.items() if k != "dsl"})  # not migrated yet: Pulsar's form for all
    assert "legacy lines" in prompt.earlier_offers({"named": [e], "similar": [(0.9, old)]}, "dsl")
