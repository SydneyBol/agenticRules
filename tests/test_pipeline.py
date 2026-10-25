"""Pipeline behaviour with a scripted model: repair round, partial drafts, never-empty results, counters, settings."""
from conftest import c, finish, priv, pts
from offer_author.llm import LLMError, ScriptedLLM
from offer_author.pipeline import generate


def earn(conds=(), factor=2, kind="multiplier"):
    return ("add_earn", {"measure": "h_bit_amount", "factor_kind": kind, "factor": factor, "conditions": list(conds), "or_groups": []})


def test_repair_round_fixes_a_wrong_code(fixture_catalog):
    bad = [earn([c("activity", "h_location", "in", values=["Downtown"])]), finish()]
    good = [earn([c("activity", "h_location", "in", values=["L1"])]), finish(activity_types=["SPEND"])]
    llm = ScriptedLLM([bad, good])
    res = generate("2x at the downtown store", fixture_catalog, llm, offer_id=77)
    assert res["status"] == "draft" and res["diagnostics"]["repairs"] == 1
    assert any("['Downtown'] are not h_location codes" in m for m in res["diagnostics"]["issues_first_pass"])
    # the repair turn sends the assistant turn back plus one error tool_result per tool_use
    repair = llm.calls[1][-1]["content"]
    assert len(repair) == 2 and all(r["type"] == "tool_result" and r["is_error"] for r in repair)
    assert 'value="L1"' in res["rules"]["xml"] and 'visible_value="Downtown Store"' in res["rules"]["xml"]


def test_still_invalid_after_repair_is_dropped_not_compiled(fixture_catalog):
    turn = [earn([c("activity", "h_location", "in", values=["L9"])]), ("add_bonus_if", {"conditions": [], "reward": pts(50)}), finish()]
    res = generate("...", fixture_catalog, ScriptedLLM([turn, turn]))
    assert res["status"] == "draft" and res["rules"]["rule_count"] == 1  # the valid bonus survives
    assert any(u["phrase"] == "earn" and "L9" in u["reason"] for u in res["unsupported"])


def test_no_tool_calls_never_returns_empty(fixture_catalog):
    res = generate("hello", fixture_catalog, ScriptedLLM([[], []]))
    assert res["status"] == "needs_input" and res["blocking_questions"] and res["rules"] is None


def test_model_outage_is_an_error_with_a_message(fixture_catalog):
    res = generate("...", fixture_catalog, ScriptedLLM([LLMError("ThrottlingException")]))
    assert res["status"] == "error" and "try again" in res["message"]


def test_blocking_question_still_returns_the_draft(fixture_catalog):
    turn = [earn([c("activity", "h_location", "in", values=["AP1", "AP2"])]),
            finish(blocking_questions=["Which airport store, AP1 or AP2?"], assumptions=["2x means total points"])]
    res = generate("2x at the airport store", fixture_catalog, ScriptedLLM([turn]))
    assert res["status"] == "needs_input" and res["rules"]
    assert res["assumptions"] == ["2x means total points", "No activity type was named, so only Spend activities count."]


def test_streak_reported_unsupported_rest_compiled(fixture_catalog):
    turn = [("add_streak", {"period": "MONTH", "n": 3, "reward": pts(1000)}), earn(), finish()]
    res = generate("...", fixture_catalog, ScriptedLLM([turn]))
    assert res["status"] == "draft" and res["rules"]["rule_count"] == 1
    assert res["unsupported"][0]["phrase"] == "streak"


def test_counters_become_mto_payloads_for_this_offer(fixture_catalog):
    turn = [("add_unique_count", {"dimension": "h_location", "n": 3, "reward": pts(300)}), finish()]
    res = generate("3 different stores", fixture_catalog, ScriptedLLM([turn]), offer_id=4242)
    m = {x["name"]: x for x in res["rules"]["counters"]}
    assert res["rules"]["expected_offer_id"] == 4242 and all(x["offer"] == 4242 and x["level"] == "OFFER" for x in m.values())
    assert m["Visits per store"]["entity_attribute_map"] == {"Activity": ["h_location"]}
    assert "target_type" not in m["300 points given"]  # TEXT counters never carry target_type


