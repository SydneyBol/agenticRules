"""One test per fixed fault (PulsarRAG-Review.md §4, §5), each on the fixed copy; the original fails where noted."""
import re
from collections import Counter
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from conftest import embed


# ---------------------------------------------------------------- contextDB: exact cosine index, floor, k > rows
@pytest.fixture
def db(monkeypatch):
    from general.vectorDB import contextDB
    rows = pd.DataFrame({"user": ["spend at hyderabad earns points", "weekend spend double points",
                                  "birthday bonus voucher", "refer a friend reward"],
                         "system": ["L1", "L2", "L3", "L4"]})
    monkeypatch.setattr(contextDB.pd, "read_csv", lambda _: rows)
    return contextDB.ContextDB("s3://gravty-ai-test/1/gpt/rag/offer/vectordb/data.csv")


def test_search_is_exact_cosine_best_first(db):
    q = "weekend spend double points"
    hits = db.search_top_k([q], 10)[q]["matches"]
    assert [h["context"] for h in hits][0] == "L2" and hits[0]["score"] == pytest.approx(1.0)
    assert len(hits) == 4  # k larger than the corpus: every row once, no -1 ids
    def cos(a, b):
        return float(embed(a) @ embed(b) / np.linalg.norm(embed(a)) / np.linalg.norm(embed(b)))
    assert [h["score"] for h in hits] == pytest.approx(sorted((cos(q, u) for u in db.DB["user"]), reverse=True))


def test_similarity_floor_drops_unrelated_examples(db):
    hits = db.search_top_k(["birthday bonus voucher"], 10, min_score=0.5)["birthday bonus voucher"]["matches"]
    assert [h["context"] for h in hits] == ["L3"]
    assert db.get_top_context("completely unrelated words", default_context="D") in ("D", "L1", "L2", "L3", "L4")


def test_rag_cache_expires_so_every_worker_reloads():
    from general.vectorDB import contextDB
    assert type(contextDB.rag_cache).__name__ == "TTLCache" and contextDB.rag_cache.ttl == 900


# ---------------------------------------------------------------- sub-prompts: names, disabled blocks
def test_counter_names_replaced_literally_and_once():
    from pulsar.data_validation.sub_prompts.blockwise_subprompt import replace_names
    names = {"Spend (AED)": "NUMBER Spend (AED)", "spend": "NUMBER spend", "Total spend": "NUMBER Total spend"}
    line = 'if member offer_level_tracker "Spend (AED)" > 5 and "Total spend" > 1 and "spend" > 0 and "spender"'
    assert replace_names(line, names) == ('if member offer_level_tracker "NUMBER Spend (AED)" > 5 and '
                                          '"NUMBER Total spend" > 1 and "NUMBER spend" > 0 and "spender"')
    # the original: re.sub("Spend (AED)", ...) never matched, and "spend" was replaced inside "Total spend"
    assert re.sub("Spend (AED)", "x", line) == line


def test_blocks_disabled_by_current_ui_are_dropped():
    from pulsar.data_validation.sub_prompts.blockwise_subprompt import DISABLED
    blocks = pd.Series(['<block type="action" disabled="true">', '<block type="action" disabled-reasons="MANUALLY_DISABLED">',
                        '<block type="action">'])
    assert blocks.str.contains(DISABLED).tolist() == [True, True, False]


# ---------------------------------------------------------------- templatising
def test_only_mutation_tags_are_opened():
    from pulsar.data_validation.templatize_offer import open_mutation_tags
    import xml.etree.ElementTree as ET
    xml = ('<xml xmlns="https://developers.google.com/blockly/xml"><block type="static_string_value" id="1">'
           '<mutation type="TEXT" /><field name="value" /></block></xml>')
    out = open_mutation_tags(xml)
    assert '<mutation xmlns="http://www.w3.org/1999/xhtml" type="TEXT"></mutation>' in out
    assert '<field name="value" />' in out
    ET.fromstring(out)  # well formed (the old replace made <field ...></mutation>)


