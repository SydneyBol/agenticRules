"""The package is split by responsibility, and the split is enforced:
  domain/ typed data every layer shares: Problem, the shapes of composed canvases
  core/  deterministic code: canvas (block XML and its compatibility checks), tenant (catalog, counters, earlier
         offers), run (the tools as code)
  ai/    what the model reads and calls, and the loop that talks to it
  api/   HTTP (the stock Pulsar contract) and CLI adapters
Dependencies point one way, api -> ai -> core -> domain: core never imports ai or api, ai never imports api."""

import ast
import glob
import os
import subprocess

import pytest

from tests.paths import DEV, ROOT

PKG = os.path.join(ROOT, "offer_author")
ALLOWED = {
    "domain": {"domain"},
    "core": {"core", "domain"},
    "ai": {"ai", "core", "domain"},
    "api": {"api", "ai", "core", "domain"},
}


def imports(path):
    """The offer_author layers a module imports (absolute or relative, resolved against its own package)."""
    rel = os.path.relpath(path, ROOT)[:-3].split(os.sep)
    package = rel[:-1]  # the module's package, e.g. offer_author/core/run
    out = set()
    for n in ast.walk(ast.parse(open(path).read())):
        if isinstance(n, ast.ImportFrom):
            if n.level == 0 and (n.module or "").startswith("offer_author."):
                out.add(n.module.split(".")[1])
            elif n.level:
                target = package[: len(package) - (n.level - 1)] + ((n.module or "").split(".") if n.module else [])
                if len(target) > 1:
                    out.add(target[1])
                else:  # "from ... import domain": the names imported are the layers
                    out |= {a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name.split(".")[1] for a in n.names if a.name.startswith("offer_author.")}
    return out


def test_every_module_sits_in_a_layer():
    loose = [f for f in glob.glob(os.path.join(PKG, "*.py")) if os.path.basename(f) != "__init__.py"]
    assert loose == [] and sorted(
        d for d in os.listdir(PKG) if os.path.isdir(os.path.join(PKG, d)) and d != "__pycache__"
    ) == ["ai", "api", "core", "domain"]


def test_dependencies_point_one_way():
    for layer, allowed in ALLOWED.items():
        for f in glob.glob(os.path.join(PKG, layer, "**", "*.py"), recursive=True):
            assert imports(f) <= allowed, (f, imports(f) - allowed)


def test_model_context_lives_with_the_ai_and_code_data_with_the_code():
    assert sorted(os.listdir(os.path.join(PKG, "ai", "context"))) == [
        "block_reference.md",
        "block_reference_segment.md",
        "conventions.md",
        "kt.md",
        "member_dates.md",
    ]
    assert {"block_reference.json", "offer_main_form.json", "blueprints", "segments"} <= set(
        os.listdir(os.path.join(PKG, "core", "data"))
    )


def top_imports(path):
    tree = ast.parse(open(path).read())
    return {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {
        (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0
    }


def test_the_product_never_imports_tooling():
    for f in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        assert not top_imports(f) & {"tools", "tests"}, f


def test_repo_holds_the_product_its_tests_and_its_generators():
    """The repo: the package, its tests (unit, e2e, live, fakes, fixtures), the generators of its context and data
    (tools/build), and Pulsar's reviewed code under vendor/. It is a Python service: no Java, no JS, no DRL. The stock-
    screen compile service, the engine harness, tenant catalogs, QA tools, batch and hosted scripts and their outputs
    live in the dev workspace (tests/paths.py DEV); the README maps them."""
    top = lambda *p: sorted(d for d in os.listdir(os.path.join(ROOT, *p)) if not d.startswith((".", "__")))
    assert {"offer_author", "tests", "tools", "vendor"} <= set(top()) and not {"harness", "pulsar_rag"} & set(top())
    assert top("tools") == ["build"] and top("vendor") == ["pulsar_rag"]
    assert {"unit", "e2e", "live", "support", "fakes", "fixtures", "golden", "cases"} <= set(top("tests"))
    assert glob.glob(os.path.join(ROOT, "tests", "test_*.py")) == []
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    present = [f for f in tracked if os.path.exists(os.path.join(ROOT, f))]
    assert not [f for f in present if f.endswith((".java", ".js", ".mjs", ".drl", "pom.xml", "package.json"))]
    assert not [f for f in tracked if f.startswith(("catalogs/", ".idea/", "pulsar_rag/", "build/"))]
    assert "## Outside this repo" in open(os.path.join(ROOT, "README.md")).read()


def test_the_dev_workspace_holds_no_tests_or_build_tools():
    """What moved into the repo is gone from the dev workspace: its tools are the QA ones only; it holds QA's harness
    (the stock-screen compile service, the engine harness) and QA's own tests of the stock screen and engine."""
    if not os.path.isdir(DEV):
        pytest.skip("no dev workspace on this machine")
    dirs = lambda *p: sorted(
        d
        for d in os.listdir(os.path.join(DEV, *p))
        if os.path.isdir(os.path.join(DEV, *p, d)) and not d.startswith((".", "__"))
    )
    assert "tests" not in dirs() and dirs("tools") == ["qa"]
    assert {"batch", "hosted", "compile"} <= set(dirs("harness", "ui")) and "src" in dirs("harness", "engine")


def test_block_reference_lists_no_tenant_values():
    """Dropdowns filled from bolapi (attributes, accounts, privileges, usage policies, tags) show "(tenant catalog)":
    the model reads those codes from the tenant catalog block only, never the fixture's or another tenant's."""
    import json

    ref = json.load(open(os.path.join(ROOT, "offer_author", "core", "data", "block_reference.json")))
    tenant = {b["type"]: set(b.get("tenant_fields") or ()) for b in ref["canvases"]["rules"]["toolbox"]}
    for t, f in (
        ("action", "LOYALTY_ACCOUNT"),
        ("action", "SELECTED_PRIVILEGE"),
        ("action", "REWARD_TAGS"),
        ("activity_attribute", "ATTRIBUTE"),
        ("member_attribute", "ATTRIBUTE"),
        ("payment_attribute", "payment_attribute"),
    ):
        assert f in tenant[t], (t, f)
    for name in ("block_reference.md", "block_reference_segment.md"):
        text = open(os.path.join(ROOT, "offer_author", "ai", "context", name)).read()
        dropdowns = [ln for ln in text.splitlines() if "live dropdowns" in ln or " adds: " in ln]
        for fixture_value in ("h_bit_amount", "date_of_birth", "[501]", "[9]", "[1]", "BONUS", "[category]"):
            assert not [ln for ln in dropdowns if fixture_value in ln], (name, fixture_value)
