"""The Pulsar contract the stock gravty-ui calls (generate-ai.component.ts, offer-rules.component.ts, offer-main.service.ts)."""

import base64
import re
import threading
import xml.etree.ElementTree as ET

import pytest

from offer_author.api import pulsar
from offer_author.api.service import create_app
from tests.fakes import ScriptedLLM
from tests.support import finish, golden, offer_main

EARN = ("compose", golden("S03_combined_weekend_gold_locations"))  # 2x at the Downtown and Mall stores (L1, L2)
MAIN = offer_main(start_date="2026-11-01")  # Offer Main settings: answered by the metadata API, never by the rules
FIN = finish(readback="2x points at the Downtown Store – weekends only", assumptions=["'2x' means total points"])
RUN = [[MAIN, EARN], [FIN]]  # the model's turns for one rules generation
NS = "{https://developers.google.com/blockly/xml}"


class SlowLLM(ScriptedLLM):
    """Blocks inside turn() until released, to exercise the Generating / re-post loop."""

    def __init__(self, turns):
        super().__init__(turns)
        self.release = threading.Event()

    def turn(self, system, messages, tools=None, on_tool=None):
        self.release.wait(10)
        return super().turn(system, messages, tools, on_tool)


def client_for(llm, fixture_catalog):
    return create_app(llm, lambda sponsor, account, auth: fixture_catalog).test_client()


def rules_req(cl, regenerate="true", slash="", **kw):
    body = {"prompt": "2x points at the downtown store", "sponsor_id": 10, "id": 777, "type": "reward"}
    body.update(kw)
    return cl.post(
        f"/ai/pulsar/offer/rules{slash}?regenerate={regenerate}", json=body, headers={"Authorization": "JWT x"}
    )


def atob(b64):
    return base64.b64decode(b64).decode("latin-1")  # what the browser's atob gives the UI


def stock_upload_filter(s):
    return re.sub(r"[^\x20-\x7E]", "", s)  # rules-upload-model.component.ts loadBlocklyFileUpload


def test_popup_contract_both_url_forms(fixture_catalog, logged):
    """Pulsar's answer exactly: {xml (base64), raw, rule_id}; the rest is in the log."""
    cl = client_for(ScriptedLLM(RUN * 2), fixture_catalog)
    for slash, prompt in (("", "2x points at the downtown store"), ("/", "2x points at the downtown store please")):
        r = rules_req(cl, slash=slash, prompt=prompt)
        assert r.status_code == 200, r.get_json()
        j = r.get_json()
        assert set(j) == {"xml", "raw", "rule_id"} and j["rule_id"] == "777" and atob(j["xml"]) == j["raw"]
        assert logged("rules", 777)["status"] == "draft"


def test_xml_survives_the_stock_upload_filter_and_the_notes_are_logged(fixture_catalog, logged):
    j = rules_req(client_for(ScriptedLLM(RUN), fixture_catalog)).get_json()
    raw = atob(j["xml"])
    assert raw == stock_upload_filter(raw)  # nothing for the UI's filter to delete
    offer = ET.fromstring(raw).find(NS + "block")
    assert offer.get("type") == "offer_generic" and offer.find(NS + "comment") is None  # the XML is the rules only
    assert 'value="L1,L2"' in raw and 'visible_value="Downtown Store,Mall Store"' in raw
    notes = logged("rules", 777)
    assert notes["readback"].startswith("2x points at the Downtown Store – weekends")
    assert notes["assumptions"] == ["'2x' means total points"] and "start_date" not in str(notes)


def test_generating_then_result_on_repost(fixture_catalog, monkeypatch):
    monkeypatch.setattr(pulsar.jobs, "HOLD_S", 0.2)
    llm = SlowLLM(RUN)
    cl = client_for(llm, fixture_catalog)
    assert rules_req(cl).get_json() == {"result": "Generating"}
    assert rules_req(cl).get_json() == {"result": "Generating"}  # re-post joins the same job, no second model call
    llm.release.set()
    for _ in range(50):
        j = rules_req(cl).get_json()
        if j.get("result") != "Generating":
            break
    assert j["xml"] and len(llm.calls) == len(RUN)


def test_rules_screen_load_gets_the_stored_xml_and_prompt(fixture_catalog):
    """Offer Main fires regenerate=true at creation; the rules screen later reads the prompt and asks regenerate=false."""
    llm = ScriptedLLM(RUN)
    cl = client_for(llm, fixture_catalog)
    first = rules_req(cl).get_json()
    assert cl.get("/ai/pulsar/777/prompt/offerrules/").get_json()["prompt"] == "2x points at the downtown store"
    again = rules_req(cl, regenerate="false", slash="/").get_json()
    assert again["xml"] == first["xml"] and len(llm.calls) == len(RUN)