def test_prompt_phrases_capped_at_label_length():
    from pulsar.data_validation.templatize_offer import get_consecutive_substrings
    prompt = " ".join(f"w{i}" for i in range(60))
    assert len(get_consecutive_substrings(prompt)) == 1830
    capped = get_consecutive_substrings(prompt, 4)
    assert len(capped) == 60 + 59 + 58 + 57 and max(len(p.split()) for p in capped) == 4
    assert get_consecutive_substrings("gold tier members", 2) == ["gold", "gold tier", "tier", "tier members", "members"]


def test_lov_page_fetched_once_per_url(monkeypatch):
    from pulsar.data_validation import templatize_offer as t
    resp = MagicMock()
    resp.json.return_value = {"results": [{"code": "HYD"}]}
    get = MagicMock(return_value=resp)
    monkeypatch.setattr(t.requests, "get", get)
    monkeypatch.setattr(t, "_lov_pages", {})
    assert t._lov_page("u", {}) == [{"code": "HYD"}] and t._lov_page("u", {}) == [{"code": "HYD"}]
    assert get.call_count == 1 and get.call_args.kwargs["timeout"] == 30


def test_untemplatable_offer_is_skipped():
    from pulsar.data_validation.templatize_offer import templatize_xml
    assert templatize_xml("<not xml", "prompt", 1, []) is None  # was: the input, real values and all


# ---------------------------------------------------------------- diverse samples, clustering
def _greedy_reference(vectors, n, threshold):
    v = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    keep, left = [], list(range(len(v)))[::-1]  # newest (last appended) first
    while left and len(keep) < n:
        c = left[0]
        keep.append(c)
        left = [i for i in left if i != c and v[i] @ v[c] <= threshold]
    return keep


def test_select_diverse_samples_matches_exact_greedy():
    from pulsar.data_validation.n_diverse_context import select_diverse_samples
    rng = np.random.default_rng(1)
    centres = rng.normal(size=(6, 16))
    vectors = np.vstack([c + 0.05 * rng.normal(size=(15, 16)) for c in centres]).astype("float32")
    df = pd.DataFrame({"user": [f"row {i}" for i in range(len(vectors))]})
    picked = select_diverse_samples(df, vectors.copy(), num_samples=50, similarity_threshold=0.9)
    assert list(picked.index) == _greedy_reference(vectors, 50, 0.9)
    assert len(picked) == 6  # one per near-duplicate group


def test_select_diverse_samples_drops_near_duplicates_behind_dropped_rows():
    """Unit vectors at these angles, threshold cos 0.9 (25.8 degrees): 0 drops 20-25; 27 is kept and must drop 46 and
    47 (19-20 degrees away). The old search asked for len(remaining) = 3 hits for 27, got itself plus 25 and 24
    (already dropped), and kept 47 as a third sample."""
    from pulsar.data_validation.n_diverse_context import select_diverse_samples
    angles = np.radians([0, 20, 22, 24, 25, 27, 47, 46])
    vectors = np.stack([np.cos(angles), np.sin(angles)], axis=1).astype("float32")
    df = pd.DataFrame({"user": [f"row {i}" for i in range(len(vectors))]})
    # newest first (F24): 46 keeps; 47, 27, 25, 24, 22 are within 25.8 degrees of it; 20 is 26 away and keeps, dropping 0
    assert list(select_diverse_samples(df, vectors, num_samples=7, similarity_threshold=0.9).index) == [7, 1]


def test_encode_sentences_uses_the_model_passed():
    from pulsar.data_validation.n_diverse_context import encode_sentences
    model = MagicMock()
    model.encode.return_value = "mpnet vectors"
    assert encode_sentences(["a"], vector_model=model) == "mpnet vectors"


@pytest.mark.parametrize("n", [3, 4, 5, 7, 8])
def test_optimal_clusters_valid_for_small_clusters(n):
    from pulsar.model_finetune.block_diverse_data import find_optimal_clusters
    from sklearn.cluster import KMeans
    x = np.random.default_rng(n).normal(size=(n, 8))
    k = find_optimal_clusters(x, max_clusters=min(40, n - 1))
    assert 2 <= k <= n - 1
    KMeans(n_clusters=k, random_state=42).fit(x)  # the old k=7 raised for n <= 7


