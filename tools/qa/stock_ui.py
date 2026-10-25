"""Drafts through the stock gravty-ui screens (harness/ui/compile, mocked bolapi): the rules screen's Upload Rules path
(mock-run.mjs; offers start with no counters) and the add-segment screen (segment-run.mjs). Needs ng serve on :4200."""
import json
import os
import shutil
import subprocess

from tools.paths import RESULTS, UI_COMPILE


def _fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def upload(drafts, run, tenant=None, timeout=3600):
    """drafts {id: rules xml} -> (report {id: what the screen made of it}, dir with <id>.upload.drl).
    run names the input and output dirs under harness/results (<run>, ui_<run>)."""
    src = _fresh(os.path.join(RESULTS, run))
    shutil.rmtree(os.path.join(RESULTS, "ui_" + run), ignore_errors=True)
    for k, xml in drafts.items():
        open(os.path.join(src, k + ".xml"), "w").write(xml)
        json.dump({"counters": []}, open(os.path.join(src, k + ".spec.json"), "w"))
    p = subprocess.run(["node", "mock-run.mjs", "--path=upload", "--discover", "--fresh", f"--in={run}", f"--out=ui_{run}",
                        *([f"--tenant={tenant}"] if tenant else [])], cwd=UI_COMPILE, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError("mock-run failed: " + p.stderr[-2000:])
    out = os.path.join(RESULTS, "ui_" + run)
    report = json.load(open(os.path.join(out, "report.json")))
    return {k.split("@")[0]: v for k, v in report.items()}, out


def segments(drafts, run, tenant=None, timeout=1800):
    """drafts {id: segment xml} -> report {id: saved xml, drl, error, exceptions, warnings}."""
    src, out = _fresh(os.path.join(RESULTS, run)), os.path.join(RESULTS, "ui_" + run)
    shutil.rmtree(out, ignore_errors=True)
    for k, xml in drafts.items():
        open(os.path.join(src, k + ".xml"), "w").write(xml)
    p = subprocess.run(["node", "segment-run.mjs", f"--in={src}", f"--out={out}", *([f"--tenant={tenant}"] if tenant else [])],
                       cwd=UI_COMPILE, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError("segment-run failed: " + p.stderr[-2000:])
    return json.load(open(os.path.join(out, "report.json")))
