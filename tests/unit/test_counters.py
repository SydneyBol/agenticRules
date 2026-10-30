"""Counters already on the offer: the agent names its counters, code checks each name against the offer (reuse only an
exact match on a planning offer), and the report lists what is reused, created and left unused."""

import base64
import json

from offer_author.ai.agent import generate
from offer_author.ai.render import text as worded
from offer_author.api.pulsar import notes
from offer_author.api.service import create_app
from offer_author.core.tenant.existing_counters import BolapiCounterSource, Guard, normalize
from tests.fakes import ScriptedLLM
from tests.support import checked, finish, golden, new_run, public, said

MILESTONE = ("compose", golden("S08_spend_milestone_once"))  # counters: Total spend (NUMBER), 500 points given (TEXT)
UNIQUE = ("compose", golden("S09_unique_locations"))  # Visits per store: VECTOR per h_location


def mto(name, data_type="NUMBER", counter_type="REGULAR", ns=None, level="OFFER"):
    m = {"id": 1, "name": name, "data_type": data_type, "counter_type": counter_type, "level": level}
    if ns:
        m["entity_attribute_map"] = {"Activity": [ns]}
    return m


def renamed(call, old, new):
    return (call[0], json.loads(json.dumps(call[1]).replace(f'"{old}"', f'"{new}"')))


def run(fixture_catalog, turns, existing, offer_id=4242):
    llm = ScriptedLLM([*turns, [finish()]])
    return generate("...", fixture_catalog, llm, offer_id=offer_id, existing=existing), llm


def problems(llm, k=1):
    return json.loads(llm.calls[-1][2 * k]["content"][0]["content"])["problems"]


def names(res):
    return [c["name"] for c in res["rules"]["counters"]]


def test_exact_match_on_planning_offer_is_reused_and_old_ones_listed(fixture_catalog):
    existing = {
        "offer_status": "planning",
        "counters": [mto("Total spend"), mto("500 points given", "TEXT"), mto("AI visit count")],
    }
    res, _ = run(fixture_catalog, [[MILESTONE]], existing)
    rep = res["rules"]["counter_report"]
    assert names(res) == ["Total spend", "500 points given"]
    assert rep["reused"] == ["Total spend", "500 points given"] and rep["renamed"] == [] and rep["created"] == []
    assert rep["unused"] == ["AI visit count"] and notes(public(res), "rules")["counter_report"] == rep


def test_same_name_other_type_is_sent_back_and_the_model_picks_another(fixture_catalog):
    existing = {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]}
    res, llm = run(fixture_catalog, [[MILESTONE], [renamed(MILESTONE, "Total spend", "Total spend 2")]], existing)
    assert any(
        "counter 'Total spend' already exists on this offer and the offer's counter is MA_O TEXT REGULAR, these "
        "rules need MA_O NUMBER REGULAR" in p
        for p in problems(llm)
    )
    assert names(res)[0] == "Total spend 2" and res["rules"]["counter_report"]["created"] == [
        "Total spend 2",
        "500 points given",
    ]
    assert "Total spend 2" in res["rules"]["xml"] and '"Total spend"' not in res["rules"]["xml"]
    assert public(res)["rules"]["counter_report"]["renamed"] == [
        {
            "wanted": "Total spend",
            "used": "Total spend 2",
            "why": "the offer's counter is MA_O TEXT REGULAR, these rules need MA_O NUMBER REGULAR",
        }
    ]


def test_a_refused_name_is_paired_with_the_new_counter_of_its_types():
    g = Guard({"offer_status": "planning", "counters": [mto("Total spend", "TEXT"), mto("Visits")]})
    assert g.taken("Total spend", "NUMBER", "REGULAR", None) and not g.taken("Visits", "NUMBER", "REGULAR", None)
    rep = g.report([mto("Visit days", "DATE"), mto("Spend so far"), mto("Visits")])  # the rename is the NUMBER one
    assert [(r["wanted"], r["used"]) for r in rep["renamed"]] == [("Total spend", "Spend so far")]
    assert rep["reused"] == ["Visits"] and rep["created"] == ["Visit days", "Spend so far"]
    assert g.report([mto("Visit days", "DATE")])["renamed"] == []  # dropped, not renamed: nothing to pair


def test_vector_namespace_must_match_too(fixture_catalog):
    existing = {
        "offer_status": "planning",
        "counters": [mto("Visits per store", counter_type="VECTOR", ns="h_bit_type")],
    }
    assert any(
        "per h_bit_type" in p for p in said(checked(new_run(fixture_catalog, 1, existing), UNIQUE[1]))["problems"]
    )


