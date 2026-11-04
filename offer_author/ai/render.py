"""What the model reads of a Problem: one wording per problem code.

The checks and tools (offer_author.core) report Problems as data; this module is the only place they become text for
the model. `reply` turns a whole tool result into what the model is sent.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..domain import BlockRef, Problem

Facts = Mapping[str, Any]


def block(ref: BlockRef | None, facts: Facts) -> str:
    """A block as problems name it: ``type#id``, then where it sits when known."""
    if ref is None:
        return str(facts.get("block_text", ""))
    return f"{ref.type}#{ref.id}" + (f" ({ref.path})" if ref.path else "")


def _counter_kind(d: Facts) -> str:
    d = dict(d)
    return f"{d['level']} {d['data_type']} {d['counter_type']}" + (
        f" per {d['namespace']}" if d.get("namespace") else ""
    )


def _counter_differs(f: Facts) -> str:
    return f"the offer's counter is {_counter_kind(f['have'])}, these rules need {_counter_kind(f['want'])}"


def _values_not_codes(f: Facts) -> str:
    closest = f"; closest: {', '.join(f['closest'])}" if f["closest"] else ""
    return f"{f['attribute']} values {list(f['values'])} are not codes of {f['attribute']}{closest} (use lookup_values)"


def _top_level_blocks(f: Facts) -> str:
    return (
        f"the workspace must hold exactly one enabled top-level block, {f['root']} (every other top-level block must "
        f"be disabled: Blockly turns each enabled one into code), found {list(f['found'])}"
    )


def _not_a_block_tree(f: Facts) -> str:
    return "not a block tree: " + (text(f["problem"]) if f.get("problem") else str(f.get("error")))