def test_cap_on_plain_earn_puts_remainder_rule_first(fixture_catalog):
    turn = [earn(factor=0.1, kind="rate_per_unit"), ("add_cap", {"max_points": 300}), finish()]
    res = generate("10% back, max 300", fixture_catalog, ScriptedLLM([turn]))
    xml = res["rules"]["xml"]
    assert res["rules"]["rule_count"] == 2 and xml.index("Earn 0.1 points per spend up to cap") < xml.index(">Earn 0.1 points per spend<")


def test_offer_settings_and_reward_account(devintam1_catalog):
    turn = [("add_bonus_if", {"conditions": [c("member", "tier_class", "==", "GOLD")], "reward": priv("64")}),
            finish(start_date="2026-11-01", end_date="2026-11-30", max_usage_per_user=1, frequency="total", activity_types=["SPEND"])]
    res = generate("Gold members get a voucher in November, once", devintam1_catalog, ScriptedLLM([turn]))
    assert res["offer_settings"] == {"start_date": "2026-11-01", "end_date": "2026-11-30", "max_usage_per_user": 1, "frequency": "total"}
    xml = res["rules"]["xml"]
    assert '<field name="SELECTED_PRIVILEGE">64</field>' in xml and '<field name="PRIVILEGE_USAGEPOLICY">67</field>' in xml
    assert 'attribute="tier_class"' in xml and 'visible_value="Spend"' in xml


def test_catalog_types_are_used_lov_location(devintam1_catalog):
    turn = [earn([c("activity", "h_location", "in", values=["HQ", "WEB"])]), finish()]
    xml = generate("...", devintam1_catalog, ScriptedLLM([turn]))["rules"]["xml"]
    assert 'attribute="h_location"' in xml and 'type="LOV"' in xml and 'visible_value="Chennai,Elite Group Online"' in xml


def test_points_go_to_the_tag_code(devintam1_catalog):
    xml = generate("...", devintam1_catalog, ScriptedLLM([[earn(), finish()]]))["rules"]["xml"]
    assert '<field name="LOYALTY_ACCOUNT">12</field>' in xml and '<field name="REWARD_TAGS">BASE</field>' in xml


def test_bonus_if_or_groups_become_one_or_block(fixture_catalog):
    turn = [("add_bonus_if", {"conditions": [c("activity", "h_bit_type", "==", "SPEND")], "reward": pts(100),
                              "or_groups": [[c("activity", "h_location", "==", "AP1")],
                                            [c("activity", "h_bit_amount", ">", "500")]]}), finish(activity_types=["SPEND"])]
    res = generate("100 points at the airport store or over $500", fixture_catalog, ScriptedLLM([turn]))
    assert res["status"] == "draft" and res["rules"]["rule_count"] == 1
    assert res["rules"]["xml"].count('<block type="or"') == 1 and not res["rules"]["grammar_problems"]


def test_date_literal_compiles_to_static_date_and_bad_dates_are_repaired(fixture_catalog):
    bad = [("add_bonus_if", {"conditions": [c("member", "date_of_birth", "<", "")], "reward": pts(50)}), finish()]
    good = [("add_bonus_if", {"conditions": [c("activity", "h_bit_date", "<", "2026-12-31")], "reward": pts(100)}), finish()]
    res = generate("100 points before 2026-12-31", fixture_catalog, ScriptedLLM([bad, good]))
    assert res["status"] == "draft" and res["diagnostics"]["repairs"] == 1
    assert any("YYYY-MM-DD" in m for m in res["diagnostics"]["issues_first_pass"])
    xml = res["rules"]["xml"]
    assert '<block type="static_date"' in xml and "2026-12-31" in xml and "DATE" in xml


def rules_of(res):
    import re
    return re.findall(r'<field name="IDENTIFIER">([^<]*)</field>', res["rules"]["xml"])


