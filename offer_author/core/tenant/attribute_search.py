"""Which catalog attributes a manager's words refer to: retrieval for attribute grounding (AttributeGrounding.md).

Catalog.lookup finds a value within an attribute already chosen; this finds the attributes. One document per catalog
attribute (its label, code, type, where it sits and a few of its values) is ranked against short phrases of the words,
and each telling word on its own, by BM25 and, when a search is given (value_search.py), by meaning; the two rankings
are fused by rank. Separately, every run of one to three of the manager's words that is a value's label names that
value's attribute with the value: "Gold" finds tier_class GLDGLW and h_member_tier_class GLDGLW alike, and the
attribute agent decides between them. A whole description also gets the transaction's own type, amount and date-time,
which it rarely names as such. Code only: no model call. On the 46 hosted devintbf2 prompts it found 151 of the 152
catalog attributes the built rules read, in 0.09 s at the median (2026-11-05).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..vocabulary import TRANSACTION_FIELDS
from .catalog import bm25, norm

if TYPE_CHECKING:
    from .catalog import Attribute, Catalog, Search

log = logging.getLogger("offer_author.attribute_search")
POOL = 6  # candidates each ranking (words, meaning) adds before the two are fused
PER_PHRASE = 3  # attributes each phrase adds, its best first
SAMPLE = 6  # values shown per attribute
WINDOW = 3  # the longest run of the manager's words matched against value labels
PHRASE = 4  # telling words per phrase the attributes are ranked by
PER_WORD = 2  # attributes each telling word adds on its own ("birthday" outweighed by its phrase's other words)
POOL_WORD = 4  # candidates a single word's ranking adds before fusion
RRF = 60  # reciprocal rank fusion's constant (the usual one)


@dataclass(frozen=True)
class _Doc:
    obj: str
    code: str
    text: str


def where(obj: str, a: Attribute) -> str:
    """Where the attribute sits: the transaction's header, a line, a payment, the member's profile, a product."""
    if obj == "member":
        return "member profile (MA_P)"
    if obj == "product":
        return "product"
    return "transaction line" if a.line_level else "transaction payment" if a.payment_level else "transaction header"


def _docs(catalog: Catalog) -> list[_Doc]:
    out = []
    for obj, attrs in catalog.attributes.items():
        for code, a in attrs.items():
            sample = ", ".join(list((a.values or {}).values())[:SAMPLE])
            text = f"{a.label} ({code}): {a.type}, {where(obj, a)}" + (f"; values: {sample}" if sample else "")
            out.append(_Doc(obj, code, text))
    return out


def _values(catalog: Catalog) -> list[tuple[str, str, str, str]]:
    """Every coded value as (object, attribute, code, label).

    The attributes' value lists, and the tier programme's tiers (object "tier", attribute the tier class: MA_T).
    """
    out = [
        (obj, code, v, lab)
        for obj, attrs in catalog.attributes.items()
        for code, a in attrs.items()
        for v, lab in (a.values or {}).items()
    ]
    out += [("tier", cls, v, lab) for cls, tiers in catalog.tiers.items() for v, lab in tiers.items()]
    return out


def _value_matches(catalog: Catalog, text: str) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(object, attribute) -> [(value code, label)] for each run of the words that is a value's label (or its core).

    A label word that many values share (more than 2% of them, at least 3) is not telling: "store" in a list of stores
    names no store. Numbers are amounts and counts in a description, never value codes, and are not matched.
    """
    labels = _values(catalog)
    df = Counter(w for *_, lab in labels for w in set(norm(lab).split()))
    common = max(3, int(0.02 * len(labels)))
    words = norm(text).split()
    runs = {
        r
        for n in range(1, WINDOW + 1)
        for i in range(len(words) - n + 1)
        if not (r := " ".join(words[i : i + n])).replace(" ", "").isdigit()
    }
    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for obj, code, v, lab in labels:
        lw = norm(lab).split()
        if not lw or norm(lab).replace(" ", "").isdigit():
            continue
        exact = norm(lab) in runs or norm(v) in runs
        telling = {w for w in lw if df[w] <= common and len(w) > 2}
        part = bool(telling) and len(lw) <= WINDOW and any(set(r.split()) == telling for r in runs)
        if exact or part:
            out.setdefault((obj, code), []).append((v, lab))
    return out


