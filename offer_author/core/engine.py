"""Run the UI's DRL in the real Gravty engine (harness/engine) on transactions the model proposes.

The model states what each BIT should earn; this only runs the engine and reports what it actually did, per BIT.
What the engine does before any rule is applied the way it does it: the member segment (segment DRL, the harness's
segment executor) and Offer Main's eligibility checks (OfferValidator.checkBitEligibility: BIT date inside the offer
dates, per-member usage limit per frequency; a filtered BIT never reaches the rules).
"""
import datetime as dt
import json
import os
import re
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JAR = os.environ.get("OFFER_AUTHOR_ENGINE_JAR", os.path.join(ROOT, "harness", "engine", "target", "harness.jar"))
OFFER_ID = 1001
BASE_DATE = dt.datetime(2026, 10, 20, 10, 0, 0)  # a Tuesday


def available():
    return os.path.exists(JAR)


def same(got, want):
    try:
        return abs(float(got) - float(want)) < 0.01
    except (TypeError, ValueError):
        return got is not None and str(got) == str(want)


def run(job, timeout=300, save_to=None):
    """One harness run of a job {offer_id, drl, member, products, privilege_products, bits, segment_drl?} -> its output.
    save_to keeps the job file (to rerun it by hand); otherwise a temp file is used and removed."""
    if save_to:
        os.makedirs(os.path.dirname(save_to), exist_ok=True)
        json.dump(job, open(save_to, "w"), indent=1)
        path = save_to
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(job, f)
        path = f.name
    try:
        p = subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=timeout)
    finally:
        if not save_to:
            os.unlink(path)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"compile": "crash", "messages": p.stderr[-800:]}


def period(date, freq):
    d = dt.datetime.fromisoformat(date)
    return {"day": d.date().isoformat(), "week": "%d-W%02d" % d.isocalendar()[:2], "month": d.strftime("%Y-%m")}.get(freq, "total")


def earned(bo):
    return bool(sum(x["total"] for x in bo.get("points", []) if x["action"] == "REWARD_POINTS") or bo.get("privileges"))


def run_offer(job, bits, offer_main=None, timeout=300, save_to=None):
    """The engine as it runs an offer: Offer Main's checks first (OfferValidator.checkBitEligibility: BIT date inside
    start/end, per-member usage limit per frequency), then the rules on the BITs that pass. offer_main: {start_date,
    end_date, max_usage_per_user, frequency}. -> the harness output with one entry per BIT; a filtered BIT is
    {"skipped": why}."""
    om = offer_main or {}
    start, end = (om.get("start_date") or "")[:10], (om.get("end_date") or "")[:10]
    skipped = {i: "outside the offer dates" for i, b in enumerate(bits)
               if (start and b["header"]["h_bit_date"][:10] < start) or (end and b["header"]["h_bit_date"][:10] > end)}
    limit, freq = om.get("max_usage_per_user") or 0, om.get("frequency") or "total"
    while True:  # limits depend on what earlier BITs earned: drop the first over-limit BIT and run again until stable
        keep = [i for i in range(len(bits)) if i not in skipped]
        out = run(dict(job, bits=[bits[i] for i in keep]), timeout, save_to)
        if out.get("compile") != "ok" or not limit:
            break
        used, over = {}, None
        for i, bo in zip(keep, out["bits"]):
            k = period(bits[i]["header"]["h_bit_date"], freq)
            if used.get(k, 0) >= limit:
                over = i
                break
            used[k] = used.get(k, 0) + earned(bo)
        if over is None:
            break
        skipped[over] = f"member limit {limit} per {freq} reached"
    if out.get("compile") == "ok":
        ran = iter(out.get("bits") or [])
        out["bits"] = [{"skipped": skipped[i]} if i in skipped else next(ran, {}) for i in range(len(bits))]
    return out


def simulate(drl, scenario, sponsor_id=10, program_id=1, segment_drl=None, offer_main=None, timeout=300):
    """scenario: {member: {attributes, tier}, products: {sku: {attr: v}}, privilege_products, bits: [{header, lines, expect}]}
    -> {compile, bits: [{points, privileges, counters, ok, skipped?}], member_in_segment?}.
    Counters: MA_O after the BIT, MA_A at its end."""
    bits = []
    for i, b in enumerate(scenario.get("bits") or []):
        h = {"h_bit_category": "ACCRUAL", "h_bit_type": "SPEND", "h_sponsor_id": sponsor_id, "h_program_id": program_id,
             "h_member_id": "M1", "h_bit_amount": 10, "h_bit_date": (BASE_DATE + dt.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S")}
        h.update(b.get("header") or {})
        lines = b.get("lines") or []
        if lines and "h_bit_amount" not in (b.get("header") or {}):
            h["h_bit_amount"] = sum(float(ln.get("l_amount") or 0) for ln in lines)
        bits.append({"header": h, "lines": lines})
    member = scenario.get("member") or {}
    job = {"offer_id": OFFER_ID, "drl": drl, "params": {}, "priority": 1,
           "member": {"id": "M1", "attributes": member.get("attributes") or {}, "tier": member.get("tier")},
           "products": scenario.get("products") or {}, "privilege_products": scenario.get("privilege_products") or {}}
    if segment_drl:
        m = re.search(r'addValidatedSegmentId\("([^"]+)"\)', segment_drl)
        job.update(segment_drl=segment_drl, segment_id=m.group(1) if m else "SEG")
    out = run_offer(job, bits, offer_main, timeout)
    if out.get("compile") != "ok":
        return {"compile": "error", "detail": str(out.get("messages"))[:1500]}
    rows = []
    for b, bo in zip(scenario.get("bits") or [], out["bits"]):
        pts = round(sum(x["total"] for x in bo.get("points", []) if x["action"] == "REWARD_POINTS"), 2)
        pv = [str(x.get("product_id", x.get("productId")) if isinstance(x, dict) else x) for x in bo.get("privileges") or []]
        ctr = {"mto": bo.get("mto_after", {}).get(str(OFFER_ID), {}), "mta": bo.get("mta_after_this_bit") or {}}
        exp = b.get("expect") or {}
        bad = []
        if "points" in exp and abs(pts - float(exp["points"])) >= 0.01:
            bad.append(f"points {pts} != {exp['points']}")
        if "privilege" in exp and (pv[:1] or [None])[0] != (str(exp["privilege"]) if exp["privilege"] is not None else None):
            bad.append(f"privilege {pv} != {exp['privilege']}")
        for lvl in ("mto", "mta"):
            for k, v in (exp.get(lvl) or {}).items():
                if not same(ctr[lvl].get(k), v):
                    bad.append(f"{lvl} {k} {ctr[lvl].get(k)} != {v}")
        rows.append({"points": pts, "privileges": pv, "counters": ctr, "status": bo.get("status"), "ok": not bad,
                     **({"skipped": bo["skipped"]} if "skipped" in bo else {}), **({"mismatch": bad} if bad else {})})
    res = {"compile": "ok", "bits": rows}
    if segment_drl:
        res["member_in_segment"] = (out.get("segment") or {}).get("member_in_segment")
    return res
