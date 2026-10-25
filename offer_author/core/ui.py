"""Client for the warm compile service (harness/ui-harness/compile-server.mjs): the stock gravty-ui rules screen, headless.

The UI is the compiler: it loads the XML through the stock upload path, keeps what it understands, generates the DRL
the engine will run, and creates the offer counters the XML refers to. Nothing here interprets blocks.
"""
import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("OFFER_AUTHOR_COMPILE_URL", "http://127.0.0.1:8078")


class CompileUnavailable(Exception):
    pass


def compile_xml(xml, tenant=None, url=None, timeout=120, canvas="rules"):
    """canvas 'rules': the offer-rules screen through Upload Rules; 'segment': the add-segment screen."""
    body = {"xml": xml, "tenant": tenant or "", "canvas": canvas}
    req = urllib.request.Request((url or URL) + "/compile", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise CompileUnavailable(f"compile service at {url or URL} not reachable: {e}") from e


def problems(res):
    """What the model must fix: the UI could not load it, dropped or changed something, or refused to generate DRL."""
    out = []
    if not res.get("loaded"):
        return [f"the screen did not load: {res.get('error')}"]
    for d in res.get("dropped_fields") or []:
        out.append(f"dropped by the UI: {d['block']} field {d['field']} ({d['in']!r})")
    for d in res.get("changed_fields") or []:
        out.append(f"changed by the UI: {d['block']} field {d['field']} {d['in']!r} -> {d['out']!r}")
    for d in res.get("mutation_diffs") or []:
        out.append(f"mutation changed by the UI: {d['block']} {d['attr']} {d['in']!r} -> {d['out']!r}")
    out += [f"validation: {m}" for m in res.get("validation_errors") or []]
    out += [f"exception: {m.splitlines()[0][:300]}" for m in res.get("exceptions") or [] if m]  # message only, no stack
    out += [f"warning: {m}" for m in res.get("block_warnings") or []]
    out += [f"disabled block: {m}" for m in res.get("disabled_blocks") or []]
    if not (res.get("drl") or "").strip():
        out.append("the screen could not compile the blocks")
    return out
