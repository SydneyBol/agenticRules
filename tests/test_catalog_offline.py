"""Offline parts of the catalog: the case files themselves, API-level negative cases, and the block slot matrix."""
import pytest

import catalog
from offer_author.llm import ScriptedLLM
from offer_author.service import create_app

CASES = catalog.load()
API_CASES = [c for c in CASES if c.get("api")]


def test_catalog_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    for c in CASES:
        assert c.get("prompt") or c.get("api"), c["id"]
        for f in c["expect"].get("features", []):
            assert f.split(":")[0] in ("block", "obj", "act", "op", "counter", "mode"), (c["id"], f)
            if f.startswith("block:"):
                assert f[6:] in catalog.TOOLBOX, (c["id"], f)
    blocks = {f[6:] for c in CASES for f in c["expect"].get("features", []) if f.startswith("block:")}
    assert blocks == catalog.TOOLBOX, sorted(catalog.TOOLBOX - blocks)  # every toolbox block has a case


@pytest.mark.parametrize("case", API_CASES, ids=[c["id"] for c in API_CASES])
def test_api_case(case, fixture_catalog):
    app = create_app(ScriptedLLM([]), lambda sponsor, account, auth: fixture_catalog)
    r = app.test_client().post(case["api"]["path"], json=case["api"]["body"])
    assert r.status_code == case["expect"]["http"], r.get_json()


SLOT_PAIRS = [(slot, child) for slot, kids in catalog.slots().items() for child in kids]


@pytest.mark.parametrize("slot,child", SLOT_PAIRS, ids=[f"{s}<-{c}" for s, c in SLOT_PAIRS])
def test_slot_pair_builds(slot, child):
    """Every container input with every child its setCheck allows must be buildable and serialisable."""
    try:
        from offer_author import blocks  # noqa: F401  the spec-driven block builder (not built yet)
    except ImportError:
        pytest.xfail("no block-level builder yet: the compiler only emits the shapes its 12 primitives use")
    raise AssertionError("block builder present but this test is not wired to it yet")
