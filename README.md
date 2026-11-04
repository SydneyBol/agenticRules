# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
Offer Main fields, the member segment, and the Blockly rule XML the stock rules screen uploads as is (no UI change).
It replaces Pulsar behind the same `/ai/pulsar/...` API, with Pulsar's answer: the rules and segment endpoints return
`{xml, raw, rule_id}` only.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
```

Contents: [Quick start](#quick-start) · [Using the agent](#using-the-agent) · [Settings](#settings) ·
[Reading a run](#reading-a-run) · [How it works](#how-it-works) · [Layout](#layout) · [Outside this repo](#outside-this-repo) ·
[Development](#development) · [Troubleshooting](#troubleshooting)

## Quick start

Install the package and its dev tools into a local virtualenv:

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e '.[dev]'
```

Sign in to AWS SSO with a profile that has Bedrock access. The model calls go through Bedrock in us-west-2:

```bash
aws sso login --profile MLCoreDeveloperAccess-384853870836
```

Point the agent at the tenant catalog dumps. A run reads the program's stores, tiers, privileges, accounts and
attributes from them:

```bash
export AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2
export OFFER_AUTHOR_CATALOGS=~/Documents/Gravty/offer-xml-generator-dev/catalogs
```

Build one offer's rules from English (rules XML on stdout or `-o`, the run's notes on stderr):

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

- One prompt per line in `prompts.txt` (`#` comments allowed). Each XML is written to `out/NN-<slug>.xml`.
- The exit code is `0` for a draft or a run that needs input, `2` when something is unsupported, and `1` on an error.
- The CLI runs the rules part only: no Offer Main, no segment and no engine check. Use the service below for the whole
  offer.

### 2. The service: what the stock offer wizard calls

```bash
.venv/bin/offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE --port 8077
```

Point the gravty-ui Pulsar endpoints at `http://localhost:8077`. A manager's flow through the stock screens maps to
these calls:

| Screen | Call | What the agent does |
|---|---|---|
| Create offer by prompt | `POST /ai/pulsar/<offer_id>/prompt/offermain/ {prompt, offer_status}` | stores the prompt and starts the whole-offer job: Offer Main first, then the segment and the rules |
| Offer Main | `POST /ai/pulsar/offer/metadata {prompt, sponsor_id, id}` | answers with the Offer Main fields as soon as the Offer Main run accepts them (about 3–8 s) |
| Select Members (segment GenAI) | `POST /ai/pulsar/segment/rules {prompt, sponsor_id, rule_id}` | the segment XML from the same job |
| Offer Rules (GenAI popup) | `POST /ai/pulsar/offer/rules/?regenerate=true {prompt, sponsor_id, id, type: "reward"}` | the rules XML; `regenerate=false` returns the job's last answer |
| Any stage's stored prompt | `GET /ai/pulsar/<offer_id>/prompt/<stage>/` | the prompt the popup shows; also warms the prompt cache |
| Health | `GET /ai/offer-author/health` | |

- A rules request may carry `existing_counters` and `current_rules_xml`, which the stock Upload Rules dialog sends.
  `--bolapi-base https://master.<tenant>.gravty.info` makes the service read the offer's counters itself with the
  caller's JWT. Existing counters are reused only on an exact match; any other clash gets a free name.