def test_counter_templates_keep_their_conditions(fixture_catalog):
    weekend = c("activity", "h_bit_date", "in", values=["6", "7"], dim="DAY_OF_WEEK")
    tier = c("member", "tier", "==", "SILVER")
    stores = c("activity", "h_location", "in", values=["L1", "L2", "L3"])
    for call in [("add_nth_activity", {"n": 2, "repeat": True, "conditions": [weekend], "reward": pts(150)}),
                 ("add_milestone", {"measure": "h_bit_amount", "threshold": 500, "conditions": [tier], "reward": priv("501")}),
                 ("add_unique_count", {"dimension": "h_location", "n": 2, "conditions": [stores], "reward": pts(250)}),
                 ("add_occasion", {"date_attribute": "enrollment_date", "window_days": 7,
                                   "conditions": [c("activity", "h_location", "==", "L2")], "reward": priv("501")}),
                 ("add_tiers", {"measure": "h_bit_amount", "conditions": [tier], "bands": [
                     {"lower": 0, "lower_inclusive": True, "has_upper": False, "upper": 0, "upper_inclusive": False,
                      "factor_kind": "multiplier", "factor": 2}]})]:
        res = generate("...", fixture_catalog, ScriptedLLM([[call, finish(activity_types=["SPEND"])]]))
        assert res["status"] == "draft", (call[0], res["diagnostics"])
        rules = res["intent"]["primitives"][0]
        (name, params), = rules.items()
        assert params["conditions"], name
        # every compiled rule carries the restriction (counting rules and reward rules alike)
        cond_attr = {"add_nth_activity": "DAY_OF_WEEK", "add_milestone": 'attribute="tier"',
                     "add_unique_count": 'value="L1,L2,L3"', "add_occasion": 'value="L2"', "add_tiers": 'attribute="tier"'}[call[0]]
        assert cond_attr in res["rules"]["xml"] and not res["rules"]["grammar_problems"], call[0]


def test_cap_applies_to_line_item_unlock_and_point_bonus(fixture_catalog):
    li = ("add_line_item", {"product_attribute": "category", "values": ["BAKERY"], "factor_kind": "points_per_unit", "factor": 10})
    un = ("add_unlock", {"trigger": [c("activity", "h_bit_type", "==", "STAY")],
                         "then_conditions": [c("activity", "h_bit_type", "==", "BOOKING")], "multiplier": 2})
    bo = ("add_bonus_if", {"conditions": [c("activity", "h_bit_amount", ">", "50")], "reward": pts(100)})
    for call in (li, un, bo):
        res = generate("...", fixture_catalog, ScriptedLLM([[call, ("add_cap", {"max_points": 100}), finish(activity_types=["SPEND"])]]))
        assert res["status"] == "draft" and not res["diagnostics"]["issues_final"], call[0]
        assert any(r.endswith("up to cap") for r in rules_of(res)), (call[0], rules_of(res))
    assert '<block type="rule_line"' in generate("...", fixture_catalog, ScriptedLLM([[li, ("add_cap", {"max_points": 100}),
                                                                                    finish()]]))["rules"]["xml"]


def test_product_condition_in_header_rule_is_repaired_to_line_item(fixture_catalog):
    bad = [("add_earn", {"measure": "l_quantity", "factor_kind": "rate_per_unit", "factor": 10, "or_groups": [],
                         "conditions": [c("product", "category", "==", "BAKERY")]}), finish()]
    res = generate("10 points per bakery item", fixture_catalog, ScriptedLLM([bad, bad]))
    msgs = " ".join(res["diagnostics"]["issues_first_pass"])
    assert "add_line_item" in msgs and res["rules"] is None


def test_offer_dates_stay_in_offer_settings_and_spend_is_default(fixture_catalog):
    # dates are Offer Main metadata: the engine checks them before the rules (OfferValidator.checkBitEligibility)
    turn = [("add_bonus_if", {"conditions": [], "reward": pts(100)}), finish(end_date="2026-12-31")]
    res = generate("100 points per spend until 2026-12-31", fixture_catalog, ScriptedLLM([turn]))
    f = res["intent"]["activity_filters"]
    assert len(f) == 1 and f[0]["attr"] == "h_bit_type" and f[0]["values"] == [["SPEND", "Spend"]]
    assert '<block type="static_date"' not in res["rules"]["xml"] and res["offer_settings"]["end_date"] == "2026-12-31"


def filter_and_rules(xml):
    """(conditions in the activity Filter, conditions of each rule) as attribute names, from the compiled XML."""
    import re
    crit = re.search(r'<statement name="ACTIVITY_CRITERIA">(.*?)</statement><statement name="RULES">', xml).group(1)
    rules = [re.search(r'^(.*?)<statement name="ACTION">', r).group(1) for r in xml.split('<block type="rule" ')[1:]]
    attrs = lambda s: re.findall(r'<mutation [^>]*?attribute="([^"]+)"', s)
    return attrs(crit), [attrs(r) for r in rules]


