"""Gold intents for scenarios.yaml (what a correct LLM output should be), using the fixture catalog."""

CATALOG = {
    "account": "1", "tag": "BONUS",
    "privileges": {"501": {"id": "501", "usage_policy": "9"}},
}

SPEND = {"obj": "activity", "attr": "h_bit_type", "type": "LOOKUP", "op": "in", "values": [["SPEND", "Spend"]]}


def act(attr, op, v, typ="NUMBER", dim=None):
    c = {"obj": "activity", "attr": attr, "type": typ, "op": op, "rhs": {"num": v}}
    if dim:
        c["dim"] = dim
    return c


def lookup_in(obj, attr, codes):
    return {"obj": obj, "attr": attr, "type": "LOOKUP", "op": "in", "values": [[c, c] for c in codes]}


INTENTS = {
    "S01_tiered_exclusive": {"activity_filters": [SPEND], "primitives": [
        {"tiers": {"bands": [{"gt": 100, "le": 500, "multiplier": 2}, {"gt": 500, "multiplier": 3}]}}]},

    "S02_tiered_plus_cap": {"activity_filters": [SPEND], "cap": {"counter": "Points toward 300 cap", "max_points": 300},
                            "primitives": [{"tiers": {"bands": [{"lt": 1000, "rate": 0.10}, {"ge": 1000, "rate": 0.20}]}}]},

    "S03_combined_weekend_gold_locations": {"activity_filters": [SPEND], "primitives": [{"bonus_if": {"conditions": [
        {"obj": "member", "level": "MA_P", "attr": "tier", "type": "LOOKUP", "op": "==", "value": "GOLD"},
        act("h_bit_date", ">=", 6, "DATETIME", "DAY_OF_WEEK"),  # Joda: Sat=6, Sun=7
        lookup_in("activity", "h_location", ["L1", "L2"]),
        act("h_bit_amount", ">=", 50)], "reward": {"points": 100}}}]},

    "S04_happy_hour_or": {"activity_filters": [SPEND], "primitives": [{"earn": {"multiplier": 2, "conditions": [{"any": [
        [act("h_bit_date", "==", 5, "DATETIME", "DAY_OF_WEEK"), act("h_bit_date", ">=", 17, "DATETIME", "HOUR_OF_DAY"),
         act("h_bit_date", "<", 19, "DATETIME", "HOUR_OF_DAY")],
        [act("h_bit_date", "==", 7, "DATETIME", "DAY_OF_WEEK")]]}]}}]},

    "S05_category_per_item": {"activity_filters": [SPEND], "primitives": [
        {"line_item": {"tag": "Snacks per item", "filter": {"attr": "category", "values": [["SNACKS", "Snacks"]]}, "points_per_unit": 5}},
        {"line_item": {"tag": "Drinks 2x", "filter": {"attr": "category", "values": [["DRINKS", "Drinks"]]}, "multiplier": 2}}]},

    "S06_collect_all_categories": {"activity_filters": [SPEND], "primitives": [{"collect_all": {"items": [
        {"attr": "category", "values": [["SNACKS", "Snacks"]]}, {"attr": "category", "values": [["DRINKS", "Drinks"]]},
        {"attr": "category", "values": [["BAKERY", "Bakery"]]}], "reward": {"privilege": 501}}}]},

    "S07_every_5th_visit": {"activity_filters": [SPEND], "primitives": [{"nth_activity": {"n": 5, "repeat": True, "reward": {"privilege": 501}}}]},

    "S08_spend_milestone_once": {"activity_filters": [SPEND], "primitives": [{"milestone": {"threshold": 1000, "reward": {"points": 500}}}]},

    "S09_unique_locations": {"activity_filters": [SPEND], "primitives": [{"unique_count": {"dimension": "h_location", "n": 3, "reward": {"points": 300}}}]},

    "S10_unlock_then_earn": {"activity_filters": [lookup_in("activity", "h_bit_type", ["STAY", "BOOKING"])], "primitives": [{"unlock": {
        "trigger": [lookup_in("activity", "h_bit_type", ["STAY"])],
        "then": {"conditions": [lookup_in("activity", "h_bit_type", ["BOOKING"])], "multiplier": 2}}}]},

    "S12_birthday_window_once_per_year": {"activity_filters": [SPEND], "primitives": [
        {"occasion": {"date_attr": "date_of_birth", "window_days": 20, "reward": {"points": 500}}}]},

    "S13_enrolment_anniversary": {"activity_filters": [SPEND], "primitives": [
        {"occasion": {"date_attr": "enrollment_date", "window_days": 3, "reward": {"privilege": 501}}}]},

    "S14_simple_sanity": {"activity_filters": [SPEND], "primitives": [{"earn": {"rate_per_unit": 10}}]},
}
