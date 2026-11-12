"""Tenant catalog: the vocabulary the model may use (attributes, lookup values, activity types, accounts, privileges).

Built from the same bolapi endpoints the Blockly dropdowns read (see block-definition.service.ts), either live
(BolapiCatalogSource, caller's JWT forwarded) or from saved responses (Catalog.from_bolapi_dumps). The model reads it
through the prompt (ai/prompt.py catalog_text) and `lookup()`; canvas/checks/grammar.py checks every code it uses
against the same object. `lookup()` with a `search` (value_search.py: embeddings and a cross-encoder) finds values by
meaning as well as by word; this module holds no model code.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...domain import Problem, Result
from ..vocabulary import ACTIVITY_TYPE

log = logging.getLogger("offer_author.catalog")

OBJECTS = ("activity", "member", "product")
# the airline objects: a BIT's origin and destination airports (module AIRPORT) and their pair (AIRPORT_DISTANCE);
# a program without them lists none
AIRLINE_OBJECTS = {"airport": "AIRPORT", "airport_pair": "AIRPORT_DISTANCE"}
# the rules screen's other attribute lists (its list name -> bolapi's custom module): the model is not shown them; the
# catalog holds their active codes so that a block naming one is checked like any other
SCREEN_LISTS = {"sponsor": "SPONSOR", "location": "SPONSORLOCATION", "payment_method": "PAYMENT_METHOD"}
# The screen's member-level attribute lists and its pickers (blockly-http.service.ts getAttributesLOV,
# getSubscriptionPlans, getPartnerPrograms, getSweepstakesList, getPlayWinSetups; the card list), active rows only. An
# attribute list becomes an object of `attributes` (shown and looked up like the member's), a picker a list of
# `pickers`; both are held as their codes for the checks (`listed`). A list that was not read, or whose feature the
# program has off, is not held, and a canvas may name nothing from it (Catalog.holds).
LEVEL_READS = {
    **{
        name: f"attributelibrary/custom/?module_type={module}"
        for name, module in (
            ("card", "COBRAND_CARD"),
            ("bank_account", "COBRAND_ACCOUNT"),
            ("subscription", "SUBSCRIPTION"),
            ("analytics", "MEMBER_SEGMENTATION"),
            ("product_offer", "PRODUCTOFFER"),
            ("partner", "PARTNER_MEMBERSHIP"),
        )
    },
    "card_list": "card-list/?page_size=1000",  # the screen asks for 60
    "subscription_plan": "subscription-plans/?status=ACTIVE&page_size=3000",
    "partner_program": "partner-programs/?status=ACTIVE&page_size=3000",
    "sweepstakes": "sweepstakes/?trim=true&status=ACTIVE&page_size=300",
    "play_win": "play-win/?status=active&page_size=300",
}
LEVEL_OBJECTS = tuple(n for n, path in LEVEL_READS.items() if path.startswith("attributelibrary/"))
LEVEL_ATTRIBUTES = frozenset(LEVEL_OBJECTS)
# a saved dump's file for each (tenant-catalog.mjs); older dumps hold some pickers as the LOV of the same table
LEVEL_FILES = {
    **{n: "custom_" + LEVEL_READS[n].rsplit("=", 1)[1] for n in LEVEL_ATTRIBUTES},
    **{n: f"screen_{n}" for n in LEVEL_READS if n not in LEVEL_ATTRIBUTES},
}
LEVEL_LOV_FILES = {
    "card_list": "lov_CARDS_LIST",
    "subscription_plan": "lov_SUBSCRIPTION_PLAN_LIST",
    "sweepstakes": "lov_SWEEPSTAKE_LIST",
    "play_win": "lov_PLAY_WIN_SETUP_LIST",
}
LEVEL_FEATURES = {"partner": "partner_program_management", "partner_program": "partner_program_management"}
# the screen's lists this catalog has no reader for: individualization (an individualized offer's own attributes) and
# retention scores (the AI service). Nothing named from one can be filled or checked, so a canvas may not name one.
UNREAD_LISTS = frozenset({"personalization", "retention"})
POOL, SHOWN = 25, 15  # lookup with a search: BM25's best 25 and the embeddings' best 25, the cross-encoder's best 15
TIE_ODDS = 0.5  # cross-encoder scores tie when their log-odds are this close (odds within ~1.6x), at any confidence
# value lists longer than this are not written into the prompt; the model finds codes with lookup_values instead
INLINE_VALUES = 40
INLINE_ACTIVITY_TYPES = 200

Scored = tuple[float, str, str, list[str]]  # (score, code, label, the manager's words it lacks)


class Search(Protocol):
    """What finds values by meaning (value_search.ValueSearch)."""

    def nearest(self, docs: list[str], text: str, k: int) -> list[int]:
        """The indexes of the k docs nearest the text."""
        ...

    def rerank(self, text: str, docs: list[str]) -> list[float]:
        """Each doc's relevance to the text."""
        ...


