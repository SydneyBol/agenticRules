# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
the Offer Main fields, the member segment, and the Blockly rule XML that the stock rules screen uploads as it is (no UI
change). It replaces Pulsar behind the same `/ai/pulsar/...` API and gives Pulsar's answer: the rules and segment
endpoints return `{xml, raw, rule_id}` only.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
```

Contents:
[Quick start](#quick-start) · [Using the agent](#using-the-agent) · [How it runs](#how-it-runs) ·
[What the model reads](#what-the-model-reads) · [Settings](#settings) · [Reading a run](#reading-a-run) ·
[Layout](#layout) · [Outside this repo](#outside-this-repo) · [Development](#development) ·
[Troubleshooting](#troubleshooting)

## Quick start

1. Install the package and its dev tools into a local virtualenv:

   ```bash
   python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e '.[dev]'
   ```

2. Sign in to AWS SSO with a profile that has Bedrock access. The model calls go to Bedrock in us-west-2:

   ```bash
   aws sso login --profile MLCoreDeveloperAccess-384853870836
   ```

3. Point the agent at the tenant catalog dumps. A run reads the program's stores, tiers, privileges, accounts and
   attributes from them:

   ```bash
   export AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2
   export OFFER_AUTHOR_CATALOGS=~/Documents/Gravty/offer-xml-generator-dev/catalogs
   ```

4. Build one offer's rules from English. The rules XML goes to stdout or `-o`, and the run's notes go to stderr:

   ```bash
   .venv/bin/offer-xml "Members earn 2 points per AED 1 on spends of AED 50 or more at the Hyderabad store" \
     --tenant devintbf2 --sponsor 1 --account 9 --tag BASE -o hyderabad.xml
   ```

A new tenant needs a catalog dump first; see [Outside this repo](#outside-this-repo).

## Using the agent

### 1. The CLI: rules XML for one prompt or a batch

```bash
.venv/bin/offer-xml --batch prompts.txt --out-dir out/ --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
```

- **Input:** one prompt per line in `prompts.txt`; `#` comments are allowed.
- **Output:** each XML is written to `out/NN-<slug>.xml`.
- **Exit code:**
  - `0` for a draft, or for a run that needs input;
  - `2` when something is unsupported;
  - `1` on an error.
- **Scope:** the CLI runs the rules part only. There is no Offer Main, no segment and no engine check; use the service
  below for the whole offer.

### 2. The service: what the stock offer wizard calls

```bash
.venv/bin/offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE --port 8077
```

Point the gravty-ui Pulsar endpoints at `http://localhost:8077`. The service is Flask (`api/service.py`). Its endpoints
are in `api/pulsar/routes.py`:

| Screen | Call | What the service does |
|---|---|---|
| Create offer by prompt | `POST /ai/pulsar/<offer_id>/prompt/offermain/ {prompt, offer_status}` | stores the prompt and starts the whole-offer job (unless `offer_status` is `update`) |
| Offer Main | `POST /ai/pulsar/offer/metadata {prompt, category: "offer"}` | answers with the Offer Main fields as soon as the job's Offer Main run accepts them (about 3–8 s); segment and rules go on |
| Add segment (name) | `POST /ai/pulsar/offer/metadata {prompt, category: "segment"}` | the segment's name and description |
| Select Members (segment GenAI) | `POST /ai/pulsar/segment/rules {prompt, sponsor_id, rule_id}` | the segment XML from the offer job that wrote this prompt, else a segment-only run |
| Offer Rules (GenAI popup) | `POST /ai/pulsar/offer/rules/?regenerate=true {prompt, sponsor_id, id, type: "reward"}` | the rules XML, or `{"result": "Generating"}` after 20 s (the popup posts again) |
| Offer Rules (screen opens) | `POST /ai/pulsar/offer/rules/?regenerate=false {…}` | the offer's last rules answer, waiting up to 45 s for a running job; `{"xml": ""}` when there is none |
| Any stage's prompt | `GET /ai/pulsar/<offer_id>/prompt/<stage>/` | the prompt the popup shows: the manager's own edit, else the one the offer job wrote, else the Offer Main prompt; also warms the prompt cache |
| Health | `GET /ai/offer-author/health` | the model, its effort and thinking, how many earlier offers are shown, and the LLM calls taken of the limit |

