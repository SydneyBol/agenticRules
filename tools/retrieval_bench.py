"""Pick the attribute retriever: the manager's words for one attribute -> the right attribute in the top 15.

Compares, on tests/cases/retrieval.yaml (157 phrases, devintbf2 + the scaled catalog):
  lexical   today's lookup scoring (word recall 0.6, label cover 0.25, string similarity 0.15)
  bm25      BM25 over the attribute text
  dense     a sentence-transformers bi-encoder alone
  hybrid    BM25 + bi-encoder, reciprocal rank fusion (k=60)
  +rerank   hybrid top 50 reranked by a cross-encoder
at each catalog's own size and padded with realistic look-alike attributes (labels recombined from both tenants'
real label words) to 3,000 and 5,000 attributes per tenant.
Metrics: recall@1/5/15, MRR, query latency p50/p95, index build seconds.
Design: ~/Documents/Gravty/ai-agent/AttributeRetrieval.md

Runs where sentence-transformers and torch are installed (not the repo venv):
  ~/PycharmProjects/ai/.venv/bin/python -I tools/retrieval_bench.py [--sizes=0,3000,5000] [--out=harness/results/retrieval_bench.json]
"""
import difflib
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
import yaml  # noqa: E402

from offer_author.core.catalog import Attribute, Catalog, norm, words  # noqa: E402
from scaled_catalog import build  # noqa: E402

ARGS = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
SIZES = [int(x) for x in ARGS.get("sizes", "0,3000,5000").split(",")]
OUT = os.path.join(ROOT, ARGS.get("out", "harness/results/retrieval_bench.json"))
EMBEDDERS = ["sentence-transformers/all-mpnet-base-v2", "BAAI/bge-small-en-v1.5", "Snowflake/snowflake-arctic-embed-m-v2.0",
             "Qwen/Qwen3-Embedding-0.6B"]
RERANKERS = ["cross-encoder/ms-marco-MiniLM-L6-v2", "BAAI/bge-reranker-base", "BAAI/bge-reranker-v2-m3"]
BGE_QUERY = "Represent this sentence for searching relevant passages: "
K, POOL = 15, 50


def doc(a):
    """What an attribute is searched by: its code in words, its label and type, a few value labels."""
    code = re.sub(r"[_.\-]+", " ", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", a.code))
    vals = ", ".join(list((a.values or {}).values())[:5])
    return f"{a.label} ({code}) {a.type}" + (f": {vals}" if vals else "")


def toks(s):
    return re.findall(r"[a-z0-9]+", s.lower())


# ------------------------------------------------------------------------------------------------ catalogs
def padded(cat, n, rnd):
    """The catalog with look-alike attributes added until each object has n: labels recombined from real label words."""
    if not n:
        return cat
    pool = [w for c in CATS.values() for o in c.attributes.values() for a in o.values() for w in toks(a.label) if len(w) > 2]
    out = Catalog(**{k: getattr(cat, k) for k in cat.__dataclass_fields__})
    out.attributes = {o: dict(v) for o, v in cat.attributes.items()}
    for obj, attrs in out.attributes.items():
        labels = {norm(a.label) for a in attrs.values()}
        i = 0
        while len(attrs) < n:
            i += 1
            label = " ".join(w.capitalize() for w in rnd.sample(pool, rnd.randint(2, 4)))
            if norm(label) in labels:
                continue
            labels.add(norm(label))
            code = "_".join(toks(label))[:40] + f"_{i}"
            attrs[code] = Attribute(code=code, label=label, type=rnd.choice(["TEXT", "NUMBER", "DATE", "LOOKUP"]))
    return out


CATS = {"devintbf2": Catalog.from_bolapi_dumps(os.path.join(ROOT, "catalogs", "devintbf2", "bolapi"), "devintbf2", 1, "9", "BASE"),
        "scaled": build()}


# ------------------------------------------------------------------------------------------------ retrievers
def lexical(q, docs):
    qn, qw = norm(q), words(q)
    out = []
    for code, a in docs:
        cw, lw = words(a.code.replace("_", " ")) | words(a.label), words(a.label)
        recall = len(qw & cw) / len(qw) if qw else 0
        cover = len(qw & lw) / len(lw) if lw else 0
        sim = max(difflib.SequenceMatcher(None, qn, norm(a.label)).ratio(), difflib.SequenceMatcher(None, qn, norm(a.code)).ratio())
        out.append((0.6 * recall + 0.25 * cover + 0.15 * sim, code))
    return [c for _, c in sorted(out, key=lambda t: -t[0])]


class BM25:
    def __init__(self, texts, k1=1.2, b=0.75):
        self.docs = [Counter(toks(t)) for t in texts]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.len) / len(self.len)
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}
        self.k1, self.b = k1, b

    def rank(self, q):
        qt = toks(q)
        s = []
        for i, d in enumerate(self.docs):
            v = sum(self.idf.get(w, 0) * d[w] * (self.k1 + 1) / (d[w] + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
                    for w in qt if w in d)
            s.append(v)
        return sorted(range(len(s)), key=lambda i: -s[i])


def rrf(*rankings, k=60):
    score = Counter()
    for r in rankings:
        for pos, i in enumerate(r[:200]):
            score[i] += 1 / (k + pos + 1)
    return [i for i, _ in score.most_common()]


def load_embedder(name, device):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(name, device=device, trust_remote_code=False)

    def enc(texts, query=False):
        kw = {}
        if query and "query" in (m.prompts or {}):
            kw["prompt_name"] = "query"
        elif query and "bge-" in name:
            texts = [BGE_QUERY + t for t in texts]
        return m.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True, **kw)
    return enc


