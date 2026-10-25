"""Map the hosted combination cases (tests/cases/hosted_combinations.yaml, written in devintam1 codes and words) to
another tenant, using tenant profiles in tests/cases/tenants/<tenant>.yaml, checked against that tenant's catalog dump
(catalogs/<tenant>/bolapi, written by harness/ui/hosted/tenant-catalog.mjs --tenant=<tenant>).

  --suggest     print the tenant's stores, tier classes, privileges, accounts and tags to fill its profile with
  (default)     check the profile against the catalog, then write
                  tests/cases/hosted_combinations.<tenant>.yaml   cases with the tenant's prompts, codes and member rows
                  tests/cases/hosted_plan.<tenant>.json            plan for harness/ui/hosted/hosted-pulsar.mjs (popup mode)

Run: .venv/bin/python -I tools/qa/tenant_cases.py devintbf2 [--suggest]
"""
import json
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root, as a script
from tools.paths import CASES as CASES_DIR, CATALOGS, ROOT  # noqa: E402

BASE = "devintam1"


def profile(tenant):
    return yaml.safe_load(open(os.path.join(CASES_DIR, "tenants", tenant + ".yaml")))


def cases_file(tenant):
    """The combination cases in a tenant's codes: the base file for devintam1, the mapped file otherwise."""
    name = "hosted_combinations.yaml" if tenant == BASE else f"hosted_combinations.{tenant}.yaml"
    return os.path.join(CASES_DIR, name)


def load(tenant):
    """The tenant's combination cases with their run defaults: default member, tier attribute, privilege products."""
    doc = yaml.safe_load(open(cases_file(tenant)))
    doc.setdefault("default_member", {"tier_class": "GOLD"})
    doc.setdefault("tier_attribute", "tier_class")
    doc["privilege_products"] = {p: {"name": "privilege " + p, "cost": 1} for p in doc.get("privileges", ["37", "64"])}
    return doc


def dump(tenant, name):
    path = os.path.join(CATALOGS, tenant, "bolapi", name + ".json")
    if not os.path.exists(path):
        return None
    body = json.load(open(path))["body"]
    return body.get("results", body) if isinstance(body, dict) and "results" in body else body


def catalog_view(tenant, sponsor=None):
    """What a profile is checked against, from the dump."""
    loc_files = [f[:-5] for f in os.listdir(os.path.join(CATALOGS, tenant, "bolapi")) if f.startswith("extra_locations")]
    loc_name = f"extra_locations{sponsor}" if sponsor and f"extra_locations{sponsor}" in loc_files else (loc_files or [None])[0]
    sla = dump(tenant, "sponsor_loyalty_accounts") or {}
    accounts = {str(a["id"]): a.get("loyalty_currency") for a in (sla.get("loyalty_accounts") if isinstance(sla, dict) else []) or []}
    tags = {}
    for acc in accounts:
        tags[acc] = {t["code"]: t["name"] for t in dump(tenant, f"extra_tags{acc}") or [] if t.get("active", True)}
    return {
        "sponsors": {str(s["id"]): s.get("name") for s in dump(tenant, "sponsors") or []},
        "sponsor_ids": [str(s) for s in (sla.get("sponsor_ids") if isinstance(sla, dict) else []) or []],
        "locations": {lo["location_code"]: (lo["location_name"], lo.get("location_category"))
                      for lo in (dump(tenant, loc_name) or []) if lo.get("active")} if loc_name else {},
        "tiers": {t["code"]: t["name"] for t in dump(tenant, "extra_tierclass") or [] if t.get("active", True)},
        "privileges": {str(p["id"]): p.get("name") for p in dump(tenant, "privilege_products") or []},
        "accounts": accounts,
        "tags": tags,
        "member_attributes": [a["name"] for a in dump(tenant, "member_attributes") or [] if "tier" in a.get("name", "").lower()],
        "spend_types": {t["code"]: t["name"] for t in dump(tenant, "lookup_BIT_TYPE") or [] if "spend" in t.get("name", "").lower()},
    }


def suggest(tenant):
    v = catalog_view(tenant)
    print(f"{tenant} catalog (catalogs/{tenant}/bolapi)\n")
    print(f"sponsors ({len(v['sponsors'])}):", ", ".join(f"{k} {n}" for k, n in list(v["sponsors"].items())[:15]),
          "| this dump's reward accounts are for sponsor(s)", v["sponsor_ids"])
    print("reward loyalty accounts:", ", ".join(f"{k} {n}" for k, n in v["accounts"].items()))
    for acc, tags in v["tags"].items():
        if tags:
            print(f"  tags of account {acc}:", ", ".join(f"{c} ({n})" for c, n in tags.items()))
    print("activity types containing 'spend':", v["spend_types"])
    print("member attributes containing 'tier':", v["member_attributes"])
    print("tier classes:", ", ".join(f"{c} ({n})" for c, n in v["tiers"].items()))
    print(f"active locations ({len(v['locations'])}):")
    for c, (n, cat) in v["locations"].items():
        print(f"  {c:24s} {n}  [{cat}]")
    print(f"privileges ({len(v['privileges'])}):")
    for i, n in v["privileges"].items():
        print(f"  {i:6s} {n}")
    print("\nPick for each role in tests/cases/tenants/<tenant>.yaml: two named stores (HQ, VJD), one ONLINE store (WEB), one more"
          " store never named (Chennai), three tier classes, two privileges; the account and its tag; the prompt words for each.")


