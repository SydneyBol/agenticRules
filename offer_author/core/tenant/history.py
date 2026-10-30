"""A tenant's earlier offers, matched to a new request, shown to the model as reference.

Never shown as part of the request (ai/prompt.py HISTORY_RULE); shown as Pulsar's RAG pairs: the manager's prompt and
the offer's sub-prompt lines. The store is a JSONL file per tenant (<history>/<tenant>/offers.jsonl, written by
harvest.py with each offer's sub-prompt from Pulsar's own code, api/harvest.py), one saved offer per line: {id, name,
prompt, description, xml, subprompt, updated, launched}; an offer without a prompt is matched by its name and
description, one without a sub-prompt is not shown. <history>/shared/offers.jsonl, when there, holds other programs'
offers: shown for their shape only, never by name. Every offer is vetted against the tenant's catalog as it is when
loaded (harvest.vet): a saved XML carries the types and codes of when it was built, so one that no longer fits (or,
from another program, never did) is not shown. Matching embeds the offers' prompts once per load, with the tenant's
values (catalog labels, numbers) replaced by what they are, so offers match by what they do ("every Nth spend by
members of a tier earns a privilege"), not by which store or tier they name. `embed` is injected (texts, kind
"document"|"query" -> unit vectors), so this module has no model code (embed.py has Bedrock's).
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from ..vocabulary import DISTINCT_VALUE_LISTS
from .harvest import read, vet

if TYPE_CHECKING:
    from .catalog import Catalog

SHARED = "shared"  # <history>/shared/offers.jsonl: other programs' offers
MAX_SHOWN = 2  # earlier offers shown per request, at most: a reference, not a template

Embed = Callable[[list[str], str], Any]  # (texts, "document" | "query") -> unit vectors (n, dims)


@dataclass(frozen=True)
class PastOffer:
    """One earlier offer.

    Attributes:
        id: Its offer id.
        name: Its name.
        text: The manager's description it was built from.
        subprompt: Pulsar's sub-prompt lines for its saved rules.
        launched: When it was launched ("" when unknown).
        own: The tenant's own offer (else another program's: matched by shape, never by name).
    """

    id: str
    name: str
    text: str
    subprompt: str
    launched: str = ""
    own: bool = True


def normal(text: str | None) -> str:
    """Text as lower-case words, punctuation dropped."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


class Masker:
    """Text -> text with the tenant's values replaced by their kind.

    A store or product becomes its attribute's label, a tier "tier", a privilege "privilege", a number "N" (the values
    that set one offer apart from another of the same kind; other value lists hold everyday words such as "Points" or
    "Member"). A value is found as a run of the description's words taken from its label, in any order, covering at
    least half of it ("a free Ski Dubai entry pass" is the privilege "Ski Dubai Free Entry Pass"). Activity types stay,
    also where another list uses their label: they say what the offer is about. Built from the catalog: this tenant's
    values, no word list.

    Attributes:
        key: The same for two maskers that mask the same way.
    """

    def __init__(self, catalog: Catalog | None) -> None:
        kinds = _kinds(catalog) if catalog else {}
        self.by_word: dict[str, list[tuple[set[str], str]]] = {}  # word -> [(label words, kind)]
        for lab, kind in kinds.items():
            for w in set(lab.split()):
                self.by_word.setdefault(w, []).append((set(lab.split()), kind))
        self.key = hash(frozenset(kinds.items()))

    def __call__(self, text: str) -> str:
        """The text with every value masked."""
        words, i = normal(text).split(), 0
        out: list[str] = []
        while i < len(words):
            run, kind = self._value_at(words, i)
            out.append(kind if run and kind else words[i])
            i += run or 1
        return re.sub(r"\b\d+(?: \d+)?(?:st|nd|rd|th|x)?\b", "N", " ".join(out))

    def _value_at(self, words: list[str], i: int) -> tuple[int, str | None]:
        """The longest value starting at words[i]: (how many words it covers, its kind), (0, None) when none."""
        run, kind = 0, None
        for lw, k in self.by_word.get(words[i], ()):
            j = i
            while j < len(words) and words[j] in lw:
                j += 1
            if j - i > run and 2 * len({*words[i:j]}) >= len(lw):
                run, kind = j - i, k
        return run, kind


def masker(catalog: Catalog | None) -> Masker:
    """The tenant's Masker."""
    return Masker(catalog)


def _kinds(catalog: Catalog) -> dict[str, str]:
    """The values Masker replaces (label -> its kind): labels of 3+ characters, not numbers or activity types."""
    labels = {
        normal(lab): normal(a.label)
        for obj, attrs in catalog.attributes.items()
        for a in attrs.values()
        if obj == "product" or a.lookup_type in DISTINCT_VALUE_LISTS
        for lab in (a.values or {}).values()
    }
    labels.update({normal(t): "tier" for tiers in catalog.tiers.values() for t in tiers.values()})
    labels.update({normal(p["name"]): "privilege" for p in catalog.privileges.values()})
    activities = {normal(lab) for lab in catalog.activity_types.values()}
    return {lab: kind for lab, kind in labels.items() if len(lab) >= 3 and not lab.isdigit() and lab not in activities}