def load_reranker(name, device):
    from sentence_transformers import CrossEncoder
    return CrossEncoder(name, device=device, trust_remote_code=False)


# ------------------------------------------------------------------------------------------------ the bench
def metrics(ranks, lat=None, build_s=None):
    found = [r for r in ranks]
    out = {f"recall@{k}": round(sum(1 for r in found if r is not None and r < k) / len(found), 4) for k in (1, 5, 15)}
    out["mrr"] = round(sum(1 / (r + 1) for r in found if r is not None) / len(found), 4)
    if lat:
        out.update(p50_ms=round(statistics.median(lat) * 1000, 1), p95_ms=round(sorted(lat)[int(0.95 * len(lat)) - 1] * 1000, 1))
    if build_s is not None:
        out["index_s"] = round(build_s, 2)
    return out


def first_hit(order, want):
    return next((i for i, c in enumerate(order) if c in want), None)


def main():
    import numpy as np
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    cases = yaml.safe_load(open(os.path.join(ROOT, "tests", "cases", "retrieval.yaml")))
    rnd = random.Random(11)
    suites = []  # (tenant, size, obj, [(code, attr)], [case])
    for tenant, objs in cases.items():
        for size in SIZES:
            cat = padded(CATS[tenant], size, rnd)
            for obj, cs in objs.items():
                suites.append((tenant, size, obj, list(cat.attributes[obj].items()), cs))
    results, misses = {}, {}

    def record(name, tenant, size, ranks, lat=None, build_s=None, wrong=None):
        results.setdefault(name, {})[f"{tenant}@{size or 'own'}"] = metrics(ranks, lat, build_s)
        if wrong:
            misses.setdefault(name, {})[f"{tenant}@{size or 'own'}"] = wrong

    def run(name, fn, per_suite_index=None):
        by = {}
        for tenant, size, obj, docs, cs in suites:
            t0 = time.time()
            idx = per_suite_index(docs) if per_suite_index else None
            b = time.time() - t0
            r = by.setdefault((tenant, size), {"ranks": [], "lat": [], "build": 0, "wrong": []})
            r["build"] += b
            for c in cs:
                t1 = time.time()
                order = fn(c["q"], docs, idx)
                r["lat"].append(time.time() - t1)
                h = first_hit(order, {str(w) for w in c["want"]})
                r["ranks"].append(h)
                if h is None or h >= 5:
                    r["wrong"].append({"q": c["q"], "obj": obj, "want": c["want"], "top3": order[:3], "rank": h})
        for (tenant, size), r in by.items():
            record(name, tenant, size, r["ranks"], r["lat"], r["build"], r["wrong"])
        print(name, json.dumps(results[name]), flush=True)

    run("lexical", lambda q, docs, idx: lexical(q, docs))
    run("bm25", lambda q, docs, idx: [docs[i][0] for i in idx.rank(q)], lambda docs: BM25([doc(a) for _, a in docs]))

    for emb_name in EMBEDDERS:
        try:
            t0 = time.time()
            enc = load_embedder(emb_name, device)
            print(f"loaded {emb_name} in {time.time() - t0:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001 - a model that needs remote code or fails to load is reported, not run
            results[emb_name] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
            print(emb_name, "ERROR", results[emb_name]["error"], flush=True)
            continue

        def index(docs):
            return {"bm25": BM25([doc(a) for _, a in docs]), "vec": enc([doc(a) for _, a in docs])}

        def dense(q, docs, idx):
            s = idx["vec"] @ enc([q], query=True)[0]
            return [docs[i][0] for i in np.argsort(-s)]

        def hybrid_ids(q, idx):
            s = idx["vec"] @ enc([q], query=True)[0]
            return rrf(idx["bm25"].rank(q), list(np.argsort(-s)))

        cache = {}

        def cached_index(docs):
            key = id(docs)
            if key not in cache:
                cache[key] = index(docs)
            return cache[key]
        short = emb_name.split("/")[-1]
        run(f"dense:{short}", dense, cached_index)
        run(f"hybrid:{short}", lambda q, docs, idx: [docs[i][0] for i in hybrid_ids(q, idx)], cached_index)
        for rr_name in RERANKERS:
            try:
                ce = load_reranker(rr_name, device)
            except Exception as e:  # noqa: BLE001
                results[rr_name] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
                print(rr_name, "ERROR", results[rr_name]["error"], flush=True)
                continue

            def reranked(q, docs, idx, ce=ce):
                ids = hybrid_ids(q, idx)[:POOL]
                sc = ce.predict([(q, doc(docs[i][1])) for i in ids], batch_size=64)
                top = [ids[j] for j in np.argsort(-np.asarray(sc))]
                return [docs[i][0] for i in top]
            run(f"hybrid:{short}+{rr_name.split('/')[-1]}", reranked, cached_index)
            del ce
        cache.clear()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"device": device, "sizes": SIZES, "cases": sum(len(s[4]) for s in suites if s[1] == SIZES[0]),
               "results": results, "misses": misses}, open(OUT, "w"), indent=1)
    print("->", OUT)


if __name__ == "__main__":
    main()
