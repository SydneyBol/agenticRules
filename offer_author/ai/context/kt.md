# How offer rules execute (KT extract: gravty-ui, bolapi, engine)

<!-- from OfferRules-Blockly.md -->
### Runtime evaluation

`POST /api/bits` → `BitOpenApi.bitProcessing` → `executeBit` → `handleRequest` (JWT decode, program-id validation, `PermissionValidator`) → `bitExecution` (`BitDataValidator`, `BitDuplicateValidatorImpl` → 409 on duplicate) → `PipelineBitController(ExecutionMode.PIPELINE_SYNC).execute(...)`.

`BitProcessor.execute()` phase order (`BitProcessor.java:139`):
```
loadMember() → validateThirdPartyMembers() → processRules() →
verifyThirdPartyMembershipStage() → processBillingRules() →
validateExecution() → analyseExecution() → persistMembers() →
persistMemberOffers() → persistPrivileges() → persistBits()
```

`StatelessDroolsExecution` iterates `ruleBase.getDroolsKb()` and fires a separate `StatelessKieSession` per `KieBase` — Base, MultiSponsor, Sponsor and Tier KieBases each fire over the same `BitExecutionContext`.

---

## 4. Blockly capability catalog

### Structural grammar

The workspace is a strict containment hierarchy, enforced by Blockly `setCheck()` type constraints:

```
offer_generic ("Offer")
├── A: activities   [check 'Activity']
│   └── activity ("On Activity")
│       ├── F: ACTIVITY_CRITERIA  [check 'Filter' | 'OR']   ← eligibility gate
│       └── R: RULES              [check 'Rule']
│           └── rule ("Rule" + tag)
│               ├── C: CONDITION  [check 'If' | 'OR']
│               └── A: ACTION     [check 'Action' | 'Rule']
└── E: events       [check 'Event']
    └── event ("On Event") → RULES → rule …
```

### Block categories

Toolboxes declare three categories: **Offer Level Block**, **Rule Level Block**, **Attributes & Variables**.

| Category | Block types |
|---|---|
| Offer-level | `offer_generic`, `offer_award`, `offer_deal`, `event`, `event_privilege`, `activity`, `activity_enrollment`, `activity_criteria`, `or`, `or_deal` |
| Rule-level | `rule`, `billing_rule`, `rule_deal`, `rule_line`, `rule_award`, `payment_rule`, `if_generic_adaptive`, `if_deal_block`, `if_payment_attribute`, `action`, `action_third_party` |
| Event-level | `targeting_holder`, `targeting_condition` |
| Attributes & Variables | `static_value`, `static_string_value`, `static_date`, `static_date_time`, `current_date_block`, `arithmetic_generic`, `arithmetic_date`, `currency_converter`, `blueprint_variable`, `activity_attribute`, `member_attribute`, `product_attribute`, `payment_attribute`, `airport_attribute`, `tier_thresholds` |

### Condition vocabulary

The **object** dropdown (`dropdownObject`) — what you can test against:

```
[['--',''], ['3rd Party','third_party'], ['Airport','airport'],
 ['Airport Pair','airport_pair'], ['BIT','activity'], ['Location','location'],
 ['Member','member'], ['Product','products_attrs'], ['Sponsor','sponsor']]
```

Narrowed per offer type: PRIVILEGE → Member only; DEAL → BIT + Product only.

**Member attribute levels** (`MemberAttributeLevel` enum in the engine — these are the `MA_*` codes that appear in compiled rules):

| Code | Meaning |
|---|---|
| `MA_P` | Program-Level |
| `MA_O` | Offer-Level (counters / MTO) |
| `MA_A` | Activity-Level |
| `MA_S` | Analytics Write-back |
| `MA_T` / `MA_TO` | Tier / Tier-MTO |
| `MP_S` | Personalization (AI individualization) |
| `MR_S` | Retention Score |
| `MA_CA` / `MA_CC` | Bank Account / Cobranded Card |
| `MA_SP` | Subscription |

