# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
Offer Main fields, the member segment, and the Blockly rule XML the stock rules screen uploads as is (no UI change).
It replaces Pulsar behind the same `/ai/pulsar/...` API, with Pulsar's answer: the rules and segment endpoints return
`{xml, raw, rule_id}` only. What a run says about its XML (status, assumptions, questions, unsupported items, readback,
counters) is logged as `pulsar answer offer=<id> part=<rules|segment> <json>`; the CLI writes the XML and logs the same
notes to stderr.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
```

## How it works

A Python service, driven by context and AI: one Claude agent (Bedrock, Sonnet 5.5 by default) builds the offer with
tools; code writes every block and checks that it fits. No Java, no JS UI code and no compiled rules (DRL) here: the
stock screen and the engine judge the XML in QA (the dev workspace), never inside the service.

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

Core code returns typed data (`domain/`: `Problem`, composed canvases), never English: `ai/render.py` words it for
the model and `api/text.py` for the manager. The model picks blocks and field values; it never writes mutations.
Design: `~/Documents/Gravty/ai-agent/AIRulesAgent-Design.md`, `DecoupledService.md`.

## Layout

| Path | What |
|---|---|
| `offer_author/domain/` | typed data every layer shares: `Problem`, block and result types |
| `offer_author/core/` | deterministic code: `canvas/` (tree, XML, block compatibility checks), `tenant/` (catalog, existing counters, earlier offers, value search), `run/` (the tools as code, run policy, outcome), `data/` (block reference, saved blueprints and segments) |
| `offer_author/ai/` | what the model reads and calls: agent, graph, model client, tools, prompt, render, `context/*.md` |
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
| Tenant catalog dumps (`<tenant>/bolapi/*.json`, written by the dev workspace's `harness/ui/hosted/tenant-catalog.mjs`) | `~/Documents/Gravty/offer-xml-generator-dev/catalogs/` | `OFFER_AUTHOR_CATALOGS` for the service and the CLI (`--catalog-dir` overrides it); production reads bolapi live |
| QA: the stock-screen compile service (`harness/ui/compile/`), the engine harness (`harness/engine/`, Java), QA tools, QA's stock-screen and engine tests (`qa_tests/`), batch and hosted scripts and every output | `~/Documents/Gravty/offer-xml-generator-dev/` (its README) | they point back at this checkout (`OFFER_AUTHOR_REPO`, default `~/PycharmProjects/offer-xml-generator`); the e2e and live suites find it with `OFFER_AUTHOR_DEV` (`tests/paths.py`) |
| Design notes, decisions, error logs, KT PDF | `~/Documents/Gravty/` (index: `README.md`; this branch: `ai-agent/`) | read by people only |
| Hosted QA runbook and its scripts | `~/.claude/skills/rulesGenerationQA/` | `scripts/backend.sh` sets `OFFER_AUTHOR_CATALOGS` |
| gravty-ui, served for QA's compile screen | `~/Downloads/newui-develop/gravty-ui` | `ng serve` on :4200 |
| Local dev servers (QA) | `~/Downloads/engine-develop/.claude/launch.json` | `compile-server` entries run the dev workspace's compile service |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e '.[dev]'
```

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/ruff check offer_author tests tools && .venv/bin/mypy
```

AWS: an SSO session for a profile with Bedrock access (`aws sso login --profile MLCoreDeveloperAccess-384853870836`).
Model: `OFFER_AUTHOR_MODEL` (default `us.anthropic.claude-sonnet-5-5`). Export `OFFER_AUTHOR_CATALOGS` for the service
and the CLI.

Hosted QA on a tenant is the `rulesGenerationQA` Claude skill (`~/.claude/skills/rulesGenerationQA/`). Results and
error logs: `~/Documents/Gravty/`.
