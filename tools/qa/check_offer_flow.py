"""Full offer flow vs the individual component APIs, through the running backend, the stock UI and the engine.

  full        the combination is the OFFER MAIN prompt. As the stock wizard does:
                POST /ai/pulsar/offer/metadata {category offer}       -> Offer Main fields (+ what the agent set)
                POST /ai/pulsar/{id}/prompt/offermain                  -> links the offer to that one agent run
                GET  /ai/pulsar/{id}/prompt/offerrules|offersegmentation -> each screen's own prompt
                POST /ai/pulsar/offer/metadata {category segment}     -> segment name / description
                POST /ai/pulsar/segment/rules  (segment stage prompt)  -> segment XML
                POST /ai/pulsar/offer/rules    (rules stage prompt)    -> rules XML from the same run
  individual  each component API alone with its own prompt: /offer/rules with the combination prompt (no Offer Main
              prompt, no Offer Main), so every condition must be in the rules.

Rules XML -> the stock rules screen (harness/ui/compile/mock-run.mjs, same DRL as the hosted screen) -> DRL.
Segment XML -> the stock segment screen (segment-run.mjs) -> segment DRL -> the engine's segment executor (harness
segment_drl): a member outside the segment gets nothing (filterOfferByTargetType).
Offer Main metadata -> the engine's pre-rule checks (offer_author.core.engine.run_offer, as
OfferValidator.checkBitEligibility does them): BIT date inside start/end, and max_usage_per_user per frequency. A
filtered BIT never reaches the rules, so it changes no counter.

Same BIT expectations (tests/cases/hosted_combinations.yaml) for both modes.
Run: python3 -I tools/qa/check_offer_flow.py full|individual|both [--only=H01,H10] [--backend=http://localhost:8077] [--reuse=1]
     [--tenant=devintam1]   other tenants: cases from tools/qa/tenant_cases.py; the backend must run with that tenant's catalog
Writes tests/.live/flow_<mode>[.<tenant>].json (--only updates those cases in it).
"""
import base64
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root, as a script
from tools.paths import LIVE  # noqa: E402
from tools.qa import stock_ui as stock  # noqa: E402
from tools.qa.engine_cases import got, passes, run, sequence  # noqa: E402
from tools.qa.tenant_cases import BASE, load  # noqa: E402

TENANT = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tenant=")), BASE)
DOC = load(TENANT)
SPONSOR = DOC.get("sponsor", 1)
DEFAULT_MEMBER, TIER_ATTR = DOC["default_member"], DOC["tier_attribute"]
SUFFIX = "" if TENANT == BASE else "." + TENANT  # saved API answers per tenant
OFFER_BASE = {"full": 970000, "individual": 980000}  # synthetic offer ids: nothing shared with the hosted offer


def call(cl, method, path, body=None):
    """Poll a 'Generating' answer as the screens do."""
    for _ in range(60):
        r = cl.request(method, path, json=body)
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if j.get("result") != "Generating":
            return r.status_code, j
        time.sleep(2)
    return r.status_code, j


