"""E2E for the catalog: every live draft (tests/.live/catalog/*.xml from test_catalog_live.py) through the UNMODIFIED
stock upload path in develop gravty-ui (mocked bolapi, offers start with no counters), then every case with
transaction sequences through the real Gravty engine with the DRL the UI produced.

Run:  .venv/bin/python -m pytest -m e2e tests/test_catalog_e2e.py   (needs ng serve on :4200 and the spike harness)
"""
import datetime as dt
import json
import os
import shutil
import socket
import subprocess

import pytest

import catalog
from conftest import ROOT, SPIKE
from offer_author.catalog import Catalog
from offer_author.expander import expand
from offer_author.pipeline import mto_payloads
from offer_author.pulsar import ui_xml
from offer_author.rule_xml import offer_xml

pytestmark = pytest.mark.e2e
BLOCKS = os.environ.get("OFFER_AUTHOR_ENGINE") == "blocks"  # blocks mode: its own live results and harness dirs
LIVE = os.path.join(ROOT, "tests", ".live" + ("_blocks" if BLOCKS else ""))
IN_DIR, UI_DIR = ("blocks_catalog", "ui_blocks_catalog") if BLOCKS else ("proto_catalog", "ui_proto_catalog")
JAR = os.path.join(SPIKE, "engine-harness", "target", "harness.jar")
SKU_CATEGORY = {"SNK-1": "SNACKS", "SNK-2": "SNACKS", "DRK-1": "DRINKS", "BAK-1": "BAKERY"}
MEMBER = {"tier": "GOLD", "date_of_birth": "1990-06-15"}
BASE_DATE = dt.datetime(2026, 10, 20, 10, 0, 0)  # a Tuesday
SEQ_CASES = [c for c in catalog.load() if c.get("prompt") and c.get("sequences")]


@pytest.fixture(scope="module")
def ui():
    if not os.path.isdir(os.path.join(SPIKE, "ui-harness")):
        pytest.skip(f"needs the spike harness at {SPIKE} (set OFFER_AUTHOR_SPIKE)")
    src_xml = os.path.join(LIVE, "catalog")
    if not os.path.isdir(src_xml):
        pytest.skip("run -m live tests/test_catalog_live.py first")
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 4200)):
            pytest.skip("gravty-ui dev server not running on :4200")
    res = json.load(open(os.path.join(LIVE, "catalog.json")))
    src = os.path.join(SPIKE, "results", IN_DIR)
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src)
    # recompile the model's cached intent with the current compiler, so compiler changes are tested without model calls
    cat = Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))
    for k, r in res.items():
        if (r.get("rules") or {}).get("xml") and r.get("intent"):
            spec, counters = expand(r["intent"], cat.expander_catalog())
            r["rules"].update(xml=offer_xml(spec), counters=mto_payloads(counters, r["rules"].get("expected_offer_id", 1001)))
    for k, r in res.items():
        if (r.get("rules") or {}).get("xml"):
            # what the browser hands loadBlocklyFileUpload: atob of the ASCII the endpoint base64-encodes
            open(os.path.join(src, k + ".xml"), "w").write(ui_xml(r))
            json.dump({"counters": []}, open(os.path.join(src, k + ".spec.json"), "w"))
    p = subprocess.run(["node", "ui-harness/mock-run.mjs", "--path=upload", "--discover", "--fresh", f"--in={IN_DIR}", f"--out={UI_DIR}"],
                       cwd=SPIKE, capture_output=True, text=True, timeout=3600)
    assert p.returncode == 0, p.stderr[-2000:]
    report = json.load(open(os.path.join(SPIKE, "results", UI_DIR, "report.json")))
    return res, {k.split("@")[0]: v for k, v in report.items()}


def drafted(res):
    return sorted(k for k, r in res.items() if (r.get("rules") or {}).get("xml"))