def check(prof, base):
    """Problems that would make the mapped cases wrong; empty when the profile is usable."""
    if "TODO" in json.dumps(prof):
        return ["profile still has TODO values"]
    t = prof["tenant"]
    v = catalog_view(t, prof["sponsor"])
    probs = []
    c = prof["codes"]
    if set(c) != set(base["codes"]):
        probs.append(f"codes must cover exactly the roles {sorted(base['codes'])}")
    for role in ("HQ", "VJD", "WEB", "Chennai"):
        if c.get(role) not in v["locations"]:
            probs.append(f"store {role} -> {c.get(role)} is not an active location of sponsor {prof['sponsor']}")
    if c.get("WEB") in v["locations"] and v["locations"][c["WEB"]][1] != "ONLINE":
        probs.append(f"WEB -> {c['WEB']} is not an ONLINE location")
    if len({c.get(r) for r in ("HQ", "VJD", "WEB", "Chennai")}) < 4:
        probs.append("the four stores must be different locations")
    for role in ("GOLD", "Silver", "Basic"):
        if c.get(role) not in v["tiers"]:
            probs.append(f"tier {role} -> {c.get(role)} is not an active TIER_CLASS code")
    for role in ("37", "64"):
        if str(c.get(role)) not in v["privileges"]:
            probs.append(f"privilege {role} -> {c.get(role)} is not a privilege product of sponsor {prof['sponsor']}")
    if str(prof["account"]) not in v["accounts"]:
        probs.append(f"account {prof['account']} is not a reward loyalty account of sponsor {prof['sponsor']}")
    elif prof["tag"] not in v["tags"].get(str(prof["account"]), {}):
        probs.append(f"tag {prof['tag']} is not an active tag of account {prof['account']}")
    if v["member_attributes"] and prof["tier_attribute"] not in v["member_attributes"]:
        probs.append(f"member attribute {prof['tier_attribute']} not in {v['member_attributes']}")
    for role in base["phrases"]:
        if not prof["phrases"].get(role):
            probs.append(f"no prompt words for {role}")
    return probs


def map_prompt(prompt, base, prof):
    """Replace the base tenant's words for each role with the tenant's, longest first, in one pass."""
    pairs = []
    for role, words in base["phrases"].items():
        target = prof["phrases"][role]
        for i, w in enumerate(words):
            pairs.append((w, target[min(i, len(target) - 1)]))
    pairs.sort(key=lambda p: -len(p[0]))
    rx = re.compile("|".join(r"\b" + re.escape(w) + r"\b" for w, _ in pairs))
    table = dict(pairs)
    return rx.sub(lambda m: table[m.group(0)], prompt)


CASE_KEYS = ("id", "prompt", "probe", "probe_expect", "sequences")


def map_cases(base, prof):
    def code(x):
        if isinstance(x, dict):  # several rewards on one BIT: {points, privilege | privileges}
            return {k: [code(i) for i in v] if k == "privileges" else v if k == "points" else code(v) for k, v in x.items()}
        return x if x is None or isinstance(x, (int, float)) and not isinstance(x, bool) else str(prof["codes"].get(str(x), x))
    out = []
    for c in yaml.safe_load(open(cases_file(BASE)))["cases"]:
        seqs = []
        for s in c["sequences"]:
            m = {prof["tier_attribute"] if k == base["tier_attribute"] else k: code(v) for k, v in (s.get("member") or {}).items()}
            bits = [[d, a, code(loc), code(e)] for d, a, loc, e in s["bits"]]
            seqs.append(dict({"member": m} if m else {}, **{k: s[k] for k in ("note", "hosted") if k in s}, bits=bits))
        extra = {k: c[k] for k in ("probe",) if k in c}
        if "probe_expect" in c:
            extra["probe_expect"] = c["probe_expect"] if c["probe_expect"] == "same" else code(c["probe_expect"])
        out.append(dict({"id": c["id"], "prompt": map_prompt(c["prompt"], base, prof)}, **extra, sequences=seqs))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tenant = args[0] if args else "devintbf2"
    if "--suggest" in sys.argv:
        return suggest(tenant)
    base, prof = profile(BASE), profile(tenant)
    probs = check(prof, base)
    if probs:
        print(f"{tenant} profile is not usable yet:\n  " + "\n  ".join(probs))
        sys.exit(1)
    cases = map_cases(base, prof)
    if tenant == BASE:  # identity mapping: a self-check, never overwrite the base cases
        same = cases == [{k: c[k] for k in CASE_KEYS if k in c} for c in yaml.safe_load(open(cases_file(BASE)))["cases"]]
        print(f"{BASE} maps onto itself unchanged: {same}")
        sys.exit(0 if same else 1)
    head = (f"# Generated by tools/qa/tenant_cases.py {tenant} from hosted_combinations.yaml and tests/cases/tenants/{tenant}.yaml.\n"
            f"# Do not edit: change the profile and re-run. Default member: {prof['tier_attribute']} {prof['codes']['GOLD']}.\n")
    with open(cases_file(tenant), "w") as f:
        f.write(head)
        yaml.safe_dump({"tenant": tenant, "sponsor": prof["sponsor"], "program": prof["program"], "currency": prof["currency"],
                        "tier_attribute": prof["tier_attribute"], "default_member": {prof["tier_attribute"]: prof["codes"]["GOLD"]},
                        "privileges": [str(prof["codes"]["37"]), str(prof["codes"]["64"])], "cases": cases},
                       f, sort_keys=False, width=200, allow_unicode=False)
    plan = os.path.join(CASES_DIR, f"hosted_plan.{tenant}.json")
    json.dump([{"key": c["id"], "mode": "popup", "prompt": c["prompt"]} for c in cases], open(plan, "w"), indent=1)
    for c in cases:
        print(f"{c['id']:34s} {c['prompt']}")
    print(f"\nwrote {os.path.relpath(cases_file(tenant), ROOT)} and {os.path.relpath(plan, ROOT)}")


if __name__ == "__main__":
    main()
