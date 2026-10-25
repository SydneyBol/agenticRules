"""The block-coverage drafts (tests/.live/block_coverage.json, from tests/live/test_block_coverage.py) through the UNMODIFIED
stock upload path in the real develop gravty-ui (mocked bolapi, offers start with no counters): every draft must load
with nothing dropped, the UI must create exactly our counters, and the UI must produce a DRL.

Run:  .venv/bin/python -m pytest -m e2e tests/e2e/test_block_coverage.py   (needs ng serve on :4200)
"""
import base64
import json
import os
import socket

import pytest

from offer_author.api.pulsar import ui_xml
from tools.paths import LIVE as LIVE_DIR
from tools.qa import stock_ui

pytestmark = pytest.mark.e2e
LIVE = os.path.join(LIVE_DIR, "block_coverage.json")


@pytest.fixture(scope="module")
def run():
    if not os.path.exists(LIVE):
        pytest.skip("run -m live tests/live/test_block_coverage.py first")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4200)):
            pytest.skip("gravty-ui dev server not running on :4200")
    results = {k: v for k, v in json.load(open(LIVE)).items() if (v.get("rules") or {}).get("xml")}
    # the endpoint's {xml}, then atob as the browser does
    drafts = {sid: base64.b64decode(base64.b64encode(ui_xml(r).encode("ascii"))).decode("latin-1") for sid, r in results.items()}
    report, _ = stock_ui.upload(drafts, "agent_block_coverage", timeout=1800)
    return results, report


def test_every_draft_loads_in_the_stock_ui(run):
    results, report = run
    bad = {sid: report[sid] for sid in results
           if not (report[sid]["loaded"] and not report[sid].get("dropped_fields") and not report[sid].get("validation_errors")
                   and not report[sid].get("exceptions") and report[sid]["drl_bytes"] > 0)}
    assert not bad, json.dumps(bad, indent=1)[:4000]


def test_stock_ui_creates_exactly_our_counters(run):
    results, report = run
    for sid, r in results.items():
        want = {(m["name"], m["data_type"], m["counter_type"]) for m in r["rules"]["counters"]}
        got = {(m.get("name"), m.get("data_type"), m.get("counter_type")) for m in report[sid].get("mto_posted") or []}
        assert got == want, (sid, sorted(got), sorted(want))