def norm(s: Any) -> str:
    """Text as words: lower case, every run of other characters one space."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(s).lower()).split())


def says_more(a: Attribute) -> str:
    """The attribute's description when it tells more than its code and label do, else "".

    bolapi's descriptions are free text a tenant admin typed: some repeat the label ("Reason for Membership
    Cancellation"), some are filler ("Hi", ".", "QA Testing"). One is kept when it has at least 3 words and at least 2
    of them are in neither the label nor the code.
    """
    import re

    words = re.findall(r"[a-z0-9]+", a.description.lower())
    known = set(re.findall(r"[a-z0-9]+", f"{a.label} {a.code}".lower()))
    new = [w for w in words if w not in known and len(w) > 2]
    return a.description if len(words) >= 3 and len(new) >= 2 else ""


@dataclass
class Attribute:
    """One attribute of the attribute library.

    Attributes:
        code: Its code.
        label: Its visible name.
        type: NUMBER | DATE | DATETIME | LOOKUP | LOV | TEXT | BOOL.
        lookup_type: The lookup or LOV its values come from.
        values: Code -> label, when known.
        line_level: A line-item attribute.
        payment_level: A payment attribute (the payment blocks' payment_attribute dropdown).
        description: What bolapi says the attribute holds ("" when it says nothing).
    """

    code: str
    label: str
    type: str
    lookup_type: str | None = None
    values: dict[str, str] | None = None
    line_level: bool = False
    payment_level: bool = False
    description: str = ""


@dataclass
class Catalog:
    """One tenant's catalog.

    Attributes:
        tenant: The tenant's name.
        sponsor_id: Its sponsor.
        attributes: object -> {code: Attribute}.
        activity_types: code -> label.
        loyalty_accounts: The reward accounts the sponsor's offers can use: [{id, name (the loyalty currency),
            currency_type, sponsor_specific, tags: [{code, name}]}]; tags are known when their GET was made.
        privileges: id -> {id, name, usage_policy (the first active one), usage_policies: [{id, name}]}.
        tiers: The MA_T tier programme: tier_class_code -> {tier_code: name}, each class's tiers by rank, lowest first.
        default_account: The program's configured loyalty account (the backend's --account).
        default_tag: The reward tag points go to, when configured.
        today: The tenant's date (fixtures pin it).
        timezone: The program's time zone (dates are read in it), when known.
        point_decimal_scale: The decimals points are kept at, when known.
        listed: The active codes of the rules screen's other attribute lists (SCREEN_LISTS, for checks only) and of
            the member-level lists and pickers (LEVEL_READS): list name -> codes; a list the catalog was not given is
            absent.
        pickers: The screen's pickers that were read (cards, subscription plans, partner programmes, sweepstakes,
            play & win setups): list name -> {id: name}, live rows only.
        reference: The program data a transaction reads in the engine, for the engine check's mocked runs (never shown
            to the model): airports {code: attributes}, airport_pairs {"ORIG-DEST": {distance, ...}},
            conversion_rates {"<account>_<currency>_AWARD": rate}, features {toggle: on}.
    """

    tenant: str
    sponsor_id: int
    attributes: dict[str, dict[str, Attribute]] = field(default_factory=dict)
    activity_types: dict[str, str] = field(default_factory=dict)
    loyalty_accounts: list[dict[str, Any]] = field(default_factory=list)
    privileges: dict[str, dict[str, Any]] = field(default_factory=dict)
    tiers: dict[str, dict[str, str]] = field(default_factory=dict)
    default_account: str | None = None
    default_tag: str | None = None
    today: str | None = None
    timezone: str | None = None
    point_decimal_scale: int | None = None
    reference: dict[str, Any] = field(default_factory=dict)
    listed: dict[str, frozenset[str]] = field(default_factory=dict)
    pickers: dict[str, dict[str, str]] = field(default_factory=dict)

    def link_activity_types(self) -> Catalog:
        """Makes the activity types the values of the activity attribute h_bit_type (lookup BIT_TYPE).

        Like any other coded attribute; added when the attribute library does not list it.
        """
        a = self.attr("activity", ACTIVITY_TYPE)
        a = a or Attribute(code=ACTIVITY_TYPE, label="Activity type", type="LOOKUP", lookup_type="BIT_TYPE")
        a.values = dict(self.activity_types) or a.values
        self.attributes.setdefault("activity", {})[ACTIVITY_TYPE] = a
        return self

    # ------------------------------------------------------------ lookups
    def attr(self, obj: str, code: str) -> Attribute | None:
        """One attribute of an object (activity, member, product, an airline object, a member-level list), or None."""
        return self.attributes.get(obj, {}).get(code)

    def holds(self, lst: str) -> bool:
        """Whether a canvas may name something from that list of the screen.

        Not a list the catalog has no reader for (UNREAD_LISTS), nor a member-level list or picker it did not read
        (LEVEL_READS: not answered, or the program's feature is off). The other lists are always taken as held.
        """
        if lst in UNREAD_LISTS:
            return False
        return lst in self.listed if lst in LEVEL_READS else True

    def active(self, lst: str, code: str) -> bool | None:
        """Whether the rules screen offers that attribute in that list of its.

        bolapi marks an attribute enabled and available for rules, and the screen lists no other
        (block-definition.service.ts getAttributesTuple); the catalog keeps only those (usable).

        Args:
            lst: activity, member, product, airport or airport_pair (the lists the model reads), or sponsor, location
                or payment_method (SCREEN_LISTS; the payment list also has the transaction's payment attributes).
            code: The attribute's code.

        Returns:
            None when the catalog does not hold that list.
        """
        if lst == "payment_method":
            a = self.attr("activity", code)
            if a is not None and a.payment_level:
                return True
        if lst in self.listed:
            return code in self.listed[lst]
        return code in self.attributes[lst] if lst in self.attributes and lst not in SCREEN_LISTS else None

    def _screen_lists(
        self,
        responses: dict[str, Any],
        lookups: dict[str, dict[str, str]],
        locations: dict[str, str],
        lovs: dict[str, dict[str, str]],
    ) -> None:
        """Takes in the screen's other lists that were read: `listed`, `pickers` and the member-level `attributes`."""
        for name in SCREEN_LISTS:
            if responses.get(f"list:{name}") is not None:
                self.attributes[name] = _attributes(responses[f"list:{name}"], lookups, locations, lovs)
                self.listed[name] = frozenset(self.attributes[name])
        for name, rows in _level_lists(responses, _reference(responses).get("features")).items():
            self.listed[name] = frozenset(rows)
            if name not in LEVEL_ATTRIBUTES:
                self.pickers[name] = rows
            elif rows:  # its usable attributes with their value lists
                self.attributes[name] = _attributes(responses[f"list:{name}"], lookups, locations, lovs)
                self.listed[name] = frozenset(self.attributes[name])

    @property
    def offer_account(self) -> str | None:
        """The account an action that names none credits.

        The sponsor's own account when it has exactly one (the screen lists it for this sponsor only), else the
        program's configured account.
        """
        own = [str(a["id"]) for a in self.loyalty_accounts if a.get("sponsor_specific")]
        return own[0] if len(own) == 1 else self.default_account

    def account(self, account_id: Any = None) -> dict[str, Any] | None:
        """A loyalty account (default: offer_account), or None."""
        aid = str(account_id or self.offer_account)
        return next((a for a in self.loyalty_accounts if str(a["id"]) == aid), None)

    def reward_tag(self, account_id: Any = None) -> str | None:
        """The reward tag points go to in an account when there is nothing to choose.

        The tenant's configured tag when the account has it (or its tags are not known), else the account's only tag;
        None when there is nothing to pick from (the model picks one).
        """
        tags = [t["code"] for t in (self.account(account_id) or {}).get("tags") or []]
        if self.default_tag and (not tags or self.default_tag in tags):
            return self.default_tag
        return tags[0] if len(tags) == 1 else None

    def values_of(self, obj: str, code: str) -> dict[str, str]:
        """Code -> label for a LOOKUP/LOV attribute."""
        a = self.attr(obj, code)
        return (a.values or {}) if a else {}

    def value_docs(self, obj: str, code: str) -> tuple[list[str], list[str]]:
        """An attribute's values as they are searched: (codes, "label (code)" texts)."""
        vals = self.values_of(obj, code)
        return list(vals), [f"{lab} ({c})" for c, lab in vals.items()]

    def airports_with(self, obj: str, code: str) -> dict[str, list[str]] | None:
        """Per value of an airport attribute, the program's active airports that have it.

        The airport setup is where an airport gets its city, country, zone and its own attributes: a value no airport
        has matches no transaction, and of two values with one name (two cities "Abu Dhabi") it tells which one the
        airports are in. None for another object's attribute, or one no airport carries.
        """
        if obj != "airport":
            return None
        out: dict[str, list[str]] = {}
        for airport, attrs in (self.reference.get("airports") or {}).items():
            if code in attrs:
                out.setdefault(str(attrs[code]), []).append(airport)
        return out or None

    def lookup(self, obj: str, code: str, text: str, k: int = 8, search: Search | None = None) -> Result:
        """The catalog codes closest to the manager's words, with facts for the model to judge by (no verdicts).

        Each candidate's score and the manager's words it lacks; the codes tied at the top; candidates sharing one
        label; the words no value holds. A word's weight comes from this value list (rarer = more telling), so 'store'
        in a list of stores counts little, with no word list kept in code; a word no value holds ranks nothing and is
        reported. With a search (value_search.py), the words ("flights") also find values by meaning ("Fly"): see
        _searched. Without one, or when it fails, the words alone rank.

        A value of an airport attribute also names the airports that have it (airports_with).

        Args:
            obj: activity, member, product, airport or airport_pair.
            code: The attribute.
            text: The manager's words for one value.
            k: Candidates shown, at most (words only).
            search: What finds values by meaning, or None.

        Returns:
            {attribute, query, total_values, candidates, tied?, same_label?, words_in_no_value?}, or {error: Problem}
            when the attribute has no value list. A candidate is {code, label, score, lacks?, airports?}.
        """
        vals = self.values_of(obj, code)
        if not vals:
            a = self.attr(obj, code)
            return {
                "error": Problem.of(
                    "not_a_catalog_attribute" if a is None else "no_value_list", attribute=f"{obj}.{code}"
                )
            }
        if search is not None:
            try:
                return self._searched(obj, code, text, search)
            except Exception:  # the words alone still rank
                log.exception("value search failed for %s.%s; ranking by words only", obj, code)
        toks = {c: set(norm(c).split()) | set(norm(lab).split()) for c, lab in vals.items()}
        df = Counter(w for ws in toks.values() for w in ws)  # how many values hold each word

        def weight(ws: Iterable[str]) -> float:
            return sum(math.log(1 + len(vals) / df[w]) for w in ws if w in df)

        qw = set(norm(text).split())
        scored = sorted(
            (_score(norm(text), qw, c, lab, toks[c], weight, df) for c, lab in vals.items()),
            key=lambda t: (-t[0], t[2], t[1]),
        )
        top = [t for t in scored[:k] if t[0] > 0.2]
        at = self.airports_with(obj, code)
        return {
            "attribute": f"{obj}.{code}",
            "query": text,
            "total_values": len(vals),
            "candidates": [
                {"code": c, "label": lab, "score": sc, **({"lacks": miss} if miss else {}), **_airports(at, c)}
                for sc, c, lab, miss in top
            ],
            **_lookup_facts(top, qw - set(df)),
        }

    def _searched(self, obj: str, code: str, text: str, search: Search) -> Result:
        """Values found by words and by meaning, each scored against the words by the cross-encoder.

        BM25's best POOL values for the words and the embeddings' best POOL by meaning, merged; the cross-encoder's
        best SHOWN, with the same facts as the words-only ranking. The cross-encoder reads each value with its
        attribute's name ("BIT Type: Bill Payment (BILL_PAYMENT)"): without it, "utility invoice" ranked Payments over
        Bill Payment. Its scores are probabilities, near 0 for every weak match, so ties are judged on log-odds
        (TIE_ODDS), not the words' fixed 0.02, and shown to 3 significant figures. An exact code or label scores 1.0,
        as in _score, so two values of the same name stay tied.
        """
        codes, docs = self.value_docs(obj, code)
        words = bm25(docs)(text)
        by_word = [i for i in sorted(range(len(docs)), key=lambda i: -words[i]) if words[i] > 0][:POOL]
        pool = list(dict.fromkeys(by_word + search.nearest(docs, text, POOL)))
        vals = self.values_of(obj, code)

        def exact(i: int) -> bool:
            return norm(text) in (norm(codes[i]), norm(vals[codes[i]]))

        about = getattr(self.attr(obj, code), "label", None) or code
        rel = search.rerank(text, [f"{about}: {docs[i]}" for i in pool])
        scores = [1.0 if exact(i) else s for i, s in zip(pool, rel, strict=True)]
        scored = sorted(zip(scores, pool, strict=True), key=lambda t: -t[0])[:SHOWN]
        top: list[Scored] = [(sc, codes[i], vals[codes[i]], []) for sc, i in scored]
        vocab = {w for d in docs for w in norm(d).split()}
        at = self.airports_with(obj, code)
        return {
            "attribute": f"{obj}.{code}",
            "query": text,
            "total_values": len(docs),
            "candidates": [
                {"code": c, "label": lab, "score": float(f"{sc:.3g}"), **_airports(at, c)} for sc, c, lab, _ in top
            ],
            **_lookup_facts(top, set(norm(text).split()) - vocab, close=lambda a, b: _odds(a) - _odds(b) <= TIE_ODDS),
        }

    # ------------------------------------------------------------ builders
    @classmethod
    def from_json(cls, path: str | os.PathLike[str]) -> Catalog:
        """A hand-written catalog (tests, the local fixture).

        Its attributes are {obj: [{code, label, type, values?, ...}]}.
        """
        d = json.loads(Path(path).read_text())
        cat = cls(
            tenant=d["tenant"],
            sponsor_id=d["sponsor_id"],
            activity_types=d["activity_types"],
            loyalty_accounts=d["loyalty_accounts"],
            privileges={str(p["id"]): p for p in d["privileges"]},
            default_account=str(d["default_account"]),
            default_tag=d.get("default_tag"),
            today=d.get("today"),
            tiers=d.get("tiers", {}),
            timezone=d.get("timezone"),
            point_decimal_scale=d.get("point_decimal_scale"),
        )
        for obj, attrs in d["attributes"].items():
            cat.attributes[obj] = {a["code"]: Attribute(**a) for a in attrs}
        return cat.link_activity_types()

    @classmethod
    def from_bolapi(
        cls, tenant: str, sponsor_id: int, account_id: Any, responses: dict[str, Any], default_tag: str | None = None
    ) -> Catalog:
        """A catalog from bolapi's answers.

        Args:
            tenant: The tenant's name.
            sponsor_id: Its sponsor.
            account_id: The loyalty account points go to.
            responses: {name: body} for the GETs of BolapiCatalogSource.requests (raw bolapi JSON bodies).
            default_tag: The reward tag points go to, when configured.

        Returns:
            The catalog.
        """
        cat = cls(tenant=tenant, sponsor_id=sponsor_id, default_account=str(account_id), default_tag=default_tag)
        lookups, locations, lovs = _value_lists(responses)
        for obj in OBJECTS:
            cat.attributes[obj] = _attributes(responses[f"{obj}_attributes"], lookups, locations, lovs)
        for obj in AIRLINE_OBJECTS:
            if responses.get(f"{obj}_attributes"):
                cat.attributes[obj] = _attributes(responses[f"{obj}_attributes"], lookups, locations, lovs)
        cat._screen_lists(responses, lookups, locations, lovs)
        cat.activity_types = lookups.get("BIT_TYPE", {})
        program = next(iter(_rows(responses.get("programs"))), {})
        cat.timezone, cat.point_decimal_scale = program.get("timezone"), program.get("point_decimal_scale")
        for t in sorted(_rows(responses.get("tiers")), key=lambda t: t.get("tier_rank") or 0):
            if live(t) and t.get("tier_class_code"):
                # the dropdown stores tier_code
                cat.tiers.setdefault(t["tier_class_code"], {})[t.get("tier_code") or t["name"]] = t["name"]
        tags = {
            k[5:]: [{"code": t["code"], "name": t["name"]} for t in _rows(v)]
            for k, v in responses.items()
            if k.startswith("tags:")
        }
        las = responses["sponsor_loyalty_accounts"]
        las = las.get("loyalty_accounts", []) if isinstance(las, dict) else las
        cat.loyalty_accounts = [
            {
                "id": str(a["id"]),
                "name": a["loyalty_currency"],
                "currency_type": a.get("currency_type"),
                "sponsor_specific": bool(a.get("is_sponsor_specific")),
                "tags": tags.get(str(a["id"]), []),
            }
            for a in las
            if a.get("status", "Active") == "Active"
        ]
        today = cat.today or _program_today(cat.timezone)
        for p in _rows(responses["privilege_products"]):
            if not live(p) or str(p.get("active_end") or "9999")[:10] < today:
                continue  # the screen asks for privileges still active today (min_active_end)
            # the screen's default policy first
            pols = sorted((u for u in p.get("usage_policy") or [] if live(u)), key=lambda u: not u.get("default"))
            if pols and p.get("privilege_type") in ("Fixed-Product", "Fixed-Discount"):
                cat.privileges[str(p["id"])] = {
                    "id": str(p["id"]),
                    "name": p["name"],
                    "usage_policy": str(pols[0]["id"]),
                    "usage_policies": [{"id": str(u["id"]), "name": _policy_name(u)} for u in pols],
                }
        cat.reference = _reference(responses)
        return cat.link_activity_types()

    @classmethod
    def from_bolapi_dumps(
        cls, directory: str, tenant: str, sponsor_id: int, account_id: Any, default_tag: str | None = None
    ) -> Catalog:
        """A catalog from saved responses written by harness/ui/hosted/tenant-catalog.mjs ({status, body} per file)."""

        def body(name: str) -> Any:
            with open(os.path.join(directory, name + ".json")) as f:
                return json.load(f)["body"]

        def answered(name: str) -> bool:
            """Saved, and bolapi answered it (the dump saves a refused call with its status too)."""
            path = os.path.join(directory, name + ".json")
            if not os.path.exists(path):
                return False
            with open(path) as f:
                return 200 <= int(json.load(f).get("status") or 200) < 300

        resp = {
            k: body(k)
            for k in (
                "activity_attributes",
                "member_attributes",
                "product_attributes",
                "sponsor_loyalty_accounts",
                "privilege_products",
            )
        }
        resp["lookup:BIT_TYPE"] = body("lookup_BIT_TYPE")
        optional = {
            "lookup:PRODUCT_CATEGORY": "extra_prodcat",
            "lookup:TIER_CLASS": "extra_tierclass",
            "lovs": "lovs",
            "tiers": "extra_tiers",
            "programs": "programs",
            "locations": f"extra_locations{sponsor_id}",
            **{f"{obj}_attributes": f"custom_{module}" for obj, module in AIRLINE_OBJECTS.items()},
            **{f"list:{name}": f"custom_{module}" for name, module in SCREEN_LISTS.items()},
            **{f"list:{name}": LEVEL_FILES[name] for name in LEVEL_READS},
        }
        resp.update({k: body(f) for k, f in optional.items() if answered(f)})
        for name, f in LEVEL_LOV_FILES.items():
            if f"list:{name}" not in resp and answered(f):
                resp[f"list:{name}"] = body(f)
        for f in os.listdir(directory):
            if f.startswith("extra_tags"):
                resp["tags:" + f[len("extra_tags") : -5]] = body(f[:-5])
            elif f.startswith("lov_"):
                resp["lov:" + f[4:-5]] = body(f[:-5])
            elif f.startswith("lookup_") and "lookup:" + unquote(f[7:-5]) not in resp:
                resp["lookup:" + unquote(f[7:-5])] = body(f[:-5])  # every lookup type the dump saved
            elif f.startswith("award_conversion"):
                resp["award_conversion:" + f[len("award_conversion") : -5]] = body(f[:-5])
        for name in ("airports", "airport_pairs", "feature_toggles"):
            if os.path.exists(os.path.join(directory, name + ".json")):
                resp[name] = body(name)
        return cls.from_bolapi(tenant, sponsor_id, account_id, resp, default_tag)


class BolapiCatalogSource:
    """The live catalog from bolapi with the caller's JWT (the Blockly dropdowns' endpoints), cached per key."""

    def __init__(self, base_url: str, http: Any = None, ttl_s: float = 900) -> None:
        """Reads from one bolapi.

        Args:
            base_url: The tenant's API host.
            http: An httpx-like client (default: httpx with a 30 s timeout).
            ttl_s: How long a catalog is kept.
        """
        import httpx

        self.base = base_url.rstrip("/") + "/bolapi/v1/"
        self.http = http or httpx.Client(timeout=30)
        self.ttl_s = ttl_s
        self._cache: dict[tuple[str, int, str], tuple[float, Catalog]] = {}

    def requests(self, sponsor_id: int, account_id: Any) -> dict[str, str]:
        """The GETs a catalog is built from: {name: path under /bolapi/v1/}."""
        return {
            "activity_attributes": "attributelibrary/activity/",
            "member_attributes": "attributelibrary/member/?all=True",
            "product_attributes": "attributelibrary/custom/?module_type=PRODUCT",
            **{f"{o}_attributes": f"attributelibrary/custom/?module_type={m}" for o, m in AIRLINE_OBJECTS.items()},
            **{f"list:{n}": f"attributelibrary/custom/?module_type={m}" for n, m in SCREEN_LISTS.items()},
            **{f"list:{n}": path for n, path in LEVEL_READS.items()},
            "airports": "airlines/airports/?page_size=1000",  # the engine check's reference data (optional)
            "airport_pairs": "airlines/airports-distance/?page_size=1000",
            "sponsor_loyalty_accounts": f"sponsors/loyaltyaccounts/?sponsor_id={sponsor_id}&offer_type=reward",
            "privilege_products": (
                f"product/?sponsor_in={sponsor_id}&as_privilege=true&page_size=500&bundle_type=static"
            ),
            "locations": f"sponsors/{sponsor_id}/locations/?active=True&page_size=1000",
            f"tags:{account_id}": TAGS.format(account_id),
            "tiers": "tier-list/?page_size=500",  # blockly-lov-registry.ts (segment tier dropdowns)
            "programs": "programs/",  # time zone and point decimal scale
            "lovs": "lovs/",  # LOV definitions; BIT_CATEGORY carries its values (model)
            "lookup:BIT_TYPE": "lookups/?type=BIT_TYPE&active=True&page_size=1000",
            "lookup:PRODUCT_CATEGORY": "lookups/?type=PRODUCT_CATEGORY&active=True&page_size=1000",
            "lookup:TIER_CLASS": "lookups/?type=TIER_CLASS&active=True&page_size=1000",
        }

    def get(
        self, tenant: str, sponsor_id: int, account_id: Any, authorization: str | None, default_tag: str | None = None
    ) -> Catalog:
        """The tenant's catalog: cached, else read with the caller's JWT (the GETs run together)."""
        hit = self._cache.get((tenant, sponsor_id, str(account_id)))
        if hit and time.time() - hit[0] < self.ttl_s:
            return hit[1]
        return self.refresh(tenant, sponsor_id, account_id, authorization, default_tag)

    def refresh(
        self, tenant: str, sponsor_id: int, account_id: Any, authorization: str | None, default_tag: str | None = None
    ) -> Catalog:
        """The tenant's catalog read again with the caller's JWT, cached in place of the one before."""
        key = (tenant, sponsor_id, str(account_id))
        reqs = self.requests(sponsor_id, account_id)

        def fetch(name: str, rel: str) -> Any:
            r = self.http.get(self.base + rel, headers={"Authorization": authorization or ""})
            try:
                r.raise_for_status()
            except Exception:
                if (
                    name in OPTIONAL
                    or name.startswith("lov:")
                    or (name.startswith("lookup:") and name not in reqs)
                    or (name.startswith("tags:") and name != f"tags:{account_id}")
                ):
                    # the catalog is complete without it (another account's tags: that account lists none; an
                    # attribute's own lookup type: that attribute has no value list)
                    return None
                raise
            return r.json()

        with ThreadPoolExecutor(6) as pool:  # 31 GETs at once instead of one after another (a miss is user-visible)
            resp = dict(zip(reqs, pool.map(fetch, reqs, reqs.values()), strict=True))
            # every other reward account's tags: an offer may award to any of them (the give_points dropdown)
            las = resp.get("sponsor_loyalty_accounts")
            las = las.get("loyalty_accounts", []) if isinstance(las, dict) else las or []
            more = {f"tags:{a['id']}": TAGS.format(a["id"]) for a in las if f"tags:{a['id']}" not in resp}
            more.update(lov_requests(resp))  # the table-backed LOVs' rows (optional)
            more.update(lookup_requests(resp))  # the attributes' other lookup types (optional)
            resp.update(zip(more, pool.map(fetch, more, more.values()), strict=True))
        cat = Catalog.from_bolapi(tenant, sponsor_id, account_id, resp, default_tag)
        self._cache[key] = (time.time(), cat)
        return cat


OPTIONAL = frozenset(
    {
        "programs",
        "airports",
        "airport_pairs",
        *(f"{o}_attributes" for o in AIRLINE_OBJECTS),
        *(f"list:{n}" for n in SCREEN_LISTS),
        *(f"list:{n}" for n in LEVEL_READS),
    }
)  # catalog GETs whose failure leaves their facts unknown instead of failing it
TAGS = "loyaltyaccounts/{}/tags/?active=true&page_size=1000"  # an account's reward tags


def _policy_name(u: dict[str, Any]) -> str:
    """A usage policy as the privilege screen names it: its name, and how often it can be used."""
    name = u.get("policy_name") or u.get("name") or str(u["id"])
    use = u.get("usage_type") or ""
    if u.get("usage_count") and u.get("usage_frequency"):
        use += f" {u['usage_count']} per {u['usage_frequency'].lower()}"
    if u.get("lifetime_usage_limit"):
        use += f", {u['lifetime_usage_limit']} in all"
    return f"{name} ({use.strip(', ')})" if use else name


def _reference(responses: dict[str, Any]) -> dict[str, Any]:
    """The program data a transaction reads in the engine, as bolapi hands it over.

    An airport's attributes are those of bolapi's Airport.get_data_for_engine (codes, ids of its city, country, ...,
    extra_data merged in); a pair's are its distance and extra_data; a loyalty account's award conversion rate per
    currency; each feature toggle on or off.
    """
    out: dict[str, Any] = {}
    airports = {}
    for a in _rows(responses.get("airports")):
        if a.get("active") and a.get("code"):
            attrs = {
                "airport_code": a["code"],
                "airport_name": a["code"],
                "airport_name_text": a.get("name"),
                "description": a.get("description"),
                "zone": a.get("zone"),
                "latitude": a.get("latitude"),
                "longitude": a.get("longitude"),
                "address": a.get("address"),
            }
            attrs.update({k: str(a[k]) for k in ("city", "country", "continent", "region", "area") if a.get(k)})
            attrs.update(a.get("extra_data") or {})
            airports[a["code"]] = {k: v for k, v in attrs.items() if v not in (None, "")}
    pairs = {}
    for p in _rows(responses.get("airport_pairs")):
        src, dst = p.get("source_airport") or {}, p.get("destination_airport") or {}
        if src.get("code") and dst.get("code"):
            pairs[f"{src['code']}-{dst['code']}"] = {
                "distance": float(p.get("distance") or 0),
                **(p.get("extra_data") or {}),
            }
    rates = {
        f"{name.split(':', 1)[1]}_{r['currency_code']}_AWARD": r["conversion_rate"]
        for name, body in responses.items()
        if name.startswith("award_conversion:")
        for r in _rows(body)
        if r.get("currency_code") and r.get("conversion_rate") is not None
    }
    toggles = responses.get("feature_toggles") or {}
    features = {
        k: bool(v.get("active", v.get("enabled", False))) if isinstance(v, dict) else bool(v)
        for k, v in toggles.items()
    }
    for k, v in (("airports", airports), ("airport_pairs", pairs), ("conversion_rates", rates), ("features", features)):
        if v:
            out[k] = v
    return out


def _level_lists(responses: dict[str, Any], features: dict[str, bool] | None) -> dict[str, dict[str, str]]:
    """The member-level lists and pickers that were read, as their active codes with their names (LEVEL_READS).

    An attribute list keeps its usable attributes (name -> visible name); a picker (cards, plans, programmes,
    sweepstakes, play & win setups) its live rows (id -> name). A list whose feature the program has off holds nothing,
    as the screen loads none (block-definition.service.ts getAllAttributesLov); with no switches known, what was read
    is kept.
    """
    out: dict[str, dict[str, str]] = {}
    for name in LEVEL_READS:
        feature = LEVEL_FEATURES.get(name)
        if feature and features is not None and not features.get(feature):
            out[name] = {}
            continue
        body = responses.get(f"list:{name}")
        if body is None:
            continue
        rows = _rows(body)
        if name in LEVEL_ATTRIBUTES:
            out[name] = {x["name"]: x.get("visible_name") or x["name"] for x in rows if usable(x)}
        else:
            out[name] = {
                str(x["id"]): str(x.get("name") or x["id"]) for x in rows if x.get("id") is not None and live(x)
            }
    return out


def _rows(body: Any) -> list[dict[str, Any]]:
    """A bolapi list body's rows (a plain list, or a page's results)."""
    return body if isinstance(body, list) else (body or {}).get("results", [])


def bm25(docs: list[str], k1: float = 1.5, b: float = 0.75) -> Callable[[str], list[float]]:
    """Okapi BM25 over the docs' words: a function from a text to each doc's score."""
    toks = [norm(d).split() for d in docs]
    avg = sum(map(len, toks)) / max(len(toks), 1) or 1
    df = Counter(w for t in toks for w in set(t))
    idf = {w: math.log(1 + (len(toks) - n + 0.5) / (n + 0.5)) for w, n in df.items()}
    tfs = [Counter(t) for t in toks]

    def score(text: str) -> list[float]:
        q = norm(text).split()
        return [
            sum(idf[w] * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg)) for w in q if w in tf)
            for tf, t in zip(tfs, toks, strict=True)
        ]

    return score


