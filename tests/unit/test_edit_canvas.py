"""Editing the offer's saved rules canvas, and loose (unattached) blocks: saved text kept where nothing changed,
incompatible attachments left loose and disabled, the agent and the Pulsar API end to end with a scripted model."""

import base64
import glob
import json
import os

import pytest

from offer_author.ai import agent
from offer_author.api.service import create_app
from offer_author.core.canvas import tree
from offer_author.core.canvas.build import to_xml
from offer_author.core.canvas.checks.grammar import check, detach
from offer_author.core.canvas.edit import Doc
from offer_author.core.canvas.tree import apply_edits, nodes, number
from tests.fakes import ScriptedLLM
from tests.paths import ROOT
from tests.support import GOLDEN, finish, golden, public, said, selections

S01 = open(os.path.join(GOLDEN, "compiled", "S01_tiered_exclusive.xml")).read()  # 3 spend bands, ids ai000nn


def saved_ids(xml):
    return {n["id"]: n for n, _, _ in nodes(Doc(xml).view()["tops"])}


def find(xml, **want):
    return next(
        n
        for n in saved_ids(xml).values()
        if all((n.get("fields") or {}).get(k) == v or n.get(k) == v for k, v in want.items())
    )


def test_every_saved_canvas_round_trips_byte_for_byte():
    files = glob.glob(os.path.join(GOLDEN, "**", "*.xml"), recursive=True) + glob.glob(
        os.path.join(ROOT, "offer_author", "core", "data", "*", "*.xml")
    )
    assert len(files) > 60
    for f in files:
        x = open(f, encoding="utf-8").read()
        d = Doc(x, "segment" if "/segments/" in f else "rules")
        assert d.prefix + d.root.text() + d.suffix == x, f
        assert d.write(d.view()["tops"])[0] == x, f


def test_a_field_edit_changes_only_that_field():
    d = Doc(S01)
    hundred = find(S01, type="static_value", value="100")
    xml, _, ids = d.write(apply_edits(d.view()["tops"], [{"id": hundred["id"], "value": "150"}]))
    xid = ids[hundred["id"]]
    assert xml == S01.replace(
        f'id="{xid}"><field name="value">100</field>', f'id="{xid}"><field name="value">150</field>'
    )


def test_inserted_and_appended_blocks_keep_the_saved_text_around_them():
    d = Doc(S01)
    tops = d.view()["tops"]
    rules = [n for n, _, _ in nodes(tops) if n["type"] == "rule"]
    new_rule = {
        "type": "rule",
        "IDENTIFIER": "Bonus",
        "ACTION": [{"type": "action", "action_type": "give_points", "FORMULA": {"type": "static_value", "value": "5"}}],
    }
    edited = number(
        apply_edits(
            tops,
            [
                {"id": rules[0]["id"], "insert_after": [new_rule]},
                {
                    "id": rules[-1]["id"],
                    "append": {
                        "ACTION": [
                            {
                                "type": "action",
                                "action_type": "give_points",
                                "FORMULA": {"type": "static_value", "value": "1"},
                            }
                        ]
                    },
                },
            ],
        )
    )
    xml, _, _ = d.write(edited)
    after = [n for n, _, _ in nodes(Doc(xml).view()["tops"]) if n["type"] == "rule"]
    assert [r["fields"]["IDENTIFIER"] for r in after] == [rules[0]["fields"]["IDENTIFIER"], "Bonus"] + [
        r["fields"]["IDENTIFIER"] for r in rules[1:]
    ]
    for n, _, _ in nodes(tops):  # every saved block keeps its Blockly id and its own saved parts
        el = d.el[n["id"]]
        assert el.open_tag() in xml
        assert "".join(k.text() for k in el.kids if k.tag in ("mutation", "field")) in xml
    middle = d.el[rules[1]["id"]].text()  # the rule after the insertion, with the rest of its chain: untouched
    assert middle in xml or rules[1] is rules[-1]


def test_blocks_that_cannot_plug_in_are_detached_and_the_rest_stays():
    action = {
        "type": "action",
        "id": "a1",
        "action_type": "give_points",
        "inputs": {"FORMULA": {"type": "static_value", "id": "v9", "fields": {"value": "3"}}},
    }
    root = {
        "type": "offer_generic",
        "id": "b1",
        "inputs": {
            "activities": [
                {
                    "type": "activity",
                    "id": "b2",
                    "inputs": {
                        "ACTIVITY_CRITERIA": [
                            {"type": "action", "id": "x1", "action_type": "give_points"}
                        ],  # an action in the filter
                        "RULES": [
                            {
                                "type": "rule",
                                "id": "r1",
                                "fields": {"IDENTIFIER": "R"},
                                "inputs": {
                                    "ACTION": [
                                        {"type": "static_value", "id": "s1", "fields": {"value": "100"}},
                                        action,
                                    ],  # a value block in a list
                                    "NOPE": [{"type": "action", "id": "n1"}],
                                },
                            }
                        ],
                    },
                }
            ]
        },
    }  # an input the rule does not have
    tops, moved = detach([root])
    assert {m["id"] for m in moved} == {"x1", "s1", "n1"}
    assert [t["id"] for t in tops[1:]] == ["x1", "s1", "n1"]
    rule = tops[0]["inputs"]["activities"][0]["inputs"]["RULES"][0]
    assert [a["id"] for a in rule["inputs"]["ACTION"]] == ["a1"] and "NOPE" not in rule["inputs"]
    why = {m["id"]: said(m["reason"]) for m in moved}
    assert (
        "no previous connection" in why["s1"]
        and "cannot plug into activity.ACTIVITY_CRITERIA" in why["x1"]
        and "no input 'NOPE'" in why["n1"]
    )


