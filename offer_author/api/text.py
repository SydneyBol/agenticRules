"""What the manager reads: the wording of run results at the API edge.

A run (offer_author.ai.agent) returns its result with Problems in it; `public` words every one of them, so the
log and the error messages read as plain text. Problems the model also reads
(a compose's problems in `grammar_problems`, a loose block's reason) keep the model's wording
(offer_author.ai.render); the codes below are worded for the manager only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..ai import render
from ..domain import Problem

Facts = Mapping[str, Any]

FOR_THE_MANAGER: dict[str, Callable[[Facts], str]] = {
    "stopped": lambda f: (
        "I could not finish this offer within the step limit, so there is no draft to review. Could you describe it "
        "again, more simply or one part at a time?"
    ),
    "nothing_built": lambda f: "I couldn't turn this into an offer. Could you describe who earns what, and when?",
    "loose_block": lambda f: (
        f"The {f['type']} block is on the canvas but not attached, and disabled so it adds nothing to the rules "
        f"({word(f['reason'])}). Attach it where it fits and enable it, or delete it."
    ),
    "select_segment": lambda f: (
        f"Who qualifies is the member segment '{f['name']}': create and select it on Select Members, otherwise every "
        "member qualifies."
    ),
    "verify_mismatch": lambda f: (
        f"The offer's test cases were run in the engine with mocked members: {f['count']} of {f['bits']} test "
        "transactions did not give what the description implies. Check the rules in Offer Testing before launch."
    ),
    "model_declined": lambda f: "The model declined this request. Please rephrase the offer description.",
    "generator_unavailable": lambda f: f"The rule generator is unavailable right now ({f['error']}). Please try again.",
    # a saved offer the history does not trust (core.tenant.harvest.vet)
    "no_saved_rules": lambda f: "no saved rules",
    "counter_saved_without_type": lambda f: f"counter {f['counter']!r} is saved without a data type",
    "counter_saved_with_types": lambda f: f"counter {f['counter']!r} is saved as {' and '.join(f['types'])}",
    "counter_without_name": lambda f: f"{f['at']}: uses a {f['level']} counter with no name",
    "saved_type_differs": lambda f: (
        f"{f['at']}: {f['attribute']} is {f['catalog_type']} in the catalog, saved as {f['saved']}"
    ),
    "field_empty": lambda f: f"{f['at']}: {', '.join(f['fields'])} is empty",
}


def word(problem: Problem) -> str:
    """A problem as the manager reads it: the manager's wording when it has one, else the model's."""
    say = FOR_THE_MANAGER.get(problem.code)
    return say(problem.data) if say else render.text(problem)


def public(result: Mapping[str, Any]) -> dict[str, Any]:
    """A run's result with every Problem worded for the manager."""
    return render.reply(result, word)