def _score(
    q: str, qw: set[str], c: str, lab: str, words: set[str], weight: Callable[[Iterable[str]], float], df: Counter[str]
) -> Scored:
    """One value scored against the manager's words.

    The manager's telling words found in the value (recall), the value's label the manager said (cover), and string
    similarity; 1.0 for the exact code or label.

    Returns:
        (score, code, label, the manager's words it lacks).
    """
    if q in (norm(c), norm(lab)):
        return 1.0, c, lab, []
    said, known, lw = qw & words, {w for w in qw if w in df}, set(norm(lab).split())
    recall = weight(said) / weight(known) if known else 0.0
    cover = weight(said & lw) / weight(lw) if weight(lw) else 0.0
    sim = max(difflib.SequenceMatcher(None, q, norm(lab)).ratio(), difflib.SequenceMatcher(None, q, norm(c)).ratio())
    return round(0.6 * recall + 0.25 * cover + 0.15 * sim, 3), c, lab, sorted(qw - said)


def _odds(p: float) -> float:
    """A probability's log-odds (kept off 0 and 1)."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def _airports(at: dict[str, list[str]] | None, code: str) -> dict[str, list[str]]:
    """A candidate's `airports` fact: the airports that have the value (none is a fact too), or nothing to add."""
    return {} if at is None else {"airports": at.get(code, [])}


