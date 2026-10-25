"""Developer tooling around offer_author (never imported by it): build/ regenerates checked-in context and data, qa/
drives the hosted tenant, the backend and the engine, bench/ measures accuracy and retrieval.

Scripts run from the repo root by path (`.venv/bin/python -I tools/qa/check_offer_flow.py ...`); each puts the repo
root on sys.path itself, so `-I` works."""
