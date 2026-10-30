"""Where the tests find things: this checkout's fixtures, and the dev workspace for what stays outside the repo.

The dev workspace (OFFER_AUTHOR_DEV, default ~/Documents/Gravty/offer-xml-generator-dev) holds the tenant catalog
dumps, the batch and hosted scripts and every output; only the e2e and live suites read it.
"""

import os

ROOT = REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
FIXTURE = os.path.join(TESTS, "fixtures", "catalog.json")  # the small catalog the offline tests use
GOLDEN = os.path.join(TESTS, "golden")  # rule XML the stock UI loads unchanged
CASES = os.path.join(TESTS, "cases")

DEV = os.environ.get("OFFER_AUTHOR_DEV", os.path.expanduser("~/Documents/Gravty/offer-xml-generator-dev"))
CATALOGS = os.environ.get("OFFER_AUTHOR_CATALOGS") or os.path.join(DEV, "catalogs")  # tenant dumps
RESULTS = os.path.join(DEV, "results")  # outputs of the e2e and live suites
LIVE = os.path.join(RESULTS, "live")