def test_exact_match_on_a_launched_offer_is_not_reused(fixture_catalog):
    existing = {"offer_status": {"status": {"code": "launched"}}, "counters": [mto("Total spend")]}
    out = said(checked(new_run(fixture_catalog, 1, existing), MILESTONE[1]))
    assert any("members may already hold values" in p for p in out["problems"])


def test_unknown_counters_are_said_not_checked(fixture_catalog):
    res, _ = run(fixture_catalog, [[MILESTONE]], None)
    assert res["rules"]["counter_report"] == {"checked": False}


def test_normalize_reads_bolapi_records():
    assert normalize([mto("Visits per store", counter_type="VECTOR", ns="h_location")]) == [
        {
            "name": "Visits per store",
            "level": "MA_O",
            "data_type": "NUMBER",
            "counter_type": "VECTOR",
            "namespace": "h_location",
        }
    ]
    assert Guard({"offer_status": {"status": "PLANNING"}, "counters": []}).planning


def test_pulsar_endpoint_takes_existing_counters_and_logs_the_report(fixture_catalog, logged):
    llm = ScriptedLLM([[MILESTONE], [renamed(MILESTONE, "Total spend", "Total spend 2")], [finish()]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    body = {
        "prompt": "500 points at 1000 total spend",
        "sponsor_id": 10,
        "id": 31,
        "type": "reward",
        "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]},
    }
    j = cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json()
    xml = base64.b64decode(j["xml"]).decode()
    assert "Counters already on this offer" in llm.calls[0][0]["content"][0]["text"]
    assert (
        logged("rules")["counter_report"]["created"] == ["Total spend 2", "500 points given"] and "Total spend 2" in xml
    )
    assert [(r["wanted"], r["used"]) for r in logged("rules")["counter_report"]["renamed"]] == [
        ("Total spend", "Total spend 2")
    ]


def test_the_offer_runs_counters_are_checked_when_the_rules_screen_takes_them(fixture_catalog, logged):
    """The whole-offer run names counters before the offer exists; a clash found when the rules are handed out is
    rebuilt as a rules-only run that knows the offer's counters."""
    from tests.support import offer_main

    llm = ScriptedLLM(
        [
            [offer_main(rules_prompt="500 points at 1000 total spend")],
            [MILESTONE],
            [finish()],
            [renamed(MILESTONE, "Total spend", "Total spend 2")],
            [finish()],
        ]
    )
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    prompt = "500 points once a member has spent 1000 in total"
    cl.post("/ai/pulsar/31/prompt/offermain/", json={"prompt": prompt, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": prompt, "category": "offer"})
    body = {
        "prompt": prompt,
        "sponsor_id": 10,
        "id": 31,
        "type": "reward",
        "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]},
    }
    assert cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).status_code == 200
    assert logged("rules")["counter_report"]["created"] == ["Total spend 2", "500 points given"] and len(llm.calls) == 5


def test_live_source_reads_offer_and_counters_with_the_callers_jwt():
    seen = []

    class R:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Http:
        def get(self, url, headers):
            seen.append((url, headers["Authorization"]))
            if url.endswith("/drl/"):
                return R([{"xml": base64.b64encode(b'<xml><block type="offer_generic"></block></xml>').decode()}])
            return R({"status": "planning"} if url.endswith("/offers/9/") else [mto("Total spend")])

    got = BolapiCounterSource("https://t.example", http=Http()).get(9, "JWT abc")
    assert got["offer_status"] == {"status": "planning"} and got["counters"][0]["name"] == "Total spend"
    assert got["rules_xml"] == '<xml><block type="offer_generic"></block></xml>'  # the saved canvas, decoded
    assert sorted(u for u, _ in seen) == [
        "https://t.example/bolapi/v1/offers/9/",
        "https://t.example/bolapi/v1/offers/9/drl/",
        "https://t.example/bolapi/v1/offers/9/member-tracked-attributes/",
    ]  # read in parallel
    assert BolapiCounterSource("https://t.example", http=Http()).get(9, None) is None


def test_same_name_at_another_level_is_not_reused():
    """devintbf2 live (2026-10-26): 'Birthday offset' existed as an ACTIVITY counter; the rules used it as MA_O. The
    dialog looks it up among the MA_O counters only, POSTs it, and bolapi answers 400 (offer, name unique): the draft
    never reached the canvas. The guard must send the name back."""
    from offer_author.core.tenant.existing_counters import Guard

    g = Guard({"offer_status": "planning", "counters": [mto("Birthday offset", level="ACTIVITY")]})
    assert g.taken("Birthday offset", "NUMBER", "REGULAR", None, "MA_O")
    assert "MA_A NUMBER REGULAR" in worded(g.why("Birthday offset", "NUMBER", "REGULAR", None, "MA_O"))
    assert not g.taken("Birthday offset", "NUMBER", "REGULAR", None, "MA_A") and g.reused == ["Birthday offset"]


def test_the_rules_screen_checks_each_counter_at_its_own_level(fixture_catalog, logged):
    """deliver() checked every counter as an offer counter (MA_O): an activity counter matching the offer's own
    activity counter was refused and the rules rebuilt for nothing."""
    from tests.support import offer_main

    tree = json.loads(
        json.dumps(MILESTONE[1])
        .replace('"MA_O", "mta_attribute": "Total spend"', '"MA_A", "mta_attribute": "Total spend"')
        .replace('"MA_O", "attribute": "Total spend"', '"MA_A", "attribute": "Total spend"')
    )
    for c in tree["counters"]:
        if c["name"] == "Total spend":
            c["level"] = "MA_A"
    llm = ScriptedLLM([[offer_main(rules_prompt="500 points at 1000 total spend")], [("compose", tree)], [finish()]])
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    prompt = "500 points once a member has spent 1000 in total"
    cl.post("/ai/pulsar/32/prompt/offermain/", json={"prompt": prompt, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": prompt, "category": "offer"})
    body = {
        "prompt": prompt,
        "sponsor_id": 10,
        "id": 32,
        "type": "reward",
        "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", level="ACTIVITY")]},
    }
    assert cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).status_code == 200
    assert logged("rules")["counter_report"]["reused"] == ["Total spend"] and len(llm.calls) == 3  # no rebuild


def test_rules_whose_counter_rebuild_is_still_running_are_not_handed_out(fixture_catalog, monkeypatch, logged):
    """The rebuild outlasting WAIT_CACHED_S used to hand out the first rules, whose counter the upload would reuse wrongly."""
    import threading

    from offer_author.api import pulsar
    from tests.support import offer_main

    monkeypatch.setattr(pulsar.jobs, "WAIT_CACHED_S", 0.2)

    class SlowRebuild(ScriptedLLM):
        release = threading.Event()

        def turn(self, *a, **k):
            if len(self.calls) >= 3:  # the rules-only rebuild
                self.release.wait(10)
            return super().turn(*a, **k)

    llm = SlowRebuild(
        [
            [offer_main(rules_prompt="500 points at 1000 total spend")],
            [MILESTONE],
            [finish()],
            [renamed(MILESTONE, "Total spend", "Total spend 2")],
            [finish()],
        ]
    )
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    prompt = "500 points once a member has spent 1000 in total"
    cl.post("/ai/pulsar/33/prompt/offermain/", json={"prompt": prompt, "offer_status": "planning"})
    cl.post("/ai/pulsar/offer/metadata", json={"prompt": prompt, "category": "offer"})
    body = {
        "prompt": prompt,
        "sponsor_id": 10,
        "id": 33,
        "type": "reward",
        "existing_counters": {"offer_status": "planning", "counters": [mto("Total spend", "TEXT")]},
    }
    assert cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json() == {"result": "Generating"}
    llm.release.set()
    for _ in range(50):
        j = cl.post("/ai/pulsar/offer/rules?regenerate=true", json=body).get_json()
        if j.get("result") != "Generating":
            break
    assert logged("rules")["counter_report"]["created"] == ["Total spend 2", "500 points given"]


def test_a_counter_used_at_another_level_than_declared_is_sent_back():
    """C12 (2026-10-30): an MA_O counter read at MA_A; the screen found no MA_A counter and left the condition empty."""
    from offer_author.core.canvas.checks.counters import counter_problems

    def xml(level):
        return (
            '<xml><block type="if_generic_adaptive" id="b1"><mutation object="member" '
            f'attribute_level="{level}" attribute="Weekend Before" operators="&lt;"/></block></xml>'
        )

    declared = [{"name": "Weekend Before", "data_type": "NUMBER"}]  # level left out: MA_O
    assert counter_problems(xml("MA_O"), declared, None) == []
    (p,) = counter_problems(xml("MA_A"), declared, None)
    assert (p.code, p.data) == ("undeclared_counter", {"name": "Weekend Before", "level": "MA_A", "declared": "MA_O"})
    assert "declares it at MA_O" in worded(p)
    (p,) = counter_problems(xml("MA_A"), [], None)
    assert worded(p) == "the blocks use counter 'Weekend Before' at MA_A, which is not declared in counters"
