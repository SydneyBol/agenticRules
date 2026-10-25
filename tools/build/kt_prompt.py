"""Copy the engine/Blockly KT sections the agent reads into offer_author/ai/context/kt.md.

The KT lives in ~/Documents/Gravty (written while studying gravty-ui, bolapi and the engine); this keeps a snapshot
of the execution-relevant sections next to the code that sends it, minus the docs' error logs and history.
The compiled rules (DRL) are private: the model learns how the engine runs the logic, never the rule text. `scrub`
drops DRL code and DRL-level lines and puts in BEHAVIOUR what each dropped part says about execution.

usage: python tools/build/kt_prompt.py [--docs ~/Documents/Gravty]
"""
import argparse
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "offer_author", "ai", "context", "kt.md")
# (doc, headings to keep: a kept heading brings its body and sub-headings until the next heading of its level or higher)
KEEP = [
    ("OfferRules-Blockly.md", ["### Runtime evaluation", "## 4. Blockly capability catalog", "## 5. Worked example",
                               "## 6. Limits and sharp edges"]),
    ("Engine.md", ["## 3. How an offer is triggered for a BIT", "## 4. How the result is saved",
                   "## Runtime behaviours confirmed by the local harness"]),
    ("RulesXml-FilterStandard.md", ["## Purpose", "## Why it is safe"]),
]
SKIP = re.compile(r"^#+ .*(Errors encountered|Results|Tests)", re.I)
# rule text: Drools/DRL syntax and the engine API the generated rules call
DRL = re.compile(r"\$activity|\$segment|readAttribute[NSD]?\b|pointsAction|assignPrivilege\(|ReadHelper|getActionHelper|"
                 r"get(Header|Line|Payment)ReadHelper|DateUtils\.|\$\{|^rule \"|\bsalience\b|\bLHS\b|kSession|"
                 r"NoThirdPartyFoundException|EngineLoggerV2|Emitted DRL|Generated DRL|DRL string|OfferDrl|Offer\.drl|\.drl\b|"
                 r"Emitted Java|Java mapping|What each layer holds|\.contentEquals|\(rhs\)|degrade to")
# DRL-level wording in a line whose point is behaviour: said in engine terms instead
REWRITE = {"`readAttributeD` uses the program timezone": "date conditions read dates in the program timezone",
           "compile to the right DRL": "compile",
           "Source: `offer-author-spike/engine-harness` (real Drools": "Source: the engine harness (real Drools",
           '`readAttributeN("member",null,"MA_O",…)` returns 0.0 and `readAttributeS` returns ""':
           "a number condition on it sees 0 and a text condition an empty value"}
# what a dropped DRL passage says about execution, in engine terms; inserted after the line that starts its section
BEHAVIOUR = {
    "| `expiry_conditions`": "\nHow text comparisons run: = / matches is an exact, case-sensitive match; contains, starts "
    "with and ends with test part of the text; on an attribute that holds several values, = holds when any of them "
    "equals the value.",
    "### Action vocabulary": "How actions run: Reward Points credits the computed quantity to the loyalty account under "
    "the reward tag; Redeem / Reset / Return / Expire Points debit, zero, return or expire points; Assign Privilege "
    "issues the privilege product (usage policy, how many, validity dates). Every action records the name of the rule "
    "that fired it.",
    "### Rule wrappers": "How each rule wrapper runs, per BIT:\n\n| Block | Runs |\n|---|---|\n"
    "| `rule` | once per BIT on its header attributes: when the conditions hold, the actions run once |\n"
    "| `rule_line` | once per line item: conditions and actions see that line (l_* and product attributes); actions can run per line |\n"
    "| `payment_rule` | once per payment item of the BIT |\n"
    "| `rule_award` | award offers: redeems the award price and assigns each product |\n\n"
    "A condition on a third party the member does not have skips that rule (logged), it does not fail the BIT.",
    "## 5. Worked example": "In the engine: the filter (activity type SPEND, sponsor X) admits the BIT to the activity; "
    "the rule's condition (amount >= 100, month 12) is checked on the BIT header; when it holds, points = amount x 2 are "
    "credited to the account under tag BASE, once per BIT.",
}


def scrub(text):
    out, fence, block = [], False, []
    for line in text.splitlines():
        if line.startswith("```"):
            if fence:  # a code block is kept only when it holds no rule text (the canvas sketch, the phase order)
                if not any(DRL.search(b) for b in block):
                    out += ["```" + block[0]] + block[1:] + ["```"] if block else []
                fence, block = False, []
            else:
                fence, block = True, [line[3:]]
            continue
        if fence:
            block.append(line)
            continue
        for old, new in REWRITE.items():
            line = line.replace(old, new)
        if DRL.search(line):
            continue
        out.append(line)
        for head, text_ in BEHAVIOUR.items():
            if line.startswith(head):
                out += ["", text_]
    # a table whose rows were all rule text: drop its header and separator too
    sep = lambda i: re.match(r"^\|[-| :]+\|$", out[i]) is not None  # noqa: E731
    row = lambda i: 0 <= i < len(out) and out[i].startswith("|") and not sep(i)  # noqa: E731
    gone = {j for i in range(len(out)) if sep(i) and not (row(i - 1) and row(i + 1)) for j in (i - 1, i) if j == i or row(j)}
    out = [ln for i, ln in enumerate(out) if i not in gone]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out).replace("DRL", "compiled rules"))


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
    open(OUT, "w").write(scrub("\n".join(parts)) + "\n")
    print(OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