**Operators** (verbatim from `BlockDefinitionService`):

| Set | Values |
|---|---|
| `num_conditional_operators` | `=` `!=` `>` `>=` `<` `<=` `is null` `is not-null` `in` `not in` `between` |
| `str_conditional_operators` (offers) | `=` `!=` `is null` `is not-null` `in` `not in` `contains` `does not contain` `doesn't match` `matches` `starts with` `ends with` |
| `str_lov_lookup_operatorsforoffers` | `=` `!=` `matches` `doesn't match` `is null` `is not-null` `in` `not in` |
| `payment_conditional_operators` | `in` `not in` only |
| `date_operators` (dimensions) | DATE, DATETIME, DAY_OF_MONTH, DAY_OF_WEEK, DAY_OF_YEAR, MONTH, WEEK_OF_YEAR, YEAR |
| `datetime_operators` | adds HOUR_OF_DAY, MINUTE_OF_HOUR |
| `expiry_conditions` | `in` / `not in` only |

How text comparisons run: = / matches is an exact, case-sensitive match; contains, starts with and ends with test part of the text; on an attribute that holds several values, = holds when any of them equals the value.

**Third parties**: ACTIVITY_REFERRER, COMPANION, CORPORATE, EMPLOYEE, ENROLLMENT_REFERRER, FAMILY_HEAD, INSTITUTION, TRANSFEREE.

### Action vocabulary

How actions run: Reward Points credits the computed quantity to the loyalty account under the reward tag; Redeem / Reset / Return / Expire Points debit, zero, return or expire points; Assign Privilege issues the privilege product (usage policy, how many, validity dates). Every action records the name of the rule that fired it.

`actiondropdownObject`, keyed by offer type:

| Offer type | Available actions |
|---|---|
| **REWARD** | Assign Play & Win Reward, Assign Privilege, Assign Sweepstakes, Expire Points, Redeem Points, Reset Points, Return Points, Reward Points, Update Attribute |
| **AWARD** | Award Product, Redeem Points |
| **PRIVILEGE** | Assign Privilege, Reward Points, Update Attribute |
| **ENROLLMENT** | Assign Privilege, Enroll Member, Redeem Points, Reward Points, Update Attribute |
| **Tier setup** | Assign Play & Win Reward, Assign Tier, Assign Sweepstakes, Redeem Points, Reset Points, Reward Points, Update Attribute |
| **SERVICE_MENU** | + Extend Privilege, Redeem Privilege, Reinstate Privilege, Transfer Points, Transfer Privilege |

**Update Attribute** operators: `INCREMENT_BY`, `DECREMENT_BY`, `RESET`, `SET` (+ `RESET_ALL` for vector counters). Non-numeric attributes get `SET` only.

### Rule wrappers — the four iteration modes

How each rule wrapper runs, per BIT:

| Block | Runs |
|---|---|
| `rule` | once per BIT on its header attributes: when the conditions hold, the actions run once |
| `rule_line` | once per line item: conditions and actions see that line (l_* and product attributes); actions can run per line |
| `payment_rule` | once per payment item of the BIT |
| `rule_award` | award offers: redeems the award price and assigns each product |

A condition on a third party the member does not have skips that rule (logged), it does not fail the BIT.

If the condition text references any third-party accessor, the whole `if` is auto-wrapped:

### Value / variable blocks

- `static_value` — validated against `/^[0-9]*\.?[0-9]+$/`
- `arithmetic_generic` — operators `/` `X` `+` `-` `%`
- `arithmetic_date` — `+` `-` `snap-to`; quantities DAYS / MONTHS / YEARS; snap to MONTH / YEAR
- `currency_converter` — Award Rate, Exchange Rate, Reward Rate, Sponsor Award Rate, Sponsor Reward Rate

