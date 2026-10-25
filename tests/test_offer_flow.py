"""One Offer Main prompt -> metadata (Offer Main), segment (Select Members) and rules (Offer Rules), each through the
stock Pulsar endpoint that screen already calls. Scripted model; the segment XML is compared to segments the stock UI
saved on devintam1."""
import base64
import json
import os
import re
import xml.etree.ElementTree as ET

from conftest import ROOT, c, finish, pts
from offer_author.llm import ScriptedLLM
from offer_author.planner import validate_plan
from offer_author.segment import segment_xml, validate_segment
from offer_author.service import create_app

MASTER = "For Gold members: 2x points on spends at the Downtown store until 2026-12-31, at most 5 times per member."
PLAN = ("plan_offer", {
    "metadata": {"offer_name": "Gold Downtown Double Points", "subtitle": "2x at Downtown", "offer_description": "Gold members earn double points at Downtown.",
                 "terms": ["Gold members only", "Up to 5 times per member"], "start_date": "", "end_date": "2026-12-31",
                 "max_usage_per_user": 5, "frequency": "total", "num_of_clients": 0, "offer_frequency_type": ""},
    "segment": {"prompt": "Members whose tier is Gold.", "name": "Gold members", "description": "Members in the Gold tier."},
    "rules": {"prompt": "2x points on spends at the Downtown store."},
    "clauses": [{"text": "For Gold members", "parts": ["segment"], "why": "who the whole offer is for"},
                {"text": "2x points on spends at the Downtown store", "parts": ["rules"], "why": "activity and reward"},
                {"text": "until 2026-12-31", "parts": ["metadata"], "why": "offer end date"},
                {"text": "at most 5 times per member", "parts": ["metadata"], "why": "per-member limit"}],
    "assumptions": [], "blocking_questions": []})
SEG = ("build_segment", {"name": "Gold members", "description": "Members in the Gold tier.",
                         "conditions": [{"level": "MA_P", "attribute": "tier", "tier_class": "", "op": "in", "value": "",
                                         "values": ["GOLD"], "value2": "", "date_part": ""}],
                         "or_groups": [], "assumptions": [], "blocking_questions": [], "unsupported": []})
EARN = ("add_earn", {"measure": "h_bit_amount", "factor_kind": "multiplier", "factor": 2, "or_groups": [],
                     "conditions": [c("activity", "h_location", "in", values=["L1"])]})


def cond(level, attribute, op, value="", values=(), tier_class="", value2="", date_part=""):
    return {"level": level, "attribute": attribute, "tier_class": tier_class, "op": op, "value": value, "values": list(values),
            "value2": value2, "date_part": date_part}


def seg(conditions=(), or_groups=()):
    return {"name": "n", "description": "d", "conditions": list(conditions), "or_groups": [list(g) for g in or_groups],
            "assumptions": [], "blocking_questions": [], "unsupported": []}


