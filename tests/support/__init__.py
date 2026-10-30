"""What every suite shares: paths (tests/paths.py) and the scripted-model steps the offline and e2e tests feed the agent."""

import os

from offer_author.ai.render import reply as said  # noqa: F401  (re-exported: what the model reads of a tool result)
from offer_author.api.text import public  # noqa: F401  (re-exported: what the manager reads of a run's result)
from offer_author.core.canvas.edit import Doc
from offer_author.core.canvas.tree import nodes
from offer_author.core.run import Run
from offer_author.core.settings import Settings
from tests.paths import CATALOGS, GOLDEN, ROOT

SEGMENTS = os.path.join(ROOT, "offer_author", "core", "data", "segments")


def selections(xml):
    """Saved XML -> (tree, counters) as the model writes them, without block ids: the root block, or the top-level
    blocks when there are several."""
    d = Doc(xml)
    for n, _, _ in nodes(d.tops):
        n.pop("id", None)
    return (d.tops[0] if len(d.tops) == 1 else d.tops), d.counters


def golden(name):
    """A golden offer as the model writes it: compose arguments (block selections, declared counters)."""
    blocks, counters = selections(open(os.path.join(GOLDEN, "compiled", name + ".xml")).read())
    return {"canvas": "rules", "blocks": blocks, "counters": counters}


def saved_segment(name):
    blocks, _ = selections(open(os.path.join(SEGMENTS, name + ".xml")).read())
    return {"canvas": "segment", "blocks": blocks}


BIT_HEADER = {"h_bit_type": "SPEND", "h_bit_category": "ACCRUAL"}  # what a model run states for a Spend offer


def finish(**kw):
    f = {"readback": "test", "assumptions": [], "blocking_questions": [], "unsupported": []}
    f.update(kw)
    return ("finish", f)


def offer_main(**kw):
    m = {
        "offer_name": "Test offer",
        "subtitle": "",
        "offer_description": "",
        "terms": [],
        "start_date": "",
        "end_date": "",
        "max_usage_per_user": 0,
        "frequency": "none",
        "num_of_clients": 0,
        "offer_frequency_type": "",
        "segment_prompt": "",
        "rules_prompt": "",
    }
    m.update(kw)
    return ("set_offer_main", m)


def dev_settings(**kw):
    """The code-default settings with this workspace's tenant catalogs (what OFFER_AUTHOR_CATALOGS names in a run)."""
    return Settings(catalogs=CATALOGS, **kw)


def new_run(catalog, offer_id=None, existing=None):
    """The run's tools as agent.run makes them."""
    return Run(catalog, offer_id, existing)


def checked(run, x):
    """A canvas through compose: written and checked."""
    return run.canvases.compose(x)
