"""Latency levers that must not change what is built: the shared prompt prefix cached for an hour and written ahead
of the popup's first run, cache writes counted at either lifetime, the bolapi reads made in parallel."""

import copy
import json
import threading

import pytest
from langchain_core.messages import AIMessage

from offer_author.ai import agent
from offer_author.ai.model import ChatLLM, usage_counts
from offer_author.ai.prompt import catalog_text, system_blocks
from offer_author.api.service import create_app
from offer_author.core.settings import Settings
from offer_author.core.tenant.catalog import BolapiCatalogSource
from tests.fakes import ScriptedLLM


def test_the_shared_prefix_is_cached_for_an_hour_unless_set_to_five_minutes(fixture_catalog):
    marks = [b.get("cache_control") for b in system_blocks(fixture_catalog)]
    assert marks == [None, {"type": "ephemeral", "ttl": "1h"}, {"type": "ephemeral", "ttl": "1h"}]
    assert [b.get("cache_control") for b in system_blocks(fixture_catalog, ttl="5m")][1] == {"type": "ephemeral"}
    assert Settings.from_env({"OFFER_AUTHOR_CACHE_TTL": "5m"}).cache_ttl == "5m" and Settings().cache_ttl == "1h"


def test_cache_writes_are_counted_at_either_lifetime():
    one_hour = {
        "input_tokens": 81510,
        "output_tokens": 0,
        "input_token_details": {"cache_read": 0, "cache_creation": 0, "ephemeral_1h_input_tokens": 81504},
    }
    assert usage_counts(one_hour) == {
        "input_tokens": 6,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 81504,
    }


class Chat:
    """The LangChain chat model as ChatLLM.warm uses it."""

    def __init__(self):
        self.calls = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages, **kw):
        self.calls.append((messages, kw))
        return AIMessage(
            content=[],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 0,
                "total_tokens": 10,
                "input_token_details": {"cache_creation": 9},
            },
        )


def test_warm_sends_the_runs_own_prefix_with_no_output(fixture_catalog):
    chat = Chat()
    usage = agent.warm(ChatLLM(model="x", chat=chat), fixture_catalog)
    ((messages, kw),) = chat.calls
    assert kw == {"max_tokens": 0} and chat.tools == agent.SCHEMAS
    assert messages[0].content == system_blocks(fixture_catalog)  # byte for byte what a run sends: the same cache entry
    assert usage["cache_creation_input_tokens"] == 9
    assert agent.warm(ScriptedLLM([]), fixture_catalog) is None  # a client without warm() is left alone


def test_an_offer_screen_warms_the_cache_once_per_lifetime(fixture_catalog):
    class Warm(ScriptedLLM):
        def __init__(self):
            super().__init__([])
            self.warms, self.done = 0, threading.Event()

        def warm(self, system, tools=None):
            self.warms += 1
            self.done.set()
            return {}

    llm = Warm()
    cl = create_app(llm, lambda s, a, auth: fixture_catalog).test_client()
    assert cl.get("/ai/pulsar/31/prompt/offerrules/").status_code == 200
    assert llm.done.wait(5)
    cl.get("/ai/pulsar/31/prompt/offersegmentation/")
    cl.get("/ai/pulsar/32/prompt/offerrules/")
    threading.Event().wait(0.3)
    assert llm.warms == 1  # the same tenant again within the cache lifetime: no second write


def test_the_live_catalog_reads_bolapi_in_parallel_and_builds_the_same_catalog(fixture_catalog):
    seen, lock, inflight, peak = [], threading.Lock(), [0], [0]

    class R:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Http:
        def get(self, url, headers):
            with lock:
                inflight[0] += 1
                peak[0] = max(peak[0], inflight[0])
            threading.Event().wait(0.05)
            with lock:
                inflight[0] -= 1
                seen.append(url)
            if "attributelibrary" in url:
                return R([{"name": "h_bit_amount", "data_type": "NUMBER"}])
            if "loyaltyaccounts/?" in url:
                return R([{"id": 9, "loyalty_currency": "Points"}])
            return R([])

    cat = BolapiCatalogSource("https://t.example", http=Http()).get("t", 1, 9, "JWT x")
    assert len(seen) == 17 and peak[0] > 1  # all seventeen read, several at once
    assert cat.attr("activity", "h_bit_amount").type == "NUMBER" and cat.loyalty_accounts[0]["id"] == "9"
    json.dumps(catalog_text(cat))


def test_after_a_run_the_catalog_is_read_again_and_its_changes_embedded_for_the_next(fixture_catalog, monkeypatch):
    from offer_author.api.pulsar import runs as runs_mod

    fresh = copy.deepcopy(fixture_catalog)
    warmed, done = [], threading.Event()
    monkeypatch.setattr(runs_mod, "warm_values", lambda cat, settings=None: warmed.append(("values", cat)))
    monkeypatch.setattr(
        runs_mod, "warm_history", lambda cat, settings=None: (warmed.append(("history", cat)), done.set())
    )
    current, reads = [fixture_catalog], []

    def reread(sponsor, account, auth):
        reads.append((sponsor, auth))
        current[0] = fresh
        return fresh

    r = runs_mod.Runs(ScriptedLLM([]), lambda s, a, auth: current[0], refresh_catalog=reread)
    assert r.segment_job("Gold members", 1, "JWT x").wait(10)  # the scripted model has no turn: the run fails
    assert done.wait(5)
    assert reads == [(1, "JWT x")] and warmed == [("values", fresh), ("history", fresh)]  # no model call in it
    assert r.catalog_for(1, None, "JWT x") is fresh  # the next run reads the catalog read again


def test_no_refresh_without_a_way_to_read_the_catalog_again(fixture_catalog, monkeypatch):
    from offer_author.api.pulsar import runs as runs_mod

    monkeypatch.setattr(runs_mod, "warm_values", lambda *a, **k: pytest.fail("nothing to refresh"))
    r = runs_mod.Runs(ScriptedLLM([]), lambda s, a, auth: fixture_catalog)
    assert r.segment_job("Gold members", 1, None).wait(10)
    threading.Event().wait(0.2)
