"""The set_offer_main tool: the Offer Main form, checked against the form's own rules."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from ...domain import Problem, Result, ToolInput
from ..vocabulary import OFFER_MAIN_FORM


def offer_main_problems(x: ToolInput, form: dict[str, Any] = OFFER_MAIN_FORM) -> list[Problem]:
    """Checks set_offer_main's input against the Offer Main form's rules (core/data/offer_main_form.json).

    Args:
        x: The tool input.
        form: The form's rules: field lengths, whether a field is required, and the choices of each list field.

    Returns:
        What does not fit the form; empty when it all fits.
    """
    errs, name = [], form["offer_name"]
    if name["max_length"] and len(x.get("offer_name") or "") > name["max_length"]:
        errs.append(Problem.of("offer_name_too_long", max=name["max_length"]))
    if name["required"] and not (x.get("offer_name") or "").strip():
        errs.append(Problem.of("offer_name_empty"))
    dates = []
    for k in ("start_date", "end_date"):
        if x.get(k):
            try:
                dates.append(dt.date.fromisoformat(x[k]))
            except ValueError:
                errs.append(Problem.of("date_not_iso", field=k, value=x[k]))
    if len(dates) == 2 and dates[1] < dates[0]:
        errs.append(Problem.of("end_before_start"))
    for key, blank in (("frequency", "none"), ("offer_frequency_type", "")):
        allowed = [blank, *form[key]["choices"]]
        if x.get(key, blank) not in allowed:
            errs.append(Problem.of("not_a_choice", field=key, allowed=allowed))
    return errs


def offer_settings(om: dict[str, Any] | None) -> dict[str, Any] | None:
    """The Offer Main values the offer's settings screen takes (dates, usage limit, frequency), or None when unset."""
    if not om:
        return None
    return {
        "start_date": om.get("start_date") or None,
        "end_date": om.get("end_date") or None,
        "max_usage_per_user": om.get("max_usage_per_user") or None,
        "frequency": None if om.get("frequency", "none") == "none" else om["frequency"],
    }


class OfferMain:
    """The run's Offer Main: unset until set_offer_main sends a form that fits.

    Attributes:
        value: The accepted form, or None.
    """

    def __init__(self, on_set: Callable[[dict[str, Any]], None] | None = None) -> None:
        """Starts unset.

        Args:
            on_set: Called with the form as soon as it is accepted (the Offer Main screen fills in from it).
        """
        self.value: dict[str, Any] | None = None
        self.on_set = on_set

    def set(self, x: ToolInput) -> Result:
        """Accepts the form when it fits, else returns its problems."""
        if errs := offer_main_problems(x):
            return {"ok": False, "problems": errs}
        self.value = dict(x)
        if self.on_set:
            self.on_set(self.value)
        return {"ok": True}
