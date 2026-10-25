"""Full offer flow vs the individual component APIs, through the running backend, the stock UI and the engine.

  full        the combination is the OFFER MAIN prompt. As the stock wizard does:
                POST /ai/pulsar/offer/metadata {category offer}       -> Offer Main fields (+ the plan)
                POST /ai/pulsar/{id}/prompt/offermain                  -> the prompt store splits it
                GET  /ai/pulsar/{id}/prompt/offerrules|offersegmentation -> each screen's own prompt
                POST /ai/pulsar/offer/metadata {category segment}     -> segment name / description
                POST /ai/pulsar/segment/rules  (segment stage prompt)  -> segment XML
                POST /ai/pulsar/offer/rules    (rules stage prompt)    -> rules XML, built with the plan as context
  individual  each component API alone with its own prompt: /offer/rules with the combination prompt (no Offer Main
              prompt, no plan), so every condition must be in the rules.

Rules XML -> the stock rules screen (mock-run.mjs --tenant=devintam1, same DRL as the hosted screen) -> DRL.
Segment XML -> the stock segment screen (segment-run.mjs) -> segment DRL -> the engine's segment executor (harness
segment_drl): a member outside the segment gets nothing (filterOfferByTargetType).
Offer Main metadata -> the engine's pre-rule checks, emulated here as OfferValidator.checkBitEligibility does them:
BIT date inside start/end, and max_usage_per_user per frequency (an availment = a BIT the offer rewarded). A filtered
BIT never reaches the rules, so it changes no counter.

Same BIT expectations (tests/cases/hosted_combinations.yaml) for both modes.
Run: python3 -I tools/check_offer_flow.py full|individual|both [--only=H01,H10] [--backend=http://localhost:8077] [--reuse=1]
     [--tenant=devintam1]   other tenants: cases from tools/tenant_cases.py; the backend must run with that tenant's catalog
Writes tests/.live/flow_<mode>.json.
"""
import base64
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

import httpx
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_hosted_pulsar import bit, got  # noqa: E402
from tenant_cases import BASE, cases_file  # noqa: E402

TENANT = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tenant=")), BASE)
DOC = yaml.safe_load(open(cases_file(TENANT)))
SPONSOR = DOC.get("sponsor", 1)
DEFAULT_MEMBER = DOC.get("default_member", {"tier_class": "GOLD"})
TIER_ATTR = DOC.get("tier_attribute", "tier_class")
PRIVILEGES = {p: {"name": "privilege " + p, "cost": 1} for p in DOC.get("privileges", ["37", "64"])}
SUFFIX = "" if TENANT == BASE else "." + TENANT  # saved API answers per tenant

SPIKE = os.environ.get("OFFER_AUTHOR_SPIKE", os.path.join(os.path.dirname(HERE), "harness"))
JAR = os.path.join(SPIKE, "engine-harness", "target", "harness.jar")
CASES = os.path.join(SPIKE, "results", "engine", "cases")

ROOT = os.path.dirname(HERE)
LIVE = os.path.join(ROOT, "tests", ".live")
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
    row["metadata_status"], row["metadata"], plan = st, md.get("metadata"), md.get("plan") or {}
    row["plan"] = plan
    call(cl, "POST", f"/ai/pulsar/{oid}/prompt/offermain/", {"prompt": p, "offer_status": "planning"})
    rules_p = call(cl, "GET", f"/ai/pulsar/{oid}/prompt/offerrules/")[1].get("prompt", "")
    seg_p = call(cl, "GET", f"/ai/pulsar/{oid}/prompt/offersegmentation/")[1].get("prompt", "")
    row["stage_prompts"] = {"offerrules": rules_p, "offersegmentation": seg_p}
    row["stage_prompts_match_plan"] = (rules_p == (plan.get("rules") or {}).get("prompt", "")
                                       and seg_p == (plan.get("segment") or {}).get("prompt", ""))
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
    for k in ("status", "assumptions", "blocking_questions", "unsupported", "offer_settings"):
        row["rules_" + k] = r.get(k)
    return row


def stock_ui(mode, rows):
    """Rules XML through the stock rules screen, segment XML through the stock segment screen -> DRLs."""
    rin, rout = f"flow_{mode}", f"ui_flow_{mode}"
    sin, sout = os.path.join(SPIKE, "results", f"flow_{mode}_seg"), os.path.join(SPIKE, "results", f"ui_flow_{mode}_seg")
    for d in (rin, rout):
        shutil.rmtree(os.path.join(SPIKE, "results", d), ignore_errors=True)
    for d in (sin, sout):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(SPIKE, "results", rin))
    os.makedirs(sin)
    for k, r in rows.items():
        if r.get("rules_xml"):
            open(os.path.join(SPIKE, "results", rin, k + ".xml"), "w").write(r["rules_xml"])
            json.dump({"counters": []}, open(os.path.join(SPIKE, "results", rin, k + ".spec.json"), "w"))
        if r.get("segment_xml"):
            open(os.path.join(sin, k + ".xml"), "w").write(r["segment_xml"])
    p = subprocess.run(["node", "ui-harness/mock-run.mjs", "--path=upload", "--discover", "--fresh", f"--tenant={TENANT}",
                        f"--in={rin}", f"--out={rout}"], cwd=SPIKE, capture_output=True, text=True, timeout=3600)
    if p.returncode:
        raise SystemExit("mock-run failed: " + p.stderr[-1500:])
    rep = {k.split("@")[0]: v for k, v in json.load(open(os.path.join(SPIKE, "results", rout, "report.json"))).items()}
    for k, r in rows.items():
        e = rep.get(k) or {}
        drl = os.path.join(SPIKE, "results", rout, k + ".upload.drl")
        r["ui"] = {x: e.get(x) for x in ("loaded", "dropped_fields", "validation_errors", "exceptions", "drl_bytes")}
        r["drl"] = open(drl).read() if os.path.exists(drl) and e.get("drl_bytes") else None
    if any(r.get("segment_xml") for r in rows.values()):
        p = subprocess.run(["node", "segment-run.mjs", f"--in={sin}", f"--out={sout}", f"--tenant={TENANT}"], cwd=os.path.join(SPIKE, "ui-harness"),
                           capture_output=True, text=True, timeout=1800)
        if p.returncode:
            raise SystemExit("segment-run failed: " + p.stderr[-1500:])
        srep = json.load(open(os.path.join(sout, "report.json")))
        for k, r in rows.items():
            if r.get("segment_xml"):
                e = srep.get(k) or {}
                r["segment_ui"] = {"loaded": e.get("loaded"), "error": e.get("error"), "exceptions": e.get("exceptions"),
                                   "warnings": e.get("warnings")}
                r["segment_drl"] = e.get("drl") or None