def _lookup_facts(
    top: list[Scored], unknown: set[str], close: Callable[[float, float], bool] = lambda best, sc: best - sc <= 0.02
) -> dict[str, Any]:
    """Facts about the best candidates, for the model to judge by.

    The codes tied at the top (close to the best score), the codes sharing one label, and the manager's words no value
    holds.
    """
    out: dict[str, Any] = {}
    if top and len(tied := [c for sc, c, _, _ in top if close(top[0][0], sc)]) > 1:
        out["tied"] = tied
    labels: dict[str, list[str]] = {}
    for _, c, lab, _ in top:
        labels.setdefault(norm(lab), []).append(c)
    if shared := [cs for cs in labels.values() if len(cs) > 1]:
        out["same_label"] = shared
    if unknown:
        out["words_in_no_value"] = sorted(unknown)
    return out


def _value_lists(
    responses: dict[str, Any],
) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, dict[str, str]]]:
    """The value lists in bolapi's answers, without the rows no longer in use (live).

    Returns:
        (lookups {type: {code: name}}, locations {code: name}, LOVs that carry their values {name: {code: name}}).
    """
    lookups = {
        name[7:]: {x["code"]: x.get("name") or x["code"] for x in _rows(body) if x.get("code") and live(x)}
        for name, body in responses.items()
        if name.startswith("lookup:")
    }
    locations = {x["location_code"]: x["location_name"] for x in _rows(responses.get("locations")) if live(x)}

    def lov_value(x: dict[str, Any], m: Any) -> tuple[str, Any]:
        return (
            (str(m.get(x.get("code") or "code")), m.get(x.get("display") or "name"))
            if isinstance(m, dict)
            else (str(m), str(m))
        )

    lovs = {
        x["name"]: dict(lov_value(x, m) for m in x["model"] if live(m))
        for x in _rows(responses.get("lovs"))
        if x.get("name") and isinstance(x.get("model"), list)
    }  # e.g. BIT_CATEGORY
    for x in _rows(responses.get("lovs")):  # a LOV backed by a table: its rows, read from its api_url (lov_requests)
        rows = _rows(responses.get("lov:" + str(x.get("name"))))
        if rows and x["name"] not in lovs:
            lovs[x["name"]] = {
                str(r[x["code"]]): r.get(x.get("display") or "name") or str(r[x["code"]])
                for r in rows
                if r.get(x["code"]) not in (None, "") and live(r)
            }
    return lookups, locations, lovs


