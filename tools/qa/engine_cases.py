"""Hosted-combination BITs through the engine harness: build the job for one case sequence, read what each BIT earned,
judge it against the case's expectation. Shared by check_offer_flow and check_hosted_combos."""
import os
import re

from offer_author.core import engine
from tools.paths import ENGINE_CASES


def bit(date, amount=60, location="HQ", btype=None):
    return {"header": {"h_bit_category": "ACCRUAL", "h_bit_type": btype, "h_sponsor_id": 1, "h_program_id": 1, "h_member_id": "M1",
                       "h_location": location, "h_bit_amount": amount, "h_bit_date": date}, "lines": []}


def got(bo):
    """-> (points, privilege ids) one BIT earned: one id as a string, several as a sorted list, none as None."""
    pts = round(sum(p["total"] for p in bo.get("points", []) if p["action"] == "REWARD_POINTS"), 2)
    ids = sorted(str(p.get("product_id", p.get("productId", p)) if isinstance(p, dict) else p) for p in bo.get("privileges", []))
    return pts, (ids[0] if len(ids) == 1 else ids or None)


def expected(expect):
    """-> (points, sorted privilege ids) a case expects: points (number), a privilege id (str), None (nothing at all),
    or {points: n, privilege: id | privileges: [ids]} for several rewards on one BIT."""
    if isinstance(expect, dict):
        ids = expect.get("privileges") or ([expect["privilege"]] if expect.get("privilege") is not None else [])
        return expect.get("points", 0), sorted(map(str, ids))
    if isinstance(expect, str):
        return 0, [expect]
    return expect or 0, []


def passes(expect, pts, priv):
    """Exact: the expected points and exactly the expected privileges (priv as got returns it)."""
    want_pts, want_ids = expected(expect)
    return abs(pts - want_pts) < 0.01 and sorted([priv] if isinstance(priv, str) else priv or []) == want_ids


def offer_id(drl):
    return int(re.search(r'rule "activity-(\d+)-', drl).group(1))


def activity_type(drl):
    """The activity type the rules require (the BITs are sent with it); Spend when they name none."""
    m = re.search(r'readAttributeS\("h_bit_type"\)\s*==\s*\(?"([^"]+)"', drl)  # == ("X") or == "X"
    return m.group(1) if m else "SPEND"


def sequence(doc, seq, drl, default_member, tier_attr, privileges, segment_drl=None):
    """One case sequence -> (job, bits, expectations, member). doc: the tenant's cases file (sponsor, program)."""
    member = dict(default_member, **seq.get("member", {}))
    bits = [bit(d, a, loc, activity_type(drl)) for d, a, loc, _ in seq["bits"]]
    for b in bits:
        b["header"].update(h_sponsor_id=doc.get("sponsor", 1), h_program_id=doc.get("program", 1))
    job = {"offer_id": offer_id(drl), "drl": drl, "params": {}, "priority": 1,
           "member": {"id": "M1", "attributes": member, "tier": member.get(tier_attr)}, "products": {},
           "privilege_products": privileges}
    if segment_drl:
        job.update(segment_drl=segment_drl, segment_id=re.search(r'addValidatedSegmentId\("([^"]+)"\)', segment_drl).group(1))
    return job, bits, [e for *_, e in seq["bits"]], member


def run(job, bits, tag, offer_main=None):
    """The engine run of one sequence (Offer Main checks first when given); the job is kept as engine/cases/<tag>.json."""
    return engine.run_offer(job, bits, offer_main, save_to=os.path.join(ENGINE_CASES, tag + ".json"))
