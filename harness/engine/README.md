# Engine harness (harness/engine)

An offline Java harness that compiles Gravty offer DRL and fires it in the real engine code: Drools 7.73 plus the
`bit_processor` / `common` 2.17.0 classes. It makes no AWS, DynamoDB, S3, Redis or Kinesis calls. The only network
access is Maven resolving its build plugins the first time you build.

## Build and run

```bash
# prerequisites: JDK 17+ (tested on JDK 25), Maven 3.9, and the engine installed in ~/.m2
# (com/gravty/bit_processor/2.17.0/bit_processor-2.17.0-jar-with-dependencies.jar)
cd harness/engine
mvn -q package -DskipTests          # -> target/harness.jar + target/lib/{engine.jar,jdk-compat-shim.jar}
java -jar target/harness.jar <case.json>                  # JSON result on stdout (--show-drl also prints the decoded DRL)
# self-test cases and selftest.sh: ~/Documents/Gravty/offer-xml-generator-dev/harness/engine
```

* Use `-Dengine.jar=/path/to/bit_processor-…-jar-with-dependencies.jar` at build time to point at another engine build.
* Use `-Dharness.log.level=DEBUG` at run time to see engine logs on stderr. The default level is WARN.
* Use `-Dharness.jvm.tz=Asia/Kolkata` at run time to change the JVM default timezone. The default is UTC, which is what
  the Lambdas use.
* Exit codes:
  * `0`: ok
  * `3`: template or compile error
  * `4`: harness crash
  * `6`: the case's `expect` block failed

### Build quirks (handled automatically)

* **Leaked signature files.** The engine fat jar contains BouncyCastle `META-INF/*.SF/*.DSA/*.RSA` signature files.
  With these files present the JVM rejects the jar ("Invalid signature file digest"). The build copies the jar to
  `target/lib/engine.jar` and removes those files. The engine-develop tree is not modified.
* **MVEL on JDK 21+.** MVEL 2.4.14, which is bundled and used by Drools 7.73, references `java.lang.Compiler`. That
  class was removed in JDK 21. The build compiles a no-op shim (`src/shim`). When `Main` sees that the class is missing,
  it re-launches itself with `--patch-module java.base=lib/jdk-compat-shim.jar`. On JDK 17, which production uses, the
  relaunch does not happen.
* **Annotation processing.** It is disabled (`<proc>none</proc>`) because the fat jar ships Lombok and other processors.

## case.json

```jsonc
{
  "offer_id": 123,                    // used for ${offer_id}, rule names, MA_O scoping
  "drl": "<blueprint DRL with ${...} placeholders, as the UI generator emits>",   // or "drl_file": "path"
  "params": {"multiplier": "2", "loyalty_account": "1", ...},                       // blueprint (template) variables
  "param_substitution": "freemarker", // default; "replace" = plain string replace; "none" = DRL already rendered
  "priority": 1, "non_cumulative": false, "offer_level": "header_level|line_level", "targeted_segment": "SEG1",
  "program": {"id": 1, "timezone": "UTC", "decimal_scale": 2,
              "features": {"ProductExternalIdValidation": false},                 // FeatureSwitch toggle_name -> active
              "accounts": {"1": {"account_type": "..."}}},                        // optional; missing accounts auto-created
  "member": {"id": "M1", "attributes": {"date_of_birth": "1990-06-15"}, "tier": "GOLD", "balances": {"1": "0"}},
  "member_offers": {"123": {"visits": "2.00"}},       // optional pre-existing MA_O (MTO) state
  "products": {"SNK-1": {"category": "SNACKS"}},      // SKU attributes merged into lines (RewardProduct)
  "privilege_products": {"501": {"name": "Free Coffee", "cost": 4.5}},   // Product rows for assignPrivilege
  "bits": [{"header": {"h_bit_date": "2026-10-24T12:00:00", "h_bit_amount": 50, "h_bit_type": "SPEND",
                       "h_bit_category": "ACCRUAL", "h_location": "L1", "h_sponsor_id": 10,
                       "h_program_id": 1, "h_member_id": "M1"},
            "lines": [{"l_product_external_id": "SNK-1", "l_quantity": 3, "l_amount": 6}]}],
  "expect": [ {"points": [{"action": "REWARD_POINTS", "account": "1", "tags": "BONUS", "total": 100.0}],
               "privileges": ["501"], "mto": {"visits": "3.00"}, "mta": {"x": 1.0}, "status": "SUCCESS"} ]
}
```

