"""Saved offers into the history (core/harvest.py): the saved XML is vetted the way the rules screen would load it,
against the tenant's catalog as it is now; one version per offer, the newest; the manager's prompt kept per offer."""

import json
import os

from offer_author.api.text import word
from offer_author.core.tenant import harvest as harvest_module
from offer_author.core.tenant import history
from offer_author.core.tenant.harvest import PromptLog, harvest
from tests.support import GOLDEN


def vet(xml, catalog=None):
    """The problems of a saved XML, as the harvest report words them."""
    return [word(p) for p in harvest_module.vet(xml, catalog)]


S07 = open(os.path.join(GOLDEN, "compiled", "S07_every_5th_visit.xml")).read()
COUNTER = 'mta_attribute="Visits toward privilege 501" type="NUMBER"'


def test_a_saved_offer_built_by_the_ui_vets_clean(fixture_catalog):
    assert vet(S07) == []
    assert not [p for p in vet(S07, fixture_catalog) if "saved as" in p or "counter" in p]


def test_a_counter_saved_with_two_types_is_not_trusted():
    """demov4: "First Transaction Date" is NUMBER in one offer, DATE in another; within one offer it must agree."""
    xml = S07.replace(COUNTER, 'mta_attribute="Visits toward privilege 501" type="DATE"', 1)
    assert "counter 'Visits toward privilege 501' is saved as DATE and NUMBER" in vet(xml)


def test_a_counter_saved_without_a_type_or_a_name_is_not_trusted():
    untyped = S07.replace(COUNTER, 'mta_attribute="Visits toward privilege 501" type="undefined"')
    assert "counter 'Visits toward privilege 501' is saved without a data type" in vet(untyped)
    unnamed = S07.replace(
        'attribute_level="MA_O" attribute="Visits toward privilege 501"', 'attribute_level="MA_O" attribute=""'
    )
    assert any("uses a MA_O counter with no name" in p for p in vet(unnamed))


def test_an_attribute_saved_with_another_type_than_the_catalog_gives_it(fixture_catalog):
    xml = S07.replace(
        'value="SPEND" visible_value="Spend" type="LOOKUP"', 'value="SPEND" visible_value="Spend" type="NUMBER"'
    )
    assert any("h_bit_type is LOOKUP in the catalog, saved as NUMBER" in p for p in vet(xml, fixture_catalog))
    assert not any("saved as" in p for p in vet(xml))  # no catalog: no type to hold it to


def test_an_empty_attribute_field_is_a_half_built_block():
    """demov4 "Flight Redemption Rewards" was launched with a counter incremented by an empty activity_attribute."""
    xml = S07.replace(
        '<field name="mta_attribute">Visits toward privilege 501</field>', '<field name="mta_attribute"></field>', 1
    )
    assert any("mta_attribute is empty" in p for p in vet(xml))


def test_a_disabled_block_is_not_vetted():
    xml = S07.replace(COUNTER, 'mta_attribute="Visits toward privilege 501" type="DATE"', 1)
    first_action = xml.index('<block type="action"')
    disabled = xml[:first_action] + xml[first_action:].replace(
        '<block type="action"', '<block type="action" disabled="true"', 1
    )
    assert not any("saved as DATE" in p for p in vet(disabled))


def test_harvest_keeps_the_newest_version_and_drops_a_broken_one(tmp_path):
    store = str(tmp_path / "t1" / "offers.jsonl")
    v1 = {"id": 5, "name": "Coffee Club", "xml": S07, "updated": "2026-10-01"}
    rep = harvest([v1, dict(v1, updated="2026-09-01", name="older")], store, prompts={"5": "every 5th visit"})
    rows = [json.loads(x) for x in open(store)]
    assert (
        rep["added"] == 1
        and len(rows) == 1
        and rows[0]["name"] == "Coffee Club"
        and rows[0]["prompt"] == "every 5th visit"
    )
    assert harvest([v1], store)["kept"] == 1  # nothing newer: as stored
    broken = dict(
        v1,
        updated="2026-10-05",
        xml=S07.replace(COUNTER, 'mta_attribute="Visits toward privilege 501" type="undefined"'),
    )
    rep = harvest([broken], store)
    assert (
        rep["removed"] == ["5"] and "5" in rep["rejected"] and open(store).read() == ""
    )  # the stored version is not the offer any more
    assert not os.path.exists(store + ".tmp")


def test_harvest_stores_each_offers_subprompt_once(tmp_path):
    store, made = str(tmp_path / "t1" / "offers.jsonl"), []
    lines = lambda xml: made.append(xml) or "rule_1, give_points 9 BASE 100"
    harvest([{"id": 5, "name": "Coffee Club", "xml": S07, "updated": "2026-10-01"}], store, subprompt=lines)
    harvest([], store, subprompt=lines)  # kept as stored: its lines are not made again
    assert [json.loads(x)["subprompt"] for x in open(store)] == ["rule_1, give_points 9 BASE 100"] and len(made) == 1


def test_pulsars_own_code_writes_the_subprompt():
    from offer_author.api.harvest import pulsar_subprompt

    lines = pulsar_subprompt()(S07).splitlines()
    assert lines[0].startswith("Filter_") and any(
        " give_privilege " in ln or " update_attribute " in ln for ln in lines
    )


def test_prompt_log_keeps_the_last_prompt_per_offer(tmp_path):
    log = PromptLog(str(tmp_path))
    log.add("t1", 5, "every 5th visit")
    log.add("t1", 5, "every 5th visit earns a coffee")
    log.add("t1", None, "no offer yet")
    PromptLog("").add("t1", 6, "no folder: nothing kept")
    assert log.last("t1") == {"5": "every 5th visit earns a coffee"} and log.last("t2") == {}


def test_the_store_shows_only_offers_that_fit_the_catalog_now(tmp_path, fixture_catalog, monkeypatch):
    monkeypatch.setattr(history, "vet", lambda xml, catalog: ["no"] if "bad" in xml else [])
    for d, rows in (
        (
            "t1",
            [
                {"id": 1, "prompt": "every 5th visit", "xml": S07, "name": "Coffee Club", "subprompt": "rule_1, a"},
                {"id": 2, "prompt": "half built", "xml": S07 + "<!-- bad -->", "subprompt": "rule_1, b"},
            ],
        ),
        (
            history.SHARED,
            [{"id": 9, "prompt": "every 10th visit", "xml": S07, "name": "Coffee Club Plus", "subprompt": "rule_1, c"}],
        ),
    ):
        (tmp_path / d).mkdir()
        (tmp_path / d / "offers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    from tests.unit.test_history import embed

    h = history.Histories(str(tmp_path), embed).get("t1", fixture_catalog)
    assert sorted((o.id, o.own) for o in h.offers) == [("1", True), ("9", False)]
    assert [o.id for o in h.named("same as Coffee Club Plus but every 3rd visit")] == [
        "1"
    ]  # another program's: shape only
    assert history.Histories(str(tmp_path), embed).get("t2", fixture_catalog).offers[0].id == "9"  # shared only


def test_always_two_earlier_offers():
    from tests.unit.test_history import OFFERS, embed

    assert len(history.History(OFFERS, embed).similar("a weekend fuel promotion with no words in common")) == 2


def test_a_saved_offer_the_grammar_check_rejects_is_reported_not_raised():
    """check() returns Problems: vet must not treat them as text (it raised on any grammar problem)."""
    broken = S07.replace('<block type="action"', '<block type="no_such_block"', 1)
    assert any("is not in the rules toolbox" in p for p in vet(broken))
    assert vet("<xml") and "not well-formed" in vet("<xml")[0]
