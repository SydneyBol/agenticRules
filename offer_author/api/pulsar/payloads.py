"""What the Pulsar endpoints answer, in Pulsar's own contract, and what is logged beside it.

A canvas answer is exactly Pulsar's {xml (base64), raw, rule_id}. The XML is ASCII only: the stock upload path deletes
every char outside 0x20-0x7E, so the rest are character references. Everything else a run says about a part (status,
readback, assumptions, questions, unsupported items, the counter report) is traced in the log (`notes`).
"""

from __future__ import annotations

import base64
import html
import re
from typing import Any

from ...core.run import NOTE_KEYS
from ...domain import Problem
from .. import text

Res = dict[str, Any]

MEMBER_FREQ = {"day": "Per Day", "week": "Weekly", "month": "Per Month", "total": "Lifetime"}


def part_view(res: Res, part: str) -> Res:
    """What one screen's API returns of a run's result: its part's notes and its status.

    Args:
        res: The run's result, worded (api.text.public).
        part: offer_main, segment or rules.

    Returns:
        {assumptions, blocking_questions, unsupported, status}: built, draft (or needs_input when the part asks
        something); not built, unsupported or needs_input; error when the run failed.
    """
    notes = (res.get("by_part") or {}).get(part) or {k: res.get(k) or [] for k in NOTE_KEYS}
    if res.get("status") == "error":
        return dict(notes, status="error")
    built = {"offer_main": res.get("offer_main"), "segment": res.get("segment"), "rules": res.get("rules")}[part]
    status = (
        ("needs_input" if notes["blocking_questions"] else "draft")
        if built
        else "unsupported"
        if notes["unsupported"]
        else "needs_input"
    )
    return dict(notes, status=status)


def offer_main_metadata(om: Res, today_iso: str) -> Res:
    """The stock /pulsar/offer/metadata 'offer' body (generate_offer_metadata.py): the UI fills Offer Main from it."""
    freq = om.get("frequency") if om.get("frequency") not in (None, "none") else ""
    return {
        "offer_name": om["offer_name"],
        "subtitle": om.get("subtitle", ""),
        "offer_description": om.get("offer_description", ""),
        "term_and_condition": "<ul>" + "".join(f"<li>{html.escape(t)}</li>" for t in om.get("terms") or []) + "</ul>",
        "num_of_clients": om.get("num_of_clients") or None,
        "max_usage_per_user": om.get("max_usage_per_user") or None,
        "offer_frequency_type": om.get("offer_frequency_type", ""),
        "option_value": freq,
        "option_visible_name": MEMBER_FREQ.get(freq or "", ""),
        "start_date": (om.get("start_date") or today_iso[:10]) + "T00:00:00",
        "end_date": (om["end_date"] + "T23:59:00") if om.get("end_date") else None,
    }


def ascii_xml(xml: str) -> str:
    """The XML with every character outside printable ASCII as a character reference."""
    return re.sub(r"[^\x20-\x7E]", lambda m: f"&#{ord(m.group(0))};", xml)


FALLBACK = {
    "segment": "No member conditions found.",
    "rules": "I couldn't turn this into rules. Could you describe who earns what, and when?",
}


def error_message(res: Res, part: str) -> str:
    """Why that part was not built, in its own items."""
    if res["status"] == "error":
        return res.get("message") or "The rule generator failed. Please try again."
    v = part_view(res, part)
    lines = [
        *v["blocking_questions"],
        *(
            f"Can't build '{u['phrase']}': {u['reason']}"
            + (f" Try: {u['alternative']}" if u.get("alternative") else "")
            for u in v["unsupported"]
        ),
    ]
    return " ".join(lines) or FALLBACK.get(part, FALLBACK["rules"])


def xml_answer(xml: str, rule_id: Any) -> Res:
    """A canvas answer as Pulsar gives it: the XML base64-encoded and raw (ASCII only), and its rule id."""
    raw = ascii_xml(xml)
    return {"xml": base64.b64encode(raw.encode("ascii")).decode(), "raw": raw, "rule_id": rule_id}


def notes(res: Res, part: str) -> Res:
    """What a run says about one part, for the log: its status and items, the readback and counter report (rules)."""
    v = part_view(res, part)
    out: Res = {"status": v["status"], **{k: v[k] for k in NOTE_KEYS}}
    if part == "rules":
        report = (res.get("rules") or {}).get("counter_report")
        if report:  # the reasons worded, as the log is read
            report = dict(report, renamed=[dict(r, why=_worded(r["why"])) for r in report.get("renamed") or []])
        out.update(readback=res.get("readback"), counter_report=report)
    if res.get("status") == "error":
        out["message"] = res.get("message")
    return out


def _worded(why: Any) -> Any:
    """A Problem as the manager reads it; anything else as it is."""
    return text.word(why) if isinstance(why, Problem) else why