def test_every_draft_loads_in_the_stock_ui(ui):
    res, report = ui
    bad = {}
    for sid in drafted(res):
        e = report.get(sid) or {}
        if not (e.get("loaded") and not e.get("dropped_fields") and not e.get("validation_errors") and not e.get("exceptions")
                and e.get("drl_bytes", 0) > 0):
            bad[sid] = {k: e.get(k) for k in ("loaded", "dropped_fields", "validation_errors", "exceptions", "drl_bytes")}
    json.dump(bad, open(os.path.join(LIVE, "catalog_ui_failures.json"), "w"), indent=1)
    assert not bad, json.dumps(bad, indent=1)[:5000]


def test_stock_ui_creates_exactly_our_counters(ui):
    res, report = ui
    wrong = {}
    for sid in drafted(res):
        want = {(m["name"], m["data_type"], m["counter_type"]) for m in res[sid]["rules"]["counters"]}
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
        bits.append(({"header": h, "lines": lines}, b.get("expect", {})))
    return bits


def same(got, want):
    """Counter values come back as text; compare numbers numerically, everything else as text."""
    try:
        return abs(float(got) - float(want)) < 0.01
    except (TypeError, ValueError):
        return got is not None and str(got) == str(want)


def run_engine(case, drl):
    rows = []
    for qi, seq in enumerate(case["sequences"]):
        bits = bits_of(seq)
        if bits is None:
            rows.append({"seq": qi, "status": "SKIPPED_EVENT"})
            continue
        member = dict(MEMBER, **seq.get("member_override", {}))
        job = {"offer_id": 1001, "drl": drl, "params": {}, "priority": 1,
               "member": {"id": "M1", "attributes": member, "tier": member.get("tier")},
               "products": {k: {"category": v} for k, v in SKU_CATEGORY.items()},
               "privilege_products": {"501": {"name": "Free Coffee", "cost": 4.5}}, "bits": [b for b, _ in bits]}
        path = os.path.join(SPIKE, "results", "engine", "cases", f"catalog__{case['id']}__{qi}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(job, open(path, "w"), indent=1)
        p = subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=300)
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            rows.append({"seq": qi, "status": "CRASH", "detail": p.stderr[-500:]})
            continue
        if out.get("compile") != "ok":
            rows.append({"seq": qi, "status": "COMPILE", "detail": str(out.get("messages"))[:500]})
            continue
        for bi, ((b, exp), bo) in enumerate(zip(bits, out["bits"])):
            if not exp:
                continue
            pts = round(sum(p_["total"] for p_ in bo.get("points", []) if p_["action"] == "REWARD_POINTS"), 2)
            pv = bo.get("privileges") or []
            priv = None if not pv else str(pv[0].get("product_id", pv[0].get("productId")) if isinstance(pv[0], dict) else pv[0])
            ok = ("points" not in exp or abs(pts - exp["points"]) < 0.01) and \
                 ("privilege" not in exp or priv == (str(exp["privilege"]) if exp["privilege"] is not None else None))
            # counter values by name: MA_O after this BIT (per offer), MA_A at the end of this BIT
            got_ctr = {"mto": bo.get("mto_after", {}).get("1001", {}), "mta": bo.get("mta_after_this_bit") or {}}
            ctr_bad = {f"{lvl}:{k}": got_ctr[lvl].get(k) for lvl in ("mto", "mta") for k, v in (exp.get(lvl) or {}).items()
                       if not same(got_ctr[lvl].get(k), v)}
            ok = ok and not ctr_bad
            if seq.get("known_gap") and not ok:
                rows.append({"seq": qi, "bit": bi, "status": "KNOWN_GAP"})
                continue
            rows.append({"seq": qi, "bit": bi, "status": "PASS" if ok else "FAIL", "date": b["header"]["h_bit_date"],
                         "expect": exp, "got": {"points": pts, "privilege": priv, **({"counters": ctr_bad} if ctr_bad else {})}})
    return rows


@pytest.fixture(scope="module")
def engine(ui):
    res, _ = ui
    out = {}
    for c in SEQ_CASES:
        drl_file = os.path.join(SPIKE, "results", UI_DIR, c["id"] + ".upload.drl")
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