WORDING: dict[str, Callable[[Facts], str]] = {
    # writing the tree (core/tree.py)
    "no_block": lambda f: f"no block {f['id']} in the last tree",
    "not_in_statement_list": lambda f: (
        f"{f['edit']}: block {f['id']} is not in a statement list (use its parent's input)"
    ),
    "append_to_value_input": lambda f: (
        f'append: {f["input"]} of block {f["id"]} is a value input: set it with {{"id", "{f["input"]}": block}}'
    ),
    "edit_without_id": lambda f: "every edit needs the id of a block in the last tree",
    "remove_root": lambda f: "the root block cannot be removed",
    "defs_not_a_mapping": lambda f: "defs must be {name: block or [blocks]}",
    "no_def": lambda f: f"no def '{f['name']}' (defs: {', '.join(f['known']) or 'none'})",
    "def_uses_itself": lambda f: f"def '{f['name']}' uses itself",
    "def_is_several_blocks": lambda f: f"def '{f['name']}' is several blocks: use it in a statement list",
    "block_without_type": lambda f: f"{f['at']}: every block needs a type",
    "value_input_takes_one": lambda f: f"{f['at']}: value input {f['input']} takes exactly one block",
    "no_root": lambda f: "the tree must have exactly one root block (offer_generic)",
    # fields the screen would drop (core/tree.py unfilled)
    "codes_belong_in_picked": lambda f: (
        f"{f['block']}: '{f['name']}' is coded (give the codes in picked, not value: the in / not_in operators take "
        "them from the popup)"
    ),
    "code_belongs_in_value": lambda f: (
        f"{f['block']}: '{f['name']}' is coded (give one code in value, not picked: the {f['operator']} operator takes "
        "it from the dropdown)"
    ),
    "date_part_field": lambda f: (
        f"{f['block']}: '{f['name']}' is a {f['type']}: its date part goes in {f['want']}, not {f['given']}"
    ),
    "date_value_without_part": lambda f: (
        f"{f['block']}: '{f['name']}' is a {f['type']} with no date part: set {f['want']} (DATE for the whole date, "
        "CALENDAR_DATE for the day and month in any year); left empty the screen reads it as CALENDAR_DATE"
    ),
    "date_has_no_time": lambda f: (
        f"{f['block']}: '{f['name']}' is a DATE (no time of day): {f['part']} needs a DATETIME attribute"
    ),
    "no_value_field": lambda f: (
        f"{f['block']}: '{f['name']}' is a {f['type']}: the screen has no value field for it; compare with a value "
        "block in RHS_INPUT (static_value for a number or a date part, a date block for a date)"
    ),
    "unknown_condition_attribute": lambda f: (
        f"{f['block']}: '{f['name']}' is not a declared counter nor a catalog attribute: declare the counter (with "
        "its level) in counters, or give OBJECT and attribute_level"
    ),
    "condition_without_operator": lambda f: f"{f['block']}: '{f['name']}' has no operator: give operators",
    "condition_without_value": lambda f: (
        f"{f['block']}: '{f['name']}' compares with nothing: give the value (value, or a value block in RHS_INPUT)"
    ),
    "segment_condition_without_level": lambda f: (
        f"{f['block']}: give attr_level, the level of the member attribute it reads (MA_P profile, MA_T tier "
        "programme, ...)"
    ),
    "tier_class_not_in_catalog": lambda f: (
        f"{f['block']}: tierslevel {f['given']!r} is not a tier class of this tenant ({', '.join(f['known'])})"
    ),
    "tier_not_in_class": lambda f: (
        f"{f['block']}: tier {f['given']!r} is not in tier class {f['tier_class']!r} ({', '.join(f['known'])})"
    ),
    "or_operand_empty": lambda f: f"{f['block']}: an OR needs a condition in both OPERAND1 and OPERAND2",
    "rule_without_tag": lambda f: "a rule with no tag (IDENTIFIER): give each rule a short name of what it does; it "
    "names the rule on the canvas, in the saved offer and in the engine check's report",
    "rule_tag_repeated": lambda f: f"two rules share the tag {f['name']!r}: give each rule a name of its own",
    "rule_without_action": lambda f: f"rule {f['name']!r} has no action: it never rewards or counts anything",
    "no_reward_tag": lambda f: (
        f"{f['block']}: give REWARD_TAGS, one of the account's tags (the tenant has no configured reward tag)"
    ),
    "update_without_counter": lambda f: (
        f"{f['block']}: give mta_attribute, the name of the counter it updates (mta_type_value is its level, which "
        "the tools fill)"
    ),
    "undeclared_update_counter": lambda f: (
        f"{f['block']}: '{f['name']}' is not a declared counter: declare it in counters with its level (MA_O or "
        "MA_A), or give mta_type_value"
    ),
    "level_field_not_a_level": lambda f: (
        f"{f['block']}: mta_type_value is the counter's level (MA_O, MA_A), not '{f['given']}': the counter's name "
        "goes in mta_attribute; leave mta_type_value out"
    ),
    # offline grammar and catalog checks (core/canvas/checks/grammar.py)
    "not_well_formed": lambda f: f"not well-formed XML: {f['error']}",
    "top_level_blocks": _top_level_blocks,
    "not_in_toolbox": lambda f: f"{f['at']}: block type {f['type']!r} is not in the {f['canvas']} toolbox",
    "cannot_plug_here": lambda f: (
        f"{f['at']}: {f['type']} cannot plug in here (its checks {list(f['checks'])}, the input accepts "
        f"{list(f['accepts'])})"
    ),
    "in_block": lambda f: f"{f['at']}: {f['type']}: {text(f['problem'])}",
    "no_such_input": lambda f: f"{f['at']}: {f['type']} has no {f['kind']} input {f['name']!r} (has {list(f['has'])})",
    "no_such_field": lambda f: f"{f['at']}: {f['type']} has no field {f['name']!r}",
    "no_input": lambda f: f"{f['parent']} has no input {f['input']!r} (has {list(f['has'])})",
    "not_a_statement_block": lambda f: (
        f"{f['type']} cannot plug into {f['parent']}.{f['input']}: it has no previous connection; the input takes "
        "statement blocks"
    ),
    "not_a_value_block": lambda f: (
        f"{f['type']} cannot plug into {f['parent']}.{f['input']}: it has no output; the input takes a value block"
    ),
    "checks_do_not_fit": lambda f: (
        f"{f['type']} cannot plug into {f['parent']}.{f['input']}: its checks {list(f['checks'])}, the input accepts "
        f"{list(f['accepts'])}"
    ),
    "cannot_follow": lambda f: f"{f['type']} cannot follow {f['previous']} in {f['parent']}.{f['input']}",
    "attribute_not_in_catalog": lambda f: f"{f['object']} attribute {f['attribute']!r} is not in the tenant catalog",
    "values_not_codes": _values_not_codes,
    "privilege_not_in_catalog": lambda f: f"privilege {f['id']!r} is not in the catalog",
    "usage_policy_not_of_privilege": lambda f: (
        f"usage policy {f['id']!r} is not one of privilege {f['privilege']}'s: {', '.join(f['policies'])}"
    ),
    "account_not_in_catalog": lambda f: (
        f"{f['field']} {f['id']!r} is not one of the sponsor's loyalty accounts in the catalog"
    ),
    "text_compared_with_block": lambda f: (
        f"{f['name']} is TEXT: with {f['operator']} the screen takes the compared text typed in `value` and has no"
        " RHS_INPUT, so the block there is dropped with every block after it (a TEXT counter is compared only with"
        " typed text; a text attribute's match / not_match take a value block)"
    ),
    "operator_not_a_code": lambda f: (
        f"{f['name']}: operators {f['given']!r} is not an operator code, so the screen selects none:"
        f" {' '.join(f['codes'])}"
    ),
    "unique_after_reset": lambda f: (
        f"counter {f['name']} is reset and read as unique: a reset sets its values to 0 and keeps its keys, and unique"
        " counts the keys whatever their values, so what the condition reads does not go down at the reset"
    ),
    "attribute_not_of_object": lambda f: (
        f"{f['attribute']} is not an attribute of OBJECT {f['object']} (the screen loads the condition empty): it is"
        f" an attribute of OBJECT {f['owner']}"
    ),
    "tag_not_of_account": lambda f: (
        f"reward tag {f['tag']!r} is not a tag of loyalty account {f['account']}: {', '.join(f['tags'])}"
    ),
    # counters (core/run/canvases.py, core/counters.py)
    "counter_declared_twice": lambda f: f"counter '{f['name']}' is declared twice",
    "vector_without_namespace": lambda f: f"counter '{f['name']}' is VECTOR: give its namespace",
    "counter_taken": lambda f: (
        f"counter '{f['name']}' already exists on this offer and {text(f['why'])}: use another name"
    ),
    "undeclared_counter": lambda f: (
        f"the blocks use counter '{f['name']}' at {f['level']}, but counters declares it at {f['declared']}: "
        "a counter has one level, so declare it at the level the blocks use or use it at its declared level"
        if f.get("declared")
        else f"the blocks use counter '{f['name']}' at {f['level']}, which is not declared in counters"
    ),
    "counter_declared_by_tools": lambda f: (
        f"counter '{f['name']}' was not in counters: declared {f['level']} {f['data_type']}, "
        + ("as declared earlier in this run" if f["source"] == "earlier" else "from how the blocks use it")
    ),
    "counter_on_live_offer": lambda f: (
        f"the offer is in {f['status'] or 'an unknown status'}, so members may already hold values in it"
    ),
    "counter_differs": _counter_differs,
    # a compose call (core/run/canvases.py)
    "unknown_canvas": lambda f: "canvas must be rules or segment",
    "nothing_to_compose": lambda f: "send blocks (a whole tree), or edits and/or loose blocks",
    "no_tree_to_edit": lambda f: f"no earlier {f['canvas']} tree to edit: send blocks",
    "mutation_given": lambda f: (
        f"{f['at']}: remove 'mutation'; give fields, picked codes and counters, the tools write the mutation"
    ),
    "not_a_block_tree": _not_a_block_tree,
    "fix_with_counters": lambda f: (
        'edits by block id, or the counters, e.g. {"canvas": "' + f["canvas"] + '", "counters": [...]}'
    ),
    "fix_with_edits": lambda f: (
        'edits by block id, e.g. {"canvas": "' + f["canvas"] + '", "edits": [{"id": "b9", "fields": {...}}]}'
    ),
    "loose_saved": lambda f: "already on the saved canvas",
    "loose_asked": lambda f: "asked for on its own",
    "loose_not_attached": lambda f: "not attached",
    "canvas_not_clean": lambda f: f"{f['canvas']}: {text(f['problem'])}",
    # Offer Main (core/run/offer_main.py)
    "offer_name_too_long": lambda f: f"offer_name must be at most {f['max']} characters",
    "offer_name_empty": lambda f: "offer_name is empty",
    "date_not_iso": lambda f: f"{f['field']} '{f['value']}' is not YYYY-MM-DD",
    "end_before_start": lambda f: "end_date is before start_date",
    "not_a_choice": lambda f: f"{f['field']} must be one of {', '.join(map(repr, f['allowed']))}",
    # finishing (core/run/policy.py)
    "offer_main_not_set": lambda f: "Offer Main is not set: call set_offer_main",
    "segment_name_empty": lambda f: "segment_name is empty: name the member segment you built",
    "bit_calendar_date_in_filter": lambda f: "an if_generic_adaptive reading h_bit_date as CALENDAR_DATE in the On "
    "Activity filter: the screen writes it with a variable only a rule has, and the offer's rules do not compile. The "
    "filter's own condition block (activity_criteria) and a rule condition both compile",
    "attribute_not_of_level": lambda f: f"{f['name']} is not an attribute of ATTRIBUTE_TYPE {f['given']}: the catalog "
    f"lists it under {f['owner']}, and the screen drops an ATTRIBUTE its level does not list",
    "segment_prompt_without_segment": lambda f: "Offer Main's segment_prompt names who the offer is for, but the "
    "segment canvas has no conditions: the Select Members screen builds its segment from that prompt and gets none. "
    "Either the segment holds those members, or segment_prompt is empty (every member)",
    # catalog lookups (core/catalog.py)
    "not_a_catalog_attribute": lambda f: f"{f['attribute']} is not a catalog attribute",
    "no_value_list": lambda f: f"{f['attribute']} has no value list",
}


def text(problem: Problem) -> str:
    """The problem as the model reads it.

    Raises:
        KeyError: The code has no wording here (a code worded for the manager only, see offer_author.api.text).
    """
    facts = problem.data
    facts["block"] = block(problem.block, facts)
    return WORDING[problem.code](facts)


def reply(value: Any, word: Callable[[Problem], str] = text) -> Any:
    """A tool result (or any nested value) with every Problem in it worded."""
    if isinstance(value, Problem):
        return word(value)
    if isinstance(value, Mapping):
        return {k: reply(v, word) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [reply(v, word) for v in value]
    if isinstance(value, set):
        return [reply(v, word) for v in value]
    return value
