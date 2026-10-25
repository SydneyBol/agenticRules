"""Live: every prompt case in the catalog (tests/cases/*.yaml + spike scenarios) through Claude on Bedrock and the
full pipeline, with the fixture catalog. Checks status, flags, required blocks/modes, toolbox-only blocks, nesting.

Run:  AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live tests/test_catalog_live.py
Writes tests/.live/catalog.json and tests/.live/catalog/<id>.xml (input to test_catalog_e2e.py).
"""
import collections
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

import catalog
from conftest import ROOT
from offer_author.llm import BedrockLLM
from offer_author.pipeline import generate
from offer_author.pulsar import ui_xml

pytestmark = pytest.mark.live
LIVE = os.path.join(ROOT, "tests", ".live")
PROMPT_CASES = [c for c in catalog.load() if c.get("prompt")]


@pytest.fixture(scope="module")
def results(fixture_catalog):
    llm = BedrockLLM()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {c["id"]: pool.submit(generate, c["prompt"], fixture_catalog, llm, offer_id=1001) for c in PROMPT_CASES}
        res = {k: f.result() for k, f in futs.items()}
    out = os.path.join(LIVE, "catalog")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(os.path.join(out, f))
    for k, r in res.items():
        if (r.get("rules") or {}).get("xml"):
            open(os.path.join(out, k + ".xml"), "w").write(ui_xml(r))
    json.dump(res, open(os.path.join(LIVE, "catalog.json"), "w"), indent=1)
    return res


@pytest.mark.parametrize("case", PROMPT_CASES, ids=[c["id"] for c in PROMPT_CASES])
def test_case(results, case):
    v, why = catalog.verdict(case, results[case["id"]])
    if v == "FLAGGED":
        pytest.xfail(why)
    assert v == "PASS", why


def test_summary(results):
    """Prints the per-group tally, latency, and which toolbox blocks the live outputs reached."""
    tally = collections.defaultdict(collections.Counter)
    seen, lat, rows = set(), [], []
    for c in PROMPT_CASES:
        r = results[c["id"]]
        v, why = catalog.verdict(c, r)
        tally[c["group"]][v] += 1
        xml = (r.get("rules") or {}).get("xml") or ""
        seen |= {f[6:] for f in catalog.features(xml) if f.startswith("block:")}
        d = r.get("diagnostics") or {}
        lat.append(d.get("latency_s") or 0)
        rows.append({"id": c["id"], "group": c["group"], "verdict": v, "why": why, "status": r.get("status"),
                     "latency_s": d.get("latency_s"), "repairs": d.get("repairs")})
    json.dump(rows, open(os.path.join(LIVE, "catalog_verdicts.json"), "w"), indent=1)
    for g, t in tally.items():
        print(f"{g:14s} PASS {t['PASS']:3d}  FLAGGED {t['FLAGGED']:3d}  FAIL {t['FAIL']:3d}")
    lat = sorted(x for x in lat if x)
    print(f"latency p50 {lat[len(lat) // 2]}s  p90 {lat[int(len(lat) * .9)]}s  max {lat[-1]}s")
    print("toolbox blocks reached:", len(seen & catalog.TOOLBOX), "of", len(catalog.TOOLBOX), sorted(seen & catalog.TOOLBOX))
    print("not reached:", sorted(catalog.TOOLBOX - seen))
