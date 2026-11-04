"""What the model reads: the system prompt and its context, all generated or derived, none of it rule text.

  context/block_reference*.md  both toolboxes, generated from the gravty-ui source (tools/build/block_reference.py)
  context/kt.md                how offers and blocks run in the engine, as a manager knows it (written by hand: no
                               rule code, backend or UI source; tests/unit/test_agent.py guards it)
  context/conventions.md       usual readings of loyalty terms (spend, Nx, per X, period ends, caps)
  the tenant catalog           catalog_text()
  earlier offers of the tenant core.tenant.history, at most two like the request, in the request message as reference
No example offers and no recipes: the model derives the logic from these facts, so a new pattern needs nothing written
for it.
No compiled rules (DRL) exist in this service.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from textwrap import indent
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.tenant.catalog import AIRLINE_OBJECTS, INLINE_ACTIVITY_TYPES, INLINE_VALUES, OBJECTS
from ..core.tenant.existing_counters import normalize
from ..core.vocabulary import ACTIVITY_TYPE

if TYPE_CHECKING:
    from ..core.tenant.catalog import Catalog
    from ..core.tenant.history import PastOffer

CONTEXT = os.path.join(os.path.dirname(__file__), "context")

SYSTEM = """You build a Gravty loyalty REWARD offer from a loyalty manager's English description, for the stock
gravty-ui offer wizard. An offer has three parts, each where the engine evaluates it:

1. Offer Main (set_offer_main): name, texts, dates, per-member limit and frequency, total limit. The engine checks
   these before any rule runs; a BIT outside the dates or over the limit never reaches the rules.
2. Member segment (Select Members canvas, root targeting_holder): WHO the offer is for, only when the whole offer is
   limited to members by their own data (profile attributes, tier programme), e.g. "for Gold members". The engine
   runs it on member data only, before the offer; it never sees transactions. No such limit: no segment. A job that
   does not build the segment (and is not told one handles the limit) builds that limit in the rules instead, as a
   member condition in the On Activity filter: a stated limit is never left to a segment nobody builds.
3. Rules (Offer Rules canvas, root offer_generic): everything about the activity (type, amount, store, day/time,
   products), the rewards, caps and counters, plus member conditions that change the reward inside the offer
   ("Gold earn 3x, Silver 2x") or are OR-ed with activity conditions.
Every clause of the description lands in exactly one part; nothing is built twice or dropped.

You compose each canvas from its toolbox blocks as a block tree. A block holds its fields and inputs directly, by
the names the block reference lists:
  {"type": ..., <field>: value, ..., "picked": "CODE1,CODE2", <input>: [blocks] | block, ...}
- a field: the block's field value (dropdown option, typed value, catalog code in a field).
- picked: codes chosen in the block's lookup popup (the "saved picked codes" lines), comma-separated.
- an input: a statement input holds a list (blocks chained in order); a value input holds one block.
- A block or condition you need in several places (the same value or counter check in several rules) goes
  once in `defs` beside the tree, {name: block | [blocks]}, and is used by name: {"use": name}.
- The tools fill these fields when you leave them out, so leave them out unless you want something else: a
  condition's OBJECT and attribute_level (from its attribute: a declared counter at its level, an activity attribute,
  a member attribute at MA_P); update_attribute's mta_type_value (the counter's level); give_points' LOYALTY_ACCOUNT
  and REWARD_TAGS (the account the catalog names as filled when none is named, and its reward tag); the levels come
  from `counters`.
- counters: declare once, beside the tree, every counter the blocks refer to, offer (MA_O) and activity (MA_A) alike:
  {"name", "data_type" NUMBER|TEXT|DATE|BOOLEAN, "counter_type" REGULAR|VECTOR, "target_type"?, "namespace"? (VECTOR:
  what it counts per, e.g. h_location), "level" MA_O|MA_A}. The rules screen creates them on upload.
You never write the canvas file yourself: the tools write it from your fields, picked codes and counters as the
screen does; the manager uploads it in the stock screen, which makes it into the offer the engine runs.

