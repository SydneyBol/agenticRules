"""Offline parts of the catalog: the case files themselves, API-level negative cases, and the block slot matrix."""
import pytest

import catalog
from offer_author.llm import ScriptedLLM
from offer_author.service import create_app

CASES = catalog.load()
API_CASES = [c for c in CASES if c.get("api")]


def test_catalog_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    for c in CASES:
        assert c.get("prompt") or c.get("api"), c["id"]
        for f in c["expect"].get("features", []):
            assert f.split(":")[0] in ("block", "obj", "act", "op", "counter", "mode"), (c["id"], f)
            if f.startswith("block:"):
                assert f[6:] in catalog.TOOLBOX, (c["id"], f)
    blocks = {f[6:] for c in CASES for f in c["expect"].get("features", []) if f.startswith("block:")}
    assert blocks == catalog.TOOLBOX, sorted(catalog.TOOLBOX - blocks)  # every toolbox block has a case


@pytest.mark.parametrize("case", API_CASES, ids=[c["id"] for c in API_CASES])
def test_api_case(case, fixture_catalog):
    app = create_app(ScriptedLLM([]), lambda sponsor, account, auth: fixture_catalog)
    r = app.test_client().post(case["api"]["path"], json=case["api"]["body"])
    assert r.status_code == case["expect"]["http"], r.get_json()


SLOT_PAIRS = [(slot, child) for slot, kids in catalog.slots().items() for child in kids]


@pytest.mark.parametrize("slot,child", SLOT_PAIRS, ids=[f"{s}<-{c}" for s, c in SLOT_PAIRS])
def test_slot_pair_builds(slot, child):
    """Every container input with every child its setCheck allows is accepted by blocks mode's offline check, whose
    connection rules come from the block reference generated from the UI source (tools/block_reference.py)."""
    from offer_author.blocks.check import check, reference
    parent, inp = slot.split(".")
    ref = reference()
    kind = "statement" if inp in {i["name"] for i in ref[parent]["statement_inputs"]} else "value"
    inner = f'<block type="{child}" id="c"/>'
    body = f'<block type="{parent}" id="p"><{kind} name="{inp}">{inner}</{kind}></block>'
    # place the parent where it belongs so only the pair under test is judged
    ctx = {"offer_generic": "{}", "activity": '<block type="offer_generic"><statement name="activities">{}</statement></block>',
           "event": '<block type="offer_generic"><statement name="events">{}</statement></block>'}
    xml = '<xml xmlns="https://developers.google.com/blockly/xml">' + ctx.get(parent, '<block type="offer_generic">{}</block>').format(body) + "</xml>"
    problems = [p for p in check(xml, None, ref) if f"{child} cannot plug in" in p or "has no" in p]
    assert not problems, problems
