"""One Offer Main prompt -> Offer Main, the member segment (Select Members) and the rules (Offer Rules) from ONE agent
run, each screen served through the stock Pulsar endpoint it already calls. Scripted model, fake compile service."""
import base64

import pytest

from conftest import finish, golden, offer_main, saved_segment
from offer_author.ai.llm import ScriptedLLM
from offer_author.api.service import create_app

pytestmark = pytest.mark.usefixtures("fake_ui")
MASTER = "For Bronze members: 2x points on spends at the Downtown store until 2026-12-31, at most 5 times per member."
MAIN = offer_main(offer_name="Bronze Downtown Double Points", subtitle="2x at Downtown", terms=["Bronze members only", "Up to 5 times"],
                  end_date="2026-12-31", max_usage_per_user=5, frequency="total",
                  segment_prompt="Bronze members", rules_prompt="2x points on spends at the Downtown store.")
SEG = ("compile", saved_segment("35_tier"))  # tier name == Bronze, as the stock UI saved it
EARN = ("compile", golden("S03_combined_weekend_gold_locations"))
FIN = finish(readback="Bronze members earn 2x at Downtown", segment_name="Bronze members", segment_description="Members in the Bronze tier.")


def client(llm, cat):
    return create_app(llm, lambda s, a, auth: cat).test_client()


def test_one_prompt_drives_all_three_screens(fixture_catalog):
    llm = ScriptedLLM([[MAIN], [SEG, EARN], [FIN]])
    cl = client(llm, fixture_catalog)
    # Offer Main popup -> metadata as soon as the agent has set it, auto-filled into the form
    md = cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"}).get_json()
    assert md["success"] and md["metadata"]["offer_name"] == "Bronze Downtown Double Points"
    assert md["metadata"]["max_usage_per_user"] == 5 and md["metadata"]["option_value"] == "total"
    assert md["metadata"]["end_date"] == "2026-12-31T23:59:00" and md["metadata"]["term_and_condition"].startswith("<ul><li>")
    # offer saved -> prompt stored; later stages read the prompts the same run wrote
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    assert cl.get("/ai/pulsar/9/prompt/offerrules/").get_json()["prompt"] == "2x points on spends at the Downtown store."
    assert cl.get("/ai/pulsar/9/prompt/offersegmentation/").get_json()["prompt"] == "Bronze members"
    # Select Members popup -> the run's segment XML; add-segment -> its name and description
    s = cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 9,
                                                  "type": "reward", "rule_id": "r1"}).get_json()
    xml = base64.b64decode(s["xml"]).decode()
    assert s["rule_id"] == "r1" and 'type="targeting_holder"' in xml and ">Bronze<" in xml
    sm = cl.post("/ai/pulsar/offer/metadata", json={"prompt": "Bronze members", "category": "segment"}).get_json()
    assert sm["metadata"] == {"member_segment_name": "Bronze members", "segment_description": "Members in the Bronze tier."}
    # Offer Main's fire-and-forget rules call (the WHOLE prompt) and the rules screen get the same run's rules
    r = cl.post("/ai/pulsar/offer/rules/?regenerate=true", json={"prompt": MASTER, "sponsor_id": 10, "id": 9, "type": "reward"}).get_json()
    assert r["xml"] and any("member segment 'Bronze members'" in a for a in r["assumptions"])
    again = cl.post("/ai/pulsar/offer/rules/?regenerate=false", json={"prompt": "2x points on spends at the Downtown store.",
                                                                       "sponsor_id": 10, "id": 9, "type": "reward"}).get_json()
    assert again["xml"] == r["xml"]
    assert len(llm.calls) == 3  # one run: every part built once
    intro = llm.calls[0][0]["content"][0]["text"]
    assert intro.startswith(MASTER) and "Build: Offer Main" in intro and "member segment" in intro and "the rules" in intro


def test_an_edited_rules_prompt_is_built_alone_told_what_the_offer_covers(fixture_catalog, monkeypatch):
    from offer_author.api import pulsar
    monkeypatch.setattr(pulsar, "RECENT_S", 0)
    llm = ScriptedLLM([[MAIN], [SEG, EARN], [FIN], [EARN], [finish()]])
    cl = client(llm, fixture_catalog)
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"})
    cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 9, "rule_id": "r1"})
    r = cl.post("/ai/pulsar/offer/rules?regenerate=true", json={"prompt": "3x points at the Downtown store", "sponsor_id": 10,
                                                                 "id": 9, "type": "reward"}).get_json()
    assert r["result"] == "Generated"
    intro = llm.calls[-1][0]["content"][0]["text"]
    assert intro.startswith("3x points at the Downtown store") and "Build: the rules (rules canvas)." in intro
    assert "Already handled outside the rules" in intro and "end_date=2026-12-31" in intro and "Bronze tier" in intro


def test_segment_without_member_conditions_is_a_clear_error(fixture_catalog):
    turn = [finish(unsupported=[{"phrase": "spend over $50", "reason": "belongs to the offer rules"}])]
    r = client(ScriptedLLM([turn]), fixture_catalog).post(
        "/ai/pulsar/segment/rules", json={"prompt": "spend over 50 dollars", "sponsor_id": 10, "id": 1, "rule_id": "x"})
    assert r.status_code == 422 and "belongs to the offer rules" in r.get_json()["error"]["message"]


def test_two_word_segment_prompt_is_built_alone(fixture_catalog):
    llm = ScriptedLLM([[SEG], [finish(segment_name="Bronze members")]])
    cl = client(llm, fixture_catalog)
    s = cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 1, "rule_id": "x"}).get_json()
    assert ">Bronze<" in base64.b64decode(s["xml"]).decode() and s["name"] == "Bronze members"
    assert "Build: the member segment" in llm.calls[0][0]["content"][0]["text"]
    assert cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze", "sponsor_id": 10, "id": 1}).status_code == 400


def test_metadata_without_offer_main_says_why(fixture_catalog):
    turn = [finish(blocking_questions=["Which weekend do you mean?"])]
    r = client(ScriptedLLM([turn]), fixture_catalog).post(
        "/ai/pulsar/offer/metadata", json={"prompt": "Double points this weekend for everyone", "category": "offer"})
    assert r.status_code == 422 and "Which weekend" in r.get_json()["error"]["message"]
