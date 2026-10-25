"""Catalog-grounded validation: the errors the repair round feeds back to the model."""
import pytest

from conftest import c, finish, priv, pts
from offer_author.validate import validate


def errs(cat, *calls):
    return [str(i) for i in validate(cat, [(f"t{k}", n, x) for k, (n, x) in enumerate(calls)])]


def earn(conds=(), measure="h_bit_amount", factor=2):
    return ("add_earn", {"measure": measure, "factor_kind": "multiplier", "factor": factor, "conditions": list(conds), "or_groups": []})


def test_clean_offer_passes(fixture_catalog):
    assert errs(fixture_catalog, earn([c("activity", "h_location", "in", values=["L1", "L2"])]), finish(activity_types=["SPEND"])) == []


@pytest.mark.parametrize("cond,needle", [
    (c("activity", "h_store", "in", values=["L1"]), "unknown activity attribute 'h_store'"),
    (c("activity", "h_location", "in", values=["L9"]), "['L9'] are not h_location codes"),
    (c("activity", "h_bit_type", "in", values=["PURCHASE"]), "['PURCHASE'] are not h_bit_type codes"),
    (c("activity", "h_bit_amount", "in", values=["5"]), "is NUMBER; use a comparison operator"),
    (c("activity", "h_bit_amount", ">", "fifty"), "is not a number"),
    (c("activity", "h_bit_date", "in", values=["8"], dim="DAY_OF_WEEK"), "DAY_OF_WEEK values must be numbers 1-7"),
    (c("activity", "h_location", "==", "L1", dim="HOUR_OF_DAY"), "only applies to DATE/DATETIME"),
    (c("member", "tier", ">", "GOLD"), "only ==, !=, in, not_in apply"),
])
def test_bad_conditions_are_explained(fixture_catalog, cond, needle):
    e = errs(fixture_catalog, earn([cond]), finish())
    assert any(needle in m for m in e), e


def test_measure_must_be_numeric(fixture_catalog):
    assert any("measure 'h_location'" in m for m in errs(fixture_catalog, earn(measure="h_location"), finish()))


def test_unknown_privilege_and_product_codes(fixture_catalog):
    e = errs(fixture_catalog,
             ("add_collect_all", {"product_attribute": "category", "groups": [["SNACKS"], ["CANDY"]], "reward": priv("999")}), finish())
    assert any("['CANDY'] are not category codes" in m for m in e) and any("privilege_id '999'" in m for m in e)


def test_overlapping_tiers(fixture_catalog):
    band = lambda lo, hi: {"lower": lo, "lower_inclusive": True, "has_upper": hi is not None, "upper": hi or 0,
                           "upper_inclusive": True, "factor_kind": "multiplier", "factor": 2}
    e = errs(fixture_catalog, ("add_tiers", {"measure": "h_bit_amount", "bands": [band(100, 500), band(500, None)]}), finish())
    assert any("overlap" in m for m in e)


def test_finish_settings(fixture_catalog):
    e = errs(fixture_catalog, earn(), finish(start_date="2026-12-01", end_date="2026-11-01", activity_types=["SHOP"]))
    assert any("end_date is before start_date" in m for m in e) and any("['SHOP'] not in catalog" in m for m in e)


def test_finish_required_once(fixture_catalog):
    assert any("exactly once" in m for m in errs(fixture_catalog, earn()))


def test_cap_alone_is_rejected(fixture_catalog):
    assert any("cap needs a points reward" in m for m in errs(fixture_catalog, ("add_cap", {"max_points": 300}), finish()))


def test_schema_errors_skip_semantics(fixture_catalog):
    e = errs(fixture_catalog, ("add_bonus_if", {"conditions": [], "reward": pts(100), "extra": 1}), finish())
    assert any("Additional properties" in m for m in e)


def test_real_tenant_codes(devintam1_catalog):
    ok = earn([c("activity", "h_location", "in", values=["HQ", "WEB"]), c("member", "tier_class", "==", "GOLD")])
    assert errs(devintam1_catalog, ok, finish(activity_types=["SPEND"])) == []
    e = errs(devintam1_catalog, earn([c("activity", "h_location", "in", values=["L1"])]), finish())
    assert any("['L1'] are not h_location codes" in m for m in e)
