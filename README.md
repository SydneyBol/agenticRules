# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
Offer Main fields, the member segment, and the Blockly rule XML the stock rules screen uploads as is (no UI change).
It replaces Pulsar behind the same `/ai/pulsar/...` API.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml-server --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
```

## How it works

One Claude agent (Bedrock, Sonnet 5.5 by default) builds the offer with tools; code writes and checks every block.

```
 English prompt ──► ai/agent.py  one tool loop per offer (Offer Main, segment, rules)
                      │  reads: ai/context (block reference, how the engine runs offers), the tenant catalog,
                      │         the product's saved blueprints and segments as block selections
                      ▼
                   core/session.py  the tools
                      lookup_values   catalog codes for the manager's words
                      set_offer_main  Offer Main fields, checked
                      validate        selections -> XML (core/tree.py), the UI's own checks (core/check.py)
                      compile         the stock screen, headless (core/ui.py -> harness/ui/compile/compile-server.mjs):
                                      what it dropped or changed, whether it generated rules, the counters it created
                      simulate        the real engine on the model's BITs (core/engine.py -> harness/engine)
                      finish          only after a clean compile of every canvas it delivers
                      ▼
                   api/pulsar.py  the Pulsar-compatible endpoints the stock wizard calls; api/cli.py
```

The model picks blocks and field values; it never writes mutations, never sees compiled rules (DRL), and never gets
an XML through that the stock screen did not accept. Design: `~/Documents/Gravty/ai-agent/AIRulesAgent-Design.md`.

## Layout (only what runs)

| Path | What |
|---|---|
| `offer_author/ai/` | model-facing: the loop, prompt, tool schemas, the Bedrock client (`llm.py`: AnthropicBedrock, streamed), `context/*.md` |
| `offer_author/core/` | deterministic: session (the tools), tree, checks, compile and engine clients, catalog, counters, `data/` (block reference, saved blueprint and segment XML) |
| `offer_author/api/` | HTTP service and CLI. Dependencies point one way: api → ai → core |
| `harness/ui/compile/` | the `compile` tool's service: stock gravty-ui screens, headless, against a mocked backend |
| `harness/engine/` | the `simulate` tool: Java harness that runs rules + BITs in the real Drools engine (`mvn -q package -DskipTests`) |

## Outside this repo

The repo holds only what runs. Everything it needs or produces besides that lives under `~/Documents/Gravty/` (or
the place named below) and is found through the setting in the last column; none of it is committed here.

| What | Where | How it is found |
|---|---|---|
| Tenant catalog dumps (`<tenant>/bolapi/*.json`, written by the dev workspace's `harness/ui/hosted/tenant-catalog.mjs`) | `~/Documents/Gravty/offer-xml-generator-dev/catalogs/` | `OFFER_AUTHOR_CATALOGS` for the service, the CLI and the compile server (`--catalog-dir` overrides it for the service and CLI); production reads bolapi live |
| Tests, QA tools, batch and hosted scripts, case files and every output | `~/Documents/Gravty/offer-xml-generator-dev/` (its README) | they point back at this checkout (`OFFER_AUTHOR_REPO`, default `~/PycharmProjects/offer-xml-generator`) |
| Design notes, decisions, error logs, KT PDF | `~/Documents/Gravty/` (index: `README.md`; this branch: `ai-agent/`) | read by people only |
| Hosted QA runbook and its scripts | `~/.claude/skills/rulesGenerationQA/` | `scripts/backend.sh` sets `OFFER_AUTHOR_CATALOGS` |
| gravty-ui, served for the compile screen | `~/Downloads/newui-develop/gravty-ui` | `ng serve` on :4200 (`APP` in `harness/ui/compile/screen.mjs`) |
| Gravty engine jar the harness runs | `~/.m2/repository/com/gravty/bit_processor/` | `engine.jar` in `harness/engine/pom.xml` (`-Dengine.jar=` overrides) |
| Local dev servers | `~/Downloads/engine-develop/.claude/launch.json` | `compile-server` entries set `OFFER_AUTHOR_CATALOGS` |

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e .
```

```bash
cd harness/ui && npm ci
```

AWS: an SSO session for a profile with Bedrock access (`aws sso login --profile MLCoreDeveloperAccess-384853870836`).
Model: `OFFER_AUTHOR_MODEL` (default `us.anthropic.claude-sonnet-5-5`). The compile service needs `ng serve` of
gravty-ui on :4200, then `OFFER_AUTHOR_CATALOGS=~/Documents/Gravty/offer-xml-generator-dev/catalogs npm --prefix harness/ui run compile-server` (:8078).
Export the same `OFFER_AUTHOR_CATALOGS` for the service and the CLI.

Hosted QA on a tenant is the `rulesGenerationQA` Claude skill (`~/.claude/skills/rulesGenerationQA/`). Results and
error logs: `~/Documents/Gravty/`.