def test_regenerate_false_without_anything_returns_empty_xml(fixture_catalog):
    j = rules_req(client_for(ScriptedLLM([]), fixture_catalog), regenerate="false", prompt="", id=999).get_json()
    assert j == {"xml": ""}  # the screen then initialises normally (no atob(undefined))


def test_no_draft_is_an_error_message_the_popup_shows(fixture_catalog):
    turn = [
        finish(
            unsupported=[
                {
                    "phrase": "friends' spend",
                    "reason": "rules only see the member's own transactions",
                    "alternative": "reward the referrer when the friend enrols",
                }
            ]
        )
    ]
    r = rules_req(client_for(ScriptedLLM([turn]), fixture_catalog))
    assert r.status_code == 422
    msg = r.get_json()["error"]["message"]
    assert "Can't build 'friends' spend'" in msg and "Try: reward the referrer" in msg


def test_model_outage_is_503_with_message(fixture_catalog):
    from offer_author.ai.model import LLMError

    r = rules_req(client_for(ScriptedLLM([LLMError("ThrottlingException")]), fixture_catalog))
    assert r.status_code == 503 and "try again" in r.get_json()["error"]["message"]


@pytest.mark.parametrize("typ", ["deal", "enrollment", None])
def test_reward_only(fixture_catalog, typ):
    r = rules_req(client_for(ScriptedLLM([]), fixture_catalog), type=typ)
    assert r.status_code == 400 and "reward offers only" in r.get_json()["error"]["message"]


def test_prompt_store_roundtrip(fixture_catalog):
    cl = client_for(ScriptedLLM([]), fixture_catalog)
    cl.post("/ai/pulsar/55/prompt/offermain/", json={"prompt": "Gold members 2x", "offer_status": "planning"})
    assert cl.get("/ai/pulsar/55/prompt/offermain").get_json()["prompt"] == "Gold members 2x"
    # no offer run answered (the model is down here): like the stock store, later stages fall back to the Offer Main prompt
    assert cl.get("/ai/pulsar/55/prompt/offerrules/").get_json()["prompt"] == "Gold members 2x"
    assert cl.get("/ai/pulsar/55/prompt/offersegmentation/").get_json()["prompt"] == ""


def test_catalog_gets_the_callers_authorization(fixture_catalog):
    seen = []
    app = create_app(ScriptedLLM(RUN), lambda sponsor, account, auth: seen.append((sponsor, auth)) or fixture_catalog)
    rules_req(app.test_client())
    assert seen == [(10, "JWT x")]


def test_non_ascii_labels_become_character_references(fixture_catalog):
    fixture = __import__("copy").deepcopy(fixture_catalog)
    fixture.attributes["activity"]["h_location"].values["L1"] = "Café Downtown"
    raw = atob(rules_req(client_for(ScriptedLLM(RUN), fixture)).get_json()["xml"])
    assert "Caf&#233; Downtown,Mall Store" in raw and ET.fromstring(stock_upload_filter(raw)) is not None


def test_a_failed_generation_is_run_again_not_served_again(fixture_catalog):
    """A failed job used to answer every regenerate for RECENT_S (120 s) with the same error."""
    from offer_author.ai.model import LLMError

    llm = ScriptedLLM([LLMError("Bedrock 429"), *RUN])
    cl = client_for(llm, fixture_catalog)
    assert rules_req(cl).status_code == 503
    j = rules_req(cl).get_json()
    assert j["xml"] and len(llm.calls) == 1 + len(RUN)


def test_offer_terms_are_escaped_in_the_html_the_ui_fills():
    meta = pulsar.offer_main_metadata(
        {"offer_name": "x", "terms": ["Spend < 50 & returns <b>excluded</b>"]}, "2026-10-29"
    )
    assert meta["term_and_condition"] == "<ul><li>Spend &lt; 50 &amp; returns &lt;b&gt;excluded&lt;/b&gt;</li></ul>"


def test_finished_jobs_are_dropped_after_keep_s(monkeypatch):
    store = pulsar.JobStore()
    old = store.start(store.key(1, "a"), lambda job: {"status": "draft"})
    old.wait(5)
    store.offer_of["1"] = store.key(1, "a")
    monkeypatch.setattr(pulsar.jobs, "KEEP_S", -1)
    store.start(store.key(2, "b"), lambda job: {"status": "draft"}).wait(5)
    assert store.key(1, "a") not in store.jobs and "1" not in store.latest and "1" not in store.offer_of