### Pipeline (mirrors production)

1. **Creation-stage render.** This matches `RulesBuilderImpl.buildDrl` and `OfferBuilderV2Impl`. The real
   `TemplateEngine` (Freemarker) renders the template with `params` plus `offer_id` (as a string). It re-emits
   `${offer_priority}`, `${header_eligibility_check}`, `${line_eligibility_check}` and `${targeted_segment}` literally.
   This produces the stored DRL, as the `/light_offers` creation API would.
2. **Runtime decode.** The stored DRL is base64-encoded into an `Offer` (`drl`, `priority`, `nonCumulative`,
   `offerLevelType`, `targetedSegment`). Then it goes through the real `DRLDecoderFactory.get().decode(offer)` chain:
   `ParamDecorator(EligibilityDecorator(Base64Decorator))`. The chain uses the real
   `cumulative_offer.json`, `header_non_cumulative_offer.json` and `line_non_cumulative_offer.json` templates from the
   jar.
3. **Compile.** Runs `DROOLS_DRL_HEADER + decoded` through the real `KieUtilities.generateKie`. On failure the harness
   repeats the same KieBuilder build to collect the messages, because the engine only throws a generic
   `RULES_COMPILE_ERROR`. Drools line numbers include the 16-line header (`header_line_offset`).
4. **Per BIT.** The harness builds the following:
   * the real `BitExtractorImpl` and `addLineSequence`
   * a new `PointsManagerImpl`
   * a new `EngineMemberImpl(member, h_bit_date, pm, memberOfferMap, programId)`
   * `new BitExecutionContextImpl(env, bitInput, engineMember)`, with a real `OfferEligibilityHelperImpl` where the
     offer is registered as cumulative, header non-cumulative or line non-cumulative

   It then calls the real `RulesExecutionHelper.Factory.get().execute(ctx, ruleBase)`, which is
   `StatelessDroolsExecution`. That call also maps exceptions to `BitError` exactly as production does.
5. **Carrying state forward.** This mimics `persistMembers` and `persistMemberOffers`:
   * The same `Member` object (attributes, loyalty balances) and the same `Map<Long, MemberOffer>` (MTO = MA_O) are
     handed to the next BIT's new `EngineMemberImpl`.
   * After a successful BIT, the real `MemberOfferUtils.updateOfferCount` runs.
   * A failed BIT is rolled back to a deep-copy snapshot, because production does not persist failed BITs.

### Output per BIT

* `rules_fired`: from an agenda listener attached through a KieBase proxy.
* `points`: grouped by action, account and tags, with totals and rule tags.
* `privileges`: product, quantity, value and generated codes.
* `attribute_updates`: every `updateMTA`, with level, name, namespace, operator, old and new values.
* `mto_after`: the MA_O map per offer.
* `mta_after_this_bit`: MA_A.
* `loyalty_balances_after` and `member_attributes_after` (MA_P).
* `status`, plus `error` with `bit_error`, `exception` and `root_cause`.

Top-level fields:

* `warnings`: unresolved placeholders and auto-created accounts.
* `stubbed_infra_calls`: every infrastructure method the engine called that fell through to an "empty" default.

## What is real and what is stubbed

| Real engine code | Stubbed |
|---|---|
| `TemplateEngineFM`, `DRLDecoderFactory` chain, template JSONs | `DataHelper`: program policy is a real `Program` object built from `case.program`. `getFeatures`, `getOffer`, `getRewardProducts` and `getProduct` come from the case. Everything else returns null or empty. |
| `BaseKieUtilities.DROOLS_DRL_HEADER`, `KieUtilities.generateKie`, Drools 7.73, MVEL 2.4.14 | `DbHelper`: every read is "not found" or empty (no stored member offers, privileges, tiers, partner memberships, card data) |
| `StatelessDroolsExecution` (including the exception-to-BitError mapping) | `GeneralConfigHelper`: `region=us-east-1` only |
| `BitExecutionContextImpl` (`readAttribute*`, `existInList*`, `getLineReadHelper`, SKU merge) | `S3Helper` singleton is pre-seeded with an uninitialised instance, so no AWS client is ever built |
| `EngineMemberImpl` (`readAttribute*`, `readMemberAttribute*`, `updateAttribute`, derived attributes) | `EnvironmentProviderImpl.ourInstance` is set by reflection to an instance with only config, db and data helpers |
| `OfferActionHelperImpl` (`pointsAction`, `assignPrivilege`, `updateMTA`, ...), `PointsManagerImpl` | Getting "eligible offers" (`EligibileOfferStrategyImpl`) is replaced by registering the single case offer |
| `OfferEligibilityHelperImpl`, `MemberOfferUtils`, `DateUtils`, `BitExtractorImpl`, `PointsManagerImpl` | Sponsor and location lookups return null |