PERSONAL_LOVS = ("MEMBER", "USER")  # LOVs listing people: not codes a rule names, and personal data


INACTIVE_STATUSES = frozenset(
    {"inactive", "i", "terminated", "t", "suspended", "s", "expired", "deleted", "d"}
)  # bolapi statuses of a row that is no longer in use (accounts Inactive; sponsors T, S; offers and privileges)


def live(row: Any) -> bool:
    """Whether a row of a bolapi list is in use: not `active: false`, not an inactive status.

    Lookups, airports, payment methods and engagement rules mark it with `active`, cards with `status: false`,
    loyalty accounts with
    `status: Inactive`, sponsors with status T (terminated) or S (suspended), offers and privileges with expired,
    terminated or suspended. The stock screen asks for most of these lists with active=true or the live statuses
    (blockly-lov-registry.ts resolveLovApiUrl); a value that is no longer in use is never loaded here.
    """
    if not isinstance(row, dict):
        return True
    if row.get("active") is False or row.get("is_active") is False or row.get("status") is False:
        return False
    status = row.get("status")
    return not (isinstance(status, str) and status.strip().lower() in INACTIVE_STATUSES)


def _program_today(timezone: str | None) -> str:
    """Today's date in the program's time zone (UTC when unknown), as YYYY-MM-DD."""
    try:
        tz = ZoneInfo(timezone) if timezone else None
    except (ZoneInfoNotFoundError, ValueError):
        tz = None
    return datetime.now(tz).strftime("%Y-%m-%d")


