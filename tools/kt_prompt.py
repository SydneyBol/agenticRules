"""Copy the engine/Blockly KT sections the blocks-mode model reads into offer_author/blocks/assets/kt.md.

The KT lives in ~/Documents/Gravty (written while studying gravty-ui, bolapi and the engine); this keeps a snapshot
of the execution-relevant sections next to the code that sends it, minus the docs' error logs and history.

usage: python tools/kt_prompt.py [--docs ~/Documents/Gravty]
"""
import argparse
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "offer_author", "blocks", "assets", "kt.md")
# (doc, headings to keep: a kept heading brings its body and sub-headings until the next heading of its level or higher)
KEEP = [
    ("OfferRules-Blockly.md", ["### Runtime evaluation", "## 4. Blockly capability catalog", "## 5. Worked example",
                               "## 6. Limits and sharp edges"]),
    ("Engine.md", ["## 3. How an offer is triggered for a BIT", "## 4. How the result is saved",
                   "## Runtime behaviours confirmed by the local harness"]),
    ("RulesXml-FilterStandard.md", ["## Purpose", "## Why it is safe"]),
]
SKIP = re.compile(r"^#+ .*(Errors encountered|Results|Tests)", re.I)


def sections(text, keep):
    out, on, level = [], False, 0
    for line in text.splitlines():
        m = re.match(r"^(#+) ", line)
        if m:
            n = len(m.group(1))
            if on and n <= level:
                on = False
            if not on and any(line.startswith(k) for k in keep):
                on, level = True, n
            if on and SKIP.match(line):
                on = False
        if on:
            out.append(line)
    return "\n".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=os.path.expanduser("~/Documents/Gravty"))
    a = ap.parse_args()
    parts = ["# How offer rules execute (KT extract: gravty-ui, bolapi, engine)"]
    for doc, keep in KEEP:
        parts.append(f"\n<!-- from {doc} -->\n" + sections(open(os.path.join(a.docs, doc)).read(), keep))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write("\n".join(parts) + "\n")
    print(OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
