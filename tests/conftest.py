import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")
GOLDEN = os.path.join(ROOT, "tests", "golden")  # rule XML the stock UI loads unchanged (13 offers)
SEGMENTS = os.path.join(ROOT, "offer_author", "core", "data", "segments")
SPIKE = os.environ.get("OFFER_AUTHOR_SPIKE", os.path.join(ROOT, "harness"))

from offer_author.core import tree, ui  # noqa: E402
from offer_author.core.catalog import Catalog  # noqa: E402


@pytest.fixture(scope="session")
def fixture_catalog():
    return Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))


@pytest.fixture(scope="session")
def devintam1_catalog():
    return Catalog.from_bolapi_dumps(os.path.join(ROOT, "catalogs", "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")


def golden(name):
    """A golden offer as the model writes it: compile arguments (block selections, declared counters)."""
    blocks, counters = tree.selections(open(os.path.join(GOLDEN, "compiled", name + ".xml")).read())
    return {"canvas": "rules", "blocks": blocks, "counters": counters}


def saved_segment(name):
    blocks, _ = tree.selections(open(os.path.join(SEGMENTS, name + ".xml")).read())
    return {"canvas": "segment", "blocks": blocks}


def finish(**kw):
    f = {"readback": "test", "assumptions": [], "blocking_questions": [], "unsupported": []}
    f.update(kw)
    return ("finish", f)


def offer_main(**kw):
    m = {"offer_name": "Test offer", "subtitle": "", "offer_description": "", "terms": [], "start_date": "", "end_date": "",
         "max_usage_per_user": 0, "frequency": "none", "num_of_clients": 0, "offer_frequency_type": "",
         "segment_prompt": "", "rules_prompt": ""}
    m.update(kw)
    return ("set_offer_main", m)


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
