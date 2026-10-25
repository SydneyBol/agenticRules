"""Build hosted-simulation test cases (input for harness/ui/hosted/hosted-simulate.mjs) from one case in
tests/cases/hosted_combinations.yaml. Three kinds, scored separately:
  scenario    the case's BITs, one chain per sequence (hosted-simulate.mjs carries the member's counters along it)
  store       one paying scenario BIT replayed at every other active sponsor location, with the counters its chain had
              before it: pays nothing when the rule names stores (only the store changed), else the same reward
  validation  a code that is not a location: the engine rejects the BIT before any rule (LOCATION_NOT_FOUND)
Expect: points (number), privilege id (string), null = no points and no privilege, {points, privilege(s)} for several
rewards on one BIT, or an error code. A sequence with `hosted: false` (a year boundary, or longer than the offer) is
local only. A case's `probe_expect` is what other stores earn when the named store is an exclusion or other stores
earn a base rate: `same` (the probe BIT's own reward) or a value; without it they earn nothing when stores are named.

Run: python3 -I tools/qa/hosted_sim_cases.py H05_three_way_or out.json [--tenant=devintam1] [--after=2026-10-26]
  --after: move the whole case forward by the same whole number of weeks (weekday, time and BIT order kept) so its
           first BIT is on/after this day and every BIT falls inside the offer's dates (the engine rejects earlier BITs
           with REWARD_OFFER_DOES_NOT_QUALIFY before any rule); member dates (date_of_birth, ...) move with them, so a
           window around a member date is kept
"""
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # repo root, as a script
from tools.paths import CATALOGS, tenant_results  # noqa: E402
from tools.qa.engine_cases import activity_type  # noqa: E402
from tools.qa.tenant_cases import BASE, load  # noqa: E402

TENANT = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tenant=")), BASE)
DOC = load(TENANT)
SPONSOR = DOC.get("sponsor", 1)
AFTER = next((dt.date.fromisoformat(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--after=")), None)
LOCATIONS = os.path.join(CATALOGS, TENANT, "bolapi", f"extra_locations{SPONSOR}.json")


DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")


def hosted(case):
    """The sequences that run on the hosted offer (not `hosted: false`)."""
    return [s for s in case["sequences"] if s.get("hosted", True)]


def shifter(case):
    """One shift for the whole case: whole weeks, so weekdays, times and the order of BITs stay as written."""
    first = min(dt.datetime.fromisoformat(b[0]) for s in hosted(case) for b in s["bits"])
    weeks = -(-(AFTER - first.date()).days // 7) if AFTER and first.date() < AFTER else 0
    return lambda date: (dt.datetime.fromisoformat(date) + dt.timedelta(weeks=weeks)).isoformat(timespec="seconds")


def shifted_member(member, shift):
    """Member dates (YYYY-MM-DD) move with the BITs."""
    return {k: shift(v)[:10] if isinstance(v, str) and DATE.match(v) else v for k, v in member.items()}


def rewarded(e):
    return e not in (None, 0) and not (isinstance(e, dict) and not e.get("points") and not e.get("privilege") and not e.get("privileges"))


def case_input(date, amount, location, btype, member):
    return {"payload": {"h_bit_category": "ACCRUAL", "h_bit_type": btype, "h_sponsor_id": SPONSOR, "h_location": location,
                        "h_bit_amount": amount, "h_bit_date": date, "h_bit_currency": DOC.get("currency", "INR")},
            "member": {"MA_P": member, "MA_O": {}}}


def main(case_id, out):
    case = next(c for c in DOC["cases"] if c["id"] == case_id)
    default_member, tier_attr = DOC["default_member"], DOC["tier_attribute"]
    drl = open(tenant_results(TENANT, BASE, "hosted_combos", case_id + ".drl")).read()
    btype = activity_type(drl)
    named = set(re.findall(r'h_location"\)(?:\.contentEquals\(|\s*==\s*\(?)"([^"]+)"', drl))  # .contentEquals("X"), == ("X"), == "X"
    shift, cases, paying = shifter(case), [], []
    for si, s in enumerate(case["sequences"]):
        if not s.get("hosted", True):
            continue
        member = shifted_member(dict(default_member, **s.get("member", {})), shift)
        chain = f"{case_id}#{si}"
        for bi, (d, a, loc, e) in enumerate(s["bits"]):
            cases.append({"kind": "scenario", "description": f"{case_id[:3]} {shift(d)[:16]} {loc} {a} {member[tier_attr]} expect {e}",
                          "expect": e, "expect_reward": rewarded(e), "input": case_input(shift(d), a, loc, btype, member),
                          "chain": chain, "step": bi})  # hosted-simulate.mjs carries counters (MA_O) along a chain
            if rewarded(e) and (not named or loc in named or "probe_expect" in case):
                paying.append((a, si, bi, d, loc, e, member))
    # the store probe replays one paying BIT whose reward depends on the store when the rule names stores: the case's
    # `probe: [sequence, bit]` when set (a cheapest BIT can pay through another OR branch, e.g. H07's Silver on Friday),
    # else the cheapest paying BIT at a named store
    pin = case.get("probe")
    pick = [p for p in paying if pin and [p[1], p[2]] == list(pin)] or sorted(paying, key=lambda p: p[0])[:1]
    if not pick:
        print(f"no paying BIT at a named store {sorted(named)}: store probes skipped")
        locations = []
    else:
        a, si, bi, d, base_loc, e, member = pick[0]
        locations = json.load(open(LOCATIONS))["body"]
    nothing = None if pick and isinstance(e, str) else 0
    other = case.get("probe_expect")
    for loc in locations:
        code = loc["location_code"]
        if loc["active"] and code not in named and code != base_loc and code.isascii() and len(code) < 20:
            want = (e if other == "same" else other) if other is not None else nothing if named else e
            cases.append({"kind": "store", "description": f"store {code} ({loc['location_name']}) like {base_loc} {a}: expect {want}",
                          "expect": want, "expect_reward": rewarded(want), "input": case_input(shift(d), a, code, btype, member),
                          "from": [f"{case_id}#{si}", bi]})
    s0 = hosted(case)[0]
    d0, a0, _, _ = s0["bits"][0]
    cases.append({"kind": "validation", "description": "NO-SUCH-STORE (not a location code) is rejected", "expect": "LOCATION_NOT_FOUND",
                  "expect_reward": False, "input": case_input(shift(d0), a0, "NO-SUCH-STORE", btype,
                                                              shifted_member(dict(default_member, **s0.get("member", {})), shift))})
    json.dump(cases, open(out, "w"), indent=1)
    kinds = {k: sum(c["kind"] == k for c in cases) for k in ("scenario", "store", "validation")}
    print(f"{len(cases)} cases {kinds}, activity type {btype}, rule names locations {sorted(named)}")


if __name__ == "__main__":
    main(*[a for a in sys.argv[1:] if not a.startswith("--")][:2])
