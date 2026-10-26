"""Tenant catalog: the vocabulary the model may use (attributes, lookup values, activity types, accounts, privileges).

Built from the same bolapi endpoints the Blockly dropdowns read (see block-definition.service.ts), either live
(BolapiCatalogSource, caller's JWT forwarded) or from saved responses (Catalog.from_bolapi_dumps). The model only
ever sees `prompt_text()` and `lookup()`; blocks/check.py checks every code it uses against the same object.
"""
import difflib
import json
import os
import re
from dataclasses import dataclass, field

OBJECTS = ("activity", "member", "product")
NUMERIC = ("NUMBER",)
DATES = ("DATE", "DATETIME")
LISTY = ("LOOKUP", "LOV", "TEXT")
# lookup_type -> how to read its values. LOOKUPs come from lookups/?type=; LOVs from their own api_url (lovs/ catalog)
LOV_SOURCES = {"SPONSOR_LOCATION_LIST": ("locations", "location_code", "location_name")}
# value lists longer than this are not written into the prompt; the model finds codes with lookup_values instead
INLINE_VALUES = 40
INLINE_ACTIVITY_TYPES = 200
# words that say what kind of thing a value is, not which one ("the Chennai store", "Gold tier")
GENERIC_WORDS = {"the", "a", "an", "at", "in", "of", "on", "for", "and", "store", "stores", "shop", "outlet", "branch",
                 "location", "tier", "level", "class", "member", "members", "product", "products", "item", "items", "sku"}


def norm(s):
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(s).lower()).split())


def words(s):
    return set(norm(s).split()) - GENERIC_WORDS


@dataclass
class Attribute:
    code: str
    label: str
    type: str  # NUMBER | DATE | DATETIME | LOOKUP | LOV | TEXT | BOOL
    lookup_type: str | None = None
    values: dict | None = None  # code -> label, when known
    line_level: bool = False
    payment_level: bool = False  # a payment attribute (the payment blocks' payment_attribute dropdown)