def test_optimal_clusters_finds_separated_groups():
    from pulsar.model_finetune.block_diverse_data import find_optimal_clusters
    rng = np.random.default_rng(3)
    centres = rng.normal(scale=20, size=(9, 8))
    x = np.vstack([c + rng.normal(size=(20, 8)) for c in centres])
    assert find_optimal_clusters(x, max_clusters=30) == 9


def test_diverse_finetuning_samples_pick_the_outliers(monkeypatch):
    from pulsar.model_finetune import block_diverse_data as b
    rng = np.random.default_rng(4)
    tight = np.ones((10, 8)) + 0.01 * rng.normal(size=(10, 8))
    far = np.array([[-5.0] * 8, [5.0, -5.0] * 4])
    vectors = np.vstack([tight[:4], far[:1], tight[4:], far[1:]]).astype("float32")  # outliers at rows 4 and 11
    monkeypatch.setattr(b, "encode_sentences_in_batches", lambda texts: vectors.copy())
    df = pd.DataFrame({"SubPrompt": ["give points to members now"] * 12})
    picked = b.select_diverse_finetuning_samples(df, min_samples_per_length=3)
    # both outliers and one of the tight group (F26: farthest points); the old code returned the last rows, 9 to 11
    assert {4, 11} < set(picked.index) and len(picked) == 3


def test_partition_dataframe_same_rows_as_before():
    from pulsar.model_finetune.block_diverse_data import partition_dataframe
    df = pd.DataFrame({"SubPrompt": ["if member tier", "update_attribute x", "give_points 5", "if_payment card", None],
                       "Block": list("abcde")})
    rules = {"update_attribute": ["update_attribute"], "if_filter_block": ["if", "offer_criteria", "if_payment"],
             "only_priv": ["give_points"]}
    parts = partition_dataframe(df, rules)
    assert parts["if_filter_block"]["Block"].tolist() == ["a", "d"]
    assert parts["update_attribute"]["Block"].tolist() == ["b"] and parts["only_priv"]["Block"].tolist() == ["c"]


# ---------------------------------------------------------------- program cache
def test_program_cache_ttl_and_unknown_program(monkeypatch):
    from general.pulsar.program_cache import template_cache as tc
    assert isinstance(tc.cache_ttl, int)
    cache = tc.TTLCache(ttl=60)
    assert cache.is_expired(123)  # was KeyError, after generate_rule had already built the rules
    cache.store(123, {})
    assert not cache.is_expired(123)


def test_store_clears_memoised_codes():
    from general.pulsar.program_cache import template_cache as tc
    proc = tc.CacheProcessor.__new__(tc.CacheProcessor)
    proc.ttl_cache, proc.attribute_for_gpt = tc.TTLCache(), {}
    proc.ttl_cache.store(1, {"member": {"tier": {"GLD": "Gold"}}})
    proc.most_similar_sentence_transformer = lambda value, names: (0, 1.0)
    assert proc.get_backend_code(1, "member", "tier", "gold") == ("GLD", 1.0)
    proc.ttl_cache.store(1, {"member": {"tier": {"GOLD2": "Gold"}}})
    assert proc.get_backend_code(1, "member", "tier", "gold") == ("GOLD2", 1.0)  # was the stale "GLD"


# ---------------------------------------------------------------- §5 (2026-10-27): faults found on demov4's 443 saved offers
def test_schema_accepts_mutation_attributes_of_other_ui_versions():
    """F18: PAYMENT_OBJECT / activecardsoperator (payment-attribute and cobrand-card offers) failed the closed list."""
    xmlschema = pytest.importorskip("xmlschema")
    if not hasattr(xmlschema, "XMLSchema") or isinstance(xmlschema.XMLSchema, MagicMock):
        pytest.skip("xmlschema is stubbed")
    import os
    schema = xmlschema.XMLSchema(os.path.join(os.path.dirname(__file__), "..", "general", "pulsar", "block_schema.xsd"))
    block = '<root><block type="if_payment_attribute" id="a"><mutation PAYMENT_OBJECT="card" activecardsoperator="==" attribute="x"></mutation></block></root>'
    assert schema.is_valid(block)


