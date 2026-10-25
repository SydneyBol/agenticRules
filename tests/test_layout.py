"""The package is split by responsibility, and the split is enforced:
  core/  deterministic code: block XML, the UI's checks, the compile and engine clients, the tools (Session), catalog
  ai/    what the model reads and calls, and the loop that talks to it
  api/   HTTP (the stock Pulsar contract) and CLI adapters
Dependencies point one way, api -> ai -> core: core never imports ai or api, ai never imports api."""
import ast
import glob
import os

from conftest import ROOT

PKG = os.path.join(ROOT, "offer_author")
ALLOWED = {"core": {"core"}, "ai": {"ai", "core"}, "api": {"api", "ai", "core"}}


def imports(path, layer):
    """The offer_author layers a module imports (absolute or relative)."""
    out = set()
    for n in ast.walk(ast.parse(open(path).read())):
        if isinstance(n, ast.ImportFrom):
            if n.level == 0 and (n.module or "").startswith("offer_author."):
                out.add(n.module.split(".")[1])
            elif n.level == 1:
                out.add(layer)
            elif n.level == 2:
                out.add((n.module or "").split(".")[0] or "offer_author")
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[1] for a in n.names if a.name.startswith("offer_author.")}
    return out


def test_every_module_sits_in_a_layer():
    loose = [f for f in glob.glob(os.path.join(PKG, "*.py")) if os.path.basename(f) != "__init__.py"]
    assert loose == [] and sorted(d for d in os.listdir(PKG) if os.path.isdir(os.path.join(PKG, d)) and d != "__pycache__") == ["ai", "api", "core"]


def test_dependencies_point_one_way():
    for layer, allowed in ALLOWED.items():
        for f in glob.glob(os.path.join(PKG, layer, "*.py")):
            assert imports(f, layer) <= allowed, (f, imports(f, layer) - allowed)


def test_model_context_lives_with_the_ai_and_code_data_with_the_code():
    assert sorted(os.listdir(os.path.join(PKG, "ai", "context"))) == ["block_reference.md", "block_reference_segment.md", "kt.md"]
    assert {"block_reference.json", "blueprints", "segments"} <= set(os.listdir(os.path.join(PKG, "core", "data")))
