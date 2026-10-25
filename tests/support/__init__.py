"""What every suite shares: repo paths and the scripted-model steps the offline and e2e tests feed the agent."""
import os

from offer_author.core import tree
from tools.paths import ROOT

GOLDEN = os.path.join(ROOT, "tests", "golden")  # rule XML the stock UI loads unchanged (13 offers)
SEGMENTS = os.path.join(ROOT, "offer_author", "core", "data", "segments")


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
