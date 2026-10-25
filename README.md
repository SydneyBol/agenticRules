# offer-xml-generator

A loyalty manager's English description of a Gravty **reward** offer → the whole offer for the stock gravty-ui wizard:
Offer Main fields, the member segment, and the Blockly rule XML the stock rules screen uploads as is (no UI change).
It replaces Pulsar behind the same `/ai/pulsar/...` API.

```bash
offer-xml "Gold tier members get 100 bonus points on weekend spends of at least \$50" -o gold.xml
offer-xml --batch examples/prompts.txt --out-dir out/
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

## Layout

| Path | What |
|---|---|
| `offer_author/ai/` | model-facing: the loop, prompt, tool schemas, Bedrock client, `context/*.md` |
| `offer_author/core/` | deterministic: session (the tools), tree, checks, compile and engine clients, catalog, counters, `data/` |
| `offer_author/api/` | HTTP service and CLI. Dependencies point one way: api → ai → core (`tests/unit/test_layout.py`) |
| `harness/ui/compile/` | stock gravty-ui screens headless against a mocked backend: compile service, upload and segment runners |
| `harness/ui/hosted/` | the signed-in debug Chrome on a tenant (:9222): QA offer save, simulation, catalog dump |
| `harness/engine/` | Java harness that runs rules + BITs in the real Drools engine (`mvn -q package -DskipTests`) |
| `tools/build/` | regenerate `ai/context` and `core/data`: `kt_prompt.py`, `block_reference.py` |
| `tools/qa/` | QA through the backend, the stock UI and the engine: `check_offer_flow.py`, `check_hosted_combos.py`, ... |
| `tools/bench/` | `scale_accuracy.py` (1,000-attribute catalog), `retrieval_bench.py` (attribute retrieval) |
| `tests/unit/`, `e2e/`, `live/` | offline (default), stock UI + engine (`-m e2e`), Bedrock (`-m live`); helpers in `tests/support/` |
| `tests/cases/` | prompt cases with expected blocks and BIT outcomes, hosted combinations, retrieval phrases |
| `catalogs/` | tenant dumps (bolapi responses) and the fixture catalog |

Outputs go to `harness/results/` and `tests/.live/` (not committed).

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -c requirements-lock.txt -e '.[test]'
```

```bash
cd harness/ui && npm ci
```

AWS: an SSO session for a profile with Bedrock access (`aws sso login --profile MLCoreDeveloperAccess-384853870836`).
Model: `OFFER_AUTHOR_MODEL` (default `us.anthropic.claude-sonnet-5-5`). The compile service needs `ng serve` of
gravty-ui on :4200, then `npm --prefix harness/ui run compile-server` (:8078).

## Tests

```bash
.venv/bin/python -m pytest
```

```bash
.venv/bin/python -m pytest -m e2e
```

```bash
AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -m pytest -m live
```

Offer flow through the running backend, stock screens and engine (whole offer vs each screen alone):

```bash
AWS_PROFILE=MLCoreDeveloperAccess-384853870836 AWS_REGION=us-west-2 .venv/bin/python -I tools/qa/check_offer_flow.py both --tenant=devintbf2
```

Hosted QA on a tenant is the `rulesGenerationQA` Claude skill (`~/.claude/skills/rulesGenerationQA/`). Results and
error logs: `~/Documents/Gravty/`.
