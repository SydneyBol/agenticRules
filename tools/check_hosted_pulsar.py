"""Run the DRL the stock hosted UI saved for the ZZ-AI-SPIKE-P* offers through the real engine harness (spike),
with transaction sequences in devintam1 codes. Prints one line per BIT and a total; exit 1 on any mismatch.

Run: python3 -I tools/check_hosted_pulsar.py [spike_dir]
"""
import json
import os
import re
import subprocess
import sys

# the spike dir is an optional directory argument (other tools import SPIKE with their own positional args)
SPIKE = next((a for a in sys.argv[1:] if os.path.isdir(a)), os.environ.get("OFFER_AUTHOR_SPIKE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "harness")))
HOSTED = os.path.join(SPIKE, "results", "hosted_pulsar")
JAR = os.path.join(SPIKE, "engine-harness", "target", "harness.jar")
CASES = os.path.join(SPIKE, "results", "engine", "cases")


def bit(date, amount=60, location="HQ", btype=None):
    return {"header": {"h_bit_category": "ACCRUAL", "h_bit_type": btype, "h_sponsor_id": 1, "h_program_id": 1, "h_member_id": "M1",
                       "h_location": location, "h_bit_amount": amount, "h_bit_date": date}, "lines": []}


def seq(btype, *rows):
    """rows: (date, amount, location, expect) with expect = points (number) or privilege id (str) or None."""
    bits, exp = [], []
    for d, a, loc, e in rows:
        b = bit(d, a, loc, btype)
        bits.append(b)
        exp.append(e)
    return bits, exp


def plans(btypes):
    sat, tue = "2026-10-24T12:00:00", "2026-10-20T12:00:00"
    p01 = btypes.get("P01_gold_weekend_stores", "SPEND")
    p02 = btypes.get("P02_every_5th_yoga", "PURCHASE")
    p03 = btypes.get("P03_birthday_voucher", "SPEND")
    p04 = btypes.get("P04_three_stores_popup", "SPEND")
    return {
        "P01_gold_weekend_stores": [
            ({"tier_class": "GOLD"}, seq(p01, (sat, 50, "HQ", 100), (sat, 49.99, "HQ", 0), (sat, 80, "WEB", 100),
                                         (sat, 80, "Chennai", 0), (tue, 80, "HQ", 0), ("2026-10-25T09:00:00", 50, "WEB", 100))),
            ({"tier_class": "Silver"}, seq(p01, (sat, 80, "HQ", 0))),
        ],
        "P02_every_5th_yoga": [
            ({}, seq(p02, *[(f"2026-10-{20 + i}T12:00:00", 20, "HQ", "37" if i in (4, 9) else None) for i in range(10)])),
        ],
        "P03_birthday_voucher": [
            ({"date_of_birth": "1990-06-15"}, seq(p03, ("2026-06-01T12:00:00", 20, "HQ", None), ("2026-06-10T12:00:00", 20, "HQ", "64"),
                                                  ("2026-06-12T12:00:00", 20, "HQ", None), ("2026-06-22T12:00:00", 20, "HQ", None),
                                                  ("2027-06-20T12:00:00", 20, "HQ", "64"))),
            ({"date_of_birth": "1990-01-03"}, seq(p03, ("2026-12-30T12:00:00", 20, "HQ", "64"), ("2027-01-05T12:00:00", 20, "HQ", None))),
        ],
        "P04_three_stores_popup": [
            ({}, seq(p04, ("2026-10-20T12:00:00", 20, "HQ", 0), ("2026-10-21T12:00:00", 20, "HQ", 0), ("2026-10-22T12:00:00", 20, "WEB", 0),
                     ("2026-10-23T12:00:00", 20, "VJD", 300), ("2026-10-24T12:00:00", 20, "Chennai", 0))),
        ],
    }


def got(bo):
    pts = round(sum(p["total"] for p in bo.get("points", []) if p["action"] == "REWARD_POINTS"), 2)
    pv = bo.get("privileges", [])
    first = pv[0] if pv else None
    priv = str(first.get("product_id", first.get("productId", first)) if isinstance(first, dict) else first) if pv else None
    return pts, priv


def main():
    # the activity type each saved DRL requires, read from every DRL present (summary.json only holds the last run's offers)
    btypes = {}
    for key in plans({}):
        f = os.path.join(HOSTED, key + ".drl")
        m = re.search(r'readAttributeS\("h_bit_type"\)== \("([^"]+)"\)', open(f).read()) if os.path.exists(f) else None
        if m:
            btypes[key] = m.group(1)
    total = ok = 0
    os.makedirs(CASES, exist_ok=True)
    for key, runs in plans(btypes).items():
        f = os.path.join(HOSTED, key + ".drl")
        if not os.path.exists(f):
            print(f"{key:28s} NO_DRL")
            total += 1
            continue
        drl = open(f).read()
        offer_id = int(re.search(r'rule "activity-(\d+)-', drl).group(1))
        for qi, (member, (bits, exp)) in enumerate(runs):
            case = {"offer_id": offer_id, "drl": drl, "params": {}, "priority": 1,
                    "member": {"id": "M1", "attributes": member, "tier": member.get("tier_class")},
                    "products": {}, "privilege_products": {"37": {"name": "yoga", "cost": 1}, "64": {"name": "voucher", "cost": 1}},
                    "bits": bits}
            path = os.path.join(CASES, f"hosted_pulsar__{key}__{qi}.json")
            json.dump(case, open(path, "w"), indent=1)
            out = json.loads(subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=300).stdout)
            if out.get("compile") != "ok":
                print(f"{key:28s} seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages'))[:300]}")
                total += len(exp)
                continue
            for bi, (b, e, bo) in enumerate(zip(bits, exp, out["bits"])):
                pts, priv = got(bo)
                good = (priv == e) if isinstance(e, str) or e is None else (abs(pts - e) < 0.01 and priv is None)
                total += 1
                ok += good
                print(f"{key:28s} seq{qi} bit{bi} {b['header']['h_bit_date'][:10]} {b['header']['h_location']:8s} "
                      f"${b['header']['h_bit_amount']:<6} expect={e!s:5} got=pts {pts} priv {priv} {'PASS' if good else 'FAIL'}")
    print(f"\nhosted stock-UI DRL: {ok}/{total} BIT expectations pass")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