def test_blueprint_offers_are_not_examples():
    """F18: a blueprint holds placeholders ("activity_1", "Blueprint - Activity"), not rules."""
    from pulsar.data_validation.sub_prompts import blockwise_subprompt as bs
    assert re.search(bs.BLUEPRINT, '<block type="blueprint_variable" id="x">')
    assert re.search(bs.BLUEPRINT, '<mutation object="activity" blueprinttypes="BLUEPRINT_MUTATION">')
    assert not re.search(bs.BLUEPRINT, '<mutation object="activity" attribute="h_bit_type">')


def test_counter_with_no_name_is_a_reasoned_error():
    """F19: give_points reading a member_attribute MA_O block with attribute="" raised IndexError mid-merge."""
    from pulsar.data_validation.sub_prompts.subprompt_formatter import RuleHandler
    block = '<block type="action"><mutation attribute_type_input="give_points"></mutation><value name="FORMULA"><block type="member_attribute"><mutation attribute_type_input="MA_O" attribute="" attr_type="undefined"></mutation></block></value></block>'
    rule = ["rule_line_5,,,", "0->>give_points", "1->>12", "2->>BASE", "member_attribute->0->>MA_O"]
    with pytest.raises(ValueError, match="counter with no name"):
        RuleHandler(rule=rule, blocks=[block], block_index=0, attribute_names={}, rule_index=4)


def test_offer_rows_keep_one_copy_per_saved_version_newest_last(monkeypatch):
    """F20: dup_check='Prompt' named no column: a row per update day piled up; and 'first' kept the oldest copy."""
    from pulsar.data_store import lambda_function as lf
    written = {}
    existing = pd.DataFrame([{"id": 7, "updated_ts": "2026-10-01", "offerXML": "old"},
                             {"id": 7, "updated_ts": "2026-10-02", "offerXML": "v2"}]).to_csv(index=False)
    monkeypatch.setattr(lf, "read_file", lambda *a, **k: existing)
    monkeypatch.setattr(lf, "write_file", lambda file_key, data, **k: written.update({file_key: data}))
    lf.retraining_data_to_s3(file_key="k", data={"id": 7, "updated_ts": "2026-10-02", "offerXML": "v2 again"},
                             dup_check=["id", "updated_ts"])
    from io import StringIO
    rows = pd.read_csv(StringIO(written["k"]))
    assert rows["offerXML"].tolist() == ["old", "v2 again"]


@pytest.fixture
def pv(monkeypatch):
    """prompt_validator imports the Batch image's top-level templatize_offer module."""
    import sys
    import types
    monkeypatch.setitem(sys.modules, "templatize_offer", types.SimpleNamespace(templatize_xml=lambda *a, **k: None))
    monkeypatch.setenv("query", "false")  # the Batch job's environment: nightly run
    from pulsar.data_validation import prompt_validator
    return prompt_validator


def test_two_prompts_keep_the_earlier_one_when_similar(pv, monkeypatch):
    """F22: `len(self._prompt_list) > 1` dropped the earlier of exactly two prompts, however similar."""
    monkeypatch.setattr(pv, "read_file", lambda *a, **k: "")
    monkeypatch.setattr(pv, "templatisation", lambda **k: "<xml/>")
    monkeypatch.setattr(pv, "calculate_similarity", lambda a, b: 0.9)
    offer = pv.Offer(offer_id="1", sponsors=["1"], rule="<xml/>", prompts=["2x points at HQ", "2x points at HQ store"])
    assert [p.prompt for p in offer.valid_prompts] == ["2x points at HQ", "2x points at HQ store"]


def test_structure_hash_tells_different_rules_apart(pv):
    """F23: the first 3 words of each line: an h_bit_currency filter and a rate_code filter shared a key."""
    generate_hash = pv.generate_hash
    a = "Filter_1, offer_criteria activity h_bit_currency == {h_bit_currency: USD}\nrule_2, give_points 2 BASE"
    b = "Filter_1, offer_criteria activity rate_code not_in {rate_code: X}\nrule_2, give_points 2 BASE"
    renumbered = "Filter_3, offer_criteria activity h_bit_currency == {h_bit_currency: USD}\nrule_7, give_points 2 BASE"
    assert generate_hash(a) != generate_hash(b) and generate_hash(a) == generate_hash(renumbered)


