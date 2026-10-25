"""Run every scenario's UI-generated DRL (results/ui/<sid>.drl) through the engine harness, one case per
sequence in scenarios/scenarios.yaml, and compare each BIT's bonus points / privilege with the expectation.

Run: python3 -I run_scenarios.py   (needs PyYAML available to the system python, or pass --venv python)
"""
import datetime as dt, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "llm-eval", ".venv", "lib"))
import yaml  # noqa: E402

UI_DIR = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--ui=")), "ui")  # results/<dir> holding <sid>.drl
UI = os.path.join(ROOT, "results", UI_DIR)
TENANT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tenant=")), None)  # see compiler/tenants.py
sys.path.insert(0, os.path.join(ROOT, "compiler"))
from tenants import TENANTS, remap  # noqa: E402
OUT = os.path.join(ROOT, "results", "engine")
CASES = os.path.join(OUT, "cases")
JAR = os.path.join(HERE, "target", "harness.jar")
SKU_CATEGORY = {"SNK-1": "SNACKS", "SNK-2": "SNACKS", "DRK-1": "DRINKS", "BAK-1": "BAKERY"}
BASE_DATE = dt.datetime(2026, 10, 20, 10, 0, 0)  # a Tuesday


def tenant_fixtures():
    """Scenarios plus SKU categories, sponsor and privilege catalog, in the tenant's codes when --tenant is set."""
    scen = yaml.safe_load(open(os.path.join(ROOT, "scenarios", "scenarios.yaml")))
    if not TENANT:
        return scen, SKU_CATEGORY, 10, {"501": {"name": "Free Coffee", "cost": 4.5}}
    t = TENANTS[TENANT]
    privs = {pid: {"name": "privilege " + pid, "cost": 1} for pid in t["catalog"]["privileges"]}
    return remap(scen, TENANT), remap(SKU_CATEGORY, TENANT), t["sponsor_id"], privs


def build_bits(seq, defaults):
    bits = []
    for i, b in enumerate(seq["bits"]):
        if "event" in b:
            return None  # periodic event BITs not supported by the harness yet
        h = {"h_bit_category": "ACCRUAL", "h_bit_type": "SPEND", "h_sponsor_id": SPONSOR, "h_program_id": 1,
             "h_member_id": "M1", "h_location": LOCATION, "h_bit_amount": 10,
             "h_bit_date": (BASE_DATE + dt.timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%S")}
        h.update(defaults)
        h.update({k: v for k, v in b.items() if k.startswith("h_")})
        lines = [{"l_product_external_id": ln["sku"], "l_quantity": ln["qty"], "l_amount": ln["amount"]} for ln in b.get("lines", [])]
        if lines and "h_bit_amount" not in b:
            h["h_bit_amount"] = sum(ln["l_amount"] for ln in lines)
        bits.append({"header": h, "lines": lines, "_expect": b.get("expect", {})})
    return bits


def run_case(case):
    path = os.path.join(CASES, f"{UI_DIR}__{case['_name']}.json")
    json.dump({k: v for k, v in case.items() if not k.startswith("_")}, open(path, "w"), indent=1)
    p = subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=300)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"compile": "crash", "stderr": p.stderr[-2000:], "stdout": p.stdout[-2000:]}


def bonus_points(bit_out):
    return round(sum(pt["total"] for pt in bit_out.get("points", []) if pt["action"] == "REWARD_POINTS"), 2)


def privilege(bit_out):
    pv = bit_out.get("privileges", [])
    if not pv:
        return None
    first = pv[0]
    return str(first.get("product_id", first.get("productId", first)) if isinstance(first, dict) else first)


def main():
    os.makedirs(CASES, exist_ok=True)
    global SPONSOR, LOCATION
    scen, sku_category, SPONSOR, privilege_products = tenant_fixtures()
    LOCATION = remap("L1", TENANT) if TENANT else "L1"
    base_member = scen["fixtures"]["member"]
    results, total, passed, gaps = [], 0, 0, 0
    for s in scen["scenarios"]:
        sid = s["id"]
        drl_path = os.path.join(UI, sid + ".drl")
        if "sequences" not in s:
            continue
        if not os.path.exists(drl_path):
            results.append({"scenario": sid, "status": "NO_DRL"})
            print(f"{sid:40s} NO_DRL (not compiled / not in UI run)")
            continue
        drl = open(drl_path).read()
        # a DRL saved by the hosted UI has the real offer id baked in (pointsAction(..., <id>, ...)); match it
        m = re.search(r'rule "activity-(\d+)-', drl)
        offer_id = int(m.group(1)) if m else 1001
        for qi, seq in enumerate(s["sequences"]):
            member = dict(base_member, **seq.get("member_override", {}))
            bits = build_bits(seq, {})
            if bits is None:
                results.append({"scenario": sid, "sequence": qi, "status": "SKIPPED_EVENT"})
                print(f"{sid:40s} seq{qi} SKIPPED (needs periodic EVENT bits)")
                continue
            attrs = {k: v for k, v in member.items() if k not in ("id",)}
            case = {"_name": f"{sid}__seq{qi}", "offer_id": offer_id, "drl": drl, "params": {}, "priority": 1,
                    "member": {"id": "M1", "attributes": attrs, "tier": member.get("tier", member.get("tier_class"))},
                    "products": {k: {"category": v} for k, v in sku_category.items()},
                    "privilege_products": privilege_products,
                    "bits": [{k: v for k, v in b.items() if not k.startswith("_")} for b in bits]}
            out = run_case(case)
            if out.get("compile") != "ok":
                results.append({"scenario": sid, "sequence": qi, "status": "COMPILE_" + str(out.get("compile")), "detail": out})
                print(f"{sid:40s} seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages', out.get('stderr', '')))[:300]}")
                continue
            for bi, (b, bo) in enumerate(zip(bits, out["bits"])):
                exp = b["_expect"]
                if not exp:
                    continue
                got = {"points": bonus_points(bo), "privilege": privilege(bo)}
                ok = True
                if "points" in exp:
                    ok &= abs(got["points"] - exp["points"]) < 0.01
                if "privilege" in exp:
                    ok &= (got["privilege"] == (str(exp["privilege"]) if exp["privilege"] is not None else None))
                known_gap = seq.get("known_gap", False)
                total += 1
                passed += ok
                gaps += (not ok and known_gap)
                row = {"scenario": sid, "sequence": qi, "bit": bi, "date": b["header"]["h_bit_date"], "expect": exp, "got": got,
                       "status": "PASS" if ok else ("KNOWN_GAP" if known_gap else "FAIL"), "rules_fired": bo.get("rules_fired"),
                       "error": bo.get("error") or bo.get("exception")}
                results.append(row)
                if not ok:
                    print(f"{sid:40s} seq{qi} bit{bi} {row['status']} expect={exp} got={got} {row['error'] or ''}")
            print(f"{sid:40s} seq{qi} done")
    json.dump(results, open(os.path.join(OUT, f"engine_scenarios_{UI_DIR}{'_' + TENANT if TENANT else ''}.json"), "w"), indent=1)
    print(f"\nBIT expectations: {passed}/{total} pass, {gaps} known-gap failures, {total - passed - gaps} unexpected failures")


if __name__ == "__main__":
    main()