def usable(row: dict[str, Any]) -> bool:
    """Whether bolapi marks an attribute active for rules: enabled and available for rules.

    The two flags of bolapi's attribute models (program/models.py AbstractAttribute, CustomAbstractAttribute; every
    attribute serializer returns both). The stock screen lists an attribute only when both are true
    (block-definition.service.ts getAttributesTuple: `available_for_rules && enabled`), so a row without one is not
    listed either. bolapi allows available_for_rules only on a rule_selectable attribute, so that flag adds nothing.
    """
    return bool(row.get("enabled") and row.get("available_for_rules"))


def _attribute_rows(responses: dict[str, Any]) -> list[dict[str, Any]]:
    """Every usable attribute row in bolapi's answers: the objects' lists and the member-level lists."""
    return [
        a
        for name, body in responses.items()
        if name.endswith("_attributes") or name.removeprefix("list:") in (*LEVEL_ATTRIBUTES, *SCREEN_LISTS)
        for a in _rows(body)
        if usable(a)
    ]


def lov_requests(responses: dict[str, Any]) -> dict[str, str]:
    """The GETs for the table-backed LOVs the attributes use: {"lov:<NAME>": path under /bolapi/v1/}.

    Only LOVs whose api_url needs no parameter (a sponsor's stores and an account's tags are read already), and none
    that lists people.
    """
    used = {a.get("lookup_type") for a in _attribute_rows(responses) if a.get("data_type") == "LOV"}
    out = {}
    for x in _rows(responses.get("lovs")):
        url, name = x.get("api_url") or "", str(x.get("name"))
        table = not isinstance(x.get("model"), list) and url.startswith("/api/v1/") and "{" not in url
        if name in used and table and not any(p in name for p in PERSONAL_LOVS):
            rel = url[len("/api/v1/") :]
            out["lov:" + name] = rel + ("&" if "?" in rel else "?") + "page_size=1000"
    return out