@dataclass
class Catalog:
    tenant: str
    sponsor_id: int
    attributes: dict = field(default_factory=dict)  # object -> {code: Attribute}
    activity_types: dict = field(default_factory=dict)  # code -> label
    loyalty_accounts: list = field(default_factory=list)  # [{id, name, tags: [{code, name}]}]
    privileges: dict = field(default_factory=dict)  # id -> {id, name, usage_policy}
    tiers: dict = field(default_factory=dict)  # MA_T tier programme: tier_class_code -> {tier_code: name}
    default_account: str | None = None
    default_tag: str | None = None
    today: str | None = None

    # ------------------------------------------------------------ lookups used by validate / adapter
    def attr(self, obj, code):
        return self.attributes.get(obj, {}).get(code)

    def account(self, account_id=None):
        aid = str(account_id or self.default_account)
        return next((a for a in self.loyalty_accounts if str(a["id"]) == aid), None)

    def label(self, obj, code, value):
        a = self.attr(obj, code)
        if obj == "activity" and code == "h_bit_type":
            return self.activity_types.get(value, value)
        return (a.values or {}).get(value, value) if a else value

    def values_of(self, obj, code):
        """code -> label for a LOOKUP/LOV attribute (activity types for h_bit_type)."""
        if (obj, code) == ("activity", "h_bit_type"):
            return self.activity_types
        a = self.attr(obj, code)
        return (a.values or {}) if a else {}

    def bit_defaults(self):
        """BIT header defaults for a test transaction, from the tenant's own codes: the activity type labelled Spend
        (else SPEND if it is a code, else the first type) and the Accrual category of the BIT_CATEGORY LOV."""
        types = self.activity_types
        spend = next((k for k, v in types.items() if norm(v) == "spend"), "SPEND" if "SPEND" in types else next(iter(types), None))
        cats = self.values_of("activity", "h_bit_category")
        cat = next((k for k, v in cats.items() if norm(v) == "accrual" or k == "ACCRUAL"), None)
        return {k: v for k, v in (("h_bit_type", spend), ("h_bit_category", cat)) if v}

    def lookup(self, obj, code, text, k=8):
        """Best catalog codes for the manager's words. A value scores by the share of the query's words found in its
        code + label (generic words like 'store' or 'tier' ignored), the share of its label the query covers, then
        string similarity. 'ambiguous' when the best codes tie or share one label (two stores named 'Chennai', tier
        classes GOLD and Gold); a note when no value has all of the query's words."""
        vals = self.values_of(obj, code)
        if not vals:
            a = self.attr(obj, code)
            return {"error": f"{obj}.{code} is not a catalog attribute" if a is None else f"{obj}.{code} has no value list"}
        q, qw = norm(text), words(text)
        scored = []  # (score, code, label, recall)
        for c, lab in vals.items():
            cw, lw = words(c) | words(lab), words(lab)
            if q in (norm(c), norm(lab)):
                scored.append((1.0, c, lab, 1.0))
                continue
            recall = len(qw & cw) / len(qw) if qw else 0.0  # the manager's words found in the value
            cover = len(qw & lw) / len(lw) if lw else 0.0  # the value's label words the manager said ('Terminal 2' unsaid)
            sim = max(difflib.SequenceMatcher(None, q, norm(lab)).ratio(), difflib.SequenceMatcher(None, q, norm(c)).ratio())
            scored.append((round(0.6 * recall + 0.25 * cover + 0.15 * sim, 3), c, lab, recall))
        scored.sort(key=lambda t: (-t[0], t[2], t[1]))
        top, best = scored[:k], scored[0]
        out = {"attribute": f"{obj}.{code}", "query": text, "total_values": len(vals),
               "candidates": [{"code": c, "label": lab, "score": sc} for sc, c, lab, _ in top if sc > 0.2]}
        if best[3] < 1.0:
            out["note"] = ("No value has all of these words; the closest are listed. Use one only if it is clearly what "
                           "the manager means, otherwise add a blocking question naming the closest codes.")
            return out
        tied = [t for t in top if best[0] - t[0] <= 0.02]
        same_label = [t for t in scored if norm(t[2]) == norm(best[2])]
        clash = tied if len(tied) > 1 else same_label if len(same_label) > 1 else []
        if clash:
            out["ambiguous"] = [{"code": c, "label": lab} for _, c, lab, _ in clash]
            out["note"] = ("Several codes match equally. Use the one the description clearly means; if it does not "
                           "settle it, use your best choice and add a blocking question naming these codes.")
        return out

    # ------------------------------------------------------------ what the model sees
    def prompt_text(self, account_id=None):
        out = [f"Tenant: {self.tenant}  sponsor: {self.sponsor_id}" + (f"  today: {self.today}" if self.today else "")]
        names = {"activity": "ACTIVITY (transaction) attributes, l_* are line-item attributes",
                 "member": "MEMBER attributes", "product": "PRODUCT attributes (use with line_item / collect_all)"}
        for obj in OBJECTS:
            out.append(f"\n## {names[obj]}  (code | label | type [| values])")
            for a in self.attributes.get(obj, {}).values():
                line = f"{a.code} | {a.label} | {a.type}" + (" (payment)" if a.payment_level else "")
                if a.values and (obj, a.code) != ("activity", "h_bit_type"):  # activity types are listed once, below
                    if len(a.values) <= INLINE_VALUES:
                        line += " | " + ", ".join(f"{k}={v}" for k, v in a.values.items())
                    else:
                        line += f" | {len(a.values)} values, not listed: find codes with lookup_values"
                out.append(line)
        if len(self.activity_types) <= INLINE_ACTIVITY_TYPES:
            out.append("\n## Activity types (h_bit_type values)\n" + ", ".join(f"{k}={v}" for k, v in self.activity_types.items()))
        else:
            spend = next((k for k, v in self.activity_types.items() if norm(v) == "spend"), None)
            out.append(f"\n## Activity types (h_bit_type values): {len(self.activity_types)}, not listed: find codes with "
                       f"lookup_values (activity, h_bit_type)" + (f". Spend = {spend}" if spend else ""))
        if self.tiers:
            out.append("\n## TIER PROGRAMME (MA_T: tier class | tiers the member can hold)")
            out += [f"{c} | " + ", ".join(k if k == v else f"{k}={v}" for k, v in t.items()) for c, t in self.tiers.items()]
        acc = self.account(account_id)
        if acc:
            out.append(f"\n## Points are awarded to loyalty account {acc['id']} \"{acc['name']}\"")
        out.append("\n## Privileges (privilege_id | name)")
        out += [f"{pid} | {p['name']}" for pid, p in self.privileges.items()]
        return "\n".join(out)

    # ------------------------------------------------------------ builders
    @classmethod
    def from_json(cls, path):
        """Hand-written catalog (tests / local fixture): attributes as {obj: [{code,label,type,values?,line_level?}]}."""
        d = json.load(open(path))
        cat = cls(tenant=d["tenant"], sponsor_id=d["sponsor_id"], activity_types=d["activity_types"],
                  loyalty_accounts=d["loyalty_accounts"], privileges={str(p["id"]): p for p in d["privileges"]},
                  default_account=str(d["default_account"]), default_tag=d.get("default_tag"), today=d.get("today"),
                  tiers=d.get("tiers", {}))
        for obj, attrs in d["attributes"].items():
            cat.attributes[obj] = {a["code"]: Attribute(**a) for a in attrs}
        return cat

    @classmethod
    def from_bolapi(cls, tenant, sponsor_id, account_id, responses, default_tag=None):
        """responses: {name: body} for the GETs listed in BolapiCatalogSource.REQUESTS (raw bolapi JSON bodies)."""
        rows = lambda b: b if isinstance(b, list) else (b or {}).get("results", [])
        lookups = {}
        for name, body in responses.items():
            if name.startswith("lookup:"):
                lookups[name[7:]] = {x["code"]: x.get("name") or x["code"] for x in rows(body) if x.get("code")}
        locations = {x["location_code"]: x["location_name"] for x in rows(responses.get("locations"))}
        lov_value = lambda x, m: ((str(m.get(x.get("code") or "code")), m.get(x.get("display") or "name"))
                                  if isinstance(m, dict) else (str(m), str(m)))
        lovs = {x["name"]: dict(lov_value(x, m) for m in x["model"])
                for x in rows(responses.get("lovs")) if x.get("name") and isinstance(x.get("model"), list)}  # LOVs that carry their values (BIT_CATEGORY)
        cat = cls(tenant=tenant, sponsor_id=sponsor_id, default_account=str(account_id), default_tag=default_tag)

        def take(obj, body):
            attrs = {}
            for x in rows(body):
                if not (x.get("enabled", True) and x.get("available_for_rules", True)) or x["data_type"] == "BOOL":
                    continue
                lt = x.get("lookup_type") if x.get("lookup_type") not in (None, "None", "") else None
                values = lookups.get(lt) if x["data_type"] == "LOOKUP" else (locations if lt == "SPONSOR_LOCATION_LIST" else lovs.get(lt))
                attrs[x["name"]] = Attribute(code=x["name"], label=x.get("visible_name") or x.get("business_name") or x["name"],
                                             type=x["data_type"], lookup_type=lt, values=values or None,
                                             line_level=bool(x.get("line_level")), payment_level=bool(x.get("payment_level")))
            cat.attributes[obj] = attrs

        take("activity", responses["activity_attributes"])
        take("member", responses["member_attributes"])
        take("product", responses["product_attributes"])
        cat.activity_types = lookups.get("BIT_TYPE", {})
        for t in rows(responses.get("tiers")):
            if t.get("active", True) and t.get("tier_class_code"):
                cat.tiers.setdefault(t["tier_class_code"], {})[t.get("tier_code") or t["name"]] = t["name"]  # the dropdown stores tier_code
        tags = {k[5:]: [{"code": t["code"], "name": t["name"]} for t in rows(v)] for k, v in responses.items() if k.startswith("tags:")}
        las = responses["sponsor_loyalty_accounts"]
        las = las.get("loyalty_accounts", []) if isinstance(las, dict) else las
        cat.loyalty_accounts = [{"id": str(a["id"]), "name": a["loyalty_currency"], "tags": tags.get(str(a["id"]), [])}
                                for a in las if a.get("status", "Active") == "Active"]
        for p in rows(responses["privilege_products"]):
            pol = next((u for u in p.get("usage_policy") or [] if u.get("active", True)), None)
            if pol and p.get("privilege_type") in ("Fixed-Product", "Fixed-Discount"):
                cat.privileges[str(p["id"])] = {"id": str(p["id"]), "name": p["name"], "usage_policy": str(pol["id"])}
        return cat

    @classmethod
    def from_bolapi_dumps(cls, directory, tenant, sponsor_id, account_id, default_tag=None):
        """Saved responses written by harness/ui/hosted/tenant-catalog.mjs ({status, body} per file)."""
        body = lambda f: json.load(open(os.path.join(directory, f + ".json")))["body"]
        resp = {k: body(k) for k in ("activity_attributes", "member_attributes", "product_attributes",
                                     "sponsor_loyalty_accounts", "privilege_products")}
        resp["lookup:BIT_TYPE"] = body("lookup_BIT_TYPE")
        for fname, lt in (("extra_prodcat", "PRODUCT_CATEGORY"), ("extra_tierclass", "TIER_CLASS")):
            if os.path.exists(os.path.join(directory, fname + ".json")):
                resp["lookup:" + lt] = body(fname)
        if os.path.exists(os.path.join(directory, "lovs.json")):
            resp["lovs"] = body("lovs")
        if os.path.exists(os.path.join(directory, "extra_tiers.json")):
            resp["tiers"] = body("extra_tiers")
        if os.path.exists(os.path.join(directory, f"extra_locations{sponsor_id}.json")):
            resp["locations"] = body(f"extra_locations{sponsor_id}")
        for f in os.listdir(directory):
            if f.startswith("extra_tags"):
                resp["tags:" + f[len("extra_tags"):-5]] = body(f[:-5])
        return cls.from_bolapi(tenant, sponsor_id, account_id, resp, default_tag)


