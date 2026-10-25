"""Run the DRL the stock hosted UI saved for each case in tests/cases/hosted_combinations.yaml (all on ONE offer)
through the real engine harness. One line per BIT and a total; exit 1 on any mismatch.

Run: python3 -I tools/check_hosted_combos.py [spike_dir] [--tenant=devintam1]
  --tenant: cases from tools/tenant_cases.py (hosted_combinations.<tenant>.yaml), DRL from results/<tenant>/hosted_combos
"""
import json
import os
import re
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_hosted_pulsar import JAR, CASES, SPIKE, bit, got  # noqa: E402
from tenant_cases import BASE, cases_file  # noqa: E402

TENANT = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tenant=")), BASE)
HOSTED = os.path.join(SPIKE, "results", *([] if TENANT == BASE else [TENANT]), "hosted_combos")


def main():
    doc = yaml.safe_load(open(cases_file(TENANT)))
    cases = doc["cases"]
    default_member = doc.get("default_member", {"tier_class": "GOLD"})
    tier_attr = doc.get("tier_attribute", "tier_class")
    privileges = {p: {"name": "privilege " + p, "cost": 1} for p in doc.get("privileges", ["37", "64"])}
    total = ok = 0
    os.makedirs(CASES, exist_ok=True)
    for c in cases:
        f = os.path.join(HOSTED, c["id"] + ".drl")
        n = sum(len(s["bits"]) for s in c["sequences"])
        if not os.path.exists(f):
            print(f"{c['id']:32s} NO_DRL")
            total += n
            continue
        drl = open(f).read()
        offer_id = int(re.search(r'rule "activity-(\d+)-', drl).group(1))
        m = re.search(r'readAttributeS\("h_bit_type"\)== \("([^"]+)"\)', drl)
        btype = m.group(1) if m else "SPEND"
        for qi, s in enumerate(c["sequences"]):
            member = dict(default_member, **s.get("member", {}))
            bits = [bit(d, a, loc, btype) for d, a, loc, _ in s["bits"]]
            for b in bits:
                b["header"].update(h_sponsor_id=doc.get("sponsor", 1), h_program_id=doc.get("program", 1))
            exp = [e for *_, e in s["bits"]]
            case = {"offer_id": offer_id, "drl": drl, "params": {}, "priority": 1,
                    "member": {"id": "M1", "attributes": member, "tier": member.get(tier_attr)}, "products": {},
                    "privilege_products": privileges, "bits": bits}
            path = os.path.join(CASES, f"hosted_combos__{c['id']}__{qi}.json")
            json.dump(case, open(path, "w"), indent=1)
            out = json.loads(subprocess.run(["java", "-jar", JAR, path], capture_output=True, text=True, timeout=300).stdout)
            if out.get("compile") != "ok":
                print(f"{c['id']:32s} seq{qi} COMPILE {out.get('compile')} {json.dumps(out.get('messages'))[:300]}")
                total += len(exp)
                continue
            for bi, (b, e, bo) in enumerate(zip(bits, exp, out["bits"])):
                pts, priv = got(bo)
                good = (priv == e) if isinstance(e, str) or e is None else (abs(pts - e) < 0.01 and priv is None)
                total += 1
                ok += good
                h = b["header"]
                print(f"{c['id']:32s} seq{qi} bit{bi} {h['h_bit_date'][:16]} {h['h_location']:8s} ${h['h_bit_amount']:<7} "
                      f"{member.get(tier_attr, ''):7s} expect={e!s:5s} got=pts {pts} priv {priv} {'PASS' if good else 'FAIL'}")
    print(f"\n{TENANT} hosted combinations on one offer: {ok}/{total} BIT expectations pass")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