def full_flow(cl, c, oid):
    row, p = {"offer_id": oid}, c["prompt"]
    st, md = call(cl, "POST", "/ai/pulsar/offer/metadata", {"prompt": p, "category": "offer"})
    row["metadata_status"], row["metadata"], om = st, md.get("metadata"), md.get("offer_main") or {}
    row["offer_main"] = om
    call(cl, "POST", f"/ai/pulsar/{oid}/prompt/offermain/", {"prompt": p, "offer_status": "planning"})
    rules_p = call(cl, "GET", f"/ai/pulsar/{oid}/prompt/offerrules/")[1].get("prompt", "")
    seg_p = call(cl, "GET", f"/ai/pulsar/{oid}/prompt/offersegmentation/")[1].get("prompt", "")
    row["stage_prompts"] = {"offerrules": rules_p, "offersegmentation": seg_p}
    row["stage_prompts_match_offer_main"] = rules_p == om.get("rules_prompt", "") and seg_p == om.get("segment_prompt", "")
    if seg_p.strip():
        row["segment_metadata"] = call(cl, "POST", "/ai/pulsar/offer/metadata", {"prompt": seg_p, "category": "segment"})[1].get("metadata")
        st, s = call(cl, "POST", "/ai/pulsar/segment/rules", {"prompt": seg_p, "sponsor_id": SPONSOR, "id": oid, "type": "reward", "rule_id": c["id"]})
        row["segment_status"] = st
        row["segment_xml"] = base64.b64decode(s["xml"]).decode() if s.get("xml") else None
        row["segment_error"] = None if s.get("xml") else s.get("error")
    st, r = call(cl, "POST", "/ai/pulsar/offer/rules?regenerate=true", {"prompt": rules_p, "sponsor_id": SPONSOR, "id": oid, "type": "reward"})
    return rules_result(row, st, r)


def individual(cl, c, oid):
    st, r = call(cl, "POST", "/ai/pulsar/offer/rules?regenerate=true", {"prompt": c["prompt"], "sponsor_id": SPONSOR, "id": oid, "type": "reward"})
    return rules_result({"offer_id": oid}, st, r)


def rules_result(row, st, r):
    row["rules_status"] = st
    row["rules_xml"] = base64.b64decode(r["xml"]).decode("ascii") if r.get("xml") else None
    row["rules_error"] = None if r.get("xml") else r.get("error")
    for k in ("status", "assumptions", "blocking_questions", "unsupported"):
        row["rules_" + k] = r.get(k)
    return row


def stock_ui(mode, rows):
    """Rules XML through the stock rules screen, segment XML through the stock segment screen -> DRLs."""
    rep, out = stock.upload({k: r["rules_xml"] for k, r in rows.items() if r.get("rules_xml")}, f"flow_{mode}", TENANT)
    for k, r in rows.items():
        e = rep.get(k) or {}
        drl = os.path.join(out, k + ".upload.drl")
        r["ui"] = {x: e.get(x) for x in ("loaded", "dropped_fields", "validation_errors", "exceptions", "drl_bytes")}
        r["drl"] = open(drl).read() if os.path.exists(drl) and e.get("drl_bytes") else None
    seg = {k: r["segment_xml"] for k, r in rows.items() if r.get("segment_xml")}
    if seg:
        srep = stock.segments(seg, f"flow_{mode}_seg", TENANT)
        for k in seg:
            e = srep.get(k) or {}
            rows[k]["segment_ui"] = {x: e.get(x) for x in ("loaded", "error", "exceptions", "warnings")}
            rows[k]["segment_drl"] = e.get("drl") or None


# ---------------------------------------------------------------- engine
def offer_main(r, mode):
    """The Offer Main checks the engine applies before the rules (full mode): only dates the manager stated."""
    if mode != "full":
        return None
    m, stated = r.get("metadata") or {}, r.get("offer_main") or {}
    return {"start_date": stated.get("start_date"), "end_date": stated.get("end_date"),
            "max_usage_per_user": m.get("max_usage_per_user"), "frequency": m.get("option_value") or "total"}


