"""Knowledge-only prompts and the tenant's earlier offers as reference: the flags off leave the prompt as it was; with
examples off the prompt holds the block reference, KT and catalog only; earlier offers are matched by meaning, never
the offer itself or its own description, and reach the model in the request message, after the cached prefix."""

import hashlib
import json
import os

import numpy as np

from offer_author.ai import agent, prompt
from offer_author.core.settings import Settings
from offer_author.core.tenant.history import Histories, History, PastOffer, from_record
from tests.fakes import ScriptedLLM
from tests.support import finish, golden

S07 = golden("S07_every_5th_visit")


def embed(texts, kind="document", dim=128):
    """Bag of words hashed into dim buckets, unit length: texts sharing words are similar."""
    out = np.zeros((len(texts), dim), dtype="float32")
    for i, t in enumerate(texts):
        for w in t.lower().split():
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def offer(i, text, name=None, shape=None):
    return PastOffer(
        str(i), name or f"Offer {i}", text, f"rule_1, give_points 9 BASE {shape if shape is not None else i}"
    )


OFFERS = [
    offer(1, "every 5th visit earns a free coffee", "Coffee Club"),
    offer(2, "every 5th visit earns a free coffee voucher", shape=1),  # same sub-prompt as 1
    offer(3, "spend 100 at hyderabad earns 50 points", "Ramadan Spend"),
    offer(4, "birthday month double points"),
    offer(5, "every 10th visit earns a free coffee"),
]


def test_no_earlier_offers_no_history_rule(fixture_catalog):
    assert prompt.system_blocks(fixture_catalog)[0]["text"] == prompt.SYSTEM and "Earlier offers" not in prompt.SYSTEM
    assert prompt.intro("x", ("rules",)) == prompt.intro("x", ("rules",), earlier={"named": [], "similar": []})


def test_at_most_two_earlier_offers_are_shown():
    h = History(OFFERS, embed)
    e = h.earlier("every 5th store visit earns one coffee, like Coffee Club", k=5)
    assert len(e["named"]) + len(e["similar"]) == 2 and [o.name for o in e["named"]] == ["Coffee Club"]


def test_history_rule_only_when_earlier_offers_may_be_shown(fixture_catalog):
    on = prompt.system_blocks(fixture_catalog, history=True)[0]["text"]
    assert on == prompt.SYSTEM + prompt.HISTORY_RULE and "Never take a value from them" in on


def test_similar_skips_itself_and_its_own_description():
    h = History(OFFERS, embed)
    got = [(o.id, s) for s, o in h.similar("every 5th visit earns a free coffee", k=2)]
    assert [i for i, _ in got] == ["2", "5"] and got[0][1] > got[1][1]  # 1 is this description again


def test_similar_ranks_by_meaning_and_dedupes_by_blocks():
    h = History(OFFERS, embed)
    ids = [o.id for _, o in h.similar("every 5th store visit earns one coffee", k=2)]
    assert ids[0] in ("1", "2") and ids[1] == "5"  # one of the two identical sub-prompts, then the next nearest
    assert next(o.id for _, o in h.similar("every 5th store visit earns one coffee", exclude_id=1)) == "2"
    assert len(h.similar("weekend fuel rebate")) == 2  # always 2: no similarity floor (user, 2026-10-27)


def test_named_offer_is_part_of_the_request_not_a_suggestion():
    h = History(OFFERS, embed)
    e = h.earlier("like our Ramadan Spend offer, but at all stores", k=3)
    assert [o.id for o in e["named"]] == ["3"] and "3" not in [o.id for _, o in e["similar"]]
    assert h.named("ramadan spending") == []  # whole names only


def test_records_load_with_their_subprompt():
    o = from_record(
        {"id": 7, "name": "Coffee Club", "prompt": "every 5th visit", "xml": "<xml/>", "subprompt": "rule_1, x"}
    )
    assert (o.id, o.text, o.subprompt) == ("7", "every 5th visit", "rule_1, x")
    assert from_record({"id": 8, "name": "no lines", "prompt": "p", "xml": "<xml/>"}) is None  # not shown


def test_earlier_offers_show_the_prompt_and_subprompt_after_the_form_note():
    text = prompt.earlier_offers({"named": [], "similar": [(0.8, OFFERS[0])]})
    assert text.startswith(prompt.SUBPROMPT_FORM)
    assert (
        '1. "Coffee Club" (similarity 0.80)\n   Prompt: every 5th visit earns a free coffee\n   Sub-prompt:\n   rule_1,'
        in text
    )


def test_histories_reload_when_the_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr("offer_author.core.tenant.history.from_record", lambda r, own=True: offer(r["id"], r["prompt"]))
    monkeypatch.setattr("offer_author.core.tenant.history.vet", lambda xml, catalog: [])
    (tmp_path / "t1").mkdir()
    f = tmp_path / "t1" / "offers.jsonl"
    f.write_text(json.dumps({"id": 1, "prompt": "a b", "xml": "<xml/>"}) + "\n")
    hs = Histories(str(tmp_path), embed)
    assert hs.get("other") is None and len(hs.get("t1").offers) == 1 and hs.get("t1") is hs.get("t1")
    f.write_text(
        json.dumps({"id": 1, "prompt": "a b", "xml": "<xml/>"})
        + "\n"
        + json.dumps({"id": 2, "prompt": "c", "xml": "<xml/>"})
        + "\n"
    )
    os.utime(f, (1, 1))
    assert len(hs.get("t1").offers) == 2


