"""offer-harvest: saved offers into the tenant's history of earlier offers (core/tenant/harvest.py).

So the reference offers the agent is shown grow with what managers save and launch.

  offer-harvest --tenant devintbf2 --pulsar <copy of S3 <program>/gpt/data_store>
  offer-harvest --tenant devintbf2 --saved saved.jsonl     # [{id, name, description, xml, updated, status}]

Sources: Pulsar's collected offers (data_store/offer/<id>/data.csv: offers_offer columns, offerXML base64 from bolapi
offers/<id>/drl/, offer_Prompt) or a JSONL of saved offers. Each version is vetted against the tenant's catalog
(core.tenant.harvest.vet) before it replaces what is stored, and stored with its sub-prompt, made by Pulsar's own code
as Pulsar's RAG makes it (`pulsar_subprompt`; needs the pulsar extra: pip install -e '.[pulsar]'). Prints the report as
JSON; exit 0.
"""

import argparse
import base64
import contextlib
import csv
import glob
import json
import logging
import os
import sys
import types
from collections.abc import Callable, Iterator
from typing import Any

from ..core.settings import ROOT, Settings
from ..core.tenant.harvest import PromptLog, harvest, read
from .cli import load_catalog
from .text import word

PULSAR = os.path.join(ROOT, "vendor", "pulsar_rag")  # Pulsar's sub-prompt code, as reviewed (PulsarRAG-Review.md)

csv.field_size_limit(sys.maxsize)
LIVE = ("launched", "Validated", "")  # Pulsar collects launched and suspended offers and rewrites both to "Validated"


def last_prompt(text: str | None) -> str:
    """offer_Prompt keeps every prompt typed for the offer, one per line, with PLACEHOLDER_TEXT for none."""
    lines = [
        ln.strip()
        for ln in (text or "").splitlines()
        if ln.strip() and "PLACEHOLDER" not in ln and "NoSuchKey" not in ln
    ]
    return lines[-1] if lines else ""


def pulsar_subprompt() -> Callable[[str], str]:
    """subprompt(xml): Pulsar's lines for a saved XML (xml_to_subprompt).

    Joined as prompt_validator.rag_data_fetch joins them. Unlike Pulsar the XML is not templatized first: a stored
    offer is vetted against this tenant's catalog, so its codes are the tenant's. "" when Pulsar cannot read the XML.
    """
    sys.path.insert(0, PULSAR)
    log = types.ModuleType("general.loggerutils")  # Pulsar's logging setup is not part of the reviewed files
    log.get_logger = lambda *a, **k: logging.getLogger("pulsar")  # type: ignore[attr-defined]
    sys.modules.setdefault("general.loggerutils", log)
    from pulsar.data_validation.sub_prompts.blockwise_subprompt import xml_to_subprompt

    def subprompt(xml: str) -> str:
        with contextlib.chdir(PULSAR):  # Pulsar reads its block schema relative to its root
            return "\n".join(line.replace(",,,", ",").strip() for line, _ in xml_to_subprompt(xml) or [])

    return subprompt


def pulsar_saved(data_store: str) -> Iterator[dict[str, Any]]:
    """Every saved version in a copy of <program>/gpt/data_store, as harvest() takes them."""
    for path in sorted(glob.glob(os.path.join(data_store, "offer", "*", "data.csv"))):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("offer_type") != "reward" or r.get("status") not in LIVE:
                    continue
                try:
                    xml = base64.b64decode(r.get("offerXML") or "").decode()
                except ValueError:
                    xml = ""
                yield {
                    "id": r["id"],
                    "name": r.get("offer_name") or "",
                    "description": (r.get("offer_description") or "").strip(),
                    "prompt": last_prompt(r.get("offer_Prompt")),
                    "xml": xml if xml.lstrip().startswith("<xml") else "",
                    "updated": r.get("updated_ts") or "",
                    "launched": (r.get("launch_date") or r.get("start_date") or "")[:10],
                }


def main(argv: list[str] | None = None) -> int:
    """Harvests a tenant's saved offers and prints the report."""
    ap = argparse.ArgumentParser(prog="offer-harvest", description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--pulsar", help="a copy of Pulsar's <program>/gpt/data_store folder")
    ap.add_argument("--saved", help="JSONL of saved offers: {id, name, description, xml, updated, status}")
    ap.add_argument("--history", help="history folder (default: $OFFER_AUTHOR_HISTORY)")
    ap.add_argument("--catalog", help="catalog JSON instead of saved bolapi responses")
    ap.add_argument("--catalog-dir", help="saved bolapi responses (default: $OFFER_AUTHOR_CATALOGS/<tenant>/bolapi)")
    ap.add_argument("--no-catalog", action="store_true", help="vet structure only (a program with no catalog here)")
    ap.add_argument("--sponsor", type=int, default=1)
    ap.add_argument("--account")
    ap.add_argument("--tag")
    a = ap.parse_args(argv)
    settings = Settings.from_env()
    root = a.history or settings.history
    if not root or not (a.pulsar or a.saved):
        ap.error("give --pulsar or --saved, and --history (or OFFER_AUTHOR_HISTORY)")
    saved = list(pulsar_saved(a.pulsar)) if a.pulsar else []
    if a.saved:
        saved += [r for r in read(a.saved) if r.get("status", "launched") in LIVE]
    catalog = None if a.no_catalog else load_catalog(a, settings)
    try:
        subprompt = pulsar_subprompt()
    except ImportError as e:
        ap.error(f"Pulsar's sub-prompt code needs the pulsar extra (pip install -e '.[pulsar]'): {e}")
    report = harvest(
        saved, os.path.join(root, a.tenant, "offers.jsonl"), catalog, PromptLog(root).last(a.tenant), subprompt
    )
    report["rejected"] = {k: [word(p) for p in problems] for k, problems in report["rejected"].items()}
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
