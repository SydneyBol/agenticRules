"""HTTP service (Flask, like the existing /ai/ services): the Pulsar endpoints the stock gravty-ui calls.

POST /ai/pulsar/offer/rules[/]?regenerate=true|false   POST /ai/pulsar/segment/rules   POST /ai/pulsar/offer/metadata
GET|POST /ai/pulsar/<offer_id>/prompt/<stage>[/]        GET /ai/offer-author/health
Answers are Pulsar's; what each run says beyond the XML is in the log (api/pulsar/payloads.py notes).

Run:  AWS_PROFILE=... OFFER_AUTHOR_CATALOGS=<tenant dumps folder> python -m offer_author.api.service \
          --tenant devintam1 --sponsor 1 --account 12 --tag BASE
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
from typing import Any

from flask import Flask

from ..ai.agent import warm_values
from ..core.settings import Settings
from .pulsar import pulsar_blueprint
from .pulsar.runs import CatalogFor, CountersFor


def create_app(
    llm: Any,
    catalog_for: CatalogFor,
    counters_for: CountersFor | None = None,
    settings: Settings | None = None,
) -> Flask:
    """The service's Flask app.

    Args:
        llm: The chat model (an object with turn(system, messages)).
        catalog_for: (sponsor_id, account_id, authorization) -> the tenant's Catalog.
        counters_for: (offer_id, authorization) -> the offer's status and counters (existing_counters.py), or None.
        settings: The settings handed to every agent run.

    Returns:
        The app, with the Pulsar endpoints registered.
    """
    app = Flask(__name__)
    app.register_blueprint(
        pulsar_blueprint(llm, catalog_for, counters_for=counters_for, settings=settings)
    )  # the contract the stock gravty-ui already calls

    @app.get("/ai/offer-author/health")
    def health() -> dict[str, Any]:
        """The model the service runs and whether earlier offers are on."""
        on = settings or Settings()
        return {
            "ok": True,
            "model": getattr(llm, "model", "?"),
            "effort": getattr(llm, "effort", "?"),
            "thinking": getattr(llm, "thinking", "?"),
            "earlier_offers": on.similar_offers if on.history else 0,
        }

    return app


def main() -> None:
    """The service, start to finish.

    Settings from the environment, the catalog, the model client, then the app that hands them to each agent run.
    """
    from ..ai.model import make_llm
    from ..core.tenant.catalog import Catalog
    from ..core.tenant.existing_counters import BolapiCounterSource

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-dir", help="saved bolapi responses (default: $OFFER_AUTHOR_CATALOGS/<tenant>/bolapi)")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--sponsor", type=int, required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--tag")
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument(
        "--bolapi-base", help="https://master.<tenant>.gravty.info: read each offer's counters with the caller's JWT"
    )
    a = ap.parse_args()
    settings = Settings.from_env()
    folder = a.catalog_dir or (settings.catalogs and os.path.join(settings.catalogs, a.tenant, "bolapi"))
    if not folder:
        ap.error("give --catalog-dir or OFFER_AUTHOR_CATALOGS (the tenant dumps folder)")
    cat = Catalog.from_bolapi_dumps(folder, a.tenant, a.sponsor, a.account, a.tag)
    counters_for = BolapiCounterSource(a.bolapi_base).get if a.bolapi_base else None
    threading.Thread(target=warm_values, args=(cat, settings), daemon=True).start()  # value-search models and lists
    create_app(make_llm(), lambda sponsor, account, auth: cat, counters_for=counters_for, settings=settings).run(
        port=a.port
    )


if __name__ == "__main__":
    main()
