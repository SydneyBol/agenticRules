import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(ROOT, "tests", "fixtures")
GOLDEN = os.path.join(ROOT, "tests", "golden")
SPIKE = os.environ.get("OFFER_AUTHOR_SPIKE", os.path.join(ROOT, "harness"))

from offer_author.catalog import Catalog  # noqa: E402


@pytest.fixture(scope="session")
def fixture_catalog():
    return Catalog.from_json(os.path.join(ROOT, "catalogs", "fixture.json"))


@pytest.fixture(scope="session")
def devintam1_catalog():
    return Catalog.from_bolapi_dumps(os.path.join(ROOT, "catalogs", "devintam1", "bolapi"), "devintam1", 1, "12", "BASE")


def recorded_runs():
    return json.load(open(os.path.join(FIX, "recorded_llm_opus55.json")))


def replay_calls(rec):
    """A recorded spike run (normalised output) -> the raw (tool, input) calls the model made.
    The spike's finish had a single `questions` list; the prototype splits it, so map it to blocking_questions."""
    o = rec["output"]
    calls = [("add_" + p["kind"], p["raw"]) for p in o["primitives"]]
    fin = {k: v for k, v in o["finish"].items() if k != "questions"}
    fin.update(assumptions=[], blocking_questions=o["finish"].get("questions", []))
    return calls + [("finish", fin)]


def finish(**kw):
    f = {"readback": "test", "activity_types": [], "start_date": "", "end_date": "", "max_usage_per_user": 0,
         "frequency": "none", "assumptions": [], "blocking_questions": [], "unsupported": []}
    f.update(kw)
    return ("finish", f)


def c(obj, attr, op, value="", values=None, dim="NONE"):
    return {"object": obj, "attribute": attr, "dimension": dim, "op": op, "value": value, "values": values or []}


def pts(n):
    return {"kind": "points", "points": n, "privilege_id": ""}


def priv(pid):
    return {"kind": "privilege", "points": 0, "privilege_id": pid}
