# The rules canvas as lines (`dsl`)

In this run the RULES canvas is written as lines, not as a block tree: compose with `{"canvas": "rules", "dsl":
"<all the lines>"}`. Do not send `blocks`, `defs`, `counters` or `edits` for the rules canvas. The tools turn the
lines into the same blocks the reference above describes and check them the same way; everything said above about
what each block does, where conditions belong and how the engine runs rules holds unchanged. The SEGMENT canvas is
still sent as `blocks`.

One statement per line: `<label>, <body>`. The lines read top to bottom as the offer's logic. Each compose carries
the whole canvas; to fix a problem, send all the lines again with the line corrected. A problem names its line:
`#L7` is line 7, `#L7.2` a value inside line 7, `#rule_2` the rule with that label.

## Labels
- `counter, "<name>" number|text|date` declares a counter of the offer (an offer counter, kept per member).
  `... per <attribute> of Activity` makes it a VECTOR counter counted per that attribute; `... transaction_only`
  makes it an activity-level value (one transaction only). Counter lines come first.
- `def, <name>, <value>` writes a value once; use it as `$<name>` in the lines after it.
- `Filter, <condition>` is a criterion of the activity (On Activity filter). Filters come before the rules.
- `rule_N "<rule name>", ...`, `rule_line_N ...` (a rule per line item), `payment_rule_N ...` (a rule per payment
  item): N counts up across the offer. A rule's first line carries its name; every line with the same label belongs
  to that rule: its `if` lines are ANDed, its other lines are its actions, in order.
- `rule_3 "<name>" inside rule_2, ...` puts rule_3 among rule_2's actions (it runs only when rule_2's conditions hold).
- `rule_1 or1.1, if ...` and `rule_1 or1.2, if ...` are the two branches of OR group 1 of that rule; several lines in
  one branch are ANDed; `or1.1 or2.1` nests group 2 inside branch 1. Filters take OR branches the same way.
- `activity_2,` starts a second activity: the lines after it are its Filters and rules. `event_1,` starts an On
  Event section.

## Conditions: `if <subject> <attribute> <operator> <value>` (a Filter has no `if`)
Subjects:
- `activity` (the transaction), `sponsor`, `location` (the transaction's store), `airport origin|destination`,
  `product` (a line item's product), `payment` (a payment item's attribute, in a payment_rule).
- `profile` a member attribute of the attribute library; `analytics` an analytics write-back value.
- `counter "<name>"` an offer counter (`counter "<name>" at <attribute>` reads a VECTOR counter's entry for the
  transaction's value of that attribute); `transaction_value "<name>"` an activity-level value.
- `card <card> <attribute>` a co-branded card's attribute, `card <card> transaction <attribute>` of the card used in
  the transaction, `card <card> any <attribute>` of any of the member's cards of that kind; `card active == <card>`
  (or `in [..]`) whether the member holds the card.
- `bank_account any|transaction <attribute>`, `subscription <plan> <attribute>`, `partner <programme> <attribute>`.
- `tier <tier programme> == <tier>`.
- `third_party <who> profile <attribute>` a third party's member attribute.
A date's part is written after the attribute: `h_bit_date.DAY_OF_WEEK`, `.DATE`, `.MONTH`, `.YEAR`, `.DAY_OF_MONTH`,
`.DAY_OF_YEAR`, `.WEEK_OF_YEAR`, `.HOUR_OF_DAY`.
Operators: `== != > >= < <= in not_in between null not_null match not_match`.
Values: a catalog code or its name (`SPEND` or `Spend`; a name with spaces in quotes: `"HDFC Sliver"`); a list
`in [A, B]`; a number; a date `2026-03-08`; `between 50 and 99.99`; any other value in parentheses. `null` and
`not_null` take no value. Cards, plans and programmes are named by their id or their name from the tenant catalog.

## Values
A number, `"text"`, a date, `activity.<attribute>` (with a part: `activity.h_bit_date.MONTH`),
`profile.<attribute>`, `counter."<name>"`, `transaction_value."<name>"`, `product.<attribute>`,
`payment.<attribute>`, `today`, `today.MONTH`, arithmetic in parentheses with one operator per pair
`((activity.h_bit_amount - 100) * 2)` (operators `+ - * / %`), a date moved by a number of units
`(2026-03-08 + 7 DAYS)` (`DAYS`, `MONTHS`, `YEARS`).

## Actions
- `give_points <value>`: points to the program's account and reward tag; another account or tag is named
  `give_points account=<id> tag=<tag> <value>`. `pending_days=<n>` holds them pending.
- `update_attribute "<counter>" INCREMENT_BY|DECREMENT_BY|SET <value>`, `update_attribute "<counter>" RESET`;
  a VECTOR counter's entry: `update_attribute "<counter>" at <attribute> INCREMENT_BY 1`.
- `update_attribute profile <attribute> = <code>` sets a member attribute.
- `give_privilege privilege=<id> policy=<usage policy id> validity=product_validity quantity=<value>`.
- `for <third party> give_points ...` gives to a third party.
- Any other action of the reference: `<action_type> <field>=<value> ...` with the reference's own field names.
A block no form above covers can be written whole: `block <type>(<field>=<value>, <input>=(<value>))`.

The tools fill what the lines leave out, as they do for a tree: each block's data type and level from the tenant
catalog and the counter lines, the program's account and tag, and the fields the screen keeps a card, plan or tier
condition in.
