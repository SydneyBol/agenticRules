"""The engine check of a run's canvases: the request to the verifier service, and its verdict.

The verifier (a separate deployable) owns the stock screens' compile and the engine; this service only sends it the
canvases the run built, Offer Main's dates and limits, and the test cases, and reads back what each transaction gave.
The cases' members are mocked in the request, as the stock Offer Testing mocks them: nothing is read from the tenant.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .run import Run


class VerifierUnavailableError(Exception):
    """The verifier could not be reached or answered with an error."""


def request(run: Run, scenarios: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The verifier request for the run's canvases (the clean compose of each, else the last); None without rules."""
    cv, cat = run.canvases, run.catalog
    rules = cv.clean.get("rules") or cv.last.get("rules")
    if rules is None or not rules.built:
        return None
    seg = cv.clean.get("segment") or cv.last.get("segment")
    om = run.offer_main.value or {}
    return {
        "tenant": cat.tenant,
        "sponsor_id": cat.sponsor_id,
        "privileges": sorted(str(p) for p in cat.privileges),
        "rules_xml": rules.xml,
        "segment_xml": seg.xml if seg is not None and seg.built else None,
        "offer_main": {
            "start_date": _saved(om.get("start_date"), "T00:00:00"),
            "end_date": _saved(om.get("end_date"), "T23:59:00"),
            "max_usage_per_user": om.get("max_usage_per_user") or 0,
            "frequency": om.get("frequency") or "total",
        },
        "locations": stores(run),
        "scenarios": scenarios,
        # the program data a transaction reads (airports, airport pairs, conversion rates) and its switches
        "reference": {k: v for k, v in cat.reference.items() if k != "features"},
        "program": {k: v for k, v in (("features", cat.reference.get("features")), ("timezone", cat.timezone)) if v},
    }


def _saved(day: str | None, time_of_day: str) -> str | None:
    """An Offer Main date as the screen saves it: a bare day gets the time the UI adds (start 00:00, end 23:59).

    The engine reads the offer's dates with their time, so a bare end date would end the offer at the start of its
    last day (2026-11-05, W01: a spend at 23:30 on the last day was refused in the check, though the saved offer runs
    to 23:59).
    """
    if not day:
        return None
    return day + time_of_day if len(day) == 10 else day


def stores(run: Run) -> list[str]:
    """The tenant's active store codes: the values of the activity's h_location.

    The engine then refuses an unknown store, as the stock request validation does; none when the catalog has none
    (every store is then known).
    """
    loc = ((run.catalog.attributes or {}).get("activity") or {}).get("h_location")
    return sorted(str(code) for code in (loc.values if loc is not None and loc.values else {}))


def verify(url: str, body: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    """POST /verify: {ok, compile, scenarios, mismatches, stubbed_infra_calls, latency_s}.

    Raises:
        VerifierUnavailableError: The service could not be reached, or refused the request.
    """
    req = urllib.request.Request(
        url.rstrip("/") + "/verify", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out: dict[str, Any] = json.loads(r.read())
            return out
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError) as e:
        raise VerifierUnavailableError(f"verifier at {url}: {e}") from e


def verdict(report: dict[str, Any], repaired: bool) -> dict[str, Any]:
    """What the run keeps of a verifier report (diagnostics and the job's log line)."""
    bits = [b for s in report.get("scenarios") or [] for b in s.get("bits") or []]
    return {
        "ok": bool(report.get("ok")),
        "scenarios": len(report.get("scenarios") or []),
        "bits": len(bits),
        "mismatches": len(report.get("mismatches") or []),
        "compile": report.get("compile"),
        "repaired": repaired,
        "latency_s": report.get("latency_s"),
        "stubbed_infra_calls": report.get("stubbed_infra_calls") or [],
    }
