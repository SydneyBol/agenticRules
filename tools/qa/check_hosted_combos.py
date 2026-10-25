"""Run the DRL the stock hosted UI saved for each case in tests/cases/hosted_combinations.yaml (all on ONE offer)
through the real engine harness. One line per BIT and a total; exit 1 on any mismatch.

Run: python3 -I tools/qa/check_hosted_combos.py [--tenant=devintam1]
  --tenant: cases from tools/qa/tenant_cases.py (hosted_combinations.<tenant>.yaml), DRL from results/<tenant>/hosted_combos
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root, as a script
from tools.paths import tenant_results  # noqa: E402
from tools.qa.engine_cases import got, passes, run, sequence  # noqa: E402
from tools.qa.tenant_cases import BASE, load  # noqa: E402

TENANT = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tenant=")), BASE)
HOSTED = tenant_results(TENANT, BASE, "hosted_combos")


def main():
    doc = load(TENANT)
    tier_attr = doc["tier_attribute"]
    total = ok = 0
    for c in doc["cases"]:
        f = os.path.join(HOSTED, c["id"] + ".drl")
        if not os.path.exists(f):
            print(f"{c['id']:32s} NO_DRL")
            total += sum(len(s["bits"]) for s in c["sequences"])
            continue
        drl = open(f).read()
        for qi, s in enumerate(c["sequences"]):
            job, bits, exp, member = sequence(doc, s, drl, doc["default_member"], tier_attr, doc["privilege_products"])
            out = run(job, bits, f"hosted_combos__{c['id']}__{qi}")
            if out.get("compile") != "ok":
                print(f"{c['id']:32s} seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages'))[:300]}")
                total += len(exp)
                continue
            for bi, (b, e, bo) in enumerate(zip(bits, exp, out["bits"])):
                pts, priv = got(bo)
                good = passes(e, pts, priv)
                total += 1
                ok += good
                h = b["header"]
                print(f"{c['id']:32s} seq{qi} bit{bi} {h['h_bit_date'][:16]} {h['h_location']:8s} ${h['h_bit_amount']:<7} "
                      f"{member.get(tier_attr, ''):7s} expect={e!s:5s} got=pts {pts} priv {priv} {'PASS' if good else 'FAIL'}")
    print(f"\n{TENANT} hosted combinations on one offer: {ok}/{total} BIT expectations pass")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