# ---------------------------------------------------------------- engine
def period_key(date, freq):
    d = dt.datetime.fromisoformat(date)
    return {"day": d.date().isoformat(), "week": "%d-W%02d" % d.isocalendar()[:2], "month": d.strftime("%Y-%m")}.get(freq, "total")


def engine_once(case_base, bits, tag):
    case = dict(case_base, bits=bits)
    path = os.path.join(CASES, f"{tag}.json")
    json.dump(case, open(path, "w"), indent=1)
    p = subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"compile": "CRASH", "messages": p.stderr[-500:]}


def run_sequence(case_base, bits, meta, tag):
    """Engine run with Offer Main's pre-rule checks: out-of-date BITs and BITs past the member's limit never reach the
    rules. Limits depend on what earlier BITs earned, so drop the first over-limit BIT and run again until stable."""
    m = meta or {}
    stated = m.get("stated") or {}  # only dates the manager stated: no date = the offer runs from its creation
    start, end = (stated.get("start_date") or "")[:10], (stated.get("end_date") or "")[:10]
    filtered = {i: "outside the offer dates" for i, b in enumerate(bits)
                if (start and b["header"]["h_bit_date"][:10] < start) or (end and b["header"]["h_bit_date"][:10] > end)}
    limit, freq = m.get("max_usage_per_user") or 0, (m.get("option_value") or "total")
    while True:
        keep = [i for i in range(len(bits)) if i not in filtered]
        out = engine_once(case_base, [bits[i] for i in keep], tag)
        if out.get("compile") != "ok" or not limit:
            break
        used, over = {}, None
        for i, bo in zip(keep, out["bits"]):
            k = period_key(bits[i]["header"]["h_bit_date"], freq)
            if used.get(k, 0) >= limit:
                over = i
                break
            pts, priv = got(bo)
            used[k] = used.get(k, 0) + (1 if (pts or priv) else 0)
        if over is None:
            break
        filtered[over] = f"member limit {limit} per {freq} reached"
    if out.get("compile") == "ok":
        res, it = [], iter(out["bits"])
        for i in range(len(bits)):
            res.append({"skipped": filtered[i]} if i in filtered else next(it))
        out["bits"] = res
    return out


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
        drl = r["drl"]
        offer_id = int(re.search(r'rule "activity-(\d+)-', drl).group(1))
        m = re.search(r'readAttributeS\("h_bit_type"\)== \("([^"]+)"\)', drl)
        btype = m.group(1) if m else "SPEND"
        for qi, s in enumerate(c["sequences"]):
            member = dict(DEFAULT_MEMBER, **s.get("member", {}))
            bits = [bit(d, a, loc, btype) for d, a, loc, _ in s["bits"]]
            for b in bits:
                b["header"].update(h_sponsor_id=SPONSOR, h_program_id=DOC.get("program", 1))
            exp = [e for *_, e in s["bits"]]
            base = {"offer_id": offer_id, "drl": drl, "params": {}, "priority": 1,
                    "member": {"id": "M1", "attributes": member, "tier": member.get(TIER_ATTR)}, "products": {},
                    "privilege_products": PRIVILEGES}
            if r.get("segment_drl"):
                base["segment_drl"] = r["segment_drl"]
                base["segment_id"] = re.search(r'addValidatedSegmentId\("([^"]+)"\)', r["segment_drl"]).group(1)
            meta = dict(r.get("metadata") or {}, stated=(r.get("plan") or {}).get("metadata")) if mode == "full" else None
            out = run_sequence(base, bits, meta, f"flow_{mode}__{c['id']}__{qi}")
            if out.get("compile") != "ok":
                print(f"   seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages'))[:200]}")
                total += len(exp)
                r["engine"].append({"seq": qi, "compile": out.get("compile")})
                continue
            insg = (out.get("segment") or {}).get("member_in_segment")
            for bi, (b, e, bo) in enumerate(zip(bits, exp, out["bits"])):
                pts, priv = got(bo)
                good = (priv == e) if isinstance(e, str) or e is None else (abs(pts - e) < 0.01 and priv is None)
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
    os.makedirs(CASES, exist_ok=True)
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
            json.dump(rows, open(os.path.join(LIVE, f"flow_{mode}{SUFFIX}.json"), "w"), indent=1)
    print()
    for mode, (ok, total) in totals.items():
        print(f"{mode}: {ok}/{total} BIT expectations pass")
    sys.exit(0 if all(ok == total for ok, total in totals.values()) else 1)


if __name__ == "__main__":
    main()
