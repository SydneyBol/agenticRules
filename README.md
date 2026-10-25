# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → Blockly rule XML that the stock gravty-ui
loads as is (no UI change), plus a note for the manager, the counters the UI will create, and proposed offer settings.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50 at the online store" -o gold.xml
offer-xml --batch examples/prompts.txt --out-dir examples/out
offer-xml-server --catalog-dir catalogs/devintam1/bolapi --tenant devintam1 --sponsor 1 --account 12 --tag BASE
```

`gold.xml` is what the UI uploads; `gold.json` next to it has status (`draft` / `needs_input` / `unsupported` /
`error`), readback, assumptions, blocking questions, unsupported items, offer settings, counters, latency.
Examples of both are in [examples/out](examples/out).

## Architecture

```
 English prompt ─────────────┐                     tenant catalog (bolapi: attributes, LOV/lookup codes,
 (CLI, or the stock UI via   │                     activity types, loyalty accounts + tags, privileges)
  the Pulsar-compatible API) │                                   │ catalog.py
                             ▼                                   ▼
              ┌───────────────────────────────────────────────────────────────┐
              │ 1. Model (llm.py, tools.py)  Claude Opus 5.5 on Bedrock         │
              │    system = rules + cached tenant catalog; tools = 12 business  │
              │    primitives (earn, bonus_if, tiers, cap, line_item,           │
              │    collect_all, nth_activity, milestone, unique_count, unlock,  │
              │    streak, occasion) + finish(readback, settings, assumptions,  │
              │    blocking_questions, unsupported). It never writes XML.       │
              ├───────────────────────────────────────────────────────────────┤
              │ 2. Validate (validate.py)  schema + every code/type/operator    │
              │    checked against the catalog → one repair round with the      │
              │    errors; still invalid → that primitive dropped + reported    │
              ├───────────────────────────────────────────────────────────────┤
              │ 3. Compile, deterministic (adapter.py, expander.py, rule_xml.py)│
              │    primitives → rules + offer counters (MA_O) → Blockly XML in  │
              │    the exact shape gravty-ui saves; grammar.py checks nesting   │
              └───────────────────────────────────────────────────────────────┘
                             │ pipeline.generate(): never empty
                             ▼
              XML + note (pinned comment on the offer block, ASCII-safe) + sidecar JSON
                             │
     cli.py: writes .xml/.json   pulsar.py: POST /ai/pulsar/offer/rules → {"result":"Generating"} … {"xml": base64}
                                           → stock UI loadBlocklyFileUpload → UI creates counters → user saves
```

The model decides *what* the offer means; deterministic code decides *how* it becomes blocks. That is why the
output always loads: the compiler only emits block shapes copied from what the UI itself saves and verified in the
real engine.

## Blocks

The reward-offer toolbox (`offer-rules.config.ts`) has 25 blocks. The compiler emits 15 of them:
`offer_generic activity activity_criteria rule rule_line if_generic_adaptive or action static_value
static_string_value arithmetic_generic arithmetic_date static_date activity_attribute member_attribute`, with
conditions on activity, member, product (line items) and offer counters, and actions give_points, give_privilege,
update counter (REGULAR and VECTOR). Offers that need the other 10 (payment rules, third-party actions, currency
conversion, airport pairs, date-time literals, streak events) are returned as `unsupported` with a reason, never as XML.

## Tests

```bash
.venv/bin/python -m pytest
```

```bash
AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live
```

```bash
.venv/bin/python -m pytest -m e2e
```

- Offline (171 passed, 89 xfailed on 2026-10-25): goldens byte-identical to engine-verified XML, 48 recorded model runs, validation, pipeline, catalog,
  Pulsar API, CLI.
- `live`: Bedrock. `test_live.py` (22 scenarios) and `test_block_coverage.py` (16 single-block, 6 combination,
  5 must-flag prompts; 28/28 on 2026-10-22, 4.8–12.4 s each).
- `e2e`: needs `ng serve` of gravty-ui on :4200 (`newui-develop/gravty-ui`, `npx ng serve --port 4200`) and the
  harness in `harness/` (or set `OFFER_AUTHOR_SPIKE` to another copy); runs the drafts through the unmodified upload
  path and the engine.

Design notes, results and error log: `~/Documents/Gravty/OfferAuthorPrototype.md`.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e '.[test]'
cd harness/ui-harness && npm ci            # puppeteer-core only; it drives your installed Chrome
```

Model: `OFFER_AUTHOR_MODEL` (default `us.anthropic.claude-opus-5-5`; `us.anthropic.claude-sonnet-5-5` is at parity for
rules, see `~/Documents/Gravty/ModelChoice-Sonnet55.md`).

## Harness (`harness/`)

Formerly `~/Downloads/offer-author-spike`, same layout so the tools run unchanged (`OFFER_AUTHOR_SPIKE` overrides):

| Path | What |
|---|---|
| `harness/ui-harness/` | puppeteer drivers: `mock-run.mjs` (stock rules screen, mocked bolapi), `segment-run.mjs`, `tenant-catalog.mjs` (catalog dump), `hosted-*.mjs` (signed-in debug Chrome on :9222) |
| `harness/engine-harness/` | Java harness that runs DRL + BITs in the real Drools engine. Build: `mvn -q package -DskipTests` (needs the engine fat jar in `~/.m2`, see its README); `target/` is not committed |
| `harness/results/` | run outputs (not committed) |

The hosted QA driver is the `rulesGenerationQA` Claude skill (`~/.claude/skills/rulesGenerationQA/`).

AWS: an SSO session for a profile with Bedrock access (`aws sso login --profile MLCoreDeveloperAccess-384853870836`).
