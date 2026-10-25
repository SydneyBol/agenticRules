"""A large tenant catalog for accuracy at scale: the fixture catalog (all its codes kept) grown to 1000 attributes,
500 stores, 5000 SKUs and 250 activity types, with the look-alikes real tenants have: amounts next to "Net Amount" /
"Tax Amount", stores next to "Home Store" / "Last Visited Store", tier next to "Previous Tier", and stores that share a
name ("Pune Central" twice). Deterministic (seeded), so cases can name exact codes.

  python -m tests.support.scaled_catalog out.json   writes it as a catalog JSON (Catalog.from_json)
"""
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from offer_author.core.catalog import Attribute, Catalog  # noqa: E402

CITIES = ["Chennai", "Mumbai", "Pune", "Delhi", "Bengaluru", "Hyderabad", "Kolkata", "Ahmedabad", "Jaipur", "Lucknow",
          "Kochi", "Coimbatore", "Madurai", "Vijayawada", "Visakhapatnam", "Nagpur", "Indore", "Bhopal", "Surat", "Vadodara",
          "Chandigarh", "Goa", "Mysuru", "Mangaluru", "Trichy", "Thane", "Noida", "Gurugram", "Patna", "Ranchi",
          "Bhubaneswar", "Guwahati", "Dehradun", "Amritsar", "Ludhiana", "Nashik", "Aurangabad", "Rajkot", "Udaipur",
          "Varanasi", "Agra", "Kanpur", "Raipur", "Jodhpur", "Siliguri", "Thrissur", "Salem", "Tirupati", "Hubli", "Belagavi"]
KINDS = [("AIR", "Airport"), ("CEN", "Central"), ("MAL", "Mall"), ("STN", "Railway Station"), ("EXP", "Express"),
         ("HWY", "Highway"), ("OUT", "Outlet"), ("BCH", "Beach Road"), ("TEC", "Tech Park"), ("UNI", "University")]
BRANDS = ["Nescafe", "Bru", "Tata", "Amul", "Britannia", "Parle", "Haldiram", "Cadbury", "Nestle", "Lays", "Kurkure",
          "Bingo", "Pepsi", "Coca-Cola", "Sprite", "Thums Up", "Maaza", "Real", "Tropicana", "Paper Boat", "Red Bull",
          "Monster", "Lipton", "Taj Mahal", "Society", "MTR", "Aashirvaad", "Fortune", "Saffola", "Dabur", "Patanjali",
          "Himalaya", "Colgate", "Pepsodent", "Dove", "Lux", "Lifebuoy", "Dettol", "Surf Excel", "Ariel", "Tide", "Vim",
          "Harpic", "Lizol", "Maggi", "Yippee", "Knorr", "Kissan", "Heinz", "Del Monte"]
ITEMS = ["Classic Coffee", "Gold Coffee", "Instant Coffee", "Filter Coffee", "Green Tea", "Masala Tea", "Premium Tea",
         "Milk", "Butter", "Cheese Slices", "Ice Cream", "Biscuits", "Cookies", "Cream Biscuits", "Namkeen", "Bhujia",
         "Chocolate Bar", "Dark Chocolate", "Potato Chips", "Cola", "Lemon Soda", "Mango Drink", "Orange Juice",
         "Apple Juice", "Energy Drink", "Iced Tea", "Noodles", "Pasta", "Ketchup", "Mayonnaise", "Atta", "Sunflower Oil",
         "Honey", "Toothpaste", "Soap", "Shampoo", "Detergent Powder", "Dishwash Bar", "Toilet Cleaner", "Floor Cleaner"]
SIZES = ["50g", "100g", "200g", "500g", "1kg", "250ml", "500ml", "1L", "2L", "Pack of 4", "Pack of 6", "Family Pack"]
# look-alikes placed next to the attributes the cases use
LOOKALIKES = {
    "activity": [("h_net_amount", "Net Amount", "NUMBER"), ("h_tax_amount", "Tax Amount", "NUMBER"),
                 ("h_discount_amount", "Discount Amount", "NUMBER"), ("h_gross_amount", "Gross Amount", "NUMBER"),
                 ("h_tip_amount", "Tip Amount", "NUMBER"), ("h_points_amount", "Points Redeemed Amount", "NUMBER"),
                 ("h_bit_received_date", "BIT Received Date", "DATETIME"), ("h_original_bit_date", "Original BIT Date", "DATETIME"),
                 ("h_location_region", "Store Region", "TEXT"), ("h_pos_terminal", "POS Terminal", "TEXT"),
                 ("h_channel", "Sales Channel", "LOOKUP"), ("h_partner_store_code", "Partner Store Code", "TEXT")],
    "member": [("previous_tier", "Previous Tier", "LOOKUP"), ("tier_expiry_date", "Tier Expiry Date", "DATE"),
               ("partner_tier", "Partner Tier", "LOOKUP"), ("home_store", "Home Store", "LOOKUP"),
               ("enrolling_store", "Enrolment Store", "LOOKUP"), ("last_visited_store", "Last Visited Store", "LOOKUP"),
               ("spouse_date_of_birth", "Spouse Date of Birth", "DATE"), ("child_date_of_birth", "Child Date of Birth", "DATE"),
               ("anniversary_date", "Wedding Anniversary", "DATE"), ("first_purchase_date", "First Purchase Date", "DATE"),
               ("employee_flag", "Is Employee", "LOOKUP"), ("age_band", "Age Band", "LOOKUP")],
}
FILLER = {"activity": ["Field", "Code", "Reference", "Flag", "Count", "Channel Detail", "Batch", "Source", "Type Detail"],
          "member": ["Preference", "Consent", "Score", "Segment Flag", "Survey Answer", "Profile Field", "Opt In", "Interest"],
          "product": ["Attribute", "Group", "Flag", "Spec", "Family", "Line", "Season", "Pack Detail"]}
