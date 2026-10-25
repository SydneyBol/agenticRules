"""End to end: fresh model output (tests/.live/fixture.json from test_live.py) -> prototype XML -> REAL gravty-ui Blockly
(develop build, mocked backend, ng serve on :4200) -> DRL -> REAL engine (Drools 7.73 harness) -> BIT expectations.

Reuses the harnesses in harness/ (OFFER_AUTHOR_SPIKE overrides).
Run:  .venv/bin/python -m pytest -m e2e      (after -m live has written tests/.live/fixture.json)
"""
import json
import os
import socket
import subprocess
import sys

import pytest

from conftest import ROOT, SPIKE

pytestmark = pytest.mark.e2e
LIVE = os.path.join(ROOT, "tests", ".live", "fixture.json")
IN_DIR, UI_DIR = "proto_live", "ui_proto_live"  # under SPIKE/results


def ui_up():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", 4200)) == 0


def spike_counters(mto_payloads):
    """MTO payloads -> the counter shape the spike's mocked backend serves back to the UI."""
    return [{"name": m["name"], "level": "MA_O", "data_type": m["data_type"], "counter_type": m["counter_type"],
             "target_type": m.get("target_type", "NO TARGET"),
             "vector_namespace": (m.get("entity_attribute_map") or {}).get("Activity", [None])[0]} for m in mto_payloads]


@pytest.fixture(scope="module")
def engine_rows():
    if not os.path.isdir(os.path.join(SPIKE, "ui-harness")):
        pytest.skip(f"needs the spike harness at {SPIKE} (set OFFER_AUTHOR_SPIKE)")
    if not os.path.exists(LIVE):
        pytest.skip("run -m live first (writes tests/.live/fixture.json)")
    if not ui_up():
        pytest.skip("gravty-ui dev server not running on :4200")
    results = json.load(open(LIVE))
    src = os.path.join(SPIKE, "results", IN_DIR)
    os.makedirs(src, exist_ok=True)
    for f in os.listdir(src):
        os.remove(os.path.join(src, f))
    for sid, r in results.items():
        if r.get("rules"):
            open(os.path.join(src, sid + ".xml"), "w").write(r["rules"]["xml"])
            json.dump({"counters": spike_counters(r["rules"]["counters"])}, open(os.path.join(src, sid + ".spec.json"), "w"))
    ui = subprocess.run(["node", "ui-harness/mock-run.mjs", "--path=append", f"--in={IN_DIR}", f"--out={UI_DIR}"],
                        cwd=SPIKE, capture_output=True, text=True, timeout=1500)
    assert ui.returncode == 0, ui.stderr[-2000:]
    report = json.load(open(os.path.join(SPIKE, "results", UI_DIR, "report.json")))
    eng = subprocess.run([sys.executable, "engine-harness/run_scenarios.py", f"--ui={UI_DIR}"],
                         cwd=SPIKE, capture_output=True, text=True, timeout=1500)
    assert eng.returncode == 0, eng.stderr[-2000:]
    rows = json.load(open(os.path.join(SPIKE, "results", "engine", f"engine_scenarios_{UI_DIR}.json")))
    print(ui.stdout[-3000:], eng.stdout[-1500:])
    return report, rows


def test_real_blockly_loads_every_draft_without_loss(engine_rows):
    report, _ = engine_rows
    entries = report if isinstance(report, list) else list(report.values())
    assert entries
    for e in entries:
        assert e.get("loaded") and not e.get("dropped_fields") and not e.get("validation_errors") and e.get("drl_bytes", 0) > 0, e


def test_engine_behaviour_matches_expectations(engine_rows):
    _, rows = engine_rows
    bits = [r for r in rows if "status" in r and r["status"] in ("PASS", "FAIL", "KNOWN_GAP")]
    fails = [r for r in bits if r["status"] == "FAIL"]
    assert not fails, json.dumps(fails, indent=1)[:3000]
    assert sum(r["status"] == "PASS" for r in bits) >= 63
    assert all(r["status"] != "NO_DRL" for r in rows if not r["scenario"].startswith(("S11", "S15", "S16")))
