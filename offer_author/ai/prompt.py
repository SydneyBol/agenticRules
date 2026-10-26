"""What the model reads: the system prompt and its context, all generated or derived, none of it rule text.

  context/block_reference*.md  both toolboxes, generated from the gravty-ui source (tools/build/block_reference.py)
  context/kt.md                how the engine executes offers and blocks (tools/build/kt_prompt.py; DRL scrubbed)
  core/data/blueprints, segments  the product's saved offers and segments, shown as block selections
  the tenant catalog           catalog.prompt_text()
The compiled rules (DRL) are private: never part of anything here.
"""
import glob
import json
import os

from ..core.counters import normalize
from ..core.session import DATA
from ..core.tree import compact, selections, share

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
- A block or condition you need in several places (a period index, the same counter check in several rules) goes
  once in `defs` beside the tree, {name: block | [blocks]}, and is used by name: {"use": name}.
- The tools fill these fields when you leave them out, so leave them out unless you want something else: a
  condition's OBJECT and attribute_level (from its attribute: a declared counter at its level, an activity attribute,
  a member attribute at MA_P); update_attribute's mta_type_value (the counter's level); give_points' LOYALTY_ACCOUNT
  and REWARD_TAGS (the tenant's account and reward tag below); the levels come from `counters`.
- counters: declare once, beside the tree, every counter the blocks refer to, offer (MA_O) and activity (MA_A) alike:
  {"name", "data_type" NUMBER|TEXT|DATE|BOOLEAN, "counter_type" REGULAR|VECTOR, "target_type"?, "namespace"? (VECTOR:
  what it counts per, e.g. h_location), "level" MA_O|MA_A}. The rules screen creates them on upload.
You never write XML or mutation attributes: the tools derive them from your fields, picked codes and counters as the
UI does, and the stock screen compiles them into the rules the engine runs.

Reference below: the block reference of both canvases (generated from the UI source: inputs, connection checks,
fields, the dropdown options the UI itself defines, shapes saved blueprints use), how the engine executes offers and
blocks (KT), the product's seeded blueprints and saved segments (as block trees) and the tenant catalog. A field marked
"(tenant catalog)" is filled from the tenant's data: its codes are the ones in the tenant catalog block, nowhere else.

Work like this:
1. set_offer_main first when Offer Main is part of the job (it also records the segment and rules prompts the
   screens show, in the manager's own words).
2. Codes: use only codes from the tenant catalog. For values not listed, call lookup_values with the manager's words
   (one phrase per call; several calls per turn are fine) and use only codes it returns.
3. compile each canvas you build. It runs the stock screen: fix every problem it reports (a dropped or changed field
   means the screen did not accept that value or shape). validate is a faster offline check of the same tree.
   To fix a tree you already sent, send only edits by block id (the ids the problems name) instead of the whole tree.
4. simulate with transactions that exercise every rule: qualifying and non-qualifying ones, boundaries, repeats for
   anything that counts across transactions, BITs outside the offer dates, members in and out of the segment, and
   the expected outcome of each. This is how you confirm each rule does what was asked. If the engine disagrees,
   decide whether the blocks or your expectation is wrong, fix, compile and simulate again.
5. Give the finish fields (readback, assumptions, blocking_questions, unsupported, segment name and description)
   with that simulate. When every canvas's last compile is clean and every BIT's expect matches, that ends the run.
   Call finish only to end without such a simulate (e.g. when you can only ask or report something unsupported).
Tools in one turn run in call order and each sees what the previous one did. Once you know the blocks and the
transactions that test them, send compile and simulate (with the finish fields) in that same turn: every turn costs
the manager waiting time. If the compile had problems or the engine disagreed, the run goes on: fix and send them
again.

Offer semantics:
- Every restriction the manager states must be built in the part the engine evaluates it in. A rules condition every
  rule needs belongs once in the On Activity filter (activity_criteria), not repeated per rule.
- "spend", "spends", "spending" mean the activity type labelled exactly "Spend"; use another type only when the
  manager names it. No activity named: use Spend and record that as an assumption.
- State that must survive from one transaction to the next (counts, totals, "once", caps, unlocks, distinct values)
  lives in offer counters (MA_O); MA_A values do not persist across transactions.
- Dates: only dates the manager stated (YYYY-MM-DD; the end date is the last day included); none stated is normal
  (the offer starts when created and has no end). Ask only for relative dates ("this weekend").
- "Nx points" means points = measure x N in total; record that as an assumption. "over X" is strict; "at least X" is >=.
- DAY_OF_WEEK numbers: Mon=1 ... Sun=7. HOUR_OF_DAY is 0-23.
- blueprint_variable blocks in the blueprints are template placeholders: never emit them; put concrete values there.
- Points go to the loyalty account and reward tag the catalog names.
- When the message says a part is already handled elsewhere, build only what you are asked to build.
- Every assumption, blocking question and unsupported item names its part (offer_main, segment, rules): the part it
  is about, i.e. the screen where the manager acts on it. Each screen shows only its own part's items.
- assumptions: interpretations the manager should glance at. An assumption never drops or widens something the manager
  stated: a stated restriction or measure the toolbox or catalog cannot express goes in `unsupported`, with a reason.
- blocking_questions: ONLY when no sensible draft is possible without the answer, or when a phrase matches several
  codes (then ask even if the draft uses all of them). Still build everything else.
- Something that cannot be expressed on a single member's own data and transactions goes in `unsupported` with an
  alternative.
- Always end with a confirming simulate or finish. Do not answer in prose.
"""


def examples(folder, title):
    """Saved XML as the selections the model writes; identical trees shown once. Never their compiled rules."""
    out, seen = [], set()
    for f in sorted(glob.glob(os.path.join(DATA, folder, "*.xml"))):
        tree, counters = selections(open(f).read())
        tree, defs = share(compact(tree, counters))
        text = json.dumps(tree, separators=(",", ":")) + (f"\nDefs: {json.dumps(defs, separators=(',', ':'))}" if defs else "")
        if text in seen:
            continue
        seen.add(text)
        out.append(f"### {os.path.basename(f)[:-4].replace('_', ' ')}\nBlocks:\n{text}\n" +
                   (f"Counters: {json.dumps(counters, separators=(',', ':'))}\n" if counters else ""))
    return f"# {title}\n\n" + "\n".join(out)


def static_prompt():
    """The reference text every run shares (~20 ms to read and render): read per run, the same bytes each time, so
    it stays a prompt-cache hit."""
    read = lambda n: open(os.path.join(CONTEXT, n)).read()  # noqa: E731
    return "\n\n".join([read("block_reference.md"), read("block_reference_segment.md"), read("kt.md"), read("member_dates.md"),
                        examples("blueprints", "Seeded blueprints (rules canvas)"),
                        examples("segments", "Saved member segments (segment canvas)")])



def system_blocks(catalog, account_id=None):
    return [{"type": "text", "text": SYSTEM},
            {"type": "text", "text": static_prompt(), "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Tenant catalog:\n" + catalog.prompt_text(account_id) + _account_line(catalog, account_id),
             "cache_control": {"type": "ephemeral"}}]



def _account_line(catalog, account_id):
    acc = catalog.account(account_id)
    tags = [t["code"] for t in (acc or {}).get("tags") or []]
    tag = catalog.reward_tag(account_id)
    return f"\nReward tag for points: {tag}" + (f" (tags on this account: {', '.join(tags)})" if tags else "") if acc else ""



def intro(prompt, parts, context=None, existing=None):
    names = {"offer_main": "Offer Main (set_offer_main)", "segment": "the member segment (segment canvas), if the "
             "offer is limited to certain members", "rules": "the rules (rules canvas)"}
    text = prompt + "\n\nBuild: " + "; ".join(names[p] for p in parts) + "."
    if tuple(parts) == ("rules",) and not context:
        text += (" Nothing else of this offer exists for this job: no Offer Main settings and no member segment. Build"
                 " every restriction the description states in the rules: a member limit (\"for Gold members\") as a"
                 " member condition in the On Activity filter, a per-member limit (\"once\", \"up to 3 times\") with an"
                 " offer counter. Never build dates into a canvas; name stated dates and per-member limits in an"
                 " offer_main assumption as well, so the manager can also set them on Offer Main.")
    else:
        if "offer_main" not in parts:
            text += (" Offer Main is set on its own screen: never build its dates or limits into a canvas; when the"
                     " description states some that are not listed below, name them in an offer_main assumption.")
        if "segment" not in parts:
            text += (" No segment is built in this job: a member limit the description states (\"for Gold members\") that"
                     " is not listed below as already handled is built in the rules as a member condition, never dropped.")
    if context:
        text += f"\n\n{context}"
    if existing and existing.get("counters"):
        text += ("\n\nCounters already on this offer (reuse one only at its own level and type; a name is unique "
                 "across levels): " + "; ".join(f"{c['name']} ({c['level']} {c['data_type']} {c['counter_type']}"
                                                + (f" per {c['namespace']})" if c.get("namespace") else ")")
                                                for c in normalize(existing["counters"])))
    return text



def rules_context(om, segment=None):
    """Told to a rules-only run whose offer already has Offer Main (and maybe a segment): what is handled there."""
    settings = [f"{k}={om[k]}" for k in ("start_date", "end_date") if om.get(k)]
    if om.get("max_usage_per_user"):
        settings.append(f"max {om['max_usage_per_user']} per member ({om.get('frequency')})")
    if om.get("num_of_clients"):
        settings.append(f"max {om['num_of_clients']} in total ({om.get('offer_frequency_type') or 'lifetime'})")
    who = (segment or {}).get("description") or om.get("segment_prompt") or "none, every member"
    return ("Already handled outside the rules (do not build rules for these, do not repeat them as conditions):\n"
            f"- Offer Main settings: {', '.join(settings) or 'none'}\n- Member segment (who qualifies): {who}")