- Only `type: "reward"` offers are built.
- With `OFFER_AUTHOR_VERIFIER_URL` set, the whole-offer job is engine-checked before it answers; see
  [The engine check](#the-engine-check).

### 3. End to end on a tenant's catalog: the `createRealisticOffers` skill

To build a set of realistic offers for a program and see which are ready to save, use the Claude Code skill in
`~/.claude/skills/createRealisticOffers/`. It does four things:

- starts every service in order: `services.sh <tenant> <sponsor> <account> <tag>` brings up ng serve :4200, the compile
  server :8078, the verifier :8079 and this service on :8077;
- drives each offer through the same calls as the stock wizard (`scripts/create_offers.py offers.yaml`);
- compiles the XML through the stock rules and segment screens, and optionally grades expected BITs on the engine;
- reports ready / not ready per offer with latency, under `~/Documents/Gravty/offer-xml-generator-dev/results/offers/`.

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

`agent.run` with the default parts builds the whole offer, and runs the engine check when `settings.verifier_url` is
set. `parts=("rules",)` builds the rules only.

### The engine check

When `OFFER_AUTHOR_VERIFIER_URL` points at the verifier service (`~/PycharmProjects/offer-verifier`, port 8079), a
whole-offer job runs two branches at once:

- the build;
- a test-case writer, which writes positive and negative BIT sequences from the manager's description.

The verifier then compiles the canvases through the stock screens and runs the cases on the engine with a mocked
member. These are the same test cases as stock Offer Testing, with no DynamoDB calls. On a mismatch the build gets one
repair, which may fix the blocks; the cases are never rewritten. The verifier needs the compile server (:8078), and
the compile server needs ng serve (:4200).

## Settings

Every setting is `OFFER_AUTHOR_<FIELD>`, read once by the CLI or the service (`core/settings.py`):

| Variable | Default | What it sets |
|---|---|---|
| `OFFER_AUTHOR_CATALOGS` | none | folder of tenant catalog dumps (`<tenant>/bolapi/*.json`) |
| `OFFER_AUTHOR_MODEL` | `us.anthropic.claude-sonnet-5-5` | Bedrock model (`us.anthropic.claude-opus-5-5` for Opus) |
| `OFFER_AUTHOR_VERIFIER_URL` | none (no engine check) | verifier service, e.g. `http://127.0.0.1:8079` |
| `OFFER_AUTHOR_HISTORY` | none | earlier offers per tenant (`<tenant>/offers.jsonl`), shown as reference |
| `OFFER_AUTHOR_SIMILAR_OFFERS` | `2` | how many earlier offers to show, at most 2 (`0` = off) |
| `OFFER_AUTHOR_TURNS` | `20` | model turns per run |
| `OFFER_AUTHOR_VERIFY_REPAIR_TURNS` | `4` | turns the one repair after a failed engine check may take |
| `OFFER_AUTHOR_CACHE_TTL` | `1h` | prompt-cache lifetime of the shared prefix (`1h` or `5m`) |
| `OFFER_AUTHOR_VALUE_SEARCH` | on | `lookup_values` also matches by meaning (`0` = by words only) |
| `OFFER_AUTHOR_INLINE_ACTIVITY_TYPES` | `200` | activity types listed in the prompt up to this many |
| `OFFER_AUTHOR_TRACE_INPUTS` | off | debugging: log each tool call's input |

AWS credentials come from `AWS_PROFILE` (an SSO profile) and `AWS_REGION`, never from keys in a file.

## Reading a run

The service logs one set of lines per finished job:

| Line | What |
|---|---|
| `pulsar job offer=<id> status=… latency_s=… turns=… tokens_… per_turn=[…] verify={…}` | the job: status, time, tokens, cache use, where the time went, the engine check's verdict and case counts |
| `pulsar answer offer=<id> part=<rules\|segment> {…}` | what the run says about its XML: status, assumptions, questions, unsupported items, readback, counters |
| `pulsar cases offer=<id> {"cases": […], "checks": […]}` | the agent's test cases and, for each check, every transaction with the engine's answer |
| `pulsar repair offer=<id> {…}` | after a mismatch: both checks' mismatches, the repair message, the canvases before it |

The CLI writes the `pulsar answer` notes to stderr. A run's `status` is one of:

- `draft`: built;
- `needs_input`: a blocking question, for example a name that matches two catalog codes;
- `unsupported`: something the screens cannot hold;
- `error`.

The assumptions say what the agent decided for the manager, for example dates left to Offer Main.

## How it works

A Python service, driven by context and AI: one Claude agent (Bedrock, Sonnet 5.5 by default) builds the offer with
tools, and code writes every block and checks that it fits. There is no Java, no JS UI code and no compiled rules (DRL)
here: the stock screen and the engine judge the XML in QA (the dev workspace), never inside the service.

```
 English prompt ──► ai/agent.py   one run per offer (Offer Main, segment, rules); the loop is a LangGraph graph
                      │            (ai/graph.py: model -> tools -> judge), the model client is ai/model.py
                      │  reads: ai/prompt.py + ai/context (block reference, how the engine runs offers), the tenant
                      │         catalog, at most two earlier offers of the tenant as reference
                      ▼
                   ai/tools.py    the tools the model calls; each runs as code in core/run/
                      lookup_values   catalog codes for the manager's words (core/tenant/catalog.py, value_search.py)
                      set_offer_main  Offer Main fields, checked
                      compose         blocks -> XML (core/canvas/build.py, edit.py), checked with data: toolbox and
                                      connections (block reference), catalog codes, screen fields, counters
                                      (core/canvas/checks/)
                      finish          only after a clean compose of every canvas it delivers (core/run/policy.py)
                      ▼
                   api/pulsar/    the Pulsar-compatible endpoints the stock wizard calls; api/service.py, api/cli.py
```

- **Whole-offer job:** an Offer Main run answers the Offer Main screen first. A build run then makes the segment and
  the rules from Offer Main's segment and rules prompts. With the verifier set, the build runs in parallel with the
  test-case branch (`ai/graph.py build_verified`).
- **Typed core:** core code returns typed data (`domain/`: `Problem`, composed canvases), never English. `ai/render.py`
  words it for the model and `api/text.py` for the manager. The model picks blocks and field values; it never writes
  mutations.

Design: `~/Documents/Gravty/ai-agent/AIRulesAgent-Design.md`, `OfferMainFirst.md`, `TwoBranchVerify.md`,
`DecoupledService.md`.

## Layout

| Path | What |
|---|---|
| `offer_author/domain/` | typed data every layer shares: `Problem`, block and result types |
| `offer_author/core/` | deterministic code: `canvas/` (tree, XML, block compatibility checks), `tenant/` (catalog, existing counters, earlier offers, value search), `run/` (the tools as code, run policy, outcome), `data/` (block reference, saved blueprints and segments), `verifier.py` (the engine-check request) |
| `offer_author/ai/` | what the model reads and calls: agent, graph, model client, tools, test cases, prompt, render, `context/*.md` |
| `offer_author/api/` | the Pulsar endpoints (`pulsar/`), the HTTP service, the CLI, the history harvester. Dependencies point one way: api → ai → core → domain (`tests/unit/test_layout.py`) |
| `tests/` | `unit/` (offline, the default run), `e2e/` (value search on the dev workspace's catalogs), `live/` (Bedrock); `fakes/` (a scripted model), `support/`, `fixtures/`, `golden/`, `cases/` (case files the suites and the dev workspace's QA tools read) |
| `tools/build/` | generators of `ai/context/block_reference*.md`, `kt.md` and `core/data/*.json` from the gravty-ui and bolapi sources |
| `vendor/pulsar_rag/` | Pulsar's sub-prompt code, as reviewed, used by `offer-harvest` (the `pulsar` extra) |

## Outside this repo

The repo holds the service, its tests and its generators. Everything else it needs or produces lives under
`~/Documents/Gravty/` (or the place named below) and is found through the setting in the last column; none of it is
committed here.

| What | Where | How it is found |
|---|---|---|
| Tenant catalog dumps (`<tenant>/bolapi/*.json`, written by the dev workspace's `harness/ui/hosted/tenant-catalog.mjs` through a signed-in tenant tab) | `~/Documents/Gravty/offer-xml-generator-dev/catalogs/` | `OFFER_AUTHOR_CATALOGS` for the service and the CLI (`--catalog-dir` overrides it); production reads bolapi live |
| The verifier (stock compile + engine, mocked member) | `~/PycharmProjects/offer-verifier` | `OFFER_AUTHOR_VERIFIER_URL` |
| QA: the stock-screen compile service (`harness/ui/compile/`), the engine harness (`harness/engine/`, Java), QA tools, QA's stock-screen and engine tests (`qa_tests/`), batch and hosted scripts and every output | `~/Documents/Gravty/offer-xml-generator-dev/` (its README) | they point back at this checkout (`OFFER_AUTHOR_REPO`, default `~/PycharmProjects/offer-xml-generator`); the e2e and live suites find it with `OFFER_AUTHOR_DEV` (`tests/paths.py`) |
| Design notes, decisions, error logs, KT PDF | `~/Documents/Gravty/` (index: `README.md`; this branch: `ai-agent/`) | read by people only |
| Offer-building runbook and its scripts | `~/.claude/skills/createRealisticOffers/` | `scripts/backend.sh` sets `OFFER_AUTHOR_CATALOGS` |
| gravty-ui, served for QA's compile screen | `~/Downloads/newui-develop/gravty-ui` | `ng serve` on :4200 |
| Local dev servers (QA) | `~/Downloads/engine-develop/.claude/launch.json` | `compile-server` entries run the dev workspace's compile service |

A new tenant's catalog: sign in to `https://master.<tenant>.gravty.info` in the debug Chrome (port 9222; the
skill's `scripts/chrome.sh <tenant>` opens it), then:

```bash
cd ~/Documents/Gravty/offer-xml-generator-dev/harness/ui/hosted && node tenant-catalog.mjs --tenant=<tenant> --sponsor=<id> --account=<id>
```

## Development

Run the offline unit suite (the default):

```bash
.venv/bin/python -m pytest
```

Lint, format and type checks:

```bash
.venv/bin/ruff check offer_author tests tools && .venv/bin/ruff format --check offer_author tests && .venv/bin/mypy
```

Live tests call Bedrock and cost money; run one suite at a time, never alongside a hosted run:

```bash
.venv/bin/python -m pytest tests/live
```

- **Restart after changes:** restart the service after any change to `offer_author/` (it has no auto-reload). Restart
  the verifier after an engine-harness `mvn package`. Restart the compile server if it started before :4200 answered.
- **Prompts and tool descriptions:** they say what the data is, never how or when to use it. The model never sees DRL.
- **Changing what the model does:** write a design note in `~/Documents/Gravty/ai-agent/` first.

## Troubleshooting

| Symptom | Cause | Do |
|---|---|---|
| every call 5xx, Bedrock `ExpiredToken` / `APIConnectionError` | SSO session expired, or two live suites at once | `aws sso login --profile …`, restart the service; run one suite at a time |
| `give --catalog-dir or OFFER_AUTHOR_CATALOGS` | no catalog folder | export `OFFER_AUTHOR_CATALOGS` or pass `--catalog-dir` |
| `needs_input` with a question like "Gold tier or Gold test tier?" | the catalog has two values with that name | name the code's own label in the prompt |
| `verify` shows `skipped` or `error` | verifier down, or it cannot reach the compile server | start :4200, then :8078, then :8079 (`services.sh` does it in order) |
| engine check "could not run the rules: None" | the verifier holds a replaced engine-harness jar | restart the verifier |
| compile server 503, "screen did not open" | its pages opened while :4200 was down | restart the compile server once :4200 answers |
| a test transaction refused `REWARD_OFFER_DOES_NOT_QUALIFY` | BIT before Offer Main's start date (or a UTC time that is the next day in program time) | use dates inside the offer; the case, not the offer, is wrong |

More, with history: the error logs in `~/Documents/Gravty/` and the skill's known-issues table.
