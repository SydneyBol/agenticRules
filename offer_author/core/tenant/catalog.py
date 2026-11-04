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
from pathlib import Path
from typing import Any, Protocol

from ...domain import Problem, Result
from ..vocabulary import ACTIVITY_TYPE

log = logging.getLogger("offer_author.catalog")

OBJECTS = ("activity", "member", "product")
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
    """

    code: str
    label: str
    type: str
    lookup_type: str | None = None
    values: dict[str, str] | None = None
    line_level: bool = False
    payment_level: bool = False


@dataclass
class Catalog:
    """One tenant's catalog.

    Attributes:
        tenant: The tenant's name.
        sponsor_id: Its sponsor.
        attributes: object -> {code: Attribute}.
        activity_types: code -> label.
        loyalty_accounts: [{id, name, tags: [{code, name}]}].
        privileges: id -> {id, name, usage_policy}.
        tiers: The MA_T tier programme: tier_class_code -> {tier_code: name}.
        default_account: The loyalty account points go to.
        default_tag: The reward tag points go to, when configured.
        today: The tenant's date (fixtures pin it).
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
        """One attribute of an object (activity, member, product), or None."""
        return self.attributes.get(obj, {}).get(code)

    def account(self, account_id: Any = None) -> dict[str, Any] | None:
        """A loyalty account (default: the tenant's), or None."""
        aid = str(account_id or self.default_account)
        return next((a for a in self.loyalty_accounts if str(a["id"]) == aid), None)

    def reward_tag(self, account_id: Any = None) -> str | None:
        """The reward tag points go to when there is nothing to choose.

        The tenant's configured tag, else the account's only tag; None when the account has several and none is
        configured (the model picks one).
        """
        tags = [t["code"] for t in (self.account(account_id) or {}).get("tags") or []]
        return self.default_tag or (tags[0] if len(tags) == 1 else None)

    def values_of(self, obj: str, code: str) -> dict[str, str]:
        """Code -> label for a LOOKUP/LOV attribute."""
        a = self.attr(obj, code)
        return (a.values or {}) if a else {}

    def value_docs(self, obj: str, code: str) -> tuple[list[str], list[str]]:
        """An attribute's values as they are searched: (codes, "label (code)" texts)."""
        vals = self.values_of(obj, code)
        return list(vals), [f"{lab} ({c})" for c, lab in vals.items()]

    def lookup(self, obj: str, code: str, text: str, k: int = 8, search: Search | None = None) -> Result:
        """The catalog codes closest to the manager's words, with facts for the model to judge by (no verdicts).

        Each candidate's score and the manager's words it lacks; the codes tied at the top; candidates sharing one
        label; the words no value holds. A word's weight comes from this value list (rarer = more telling), so 'store'
        in a list of stores counts little, with no word list kept in code; a word no value holds ranks nothing and is
        reported. With a search (value_search.py), the words ("flights") also find values by meaning ("Fly"): see
        _searched. Without one, or when it fails, the words alone rank.

        Args:
            obj: activity, member or product.
            code: The attribute.
            text: The manager's words for one value.
            k: Candidates shown, at most (words only).
            search: What finds values by meaning, or None.

        Returns:
            {attribute, query, total_values, candidates, tied?, same_label?, words_in_no_value?}, or {error: Problem}
            when the attribute has no value list.
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
        return {
            "attribute": f"{obj}.{code}",
            "query": text,
            "total_values": len(vals),
            "candidates": [
                {"code": c, "label": lab, "score": sc, **({"lacks": miss} if miss else {})} for sc, c, lab, miss in top
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
        return {
            "attribute": f"{obj}.{code}",
            "query": text,
            "total_values": len(docs),
            "candidates": [{"code": c, "label": lab, "score": float(f"{sc:.3g}")} for sc, c, lab, _ in top],
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
        cat.activity_types = lookups.get("BIT_TYPE", {})
        for t in _rows(responses.get("tiers")):
            if t.get("active", True) and t.get("tier_class_code"):
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
            {"id": str(a["id"]), "name": a["loyalty_currency"], "tags": tags.get(str(a["id"]), [])}
            for a in las
            if a.get("status", "Active") == "Active"
        ]
        for p in _rows(responses["privilege_products"]):
            pol = next((u for u in p.get("usage_policy") or [] if u.get("active", True)), None)
            if pol and p.get("privilege_type") in ("Fixed-Product", "Fixed-Discount"):
                cat.privileges[str(p["id"])] = {"id": str(p["id"]), "name": p["name"], "usage_policy": str(pol["id"])}
        return cat.link_activity_types()

    @classmethod
    def from_bolapi_dumps(
        cls, directory: str, tenant: str, sponsor_id: int, account_id: Any, default_tag: str | None = None
    ) -> Catalog:
        """A catalog from saved responses written by harness/ui/hosted/tenant-catalog.mjs ({status, body} per file)."""

        def body(name: str) -> Any:
            with open(os.path.join(directory, name + ".json")) as f:
                return json.load(f)["body"]

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
            "locations": f"extra_locations{sponsor_id}",
        }
        resp.update({k: body(f) for k, f in optional.items() if os.path.exists(os.path.join(directory, f + ".json"))})
        for f in os.listdir(directory):
            if f.startswith("extra_tags"):
                resp["tags:" + f[len("extra_tags") : -5]] = body(f[:-5])
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
            "sponsor_loyalty_accounts": f"sponsors/loyaltyaccounts/?sponsor_id={sponsor_id}&offer_type=reward",
            "privilege_products": (
                f"product/?sponsor_in={sponsor_id}&as_privilege=true&page_size=500&bundle_type=static"
            ),
            "locations": f"sponsors/{sponsor_id}/locations/?active=True&page_size=1000",
            f"tags:{account_id}": f"loyaltyaccounts/{account_id}/tags/?active=true&page_size=1000",
            "tiers": "tier-list/?page_size=500",  # blockly-lov-registry.ts (segment tier dropdowns)
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

        def fetch(rel: str) -> Any:
            r = self.http.get(self.base + rel, headers={"Authorization": authorization or ""})
            r.raise_for_status()
            return r.json()

        reqs = self.requests(sponsor_id, account_id)
        with ThreadPoolExecutor(6) as pool:  # 12 GETs at once instead of one after another (a miss is user-visible)
            resp = dict(zip(reqs, pool.map(fetch, reqs.values()), strict=True))
        cat = Catalog.from_bolapi(tenant, sponsor_id, account_id, resp, default_tag)
        self._cache[key] = (time.time(), cat)
        return cat


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
    """The value lists in bolapi's answers.

    Returns:
        (lookups {type: {code: name}}, locations {code: name}, LOVs that carry their values {name: {code: name}}).
    """
    lookups = {
        name[7:]: {x["code"]: x.get("name") or x["code"] for x in _rows(body) if x.get("code")}
        for name, body in responses.items()
        if name.startswith("lookup:")
    }
    locations = {x["location_code"]: x["location_name"] for x in _rows(responses.get("locations"))}

    def lov_value(x: dict[str, Any], m: Any) -> tuple[str, Any]:
        return (
            (str(m.get(x.get("code") or "code")), m.get(x.get("display") or "name"))
            if isinstance(m, dict)
            else (str(m), str(m))
        )

    lovs = {
        x["name"]: dict(lov_value(x, m) for m in x["model"])
        for x in _rows(responses.get("lovs"))
        if x.get("name") and isinstance(x.get("model"), list)
    }  # e.g. BIT_CATEGORY
    return lookups, locations, lovs


def _attributes(
    body: Any, lookups: dict[str, dict[str, str]], locations: dict[str, str], lovs: dict[str, dict[str, str]]
) -> dict[str, Attribute]:
    """The attributes rules may use (enabled, available for rules, not BOOL), with their value lists."""
    out = {}
    for x in _rows(body):
        if not (x.get("enabled", True) and x.get("available_for_rules", True)) or x["data_type"] == "BOOL":
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
        )
    return out
