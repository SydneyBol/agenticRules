"""Block grammar check: which block types each statement input accepts (setCheck in the gravty-ui block code)."""
import xml.etree.ElementTree as ET

NS = "{https://developers.google.com/blockly/xml}"
ALLOWED = {
    "activities": {"activity"}, "events": {"event"}, "ACTIVITY_CRITERIA": {"activity_criteria", "or"},
    "RULES": {"rule", "rule_line", "payment_rule"}, "CONDITION": {"if_generic_adaptive", "or"},
    "ACTION": {"action", "rule", "rule_line"}, "OPERAND1": {"if_generic_adaptive", "activity_criteria", "or"},
    "OPERAND2": {"if_generic_adaptive", "activity_criteria", "or"},
}


def check(xml):
    root = ET.fromstring(xml)
    problems = []
    for st in root.iter(NS + "statement"):
        name = st.get("name")
        b = st.find(NS + "block")
        while b is not None:
            if name in ALLOWED and b.get("type") not in ALLOWED[name]:
                problems.append(f"{b.get('type')} not allowed in {name}")
            nxt = b.find(NS + "next")
            b = nxt.find(NS + "block") if nxt is not None else None
    return problems
