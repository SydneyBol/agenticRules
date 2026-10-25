"""Fixtures shared by tests/unit, tests/e2e and tests/live (helpers and paths: tests/support)."""
import os

import pytest

from offer_author.core import tree, ui
from offer_author.core.catalog import Catalog
from tests.support import ROOT


@pytest.fixture(scope="session")
def fixture_catalog():
    return Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))


@pytest.fixture(scope="session")
def devintam1_catalog():
    return Catalog.from_bolapi_dumps(os.path.join(ROOT, "catalogs", "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")


@pytest.fixture
def fake_ui(monkeypatch):
    """The compile service stand-in: the screen keeps everything, generates a DRL and (rules canvas) creates the offer
    counters the XML refers to, as the upload dialog does. fake_ui.bad[canvas] = [dropped field] makes it complain."""
    class Seen(list):
        bad = {}

    seen = Seen()

    def compile_xml(xml, tenant=None, canvas="rules", **_):
        seen.append((canvas, xml))
        dropped = seen.bad.get(canvas) or []
        if canvas == "segment":
            return {"loaded": True, "drl": 'rule "S" when then addValidatedSegmentId("S1"); end', "validation_errors": [],
                    "block_warnings": [], "dropped_fields": dropped}
        _, counters = tree.selections(xml)
        posted = [{"name": c["name"], "data_type": c["data_type"], "level": "OFFER", "counter_type": c["counter_type"],
                   **({"entity_attribute_map": {"Activity": [c["namespace"]]}} if c.get("namespace") else {})} for c in counters]
        return {"loaded": True, "drl": "rule x end", "counters_created": posted, "dropped_fields": dropped,
                "changed_fields": [], "mutation_diffs": [], "validation_errors": [], "exceptions": []}
    monkeypatch.setattr(ui, "compile_xml", compile_xml)
    return seen