def lookup_requests(responses: dict[str, Any]) -> dict[str, str]:
    """The GETs for the lookup types the attributes use and the answers do not hold: {"lookup:<TYPE>": path}.

    A LOOKUP attribute's values are its lookup type's rows (gender, currency, an airport's zone, a tenant's own
    types); only the attributes rules may use are read for.
    """
    used = {
        str(a["lookup_type"])
        for a in _attribute_rows(responses)
        if a.get("data_type") == "LOOKUP" and a.get("lookup_type") not in (None, "None", "")
    }
    return {
        f"lookup:{t}": f"lookups/?type={quote(t, safe='')}&active=True&page_size=1000"
        for t in sorted(used)
        if f"lookup:{t}" not in responses
    }


def _attributes(
    body: Any, lookups: dict[str, dict[str, str]], locations: dict[str, str], lovs: dict[str, dict[str, str]]
) -> dict[str, Attribute]:
    """The attributes rules may use (usable), with their value lists."""
    out = {}
    for x in _rows(body):
        if not usable(x):
            continue
        lt = x.get("lookup_type") if x.get("lookup_type") not in (None, "None", "") else None
        values = (
            lookups.get(lt or "")
            if x["data_type"] == "LOOKUP"
            else (locations if lt == "SPONSOR_LOCATION_LIST" else lovs.get(lt or ""))
        )
        out[x["name"]] = Attribute(
            code=x["name"],
            label=x.get("visible_name") or x.get("business_name") or x["name"],
            type=x["data_type"],
            lookup_type=lt,
            values=values or None,
            line_level=bool(x.get("line_level")),
            payment_level=bool(x.get("payment_level")),
            description=" ".join(str(x.get("description") or "").split()),
        )
    return out
