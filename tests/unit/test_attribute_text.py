"""What the catalog text shows of an attribute (descriptions, tenant use, a shortlist), find_attributes, and the test
cases' unset attributes (AttributeSelection-Review-2026-11-12.md, items 4 to 7 and 5)."""

import copy
import json

from offer_author.ai import cases, prompt, tools
from offer_author.ai.prompt import CatalogShow, catalog_show, catalog_text, shortlisted
from offer_author.core.run import Run
from offer_author.core.settings import Settings
from offer_author.core.tenant.catalog import Attribute, says_more
from offer_author.core.tenant.usage import attribute_use
from tests.support import golden


def attr(description, label="Bonus", code="h_bonus"):
    return Attribute(code=code, label=label, type="TEXT", description=description)


def test_a_description_is_kept_only_when_it_tells_more_than_the_label():
    assert says_more(attr("Currency in which the transaction has been done", "BIT Currency", "h_bit_currency"))
    for filler in ("", ".", "Hi", "QA Testing", "Reason for Membership Cancellation"):
        assert says_more(attr(filler, "Membership Cancellation Reason", "h_cancel")) == ""


def test_descriptions_are_shown_only_when_asked(fixture_catalog):
    cat = copy.deepcopy(fixture_catalog)
    cat.attributes["activity"]["h_bit_category"].description = "The kind of reward event the member's spend belongs to"
    plain, shown = catalog_text(cat), catalog_text(cat, show=CatalogShow(descriptions=True))
    assert "  -- " not in plain and "[-- what it holds]" not in plain
    assert "h_bit_category" in shown and "  -- The kind of reward event" in shown and "[-- what it holds]" in shown


def test_the_attributes_earlier_offers_read_come_first_and_a_shortlist_leaves_the_rest_to_a_search(fixture_catalog):
    use = {("activity", "l_amount"): 5, ("member", "gender"): 2}
    ordered = catalog_text(fixture_catalog, show=CatalogShow(ordered=True, use=use))
    block = ordered.split("## ACTIVITY")[1].split("\n## ")[0].splitlines()
    assert block[1].startswith("l_amount |") and ordered.count("l_amount |") == 1
    short = catalog_text(fixture_catalog, show=CatalogShow(use=use, shortlist=True))
    assert "l_amount |" in short and "h_bit_type |" in short  # used, and the core
    assert "enrollment_date |" not in short and "gender |" in short  # used; the other member attributes are left out
    assert "more member attributes are not listed: use find_attributes" in short
    assert len(short) < len(catalog_text(fixture_catalog))


def test_the_catalog_is_a_shortlist_only_over_the_size_asked(fixture_catalog):
    total = sum(len(v) for v in fixture_catalog.attributes.values())
    assert not shortlisted(fixture_catalog, 0) and not shortlisted(fixture_catalog, total)
    assert shortlisted(fixture_catalog, total - 1)
    show = catalog_show(fixture_catalog, Settings(shortlist_over=total - 1))
    assert show.shortlist and not show.descriptions and catalog_show(fixture_catalog, Settings()) == CatalogShow()


def test_the_use_of_each_attribute_is_counted_over_the_tenants_stored_offers(fixture_catalog, tmp_path):
    from tests.unit.test_dsl_feedback import xml_of

    folder = tmp_path / fixture_catalog.tenant
    folder.mkdir()
    xml = xml_of("S14_simple_sanity")
    (folder / "offers.jsonl").write_text("\n".join(json.dumps({"id": i, "xml": xml}) for i in "ab") + "\nnot json\n")
    assert attribute_use(str(tmp_path), fixture_catalog) == {
        ("activity", "h_bit_type"): 2,
        ("activity", "h_bit_amount"): 2,
    }
    assert attribute_use("", fixture_catalog) == {} and attribute_use(str(tmp_path / "none"), fixture_catalog) == {}


def test_find_attributes_is_in_the_request_only_over_a_shortlist(fixture_catalog):
    names = lambda s: [t["name"] for t in s]
    assert "find_attributes" not in names(tools.schemas_for("json")) and tools.schemas_for("json") is tools.SCHEMAS
    assert names(tools.schemas_for("json", True))[-1] == "find_attributes"
    assert names(tools.schemas_for("dsl", True))[:4] == names(tools.schemas_for("dsl"))
    out = Run(fixture_catalog, None).find_attributes({"words": "birth date"})
    assert any(a["code"] == "date_of_birth" for a in out["attributes"])


def test_the_attributes_no_case_sets_are_the_ones_the_rules_read():
    reads = [
        {"list": "activity", "place": "the transaction", "attribute": "h_bit_currency", "label": "BIT Currency"},
        {"list": "activity", "place": "the transaction", "attribute": "h_bit_amount", "label": "BIT Amount"},
        {"list": "member", "place": "the member's profile", "attribute": "age", "label": "Age"},
        {"list": "location", "place": "the store", "attribute": "city", "label": "City"},
    ]
    one = {
        "member": {"MA_P": {"age": 30}},
        "bits": [{"header": {"h_bit_amount": 5}, "lines": [], "payment_details": []}],
    }
    assert [r["attribute"] for r in cases.unset_reads([one], reads)] == ["h_bit_currency"]
    two = {"member": {"MA_P": {}}, "bits": [{"header": {"h_bit_currency": "AED"}}]}
    assert [r["attribute"] for r in cases.unset_reads([two], reads)] == ["age"]
    pay = {"member": {"MA_P": {"age": 1}}, "bits": [{"header": {}, "payment_details": [{"h_bit_currency": "AED"}]}]}
    assert cases.unset_reads([pay], reads) == []


def test_golden_is_unchanged_by_the_default_show():
    assert prompt.catalog_text.__defaults__ is not None and golden("S14_simple_sanity")
