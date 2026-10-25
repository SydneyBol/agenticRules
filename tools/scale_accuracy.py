"""Attribute and value picking at scale, live on Bedrock: tests/cases/scale.yaml against the scaled catalog
(tests/scaled_catalog.py: 1000 attributes, 507 stores, 5000 SKUs, 250 activity types), in two modes:

  inline  every value list written into the prompt, no lookup tool (the approach before lookup_values)
  lookup  long lists left out of the prompt, the model finds codes with lookup_values (current)

A case passes when the draft uses exactly the expected codes on each expected attribute, none of the forbidden
look-alike attributes, and asks a blocking question exactly when one is expected.
Run: AWS_PROFILE=... AWS_REGION=us-west-2 .venv/bin/python -I tools/scale_accuracy.py [inline|lookup|both] [--only=SC01,SC03]
Writes tests/.live/scale_accuracy.json.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
import offer_author.core.catalog as catalog_mod  # noqa: E402
from offer_author.ai.llm import BedrockLLM  # noqa: E402
from offer_author.ai.agent import generate  # noqa: E402
from scaled_catalog import build  # noqa: E402

CASES = os.path.join(ROOT, "tests", "cases", "scale.yaml")
OUT = os.path.join(ROOT, "tests", ".live", "scale_accuracy.json")


class NoLookup:
    """The model without the lookup tool (inline mode)."""

    def __init__(self, llm):
        self.llm, self.model = llm, llm.model

    def turn(self, system, messages, tools=None):
        return self.llm.turn(system, messages, tools=[t for t in tools if t["name"] != "lookup_values"])


def used(xml):
    """{"object.attribute": set(codes)} for every attribute the delivered blocks test, with the codes they compare to."""
    import xml.etree.ElementTree as ET
    from offer_author.core.tree import CATALOG_OBJECT
    out = {}
    for m in ET.fromstring(xml).iter() if xml else []:
        if m.tag.split("}")[-1] != "mutation" or not m.get("attribute") or m.get("object") not in CATALOG_OBJECT:
            continue
        codes = (m.get("value") or "").split(",") if m.get("operators") in ("in", "not_in", "==", "!=") else []
        out.setdefault(f"{CATALOG_OBJECT[m.get('object')]}.{m.get('attribute')}", set()).update(c for c in codes if c and c != "null")
    return out


def score(case, res):
    got = used((res.get("rules") or {}).get("xml"))
    problems = []
    for attr, codes in (case.get("picks") or {}).items():
        if attr not in got:
            problems.append(f"{attr} not used")
        elif codes and got[attr] != set(codes):
            problems.append(f"{attr} = {sorted(got[attr])}, expected {sorted(codes)}")
    for attr in case.get("forbid") or []:
        if attr in got:
            problems.append(f"look-alike {attr} used")
    asked = bool(res.get("blocking_questions"))
    if case["question"] and not asked:
        problems.append("expected a blocking question")
    if not case["question"] and asked:
        problems.append("unexpected question: " + " | ".join(res["blocking_questions"])[:200])
    if res.get("status") == "error":
        problems.append("error: " + res.get("message", ""))
    return problems, {k: sorted(v) for k, v in got.items()}


def run(mode, cases, cat, base):
    if mode == "inline":
        catalog_mod.INLINE_VALUES = catalog_mod.INLINE_ACTIVITY_TYPES = 10 ** 9
        llm = NoLookup(base)
    else:
        catalog_mod.INLINE_VALUES, catalog_mod.INLINE_ACTIVITY_TYPES = 40, 200
        llm = base
    prompt_chars = len(cat.prompt_text())
    generate(cases[0]["prompt"], cat, llm, offer_id=1001)  # warm the prompt cache once, so latencies compare like for like
    with ThreadPoolExecutor(max_workers=5) as pool:
        res = dict(zip([c["id"] for c in cases], pool.map(lambda c: generate(c["prompt"], cat, llm, offer_id=1001), cases)))
    rows = {}
    for c in cases:
        r = res[c["id"]]
        problems, got = score(c, r)
        d = r.get("diagnostics") or {}
        rows[c["id"]] = {"pass": not problems, "problems": problems, "used": got, "status": r.get("status"),
                         "questions": r.get("blocking_questions"), "latency_s": d.get("latency_s"), "usage": d.get("usage"),
                         "lookup_rounds": d.get("lookup_rounds"), "lookups": d.get("lookups"), "repairs": d.get("repairs")}
    return {"prompt_chars": prompt_chars, "rows": rows}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")] or ["both"]
    modes = ["inline", "lookup"] if args == ["both"] else args
    only = next((a.split("=", 1)[1].split(",") for a in sys.argv[1:] if a.startswith("--only=")), None)
    cases = [c for c in yaml.safe_load(open(CASES))["cases"] if not only or any(c["id"].startswith(o) for o in only)]
    cat, base = build(), BedrockLLM()
    report = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for mode in modes:
        t0 = time.time()
        report[mode] = run(mode, cases, cat, base)
        report[mode]["wall_s"] = round(time.time() - t0, 1)
        json.dump(report, open(OUT, "w"), indent=1)
    for mode in modes:
        rows = report[mode]["rows"]
        lat = sorted(r["latency_s"] or 0 for r in rows.values())
        inp = [sum((r["usage"] or {}).get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
               for r in rows.values()]
        print(f"\n==== {mode}: {sum(r['pass'] for r in rows.values())}/{len(rows)} pass   prompt {report[mode]['prompt_chars']} chars   "
              f"latency p50 {lat[len(lat) // 2]}s max {lat[-1]}s   input tokens/case avg {sum(inp) // len(inp)}")
        for k, r in rows.items():
            print(f"  {k:32s} {'PASS' if r['pass'] else 'FAIL'}  lookups {r['lookup_rounds'] or 0}  {r['latency_s']}s  "
                  + ("; ".join(r["problems"]) if r["problems"] else ""))


if __name__ == "__main__":
    main()
