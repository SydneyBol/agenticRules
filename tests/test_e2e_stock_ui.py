"""Stock UI, no UI change: what the Pulsar endpoint returns -> atob -> the UNMODIFIED upload path
(rulesUploadConfirmation + loadBlocklyFileUpload(xml, true), exactly what handlePopupResponse and the rules-screen
autoload call) in the real develop gravty-ui with a mocked bolapi. The offer starts with NO counters, so the UI's own
counter discovery must create them. Then the UI's DRL goes through the real engine.

Run:  .venv/bin/python -m pytest -m e2e   (after -m live; needs ng serve on :4200 and the spike)
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys

import pytest

from conftest import ROOT, SPIKE
from offer_author.catalog import Catalog
from offer_author.expander import expand
from offer_author.pipeline import mto_payloads
from offer_author.pulsar import ui_xml
from offer_author.rule_xml import offer_xml

pytestmark = pytest.mark.e2e
LIVE = os.path.join(ROOT, "tests", ".live", "fixture.json")
IN_DIR, UI_DIR, DRL_DIR = "proto_pulsar", "ui_proto_pulsar", "ui_proto_pulsar_drl"


@pytest.fixture(scope="module")
def run():
    if not os.path.isdir(os.path.join(SPIKE, "ui-harness")):
        pytest.skip(f"needs the spike harness at {SPIKE} (set OFFER_AUTHOR_SPIKE)")
    if not os.path.exists(LIVE):
        pytest.skip("run -m live first (writes tests/.live/fixture.json)")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4200)):
            pytest.skip("gravty-ui dev server not running on :4200")
    results = {k: v for k, v in json.load(open(LIVE)).items() if v.get("rules")}
    # recompile the model's cached intent with the current compiler, so compiler fixes are tested without new model calls
    cat = Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))
    for sid, r in results.items():
        spec, counters = expand(r["intent"], cat.expander_catalog())
        r["rules"].update(xml=offer_xml(spec), counters=mto_payloads(counters, r["rules"]["expected_offer_id"]))
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
    print(ui.stdout[-2500:])
    report = json.load(open(os.path.join(SPIKE, "results", UI_DIR, "report.json")))
    drl_dir = os.path.join(SPIKE, "results", DRL_DIR)
    shutil.rmtree(drl_dir, ignore_errors=True)
    os.makedirs(drl_dir)
    for sid in results:
        f = os.path.join(SPIKE, "results", UI_DIR, sid + ".upload.drl")
        if os.path.exists(f):
            shutil.copy(f, os.path.join(drl_dir, sid + ".drl"))
    eng = subprocess.run([sys.executable, "engine-harness/run_scenarios.py", f"--ui={DRL_DIR}"],
                         cwd=SPIKE, capture_output=True, text=True, timeout=1800)
    assert eng.returncode == 0, eng.stderr[-2000:]
    print(eng.stdout[-1200:])
    rows = json.load(open(os.path.join(SPIKE, "results", "engine", f"engine_scenarios_{DRL_DIR}.json")))
    return results, {k.split("@")[0]: v for k, v in report.items()}, rows


def test_stock_upload_path_loads_every_draft(run):
    results, report, _ = run
    for sid in results:
        e = report[sid]
        assert e["loaded"] and not e.get("dropped_fields") and not e.get("validation_errors") and e["drl_bytes"] > 0, (sid, e)
        assert not e.get("exceptions"), (sid, e["exceptions"])


def test_note_survives_on_the_offer_block(run):
    results, _, _ = run
    for sid in results:
        canonical = open(os.path.join(SPIKE, "results", UI_DIR, sid + ".upload.canonical.xml")).read()
        assert "<comment" in canonical and "AI draft: " in canonical, sid


def test_stock_ui_creates_exactly_our_counters(run):
    results, report, _ = run
    for sid, r in results.items():
        want = {(m["name"], m["data_type"], m["counter_type"]) for m in r["rules"]["counters"]}
        got = {(m.get("name"), m.get("data_type"), m.get("counter_type")) for m in report[sid].get("mto_posted") or []}
        assert got == want, (sid, sorted(got), sorted(want))
        for m in report[sid].get("mto_posted") or []:
            if m.get("counter_type") == "VECTOR":
                assert "h_location" in json.dumps(m), (sid, m)


def test_engine_behaviour_unchanged(run):
    _, _, rows = run
    bits = [r for r in rows if r.get("status") in ("PASS", "FAIL", "KNOWN_GAP")]
    assert not [r for r in bits if r["status"] == "FAIL"], json.dumps([r for r in bits if r["status"] == "FAIL"])[:3000]
    assert sum(r["status"] == "PASS" for r in bits) >= 63
