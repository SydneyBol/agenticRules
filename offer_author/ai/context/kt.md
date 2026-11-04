# How offers and blocks run in the engine

What a loyalty manager who builds offers in the Offer Rules screen knows about how the engine runs them: block by
block, in the order the engine meets them. The screen compiles the canvas; how it does that is not part of this.

## 1. When an offer runs for a transaction (BIT)

A BIT is one member transaction sent to the program: a header (activity type `h_bit_type`, category
`h_bit_category`, amount `h_bit_amount`, date `h_bit_date`, store `h_location`, sponsor, ...), optional line items
(one per product, `l_*` attributes) and optional payment items.

For each BIT the engine, in this order:
1. Rejects the BIT before any offer when it is invalid: an unknown store code (LOCATION_NOT_FOUND), a duplicate of a
   BIT already processed, a member whose membership does not allow it.
2. Finds the offers that apply: launched offers (test offers only for test members), for the member's segment, at a
   location the offer allows, opted into when the offer needs opt-in, whose dates contain the BIT date (program
   timezone), whose per-member limit and frequency (per day, week, month or lifetime; there is no yearly
   frequency: a lifetime limit counts the member's rewards over the whole life of the offer and never starts over at a
   new year)
   and total limit are not used up. Offer Main holds one per-member limit with its one frequency, and one total
   limit with its frequency; a per-member limit for a second period is not an Offer Main setting. A BIT before the offer's start date is refused for that offer
   (REWARD_OFFER_DOES_NOT_QUALIFY). These are Offer Main and segment settings: they are checked before any block of
   the rules runs, and a BIT they stop never reaches the rules.
3. Runs the rules of each offer that applies, as described below. Offers that are not cumulative are exclusive: at
   most one of them rewards a BIT's header, and at most one rewards each line item's product.
4. Saves the outcome: points credited, privileges issued, counters updated, each action labelled with the name of the
   rule that fired it.

Reward offer rules run for accrual BITs (and event BITs, through On Event). A cancellation BIT reverses the original
BIT's rewards and counters. Availment, payment, service, sweepstake, play & win and subscription BITs run no offer
rules.

## 2. The canvas

```
Offer (offer_generic)
├── activities: On Activity (activity), one or more
│   ├── Filter (activity_criteria) and OR blocks: which BITs this activity takes
│   └── Rules: Rule (rule), Line Rule (rule_line), Payment Rule (payment_rule), in order
│       ├── Condition: If blocks (if_generic_adaptive) and OR blocks
│       └── Action: Action blocks (action) and Rule blocks (a rule inside a rule)
└── events: On Event (event), one or more
    └── Rules: Rule ...
```

## 3. How each block runs

**On Activity.** Runs for every BIT that reaches the offer. Its Filter decides whether the BIT is taken: every Filter
condition must hold. When it does not, nothing under this activity runs for that BIT. When it does, the activity's
rules run, top to bottom.

**Filter (and OR inside the filter).** Filter conditions chained one under another must all hold (AND). An OR block
holds when either of its two branches holds; each branch may hold several chained conditions (AND inside the branch).
A Filter condition applies to every rule under its activity. The standard: a condition that every rule under the
activity needs is written once, in the Filter; a rule holds only the conditions that make it different from the other
rules (moving a shared condition into the Filter does not change what the offer does).

**Several On Activity blocks in one offer.** Each takes BITs by its own Filter and runs its own rules. The engine
promises no order between two On Activity blocks of one offer: one activity may run before or after the other in the
same BIT, so whether it reads the other's counter updates of that BIT is not defined.

**Rule.** Its conditions (If blocks chained one under another = AND; an OR block = either branch) are checked once per
BIT, at header level; when they hold, its actions run once, top to bottom. A rule with no condition always runs.
There is no "else": every rule under the activity is checked, top to bottom, and every rule whose conditions hold
runs. Two rules that both hold both reward (they stack).

