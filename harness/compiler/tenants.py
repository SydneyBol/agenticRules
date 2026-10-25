"""Tenant code maps: rewrite the spike's placeholder codes (fixture catalog) into a real tenant's codes.

Applied to the gold intents before expansion (compile_all.py --tenant=) and to the scenario BITs/member fixtures
(engine-harness/run_scenarios.py --tenant=), so the intent, XML, DRL and test data all use the same codes.
Every code below was read from the tenant with ui-harness/tenant-catalog.mjs (results/<tenant>/catalog/).
"""

TENANTS = {
    "devintam1": {
        # sponsor 1 "Elite International"; loyalty account 12 "DELTA MILES" (Recognition) with its only tag "Base";
        # privilege product 64 "Complimentary Voucher Codes" (Fixed-Product) with usage policy 67
        "catalog": {"account": "12", "tag": "BASE",  # tag code, not its name "Base"
                    "privileges": {"64": {"id": "64", "usage_policy": "67"}}},
        "sponsor_id": 1,
        # exact-match rewrites of string values and dict keys (codes, attribute names)
        "values": {
            "L1": "HQ", "L2": "WEB", "L3": "Chennai",                    # SPONSOR_LOCATION_LIST location_code
            "SNACKS": "MEAL", "DRINKS": "COFFEE", "BAKERY": "DSRT",      # PRODUCT_CATEGORY
            "STAY": "FLIGHT",                                            # BIT_TYPE (no STAY type on the tenant)
            "SILVER": "Silver",                                          # TIER_CLASS (GOLD exists as-is)
            "tier": "tier_class",                                        # member LOOKUP attribute TIER_CLASS
            "enrollment_date": "date_of_joining",                       # member DATE attribute
            "member.enrollment_date": "member.date_of_joining",
            "501": "64",
        },
        "labels": {"MEAL": "Meal", "COFFEE": "Coffee", "DSRT": "Dessert", "FLIGHT": "Flight",
                   "HQ": "Chennai", "WEB": "Elite Group Online", "Chennai": "Chennai"},
        "ints": {501: 64},                                               # only under a "privilege" key
    },
}


def remap(obj, tenant):
    """Deep copy of obj with the tenant's codes; [code, label] pairs get the tenant's label too."""
    t = TENANTS[tenant]
    vals, labels, ints = t["values"], t.get("labels", {}), t.get("ints", {})

    def go(o, key=None):
        if isinstance(o, dict):
            return {vals.get(k, k) if isinstance(k, str) else k: go(v, k) for k, v in o.items()}
        if isinstance(o, list):
            if len(o) == 2 and all(isinstance(x, str) for x in o) and o[0] in vals:
                code = vals[o[0]]
                return [code, labels.get(code, o[1])]
            return [go(x) for x in o]
        if isinstance(o, str):
            return vals.get(o, o)
        if isinstance(o, int) and not isinstance(o, bool) and key == "privilege":  # never amounts like h_bit_amount: 501
            return ints.get(o, o)
        return o

    return go(obj)
