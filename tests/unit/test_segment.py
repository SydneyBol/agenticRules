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


def test_finish_sent_back_when_offer_main_names_members_without_a_segment():
    """H_SG2: Offer Main's segment_prompt named members, the segment canvas stayed empty, Select Members got a 422."""
    from types import SimpleNamespace

    from offer_author.core.run.policy import finish_blockers

    def run(prompt, segment):
        canvases = SimpleNamespace(pending=list, last={"segment": segment} if segment else {})
        return SimpleNamespace(canvases=canvases, offer_main=SimpleNamespace(value={"segment_prompt": prompt}))

    parts = ("offer_main", "segment", "rules")
    codes = lambda r, x=None: [p.code for p in finish_blockers(r, parts, x or {})]
    assert codes(run("members who joined in the last 90 days", None)) == ["segment_prompt_without_segment"]
    assert codes(run("", None)) == []  # every member: no segment needed
    assert codes(run("Gold members", SimpleNamespace(built=True)), {"segment_name": "Gold"}) == []
    assert codes(run("Gold members", None), {"blocking_questions": ["Which Gold?"]}) == []  # asking instead


def test_member_attribute_level_must_list_its_attribute():
    """S12: member_attribute ATTRIBUTE_TYPE MP_S naming date_of_birth (Program-Level); the screen dropped it."""
    from offer_author.core.canvas.checks.screen_fields import _level_form
    from offer_author.core.tenant.catalog import Catalog
    from tests.paths import FIXTURE

    cat = Catalog.from_json(FIXTURE)
    form = lambda level: _level_form("member_attribute", {"ATTRIBUTE_TYPE": level, "ATTRIBUTE": "date_of_birth"}, cat)
    assert form("MP_S") == ("attribute_not_of_level", {"given": "MP_S", "owner": "MA_P"})
    assert form("MA_P") is None
    assert form("MA_O") is None  # an offer counter: counters are named by the rules


def test_rule_condition_reading_bit_calendar_date_in_the_filter_is_reported():
    """X01: if_generic_adaptive on h_bit_date CALENDAR_DATE in ACTIVITY_CRITERIA compiles with bitHeader, which fails."""
    from offer_author.core.canvas.checks.screen_fields import _filter_problems

    cond = {
        "type": "if_generic_adaptive",
        "id": "c1",
        "fields": {"attribute": "h_bit_date", "datetime_operator": "CALENDAR_DATE"},
    }
    filt = {
        "type": "activity_criteria",
        "id": "c2",
        "fields": {"attribute": "h_bit_date", "datetime_operator": "CALENDAR_DATE"},
    }
    tree = lambda c: {"type": "activity", "id": "a", "inputs": {"ACTIVITY_CRITERIA": [c]}}
    assert [p.code for p in _filter_problems(tree(cond))] == ["bit_calendar_date_in_filter"]
    assert _filter_problems(tree(filt)) == []  # the filter's own condition compiles
    rule = {
        "type": "activity",
        "id": "a",
        "inputs": {"RULES": [{"type": "rule", "id": "r", "inputs": {"CONDITION": [cond]}}]},
    }
    assert _filter_problems(rule) == []  # in a rule it compiles


def test_rule_without_a_tag_of_its_own_is_reported():
    """X30: an empty IDENTIFIER is saved as the screen's default "tag"; two rules sharing a tag are reported too."""
    from offer_author.core.canvas.checks.screen_fields import _tag_problems

    rule = lambda i, tag=None: {"type": "rule", "id": i, "fields": {"IDENTIFIER": tag} if tag is not None else {}}
    tree = lambda *rules: {"type": "activity", "id": "a", "inputs": {"RULES": list(rules)}}
    assert [p.code for p in _tag_problems(tree(rule("r1")))] == ["rule_without_tag"]
    assert [p.code for p in _tag_problems(tree(rule("r1", "tag")))] == ["rule_without_tag"]
    assert [p.code for p in _tag_problems(tree(rule("r1", "Birthday 2x"), rule("r2", "birthday 2x")))] == [
        "rule_tag_repeated"
    ]
    assert _tag_problems(tree(rule("r1", "Birthday 2x"), rule("r2", "Once a year"))) == []