**A rule inside a rule** (a Rule block in another rule's actions). Checked only when the outer rule's conditions held,
at its place among the outer rule's actions.

**Line Rule.** Runs once per line item of the BIT: its conditions and actions see that line's attributes (`l_*`) and
the attributes of the line's product; its actions run for each line whose conditions hold (points per qualifying
product, counting products of a category).

**Payment Rule.** Runs once per payment item of the BIT, on that payment's attributes.

**On Event.** Runs for event BITs (activity type Event), with the same rule blocks.

**Member segment (Select Members canvas).** Decides which members the offer is for, from member data only, before
any rule. The conditions under Select (`targeting_holder`) must all hold (AND); an OR block holds when either side
does, and both sides need a condition (each side's conditions are ANDed). A condition reads a member attribute at
its `attr_level`:
- `MA_P`, the member profile: `tier_class`, `gender`, `date_of_joining`, the calculated `age`, account balances
  (`<account id>_balance`), ... A coded attribute takes the tenant's codes.
- `MA_T`, the tier programme: `tierslevel` is the tier class, `tiermainoption` what is tested (`name`, with the tier
  in `tierlevelnamevalue`; `tier_start_date` or `tier_validity`, compared with a date).
- `MA_CC`, `MA_SP`, ...: cobranded card, subscription and the other levels of the member attribute table.

A segment condition (`targeting_condition`) holds `attr_level`, `attribute`, `operators` and the right-hand side, a
value block in `RHS_INPUT`, and for `between` a second one in `RHS_VALUE`:
- A number (`age`, a balance): a static value block. A coded attribute (`gender`): its code in `value`, or the codes of
  `in` / `not in` in `picked`.
- A date (`date_of_joining`, `date_of_birth`): its part in `date_operator` (DATE, CALENDAR_DATE, YEAR, MONTH,
  DAY_OF_MONTH, ...; a date-time attribute in `datetime_operator`). DATE and CALENDAR_DATE compare with a static date
  or a date arithmetic block, the other parts with a static value. CALENDAR_DATE is the day and month in any year.
  The current date block is read in the program timezone when the segment runs, and date arithmetic can move it by
  DAYS, MONTHS or YEARS. The segment is checked when each transaction is processed:
  its current date is that moment, and it cannot read the transaction's own date. A program sends a transaction as
  it happens, so that moment is the transaction's own day; only one sent later (a retro claim, a late file) is
  processed after its date.
- The comparison reads the member's value against the right-hand side (`date_of_joining >= 2026-01-01`: joined on or
  after 1 Jan). In a segment `between` includes both ends (unlike the rules canvas).
- The tier programme (`attr_level` MA_T): `tierslevel` = the tier class, `tiermainoption` = `name` (the tier code in
  `tierlevelnamevalue`), `tier_start_date` (`datetime_operator` DATE, compared with a date block) or `tier_validity`
  ("Tier Validity (in months)", compared with a static value).
- The block's other fields (`RhsSelectedOption`, `customBlockOption`, `customdateopt1`, `datevalue`,
  `customdatedropdownopts2`, `rhsdateoptionselected`, `segment_rhs_attribute`, the card, subscription and partner
  fields) are the screen's other ways of filling the right-hand side or other attribute levels; a segment built with
  the value blocks above needs none of them.

The tier a member is in can be read both ways: `tier_class` (MA_P) and the tier programme (MA_T) are separate data
on a tenant. A member without the attribute reads as section 4 says (a calculated
attribute 0, a date part -1).

**Actions.** Run top to bottom, each when it is reached.
- Reward Points: credits the computed quantity to the loyalty account under the reward tag. The quantity is any value
  block: a number, an attribute, arithmetic on them (`h_bit_amount * 2`).
- Assign Privilege: issues the privilege product (its usage policy, how many, validity dates).
- Update Attribute: changes a counter (section 5), at once.
- Redeem / Reset / Return / Expire Points: debit, zero, return or expire points.
- Assign Sweepstakes, Assign Play & Win Reward: issue those rewards.

**Third-party conditions.** A condition on a third party the member does not have (family head, referrer, companion,
employee, corporate, institution, transferee) skips that rule; the BIT is not failed.

