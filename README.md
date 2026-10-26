# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
Offer Main fields, the member segment, and the Blockly rule XML the stock rules screen uploads as is (no UI change).
It replaces Pulsar behind the same `/ai/pulsar/...` API.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml-server --catalog-dir catalogs/devintbf2/bolapi --tenant devintbf2 --sponsor 1 --account 9 --tag BASE
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
| `catalogs/<tenant>/bolapi/` | tenant catalog dumps (the CLI, the local backend, the compile screen's catalog calls); production reads bolapi live |

Tests, QA tools, batch and hosted scripts, case files and their outputs live in the dev workspace
`~/Documents/Gravty/offer-xml-generator-dev/` (see its README); they run against this checkout.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e .
```

```bash
cd harness/ui && npm ci
```

AWS: an SSO session for a profile with Bedrock access (`aws sso login --profile MLCoreDeveloperAccess-384853870836`).
Model: `OFFER_AUTHOR_MODEL` (default `us.anthropic.claude-sonnet-5-5`). The compile service needs `ng serve` of
gravty-ui on :4200, then `npm --prefix harness/ui run compile-server` (:8078).

Hosted QA on a tenant is the `rulesGenerationQA` Claude skill (`~/.claude/skills/rulesGenerationQA/`). Results and
error logs: `~/Documents/Gravty/`.
