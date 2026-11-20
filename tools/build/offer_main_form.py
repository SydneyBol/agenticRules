"""Generate the Offer Main form rules the tools check set_offer_main against, from the sources (no hand-typed rules):
  offer name, subtitle, offer description: required and maxLength on the Offer Title, Offer Subtitle and Offer
  Description inputs (newui offer-details-form.component.html). The screen sets a generated value through ngModel,
  which maxLength does not cut, so an over-long value reaches bolapi (2026-11-20: a 751-character description, bolapi
  400 "no more than 500 characters" on offer_description, CharField max_length=500);
  frequency, offer_frequency_type: the choices of bolapi's Offer model (offers/models.py FREQUENCY_CHOICES,
  OFFER_FREQUENCY_CHOICES); both may be blank (blank=True there; the screen sends "").
The JSON records a hash of every source file read, so a test can tell when it is stale.

usage: python tools/build/offer_main_form.py [--ui ~/Downloads/newui-develop/gravty-ui] [--bolapi ~/Downloads/bolapi-develop]
       [--out <path to offer_main_form.json>]
writes offer_author/core/data/offer_main_form.json in this checkout unless --out names another file (the Pulsar
agent's copy: <ai repo>/apis/flask_apis/pulsar/pulsar_offer_agent/core/data/offer_main_form.json)
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
TEXT_FIELDS = ("offer_name", "subtitle", "offer_description")  # the text inputs set_offer_main fills


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
        **{field: input_rules(text["ui"], field) for field in TEXT_FIELDS},
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
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    spec = build(a.ui, a.bolapi)
    with open(a.out, "w") as f:
        json.dump(spec, f, indent=1)
        f.write("\n")
    print(a.out, json.dumps({k: v for k, v in spec.items() if k != "sources"}))


if __name__ == "__main__":
    main()