**Filling the blocks.** The block reference lists every field; these carry the logic, and the screen keeps a value
only in the field it belongs to:
- A condition (If, Filter): `OBJECT` (activity, member, products_attrs, location, sponsor, ...), `attribute` (the
  attribute's code, or the counter's name), `attribute_level` for a member attribute or counter (the tools fill it for
  catalog attributes and declared counters), `operators`, then the right-hand side. A coded (lookup / list)
  attribute takes one code in `value`, or the codes of `in` / `not in` in `picked`. A number, a date or a date-time
  has no `value` field: what it is compared with is a value block in `RHS_INPUT` (a number block for `h_bit_amount >
  100`). A TEXT counter is compared only with text typed in `value` (operators `==`, `!=`, `contains`,
  `not_contains`, `starts_with`, `ends_with`, `in`, `not_in`, `null`, `not_null`): no operator compares it with an
  attribute or any other block. A text, lookup or list attribute takes typed text or a code in `value` for `==` / `!=`,
  and a value block in `RHS_INPUT` (an attribute block, ...) for `match` / `not_match`.
- Update Attribute on a TEXT counter has no operator dropdown: it sets the counter to the value block in
  `RHS_INPUT`.
- A date part (the condition's, or an attribute block's): a DATE attribute takes it in `date_operator`, a DATE-TIME
  attribute in `datetime_operator`; HOUR_OF_DAY and MINUTE_OF_HOUR exist for date-times only. The part is then compared
  with a number block in `RHS_INPUT` (Sunday: `datetime_operator` DAY_OF_WEEK, `==`, the number 7); DATE compares with
  a date block (static date, current date, date attribute or date arithmetic). `h_bit_date` is a DATE-TIME attribute.
- A date attribute block used as a value (a member or activity attribute block holding a date, alone or inside date
  arithmetic) also takes its part: `date_operator` DATE (a DATE-TIME: `datetime_operator` DATE) for the whole date.
  Left empty, the screen reads the block as CALENDAR_DATE, the day and month in a fixed year, which a BIT date never
  equals or passes the way a dated comparison means.
- `between`: the first value block in `RHS_INPUT`, the second in `RHS_VALUE`.
- Reward Points: `action_type` give_points; `FORMULA` = the quantity (a value block); `LOYALTY_ACCOUNT` = the loyalty
  account credited (an account id from the catalog); `REWARD_TAGS` = one of that account's reward tags.
- Assign Privilege: `action_type` give_privilege; `SELECTED_PRIVILEGE` (a privilege id from the catalog),
  `PRIVILEGE_USAGEPOLICY` (its usage policy), `PRIVILEGE_QUANTITY` (how many, a value block).
- Update Attribute: `action_type` update_attribute; `mta_attribute` = the counter's name (declared in counters);
  `action_operator` = INCREMENT_BY, DECREMENT_BY, SET or RESET (RESET_ALL for a vector counter); `RHS_INPUT_VALUE` =
  the value block (none for RESET). `mta_type_value` is the counter's level (MA_O, MA_A): the tools fill it from the
  declaration. Without `mta_attribute` the screen has no counter for the action and keeps none of its operator.

## 4. Conditions and values

**What a condition can test** (the OBJECT dropdown): BIT (activity) attributes, member attributes (by level, below),
product attributes (in a Line Rule), location, sponsor, airport and airport pair, payment attributes (Payment Rule),
third parties.

**Member attribute levels:**

| Level | What it is |
|---|---|
| `MA_P` | Program-level member profile: tier, dates of birth and joining, gender, ... |
| `MA_O` | Offer counter: belongs to this member and this offer, kept from BIT to BIT |
| `MA_A` | Activity counter: lives for one BIT only, starts empty on every BIT |
| `MA_T` / `MA_TO` | Tier attributes / tier counters |
| `MA_S`, `MP_S`, `MR_S` | Analytics, personalization and retention scores written back to the member |
| `MA_CA`, `MA_CC`, `MA_SP` | Bank account, cobranded card, subscription |

