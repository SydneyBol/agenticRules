"""Where the harness, its outputs and the suites' outputs live: one place for every tool and suite."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.environ.get("OFFER_AUTHOR_HARNESS", os.path.join(ROOT, "harness"))
UI_COMPILE = os.path.join(HARNESS, "ui", "compile")  # stock gravty-ui screens, headless, mocked backend
UI_HOSTED = os.path.join(HARNESS, "ui", "hosted")  # the signed-in debug Chrome on a tenant
RESULTS = os.path.join(HARNESS, "results")  # harness outputs (ignored)
ENGINE_CASES = os.path.join(RESULTS, "engine", "cases")  # every engine job the tools ran, to rerun one by hand
CASES = os.path.join(ROOT, "tests", "cases")
LIVE = os.path.join(ROOT, "tests", ".live")  # live suite outputs (ignored)
CATALOGS = os.path.join(ROOT, "catalogs")


def tenant_results(tenant, base, *parts):
    """harness/results[/<tenant>]/...: the base tenant's results sit at the top level, as they always have."""
    return os.path.join(RESULTS, *([] if tenant == base else [tenant]), *parts)
