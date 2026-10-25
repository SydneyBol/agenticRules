"""The block-coverage drafts (tests/.live/block_coverage.json, from test_block_coverage.py) through the UNMODIFIED
stock upload path in the real develop gravty-ui (mocked bolapi, offers start with no counters): every draft must load
with nothing dropped, the UI must create exactly our counters, and the UI must produce a DRL.

Run:  .venv/bin/python -m pytest -m e2e tests/test_e2e_block_coverage.py   (needs ng serve on :4200 and the spike)
"""
import base64
import json
import os
import shutil
import socket
import subprocess

import pytest

from conftest import ROOT, SPIKE
from offer_author.pulsar import ui_xml

pytestmark = pytest.mark.e2e
LIVE = os.path.join(ROOT, "tests", ".live", "block_coverage.json")
IN_DIR, UI_DIR = "proto_block_coverage", "ui_proto_block_coverage"


@pytest.fixture(scope="module")
def run():
    if not os.path.isdir(os.path.join(SPIKE, "ui-harness")):
        pytest.skip(f"needs the spike harness at {SPIKE} (set OFFER_AUTHOR_SPIKE)")
    if not os.path.exists(LIVE):
        pytest.skip("run -m live tests/test_block_coverage.py first")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4200)):
            pytest.skip("gravty-ui dev server not running on :4200")
    results = {k: v for k, v in json.load(open(LIVE)).items() if (v.get("rules") or {}).get("xml")}
    src = os.path.join(SPIKE, "results", IN_DIR)
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    for sid, r in results.items():
        delivered = base64.b64encode(ui_xml(r).encode("ascii")).decode()  # the endpoint's {xml}
        open(os.path.join(src, sid + ".xml"), "w").write(base64.b64decode(delivered).decode("latin-1"))  # atob
        json.dump({"counters": []}, open(os.path.join(src, sid + ".spec.json"), "w"))
    ui = subprocess.run(["node", "ui-harness/mock-run.mjs", "--path=upload", "--discover", "--fresh",
                         f"--in={IN_DIR}", f"--out={UI_DIR}"], cwd=SPIKE, capture_output=True, text=True, timeout=1800)
    assert ui.returncode == 0, ui.stderr[-2000:]
    print(ui.stdout[-4000:])
    report = json.load(open(os.path.join(SPIKE, "results", UI_DIR, "report.json")))
    return results, {k.split("@")[0]: v for k, v in report.items()}


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
