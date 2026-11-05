"""The UI's and bolapi's own names and form rules, in one place.

Each comes from the source cited beside it; nothing here is a guess about the manager's words or a tenant's habits.
"""

import json
import os

# the Offer Main form: offer name length, frequency choices (generated: tools/build/offer_main_form.py)
with open(os.path.join(os.path.dirname(__file__), "data", "offer_main_form.json")) as _f:
    OFFER_MAIN_FORM = json.load(_f)

# block mutation `object` -> catalog object (gravty-ui gravty-blockly/blocks/rule-level/if-generic.block.ts)
CATALOG_OBJECT = {
    "activity": "activity",
    "member": "member",
    "products_attrs": "product",
    "airport": "airport",
    "airport_pair": "airport_pair",
}

# attribute levels (block-definition.service.ts getAttributesTuple; thirdparty-action.block.ts)
OFFER_COUNTER = "MA_O"  # an offer counter: persists per member across transactions (the UI's default level)
ACTIVITY_COUNTER = "MA_A"  # an activity-level value: one transaction only
TIER_COUNTER = "MA_TO"
COUNTER_LEVELS = (OFFER_COUNTER, ACTIVITY_COUNTER, TIER_COUNTER)
MEMBER_ATTRIBUTE = "MA_P"  # a member profile attribute (the attribute library)
ATTRIBUTE_LEVELS = (
    None,
    "",
    "null",
    MEMBER_ATTRIBUTE,
)  # a block's level when it reads a catalog attribute, not a counter

# bolapi MemberTrackedOfferAttributes.level -> block level (bolapi offers/models.py)
BOLAPI_LEVELS = {
    "OFFER": OFFER_COUNTER,
    "ACTIVITY": ACTIVITY_COUNTER,
    "TIER": TIER_COUNTER,
    "TIER-ACTIVITY": ACTIVITY_COUNTER,
}

ACTIVITY_TYPE = "h_bit_type"  # the activity attribute whose values are the tenant's activity types (lookup BIT_TYPE)
# the transaction's own type, amount and date-time: nearly every offer reads them, and a description rarely names them
# as such ("a spend of $50", "on weekends"), so attribute grounding always offers them (AttributeGrounding.md)
TRANSACTION_FIELDS = (ACTIVITY_TYPE, "h_bit_amount", "h_bit_date")

# value lists whose values set one offer apart from another of its kind: stores and tier classes (bolapi lookup types)
DISTINCT_VALUE_LISTS = ("SPONSOR_LOCATION_LIST", "TIER_CLASS")