def test_global_conditions_go_in_the_filter_once(fixture_catalog):
    # "Gold members whose total spend reaches $500 get a voucher": the model repeats the activity type and the tier on
    # the counting rule and the reward rule; the Filter must hold each once and no rule may repeat them
    gold = c("member", "tier", "==", "GOLD")
    spend = c("activity", "h_bit_type", "==", "SPEND")
    call = ("add_milestone", {"measure": "h_bit_amount", "threshold": 500, "conditions": [spend, gold], "reward": priv("501")})
    res = generate("...", fixture_catalog, ScriptedLLM([[call, finish(activity_types=["SPEND"])]]))
    assert res["status"] == "draft" and not res["rules"]["grammar_problems"], res["diagnostics"]
    filt, rules = filter_and_rules(res["rules"]["xml"])
    assert filt == ["h_bit_type", "tier"]
    assert len(rules) >= 2 and all("tier" not in r and "h_bit_type" not in r for r in rules), rules


def test_condition_only_some_rules_need_stays_in_those_rules(fixture_catalog):
    base = ("add_earn", {"measure": "h_bit_amount", "conditions": [], "or_groups": [], "factor_kind": "rate_per_unit", "factor": 1})
    weekend = ("add_bonus_if", {"conditions": [c("activity", "h_bit_date", "in", values=["6", "7"], dim="DAY_OF_WEEK")],
                                "or_groups": [], "reward": pts(100)})
    res = generate("...", fixture_catalog, ScriptedLLM([[base, weekend, finish(activity_types=["SPEND"])]]))
    filt, rules = filter_and_rules(res["rules"]["xml"])
    assert filt == ["h_bit_type"] and ["h_bit_date"] in rules


def test_or_groups_and_counters_are_never_hoisted(fixture_catalog):
    call = ("add_bonus_if", {"conditions": [], "or_groups": [[c("activity", "h_location", "==", "L1")],
                                                            [c("activity", "h_bit_amount", ">", "500")]], "reward": pts(100)})
    res = generate("...", fixture_catalog, ScriptedLLM([[call, finish(activity_types=["SPEND"])]]))
    filt, _ = filter_and_rules(res["rules"]["xml"])
    assert filt == ["h_bit_type"] and '<block type="or"' in res["rules"]["xml"]


def test_hoisted_lookup_equality_is_written_as_a_one_value_list(devintam1_catalog):
    # the stock Filter drops a one-value == on a LOOKUP it can't find in its dropdown ("tier_class is empty");
    # its in-list keeps the code and label as written
    silver = c("member", "tier_class", "==", "Silver")
    call = ("add_nth_activity", {"n": 3, "repeat": True, "conditions": [silver], "reward": priv("37")})
    res = generate("...", devintam1_catalog, ScriptedLLM([[call, finish(activity_types=["SPEND"])]]))
    xml = res["rules"]["xml"]
    filt, rules = filter_and_rules(xml)
    assert filt == ["h_bit_type", "tier_class"] and all("tier_class" not in r for r in rules)
    assert 'attribute="tier_class" operators="in"' in xml and 'value="Silver" visible_value="Silver"' in xml


def test_counter_and_rule_names_read_like_variables(fixture_catalog):
    """MA_O counters and rule tags name what they hold / do in catalog words: no raw codes, no '.', unique, <= 50."""
    turn = [("add_unique_count", {"dimension": "h_location", "n": 3, "reward": pts(300)}),
            ("add_milestone", {"measure": "h_bit_amount", "threshold": 1000, "reward": pts(300), "conditions": []}), finish()]
    res = generate("3 different stores, and 300 points at 1000 total spend", fixture_catalog, ScriptedLLM([turn]))
    names = [x["name"] for x in res["rules"]["counters"]]
    assert names == ["Visits per store", "Different stores visited", "300 points given", "Total spend", "300 points given 2"]
    assert all(len(x) <= 50 and "." not in x and "h_" not in x for x in names)
    tags = rules_of(res)
    assert len(set(tags)) == len(tags) and not any("h_" in t for t in tags)


def test_default_model_is_sonnet_and_env_overrides(monkeypatch):
    from offer_author.llm import BedrockLLM
    monkeypatch.delenv("OFFER_AUTHOR_MODEL", raising=False)
    assert BedrockLLM(client=object()).model == "us.anthropic.claude-sonnet-5-5"
    monkeypatch.setenv("OFFER_AUTHOR_MODEL", "us.anthropic.claude-opus-5-5")
    assert BedrockLLM(client=object()).model == "us.anthropic.claude-opus-5-5"
