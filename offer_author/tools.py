"""The model's contract: system prompt + one tool per intent primitive + `finish`.

Non-strict tools validated locally (validate.py): Bedrock rejects one big strict schema (>16 union params) and a
strict tool per primitive (grammar too large). Forced tool_choice 400s on Opus/Sonnet 5.5, so tool_choice is auto.
"""

SYSTEM = """You translate a loyalty manager's English description of a REWARD offer into a structured offer INTENT for the
Gravty loyalty platform. The intent is compiled deterministically into Blockly rules, so you describe WHAT the offer
does using the primitives below; never invent counters, flags or rule plumbing yourself.

Primitives (use as many as needed, in order):
- earn: points proportional to a measure. multiplier (amount x m) or rate_per_unit (points per currency unit); optional conditions.
- bonus_if: fixed reward when all conditions hold.
- tiers: exclusive bands on a measure (gt/ge/lt/le bounds), each with multiplier or rate. Only one band can match a transaction.
- cap: lifetime maximum bonus points per member; limits every points reward in the offer (earn, tiers, line_item,
  unlock, fixed point bonuses).
- line_item: per line-item reward for products matching a product attribute filter (points_per_unit, or multiplier on l_amount).
- collect_all: reward once a member has bought from every listed product group (across any visits).
- nth_activity: reward on the Nth qualifying activity; repeat=true for every Nth.
- milestone: reward once when a cumulative measure reaches a threshold.
- unique_count: reward once when the member has done activities at N distinct values of a dimension (e.g. h_location).
- unlock: a trigger activity unlocks a reward on later activities.
- streak: activity in each of N consecutive periods.
- occasion: recurring window of +/- window_days around a member date attribute, rewarded once per occurrence.

Rules:
- Use ONLY codes from the tenant catalog below. Match the manager's words to catalog labels (e.g. "online store" -> the
  location whose label says online). If a phrase matches several codes or none, add a blocking question instead of guessing.
- Long value lists (stores, products, lookups) are not written out: the catalog says "N values, not listed". For those,
  call lookup_values with the manager's words BEFORE writing the add_* calls (one lookup per phrase; several lookups can
  go in one turn), then use only codes it returned. When it reports "ambiguous", pick the code the description clearly
  means, otherwise keep your best choice and add a blocking question naming the candidate codes.
- Every restriction the manager states (store, day, time, tier, amount, activity) must be a condition on the primitive it
  restricts, never only in the readback. For nth_activity, milestone and unique_count the conditions decide which
  activities count towards the target.
- Product attributes (and l_* line attributes) only apply per line item: use line_item or collect_all for "per X item",
  never earn/bonus_if with a product condition.
- Activity types: always fill finish.activity_types. "spend", "spends", "spending" mean the type labelled exactly
  "Spend"; use another type only when the manager names it (e.g. "shopping", "booking", "stay"). If no activity is named,
  use the Spend type and record that as an assumption. Never ask which type "spend" means. The activity type is the
  offer's Filter: never repeat it as a condition of a rule.
- A condition every rule needs (e.g. "Gold members ..." applying to the whole offer) is written once per rule as
  usual; it is moved into the offer's Filter automatically, so don't invent extra rules to avoid repeating it.
- Never ask for offer dates: none stated means the offer settings decide them.
- Offer start/end dates, per-member usage limits and frequency go in finish: they are Offer Main settings, which the
  engine checks before any rule runs, so never repeat them as rule conditions. Only dates the manager stated: leave
  them empty otherwise, never default to today.
- When the message lists what is "already handled outside the rules", build only the rest.
- "Nx points" means points = measure x N in total (not N-1 on top of base earning); record that as an assumption.
- "over X" is strict (gt); "at least X" is ge.
- "A or B" for the same reward: one earn/bonus_if with or_groups (it becomes one OR block the manager can read), not
  separate rules; the reward is then given once even when both match.
- If something cannot be expressed on a single member's own transactions, add it to `unsupported` with a reason and an alternative.
- assumptions: every interpretation you made that the manager should glance at (defaults, conventions, missing dates). They do not block.
  An assumption never drops or widens something the manager stated: a stated restriction or measure you cannot
  express with catalog codes (e.g. a payment method, a currency rate) goes in `unsupported`.
- blocking_questions: ONLY when no sensible draft is possible without the answer, or when a phrase matches several
  codes (then ask even if the draft uses all of them). Still build everything else.
- Always return a result: never finish without primitives, a blocking question or an unsupported entry.
- DAY_OF_WEEK numbers: Mon=1 ... Sun=7. HOUR_OF_DAY is 0-23.

Record the intent ONLY by calling the add_* tools (one call per primitive, all in one response) and then `finish`
exactly once. Do not answer in prose.
"""


def obj(props, required=None):
    return {"type": "object", "additionalProperties": False, "properties": props, "required": required or list(props)}


