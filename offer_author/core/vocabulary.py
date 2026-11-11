"""The UI's and bolapi's own names and form rules, in one place.

Each comes from the source cited beside it; nothing here is a guess about the manager's words or a tenant's habits.
"""

import json
import os
from typing import Any

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

# The stock screen's attribute lists by what a block names (blockly-http.service.ts getAttributesLOV), as catalog list
# names (Catalog.active, Catalog.holds). A member or third-party level; a condition's OBJECT; a product block's
# ATTRIBUTE_PRODUCTS or a condition's ProductOrOfferContext; the actions that pick a sweepstake or a play & win setup.
# Counters (MA_O, MA_A, MA_TO) and the tier programme (MA_T) are not attribute lists: they are checked on their own.
LEVEL_LISTS = {
    "MA_P": "member",
    "MA_CC": "card",
    "MA_CA": "bank_account",
    "MA_SP": "subscription",
    "MA_S": "analytics",
    "MP_S": "personalization",
    "MR_S": "retention",
    "MA_PP": "partner",
}
# where the attributes of a member-level list sit, as they are shown (the catalog text, the attribute search)
LIST_PLACES = {
    "card": "cobranded card (MA_CC)",
    "bank_account": "bank account (MA_CA)",
    "subscription": "subscription (MA_SP)",
    "analytics": "analytics write-back (MA_S)",
    "partner": "partner membership (MA_PP)",
    "product_offer": "product offer context",
    "location": "the transaction's store (OBJECT location)",
    "sponsor": "the transaction's sponsor (OBJECT sponsor)",
    "payment_method": "a payment's method (payment blocks)",
}
OBJECT_LISTS = {o: o for o in ("activity", "airport", "airport_pair", "location", "sponsor")}
PRODUCT_LISTS = {"products_attrs": "product", "product_offer_attributes": "product_offer"}  # product_cluster: none
# How each screen's condition lays out a member level that holds more than an attribute (if-generic.block.ts
# updateShapeByCardLevel, updateShapeForBankAccountCardLevel, updateShapeForSubscriptionPlan;
# targeting-condition-segment.block.ts updateShapeByCardSelected), by screen and level:
#   attribute: the field that names the level's attribute;
#   pick, picks: the field that picks a card, plan or programme, and the catalog list of its ids;
#   scope, scopes: the field that says which of the member's cards or accounts are read, and its codes.
# A level not named keeps its attribute in `attribute`.
LEVEL_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "rules": {
        "MA_CC": {
            "pick": "card_main_option",
            "picks": "card_list",
            "scope": "onlycard_second_option",
            "scopes": ("ALL_CARDS", "TRANSACTION_CARD"),
            "attribute": "attribute",
        },
        "MA_CA": {
            "scope": "card_main_option",
            "scopes": ("ALL_ACCOUNTS", "TRANSACTION_ACCOUNT"),
            "attribute": "attribute",
        },
        "MA_SP": {
            "pick": "selected_subscription_plan",
            "picks": "subscription_plan",
            "attribute": "selected_subscription_attribute",
        },
        "MA_PP": {
            "pick": "selected_partner_program",
            "picks": "partner_program",
            "attribute": "selected_partner_program_attribute",
        },
    },
    "segment": {
        "MA_CC": {"pick": "card_setup_id", "picks": "card_list", "attribute": "selected_card_attribute"},
        "MA_CA": {"attribute": "selected_bank_attribute"},
        "MA_SP": {
            "pick": "selected_subscription_plan",
            "picks": "subscription_plan",
            "attribute": "selected_subscription_attribute",
        },
        "MA_PP": {
            "pick": "selected_partner_program",
            "picks": "partner_program",
            "attribute": "selected_partner_program_attribute",
        },
    },
}
# A card condition on "the member's active cards" (the card picker's first option) compares card ids, not an attribute.
# The rules screen keeps its operator and its one compared card in fields of their own (if-generic.block.ts
# updateShapeByCardInput, updateShapeByActiveCardLevelOperators); the segment screen in `operators` and `value`
# (lov_lookup_active_card_level_operators). in / not_in take the cards from the popup (picked) on both.
ACTIVE_CARDS = "ACTIVE_CARDS"
ACTIVE_CARD_FIELDS = {
    "rules": {
        "operator": "activecard_mainoperators",
        "value": "cardvalue",
        "operators": ("==", "!=", "null", "not_null", "in", "not_in"),
    },
    "segment": {"operator": "operators", "value": "value", "operators": ("==", "!=", "in", "not_in")},
}
# every field of LEVEL_FIELDS and ACTIVE_CARD_FIELDS, in the order the screen builds them: a field set before the one
# its dropdown hangs on is cleared when that one is set (as attribute_level clears attribute)
LEVEL_FIELD_ORDER = (
    "card_main_option",
    "card_setup_id",
    "selected_subscription_plan",
    "selected_partner_program",
    "onlycard_second_option",
    "activecard_mainoperators",
    "cardvalue",
    "selected_card_attribute",
    "selected_bank_attribute",
    "selected_subscription_attribute",
    "selected_partner_program_attribute",
)
# Where a member level's value block keeps its codes when it has a picker (member-attributes.block.ts,
# member-attribute-segment.block.ts): (field, list) pairs. A level not named keeps its attribute in `ATTRIBUTE`.
VALUE_CODES = {
    "MA_CC": (("ATTRIBUTE", "card_list"), ("selected_card_attribute_targeted", "card"), ("cardattribute", "card")),
    "MA_SP": (
        ("selected_subscription_plan", "subscription_plan"),
        ("selected_subscription_attribute", "subscription"),
        ("ATTRIBUTE", "subscription"),
    ),
    "MA_PP": (
        ("selected_partner_program", "partner_program"),
        ("selected_partner_program_attribute", "partner"),
        ("ATTRIBUTE", "partner"),
    ),
}
# what those fields hold that is not a code: unset, and the card condition's "the member's active cards" (whose
# compared values are then card ids: CARD_VALUES)
NOT_CODES = ("", "null", "None", "undefined", ACTIVE_CARDS)
ACTION_LISTS = {
    "assign_sweepstakes": ("sweepstakes", "sweepstakeid"),
    "assign_play_win_reward": ("play_win", "playwinsetup"),
}

# attribute levels (block-definition.service.ts getAttributesTuple; thirdparty-action.block.ts)
OFFER_COUNTER = "MA_O"  # an offer counter: persists per member across transactions (the UI's default level)
ACTIVITY_COUNTER = "MA_A"  # an activity-level value: one transaction only
TIER_COUNTER = "MA_TO"
COUNTER_LEVELS = (OFFER_COUNTER, ACTIVITY_COUNTER, TIER_COUNTER)
# the levels an offer declares counters at and the upload creates them at (a tier counter belongs to its tier class)
DECLARED_LEVELS = (OFFER_COUNTER, ACTIVITY_COUNTER)
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