- **Existing counters:**
  - A rules request may carry `existing_counters` and `current_rules_xml`, which the stock Upload Rules dialog sends.
  - With `--bolapi-base https://master.<tenant>.gravty.info`, the service reads the offer's counters itself with the
    caller's JWT.
  - An existing counter is reused only on an exact match; any other clash gets a free name (see
    [Counter check on delivery](#counter-check-on-delivery)).
- **Offer type:** only `type: "reward"` offers are built; anything else is a 400.
- **Engine check:** with `OFFER_AUTHOR_VERIFIER_URL` set, the whole-offer job is engine-checked before it answers; see
  [The engine check](#the-engine-check).

### 3. End to end on a tenant's catalog: the `createRealisticOffers` skill

To build a set of realistic offers for a program and see which are ready to save, use the Claude Code skill in
`~/.claude/skills/createRealisticOffers/`:

- **Services:** `scripts/services.sh <tenant> <sponsor> <account> <tag>` starts, in order:
  1. ng serve on :4200;
  2. the compile server on :8078;
  3. the verifier on :8079;
  4. this service on :8077 (`scripts/backend.sh`).
- **Offers:** `scripts/create_offers.py offers.yaml` drives each offer through the same calls as the stock wizard.
- **Compile and grade:** it compiles the XML through the stock rules and segment screens, and can grade expected BITs
  on the engine.
- **Report:** ready / not ready per offer, with latency, under
  `~/Documents/Gravty/offer-xml-generator-dev/results/offers/`.

Ask Claude Code to "create realistic offers for devintbf2", or follow the skill's `SKILL.md` by hand. The skill works
locally and never saves anything on a tenant.

### 4. In code

```python
from offer_author.ai import agent
from offer_author.ai.model import make_llm
from offer_author.core.settings import Settings
from offer_author.core.tenant.catalog import Catalog

settings = Settings.from_env()
catalog = Catalog.from_bolapi_dumps(f"{settings.catalogs}/devintbf2/bolapi", "devintbf2", 1, "9", "BASE")
res = agent.run("Gold members earn double points on weekend spends", catalog, make_llm(), account_id="9", settings=settings)
res["status"], res["offer_main"], res["segment"]["xml"], res["rules"]["xml"], res["diagnostics"]["verify"]
```

- With the default parts, `agent.run` builds the whole offer. It runs the engine check when `settings.verifier_url`
  is set.
- `parts=("rules",)` builds the rules only.

## How it runs

### The pieces

```
  stock gravty-ui wizard                     this repo (Python)                       QA / dev only
 ─────────────────────────       ───────────────────────────────────────       ──────────────────────────────
  Offer Main popup  ──────┐       api/service.py      Flask on :8077            verifier :8079 (offer-verifier)
  Select Members popup ───┼──►    api/pulsar/         routes, jobs, runs     ──►   compile: stock screens via
  Offer Rules popup  ─────┘           │                                             compile server :8078
                                      ▼                                             (puppeteer on ng serve :4200)
                                  ai/agent.py         the job graph                engine: the rules on mocked
                                  ai/graph.py         the build loop, the               members, real BIT checks
                                                      engine-checked run
                                  ai/cases.py         the test-case writer
                                  ai/tools.py         4 tools → core/run/
                                  core/canvas/        blocks → Blockly XML, checks
                                  core/tenant/        catalog, counters, history
                                      │
                                      ▼
                                  Bedrock (Sonnet 5.5) through ai/model.py, every call counted by ai/budget.py
```

The service holds no Java, no JS UI code and no compiled rules (DRL). The stock screens and the engine judge the XML
in the verifier, never inside the service. Dependencies point one way: `api → ai → core → domain`
(`tests/unit/test_layout.py` enforces it).

### Startup

`offer-xml-server` (`api/service.py main`):

1. reads `Settings.from_env()` once (`core/settings.py`). Nothing else reads the environment.
2. loads the tenant catalog from `<OFFER_AUTHOR_CATALOGS>/<tenant>/bolapi/*.json` (`core/tenant/catalog.py`);
3. starts two background warm-ups:
   - the value-search models and lists (`warm_values`);
   - the earlier offers' embeddings (`warm_history`);
4. builds the Bedrock client (`ai/model.py make_llm`) and the Flask app with the Pulsar blueprint.

After each run the catalog dumps are read again, so a new dump is served without a restart. Code changes need a
restart: the service has no auto-reload.

### A manager's offer, request by request

```
 manager                     wizard → service                                 job in the store (api/pulsar/jobs.py)
 ───────                     ───────────────────                              ─────────────────────────────────────
 types the offer prompt  ──► POST …/<id>/prompt/offermain/          ───────►  offer job starts (keyed by the prompt)
 Offer Main screen       ──► POST /offer/metadata {category: offer} ◄───────  answered when Offer Main is accepted
 Select Members popup    ──► GET  …/<id>/prompt/offersegmentation/  ◄───────  the segment prompt the job wrote
                         ──► POST /segment/rules                    ◄───────  the job's segment XML
 Offer Rules popup       ──► GET  …/<id>/prompt/offerrules/         ◄───────  the rules prompt the job wrote
                         ──► POST /offer/rules?regenerate=true      ◄───────  the job's rules XML, after the
                                                                              counter check on delivery
 edits a screen's prompt ──► POST …/<id>/prompt/<stage>/ + regenerate ─────►  a run for that canvas only, told what
                                                                              the rest of the offer already covers
```

- **One job per offer prompt.** The job builds all three parts; each screen reads its own part
  (`api/pulsar/runs.py offer_job`).
- **Edited prompts.** A rules or segment prompt the manager edits gets its own run for that canvas only
  (`rules_only`, `segment_job`). It is told what Offer Main and the segment already cover (`ai/prompt.py
  rules_context`), and reuses the attributes the offer job confirmed.
- **Jobs in memory** (`JobStore`):
  - a popup that posts again within 120 s gets the finished job instead of a new generation;
  - a finished job is dropped after 24 h.

### The whole-offer job (`ai/agent.py run`)

```
            ┌──► offer_main   Offer Main run, at most 3 turns: the Offer Main popup is answered from it
            ├──► attributes   (OFFER_AUTHOR_ATTRIBUTES, off) the catalog data the terms refer to
  START ────┼──► plan         (OFFER_AUTHOR_SHARED_PLAN, off) one plan that build and cases both read
            └──► earlier      at most 2 earlier offers of the tenant, as reference
                       │
                       ▼
                  prepared ── not an offer, or a question first ──► END (needs_input)
                       │
                       ▼
                  build        segment + rules, given the accepted Offer Main (and the plan or attributes)
                               = the engine-checked run below when a verifier is set
                       │
                       ▼
                      END      one answer: offer_main, segment, rules, notes, diagnostics
```

- **Offer Main first.** Offer Main is built first and on its own, so the Offer Main screen is answered before the
  rules are designed.
- **The build run.** The build run gets the accepted Offer Main as context (`offer_main_context`). Its dates and limits
  then hold for the engine check.
- **Rules-only and segment-only jobs** skip this graph. They make one run of the build loop (`_single`).

### The build loop (`ai/graph.py build`)

```
  START ──► model ──► tools ──► judge ──► model … ──► END
              │                   │
              └──► nudge ──► model   (a turn that called no tool is told to use them)
```

- **model:** one streamed Bedrock turn. With `early_tools`, each tool call starts as soon as it has streamed.
- **tools:** LangGraph's `ToolNode`. Each call is validated against its schema and runs as code (`core/run/`). The
  tools are:

  | Tool | What it does (code in `core/`) |
  |---|---|
  | `lookup_values` | catalog codes for the manager's words: stores, tiers, privileges, activity types, attribute values (`tenant/catalog.py`, `tenant/value_search.py`: by words, and by meaning with a bi-encoder plus a reranker) |
  | `set_offer_main` | the Offer Main fields, checked against the form (`run/offer_main.py`, `data/offer_main_form.json`) |
  | `compose` | blocks → Blockly XML for a canvas (`canvas/tree.py`, `build.py`, `edit.py`), checked with data: toolbox and connections (block reference), catalog codes, screen fields, counters (`canvas/checks/`) |
  | `finish` | ends the run; accepted only after a clean compose of every canvas it delivers (`run/policy.py`) |

- **judge:** reads the turn's results. `finish` is judged after the turn's other calls, and the run ends once it is
  accepted.
- **Limits:** the run ends at `OFFER_AUTHOR_TURNS` turns (default 20).
- **Typed results:** core code returns typed data (`domain/`: `Problem`, composed canvases), never English.
  `ai/render.py` words it for the model and `api/text.py` for the manager.
- **No mutations:** the model picks blocks, field values, catalog codes and counters; it never writes XML or
  mutations itself.

#### How a canvas is written (`core/canvas/`)

- **Tree:** the model's blocks are kept as a tree (`tree.py`) and edited in place (`edit.py`). An edit that picks
  catalog codes keeps one value: the picked codes, never a stale `value` field beside them.
- **XML:** `build.py to_xml` writes the stock screen's XML:
  - the rules start at x=10, y=10;
  - a block the model leaves loose (not attached to a rule, for example a hint the manager asked for) is written
    disabled (`MANUALLY_DISABLED`). It sits at x=10 below the rules and below the loose blocks before it (`rows`,
    `loose_ys`), so nothing overlaps on the canvas;
  - a comment always belongs to a block (`<comment>` inside the block); there are no workspace comments.
- **Counters:** the counters the rules need are declared from the blocks (`declare.py`) and checked
  (`checks/counters.py`).

### The engine check (`ai/graph.py build_verified`)

When `OFFER_AUTHOR_VERIFIER_URL` points at the verifier service (`~/PycharmProjects/offer-verifier`, port 8079), the
build of a whole-offer job runs as:

```
            ┌──► build   the build loop above, to its end
  START ────┤                                              ┌── ok, or no repair left, or canvases unchanged ──► END
            └──► cases   one turn writing test cases ──► verify
                                                           └── mismatch ──► build (repair: up to 4 more turns) ──► verify
```

1. **The two branches run at once.**
   - The build loop builds the canvases.
   - The test-case writer (`ai/cases.py write`) writes positive and negative BIT sequences from the manager's
     description alone, without seeing the blocks.
2. **Case checks in code.** Before the cases are used, code checks them:
   - **coverage:** with a shared plan, every plan id must be covered by a scenario;
   - **posting dates:** a test transaction can be posted from now (program time) up to 12 months ahead, at
     23:59:59 on the last day (`cases.window`). A member's own dates (joining, birthday) can be any date.

   If either check fails, the writer gets one more turn with only those scenarios. A scenario still undated after
   that turn is dropped, and recorded in `cases_info`.
3. **verify** (`core/verifier.py`). The service sends the verifier:
   - the clean canvases;
   - Offer Main's dates and limits;
   - the cases, with mocked members.

   The verifier compiles the canvases through the stock screens and runs every BIT on the engine. The service reads
   back what each transaction gave. Nothing is read from or written to a tenant.
4. **Repair.** On a mismatch, the build run gets a message with the failing transactions and the canvases' outline
   (`cases.mismatch_message`), plus up to `OFFER_AUTHOR_VERIFY_REPAIR_TURNS` (4) more turns.
   - A second repair is made only when the first one's recheck still fails (`OFFER_AUTHOR_VERIFY_REPAIRS`, 2).
   - No further repair is made when a repair left the canvases unchanged.
   - The cases are never rewritten to match the blocks.
5. **Answer.** The verdict, the cases and every check's answers are kept in `diagnostics.verify` and logged (see
   [Reading a run](#reading-a-run)).

The verifier needs the compile server (:8078), and the compile server needs ng serve (:4200).

### Counter check on delivery

The offer job builds its rules before the offer exists. When the rules screen asks for them, `Runs.deliver` checks the
counters the rules use against the offer's existing counters (`core/tenant/existing_counters.py Guard`):

- **Reuse:** a counter is reused only on an exact match of name, data type, counter type, namespace and level, on a
  planning offer.
- **Clash:** any other clash rebuilds the rules once with free names ("Total spend 2").
- **Report:** the answer's `counter_report` lists what was reused, renamed or left unused.

### The LLM call ledger (`ai/budget.py`)

Every request to Bedrock takes one call from a ledger file shared by every process on the machine: the service, the
CLI and the live tests. A call means each turn, each retry and each cache warm.

- **Ledger file:** `~/.offer_author/llm_calls.json`, or the path in `OFFER_AUTHOR_LLM_LEDGER`.
- **Hard limit:** `MAX_LLM_CALLS` = 3500, set in code. Once it is reached, every request fails with `LLMBudgetError`
  before it is sent.
- **Never reset by code:** raising the limit is a code change, recorded in the module docstring. A running service
  keeps the value it loaded, so restart it after a change.

## What the model reads

The system prompt (`ai/prompt.py`) is the same for every request to a tenant, so it is cached (`OFFER_AUTHOR_CACHE_TTL`).
It holds:

| Source | What it holds |
|---|---|
| `ai/context/block_reference.md`, `block_reference_segment.md` | every toolbox block of the rules and segment screens: fields, inputs, what each reads or writes. Generated by `tools/build/` from the gravty-ui sources |
| `ai/context/kt.md` | how the engine runs an offer, as a manager knows it. For example: the BIT processing order, date parts read as written, a missing date, a BIT more than 12 months ahead refused, the offer start as a date and time, `%` keeping decimals, no rounding block |
| `ai/context/conventions.md` | the usual readings of loyalty terms ("Nx points", "up to N", weekends, an amount above 0, a late BIT leaving the current period as it is), each one recorded as an assumption when used |
| the tenant catalog | accounts, tiers, activity types (inline up to `OFFER_AUTHOR_INLINE_ACTIVITY_TYPES`); other values are found with `lookup_values` |
| at most 2 earlier offers | the tenant's own launched offers closest to the request, shown as Pulsar sub-prompts (needs `OFFER_AUTHOR_HISTORY`) |

Rules for these files:

- They say what the data is, never how or when to use it.
- There are no example offers.
- The model never sees compiled rules (DRL), the backend or the UI source.

## Settings

Every setting is `OFFER_AUTHOR_<FIELD>`, read once by the CLI or the service (`core/settings.py`). A bool is off when
the value is `"0"`.

| Variable | Default | What it sets |
|---|---|---|
| `OFFER_AUTHOR_CATALOGS` | none | folder of tenant catalog dumps (`<tenant>/bolapi/*.json`) |
| `OFFER_AUTHOR_VERIFIER_URL` | none (no engine check) | verifier service, e.g. `http://127.0.0.1:8079` |
| `OFFER_AUTHOR_TURNS` | `20` | model turns per run |
| `OFFER_AUTHOR_VERIFY_REPAIR_TURNS` | `4` | turns each repair after a failed engine check may take |
| `OFFER_AUTHOR_VERIFY_REPAIRS` | `2` | repairs after failed checks; the second only when the first one's recheck fails |
| `OFFER_AUTHOR_SHARED_PLAN` | off | the engine-checked job first writes one plan that build and cases both read |
| `OFFER_AUTHOR_ATTRIBUTES` | off | the attribute step: the catalog data the terms refer to, confirmed once for build and cases |
| `OFFER_AUTHOR_HISTORY` | none | earlier offers per tenant (`<tenant>/offers.jsonl`), shown as reference |
| `OFFER_AUTHOR_SIMILAR_OFFERS` | `2` | how many earlier offers to show, at most 2 (`0` = off) |
| `OFFER_AUTHOR_EMBED_MODEL` | `cohere.embed-multilingual-v3` | Bedrock embeddings for matching a request to earlier offers |
| `OFFER_AUTHOR_VALUE_SEARCH` | on | `lookup_values` also matches by meaning (`0` = by words only) |
| `OFFER_AUTHOR_VALUE_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | value-search bi-encoder |
| `OFFER_AUTHOR_VALUE_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | value-search cross-encoder |
| `OFFER_AUTHOR_INLINE_ACTIVITY_TYPES` | `200` | activity types listed in the prompt up to this many |
| `OFFER_AUTHOR_EARLY_TOOLS` | on | start each tool call as soon as it has streamed |
| `OFFER_AUTHOR_CACHE_TTL` | `1h` | prompt-cache lifetime of the shared prefix (`1h` or `5m`) |
| `OFFER_AUTHOR_TRACE_INPUTS` | off | debugging: keep each tool call's input in the run's trace |

The model client reads its own variables (`ai/model.py make_llm`):

| Variable | Default | What it sets |
|---|---|---|
| `OFFER_AUTHOR_MODEL` | `us.anthropic.claude-sonnet-5-5` | Bedrock model (`us.anthropic.claude-opus-5-5` for Opus) |
| `OFFER_AUTHOR_EFFORT` | `low` | the model's effort |
| `OFFER_AUTHOR_THINKING` | the model's own (adaptive) | thinking type (`adaptive`, or `between_tools`) |
| `OFFER_AUTHOR_LLM_LEDGER` | `~/.offer_author/llm_calls.json` | the call ledger file |

AWS credentials come from `AWS_PROFILE` (an SSO profile) and `AWS_REGION` (default `us-west-2`), never from keys in a
file.

## Reading a run

The service logs one set of lines per finished job:

| Line | What |
|---|---|
| `pulsar job offer=<id> status=… latency_s=… turns=… tokens_… per_turn=[…] verify={…} problems=…` | the job: status, time, tokens, cache use, where the time went, the engine check's verdict and case counts |
| `pulsar answer offer=<id> part=<rules\|segment> {…}` | what the run says about its XML: status, assumptions, questions, unsupported items, readback, counters |
| `pulsar cases offer=<id> {"cases": […], "checks": […]}` | the agent's test cases and, for each check, every transaction with the engine's answer |
| `pulsar repair offer=<id> {…}` | after a mismatch: each check's mismatches, the repair messages, the canvases before the first |

The CLI writes the `pulsar answer` notes to stderr. A run's `status` is one of:

- `draft`: built;
- `needs_input`: a blocking question, for example a name that matches two catalog codes;
- `unsupported`: something the screens cannot hold;
- `error`.

The assumptions say what the agent decided for the manager, for example the dates left to Offer Main or a usual
reading from `conventions.md`.

Design notes for each part: `~/Documents/Gravty/ai-agent/` (`AIRulesAgent-Design.md`, `OfferMainFirst.md`,
`TwoBranchVerify.md`, `DecoupledService.md`, `SharedPlan.md`, `AttributeGrounding.md`, `AgentVsBaselines.md`).

## Layout

| Path | What |
|---|---|
| `offer_author/domain/` | typed data every layer shares: `Problem`, block and result types |
| `offer_author/core/canvas/` | the block tree (`tree.py`), XML writing (`build.py`), edits (`edit.py`), counter declarations (`declare.py`), field and block reference (`fields.py`, `reference.py`), checks (`checks/`: grammar, screen fields, counters) |
| `offer_author/core/run/` | the tools as code (`run.py`, `lookups.py`, `offer_main.py`, `canvases.py`), the finish policy (`policy.py`), the run's outcome (`outcome.py`) |
| `offer_author/core/tenant/` | the catalog, value search, existing counters and the counter guard, earlier offers (`history.py`, `embed.py`), the harvester's store |
| `offer_author/core/data/` | `block_reference.json`, `offer_main_form.json`, saved blueprints and segments |
| `offer_author/core/` | also `settings.py` (every setting), `verifier.py` (the engine-check request), `vocabulary.py` |
| `offer_author/ai/` | `agent.py` (the job), `graph.py` (the build loop, the engine-checked run), `model.py` (Bedrock client), `budget.py` (call ledger), `tools.py`, `cases.py` (test cases), `plan.py`, `attributes.py`, `prompt.py`, `render.py`, `context/*.md` |
| `offer_author/api/` | `service.py` (HTTP), `pulsar/` (routes, jobs, runs, payloads), `cli.py`, `text.py` (words for the manager), `harvest.py` (`offer-harvest`: earlier offers from bolapi) |
| `tests/` | `unit/` (offline, the default run), `e2e/` (value search on the dev workspace's catalogs), `live/` (Bedrock); `fakes/` (a scripted model), `support/`, `fixtures/`, `golden/`, `cases/` |
| `tools/build/` | generators of `ai/context/block_reference*.md`, `kt.md` and `core/data/*.json` from the gravty-ui and bolapi sources |
| `vendor/pulsar_rag/` | Pulsar's sub-prompt code, as reviewed, used by `offer-harvest` (the `pulsar` extra) |

Console scripts (`pyproject.toml`): `offer-xml` (CLI), `offer-xml-server` (service), `offer-harvest` (earlier offers).

## Outside this repo

The repo holds the service, its tests and its generators. Everything else it needs or produces lives outside it and is
found through the setting in the last column; none of it is committed here.

| What | Where | How it is found |
|---|---|---|
| Tenant catalog dumps (`<tenant>/bolapi/*.json`, written by `tenant-catalog.mjs` through a signed-in tenant tab) | `~/Documents/Gravty/offer-xml-generator-dev/catalogs/` | `OFFER_AUTHOR_CATALOGS` for the service and the CLI (`--catalog-dir` overrides it) |
| The verifier (stock compile + engine, mocked members) | `~/PycharmProjects/offer-verifier` | `OFFER_AUTHOR_VERIFIER_URL` |
| QA: the compile service (`harness/ui/compile/`), the engine harness (`harness/engine/`, Java), QA tools, batch and hosted scripts and every output | `~/Documents/Gravty/offer-xml-generator-dev/` (its README) | they point back at this checkout (`OFFER_AUTHOR_REPO`); the e2e and live suites find it with `OFFER_AUTHOR_DEV` (`tests/paths.py`) |
| Design notes, decisions, error logs, KT PDF | `~/Documents/Gravty/` (index: `README.md`; this branch: `ai-agent/`) | read by people only |
| Offer-building runbook and its scripts | `~/.claude/skills/createRealisticOffers/` | `scripts/backend.sh` sets `OFFER_AUTHOR_CATALOGS` |
| gravty-ui, served for the compile screen | `~/Downloads/newui-develop/gravty-ui` | `ng serve` on :4200 |
| Local dev servers | `~/Downloads/engine-develop/.claude/launch.json` | the `compile-server` entries |

To dump a new tenant's catalog:

1. Sign in to `https://master.<tenant>.gravty.info` in the debug Chrome (port 9222). The skill's
   `scripts/chrome.sh <tenant>` opens it.
2. Run:

   ```bash
   cd ~/Documents/Gravty/offer-xml-generator-dev/harness/ui/hosted && node tenant-catalog.mjs --tenant=<tenant> --sponsor=<id> --account=<id>
   ```

## Development

Run the offline unit suite (the default; `pyproject.toml` addopts hold the marker filter, so do not override them):

```bash
.venv/bin/python -m pytest
```

Lint, format and type checks:

```bash
.venv/bin/ruff check offer_author tests tools && .venv/bin/ruff format --check offer_author tests && .venv/bin/mypy
```

Live tests call Bedrock, cost money and take calls from the ledger. Run one suite at a time, never alongside a hosted
run:

```bash
.venv/bin/python -m pytest tests/live
```

- **Restart after changes:**
  - Restart the service after any change to `offer_author/`, including `MAX_LLM_CALLS`.
  - Restart the verifier after an engine-harness `mvn package`.
  - Restart the compile server if it started before :4200 answered.
- **Context files:** prompts, context files and tool descriptions say what the data is, never how or when to use it.
  The model never sees DRL.
- **Changing what the model does:** write a design note in `~/Documents/Gravty/ai-agent/` first.

## Troubleshooting

| Symptom | Cause | Do |
|---|---|---|
| every call 5xx, Bedrock `ExpiredToken` / `APIConnectionError` | SSO session expired, or two live suites at once | `aws sso login --profile …`, restart the service; run one suite at a time |
| `LLMBudgetError`, "budget spent: N of N" | the call ledger reached `MAX_LLM_CALLS` | the limit is raised only by the owner, in code; then restart the service |
| `give --catalog-dir or OFFER_AUTHOR_CATALOGS` | no catalog folder | export `OFFER_AUTHOR_CATALOGS` or pass `--catalog-dir` |
| `needs_input` with a question like "Gold tier or Gold test tier?" | the catalog has two values with that name | name the code's own label in the prompt |
| `verify` shows `skipped` or `error` | verifier down, it cannot reach the compile server, or the run did not finish | start :4200, then :8078, then :8079 (`services.sh` does it in order) |
| engine check "could not run the rules: None" | the verifier holds a replaced engine-harness jar | restart the verifier |
| compile server 503, "screen did not open" | its pages opened while :4200 was down | restart the compile server once :4200 answers |
| a test transaction refused `REWARD_OFFER_DOES_NOT_QUALIFY` | BIT before the offer's start, which is a date and a time (earlier the same day is refused) | date test BITs inside the offer; the case, not the offer, is wrong |
| a test transaction refused `VALIDATION_FAILED` | BIT more than 12 months after today | date test BITs within 12 months |
| popup shows "Generating" for long | the job is still running (20 s hold, then the popup posts again) | wait; the `pulsar job` line shows when it ended |

More, with history: the error logs in `~/Documents/Gravty/` and the skill's known-issues table.