S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
STRS = {"type": "array", "items": S}
COND = obj({
    "object": {"type": "string", "enum": ["activity", "member", "product"]},
    "attribute": S,
    "dimension": {"type": "string", "enum": ["NONE", "DAY_OF_WEEK", "HOUR_OF_DAY", "MONTH", "YEAR", "DAY_OF_MONTH"]},
    "op": {"type": "string", "enum": ["==", "!=", ">", ">=", "<", "<=", "in", "not_in"]},
    "value": {"type": "string", "description": "Single value as text (numbers as digits). Empty when op is in/not_in."},
    "values": STRS,
})
REWARD = obj({"kind": {"type": "string", "enum": ["points", "privilege"]}, "points": N,
              "privilege_id": {"type": "string", "description": "Catalog privilege id; empty for points"}})
BAND = obj({"lower": N, "lower_inclusive": B, "has_upper": B, "upper": N, "upper_inclusive": B,
            "factor_kind": {"type": "string", "enum": ["multiplier", "rate"]}, "factor": N})


def tool(name, desc, props, required=None):
    """A CONDS field is optional unless listed: older recorded calls (and plain 'every 5th visit') omit it."""
    required = required or [k for k, v in props.items() if v is not CONDS]
    return {"name": name, "description": desc, "input_schema": obj(props, required)}


CONDS = {"type": "array", "items": COND,
         "description": "Only activities matching ALL of these qualify (store, day, time, tier, amount); empty if none"}
OR_GROUPS = {"type": "array", "items": {"type": "array", "items": COND},
             "description": "Alternative condition groups (any group may match); empty if none"}

TOOLS = [
    tool("add_earn", "Points proportional to a measure (multiplier or points per unit), optionally only when conditions hold.",
         {"measure": S, "factor_kind": {"type": "string", "enum": ["multiplier", "rate_per_unit"]}, "factor": N,
          "conditions": {"type": "array", "items": COND},
          "or_groups": OR_GROUPS}),
    tool("add_bonus_if", "Fixed reward when all conditions hold (and, if or_groups is given, at least one group matches).",
         {"conditions": {"type": "array", "items": COND}, "or_groups": OR_GROUPS, "reward": REWARD}, required=["conditions", "reward"]),
    tool("add_tiers", "Exclusive bands on a measure; only one band applies per transaction.",
         {"measure": S, "bands": {"type": "array", "items": BAND}, "conditions": CONDS}),
    tool("add_cap", "Lifetime maximum bonus points per member across every points reward in the offer.", {"max_points": N}),
    tool("add_line_item", "Per line-item reward for products whose attribute matches.",
         {"product_attribute": S, "values": STRS, "factor_kind": {"type": "string", "enum": ["points_per_unit", "multiplier_on_line_amount"]}, "factor": N}),
    tool("add_collect_all", "Reward once the member has bought from every group (any visits).",
         {"product_attribute": S, "groups": {"type": "array", "items": STRS}, "reward": REWARD}),
    tool("add_nth_activity", "Reward on the Nth qualifying activity; repeat for every Nth. Only activities matching conditions count.",
         {"n": I, "repeat": B, "conditions": CONDS, "reward": REWARD}),
    tool("add_milestone", "Reward once when a cumulative measure reaches a threshold. Only activities matching conditions add to it.",
         {"measure": S, "threshold": N, "conditions": CONDS, "reward": REWARD}),
    tool("add_unique_count", "Reward once after activities at N distinct values of a dimension. To restrict the values (e.g. "
         "'among stores A, B, C'), add an in-condition on the same dimension.", {"dimension": S, "n": I, "conditions": CONDS, "reward": REWARD}),
    tool("add_unlock", "Trigger activity unlocks a points multiplier on later matching activities.",
         {"trigger": {"type": "array", "items": COND}, "then_conditions": {"type": "array", "items": COND}, "multiplier": N}),
    tool("add_streak", "Activity in each of N consecutive periods.", {"period": {"type": "string", "enum": ["DAY", "WEEK", "MONTH", "YEAR"]}, "n": I, "reward": REWARD}),
    tool("add_occasion", "Recurring window +/- days around a member date attribute; rewarded once per occurrence.",
         {"date_attribute": S, "window_days": I, "conditions": CONDS, "reward": REWARD}),
    tool("lookup_values", "Find catalog codes for the manager's words for an attribute whose values are not listed "
         "(or to check a close call). Returns the best-matching codes with labels; 'ambiguous' lists codes that match equally.",
         {"object": {"type": "string", "enum": ["activity", "member", "product"]}, "attribute": S,
          "text": {"type": "string", "description": "The manager's words for ONE value, e.g. 'Chennai airport store'"}}),
    tool("finish", "Call exactly once, last.",
         {"readback": {"type": "string", "description": "Plain-English description of what the offer will do"},
          "activity_types": STRS,
          "start_date": {"type": "string", "description": "YYYY-MM-DD the manager stated, else empty"},
          "end_date": {"type": "string", "description": "YYYY-MM-DD the manager stated (last day included), else empty"},
          "max_usage_per_user": {"type": "integer", "description": "0 if unlimited"},
          "frequency": {"type": "string", "enum": ["none", "day", "week", "month", "total"]},
          "assumptions": STRS,
          "blocking_questions": STRS,
          "unsupported": {"type": "array", "items": obj({"phrase": S, "reason": S, "alternative": S})}}),
]
SCHEMAS = {t["name"]: t["input_schema"] for t in TOOLS}