def test_loose_blocks_are_written_disabled_and_only_enabled_extras_are_problems(fixture_catalog):
    root = selections(S01)[0]
    xml, _ = to_xml([root, {"type": "static_value", "id": "L1", "fields": {"value": "100"}}], fixture_catalog)
    assert '<block type="static_value" id="L1" x="900" y="40" disabled-reasons="MANUALLY_DISABLED">' in xml
    assert not [p for p in said(check(xml, fixture_catalog)) if "top-level" in p]
    enabled = xml.replace(' disabled-reasons="MANUALLY_DISABLED"', "")
    assert any("exactly one enabled top-level block" in p for p in said(check(enabled, fixture_catalog)))


def run(fixture_catalog, turns, saved=S01, prompt="change the first band to 150"):
    llm = ScriptedLLM(turns)
    res = agent.generate(prompt, fixture_catalog, llm, existing={"rules_xml": saved} if saved else None)
    return res, llm


def test_the_agent_edits_the_saved_canvas_and_keeps_the_rest(fixture_catalog):
    hundred = find(S01, type="static_value", value="100")
    res, llm = run(
        fixture_catalog,
        [
            [("compose", {"canvas": "rules", "edits": [{"id": hundred["id"], "value": "150"}]})],
            [finish(readback="first band from 150")],
        ],
    )
    intro = llm.calls[0][0]["content"][0]["text"]
    assert "Rules saved on this offer now" in intro and f'"id":"{hundred["id"]}"' in intro
    assert res["status"] == "draft" and res["rules"]["edited"] is True
    xid = Doc(S01).xid[hundred["id"]]
    assert res["rules"]["xml"] == S01.replace(
        f'id="{xid}"><field name="value">100</field>', f'id="{xid}"><field name="value">150</field>'
    )


def test_a_block_asked_for_on_its_own_is_loose_and_the_rules_stay(fixture_catalog):
    res, llm = run(
        fixture_catalog,
        [
            [("compose", {"canvas": "rules", "loose": [{"type": "static_value", "value": "100"}]})],
            [finish(readback="a static value block with 100, not attached")],
        ],
        prompt="add a static value block with 100",
    )
    out = json.loads(llm.calls[1][2]["content"][0]["content"])
    assert out["ok"] and out["loose"][0]["reason"] == "asked for on its own"
    xml = res["rules"]["xml"]
    assert (
        xml.startswith(S01[: -len("</xml>")])
        and 'disabled-reasons="MANUALLY_DISABLED"><field name="value">100</field></block></xml>' in xml
    )
    assert any("static_value block is on the canvas but not attached" in a for a in public(res)["assumptions"])


def test_an_incompatible_attachment_is_left_loose_with_the_reason(fixture_catalog):
    rule = find(S01, type="rule")
    res, llm = run(
        fixture_catalog,
        [
            [
                (
                    "compose",
                    {
                        "canvas": "rules",
                        "edits": [{"id": rule["id"], "append": {"ACTION": [{"type": "static_value", "value": "7"}]}}],
                    },
                )
            ],
            [finish()],
        ],
        prompt="add a static value 7 to the first rule's actions",
    )
    out = json.loads(llm.calls[1][2]["content"][0]["content"])
    assert out["ok"] and "cannot plug into rule.ACTION" in out["loose"][0]["reason"]
    assert 'disabled-reasons="MANUALLY_DISABLED"><field name="value">7</field>' in res["rules"]["xml"]
    assert res["rules"]["xml"].count('<field name="value">7</field>') == 1  # loose only, not in the rule


def test_a_whole_tree_replaces_the_saved_canvas(fixture_catalog):
    res, _ = run(
        fixture_catalog, [[("compose", golden("S07_every_5th_visit"))], [finish()]], prompt="every 5th visit a coffee"
    )
    assert res["rules"]["edited"] is False and "ai00019" not in res["rules"]["xml"]


