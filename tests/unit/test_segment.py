"""The segment canvas's block checks: its conditions are checked as member conditions at their attr_level, against the
tenant catalog (attributes, codes, tier programme), and an OR needs both sides."""

import glob
import os

import pytest

from tests.support import SEGMENTS, new_run, said, saved_segment

# saved segments that are not clean, and why (2026-10-31)
KNOWN = {
    "22_Target_member_per_points": "'6_balance' is not in the tenant catalog",  # account 6 is gone from devintam1
    "50_Static_segment": "an OR needs a condition in both OPERAND1 and OPERAND2",  # saved with one operand
}


def seg(*conds):
    return {"canvas": "segment", "blocks": {"type": "targeting_holder", "inputs": {"CONDITIONS": list(conds)}}}


def cond(**fields):
    picked = fields.pop("picked", None)
    inputs = fields.pop("inputs", None)
    return (
        {"type": "targeting_condition", "fields": fields}
        | ({"picked": picked} if picked else {})
        | ({"inputs": inputs} if inputs else {})
    )


def problems(catalog, *conds):
    return said(new_run(catalog, None).compose(seg(*conds)))["problems"]


def test_the_saved_segments_compose_clean(devintam1_catalog):
    for f in sorted(glob.glob(os.path.join(SEGMENTS, "*.xml"))):
        name = os.path.basename(f)[:-4]
        got = problems(devintam1_catalog, *saved_segment(name)["blocks"]["inputs"]["CONDITIONS"])
        assert (len(got) == 1 and KNOWN[name] in got[0]) if name in KNOWN else got == [], (name, got)


AGE = {"attr_level": "MA_P", "attribute": "age"}
TIER = {"attr_level": "MA_T", "tierslevel": "Elite_Levels", "tiermainoption": "name", "operators": "=="}


@pytest.mark.parametrize(
    "c, problem",
    [
        (cond(attr_level="MA_P", attribute="gender", operators="==", value="male"), None),
        (cond(attr_level="MA_P", attribute="shoe_size", operators="==", value="9"), "'shoe_size' is not in the tenant"),
        (cond(attr_level="MA_P", attribute="tier_class", operators="==", value="Golden"), "values ['Golden']"),
        (cond(attr_level="MA_P", attribute="tier_class", operators="==", picked="GOLD"), "give one code in value"),
        (cond(**AGE), "'age' has no operator"),
        (cond(**AGE, operators=">="), "'age' compares with nothing"),
        (cond(**AGE, operators=">=", inputs={"RHS_INPUT": {"type": "static_value", "value": "25"}}), None),
        (cond(**AGE, operators="not_null"), None),
        (cond(attribute="age", operators="not_null"), "give attr_level"),
        (cond(**TIER, tierlevelnamevalue="Bronze"), None),
        (cond(**TIER, tierlevelnamevalue="Platinum"), "tier 'Platinum' is not in tier class 'Elite_Levels'"),
        (cond(**dict(TIER, tierslevel="Gold"), tierlevelnamevalue="Bronze"), "'Gold' is not a tier class"),
    ],
)
def test_a_segment_condition_is_checked_against_the_catalog(devintam1_catalog, c, problem):
    got = problems(devintam1_catalog, c)
    assert (len(got) == 1 and problem in got[0]) if problem else got == [], got


def test_an_or_needs_both_sides(devintam1_catalog):
    male = cond(attr_level="MA_P", attribute="gender", operators="==", value="male")
    one = {"type": "or", "inputs": {"OPERAND1": [male]}}
    assert problems(devintam1_catalog, one) == ["or#b2: an OR needs a condition in both OPERAND1 and OPERAND2"]
    assert problems(devintam1_catalog, {"type": "or", "inputs": {"OPERAND1": [male], "OPERAND2": [male]}}) == []


def test_a_rules_condition_without_operator_or_value_is_reported(fixture_catalog):
    """C12 (2026-10-30): an empty condition compiled to a bare read inside &&."""
    from offer_author.core.canvas import tree
    from offer_author.core.canvas.checks import screen_fields

    def got(**f):
        node = {"type": "if_generic_adaptive", "id": "b5", "OBJECT": "activity", "attribute": "h_bit_amount", **f}
        return [
            p for p in said(screen_fields.unfilled(tree.expand(node), fixture_catalog, {})) if "'h_bit_amount'" in p
        ]

    assert got() == ["if_generic_adaptive#b5: 'h_bit_amount' has no operator: give operators"]
    assert "compares with nothing" in got(operators=">")[0]
    assert got(operators=">", inputs={"RHS_INPUT": {"type": "static_value", "value": "100"}}) == []
