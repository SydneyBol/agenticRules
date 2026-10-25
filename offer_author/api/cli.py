"""offer-xml: English description of a reward offer -> Blockly XML file the stock gravty-ui loads.

  offer-xml "Gold members get 100 points on weekend spends over $50" -o gold.xml
  offer-xml --batch examples/prompts.txt --out-dir out/

Each .xml has a <name>.json next to it: status, readback, assumptions, blocking questions, unsupported items,
proposed offer settings, the counters (MTO payloads) the UI will create, and latency. Exit code 0 for draft or
needs_input, 2 for unsupported, 1 for error.
"""
import argparse
import json
import os
import re
import sys

from ..core.catalog import Catalog
from ..ai.agent import generate
from .pulsar import ascii_xml, ui_xml

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXIT = {"draft": 0, "needs_input": 0, "unsupported": 2, "error": 1}
SEVERITY = {0: 0, 2: 1, 1: 2}  # batch exit code: an error outranks unsupported


def load_catalog(a):
    if a.catalog:
        return Catalog.from_json(a.catalog)
    return Catalog.from_bolapi_dumps(a.catalog_dir, a.tenant, a.sponsor, a.account or "12", a.tag or "BASE")


def slugify(text, n=40):
    """File name from the prompt, cut at a word boundary."""
    words, out = re.sub(r"[^a-z0-9]+", " ", text.lower()).split(), ""
    for w in words:
        if out and len(out) + 1 + len(w) > n:
            break
        out = f"{out}-{w}" if out else w[:n]
    return out or "offer"


def write(res, xml_path, plain):
    rules = res.get("rules") or {}
    if rules.get("xml"):
        xml = ascii_xml(rules["xml"]) if plain else ui_xml(res)
        with open(xml_path, "w", encoding="ascii") as f:
            f.write(xml)
    side = {k: res.get(k) for k in ("status", "message", "readback", "assumptions", "blocking_questions", "unsupported", "offer_settings")}
    side.update(counters=rules.get("counters", []), rule_count=rules.get("rule_count"), block_count=rules.get("block_count"),
                latency_s=(res.get("diagnostics") or {}).get("latency_s"), repairs=(res.get("diagnostics") or {}).get("repairs"))
    with open(os.path.splitext(xml_path)[0] + ".json", "w") as f:
        json.dump(side, f, indent=1)
    return side


def main(argv=None, llm=None):
    ap = argparse.ArgumentParser(prog="offer-xml", description=__doc__.split("\n")[0])
    ap.add_argument("prompt", nargs="?", help="the offer in plain English")
    ap.add_argument("-o", "--out", help="output .xml path (default: stdout)")
    ap.add_argument("--batch", help="file with one prompt per line (# comments allowed)")
    ap.add_argument("--out-dir", default="out", help="output folder for --batch")
    ap.add_argument("--catalog", help="catalog JSON (e.g. catalogs/fixture.json) instead of saved bolapi responses")
    ap.add_argument("--catalog-dir", default=os.path.join(HERE, "catalogs", "devintam1", "bolapi"))
    ap.add_argument("--tenant", default="devintam1")
    ap.add_argument("--sponsor", type=int, default=1)
    ap.add_argument("--account", help="loyalty account id for points (default: the catalog's; 12 for devintam1)")
    ap.add_argument("--tag", help="reward tag code (default: the catalog's; BASE for devintam1)")
    ap.add_argument("--offer-id", type=int, help="offer id to stamp into the rules (the UI re-stamps it on upload)")
    ap.add_argument("--plain", action="store_true", help="omit the pinned note for the manager on the offer block")
    a = ap.parse_args(argv)
    if not a.prompt and not a.batch:
        ap.error("give a prompt or --batch FILE")
    if llm is None:
        from ..ai.llm import BedrockLLM
        llm = BedrockLLM()
    cat = load_catalog(a)

    def run(prompt):
        return generate(prompt, cat, llm, offer_id=a.offer_id, account_id=a.account)

    if a.batch:
        os.makedirs(a.out_dir, exist_ok=True)
        prompts = [p.strip() for p in open(a.batch) if p.strip() and not p.lstrip().startswith("#")]
        worst = 0
        for i, p in enumerate(prompts, 1):
            res = run(p)
            side = write(res, os.path.join(a.out_dir, f"{i:02d}-{slugify(p)}.xml"), a.plain)
            print(f"{i:02d} {side['status']:12s} {side['latency_s']}s rules={side['rule_count']} blocks={side['block_count']}  {p}")
            worst = max(worst, EXIT.get(side["status"], 1), key=SEVERITY.get)
        return worst

    res = run(a.prompt)
    if a.out:
        side = write(res, a.out, a.plain)
        print(f"{side['status']} {side['latency_s']}s -> {a.out} (+ {os.path.splitext(a.out)[0]}.json)", file=sys.stderr)
    else:
        rules = res.get("rules") or {}
        if rules.get("xml"):
            print(ascii_xml(rules["xml"]) if a.plain else ui_xml(res))
        print(json.dumps({k: res.get(k) for k in ("status", "message", "readback", "assumptions", "blocking_questions", "unsupported")},
                         indent=1), file=sys.stderr)
    return EXIT.get(res.get("status"), 1)


if __name__ == "__main__":
    sys.exit(main())