def test_rag_store_drops_near_duplicates_under_the_cap_keeping_the_newest():
    """F24: at or under num_samples (800 for the RAG store) nothing was dropped; the oldest of near duplicates won."""
    from pulsar.data_validation.n_diverse_context import select_diverse_samples
    df = pd.DataFrame({"user": ["earn 2x at store (old)", "earn 2x at store", "earn 2x at store (new)", "birthday coffee"]})
    e = np.array([[1, 0], [1, 0], [1, 0.01], [0, 1]], dtype="float32")
    assert sorted(select_diverse_samples(df, e, 800, 0.9)["user"]) == ["birthday coffee", "earn 2x at store (new)"]


def test_lines_ending_in_an_operator_are_filtered():
    """F25: str[-1] (one character) never equals '>=' or '=='."""
    from pulsar.model_finetune.block_diverse_data import usable_lines
    df = pd.DataFrame({"SubPrompt": ["Filter_1, offer_criteria activity h_bit_amount >=", "rule_2, give_points 10 BASE"]})
    assert usable_lines(df)["SubPrompt"].tolist() == ["rule_2, give_points 10 BASE"]


def test_farthest_points_cover_every_group():
    """F26: the 10 farthest by mean distance all came from the outlying groups, none from the majority."""
    from pulsar.model_finetune.block_diverse_data import farthest_points
    rng = np.random.default_rng(0)
    x = np.vstack([rng.normal(c, 0.05, (n, 3)) for c, n in (([1, 0, 0], 40), ([0, 1, 0], 12), ([0, 0, 1], 12))])
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    d = np.maximum(0, 2 - 2 * x @ x.T)
    picks = farthest_points(d, 50, 10)
    assert len(set(picks)) == 10 and {0, 1, 2} <= {0 if i < 40 else 1 if i < 52 else 2 for i in picks}
    assert len(set(farthest_points(np.zeros((5, 5)), 0, 3))) == 3  # identical rows: still 3 different ones


# ---------------------------------------------------------------- generated vs saved feedback (SubpromptFeedback.md)
GOLDEN = __import__("os").path.abspath(__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "..", "tests", "golden", "compiled"))


PULSAR_ROOT = __import__("os").path.join(__import__("os").path.dirname(__file__), "..")


def _golden(name):
    return open(__import__("os").path.join(GOLDEN, name + ".xml")).read()


def _original_failed_blocks(model_lines, saved_lines):
    """ai-develop's comparison: label-free texts, "matched" = occurs twice or more across both lists."""
    combined = pd.DataFrame({"SubPrompt": model_lines + saved_lines})
    duplicates = combined[combined.duplicated(keep=False)]
    return [s for s in saved_lines if s not in set(duplicates["SubPrompt"])]


def test_a_line_the_saved_offer_repeats_is_still_a_miss_and_the_wrong_line_an_extra(pv, monkeypatch):
    """B1, B3: the saved rule 2 reads > 100 like rule 1; the draft wrote > 500 there."""
    monkeypatch.chdir(PULSAR_ROOT)  # Pulsar reads its block schema relative to its root
    draft = _golden("S01_tiered_exclusive")
    saved = draft.replace('id="ai00015"><field name="value">500<', 'id="ai00015"><field name="value">100<')
    rows = pv.failed_blocks_filter(draft, saved, program="1")
    got = sorted(zip(rows["Kind"], rows["SubPrompt"]))
    assert got == [("extra", "if activity h_bit_amount > 500"), ("missing", "if activity h_bit_amount > 100")]
    assert set(rows["program_id"]) == {"1"} and all(rows["Place"] == "activity 1 rule")  # B5: rows carry their program
    from pulsar.data_validation.sub_prompts.blockwise_subprompt import xml_to_subprompt
    text = lambda x: [ln.split(",,,")[1].strip() for ln, _ in xml_to_subprompt(x)]
    assert "if activity h_bit_amount > 100" not in _original_failed_blocks(text(draft), text(saved))  # the original


