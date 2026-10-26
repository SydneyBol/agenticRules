"""What the model may call: the tool schemas it is given. Only descriptions and shapes; the tools themselves are
code (core/session.py) and never import this."""
from ..core.tree import COUNTER_LEVELS

S, I, STRS = {"type": "string"}, {"type": "integer"}, {"type": "array", "items": {"type": "string"}}
PART = {"type": "string", "enum": ["offer_main", "segment", "rules"],
        "description": "the part it is about; each screen's API returns only its own part's items"}
NOTES = {"type": "array", "items": {"type": "object", "properties": {"part": PART, "text": S}, "required": ["part", "text"]}}


def tool(name, desc, props, required=None):
    return {"name": name, "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": list(props) if required is None else required}}


COUNTER = {"type": "object", "properties": {
    "name": S, "level": {"type": "string", "enum": list(COUNTER_LEVELS)},
    "data_type": {"type": "string", "enum": ["NUMBER", "TEXT", "DATE", "BOOLEAN"]},
    "counter_type": {"type": "string", "enum": ["REGULAR", "VECTOR"]}, "target_type": S,
    "namespace": {"type": "string", "description": "VECTOR only: the attribute it counts per (e.g. h_location)"},
    "entity_level": {"type": "string", "description": "VECTOR only; default Activity"}},
    "required": ["name", "data_type", "counter_type"]}
CANVAS_ARGS = {"canvas": {"type": "string", "enum": ["rules", "segment"]},
               "blocks": {"type": "object", "description": "The block tree: one offer_generic (rules) or targeting_holder (segment) root"},
               "counters": {"type": "array", "items": COUNTER, "description": "The offer counters the blocks refer to "
                                                                             "(with edits: kept from the last compile unless given)"},
               "edits": {"type": "array", "description": "Instead of blocks: changes to this canvas's last tree, by the "
                                                         "block ids problems name (rule#b9 -> b9). {id, fields} merges "
                                                         "fields; {id, picked}; {id, replace: node}; {id, remove: true}",
                         "items": {"type": "object", "properties": {"id": S, "fields": {"type": "object"}, "picked": S,
                                                                    "replace": {"type": "object"}, "remove": {"type": "boolean"}},
                                   "required": ["id"]}}}
BIT = {"type": "object", "properties": {
    "header": {"type": "object", "description": "BIT header attributes (h_bit_type, h_bit_amount, h_bit_date "
                                                "YYYY-MM-DDTHH:MM:SS, h_location, ...); defaults: the tenant's Spend activity type "
                                                "and Accrual category, amount 10, one day apart from 2026-10-20"},
    "lines": {"type": "array", "items": {"type": "object"}, "description": "line items: l_product_external_id, l_quantity, l_amount"},
    "expect": {"type": "object", "description": "what the manager's offer should do on this BIT: points (REWARD_POINTS "
                                                "total), privilege (id or null), mto {counter: value after}, mta {...}"}}}
TOOLS = [
    tool("lookup_values", "Find catalog codes for the manager's words, for an attribute whose values are not listed.",
         {"object": {"type": "string", "enum": ["activity", "member", "product"]}, "attribute": S,
          "text": {"type": "string", "description": "The manager's words for ONE value"}}),
    tool("set_offer_main", "The Offer Main settings, and the prompt each later screen shows.",
         {"offer_name": {"type": "string", "description": "under 50 characters"}, "subtitle": S, "offer_description": S,
          "terms": STRS, "start_date": {"type": "string", "description": "YYYY-MM-DD the manager stated, else empty"},
          "end_date": {"type": "string", "description": "YYYY-MM-DD the manager stated (last day included), else empty"},
          "max_usage_per_user": {"type": "integer", "description": "how often ONE member can earn it; 0 = unlimited"},
          "frequency": {"type": "string", "enum": ["none", "day", "week", "month", "total"]},
          "num_of_clients": {"type": "integer", "description": "total earnings across all members; 0 = unlimited"},
          "offer_frequency_type": {"type": "string", "enum": ["", "day", "lifetime"]},
          "segment_prompt": {"type": "string", "description": "who the offer is for, in the manager's words; empty = every member"},
          "rules_prompt": {"type": "string", "description": "the rules part in the manager's words, without what Offer Main "
                                                           "or the segment covers"}}),
    tool("validate", "Fast offline check of a canvas: toolbox blocks only, connection checks, field names, catalog "
                     "codes, counters declared.", CANVAS_ARGS, required=["canvas"]),
    tool("compile", "Load the canvas in the stock screen (rules: through Upload Rules). Returns what it dropped or "
                    "changed, validation messages, whether it compiled, and the counters it created.",
         CANVAS_ARGS, required=["canvas"]),
    tool("simulate", "Run the rules of the last compile in the real engine, one member, BITs in order (offer "
                     "counters persist across them), with the last compiled segment and the Offer Main dates and "
                     "limits applied as the engine applies them. Returns per BIT: points, privileges, counters, "
                     "skipped (and why), mismatches with expect; and whether the member is in the segment.",
         {"member": {"type": "object", "description": "{attributes: {date_of_birth, ...}, tier}"},
          "products": {"type": "object", "description": "SKU -> product attributes, e.g. {\"SNK-1\": {\"category\": \"SNACKS\"}}"},
          "bits": {"type": "array", "items": BIT}}, required=["bits"]),
    tool("finish", "Call once, last. Delivers the last clean compile of each canvas.",
         {"readback": {"type": "string", "description": "Plain-English description of what the offer will do"},
          "segment_name": {"type": "string", "description": "under 50 characters, when a segment was built"},
          "segment_description": S,
          "assumptions": NOTES, "blocking_questions": NOTES,
          "unsupported": {"type": "array", "items": {"type": "object", "properties": {"part": PART, "phrase": S, "reason": S,
                                                                                       "alternative": S},
                                                      "required": ["part", "phrase", "reason"]}}},
         required=["readback", "assumptions", "blocking_questions", "unsupported"]),
]
