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
   timezone), whose per-member limit and frequency (per day, week, month or lifetime; there is no yearly frequency)
   and total limit are not used up. A BIT before the offer's start date is refused for that offer
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
promises no order between two On Activity blocks of one offer: logic in which one part must see what another part did
in the same BIT (a counter update) belongs in one activity.

**Rule.** Its conditions (If blocks chained one under another = AND; an OR block = either branch) are checked once per
BIT, at header level; when they hold, its actions run once, top to bottom. A rule with no condition always runs.
There is no "else": every rule under the activity is checked, top to bottom, and every rule whose conditions hold
runs. Two rules that both hold both reward (they stack). To make rules alternatives, give them conditions that exclude
each other (bands such as `>= 100` and `>= 50 and < 100`, a tier `in` and `not in`) or put one inside the other.

**A rule inside a rule** (a Rule block in another rule's actions). Checked only when the outer rule's conditions held,
at its place among the outer rule's actions. Use it for a condition that refines the outer one ("Gold members get 100;
among them, those at the airport store get 50 more") or for a step that must follow the outer rule's actions.

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

The tier a member is in can be read both ways: `tier_class` (MA_P) and the tier programme (MA_T) are separate data
on a tenant. A member without the attribute reads as section 4 and the member dates notes say (a calculated
attribute 0, a date part -1): `age < 30` also takes members with no date of birth, unless the segment also asks for one.

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
  100`).
- A date part (the condition's, or an attribute block's): a DATE attribute takes it in `date_operator`, a DATE-TIME
  attribute in `datetime_operator`; HOUR_OF_DAY and MINUTE_OF_HOUR exist for date-times only. The part is then compared
  with a number block in `RHS_INPUT` (Sunday: `datetime_operator` DAY_OF_WEEK, `==`, the number 7); DATE compares with
  a date block.
- Reward Points: `action_type` give_points; `FORMULA` = the quantity (a value block); the account and reward tag are
  filled by the tools.
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
activity), `email_domain`. A member without the source attribute reads 0. To test "members over 27", the member needs
a `date_of_birth`, not an `age`.

**Operators:**

| Values | Operators |
|---|---|
| numbers | `=` `!=` `>` `>=` `<` `<=` `between` `in` `not in` `is null` `is not-null` |
| text | `=` `!=` `in` `not in` `contains` `does not contain` `starts with` `ends with` `matches` `doesn't match` `is null` `is not-null` |
| lookup / list values | `=` `!=` `in` `not in` `matches` `doesn't match` `is null` `is not-null` |
| payment mode, expiry | `in` `not in` only |
| dates | compare a part of the date: DATE, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, MONTH, WEEK_OF_YEAR, YEAR; date-times add HOUR_OF_DAY, MINUTE_OF_HOUR |

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

**Value blocks:** a number (digits, optional decimals), a text, a date, a date-time, the current date, arithmetic
(operator `+` `-` `*` `/`, and `%` for the remainder), date arithmetic (`+` / `-` DAYS, MONTHS or YEARS, and snap to the start of
the MONTH or YEAR), a currency rate (award, exchange, reward rates), and attribute blocks (BIT, member, product,
payment, airport).

## 5. Counters

A counter is a member attribute the offer declares: `MA_O` (offer counter, kept across BITs) or `MA_A` (activity
counter, one BIT only). Anything that must carry from one BIT to the next (counts, running totals, "once", caps,
unlocks, the period something last happened in, distinct stores) is an offer counter.

- **Reading.** A counter never set reads 0 (number) or empty (text). Numbers are kept at the program's decimal scale.
- **Updating.** Update Attribute: INCREMENT_BY, DECREMENT_BY, SET, RESET (RESET_ALL for a vector counter); a text
  counter takes SET only. The value can be any value block, including arithmetic on the counter itself.
- **When an update is seen.** At once. In the same BIT, every condition and quantity reached after the update (later
  actions of the rule, rules further down the activity) reads the new value; rules above it do not run again. So the
  place of an update matters: a counter updated before a reward is read with its new value by that reward's quantity
  and by the rules below.
- **Regular and vector.** A regular counter holds one value. A vector counter holds one value per key of its
  namespace (per store, per product category): a condition can read the current key's value, the sum across all keys
  (Sum Across) or the number of keys with a value (Count of Unique), e.g. "visited 3 different stores". A condition
  picks which in `vector_counter_name`: the namespace attribute (e.g. `h_location`) reads the current BIT's key,
  `across` the sum, `unique` the number of keys. So "N different stores" is one vector counter per store, updated on
  each visit, tested at `unique` >= N; read at the current store it only ever sees that one store's value.
- **Targets.** A counter's target type: NO TARGET (any comparison), SINGLE (compared with its one target value) or
  LADDER (compared with one of the ladder's levels).
- **Per period.** A counter does not reset by itself at a month or week boundary. A per-period count keeps the period
  it belongs to (e.g. the month's index from the BIT date) in a second counter, and starts over when the BIT's period
  differs from it.

## 6. What blocks cannot express

- Loops: the only iteration is per BIT, per line item and per payment item.
- Looking at earlier BITs: anything from the past must have been kept in a counter when it happened.
- Custom functions: actions are the closed list above; values are the value blocks.
- Which offer runs first: the program orders offers; the rules of one offer cannot depend on another offer.
- The number of days between two dates, or a member date moved to the BIT's year (see the member dates notes).

## 7. Example

*"Earn 2x points on spend over $100 at sponsor X during December."*

```
Offer
└─ On Activity
   ├─ Filter [BIT]  h_bit_type == "SPEND"
   │  Filter [Sponsor]  sponsor_id == X
   └─ Rule "december-2x"
      ├─ If [BIT] h_bit_amount > 100
      │  If [BIT] h_bit_date MONTH == 12
      └─ Action → Reward Points in <account>, tag BASE
              Quantity = BIT h_bit_amount * 2
```

The filter takes Spend BITs at sponsor X; the rule's conditions are checked on the header; when they hold, the amount
* 2 is credited once for the BIT. The December window is better set as the offer's dates in Offer Main (checked
before the rules); a month condition in the rule is only needed in addition to them.