**`in` / `not in` open a modal**, not an inline field — `inOperatorPopup` → `inLovsPopup` (LOV) / `inLookUpPopup` (LOOKUP) / `inTextPopup`. The chosen set is stored in `inLovLookupListMap` keyed by attribute + block id. Values can also be **bulk-uploaded** via an S3 path carrying the marker `bulk-values-validation`.

### Counters (MTO)

An `MA_O` / `MA_TO` attribute carries `counter_type` ∈ {REGULAR, VECTOR} and `target_type` ∈ {LADDER, SINGLE, NO TARGET}:
- **LADDER** → dropdown of the ladder's target levels
- **SINGLE** → read-only label of `MTO.target_value`
- **NO TARGET** → full numeric operator set

Vector counters add aggregation options: `Sum Across <name>` (`across`) and `Count of Unique <name>` (`unique`).

---

## 5. Worked example

In the engine: the filter (activity type SPEND, sponsor X) admits the BIT to the activity; the rule's condition (amount >= 100, month 12) is checked on the BIT header; when it holds, points = amount x 2 are credited to the account under tag BASE, once per BIT.

*"Earn 2× points on spend over $100 at sponsor X during December."*

**Canvas**
```
Offer
└─ A: On Activity
   ├─ F: Filter [BIT]  h_bit_type == "SPEND"
   │     Filter [Sponsor]  sponsor_id == X
   └─ R: Rule "december-2x"
      ├─ C: If [BIT] h_bit_amount >= 100
      │     If [BIT] h_bit_date MONTH == 12
      └─ A: Action → Reward Points in <account> Tag BASE
              Quantity = Arithmetic( BIT.h_bit_amount × 2 )
```

Note the December window is **not** in the compiled rules — it's `Offer.start_date` / `end_date` on the bolapi row, enforced by offer selection before Drools ever runs. Only put a month condition in the rule if you need it *in addition to* the offer schedule.

---

## 6. Limits and sharp edges

**Blockly cannot express**
- Arbitrary loops or recursion. Iteration is fixed to the four rule wrappers (header / line / payment / award).
- Cross-BIT or historical queries. Everything reads the current `BitExecutionContext`. Accumulation must go through MTO counters.
- Custom functions. The action vocabulary is a closed dropdown per offer type.
- Offer *selection* logic — that is hand-written Java (`EligibileOfferStrategyImpl`), not authorable.

**Where the abstraction leaks**
- **Deal offers emit JSON, not compiled rules.** `offer_deal`, `rule_deal` and `if_deal_block` `JSON.stringify` their children and split on `//`. `Offer.compiled rules_EXCLUDED_OFFERS = (deal, award_catalog)`.
- **No Drools at all for 8 BIT categories.** `KbGenerator.getKB()` returns `null` for AVAILMENT, SERVICE(EXPIRATION), CANCELLATION, DEAL_REDEMPTION, PAYMENT, SWEEPSTAKE, PLAY_AND_WIN, SUBSCRIPTION — those are pure hand-written Java.
- **Block-type collisions.** `blocks/index.ts` deliberately omits `SEGMENT_LEVEL_BLOCKS` and `EVENT_LEVEL_BLOCKS` from `CUSTOM_BLOCKS`, and never imports `PRODUCT_LEVEL_BLOCKS` — three classes share the type `targeting_condition` and two share `targeting_holder`. Blockly registration is global and last-wins, so these are injected per-screen via `[customBlocks]`. Fragile.
- **Mode detection via URL string matching.** Blueprint mode is `window.location.href.indexOf('offerblueprintrules/') !== -1`; tier mode likewise. Routing changes silently change compiled rules dialect.
- **`Payment Amount` is a backward-compat hack.** Force-appended to BIT attributes with code `amount` and datatype forced to `LOV`, kept for offers created up to 2.4.7 — which is why it only offers `in` / `not in`.

