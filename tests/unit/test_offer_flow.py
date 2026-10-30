"""One Offer Main prompt -> Offer Main, the member segment (Select Members) and the rules (Offer Rules) from ONE agent
run, each screen served through the stock Pulsar endpoint it already calls. Scripted model, fake compile service."""

import base64

from offer_author.api.service import create_app
from tests.fakes import ScriptedLLM
from tests.support import finish, golden, offer_main, saved_segment

MASTER = "For Bronze members: 2x points on spends at the Downtown store until 2026-12-31, at most 5 times per member."
MAIN = offer_main(
    offer_name="Bronze Downtown Double Points",
    subtitle="2x at Downtown",
    terms=["Bronze members only", "Up to 5 times"],
    end_date="2026-12-31",
    max_usage_per_user=5,
    frequency="total",
    segment_prompt="Bronze members",
    rules_prompt="2x points on spends at the Downtown store.",
)
SEG = ("compose", saved_segment("35_tier"))  # tier name == Bronze, as the stock UI saved it
EARN = ("compose", golden("S03_combined_weekend_gold_locations"))
FIN = finish(
    readback="Bronze members earn 2x at Downtown",
    segment_name="Bronze members",
    segment_description="Members in the Bronze tier.",
)


def client(llm, cat):
    return create_app(llm, lambda s, a, auth: cat).test_client()


def test_one_prompt_drives_all_three_screens(fixture_catalog, logged):
    llm = ScriptedLLM([[MAIN], [SEG, EARN], [FIN]])
    cl = client(llm, fixture_catalog)
    # Offer Main popup -> metadata as soon as the agent has set it, auto-filled into the form
    md = cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"}).get_json()
    assert md["success"] and md["metadata"]["offer_name"] == "Bronze Downtown Double Points"
    assert md["metadata"]["max_usage_per_user"] == 5 and md["metadata"]["option_value"] == "total"
    assert md["metadata"]["end_date"] == "2026-12-31T23:59:00" and md["metadata"]["term_and_condition"].startswith(
        "<ul><li>"
    )
    # offer saved -> prompt stored; later stages read the prompts the same run wrote
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    assert (
        cl.get("/ai/pulsar/9/prompt/offerrules/").get_json()["prompt"] == "2x points on spends at the Downtown store."
    )
    assert cl.get("/ai/pulsar/9/prompt/offersegmentation/").get_json()["prompt"] == "Bronze members"
    # Select Members popup -> the run's segment XML; add-segment -> its name and description
    s = cl.post(
        "/ai/pulsar/segment/rules",
        json={"prompt": "Bronze members", "sponsor_id": 10, "id": 9, "type": "reward", "rule_id": "r1"},
    ).get_json()
    xml = base64.b64decode(s["xml"]).decode()
    assert s["rule_id"] == "r1" and 'type="targeting_holder"' in xml and ">Bronze<" in xml
    sm = cl.post("/ai/pulsar/offer/metadata", json={"prompt": "Bronze members", "category": "segment"}).get_json()
    assert sm["metadata"] == {
        "member_segment_name": "Bronze members",
        "segment_description": "Members in the Bronze tier.",
    }
    # Offer Main's fire-and-forget rules call (the WHOLE prompt) and the rules screen get the same run's rules
    r = cl.post(
        "/ai/pulsar/offer/rules/?regenerate=true", json={"prompt": MASTER, "sponsor_id": 10, "id": 9, "type": "reward"}
    ).get_json()
    assert r["xml"] and any("member segment 'Bronze members'" in a for a in logged("rules", 9)["assumptions"])
    again = cl.post(
        "/ai/pulsar/offer/rules/?regenerate=false",
        json={"prompt": "2x points on spends at the Downtown store.", "sponsor_id": 10, "id": 9, "type": "reward"},
    ).get_json()
    assert again["xml"] == r["xml"]
    assert len(llm.calls) == 3  # one run: every part built once
    intro = llm.calls[0][0]["content"][0]["text"]
    assert (
        intro.startswith(MASTER) and "Build: Offer Main" in intro and "member segment" in intro and "the rules" in intro
    )


def test_an_edited_rules_prompt_is_built_alone_told_what_the_offer_covers(fixture_catalog, monkeypatch):
    from offer_author.api import pulsar

    monkeypatch.setattr(pulsar.jobs, "RECENT_S", 0)
    llm = ScriptedLLM([[MAIN], [SEG, EARN], [FIN], [EARN], [finish()]])
    cl = client(llm, fixture_catalog)
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"})
    cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 9, "rule_id": "r1"})
    r = cl.post(
        "/ai/pulsar/offer/rules?regenerate=true",
        json={"prompt": "3x points at the Downtown store", "sponsor_id": 10, "id": 9, "type": "reward"},
    ).get_json()
    assert r["xml"]
    first = llm.calls[-1][0]["content"]  # the history is sent unedited: only the newest message is a cached block list
    intro = first if isinstance(first, str) else first[0]["text"]
    assert intro.startswith("3x points at the Downtown store") and "Build: the rules (rules canvas)." in intro
    assert "Already handled outside the rules" in intro and "end_date=2026-12-31" in intro and "Bronze tier" in intro