def _phrases(text: str, docs: list[str]) -> list[str]:
    """Short phrases of the words to rank attributes by: runs of PHRASE telling words, overlapping.

    A function word ("of", "the"), a word many attribute documents hold (over 5% of them, at least 3) and a number
    (any word with a digit: an amount, "5th", a date) tell no attribute and are dropped; a few words is what an
    attribute's description can match.
    """
    toks = [set(norm(d).split()) for d in docs]
    df = Counter(w for t in toks for w in t)
    words = [
        w
        for w in norm(text).split()
        if not any(ch.isdigit() for ch in w) and w not in function_words() and df[w] <= max(3, 0.05 * len(docs))
    ]
    if len(words) <= PHRASE:
        return [" ".join(words)] if words else []
    starts = list(range(0, len(words) - PHRASE + 1, PHRASE // 2 or 1))
    if starts[-1] != len(words) - PHRASE:  # the last words always get a phrase of their own
        starts.append(len(words) - PHRASE)
    return [" ".join(words[i : i + PHRASE]) for i in starts]


def function_words() -> frozenset[str]:
    """English function words ("on", "of", "their").

    scikit-learn's list (installed with sentence-transformers); none without it.
    """
    try:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

        return frozenset(ENGLISH_STOP_WORDS)
    except ImportError:
        return frozenset()


def find(
    catalog: Catalog, text: str, search: Search | None = None, k: int | None = None, transaction: bool = False
) -> list[dict[str, Any]]:
    """The catalog attributes the words refer to, best first, with the values the words name.

    Args:
        catalog: The tenant's catalog.
        text: The manager's words (a whole prompt, or a few words).
        search: What ranks by meaning (value_search.ValueSearch), or None: by words only.
        k: Attributes kept from the ranking, at most (None: every phrase's and word's best); attributes whose values
            the words name are always kept.
        transaction: List the transaction's own type, amount and date-time first (TRANSACTION_FIELDS), whatever the
            words: for a whole description, which rarely names them as such.

    Returns:
        [{object, code, label, type, where, score, values?: [{code, label}] named, sample?: [labels]}].
    """
    docs = _docs(catalog)
    if not docs:
        return []
    texts = [d.text for d in docs]
    score = bm25(texts)
    named = _value_matches(catalog, text)
    index = {(d.obj, d.code): i for i, d in enumerate(docs)}
    phrases = _phrases(text, texts)
    single = [w for w in dict.fromkeys(" ".join(phrases).split()) if len(phrases) > 1]
    queries = [*((p, PER_PHRASE, POOL) for p in phrases), *((w, PER_WORD, POOL_WORD) for w in single)]
    ranks = _ranked(queries, texts, score, search)
    scores: dict[int, float] = {}
    ranked: list[int] = []  # each query's best few, in the queries' order: no phrase crowds out another
    for (_, top, _), got in zip(queries, ranks, strict=True):
        for i, sc in got.items():
            scores[i] = max(scores.get(i, 0.0), sc)
        ranked += sorted(got, key=lambda i: -got[i])[:top]
    own = [index[("activity", f)] for f in TRANSACTION_FIELDS if transaction and ("activity", f) in index]
    keep = list(dict.fromkeys(own + list(dict.fromkeys(ranked))[:k] + [index[key] for key in named if key in index]))
    out: list[dict[str, Any]] = []
    out_tiers: list[dict[str, Any]] = []
    for obj, cls in named:
        if obj == "tier":  # the tier programme (MA_T): a tier class, not an attribute of the library
            out_tiers.append(
                {
                    "object": "tier",
                    "code": cls,
                    "label": f"tier class {cls}",
                    "type": "LOOKUP",
                    "where": "member tier (MA_T)",
                    "score": 0.0,
                    "values": [{"code": v, "label": lab} for v, lab in named[(obj, cls)]],
                }
            )
    for i in keep:
        d = docs[i]
        a = catalog.attributes[d.obj][d.code]
        row: dict[str, Any] = {
            "object": d.obj,
            "code": d.code,
            "label": a.label,
            "type": a.type,
            "where": where(d.obj, a),
            "score": float(f"{scores.get(i, 0.0):.3g}"),
        }
        if (d.obj, d.code) in named:
            row["values"] = [{"code": v, "label": lab} for v, lab in named[(d.obj, d.code)]]
        elif a.values:
            row["sample"] = list(a.values.values())[:SAMPLE]
        out.append(row)
    return out + out_tiers


def _ranked(
    queries: list[tuple[str, int, int]], texts: list[str], score: Any, search: Search | None
) -> list[dict[int, float]]:
    """Each query's attribute documents, scored.

    By words (BM25, relative to its best); with a search, by meaning too (every query embedded in one batch) and the
    two rankings fused by rank.
    """
    pools, got = [], []
    for q, _, size in queries:
        words = score(q)
        best = max(words) or 1.0
        pool = [i for i in sorted(range(len(texts)), key=lambda i: -words[i]) if words[i] > 0][:size]
        pools.append(pool)
        got.append({i: words[i] / best for i in pool})
    if search is None or not queries:
        return got
    try:
        near = (
            search.nearest_many(texts, [q for q, _, _ in queries], max(s for _, _, s in queries))
            if hasattr(search, "nearest_many")
            else [search.nearest(texts, q, s) for q, _, s in queries]
        )
    except Exception:  # the words alone still rank
        log.exception("attribute search by meaning failed; ranking by words only")
        return got
    # each query's two rankings fused by rank (reciprocal rank fusion): no cross-encoder, whose ~11 ms per pair made a
    # long description take 4-5 s (410 pairs, 2026-11-05); recall on the 46 hosted prompts was the same
    out = []
    for p, n, (_, _, size) in zip(pools, near, queries, strict=True):
        fused: dict[int, float] = {}
        for ranking in (p, n[:size]):
            for r, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1 / (RRF + r)
        out.append(fused)
    return out


def read_by(xml: str | None) -> list[tuple[str, str]]:
    """The catalog attributes a canvas's blocks read, as (object, code): what saved rules already ground.

    Offer counters (MA_O) and the activity-level values the rules compute (MA_A, and anything the rules write) are not
    catalog attributes and are left out.
    """
    import re

    out: list[tuple[str, str]] = []
    written = set(re.findall(r'mta_attribute="([^"]+)"', xml or ""))
    for m in re.finditer(r'<block type="([^"]+)" id="[^"]+"><mutation([^>]*)>', xml or ""):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        code = a.get("attribute")
        if not code or code in written:
            continue
        obj = a.get("object") or ("activity" if m.group(1).startswith("activity") else "member")
        level = a.get("attribute_level") or a.get("attribute_type_input")
        if obj == "member" and level not in (None, "MA_P"):
            continue
        if obj in ("activity", "member", "product") and (obj, code) not in out:
            out.append((obj, code))
    return out
