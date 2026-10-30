"""The test-case catalog (tests/cases/*.yaml + the 16 scenarios in tests/fixtures) and the checks shared by the live and
e2e runs.

Verdicts per case:
  PASS     every expectation met
  FLAGGED  the case needs tenant data the catalog does not carry (needs_catalog) and the service flagged it instead
           of inventing codes: acceptable, reported separately so missing capability stays visible
  FAIL     anything else
"""

import os
import re

import yaml

TESTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(TESTS, "cases")
FILES = ["blocks.yaml", "logic.yaml", "combinations.yaml", "counters.yaml", "negative.yaml"]

TOOLBOX = {  # offer-rules.config.ts:30-43
    "offer_generic",
    "activity",
    "activity_criteria",
    "event",
    "or",
    "rule",
    "rule_line",
    "payment_rule",
    "if_generic_adaptive",
    "if_payment_attribute",
    "action",
    "action_third_party",
    "static_value",
    "static_string_value",
    "arithmetic_generic",
    "arithmetic_date",
    "current_date_block",
    "static_date",
    "static_date_time",
    "currency_converter",
    "activity_attribute",
    "member_attribute",
    "product_attribute",
    "payment_attribute",
    "airport_attribute",
}


def load():
    """[{id, group, prompt?, api?, expect, sequences?, needs_catalog?}] in file order."""
    out = []
    for f in FILES:
        for c in yaml.safe_load(open(os.path.join(CASES, f)))["cases"]:
            out.append(dict(c, group=f[:-5]))
    for s in yaml.safe_load(open(os.path.join(TESTS, "fixtures", "scenarios.yaml")))["scenarios"]:
        exp = {"status": ["draft", "needs_input"]}
        if s.get("expect_questions"):
            exp = {"status": ["needs_input", "draft"], "flag": True}
        if s.get("expect_unsupported"):
            exp = {"status": ["unsupported", "needs_input"], "flag": True}
        out.append(
            {
                "id": s["id"],
                "group": "spike",
                "prompt": s["prompt"],
                "expect": exp,
                "sequences": s.get("sequences"),
                "needs_catalog": ["periodic EVENT bits"] if s["id"] == "S11_monthly_streak" else None,
            }
        )
    return out


def slots():
    return yaml.safe_load(open(os.path.join(CASES, "blocks.yaml"))).get("slots", {})


def features(xml, counters=()):
    f = {"block:" + t for t in re.findall(r'<block type="([a-z_]+)"', xml)}
    f |= {"obj:" + o for o in re.findall(r'\bobject="([a-z_]+)"', xml)}
    f |= {"act:" + a for a in re.findall(r'attribute_type_input="(give_points|give_privilege|update_attribute)"', xml)}
    f |= {
        "op:" + o.replace("&gt;", ">").replace("&lt;", "<")
        for o in re.findall(r'\boperators="([^"]+)"', xml)
        if o != "null"
    }
    f |= {"counter:" + c["counter_type"] for c in counters}
    f |= {f"mode:{k}={v}" for k, v in re.findall(r'<field name="([A-Za-z_]+)">([^<]*)</field>', xml)}
    f |= {
        f"mode:{k}={v}"
        for k, v in re.findall(r'\b([a-z_]+)="([^"]*)"', xml)
        if k not in ("type", "id", "name", "xmlns")
    }
    # the action type and the member attribute level are both saved as attribute_type_input
    f |= {"mode:action_type=" + v for v in re.findall(r'attribute_type_input="([a-z_]+)"', xml)}
    f |= {
        "mode:attribute_level=" + v
        for v in re.findall(r'(?:attribute_type_input|attribute_level|mta_type_value)="(MA_[A-Z]+)"', xml)
    }
    return f


def verdict(case, res):
    """(verdict, reason) for one prompt case against the service result."""
    exp = case["expect"]
    rules = res.get("rules") or {}
    xml = rules.get("xml") or ""
    flagged = bool(res.get("blocking_questions") or res.get("unsupported")) or res.get("status") in (
        "needs_input",
        "unsupported",
    )
    for bad in exp.get("no_xml_contains", []):
        if bad in xml:
            return "FAIL", f"output contains {bad}"
    if exp.get("no_xml") and xml:
        return "FAIL", "returned rules for a non-offer"
    blocks = set(re.findall(r'<block type="([a-z_]+)"', xml))
    if blocks - TOOLBOX:
        return "FAIL", f"blocks outside the toolbox: {sorted(blocks - TOOLBOX)}"
    if rules.get("grammar_problems"):
        return "FAIL", f"grammar: {rules['grammar_problems'][:3]}"
    want = set(exp.get("features", []))
    got = features(xml, rules.get("counters", []))
    missing = sorted(want - got)
    status_ok = res.get("status") in exp.get("status", ["draft"])
    if exp.get("flag") and not flagged:
        return "FAIL", "expected a question or unsupported item"
    if status_ok and not missing:
        return "PASS", ""
    if (
        case.get("needs_catalog") and flagged
    ):  # drafts never carry codes outside the catalog (validated before compiling)
        return "FLAGGED", f"needs {case['needs_catalog']}: {first_reason(res)}"
    return "FAIL", (f"status {res.get('status')} not in {exp.get('status')}; " if not status_ok else "") + (
        f"missing {missing}; " if missing else ""
    ) + first_reason(res)


def first_reason(res):
    u = res.get("unsupported") or []
    q = res.get("blocking_questions") or []
    if u:
        return f"unsupported: {u[0].get('phrase')}: {u[0].get('reason')}"[:220]
    if q:
        return f"question: {q[0]}"[:220]
    return res.get("message") or ""