def test_segment_without_member_conditions_is_a_clear_error(fixture_catalog):
    turn = [finish(unsupported=[{"phrase": "spend over $50", "reason": "belongs to the offer rules"}])]
    r = client(ScriptedLLM([turn]), fixture_catalog).post(
        "/ai/pulsar/segment/rules", json={"prompt": "spend over 50 dollars", "sponsor_id": 10, "id": 1, "rule_id": "x"}
    )
    assert r.status_code == 422 and "belongs to the offer rules" in r.get_json()["error"]["message"]


def test_two_word_segment_prompt_is_built_alone(fixture_catalog):
    llm = ScriptedLLM([[SEG], [finish(segment_name="Bronze members")]])
    cl = client(llm, fixture_catalog)
    s = cl.post(
        "/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 1, "rule_id": "x"}
    ).get_json()
    assert ">Bronze<" in base64.b64decode(s["xml"]).decode() and s["rule_id"] == "x"
    assert "Build: the member segment" in llm.calls[0][0]["content"][0]["text"]
    assert cl.post("/ai/pulsar/segment/rules", json={"prompt": "Bronze", "sponsor_id": 10, "id": 1}).status_code == 400


def test_metadata_without_offer_main_says_why(fixture_catalog):
    turn = [finish(blocking_questions=["Which weekend do you mean?"])]
    r = client(ScriptedLLM([turn]), fixture_catalog).post(
        "/ai/pulsar/offer/metadata", json={"prompt": "Double points this weekend for everyone", "category": "offer"}
    )
    assert r.status_code == 422 and "Which weekend" in r.get_json()["error"]["message"]


def test_each_screen_gets_only_its_own_part(fixture_catalog, logged):
    """Metadata answers for the whole offer; the segment and rules answers are the XML only, and each logs only its
    own part's assumptions, questions and unsupported items (no Offer Main settings in the rules). A question about
    the segment leaves the rules a draft."""
    fin = finish(
        readback="Bronze members earn 2x at Downtown",
        segment_name="Bronze members",
        segment_description="Bronze tier.",
        assumptions=[
            {"part": "offer_main", "text": "No start date was stated."},
            {"part": "segment", "text": "Bronze is the tier class BRONZE."},
            {"part": "rules", "text": "'2x' means points = amount x 2."},
        ],
        blocking_questions=[{"part": "segment", "text": "Bronze tier or Bronze test tier?"}],
    )
    cl = client(ScriptedLLM([[MAIN], [SEG, EARN], [fin]]), fixture_catalog)
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": MASTER, "category": "offer"})
    cl.post("/ai/pulsar/9/prompt/offermain/", json={"prompt": MASTER, "offer_status": "planning"})
    r = cl.post(
        "/ai/pulsar/offer/rules/?regenerate=false", json={"prompt": MASTER, "sponsor_id": 10, "id": 9, "type": "reward"}
    ).get_json()
    n = logged("rules", 9)
    assert set(r) == {"xml", "raw", "rule_id"} and "amount x 2" not in r["raw"]
    assert n["status"] == "draft" and n["blocking_questions"] == [] and "offer_settings" not in n
    assert n["assumptions"][0] == "'2x' means points = amount x 2." and not any(
        "start date" in a or "tier class" in a for a in n["assumptions"]
    )
    s = cl.post(
        "/ai/pulsar/segment/rules", json={"prompt": "Bronze members", "sponsor_id": 10, "id": 9, "type": "reward"}
    ).get_json()
    n = logged("segment", 9)
    assert s["xml"] and n["status"] == "needs_input" and n["assumptions"] == ["Bronze is the tier class BRONZE."]
    assert n["blocking_questions"] == ["Bronze tier or Bronze test tier?"]


def test_untagged_items_belong_to_every_part():
    from offer_author.core.run import notes_by_part

    out = notes_by_part(
        {
            "assumptions": ["plain", {"part": "rules", "text": "r"}, {"part": "offer_main", "text": "o"}],
            "unsupported": [{"part": "segment", "phrase": "friends", "reason": "not own data"}],
        },
        ("segment", "rules"),
    )
    assert out["rules"]["assumptions"] == ["plain", "r", "o"] and out["segment"]["assumptions"] == ["plain", "o"]
    assert (
        out["segment"]["unsupported"] == [{"phrase": "friends", "reason": "not own data"}]
        and out["rules"]["unsupported"] == []
    )