def test_the_same_offer_gives_no_rows_and_rule_order_does_not_matter():
    """B1, B10 (in part): units are matched by their lines, whatever the order of the rules."""
    from pulsar.data_validation.sub_prompts.feedback import compare
    r1 = Counter({((), "if activity h_bit_amount > 100"): 1, ((), "give_points 1 BONUS 2"): 1})
    r2 = Counter({((), "if activity h_bit_amount > 500"): 1, ((), "give_points 1 BONUS 3"): 1})
    saved = {(1, ("rule#1",)): r1, (1, ("rule#2",)): r2}
    draft = {(1, ("rule#1",)): r2, (1, ("rule#2",)): r1}
    assert compare(draft, saved) == ([], [])


def test_a_condition_on_the_wrong_side_of_an_or_or_in_the_wrong_rule_is_a_miss():
    """B2: Pulsar dropped the labels, so these drafts counted as right."""
    from pulsar.data_validation.sub_prompts.feedback import compare
    saved = {(1, ("rule#1",)): Counter({(((1, 1),), "if a"): 1, (((1, 2),), "if b"): 1, ((), "give 1"): 1}),
             (1, ("rule#2",)): Counter({((), "if c"): 1, ((), "give 2"): 1})}
    swapped_or = {(1, ("rule#1",)): Counter({(((1, 1),), "if a"): 1, (((1, 1),), "if b"): 1, ((), "give 1"): 1}),
                  (1, ("rule#2",)): Counter({((), "if c"): 1, ((), "give 2"): 1})}
    missing, extra = compare(swapped_or, saved)
    assert [m[1:] for m in missing] == [(((1, 2),), "if b")] and [e[1:] for e in extra] == [(((1, 1),), "if b")]
    moved = {(1, ("rule#1",)): Counter({(((1, 1),), "if a"): 1, (((1, 2),), "if b"): 1, ((), "give 1"): 1,
                                        ((), "if c"): 1}),
             (1, ("rule#2",)): Counter({((), "give 2"): 1})}
    missing, extra = compare(moved, saved)
    assert [m[2] for m in missing] == ["if c"] and [e[2] for e in extra] == ["if c"]


def test_the_or_branches_are_read_from_the_xml(pv, monkeypatch):
    monkeypatch.chdir(PULSAR_ROOT)
    from pulsar.data_validation.sub_prompts.blockwise_subprompt import xml_to_subprompt
    from pulsar.data_validation.sub_prompts.feedback import place_label, units
    x = _golden("S04_happy_hour_or")
    u = units(x, xml_to_subprompt(x))
    places = {place_label(unit, br) for unit, lines in u.items() for br, _ in lines}
    assert places == {"activity 1 Filter", "activity 1 rule", "activity 1 rule, OR 1 branch 1",
                      "activity 1 rule, OR 1 branch 2"}


def test_an_unreadable_draft_teaches_nothing(pv):
    """B7: the original logged the parse error and turned every saved line into a failure row."""
    saved = _golden("S01_tiered_exclusive")
    assert pv.failed_blocks_filter("<xml><block", saved) is None
    assert pv.failed_blocks_filter("", saved) is None


def test_a_suspended_offer_is_neither_an_example_nor_a_fine_tune_row(pv, monkeypatch):
    """B9: the newest saved row is suspended: the offer is not used, its older launched versions neither."""
    rows = pd.DataFrame([{"offerXML": "eA==", "status": "launched", "offer_Prompt": "2x at HQ"},
                         {"offerXML": "eA==", "status": "suspended", "offer_Prompt": "2x at HQ"}]).to_csv(index=False)
    monkeypatch.setattr(pv, "read_file", lambda *a, **k: rows)
    pv.OfferPromptValidator.instance = None
    pv.OfferPromptValidator()
    pv.OfferPromptValidator.process_prompts(offer_id="9", sponsor_id=["1"])
    assert pv.OfferPromptValidator.instance.offer_list == []