Calculated member attributes are computed when read, from the current date (not the BIT date): `age` (whole years
since `date_of_birth`), `membership_tenure` (days since `date_of_joining`), `days_since_bit` (days since the last
activity), `email_domain`. A member without the source attribute reads 0. A calculated attribute is never stored on
the member: it comes from its source attribute.

**Operators:**

| Values | Operators |
|---|---|
| numbers | `=` `!=` `>` `>=` `<` `<=` `between` `in` `not in` `is null` `is not-null` |
| text | `=` `!=` `in` `not in` `contains` `does not contain` `starts with` `ends with` `matches` `doesn't match` `is null` `is not-null` |
| lookup / list values | `=` `!=` `in` `not in` `matches` `doesn't match` `is null` `is not-null` |
| payment mode, expiry | `in` `not in` only |
| dates, date-times | the number operators, on a part of the date: DATE, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, MONTH, WEEK_OF_YEAR, YEAR; date-times add HOUR_OF_DAY, MINUTE_OF_HOUR, and DATETIME (date and time) |

`between` holds strictly between its two values: both ends are excluded (`h_bit_amount between 40 and 50` holds for
45, not for 40 or 50; on dates, between 10 and 12 Nov holds on 11 Nov only). Its first value is the block in
`RHS_INPUT`, the second the block in `RHS_VALUE`.

`operators` holds the code, not the label: `==` `!=` `>` `>=` `<` `<=` `between` `in` `not_in` `null` (is null)
`not_null` `contains` `not_contains` `starts_with` `ends_with` `match` (matches) `not_match` (does not match).
Text: `=` and `matches` are exact and case-sensitive; contains, starts with and ends with test part of the text; on
an attribute that holds several values, `=` holds when any of them equals the value. `in` / `not in` take the chosen
codes from the value popup. Payment Amount is an old list attribute: it takes only `in` / `not in`.

**Dates and times.** Date conditions read dates in the program timezone. A BIT date sent without a timezone offset is
read as UTC; one with an offset keeps it. DAY_OF_WEEK runs Monday = 1 to Sunday = 7; HOUR_OF_DAY 0-23.
When the transaction happened is the BIT's date, `h_bit_date` (an activity attribute block, or the condition's own
attribute). The current date block is the moment the engine processes the BIT, not when the transaction happened: a
BIT sent late, replayed or tested with another date has a different current date. So a condition about the
transaction ("in the birthday month", "on Sundays", "in December") compares a part of `h_bit_date`; the current date
is only for what is meant as "today" at processing time.
The DATE part of a date or date-time is its calendar day in the program timezone, compared day by day with the
right-hand side: a static date, the current date, another date attribute or a date arithmetic block. `h_bit_date`
DATE `>` 10 Nov holds from 11 Nov; `>=` 10 Nov from 10 Nov; `<=` 10 Nov up to the end of 10 Nov.
A date part (YEAR, MONTH, DAY_OF_MONTH, DAY_OF_YEAR, WEEK_OF_YEAR, ...) is a whole number; DAY_OF_YEAR runs 1 to 365, or 366 in a
leap year, where every day after 28 Feb is one higher; it starts again at 1 every 1 Jan.
CALENDAR_DATE is a date's day and month in one fixed year (the engine replaces the year with 0000), compared in
calendar order from 1 Jan to 31 Dec, so the same day and month compare equal in every year. Date arithmetic is applied
to the whole date before the year is dropped: `date_of_birth` CALENDAR_DATE − 10 DAYS is the day and month ten days
before the birthday, whatever the year. A range of CALENDAR_DATEs that runs past 31 Dec (28 Dec ± 10 days: 18 Dec to
7 Jan) is two ranges, from its start to 31 Dec and from 1 Jan to its end.
A member date that is missing or cannot be parsed reads as -1 in every date part. A member date used inside a date
arithmetic block, for a member who has none, fails the offer for that transaction (a rules execution error: no rule
of the offer rewards it); an `is not-null` condition on that date placed earlier in the same rule or filter stops the
rule before it is reached.

