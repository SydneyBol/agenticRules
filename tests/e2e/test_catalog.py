"""E2E for the catalog: every live draft (tests/.live/catalog/*.xml from tests/live/test_catalog.py) through the UNMODIFIED
stock upload path in develop gravty-ui (mocked bolapi, offers start with no counters), then every case with
transaction sequences through the real Gravty engine with the DRL the UI produced.

Run:  .venv/bin/python -m pytest -m e2e tests/e2e/test_catalog.py   (needs ng serve on :4200 and the engine jar)
"""
import datetime as dt
import json
import os
import socket

import pytest

from offer_author.api.pulsar import ui_xml
from offer_author.core import engine as eng
from tests.support import cases
from tools.paths import LIVE
from tools.qa import stock_ui

pytestmark = pytest.mark.e2e
SKU_CATEGORY = {"SNK-1": "SNACKS", "SNK-2": "SNACKS", "DRK-1": "DRINKS", "BAK-1": "BAKERY"}
MEMBER = {"tier": "GOLD", "date_of_birth": "1990-06-15"}
BASE_DATE = dt.datetime(2026, 10, 20, 10, 0, 0)  # a Tuesday
SEQ_CASES = [c for c in cases.load() if c.get("prompt") and c.get("sequences")]


@pytest.fixture(scope="module")
def ui():
    if not os.path.isdir(os.path.join(LIVE, "catalog")):
        pytest.skip("run -m live tests/live/test_catalog.py first")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4200)):
            pytest.skip("gravty-ui dev server not running on :4200")
    res = json.load(open(os.path.join(LIVE, "catalog.json")))
    # what the browser hands loadBlocklyFileUpload: atob of the ASCII the endpoint base64-encodes
    report, out = stock_ui.upload({k: ui_xml(r) for k, r in res.items() if (r.get("rules") or {}).get("xml")}, "agent_catalog")
    return res, report, out


def drafted(res):
    return sorted(k for k, r in res.items() if (r.get("rules") or {}).get("xml"))


def test_every_draft_loads_in_the_stock_ui(ui):
    res, report, _ = ui
    bad = {}
    for sid in drafted(res):
        e = report.get(sid) or {}
        if not (e.get("loaded") and not e.get("dropped_fields") and not e.get("validation_errors") and not e.get("exceptions")
                and e.get("drl_bytes", 0) > 0):
            bad[sid] = {k: e.get(k) for k in ("loaded", "dropped_fields", "validation_errors", "exceptions", "drl_bytes")}
    json.dump(bad, open(os.path.join(LIVE, "catalog_ui_failures.json"), "w"), indent=1)
    assert not bad, json.dumps(bad, indent=1)[:5000]


def test_stock_ui_creates_exactly_our_counters(ui):
    res, report, _ = ui
    wrong = {}
    for sid in drafted(res):
        want = {(m.get("name"), m.get("data_type"), m.get("counter_type")) for m in res[sid]["rules"]["counters"]}
        got = {(m.get("name"), m.get("data_type"), m.get("counter_type")) for m in (report.get(sid) or {}).get("mto_posted") or []}
        if got != want:
            wrong[sid] = {"ui": sorted(got), "ours": sorted(want)}
    assert not wrong, json.dumps(wrong, indent=1)[:4000]


def bits_of(seq):
    bits = []
    for i, b in enumerate(seq["bits"]):
        if "event" in b:
            return None
        h = {"h_bit_category": "ACCRUAL", "h_bit_type": "SPEND", "h_sponsor_id": 10, "h_program_id": 1, "h_member_id": "M1",
             "h_location": "L1", "h_bit_amount": 10, "h_bit_date": (BASE_DATE + dt.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S")}
        h.update({k: v for k, v in b.items() if k.startswith("h_")})
        lines = [{"l_product_external_id": ln["sku"], "l_quantity": ln["qty"], "l_amount": ln["amount"]} for ln in b.get("lines", [])]
        if lines and "h_bit_amount" not in b:
            h["h_bit_amount"] = sum(ln["l_amount"] for ln in lines)
        bits.append({"header": h, "lines": lines, "expect": b.get("expect", {})})
    return bits


def run_engine(case, drl):
    """Each sequence through the engine (offer_author.core.engine.simulate: points, privilege and counters by name,
    MA_O after the BIT and MA_A at its end, against the case's expectations)."""
    rows = []
    for qi, seq in enumerate(case["sequences"]):
        bits = bits_of(seq)
        if bits is None:
            rows.append({"seq": qi, "status": "SKIPPED_EVENT"})
            continue
        member = dict(MEMBER, **seq.get("member_override", {}))
        out = eng.simulate(drl, {"member": {"attributes": member, "tier": member.get("tier")},
                                 "products": {k: {"category": v} for k, v in SKU_CATEGORY.items()},
                                 "privilege_products": {"501": {"name": "Free Coffee", "cost": 4.5}}, "bits": bits})
        if out.get("compile") != "ok":
            rows.append({"seq": qi, "status": "COMPILE", "detail": out.get("detail")})
            continue
        for bi, (b, r) in enumerate(zip(bits, out["bits"])):
            if not b["expect"]:
                continue
            if seq.get("known_gap") and not r["ok"]:
                rows.append({"seq": qi, "bit": bi, "status": "KNOWN_GAP"})
                continue
            rows.append({"seq": qi, "bit": bi, "status": "PASS" if r["ok"] else "FAIL", "date": b["header"]["h_bit_date"],
                         "expect": b["expect"], "got": {"points": r["points"], "privileges": r["privileges"],
                                                        **({"mismatch": r["mismatch"]} if r.get("mismatch") else {})}})
    return rows


@pytest.fixture(scope="module")
def engine(ui):
    res, _, ui_out = ui
    out = {}
    for c in SEQ_CASES:
        drl_file = os.path.join(ui_out, c["id"] + ".upload.drl")
        out[c["id"]] = run_engine(c, open(drl_file).read()) if os.path.exists(drl_file) else [{"status": "NO_DRL",
                                                                                                "why": res.get(c["id"], {}).get("status")}]
    json.dump(out, open(os.path.join(LIVE, "catalog_engine.json"), "w"), indent=1)
    return out


@pytest.mark.parametrize("case", SEQ_CASES, ids=[c["id"] for c in SEQ_CASES])
def test_engine_behaviour(engine, case):
    rows = engine[case["id"]]
    bad = [r for r in rows if r["status"] not in ("PASS", "KNOWN_GAP", "SKIPPED_EVENT")]
    if rows and rows[0]["status"] == "NO_DRL" and case.get("needs_catalog"):
        pytest.xfail(f"no draft (needs {case['needs_catalog']})")
    assert not bad, json.dumps(bad)[:1500]
