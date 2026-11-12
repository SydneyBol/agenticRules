"""Which catalog attributes a canvas reads, and the ones that share their name with an attribute of another list.

Two facts from code, no model call. The reads tell the manager what the draft took each phrase to mean ("city" is the
store's city, not the member's). A look-alike is a read attribute whose code or label another list also has (`city` in
four lists, `Membership Stage` in the transaction and the member profile): both compile and pass every check, so a
wrong list shows only in Offer Testing. The look-alike is a fact beside the problems, not a problem: it holds no
finish back, it asks the finish to say which one was taken.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ....domain import Problem
from ...vocabulary import ATTRIBUTE_LEVELS, LEVEL_LISTS

# the block types whose mutation names a catalog attribute, and the list a value block reads without saying
VALUE_LISTS = {"activity_attribute": "activity", "product_attribute": "product", "airport_attribute": "airport"}
CONDITIONS = ("activity_criteria", "if_generic_adaptive")
OBJECT_LISTS = {"activity": "activity", "airport": "airport", "airport_pair": "airport_pair", "location": "location",
                "sponsor": "sponsor"}  # fmt: skip
# where each list's attributes are, as the manager reads them
PLACES = {
    "activity": "the transaction",
    "member": "the member's profile",
    "product": "the product",
    "airport": "the airport",
    "airport_pair": "the airport pair",
    "location": "the store",
    "sponsor": "the sponsor",
    "payment_method": "the payment method",
    "card": "the member's cobranded card",
    "bank_account": "the member's bank account",
    "subscription": "the member's subscription",
    "analytics": "the member's analytics values",
}
LEVEL_FIELDS = ("attribute", "attribute_type_input", "selected_subscription_attribute", "cardattribute")


def _tag(e: ET.Element) -> str:
    return e.tag.split("}")[-1]


def _list(kind: str, m: ET.Element, field: dict[Any, str | None]) -> tuple[str, str] | None:
    """(the catalog list, the attribute code) a block reads, or None for a counter or anything the catalog lacks."""
    if kind in VALUE_LISTS:
        return (VALUE_LISTS[kind], m.get("attribute") or "") if m.get("attribute") else None
    obj: str | None = m.get("object")
    level: str | None = m.get("attribute_level") or m.get("attribute_type_input")
    if kind == "member_attribute":
        obj = "member"
    if obj is None and "attr_level" in m.attrib:  # a segment condition
        obj, level = "member", m.get("attr_level")
    if obj == "products_attrs":
        which = "product" if field.get("ProductOrOfferContext") in (None, "", "products_attrs") else "product_offer"
        product = m.get("attribute")
        return (which, product) if product else None
    if obj == "member":
        if level in ATTRIBUTE_LEVELS:  # the member's own profile attribute (MA_P)
            name = m.get("attribute")
            return ("member", name) if name else None
        lst = LEVEL_LISTS.get(level or "")  # a card, account, plan or analytics attribute
        name = next((m.get(k) for k in LEVEL_FIELDS if m.get(k) not in (None, "", "null")), None)
        return (lst, name) if lst and name else None
    own = OBJECT_LISTS.get(obj or "")
    return (own, m.get("attribute") or "") if own and m.get("attribute") else None


def attribute_reads(xml: str | None, catalog: Any) -> list[dict[str, str]]:
    """The catalog attributes the canvas reads, each once, in canvas order: {list, place, attribute, label}.

    Counters (offer, activity-level and tier counters) and attributes the catalog does not have are left out.
    """
    if not xml or catalog is None:
        return []
    out: dict[tuple[str, str], dict[str, str]] = {}
    for b in ET.fromstring(xml).iter():
        m = next((c for c in b if _tag(c) == "mutation"), None) if _tag(b) == "block" else None
        if m is None:
            continue
        own = {f.get("name"): f.text for f in b if _tag(f) == "field"}
        found = _list(b.get("type") or "", m, own)
        if found is None or catalog.attr(*found) is None:
            continue
        a = catalog.attr(*found)
        out.setdefault(found, {"list": found[0], "place": PLACES.get(found[0], found[0]), "attribute": found[1],
                               "label": a.label or found[1]})  # fmt: skip
    return list(out.values())


def _same(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def lookalikes(reads: list[dict[str, str]], catalog: Any) -> list[Problem]:
    """A note per read attribute that another attribute shares its code or label with.

    The other one is any attribute the screens list under another place (or another code of the same list with the
    same label). Test and automation attributes are not judged: only what the canvas reads is looked at.
    """
    out: list[Problem] = []
    for r in reads:
        mine = (r["list"], r["attribute"])
        code, label = _same(r["attribute"]), _same(r["label"])
        others: dict[tuple[str, str], str] = {}
        for lst, attrs in catalog.attributes.items():
            if lst not in PLACES:
                continue
            for c, a in attrs.items():
                if (lst, c) == mine:
                    continue
                if _same(c) == code or (label and _same(a.label or "") == label):
                    others[(lst, c)] = a.label or c
        if others:
            out.append(
                Problem.of(
                    "attribute_lookalike",
                    attribute=r["attribute"],
                    place=r["place"],
                    others=tuple(f"{PLACES[lst]} {label} ({c})" for (lst, c), label in sorted(others.items())[:5]),
                )
            )
    return out