**Value blocks:** a number (digits, optional decimals), a text, a date, a date-time, the current date, arithmetic
(operator `+` `-` `*` `/`, and `%` for the remainder), date arithmetic (`+` / `-` DAYS, MONTHS or YEARS, and snap to the end of
the MONTH or YEAR), a currency rate (award, exchange, reward rates), and attribute blocks (BIT, member, product,
payment, airport).

**Arithmetic results.**
- A number attribute (`h_bit_amount`, a counter, a balance) is a decimal: `h_bit_amount / 10` on 25 is 2.5.
- Two numbers written without decimals are whole numbers, and their division drops the remainder: `100 / 3` is 33,
  `7 % 2` is 1. With a decimal point on either side the result keeps its decimals (`100 / 3.0`).
- Arithmetic blocks nest: the inner block is computed first, whatever its operator (no operator precedence between
  blocks).
- Points credited are rounded half up to the program's point decimal scale (the tenant catalog gives it): at scale 2,
  2.5 points stay 2.5 and 3.333 become 3.33; at scale 0, 2.5 would be 3.
- A rate per currency unit ("N points per $1", "per AED 1") is the amount times N: a part of a unit earns its share,
  credited at the point decimal scale (12.50 at 10 per $1 is 125). Only a rate stated per full or whole unit ("per full
  $10") counts whole units.
- Date arithmetic: `+` / `-` N MONTHS keeps the day of the month, or the month's last day when it has fewer days
  (31 Jan + 1 MONTHS is 28 Feb). Snap to MONTH is the month's last day, snap to YEAR 31 Dec.

## 5. Counters

A counter is a member attribute the offer declares: `MA_O` (offer counter, kept across BITs) or `MA_A` (activity
counter, one BIT only). Only offer counters keep a value from one BIT to the
next: nothing else of an earlier BIT is visible to the rules.

- **Reading.** A counter never set reads 0 (number) or empty (text). Numbers are kept at the program's decimal scale.
- **Updating.** Update Attribute: INCREMENT_BY, DECREMENT_BY, SET, RESET (RESET_ALL for a vector counter); a text
  counter takes SET only. The value can be any value block, including arithmetic on the counter itself.
- **When an update is seen.** At once. In the same BIT, every condition and quantity reached after the update (later
  actions of the rule, rules further down the activity) reads the new value; rules above it do not run again. So the
  place of an update matters: a counter updated before a reward is read with its new value by that reward's quantity
  and by the rules below.
- **Regular and vector.** A regular counter holds one value. A vector counter holds one value per key of its
  namespace (per store, per product category): a condition can read the current key's value, the sum across all keys
  (Sum Across) or the number of keys the vector holds (Count of Unique). A
  condition picks which in `vector_counter_name`: the namespace attribute (e.g. `h_location`) reads the current BIT's
  key, `across` the sum of the values, `unique` the number of keys whatever their values (a key at 0 counts).
  RESET and RESET_ALL set values to 0 and keep the keys; no
  update removes a key, so a member's `unique` never goes down.
- **Targets.** A counter's target type: NO TARGET (any comparison), SINGLE (compared with its one target value) or
  LADDER (compared with one of the ladder's levels).
- **Per period.** A counter does not reset by itself at a month or week boundary.
- **Dates in a counter.** A counter holds numbers or text, not dates; a date is kept as a number built from its parts.
  YEAR × 366 + DAY_OF_YEAR grows with every day and never repeats, and the difference of two such numbers is the days
  between them, plus one for each 365-day year end in between. YEAR × 12 + MONTH does the same for months. A span of
  days that runs past 31 Dec has its days in two YEARs.

## 6. What blocks cannot express

- Loops: the only iteration is per BIT, per line item and per payment item.
- Looking at earlier BITs: anything from the past must have been kept in a counter when it happened.
- Custom functions: actions are the closed list above; values are the value blocks.
- Which offer runs first: the program orders offers; the rules of one offer cannot depend on another offer.
- The number of days between two dates, or a member date moved to the BIT's year.