def test_one_prompt_drives_all_three_screens(fixture_catalog):
    llm = ScriptedLLM([[PLAN], [SEG], [EARN, finish(activity_types=["SPEND"])]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    # Offer Main popup -> metadata, auto-filled into the form
    md = cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"}).get_json()
    assert md["success"] and md["metadata"]["offer_name"] == "Gold Downtown Double Points"
    assert md["metadata"]["max_usage_per_user"] == 5 and md["metadata"]["option_value"] == "total"
    assert md["metadata"]["end_date"] == "2026-12-31T23:59:00" and md["metadata"]["term_and_condition"].startswith("<ul><li>")
    # offer saved -> prompt stored; later stages read their own part of the same plan (no second planner call)
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    assert cl.get("/ai/pulsar/9/prompt/offerrules/").get_json()["prompt"] == "2x points on spends at the Downtown store."
    assert cl.get("/ai/pulsar/9/prompt/offersegmentation/").get_json()["prompt"] == "Members whose tier is Gold."
    # Select Members popup -> segment XML; add-segment -> name/description from the plan
    s = cl.post("/ai/pulsar/segment/rules", json={"prompt": "Members whose tier is Gold.", "sponsor_id": 10, "id": 9,
                                                  "type": "reward", "rule_id": "r1"}).get_json()
    xml = base64.b64decode(s["xml"]).decode()
    assert s["rule_id"] == "r1" and 'type="targeting_holder"' in xml and 'attribute="tier"' in xml and 'value="GOLD"' in xml
    sm = cl.post("/ai/pulsar/offer/metadata", json={"prompt": "Members whose tier is Gold.", "category": "segment"}).get_json()
    assert sm["metadata"] == {"member_segment_name": "Gold members", "segment_description": "Members in the Gold tier."}
    # Offer Main's fire-and-forget rules call sends the WHOLE prompt: the rules part is built, with the plan as context
    r = cl.post("/ai/pulsar/offer/rules/?regenerate=true", json={"prompt": MASTER, "sponsor_id": 10, "id": 9, "type": "reward"}).get_json()
    assert r["xml"] and any("member segment 'Gold members'" in a for a in r["assumptions"])
    rules_msg = llm.calls[2][0]["content"]
    assert rules_msg.startswith("2x points on spends at the Downtown store.") and "Already handled outside the rules" in rules_msg
    assert "Members whose tier is Gold." in rules_msg and "end_date=2026-12-31" in rules_msg
    assert len(llm.calls) == 3  # plan, segment, rules: each part generated once


def test_segment_without_member_conditions_is_a_clear_error(fixture_catalog):
    empty = ("build_segment", dict(seg(), unsupported=[{"phrase": "spend over $50", "reason": "belongs to the offer rules"}]))
    cl = create_app(ScriptedLLM([[empty]]), lambda s, a, auth: fixture_catalog).test_client()
    r = cl.post("/ai/pulsar/segment/rules", json={"prompt": "spend over 50 dollars", "sponsor_id": 10, "id": 1, "rule_id": "x"})
    assert r.status_code == 422 and "belongs to the offer rules" in r.get_json()["error"]["message"]


def test_two_word_segment_prompt_is_built(fixture_catalog):
    # Sonnet's planner writes "Gold members"; a 3-word minimum rejected it and the offer paid every tier
    cl = create_app(ScriptedLLM([[SEG]]), lambda s, a, auth: fixture_catalog).test_client()
    s = cl.post("/ai/pulsar/segment/rules", json={"prompt": "Gold members", "sponsor_id": 10, "id": 1, "rule_id": "x"}).get_json()
    assert 'value="GOLD"' in base64.b64decode(s["xml"]).decode()
    r = cl.post("/ai/pulsar/segment/rules", json={"prompt": "Gold", "sponsor_id": 10, "id": 1, "rule_id": "x"})
    assert r.status_code == 400


def test_plan_validation():
    bad = json.loads(json.dumps(PLAN[1]))
    bad["metadata"]["end_date"], bad["rules"]["prompt"], bad["clauses"] = "31/12/2026", "", []
    errs = " ".join(validate_plan(bad))
    assert "YYYY-MM-DD" in errs and "rules.prompt is empty" in errs and "clauses" in errs
    assert validate_plan(PLAN[1]) == []


def test_segment_validation_uses_the_catalog(fixture_catalog):
    errs = " ".join(validate_segment(fixture_catalog, seg([cond("MA_T", "name", "==", "Gold", tier_class="Elite_Levels"),
                                                           cond("MA_P", "age", ">", "abc"), cond("MA_P", "shoe", "==", "x")])))
    assert "not tiers of Elite_Levels" in errs and "is NUMBER" in errs and "unknown member attribute 'shoe'" in errs
    assert "single group" in " ".join(validate_segment(fixture_catalog, seg(or_groups=[[cond("MA_P", "age", ">", "30")]])))


def blocks(xml):
    """Structure of a segment XML without ids: (type, mutation attrs, fields, child names) per block, in order."""
    root = ET.fromstring(xml)
    out = []
    for b in root.iter("{https://developers.google.com/blockly/xml}block"):
        m = b.find("{https://developers.google.com/blockly/xml}mutation")
        mut = {k: v for k, v in (m.attrib if m is not None else {}).items() if k not in ("visible_value",)}
        fields = {f.get("name"): f.text for f in b.findall("{https://developers.google.com/blockly/xml}field")}
        kids = [k.get("name") for k in b if k.tag.split("}")[1] in ("value", "statement")]
        out.append((b.get("type"), mut, fields, kids))
    return out


def saved(name):
    S = json.load(open(os.path.join(ROOT, "catalogs", "devintam1", "segments", "segments.json")))
    x = next(s for s in S if s["name"] == name)["xml"]
    return x if x.startswith("<") else base64.b64decode(x).decode()


def test_segment_xml_matches_what_the_stock_ui_saves(devintam1_catalog):
    cat = devintam1_catalog
    cat.attributes["member"].setdefault("age", type(next(iter(cat.attributes["member"].values())))("age", "Age", "NUMBER"))
    # tier name == Bronze (saved segment "tier")
    assert blocks(segment_xml(cat, seg([cond("MA_T", "name", "==", "Bronze", tier_class="Elite_Levels")]))) == blocks(saved("tier"))
    # age > 27 (saved segment "Members Over 27")
    assert blocks(segment_xml(cat, seg([cond("MA_P", "age", ">", "27")]))) == blocks(saved("Members Over 27"))


def test_or_always_has_two_operands(fixture_catalog):
    xml = segment_xml(fixture_catalog, seg(or_groups=[[cond("MA_P", "age", ">", "30")], [cond("MA_T", "name", "in", values=["Bronze", "Stone"], tier_class="Elite_Levels")],
                                                      [cond("MA_P", "gender", "in", values=["female"])]]))
    root = ET.fromstring(xml)
    for o in root.iter("{https://developers.google.com/blockly/xml}block"):
        if o.get("type") == "or":
            assert [s.get("name") for s in o.findall("{https://developers.google.com/blockly/xml}statement")] == ["OPERAND1", "OPERAND2"]
    assert xml.count('type="or"') == 2 and 'value="Bronze,Stone"' in xml
