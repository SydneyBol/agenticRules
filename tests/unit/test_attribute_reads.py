"""The attributes a canvas reads, reported from code, and the look-alikes among them (core/canvas/checks/reads.py)."""

import copy

from offer_author.api.text import word
from offer_author.core.canvas.checks.reads import attribute_reads, lookalikes
from offer_author.core.run import Run
from offer_author.core.tenant.catalog import Attribute
from tests.support import golden


def compose(catalog, name):
    run = Run(catalog, None)
    out = run.compose({"canvas": "rules", **golden(name)})
    assert out["ok"], out
    return run, out


def test_the_reads_name_each_attribute_with_its_place_once(fixture_catalog):
    run, _ = compose(fixture_catalog, "S14_simple_sanity")
    reads = attribute_reads(run.canvases.last["rules"].xml, fixture_catalog)
    assert [(r["list"], r["attribute"], r["place"]) for r in reads] == [
        ("activity", "h_bit_type", "the transaction"),
        ("activity", "h_bit_amount", "the transaction"),
    ]
    assert all(r["label"] for r in reads)


def test_a_counter_is_not_an_attribute_read(fixture_catalog):
    run, _ = compose(fixture_catalog, "S07_every_5th_visit")
    xml = run.canvases.last["rules"].xml
    assert {r["attribute"] for r in attribute_reads(xml, fixture_catalog)}.isdisjoint(
        {c["name"] for c in run.canvases.last["rules"].counters}
    )


def test_an_attribute_another_list_shares_its_name_with_is_noted(fixture_catalog):
    fixture_catalog = copy.deepcopy(fixture_catalog)
    reads = [{"list": "airport", "place": "the airport", "attribute": "country", "label": "Country"}]
    for lst in ("airport", "member", "location"):
        fixture_catalog.attributes.setdefault(lst, {})["country"] = Attribute(
            code="country", label="Country", type="TEXT"
        )
    (note,) = lookalikes(reads, fixture_catalog)
    assert note.code == "attribute_lookalike" and note.data["attribute"] == "country"
    assert len(note.data["others"]) == 2 and any("member's profile" in o for o in note.data["others"])
    assert "the member's profile" in word(note) and "Check that the offer reads the one you mean" in word(note)


def test_a_unique_attribute_gets_no_note(fixture_catalog):
    reads = [{"list": "activity", "place": "the transaction", "attribute": "h_bit_amount", "label": "BIT Amount"}]
    assert lookalikes(reads, fixture_catalog) == []


def test_the_note_is_a_fact_beside_the_problems_and_the_result_lists_the_reads(fixture_catalog):
    fixture_catalog = copy.deepcopy(fixture_catalog)
    fixture_catalog.attributes["member"]["h_bit_amount"] = Attribute(
        code="h_bit_amount", label="BIT Amount", type="NUMBER"
    )
    _, out = compose(fixture_catalog, "S14_simple_sanity")  # the compose is ok: the note holds nothing back
    assert [p.code for p in out["attribute_lookalikes"]] == ["attribute_lookalike"]
    assert out["attribute_lookalikes"][0].data["attribute"] == "h_bit_amount"


def test_a_finished_offer_lists_what_it_reads_and_states_each_look_alike_for_the_manager(fixture_catalog):
    fixture_catalog = copy.deepcopy(fixture_catalog)
    from offer_author.ai import agent
    from offer_author.api.text import public
    from tests.fakes import ScriptedLLM
    from tests.support import finish

    fixture_catalog.attributes["member"]["h_bit_amount"] = Attribute(
        code="h_bit_amount", label="BIT Amount", type="NUMBER"
    )
    llm = ScriptedLLM([[("compose", golden("S14_simple_sanity"))], [finish(readback="10 points per spend")]])
    res = agent.generate("10 points per spend", fixture_catalog, llm, offer_id=1001)
    assert [r["attribute"] for r in res["rules"]["attributes_read"]] == ["h_bit_type", "h_bit_amount"]
    said = public(res)["by_part"]["rules"]["assumptions"]
    assert any("h_bit_amount is taken from the transaction" in a and "Check that the offer reads" in a for a in said)
