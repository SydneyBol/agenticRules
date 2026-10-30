"""Generate the Offer Main form rules the tools check set_offer_main against, from the sources (no hand-typed rules):
  offer name: required and maxLength on the Offer Title input (newui offer-details-form.component.html);
  frequency, offer_frequency_type: the choices of bolapi's Offer model (offers/models.py FREQUENCY_CHOICES,
  OFFER_FREQUENCY_CHOICES); both may be blank (blank=True there; the screen sends "").
The JSON records a hash of every source file read, so a test can tell when it is stale.

usage: python tools/build/offer_main_form.py [--ui ~/Downloads/newui-develop/gravty-ui] [--bolapi ~/Downloads/bolapi-develop]
writes offer_author/core/data/offer_main_form.json in $OFFER_AUTHOR_REPO (default ~/PycharmProjects/offer-xml-generator)
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)  # this checkout, which they write into
OUT = os.path.join(ROOT, "offer_author", "core", "data", "offer_main_form.json")
DETAILS = "src/app/workspaces/offer/modules/offer-setup/screens/offer-main/components/offer-main-forms/offer-details-form/offer-details-form.component.html"
MODELS = "offers/models.py"


def input_rules(html, model_field):
    """required / maxLength of the gravty-custom-text-input bound to offerCreationData?.<model_field>."""
    tag = next(
        t for t in re.findall(r"<gravty-custom-text-input\b[^>]*>", html) if f"offerCreationData?.{model_field}" in t
    )
    max_len = re.search(r'\[maxLength\]="(\d+)"', tag)
    return {"required": '[required]="true"' in tag, "max_length": int(max_len.group(1)) if max_len else None}


def choices(models_py, name):
    """The codes of a Django choices tuple: NAME = ((CONST, label), ...) with CONST = "code" defined in the class."""
    body = re.search(rf"{name} = \((.*?)\n    \)", models_py, re.DOTALL).group(1)
    consts = dict(re.findall(r"""^\s*([A-Z_]+) = ["']([^"']+)["']""", models_py, re.MULTILINE))
    return [consts[c] for c in re.findall(r"\(\s*([A-Z_]+)\s*,", body)]


def build(ui, bolapi):
    paths = {"ui": os.path.join(ui, DETAILS), "bolapi": os.path.join(bolapi, MODELS)}
    text = {k: Path(p).read_text() for k, p in paths.items()}
    return {
        "offer_name": input_rules(text["ui"], "offer_name"),
        "frequency": {"choices": choices(text["bolapi"], "FREQUENCY_CHOICES"), "blank": True},
        "offer_frequency_type": {"choices": choices(text["bolapi"], "OFFER_FREQUENCY_CHOICES"), "blank": True},
        "sources": {
            f"{k}:{DETAILS if k == 'ui' else MODELS}": hashlib.sha256(t.encode()).hexdigest()[:16]
            for k, t in text.items()
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ui", default=os.path.expanduser("~/Downloads/newui-develop/gravty-ui"))
    ap.add_argument("--bolapi", default=os.path.expanduser("~/Downloads/bolapi-develop"))
    a = ap.parse_args()
    spec = build(a.ui, a.bolapi)
    with open(OUT, "w") as f:
        json.dump(spec, f, indent=1)
        f.write("\n")
    print(OUT, json.dumps({k: v for k, v in spec.items() if k != "sources"}))


if __name__ == "__main__":
    main()
