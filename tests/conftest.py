"""Fixtures shared by tests/unit, tests/e2e and tests/live (helpers and paths: tests/support)."""

import json
import logging
import os
import re

import pytest

from offer_author.core.tenant.catalog import Catalog
from tests.paths import CATALOGS, FIXTURE


@pytest.fixture(scope="session")
def fixture_catalog():
    return Catalog.from_json(FIXTURE)


@pytest.fixture(scope="session")
def devintam1_catalog():
    return Catalog.from_bolapi_dumps(os.path.join(CATALOGS, "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")


@pytest.fixture
def logged(caplog):
    """logged(part, offer_id) -> the notes the last Pulsar answer for that part (and offer) wrote to the log."""
    caplog.set_level(logging.INFO, logger="offer_author.pulsar")

    def read(part="rules", offer_id=None):
        for r in reversed(caplog.records):
            m = re.match(r"pulsar answer offer=(\S+) part=(\w+) (.*)", r.getMessage())
            if m and m[2] == part and (offer_id is None or m[1] == str(offer_id)):
                return json.loads(m[3])
        return None

    return read


@pytest.fixture
def composed(monkeypatch):
    """(canvas, xml) of each compose that wrote its canvas, in order: what reached the checks."""
    from offer_author.core.run.canvases import Canvases

    real, seen = Canvases.compose, []

    def spy(self, x):
        before = dict(self.last)
        out = real(self, x)
        seen.extend((c, w.xml) for c, w in self.last.items() if before.get(c) is not w)
        return out

    monkeypatch.setattr(Canvases, "compose", spy)
    return seen


@pytest.fixture
def flagged(monkeypatch):
    """Block ids (as written in the XML) the checks also report, while listed: a problem the model must fix."""
    from offer_author.core.run import canvases
    from offer_author.domain import BlockRef, Problem

    real, ids = canvases.check, []
    monkeypatch.setattr(
        canvases,
        "check",
        lambda xml, catalog, canvas: [
            *real(xml, catalog, canvas=canvas),
            *(Problem.of("no_reward_tag", BlockRef("action", b)) for b in ids),
        ],
    )
    return ids