Reference below: the blocks of both canvases (inputs, connection checks, fields, the screen's dropdown options,
shapes saved offers use), how offers and blocks run in the engine, the usual readings of loyalty terms and the tenant
catalog. A field marked "(tenant catalog)" is filled from the tenant's data: its codes are the ones in the tenant \
catalog block, nowhere else.

Work like this:
1. set_offer_main first when Offer Main is part of the job (it also records the segment and rules prompts the
   screens show, in the manager's own words).
2. Codes: use only codes from the tenant catalog. For values not listed, call lookup_values with the manager's words
   (one phrase per call; several calls per turn are fine) and use only codes it returns.
3. compose each canvas you build: the tools write it and check that every block fits where it is (the block
   reference), its values and codes (the tenant catalog) and its counters. Fix every problem it reports.
   To fix a tree you already sent, send only edits by block id (the ids the problems name) instead of the whole tree.
4. Before you compose, check the rules against how the engine runs them (the knowledge below): for each kind of
   transaction the description implies, including boundaries and repeats across transactions, the rules give what
   was asked, and every counter is read and updated where the knowledge says it is seen.
5. finish with the readback, assumptions, blocking_questions, unsupported, segment name and description. It ends the
   run when every canvas's last compose is clean.
Tools in one turn run in call order and each sees what the previous one did. Send compose and finish in the same
turn: a finish after a compose with problems is sent back with them, and you fix them in the next turn. Every turn
costs the manager waiting time.

Saved rules and single blocks:
- When the message shows the rules saved on this offer, a request to change, add to, keep or remove part of them is
  answered with edits on that canvas, by its block ids (insert_after / insert_before / append for new blocks): every
  block no edit names stays exactly as saved. Never re-send the saved blocks; a whole tree (blocks) replaces them,
  which is right only when the description is a whole offer of its own.
- A block the manager asks for by itself ("a static value block with 100", "an empty rule block", "an offer block")
  goes in `loose`, whatever its type, even where it could plug in and even when the saved rules already hold one like
  it: it is placed on the canvas, not attached and disabled, and the rules stay as they are. The block's own settings
  (its attribute, operator or value) do not place it; it is attached only when the manager names where it goes in the
  rules or asks for a change to what the offer does. Such a change that the saved rules already make: finish saying
  so, without a compose.
- Attach a block only where it can plug in (the block reference lists each input's check and each block's
  connections). Where it cannot, the tools leave it loose and the compose result says why: tell the manager in the
  readback, never force it into the rules.

Offer semantics:
- Every restriction the manager states must be built in the part the engine evaluates it in. A rules condition every
  rule needs belongs once in the On Activity filter (activity_criteria), not repeated per rule.
- Words the description leaves open read as the usual readings say; any other open word takes its plain reading,
  recorded in assumptions.
- Dates: only dates the manager stated (YYYY-MM-DD; the end date is the last day included); none stated is normal
  (the offer starts when created and has no end). Ask only for relative dates ("this weekend").
- blueprint_variable blocks are template placeholders: never emit them; put concrete values there.
- Loyalty accounts (the currency points are in), their reward tags and the privileges' usage policies are dropdowns
  like any other: their values are the tenant catalog's.
- When the message says a part is already handled elsewhere, build only what you are asked to build.
- Every assumption, blocking question and unsupported item names its part (offer_main, segment, rules): the part it
  is about, i.e. the screen where the manager acts on it. Each screen shows only its own part's items.
- assumptions: interpretations the manager should glance at. An assumption never drops or widens something the manager
  stated: a stated restriction or measure the toolbox or catalog cannot express goes in `unsupported`, with a reason.
- blocking_questions: ONLY when no sensible draft is possible without the answer, or when a phrase matches several
  codes (then ask even if the draft uses all of them). Still build everything else.
- Something that cannot be expressed on a single member's own data and transactions goes in `unsupported` with an
  alternative.
- Always end with finish. Do not answer in prose.
"""

HISTORY_RULE = """
Earlier offers, when the message shows them, are what this tenant built before for similar descriptions. They are
not part of the request. Use them only to choose among things the description leaves open: which attribute, counter
or tag this tenant uses for a concept. Never take a value from them (amount, multiplier, store, tier, date, limit,
privilege) that the description does not state. When the description and an earlier offer differ, the description
wins. Anything you took from an earlier offer goes in assumptions, naming the offer. An offer the manager refers to
by name ("like our Ramadan offer") is part of the request: follow it where the description does not say otherwise. A
name that appears only as ordinary words of the description ("in the birthday month") is not a reference.
"""


def system_text(history: bool = False) -> str:
    """SYSTEM as sent: with the rule for earlier offers when those may be shown."""
    return SYSTEM + HISTORY_RULE if history else SYSTEM


def static_prompt() -> str:
    """The knowledge every run shares: block reference, how blocks run, usual readings.

    Read per run (~5 ms), the same bytes each time, so it stays a prompt-cache hit.
    """
    return "\n\n".join(
        Path(CONTEXT, n).read_text()
        for n in ("block_reference.md", "block_reference_segment.md", "kt.md", "conventions.md")
    )


def system_blocks(
    catalog: Catalog,
    account_id: Any = None,
    ttl: str = "1h",
    history: bool = False,
    inline_types: int = INLINE_ACTIVITY_TYPES,
) -> list[dict[str, Any]]:
    """The system prompt: the instructions, the knowledge every tenant shares and this tenant's catalog, each cached.

    Args:
        catalog: The tenant catalog.
        account_id: The loyalty account points go to (default: the tenant's).
        ttl: The cache lifetime, "1h" or "5m" (the conversation's own sliding cache point is 5 minutes, after these).
        history: Earlier offers may be shown in the request.
        inline_types: Activity types are listed up to this many; more are looked up.

    Returns:
        The system content blocks.
    """
    mark = {"type": "ephemeral", **({"ttl": ttl} if ttl != "5m" else {})}
    return [
        {"type": "text", "text": system_text(history)},
        {"type": "text", "text": static_prompt(), "cache_control": mark},
        {
            "type": "text",
            "text": "Tenant catalog:\n" + catalog_text(catalog, account_id, inline_types),
            "cache_control": mark,
        },
    ]


MAX_TAGS_SHOWN = 12  # reward tags listed per account; the compose check names the closest when another is used


def _accounts_text(catalog: Catalog) -> list[str]:
    """The loyalty account dropdowns' values (give, take, return, reset and expire points; currency converter)."""
    if not catalog.loyalty_accounts:
        return []
    acc = catalog.account()
    head = (
        "\n## LOYALTY ACCOUNTS (the reward accounts this sponsor's offers can use: id | loyalty currency"
        " | currency type | sponsor-specific | reward tags)"
    )
    if acc:
        own = " (the sponsor's own account)" if acc.get("sponsor_specific") else " (the program's configured account)"
        head += f'\nThe account the tools fill when an action names none: {acc["id"]} "{acc["name"]}"{own}'
        tag = catalog.reward_tag(acc["id"])
        head += f", reward tag {tag}" if tag else ""
    lines = [head]
    for a in catalog.loyalty_accounts:
        tags = [t["code"] for t in a.get("tags") or []]
        shown = ", ".join(tags[:MAX_TAGS_SHOWN]) + (
            f" (+{len(tags) - MAX_TAGS_SHOWN} more)" if len(tags) > MAX_TAGS_SHOWN else ""
        )
        lines.append(
            f"{a['id']} | {a['name']} | {a.get('currency_type') or ''} | {'yes' if a.get('sponsor_specific') else 'no'}"
            f" | {shown or 'not read'}"
        )
    return lines


def intro(
    prompt: str,
    parts: tuple[str, ...] | list[str],
    context: str | None = None,
    existing: dict[str, Any] | None = None,
    current: dict[str, Any] | None = None,
    earlier: dict[str, Any] | None = None,
) -> str:
    """The run's first message: the manager's description, what to build, and what the offer already has.

    Args:
        prompt: The manager's description.
        parts: The parts to build (offer_main, segment, rules).
        context: What is handled outside the parts built (rules_context).
        existing: The offer's existing counters ({counters}).
        current: The rules saved on the offer ({root, loose, counters}).
        earlier: The tenant's earlier offers ({named, similar}, History.earlier).

    Returns:
        The message text.
    """
    names = {
        "offer_main": "Offer Main (set_offer_main)",
        "segment": "the member segment (segment canvas), if the offer is limited to certain members",
        "rules": "the rules (rules canvas)",
    }
    text = prompt + "\n\nBuild: " + "; ".join(names[p] for p in parts) + "."
    if tuple(parts) == ("offer_main",):
        text += (
            " The member segment and the rules are built after Offer Main, from its segment_prompt and rules_prompt."
        )
    elif tuple(parts) == ("rules",) and not context:
        text += (
            " Nothing else of this offer exists for this job: no Offer Main settings and no member segment. Build"
            ' every restriction the description states in the rules: a member limit ("for Gold members") as a'
            ' member condition in the On Activity filter, a per-member limit ("once", "up to 3 times") with an'
            " offer counter. Never build dates into a canvas; name stated dates and per-member limits in an"
            " offer_main assumption as well, so the manager can also set them on Offer Main."
        )
    else:
        if "offer_main" not in parts:
            text += (
                " Offer Main is set on its own screen: never build its dates or limits into a canvas; when the"
                " description states some that are not listed below, name them in an offer_main assumption."
            )
        if "segment" not in parts:
            text += (
                ' No segment is built in this job: a member limit the description states ("for Gold members") that'
                " is not listed below as already handled is built in the rules as a member condition, never dropped."
            )
    if context:
        text += f"\n\n{context}"
    if current:
        text += (
            "\n\nRules saved on this offer now (rules canvas; block ids for edits):\n"
            + _json(current["root"])
            + ("\nLoose blocks already on the canvas: " + _json(current["loose"]) if current["loose"] else "")
            + ("\nCounters these rules use: " + _json(current["counters"]) if current["counters"] else "")
            + "\nIf the description changes, adds to, keeps or removes part of these rules, compose with edits (and "
            "loose blocks) on this canvas. If it describes a whole offer of its own, compose a whole tree: it "
            "replaces these rules."
        )
    if existing and existing.get("counters"):
        text += (
            "\n\nCounters already on this offer (reuse one only at its own level and type; a name is unique "
            "across levels): "
            + "; ".join(
                f"{c['name']} ({c['level']} {c['data_type']} {c['counter_type']}"
                + (f" per {c['namespace']})" if c.get("namespace") else ")")
                for c in normalize(existing["counters"])
            )
        )
    if earlier and (earlier["named"] or earlier["similar"]):
        text += "\n\n" + earlier_offers(earlier)
    return text


SUBPROMPT_FORM = """Earlier offers show the manager's prompt and the offer's blocks in Pulsar's sub-prompt form,
one line per block in canvas order: a label, then the block. The label says where the block sits. "Filter" lines are
conditions of the On Activity filter (activity_criteria, written "offer_criteria <object> <attribute> <operator>
<value>"). "rule_N" lines belong to rule N (rule_line_N: a Line Rule, payment_rule_N: a Payment Rule); "rule_N inside
rule_M" is a rule placed among rule M's actions, checked only when rule M's conditions held. When the offer has more
than one On Activity or On Event, the label starts with it ("activity_2", "event_1"). A condition line is an "if"
(if_generic_adaptive; "if_payment" is if_payment_attribute). The conditions of one rule or filter must all hold,
except those labelled with an OR: "OR_K branch 1" and "OR_K branch 2" are the two sides of one OR block, which holds
when every condition of either side holds (a further "OR_J branch ..." after it is an OR inside that side). The other
lines are actions: "give_points <loyalty account> <reward tag> <formula>", "give_privilege <privilege id> ...
previlege_Validity <validity> <quantity>", "update_attribute <level> <attribute> <operator> <value>". A member
attribute is "member <level> <attribute>": program_level_tracker is MA_P, offer_level_tracker an offer counter (MA_O),
activity_level_tracker an activity counter (MA_A); a counter is quoted as "<data type> <name>".
"activity_attribute->x" is the activity's attribute x; brackets are a formula. Rule names are not shown."""


def earlier_offers(earlier: dict[str, Any]) -> str:
    """The tenant's earlier offers for this request, after the note on the sub-prompt form.

    Each with its name, date, similarity, prompt and sub-prompt lines; named by the manager first (part of the
    request), then the similar ones (reference only).
    """

    def record(i: int, score: float | None, e: PastOffer) -> str:
        meta = ", ".join(
            x for x in (e.launched and f"launched {e.launched}", score is not None and f"similarity {score:.2f}") if x
        )
        return (
            f'{i}. "{e.name}"'
            + (f" ({meta})" if meta else "")
            + f"\n   Prompt: {e.text}\n   Sub-prompt:\n"
            + indent(e.subprompt, "   ")
        )

    sections = (
        (
            (
                "Earlier offers whose names appear in the description (part of the request if the manager refers to "
                "that offer, where the description does not say otherwise):"
            ),
            [(None, e) for e in earlier["named"]],
        ),
        (
            "Earlier offers of this tenant that look similar (reference only, not part of the request):",
            earlier["similar"],
        ),
    )
    return "\n\n".join(
        [SUBPROMPT_FORM]
        + [title + "\n" + "\n".join(record(i, *x) for i, x in enumerate(rows, 1)) for title, rows in sections if rows]
    )


def _json(value: Any) -> str:
    """Compact JSON."""
    return json.dumps(value, separators=(",", ":"))


def _offer_main_settings(om: dict[str, Any]) -> list[str]:
    """Offer Main's dates and limits, as the run is told them."""
    settings = [f"{k}={om[k]}" for k in ("start_date", "end_date") if om.get(k)]
    if om.get("max_usage_per_user"):
        settings.append(f"max {om['max_usage_per_user']} per member ({om.get('frequency')})")
    if om.get("num_of_clients"):
        settings.append(f"max {om['num_of_clients']} in total ({om.get('offer_frequency_type') or 'lifetime'})")
    return settings


def offer_main_context(om: dict[str, Any]) -> str:
    """Told to the build run of a whole-offer job: the Offer Main its own run set, and the parts it left to this run."""
    return (
        "Offer Main is already set for this offer (not built in a canvas):\n"
        f"- Settings: {', '.join(_offer_main_settings(om)) or 'none'}\n"
        f"- Who the offer is for (Offer Main's segment_prompt): {om.get('segment_prompt') or 'every member'}\n"
        f"- The rules part (Offer Main's rules_prompt): {om.get('rules_prompt') or 'none'}"
    )


def rules_context(om: dict[str, Any], segment: dict[str, Any] | None = None) -> str:
    """Told to a rules-only run whose offer already has Offer Main (and maybe a segment): what is handled there."""
    settings = _offer_main_settings(om)
    who = (segment or {}).get("description") or om.get("segment_prompt") or "none, every member"
    return (
        "Already handled outside the rules (do not build rules for these, do not repeat them as conditions):\n"
        f"- Offer Main settings: {', '.join(settings) or 'none'}\n- Member segment (who qualifies): {who}"
    )


def _airports_text(catalog: Catalog) -> list[str]:
    """The program's airports and airport pairs, and the header fields a transaction names its airports by."""
    airports, pairs = catalog.reference.get("airports") or {}, catalog.reference.get("airport_pairs") or {}
    if not airports:
        return []
    out = [
        "\n## The program's airports (a transaction names its origin and destination airports by code in the header "
        "fields h_origin_airport and h_destination_airport; the airport and airport pair attributes are read from "
        "them)",
        ", ".join(airports) if len(airports) <= INLINE_VALUES else f"{len(airports)} airports, not listed",
    ]
    if pairs:
        out.append("Airport pairs (origin-destination | distance):")
        out += [f"{k} | {v.get('distance')}" for k, v in list(pairs.items())[:INLINE_VALUES]]
        if len(pairs) > INLINE_VALUES:
            out.append(f"and {len(pairs) - INLINE_VALUES} more pairs")
    return out


def _today(catalog: Catalog) -> str:
    """The catalog's date (fixtures pin it), else today's date in the program's time zone."""
    if catalog.today:
        return catalog.today
    try:
        tz = ZoneInfo(catalog.timezone) if catalog.timezone else None
    except (ZoneInfoNotFoundError, ValueError):
        tz = None
    return datetime.now(tz).strftime("%Y-%m-%d")


def catalog_text(catalog: Catalog, account_id: Any = None, inline_types: int = INLINE_ACTIVITY_TYPES) -> str:
    """The tenant catalog as the model reads it.

    Attributes per object (code, label, type, short value lists), activity types, tiers, the loyalty account and the
    privileges.
    """
    out = [
        f"Tenant: {catalog.tenant}  sponsor: {catalog.sponsor_id}"
        + f"  today: {_today(catalog)}"
        + (f"  program time zone: {catalog.timezone}" if catalog.timezone else "")
        + (f"  point decimal scale: {catalog.point_decimal_scale}" if catalog.point_decimal_scale is not None else "")
    ]
    names = {
        "activity": "ACTIVITY (transaction) attributes, l_* are line-item attributes",
        "member": "MEMBER attributes",
        "product": "PRODUCT attributes (use with line_item / collect_all)",
        "airport": "AIRPORT attributes (OBJECT airport; airporttypes origin / destination: the BIT's airport)",
        "airport_pair": "AIRPORT PAIR attributes (OBJECT airport_pair: the BIT's origin to destination pair)",
    }
    for obj in (*OBJECTS, *(o for o in AIRLINE_OBJECTS if catalog.attributes.get(o))):
        out.append(f"\n## {names[obj]}  (code | label | type [| values])")
        for a in catalog.attributes.get(obj, {}).values():
            line = f"{a.code} | {a.label} | {a.type}" + (" (payment)" if a.payment_level else "")
            if a.values and (obj, a.code) != ("activity", ACTIVITY_TYPE):  # activity types are listed once, below
                if len(a.values) <= INLINE_VALUES:
                    line += " | " + ", ".join(f"{k}={v}" for k, v in a.values.items())
                else:
                    line += f" | {len(a.values)} values, not listed: find codes with lookup_values"
            out.append(line)
    out += _airports_text(catalog)
    if len(catalog.activity_types) <= inline_types:
        out.append(
            "\n## Activity types (h_bit_type values)\n"
            + ", ".join(f"{k}={v}" for k, v in catalog.activity_types.items())
        )
    else:
        out.append(
            f"\n## Activity types (h_bit_type values): {len(catalog.activity_types)}, not listed: find codes with "
            "lookup_values (activity, h_bit_type)"
        )
    if catalog.tiers:
        out.append("\n## TIER PROGRAMME (MA_T: tier class | tiers the member can hold, by rank, lowest first)")
        out += [
            f"{c} | " + ", ".join(k if k == v else f"{k}={v}" for k, v in t.items()) for c, t in catalog.tiers.items()
        ]
    out += _accounts_text(catalog)
    out.append("\n## Privileges (privilege_id | name | usage policies: id=name, the first is the screen's default)")
    out += [
        f"{pid} | {p['name']} | "
        + ", ".join(
            f"{u['id']}={u['name']}"
            for u in p.get("usage_policies") or [{"id": p.get("usage_policy"), "name": "default"}]
        )
        for pid, p in catalog.privileges.items()
    ]
    return "\n".join(out)