def test_problems_the_saved_canvas_already_had_do_not_block(fixture_catalog, monkeypatch):
    from offer_author.core.run import canvases
    from offer_author.domain import Problem

    stale = Problem.of("attribute_not_in_catalog", object="member", attribute="old_attr")  # on every check of it
    monkeypatch.setattr(canvases, "check", lambda xml, catalog, canvas: [*check(xml, catalog, canvas=canvas), stale])
    hundred = find(S01, type="static_value", value="100")
    res, llm = run(
        fixture_catalog,
        [[("compose", {"canvas": "rules", "edits": [{"id": hundred["id"], "value": "150"}]})], [finish()]],
    )
    out = json.loads(llm.calls[1][2]["content"][0]["content"])
    assert out["ok"] and out["already_on_the_canvas"] and res["status"] == "draft"


def test_the_popup_api_edits_the_saved_canvas_it_is_sent(fixture_catalog, logged):
    hundred = find(S01, type="static_value", value="100")
    llm = ScriptedLLM(
        [[("compose", {"canvas": "rules", "edits": [{"id": hundred["id"], "value": "150"}]})], [finish(readback="150")]]
    )
    cl = create_app(llm, lambda sponsor, account, auth: fixture_catalog).test_client()
    saved = S01.replace(
        'id="ai00019" x="50" y="50">',
        'id="ai00019" x="50" y="50"><comment pinned="true" h="220" w="520">AI draft: old</comment>',
    )
    r = cl.post(
        "/ai/pulsar/offer/rules?regenerate=true",
        headers={"Authorization": "JWT x"},
        json={
            "prompt": "change the first band to 150",
            "sponsor_id": 10,
            "id": 777,
            "type": "reward",
            "current_rules_xml": base64.b64encode(saved.encode()).decode(),
        },
    )
    assert r.status_code == 200, r.get_json()
    xml = base64.b64decode(r.get_json()["xml"]).decode("latin-1")
    assert xml.count("<comment") == 1 and "AI draft: old" in xml  # the saved canvas's own comment, kept; none added
    assert (
        '<field name="value">150</field>' in xml and 'id="ai00006"' in xml and logged("rules", 777)["readback"] == "150"
    )


@pytest.mark.parametrize("edit", [{"insert_after": [{"type": "rule"}]}, {"remove": True}])
def test_the_root_cannot_be_removed_or_given_siblings(edit):
    tops = Doc(S01).view()["tops"]
    with pytest.raises(tree.TreeError):
        apply_edits(tops, [dict(edit, id=tops[0]["id"])])


def test_a_condition_level_given_after_its_attribute_is_written_where_the_screen_reads_it(fixture_catalog):
    """devintbf2 2026-10-26: the model wrote attribute before attribute_level; the screen rebuilds the attribute
    dropdown when the level is set and dropped the attribute ("Member program level attribute is not selected")."""
    cond = {
        "type": "activity_criteria",
        "OBJECT": "member",
        "attribute": "tier",
        "attribute_level": "MA_P",
        "operators": "==",
        "value": "GOLD",
    }
    root = tree.expand({"type": "offer_generic", "activities": [{"type": "activity", "ACTIVITY_CRITERIA": [cond]}]})
    xml, _ = to_xml(number(root), fixture_catalog)
    assert (
        '<field name="OBJECT">member</field><field name="attribute_level">MA_P</field><field name="attribute">tier</field>'
        in xml
    )


def test_a_request_the_saved_rules_already_meet_returns_them_unchanged(fixture_catalog):
    """Live 2026-10-26: 'an activity filter for Spend' on a canvas that has one; the model finished without a compile
    and the answer was 'couldn't turn this into an offer'."""
    res, _ = run(
        fixture_catalog, [[finish(readback="the saved rules already filter on Spend")]], prompt="filter on Spend"
    )
    assert res["status"] == "draft" and res["rules"]["xml"] == S01 and res["rules"]["edited"] is False


def test_finish_takes_one_note_as_a_string():
    from offer_author.ai.tools import RunContext, run_now
    from offer_author.core.settings import Settings

    ctx = RunContext(None, None, [], ("rules",), Settings())
    msg = run_now(
        ctx, "t1", "finish", {"readback": "ok", "assumptions": "one", "blocking_questions": [], "unsupported": []}
    )
    ctx.close()
    assert msg.status != "error" and msg.artifact.input["assumptions"] == [{"text": "one"}]


def test_a_changed_counter_declaration_rewrites_the_blocks_that_use_it(fixture_catalog):
    """Blocks whose own fields were unchanged kept their saved mutation, with the counter's old data type."""
    import xml.etree.ElementTree as ET

    s08 = open(os.path.join(GOLDEN, "compiled", "S08_spend_milestone_once.xml")).read()
    d = Doc(s08)
    view = d.view()
    same = {c["name"]: c for c in view["counters"]}
    assert d.write(view["tops"], fixture_catalog, same)[0] == s08
    changed = dict(same, **{"Total spend": dict(same["Total spend"], data_type="DATE")})
    xml = d.write(view["tops"], fixture_catalog, changed)[0]
    muts = [
        m
        for m in ET.fromstring(xml).iter()
        if m.tag.endswith("mutation") and "Total spend" in (m.get("attribute"), m.get("mta_attribute"))
    ]
    assert muts and all(m.get("type") == "DATE" for m in muts if m.get("type"))
