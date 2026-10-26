"""HTTP API (Flask, like the existing /ai/ services).

POST /ai/offer-author/generate  {offer_type, sponsor_id, prompt, offer_id?, loyalty_account_id?}
     -> 202 {job_id}            (?wait=true runs inline and returns 200 with the result, for local use)
GET  /ai/offer-author/<job_id>  -> {job_id, state: queued|running|done|failed, result?}
GET  /ai/offer-author/health
Pulsar-compatible (what the stock gravty-ui calls, see pulsar.py):
POST /ai/pulsar/offer/rules[/]?regenerate=true|false   GET|POST /ai/pulsar/<offer_id>/prompt/<stage>[/]

Reward offers only: other offer types get 422 unsupported_offer_type.
Run:  AWS_PROFILE=... OFFER_AUTHOR_CATALOGS=<tenant dumps folder> python -m offer_author.api.service --tenant devintam1 \
          --sponsor 1 --account 12 --tag BASE
"""
import argparse
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request

from ..ai.agent import generate
from .pulsar import pulsar_blueprint

MAX_PROMPT = 4000


class Jobs:
    def __init__(self, workers=4):
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.jobs, self.lock = {}, threading.Lock()

    def submit(self, fn):
        jid = uuid.uuid4().hex
        with self.lock:
            self.jobs[jid] = {"job_id": jid, "state": "queued"}

        def run():
            self._set(jid, state="running")
            try:
                self._set(jid, state="done", result=fn())
            except Exception as e:  # noqa: BLE001 - surfaced to the caller as a failed job, never an empty 200
                self._set(jid, state="failed", error=f"{type(e).__name__}: {e}")

        self.pool.submit(run)
        return jid

    def _set(self, jid, **kw):
        with self.lock:
            self.jobs[jid].update(kw)

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
            return dict(j) if j else None


def create_app(llm, catalog_for, jobs=None, counters_for=None, settings=None, engine=None):
    """catalog_for(sponsor_id, account_id, authorization) -> Catalog. llm: object with turn(system, messages).
    counters_for(offer_id, authorization) -> the offer's status and counters (counters.py), or None.
    settings, engine: core.settings.Settings and core.engine.Engine, handed to every agent run."""
    app = Flask(__name__)
    jobs = jobs or Jobs()
    app.register_blueprint(pulsar_blueprint(llm, catalog_for, counters_for=counters_for, settings=settings, engine=engine))  # the contract the stock gravty-ui already calls

    def bad(code, msg, status=400):
        return jsonify({"code": code, "detail": msg}), status

    @app.get("/ai/offer-author/health")
    def health():
        return {"ok": True, "model": getattr(llm, "model", "?")}

    @app.post("/ai/offer-author/generate")
    def gen():
        body = request.get_json(silent=True) or {}
        if body.get("offer_type") != "reward":
            return bad("unsupported_offer_type", "English-to-rules is available for reward offers only.", 422)
        prompt = (body.get("prompt") or "").strip()
        if not prompt:
            return bad("prompt_required", "Describe the offer in a sentence or two.")
        if len(prompt) > MAX_PROMPT:
            return bad("prompt_too_long", f"Keep the description under {MAX_PROMPT} characters.")
        if not isinstance(body.get("sponsor_id"), int):
            return bad("sponsor_required", "sponsor_id (integer) is required.")
        offer_id = body.get("offer_id")
        account = body.get("loyalty_account_id")
        try:
            catalog = catalog_for(body["sponsor_id"], account, request.headers.get("Authorization"))
            if catalog.account(account) is None:  # fail fast on an unknown account
                raise ValueError(f"loyalty account {account or catalog.default_account} not in catalog")
        except Exception as e:  # noqa: BLE001
            return bad("catalog_unavailable", f"Could not load the tenant catalog: {e}", 424)
        existing = body.get("existing_counters") if isinstance(body.get("existing_counters"), dict) else \
            (counters_for(offer_id, request.headers.get("Authorization")) if counters_for and offer_id is not None else None)
        work = lambda: generate(prompt, catalog, llm, offer_id=offer_id, account_id=account, existing=existing,  # noqa: E731
                                settings=settings, engine=engine)
        if request.args.get("wait") == "true":
            return jsonify(work())
        return jsonify({"job_id": jobs.submit(work)}), 202

    @app.get("/ai/offer-author/<job_id>")
    def job(job_id):
        j = jobs.get(job_id)
        return (jsonify(j), 200) if j else bad("not_found", "unknown job", 404)

    return app


def main():
    """The service, start to finish: settings from the environment, the catalog, the engine (warm for the process),
    the model client, then the app that hands them to each agent run."""
    from ..ai.llm import make_llm
    from ..core.catalog import Catalog
    from ..core.counters import BolapiCounterSource
    from ..core.engine import Engine
    from ..core.settings import Settings
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-dir", help="saved bolapi responses (default: $OFFER_AUTHOR_CATALOGS/<tenant>/bolapi)")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--sponsor", type=int, required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--tag")
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--bolapi-base", help="https://master.<tenant>.gravty.info: read each offer's counters with the caller's JWT")
    a = ap.parse_args()
    settings = Settings.from_env()
    folder = a.catalog_dir or (settings.catalogs and os.path.join(settings.catalogs, a.tenant, "bolapi"))
    if not folder:
        ap.error("give --catalog-dir or OFFER_AUTHOR_CATALOGS (the tenant dumps folder)")
    cat = Catalog.from_bolapi_dumps(folder, a.tenant, a.sponsor, a.account, a.tag)
    counters_for = BolapiCounterSource(a.bolapi_base).get if a.bolapi_base else None
    engine = Engine.from_settings(settings).start()  # the first simulate then answers in milliseconds, not after a JVM start
    try:
        create_app(make_llm(), lambda sponsor, account, auth: cat, counters_for=counters_for, settings=settings,
                   engine=engine).run(port=a.port)
    finally:
        engine.close()


if __name__ == "__main__":
    main()