def check(mode, cases, rows):
    total = ok = 0
    for c in cases:
        r = rows[c["id"]]
        n = sum(len(s["bits"]) for s in c["sequences"])
        r["engine"] = []
        why = None
        if not r.get("drl"):
            why = f"NO RULES DRL ({r.get('rules_status')} {json.dumps(r.get('rules_error') or r.get('rules_blocking_questions'))[:160]})"
        elif r.get("segment_xml") is None and (r.get("stage_prompts") or {}).get("offersegmentation", "").strip():
            why = f"SEGMENT NOT BUILT ({r.get('segment_status')} {json.dumps(r.get('segment_error'))[:160]})"
        elif r.get("segment_xml") and not r.get("segment_drl"):
            why = f"SEGMENT DRL MISSING ({json.dumps(r.get('segment_ui'))[:160]})"
        seg = (r.get("stage_prompts") or {}).get("offersegmentation", "")
        print(f"{c['id']}  [{'segment: ' + seg if seg.strip() else 'no segment'}]"
              + (f"  [limit {r['metadata'].get('max_usage_per_user')} {r['metadata'].get('option_value')}]"
                 if (r.get("metadata") or {}).get("max_usage_per_user") else ""))
        if why:
            print("   " + why)
            total += n
            continue
        for qi, s in enumerate(c["sequences"]):
            job, bits, exp, member = sequence(DOC, s, r["drl"], DEFAULT_MEMBER, TIER_ATTR, DOC["privilege_products"],
                                              r.get("segment_drl"))
            out = run(job, bits, f"flow_{mode}__{c['id']}__{qi}", offer_main(r, mode))
            if out.get("compile") != "ok":
                print(f"   seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages'))[:200]}")
                total += len(exp)
                r["engine"].append({"seq": qi, "compile": out.get("compile")})
                continue
            insg = (out.get("segment") or {}).get("member_in_segment")
            for bi, (b, e, bo) in enumerate(zip(bits, exp, out["bits"])):
                pts, priv = got(bo)
                good = passes(e, pts, priv)
                total += 1
                ok += good
                h = b["header"]
                note = bo.get("skipped", "")
                print(f"   seq{qi} bit{bi} {h['h_bit_date'][:16]} {h['h_location']:7s} ${h['h_bit_amount']:<6} {member[TIER_ATTR]:7s}"
                      f"{'' if insg is None else (' in-seg ' if insg else ' OUT-seg')} expect={e!s:5s} got=pts {pts} priv {priv} "
                      f"{'PASS' if good else 'FAIL'}{'  (' + note + ')' if note else ''}")
                r["engine"].append({"seq": qi, "bit": bi, "expect": e, "points": pts, "privilege": priv, "pass": good, "note": note})
    return ok, total


def main():
    argv = sys.argv[1:]
    modes = [a for a in argv if not a.startswith("--")] or ["both"]
    modes = ["full", "individual"] if modes == ["both"] else modes
    opt = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    cases = DOC["cases"]
    if opt.get("only"):
        cases = [c for c in cases if any(c["id"].startswith(o) for o in opt["only"].split(","))]
    os.makedirs(LIVE, exist_ok=True)
    totals = {}
    with httpx.Client(base_url=opt.get("backend", "http://localhost:8077"), timeout=180) as cl:
        for mode in modes:
            saved = os.path.join(LIVE, f"flow_{mode}{SUFFIX}.json")
            if "reuse" in opt and os.path.exists(saved):  # --reuse=1: the last run's API answers, re-checked (no model calls)
                rows = {k: v for k, v in json.load(open(saved)).items() if k in {c["id"] for c in cases}}
            else:
                rows = {}
                for i, c in enumerate(cases, 1):
                    t0 = time.time()
                    rows[c["id"]] = (full_flow if mode == "full" else individual)(cl, c, OFFER_BASE[mode] + i)
                    rows[c["id"]]["latency_s"] = round(time.time() - t0, 1)
            stock_ui(mode, rows)
            print(f"\n==== {mode} ====")
            totals[mode] = check(mode, cases, rows)
            if opt.get("only") and os.path.exists(saved):  # a partial run updates its cases, keeps the others
                rows = dict(json.load(open(saved)), **rows)
            json.dump(rows, open(saved, "w"), indent=1)
    print()
    for mode, (ok, total) in totals.items():
        print(f"{mode}: {ok}/{total} BIT expectations pass")
    sys.exit(0 if all(ok == total for ok, total in totals.values()) else 1)


if __name__ == "__main__":
    main()