def from_record(r: Mapping[str, Any], own: bool = True) -> PastOffer | None:
    """A JSONL record as a PastOffer, or None when it has no sub-prompt."""
    text = r.get("prompt") or ". ".join(x for x in (r.get("name"), r.get("description")) if x)
    if not r.get("subprompt") or not text:
        return None
    return PastOffer(str(r.get("id", "")), r.get("name") or "", text, r["subprompt"], r.get("launched") or "", own)


class History:
    """One tenant's earlier offers, embedded once, matched to requests."""

    def __init__(self, offers: Iterable[PastOffer], embed: Embed, mask: Callable[[str], str] = normal) -> None:
        self.offers, self.embed, self.mask = list(offers), embed, mask
        texts = [mask(o.text) for o in self.offers]
        self.vectors = np.asarray(embed(texts, "document")) if self.offers else np.zeros((0, 1))

    @classmethod
    def load(
        cls,
        path: str | None,
        embed: Embed,
        mask: Callable[[str], str] = normal,
        catalog: Catalog | None = None,
        shared: str | None = None,
    ) -> History:
        """The offers of `path` (and of `shared`, another programs' file) that vet clean against `catalog`."""
        offers = [
            from_record(r, own)
            for p, own in ((path, True), (shared, False))
            if p
            for r in read(p)
            if r.get("xml") and not vet(r["xml"], catalog)
        ]
        return cls([o for o in offers if o], embed, mask)

    def named(self, request: str, exclude_id: Any = None) -> list[PastOffer]:
        """Earlier offers whose name (3+ characters) appears in the request.

        As a fact: whether the manager refers to the offer or only uses the same words is the model's call
        (prompt.HISTORY_RULE).
        """
        req = normal(request)
        return [
            o
            for o in self.offers
            if o.own
            and o.id != str(exclude_id)
            and normal(o.text) != req
            and len(normal(o.name)) >= 3
            and re.search(rf"\b{re.escape(normal(o.name))}\b", req)
        ]

    def similar(
        self, request: str, k: int = MAX_SHOWN, exclude_id: Any = None, skip: Iterable[PastOffer] = ()
    ) -> list[tuple[float, PastOffer]]:
        """The k earlier offers most like the request, best first, as (similarity, offer).

        One per distinct sub-prompt, never the offer being edited (exclude_id) or one built from this same description
        (so a regenerated offer is not shown its own earlier answer), nor those in skip.
        """
        if not self.offers or k <= 0:
            return []
        scores = self.vectors @ np.asarray(self.embed([self.mask(request)], "query"))[0]
        req, seen = normal(request), set()
        out: list[tuple[float, PastOffer]] = []
        skip = list(skip)
        for i in np.argsort(-scores):
            if len(out) == k:
                break
            o = self.offers[i]
            if o.id == str(exclude_id) or normal(o.text) == req or o in skip or o.subprompt in seen:
                continue
            seen.add(o.subprompt)
            out.append((float(scores[i]), o))
        return out

    def earlier(self, request: str, k: int, exclude_id: Any = None) -> dict[str, Any]:
        """The offers prompt.intro shows, MAX_SHOWN at most.

        Returns:
            {named: [PastOffer], similar: [(similarity, PastOffer)]}.
        """
        named = self.named(request, exclude_id)[:MAX_SHOWN]
        return {
            "named": named,
            "similar": self.similar(request, min(k, MAX_SHOWN - len(named)), exclude_id, skip=named),
        }


def vetted_by(catalog: Catalog | None) -> int | None:
    """What vet() reads of a catalog (attribute types, codes, privileges): a change re-vets the stored offers.

    Not the catalog object: it is built again per request.
    """
    if catalog is None:
        return None
    return hash(
        (
            frozenset(
                (o, k, a.type, frozenset(a.values or ()))
                for o, attrs in catalog.attributes.items()
                for k, a in attrs.items()
            ),
            frozenset(catalog.privileges),
        )
    )


class Histories:
    """Per tenant History, loaded on first use and again when its file, the shared file or the tenant catalog changes.

    None when neither file is there.
    """

    def __init__(self, root: str | None, embed: Embed) -> None:
        self.root, self.embed, self.lock = root, embed, threading.Lock()
        self.cache: dict[str, tuple[tuple[Any, ...], History]] = {}

    def get(self, tenant: str | None, catalog: Catalog | None = None) -> History | None:
        """The tenant's History, or None when it has no history file and there is no shared one."""
        if not self.root or not tenant:
            return None
        own, shared = (
            p if os.path.exists(p) else None
            for p in (os.path.join(self.root, d, "offers.jsonl") for d in (tenant, SHARED))
        )
        if not own and not shared:
            return None
        mask = masker(catalog)
        key = (*(os.path.getmtime(p) if p else 0 for p in (own, shared)), mask.key, vetted_by(catalog))
        with self.lock:
            hit = self.cache.get(tenant)
            if not hit or hit[0] != key:
                hit = self.cache[tenant] = (key, History.load(own, self.embed, mask, catalog, shared))
            return hit[1]
