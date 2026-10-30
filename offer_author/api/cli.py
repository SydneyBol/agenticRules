"""offer-xml: English description of a reward offer -> Blockly XML file the stock gravty-ui loads.

  offer-xml "Gold members get 100 points on weekend spends over $50" -o gold.xml
  offer-xml --batch examples/prompts.txt --out-dir out/

The output is the rules XML only; what the run says about it (status, readback, assumptions, questions, unsupported
items, the counters) is logged to stderr. Exit code 0 for draft or needs_input, 2 for unsupported, 1 for error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from ..ai.agent import generate
from ..core.settings import Settings
from ..core.tenant.catalog import Catalog
from .pulsar import ascii_xml, notes
from .text import public

log = logging.getLogger("offer_author.cli")

EXIT = {"draft": 0, "needs_input": 0, "unsupported": 2, "error": 1}
SEVERITY = {0: 0, 2: 1, 1: 2}  # batch exit code: an error outranks unsupported


def load_catalog(a: argparse.Namespace, settings: Settings) -> Catalog:
    """--catalog (a catalog JSON), else --catalog-dir, else the tenant's dumps under OFFER_AUTHOR_CATALOGS."""
    if a.catalog:
        return Catalog.from_json(a.catalog)
    folder = a.catalog_dir or (settings.catalogs and os.path.join(settings.catalogs, a.tenant, "bolapi"))
    if not folder:
        raise SystemExit("offer-xml: give --catalog, --catalog-dir or OFFER_AUTHOR_CATALOGS (the tenant dumps folder)")
    return Catalog.from_bolapi_dumps(folder, a.tenant, a.sponsor, a.account or "12", a.tag or "BASE")


def slugify(text: str, n: int = 40) -> str:
    """File name from the prompt, cut at a word boundary."""
    words, out = re.sub(r"[^a-z0-9]+", " ", text.lower()).split(), ""
    for w in words:
        if out and len(out) + 1 + len(w) > n:
            break
        out = f"{out}-{w}" if out else w[:n]
    return out or "offer"


def answer(res: dict[str, Any], prompt: str, out: str | None) -> int:
    """Logs what the run says, writes its rules XML (to out, else stdout); returns the exit code."""
    v = notes(res, "rules")
    log.info("%s %s", prompt[:80], json.dumps(v, default=str))
    xml = (res.get("rules") or {}).get("xml")
    if xml and out:
        Path(out).write_text(ascii_xml(xml), encoding="ascii")
    elif xml:
        print(ascii_xml(xml))
    return EXIT.get(v["status"], 1)


def main(argv: list[str] | None = None, llm: Any = None) -> int:
    """The CLI: one prompt, or a batch; returns the exit code."""
    ap = argparse.ArgumentParser(prog="offer-xml", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("prompt", nargs="?", help="the offer in plain English")
    ap.add_argument("-o", "--out", help="output .xml path (default: stdout)")
    ap.add_argument("--batch", help="file with one prompt per line (# comments allowed)")
    ap.add_argument("--out-dir", default="out", help="output folder for --batch")
    ap.add_argument("--catalog", help="catalog JSON instead of saved bolapi responses")
    ap.add_argument("--catalog-dir", help="saved bolapi responses (default: $OFFER_AUTHOR_CATALOGS/<tenant>/bolapi)")
    ap.add_argument("--tenant", default="devintam1")
    ap.add_argument("--sponsor", type=int, default=1)
    ap.add_argument("--account", help="loyalty account id for points (default: the catalog's; 12 for devintam1)")
    ap.add_argument("--tag", help="reward tag code (default: the catalog's; BASE for devintam1)")
    ap.add_argument("--offer-id", type=int, help="offer id to stamp into the rules (the UI re-stamps it on upload)")
    a = ap.parse_args(argv)
    if not a.prompt and not a.batch:
        ap.error("give a prompt or --batch FILE")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if llm is None:
        from ..ai.model import make_llm

        llm = make_llm()
    settings = Settings.from_env()
    cat = load_catalog(a, settings)

    def run(prompt: str) -> dict[str, Any]:
        return public(generate(prompt, cat, llm, offer_id=a.offer_id, account_id=a.account, settings=settings))

    if not a.batch:
        return answer(run(a.prompt), a.prompt, a.out)
    os.makedirs(a.out_dir, exist_ok=True)
    lines = Path(a.batch).read_text().splitlines()
    prompts = [p.strip() for p in lines if p.strip() and not p.lstrip().startswith("#")]
    codes = [answer(run(p), p, os.path.join(a.out_dir, f"{i:02d}-{slugify(p)}.xml")) for i, p in enumerate(prompts, 1)]
    return max(codes, key=lambda code: SEVERITY[code], default=0)


if __name__ == "__main__":
    sys.exit(main())