Stubs are plain `java.lang.reflect.Proxy` objects (see `Stubs.java`). The harness does not use Mockito, because the
ByteBuddy bundled in the fat jar does not support JDK 25.

## Known limitations

* **Offer selection is not run.** The harness does not run `EligibileOfferStrategyImpl`, so these checks are skipped:
  offer dates, status, location filters, targeted segment membership, acceptance, frequency and limit-per-member
  checks. `checkEligibility(<id>)` uses the real `OfferEligibilityHelperImpl`, with the case offer registered as
  eligible. Non-cumulative "first applied offer wins" semantics are real, but there is only one offer.
* **Post-rule BIT processing is not run.** The harness does not run `validateExecution`, caps, accrual and redemption
  limits, sponsor balance and billing, `BitPolicyValidator`, tiers KieBase, deals, or third-party bits.
* **Only one KieBase.** Production may also add tier and other KieBases. The harness runs one KieBase holding the
  single offer.
* **Infrastructure-backed reads are empty.** DRL that reads tier data, cobrand cards, segments (`MA_S`), custom
  entities, airport pairs or sponsor and location attributes returns empty values or 0, because the DB stub has no
  rows. Sponsor and location lookups return null.
* **Timezones.** The JVM default timezone is pinned to UTC, as on the Lambdas. Joda's `DateTime.parse()` on offset-less
  strings such as `h_bit_date` uses the JVM default zone. `DateUtils.dateToField(...)` and
  `readAttributeD` / `readMemberAttributeD` interpret offset-less dates in the program timezone
  (`program.timezone`), using `withZone(programTz).withOffsetParsed()`. Strings that include an offset keep their own
  offset and are not converted. A quick check confirmed this: `dateToField("2026-10-24T23:30:00Z", HOUR_OF_DAY, "Asia/Kolkata")`
  returns 23, not 5.
* **Accounts are auto-created.** An account referenced by `pointsAction` but missing from `program.accounts` is
  auto-created with defaults. The result's `warnings` field reports it. Production would fail the BIT in that case: an NPE in `pointsAction` becomes
  `UNEXPECTED_RULES_ERROR`.
* **One case per JVM.** Engine singletons (`FeatureSwitchHelper`, `EnvironmentProviderImpl`) are static.

## Engine behaviours confirmed with this harness

* **Unset MA_O reads.** `readAttributeN("member", null, "MA_O", name, offerId)` returns `0.0` when the MemberOffer or
  key is missing (`MemberOfferUtils.readAttributeN`). `readAttributeS` returns `""`, so `.contentEquals("Yes")` is
  safe.
* **MA_O number format.** MA_O numbers are stored as strings at the program decimal scale: `"1.00"`, `"2.00"`, ...
* **MA_O persists, MA_A does not.**
  * MA_O lives in `MemberOffer.mtoMap` and persists across BITs.
  * MA_A is `EngineMemberImpl.tempAttributesN/S`. It is never persisted, so it is BIT-scoped: it starts at 0 in every
    BIT.
* **`${offer_id}` cannot be left for the runtime decoder.** `EligibilityDecorator` runs Freemarker without `offer_id` in
  its input map. Freemarker throws `GravtyTemplateMissingParameter`, and the decorator only logs it. The DRL then
  passes through with every `${...}` still present, and compile fails at `rule "activity-${offer_id}-2"`. In
  production, `offer_id` and the blueprint variables are substituted at offer creation.
* **Reward re-issue in `Progressive_Spend_Bonus`.** The blueprint's `== N` level checks have no "already rewarded"
  guard. A later BIT that does not move the counter (amount below the threshold) re-issues the reward for the current
  level. The dev workspace's `harness/engine/cases/progressive_spend_bonus.json` encodes this.