class BolapiCatalogSource:
    """Live catalog from bolapi with the caller's JWT (same endpoints as the Blockly dropdowns). Cached per key."""

    def __init__(self, base_url, http=None, ttl_s=900):
        import httpx
        self.base = base_url.rstrip("/") + "/bolapi/v1/"
        self.http = http or httpx.Client(timeout=30)
        self.ttl_s, self._cache = ttl_s, {}

    def requests(self, sponsor_id, account_id):
        return {
            "activity_attributes": "attributelibrary/activity/",
            "member_attributes": "attributelibrary/member/?all=True",
            "product_attributes": "attributelibrary/custom/?module_type=PRODUCT",
            "sponsor_loyalty_accounts": f"sponsors/loyaltyaccounts/?sponsor_id={sponsor_id}&offer_type=reward",
            "privilege_products": f"product/?sponsor_in={sponsor_id}&as_privilege=true&page_size=500&bundle_type=static",
            "locations": f"sponsors/{sponsor_id}/locations/?active=True&page_size=1000",
            f"tags:{account_id}": f"loyaltyaccounts/{account_id}/tags/?active=true&page_size=1000",
            "tiers": "tier-list/?page_size=500",  # blockly-lov-registry.ts (segment tier dropdowns)
            "lovs": "lovs/",  # LOV definitions; BIT_CATEGORY carries its values (model)
            "lookup:BIT_TYPE": "lookups/?type=BIT_TYPE&active=True&page_size=1000",
            "lookup:PRODUCT_CATEGORY": "lookups/?type=PRODUCT_CATEGORY&active=True&page_size=1000",
            "lookup:TIER_CLASS": "lookups/?type=TIER_CLASS&active=True&page_size=1000",
        }

    def get(self, tenant, sponsor_id, account_id, authorization, default_tag=None):
        import time
        key = (tenant, sponsor_id, str(account_id))
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.ttl_s:
            return hit[1]
        resp = {}
        for name, rel in self.requests(sponsor_id, account_id).items():
            r = self.http.get(self.base + rel, headers={"Authorization": authorization})
            r.raise_for_status()
            resp[name] = r.json()
        cat = Catalog.from_bolapi(tenant, sponsor_id, account_id, resp, default_tag)
        self._cache[key] = (time.time(), cat)
        return cat