TARGET = {"activity": 380, "member": 470, "product": 150}  # 1000 attributes


def stores(rnd):
    out = {}
    for city in CITIES:
        for code, kind in KINDS:
            out[f"{city[:3].upper()}-{code}"] = f"{city} {kind}"
    out["PUN-CEN-2"] = "Pune Central"  # a second store with the same name as PUN-CEN
    out["CHE-AIR-T2"] = "Chennai Airport Terminal 2"
    return out  # 50 cities x 10 + 2 = 502


def skus(rnd):
    out = {}
    combos = [(b, i, s) for b in BRANDS for i in ITEMS for s in SIZES]
    rnd.shuffle(combos)
    for n, (b, i, s) in enumerate(combos[:5000], 1):
        out[f"SKU-{n:05d}"] = f"{b} {i} {s}"
    return out


def build(seed=7):
    rnd = random.Random(seed)
    cat = Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))
    cat.tenant = "scaled"
    loc = cat.attr("activity", "h_location")
    loc.values = dict(loc.values, **stores(rnd))
    sku = skus(rnd)
    cat.attributes["product"]["sku"] = Attribute(code="sku", label="SKU", type="LOOKUP", lookup_type="PRODUCT_SKU", values=sku)
    cat.attributes["product"]["brand"] = Attribute(code="brand", label="Brand", type="LOOKUP", lookup_type="BRAND",
                                                   values={b.upper().replace(" ", "_"): b for b in BRANDS})
    for obj, rows in LOOKALIKES.items():
        for code, label, typ in rows:
            vals = None
            if code in ("home_store", "enrolling_store", "last_visited_store"):
                vals = dict(loc.values)
            elif code in ("previous_tier", "partner_tier"):
                vals = dict(cat.attr("member", "tier").values)
            elif typ == "LOOKUP":
                vals = {"Y": "Yes", "N": "No"} if code == "employee_flag" else {"A": "Band A", "B": "Band B"}
            cat.attributes[obj][code] = Attribute(code=code, label=label, type=typ, values=vals)
    for obj, n in TARGET.items():
        i = 0
        while len(cat.attributes[obj]) < n:
            i += 1
            word = FILLER[obj][i % len(FILLER[obj])]
            code = f"{'h_' if obj == 'activity' else ''}cf_{obj[:3]}_{i:03d}"
            typ = rnd.choice(["TEXT", "NUMBER", "DATE", "LOOKUP"])
            vals = {f"V{j}": f"{word} option {j}" for j in range(1, rnd.randint(3, 8))} if typ == "LOOKUP" else None
            cat.attributes[obj][code] = Attribute(code=code, label=f"Custom {word} {i}", type=typ, values=vals)
    for i in range(len(cat.activity_types), 250):
        cat.activity_types[f"EVT_{i:03d}"] = f"Partner Event {i}"
    return cat


def to_json(cat):
    return {"tenant": cat.tenant, "sponsor_id": cat.sponsor_id, "today": cat.today, "default_account": cat.default_account,
            "default_tag": cat.default_tag, "activity_types": cat.activity_types, "loyalty_accounts": cat.loyalty_accounts,
            "privileges": list(cat.privileges.values()), "tiers": cat.tiers,
            "attributes": {o: [{k: v for k, v in vars(a).items() if v is not None} for a in attrs.values()]
                           for o, attrs in cat.attributes.items()}}


if __name__ == "__main__":
    c = build()
    if len(sys.argv) > 1:
        json.dump(to_json(c), open(sys.argv[1], "w"), indent=1)
    print({o: len(a) for o, a in c.attributes.items()}, "stores", len(c.attr("activity", "h_location").values),
          "skus", len(c.attr("product", "sku").values), "activity types", len(c.activity_types))