**Known gotchas**
- `OfferStatus` seeds 11 codes (adds `review`, `runtest`) but `Offer.OFFER_STATUS_CHOICES` lists only 9 — the model choices tuple is out of sync with the seeded DB rows.
- `OFFER_TYPE_PRIVILEDGE` is misspelled in `offers/models.py`.
- Offer-movement components use plain `btoa(...)` with no UTF-8 pre-escape, unlike the offer-rules path — non-ASCII rule content will corrupt on migration.
- `engine/offertemplates/` is not a Maven module and is referenced by zero Java files — it's a repo-stored library of FreeMarker templates meant for S3 upload.
- `blocks/rule-level/sample.ts` is a zero-byte file.
- `BlockMutator` (the framework's abstract mutator class) is declared and wired through `Blockly.Extensions.registerMutator` but **never instantiated by any block** — all real mutation uses direct `this.block.mutationToDom = …` assignment.

---

<!-- from Engine.md -->
## 3. How an offer is triggered for a BIT

Entry: `POST /api/bits` (`BitOpenApi` :146), Kinesis (`AsyncBitsLambda`), batch
(`bits_batch` / EMR), internal events (`event/ScheduledOfferTargetLambda`, `bg_offer_execution`).

1. Raw BIT → Firehose; JWT; `BitDataValidatorImpl`; duplicate check
   (`BitDuplicateValidatorImpl` — conditional `BIT_CORRELATION` insert; `DUPLICATE_BIT`).
2. `PipelineBitController.executeBIT` :92 loads Member, MemberOffers, segmentation → `BitExecutionContextImpl`.
3. `validateBitPolicy` (membership stage; `RewardBitPolicyValidator` etc. per category).
4. `BitController.process` :243: processor by `h_bit_category` (`ProcessorFactory`): ACCRUAL→Reward,
   REDEMPTION→Award, CANCELLATION→Revert, AVAILMENT, DEAL_REDEMPTION, SERVICE, POINTS_RESERVATION,
   SWEEPSTAKE, PLAY_AND_WIN… Optional trust/fraud simulation (`fraud_status`).
5. **Eligibility** — `EligibileOfferStrategyImpl.findAllEligibleOffers` :88:
   base + multi-sponsor + sponsor active offers → LAUNCHED only (TEST for test members) →
   qualifying segments → target type (all / sponsor / member-targeted / segment) :201 →
   `RewardOfferValidator.checkBitEligibility` (location, opt-in, per-member day/week/month/lifetime
   limit, date window, sponsor balance, multi-sponsor) → sort by priority.
6. **KieBase** — `KbGenerator.Factory`: `RewardSpecificOfferKbGenerator` (BIT names `offer_id`),
   `RewardKbOptimizedGenerator` (precompiled Base / MultiSponsor / Sponsor KieBases from Ehcache,
   503 if missing), or `RewardKbGenerator` (compile eligible offers per BIT). Cached KBs contain all
   active offers; per-BIT filtering happens inside the rule via `OfferEligibilityHelperImpl.checkEligibility`
   :69, which also enforces non-cumulative exclusivity (one header offer per BIT / one line offer per SKU).
7. **Fire** — `StatelessDroolsExecution.execute`: one stateless session per KieBase, sole fact = `BitExecutionContext`.
   multiplier = computed value with tag BASE/BONUS), `adjustPoints`, `assignPrivilege` (coupons/vouchers),
   `availPrivilege`, `updateMTA` (counters/accumulators), `resetAllMTO`, `transferPoints/Privilege`,
   `updateMemberTier`, `enrol`, `assignSweepstake`, `assignPlayWinReward`, FX/award conversions.

## 4. How the result is saved (`BitProcessor.execute` :160)
1. `validateExecution` — program/sponsor/location accrual & redemption limits (`ACCRUAL_LIMIT`,
   `REDEMPTION_LIMIT`), sponsor prepaid balance, deals.