def test_run_shows_earlier_offers_in_the_request_and_reports_them(fixture_catalog):
    systems = []
    llm = ScriptedLLM([[("compose", S07)], [finish(readback="every 5th visit earns a coffee")]])
    turn = llm.turn
    llm.turn = lambda system, messages, *a, **k: systems.append(system) or turn(system, messages, *a, **k)
    settings = Settings(similar_offers=3, history="unused")
    res = agent.generate(
        "every 5th store visit earns one coffee",
        fixture_catalog,
        llm,
        offer_id=1,
        settings=settings,
        history=History(OFFERS, embed),
    )
    first = json.dumps(llm.calls[0][0])
    assert "Earlier offers of this tenant that look similar (reference only" in first and "Offer 2" in first
    assert "Coffee Club" not in first  # offer 1 is the offer being edited
    assert res["diagnostics"]["earlier"]["similar"][0][0] == "2"
    assert prompt.HISTORY_RULE in systems[0][0]["text"] and "# Seeded blueprints" not in systems[0][1]["text"]


def test_run_without_the_flag_shows_none(fixture_catalog):
    llm = ScriptedLLM([[("compose", S07)], [finish(readback="every 5th visit earns a coffee")]])
    res = agent.generate(
        "every 5th visit", fixture_catalog, llm, settings=Settings(similar_offers=0), history=History(OFFERS, embed)
    )
    assert "Earlier offers" not in json.dumps(llm.calls[0][0]) and "earlier" not in res["diagnostics"]


def test_retrieval_failure_does_not_fail_the_run(fixture_catalog):
    def broken(texts, kind="document"):
        if kind == "query":
            raise RuntimeError("bedrock down")
        return embed(texts)

    llm = ScriptedLLM([[("compose", S07)], [finish(readback="every 5th visit earns a coffee")]])
    res = agent.generate(
        "every 5th visit", fixture_catalog, llm, settings=Settings(similar_offers=3), history=History(OFFERS, broken)
    )
    assert res["status"] == "draft" and "earlier" not in res["diagnostics"]


def test_masker_replaces_the_tenants_values_not_everyday_words():
    from offer_author.core.tenant.catalog import Attribute, Catalog
    from offer_author.core.tenant.history import masker

    cat = Catalog(
        tenant="t",
        sponsor_id=1,
        activity_types={"SPEND": "Spend"},
        tiers={"TC": {"G": "Gold"}},
        privileges={"1": {"id": "1", "name": "Ski Dubai Free Entry Pass"}},
        attributes={
            "activity": {
                "h_location": Attribute(
                    "h_location", "Store", "LOV", "SPONSOR_LOCATION_LIST", {"HYD": "Hyderabad", "KOL": "Kolkata Mall"}
                ),
                "h_bit_category": Attribute("h_bit_category", "Category", "LOV", "BIT_CATEGORY", {"P": "Points"}),
            }
        },
    )
    m = masker(cat)
    assert (
        m("Every 3rd spend by Gold members at Kolkata Mall earns a free Ski Dubai entry pass")
        == "every N spend by tier members at store earns a privilege"
    )
    assert m("2x points on spends over $500 at Hyderabad") == "N points on spends over N at store"  # 'points' stays


def test_masked_offers_match_by_what_they_do():
    from offer_author.core.tenant.catalog import Catalog
    from offer_author.core.tenant.history import masker

    cat = Catalog(
        tenant="t",
        sponsor_id=1,
        tiers={"TC": {"G": "Gold", "S": "Silver"}},
        privileges={"1": {"id": "1", "name": "Ski Pass"}, "2": {"id": "2", "name": "Movie Ticket"}},
    )
    h = History(
        [offer(1, "every 3rd spend by Silver members earns a ski pass"), offer(2, "Gold members earn a movie ticket")],
        embed,
        masker(cat),
    )
    assert h.similar("every 2nd spend by Gold members earns a movie ticket", k=1)[0][1].id == "1"


def test_histories_keep_the_embeddings_for_an_equal_catalog(tmp_path, fixture_catalog):
    import dataclasses

    (tmp_path / "t1").mkdir()
    (tmp_path / "t1" / "offers.jsonl").write_text("")
    calls = []
    hs = Histories(str(tmp_path), lambda texts, kind="document": calls.append(kind) or embed(texts, kind))
    first = hs.get("t1", fixture_catalog)
    assert hs.get("t1", dataclasses.replace(fixture_catalog)) is first  # a new but equal catalog: no reload


def test_warm_loads_the_history_when_it_is_on(fixture_catalog, monkeypatch):
    from offer_author.core.tenant import embed as embed_mod

    got = []
    monkeypatch.setattr(
        embed_mod, "histories", lambda root, model: type("H", (), {"get": lambda self, t, c: got.append(t)})()
    )
    llm = type("L", (), {"warm": lambda self, system, tools: "ok"})()
    assert agent.warm(llm, fixture_catalog, settings=Settings(similar_offers=3, history="x")) == "ok" and got == [
        fixture_catalog.tenant
    ]
