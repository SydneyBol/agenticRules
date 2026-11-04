"""The offer's logic is the model's: code writes the XML for the tree it is given and adds no rule, condition, action,
operator or value of its own.

Each golden offer is sent as the model would send it with every field the tools fill left out; the XML the tools
write, read back, has the same blocks in the same places with the same fields, and the only fields added are the ones
fields.fill derives from the model's own choices (which OBJECT an attribute belongs to, a counter's level, the
account's reward tag).
"""

import glob
import os

import pytest

from offer_author.core.canvas import build
from offer_author.core.canvas.tree import nodes
from tests.paths import GOLDEN
from tests.support import golden, selections

FILLED = {"OBJECT", "attribute_level", "LOYALTY_ACCOUNT", "REWARD_TAGS", "mta_type_value"}  # fields.fill
NAMES = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(GOLDEN, "compiled", "*.xml")))


def _strip(tree):
    """The tree with the fields the tools fill left out."""
    if isinstance(tree, list):
        return [_strip(t) for t in tree]
    out = dict(tree, fields={k: v for k, v in (tree.get("fields") or {}).items() if k not in FILLED})
    if tree.get("inputs"):
        out["inputs"] = {k: _strip(v) if isinstance(v, (dict, list)) else v for k, v in tree["inputs"].items()}
    return out


def _shape(tree):
    """Every block in order, with where it hangs: (type, input it sits in)."""
    return [(n.get("type"), key) for n, _, key in nodes(tree)]


@pytest.mark.parametrize("name", NAMES)
def test_the_written_offer_is_the_tree_the_model_sent(name, fixture_catalog):
    spec = golden(name)
    sent = _strip(spec["blocks"])
    xml = build.to_xml(sent, fixture_catalog, {c["name"]: c for c in spec["counters"]})[0]
    back, _ = selections(xml)
    assert _shape(back) == _shape(sent)  # no block added, dropped or moved
    for (given, _, _), (written, _, _) in zip(nodes(sent), nodes(back), strict=True):
        g, w = given.get("fields") or {}, written.get("fields") or {}
        assert {k: str(v) for k, v in g.items()} == {k: str(w[k]) for k in g}  # every choice kept as made
        assert set(w) - set(g) <= FILLED  # and nothing decided besides what fill derives


def test_a_root_with_no_rules_is_written_with_no_rules(fixture_catalog):
    xml = build.to_xml({"type": "offer_generic"}, fixture_catalog, {})[0]
    back, _ = selections(xml)
    assert _shape(back) == [("offer_generic", None)]