2. `analyseExecution` — last-activity dates, product counters.
3. `persistMembers` — optional CRIS external accrual / pre-commit webhook → `MEMBER` (balances,
   `@DynamoDbVersionAttribute` optimistic lock; retried, then MEMBER_LOCKED), `PENDING_POINTS`.
4. `persistPrivileges` — `ASSIGNED_PRIVILEGE`, `AVAILMENT_LIMIT`.
5. `persistMemberOffers` — `MEMBER_OFFER` usage counts; `Counter` and per-day/week/month `AvailmentLimit`.
6. `persistBits` — BitOutput to Kinesis `kinesis_stream_bit_processed` (or SNS); processed BIT +
   offer actions as LZ4 JSON to S3 (`processed_bit_storage_directory/{program}/{member}/{bit}`);
   `TIER_ACTION`; source-id `Lookup`; limit tables.
7. Firehose backup of processed BIT; correlation → SUCCESS; tier processing triggered; 201 with balances.

Reversal: CANCELLATION BIT → `RevertBitProcessor` loads original from S3, checks `BIT_STATUS`,
inverts actions (REVERSAL / PARTIAL_REFUND per SKU / HEADER_LEVEL_PARTIAL_REFUND), decrements counters.

## Runtime behaviours confirmed by the local harness (2026-10-21)
Source: `offer-author-spike/engine-harness` (real Drools + `OfferActionHelperImpl`, infra stubbed).
- **MA_A is not persisted across BITs.** It lives only in `EngineMemberImpl` temp maps. Cross-transaction state must use MA_O.
- **Unset MA_O reads 0 / "".** a number condition on it sees 0 and a text condition an empty value. Values are stored as strings at program decimal scale ("1.00").
- **`updateMTA` is visible to later `if`s in the same Drools rule.** Order matters for cap/remainder logic.
- **Timezones:** offset-less `h_bit_date` parses in the JVM zone (UTC on Lambda); date conditions read dates in the program timezone. A string with an offset keeps that offset and is not converted.
- **`LimitFrequency` has no YEAR,** and `fromValue` maps unknown values to TOTAL.
- **Calculated member attributes are never stored** (`CalculatedMemberAttribute`, `DerivedAttributeCalculator`): `age` = whole years since `date_of_birth`, `membership_tenure` = days since `date_of_joining`, `days_since_bit` = days since `last_activity_date`, `email_domain` = the part of `email` after `@`. The segment executor computes them against the current date (not the BIT date); a member without the source attribute reads 0. To test "members over 27", give `date_of_birth`, not `age` (confirmed 2026-10-25: `age: 30` alone is outside the segment).
- **Local build on JDK 25** needs `-Dlombok.version=1.18.42 -Dmaven.compiler.proc=full`, plus a `java.lang.Compiler` shim for MVEL at runtime.

<!-- from RulesXml-FilterStandard.md -->
## Purpose

Generated rules XML must read like a well-written offer, not just compile. The manager's review
(2026-10-22, on `offer_rules_xml1281376581.xml`, the H10 "Gold members whose total spend reaches $500 get a
complimentary voucher, once" draft) showed two violations:

- The On Activity Filter already said `Activity Type in Spend`, yet each rule repeated `h_bit_type == SPEND`.
- `tier_class in GOLD,Gold` was repeated in both rules (the counting rule and the reward rule), although the whole
  offer is for Gold members.

The standard is: **a condition that every rule under On Activity needs is global, and is written once, in the Filter
(`activity_criteria`)**. A rule holds only the conditions that make it different from the other rules.

## Why it is safe (stock UI + engine)

- The stock Filter block (`gravty-ui/src/app/gravty-blockly/blocks/offer-level/filter.block.ts`) offers the same
  objects as a rule condition: activity, member (MA_P and the other levels), location, sponsor.
- The UI's compiled rules generator ANDs the Filter into every rule under the activity. Moving a condition that every rule
  already has into the Filter therefore produces the same rule semantics.
