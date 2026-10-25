"""Generate the block reference the agent reads, from the gravty-ui source (no hand-written block facts).

Per canvas (rules: offer-rules.config.ts; segment: segmentation-blockly.config.ts), three layers, all derived:
  1. static, from the TypeScript: for every toolbox block, its type, connection checks, statement/value/dummy inputs,
     field names, where mutationToDom takes each mutation attribute (field, constant, derived) and in which order,
     and the upload dialog's counter-detection regexes (rules-upload-model.component.ts; attribute ORDER matters);
  2. live (optional), compile-server.mjs /introspect: the dropdown options the running stock screen offers per field
     and selector choice (the option lists are context-dependent getters, so only the UI knows them);
  3. evidence, from XML the UI saved (seeded blueprints; segments saved on devintam1): the mutation signatures,
     fields and picked codes each block type really carries.
The JSON also records a hash of every source file read, so a test can tell when the reference is stale.

usage: python tools/block_reference.py [--ui ~/Downloads/newui-develop/gravty-ui]
writes offer_author/core/data/block_reference.json (what the code reads) and offer_author/ai/context/
block_reference.md (rules), block_reference_segment.md
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "offer_author", "core", "data")  # code: tree.py / check.py read the json
CONTEXT = os.path.join(ROOT, "offer_author", "ai", "context")  # AI: the md the model reads
CANVASES = {  # canvas -> (toolbox config, evidence dir, live introspection output)
    "rules": ("src/app/workspaces/offer/modules/offer-setup/screens/offer-rules/config/offer-rules.config.ts",
              os.path.join(DATA, "blueprints"), os.path.join(ROOT, "harness", "results", "introspect.json")),
    "segment": ("src/app/workspaces/program/screens/segment-setup/config/segmentation-blockly.config.ts",
                os.path.join(DATA, "segments"), os.path.join(ROOT, "harness", "results", "introspect_segment.json")),
}
UPLOAD = "src/app/gravty-blockly/layouts/rules-upload-model/rules-upload-model.component.ts"
BLOCKS = "src/app/gravty-blockly/blocks"
Q = r"""['"]([^'"]+)['"]"""


def toolbox(ui, config):
    """[(category, class_name)] in toolbox order."""
    src = open(os.path.join(ui, config)).read()
    lists = {m.group(1): re.findall(r"new (\w+)\(", m.group(2))
             for m in re.finditer(r"const (\w+)\s*(?::[^=]+)?=\s*\[(.*?)\];?\s*\n", src, re.S)}
    out = []
    for cat, body in re.findall(r"new Category\(" + Q + r",\s*'[^']*',\s*\[(.*?)\]\)", src, re.S):
        for name in re.findall(r"\.\.\.(\w+)", body):
            out += [(cat, c) for c in lists.get(name, [])]
    return out


def class_sources(ui):
    """class name -> (file, class body)."""
    out = {}
    for f in glob.glob(os.path.join(ui, BLOCKS, "**", "*.ts"), recursive=True):
        src = open(f).read()
        starts = [(m.start(), m.group(1)) for m in re.finditer(r"export class (\w+) extends CustomBlock", src)]
        for i, (s, name) in enumerate(starts):
            out[name] = (os.path.relpath(f, os.path.join(ui, BLOCKS)), src[s:starts[i + 1][0] if i + 1 < len(starts) else len(src)])
    return out


def body_of(src, name):
    """Text of the first method `name(...) {...}` (brace-matched), or ''."""
    m = re.search(r"\b" + name + r"\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|. ]+)?\s*\{", src)
    if not m:
        return ""
    depth, i = 0, m.end() - 1
    for j in range(i, len(src)):
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return src[i:j + 1]
    return src[i:]


def method_text(src, name, until):
    """From the definition of `name(...) {` to the definition of `until(...) {` (regex literals with braces defeat
    brace matching), else the brace-matched body."""
    m = re.search(r"\b" + name + r"\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|. ]+)?\s*\{", src)
    n = re.search(r"\b" + until + r"\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|. ]+)?\s*\{", src[m.end():]) if m else None
    return src[m.start():m.end() + n.start()] if n else body_of(src, name)


def mutation_sources(body):
    """attr -> {"fields": [...], "consts": [...], "derived": bool} from mutationToDom: where the UI takes each value
    (a field read directly or through a local variable, a string constant, or something it computes)."""
    var = dict(re.findall(r"(?:let|const|var)\s+(\w+)\s*=\s*this\.block\.getFieldValue\(\s*" + Q + r"\s*\)", body))
    out = {}
    for attr, val in re.findall(r"setAttribute\(\s*" + Q + r"\s*,\s*([^;]+?)\)\s*;", body):
        e = out.setdefault(attr, {"fields": [], "consts": [], "derived": False})
        val = val.strip()
        m = re.fullmatch(r"this\.block\.getFieldValue\(\s*" + Q + r"\s*\)", val)
        f = m.group(1) if m else var.get(val)
        if f:
            e["fields"] += [f] if f not in e["fields"] else []
        elif literal(val) is not None:
            e["consts"] += [literal(val)] if literal(val) not in e["consts"] else []
        else:
            e["derived"] = True
    return out


def mutation_order(static, signatures):
    """One attribute order for the serializer: mutationToDom's, with attributes only seen in saved XML inserted after
    the attribute that precedes them there."""
    order = list(static)
    for sig in signatures:
        attrs = sig.split()
        for i, a in enumerate(attrs):
            if a not in order:
                prev = next((attrs[j] for j in range(i - 1, -1, -1) if attrs[j] in order), None)
                order.insert(order.index(prev) + 1 if prev else 0, a)
    return order


def checks(arg):
    arg = arg.strip()
    if arg in ("null", "undefined", ""):
        return None
    return re.findall(Q, arg) or [arg]


def unique(seq):
    seen, out = set(), []
    for x in seq:
        k = json.dumps(x, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def calls(src, fn):
    """Top-level argument lists of every `fn(...)` call (balanced parentheses, quotes skipped)."""
    out = []
    for m in re.finditer(r"\b" + fn + r"\(", src):
        depth, args, cur, i, quote = 1, [], "", m.end(), None
        while i < len(src) and depth:
            ch = src[i]
            if quote:
                quote = None if ch == quote and src[i - 1] != "\\" else quote
            elif ch in "'\"`":
                quote = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if depth == 1 and ch == "," and not quote:
                args.append(cur.strip())
                cur = ""
            elif depth:
                cur += ch
            i += 1
        out.append(args + [cur.strip()])
    return out


def literal(arg):
    m = re.fullmatch(Q, arg.strip())
    return m.group(1) if m else None


def merged(pairs):
    """[(input name, setCheck arg)] -> [{name, check}], one per name, checks unioned (None = accepts anything)."""
    out = {}
    for n, c in pairs:
        ck = checks(c) if c else None
        if n not in out:
            out[n] = ck
        elif out[n] is not None:
            out[n] = None if ck is None else out[n] + [x for x in ck if x not in out[n]]
    return [{"name": n, "check": c} for n, c in out.items()]


def static_shape(src):
    btype = re.search(r"super\((?:\w+\s*\?\?\s*)?" + Q, src) or re.search(r"\w+\s*=\s*type\s*\?\?\s*" + Q, src)
    conn = {}
    for kind in ("PreviousStatement", "NextStatement", "Output"):
        conn[kind] = unique([checks(a) for a in re.findall(r"set" + kind + r"\(\s*true\s*(?:,\s*([^)]*))?\)", src)])
    stmts = merged(re.findall(r"appendStatementInput\(" + Q + r"\)\s*(?:\.setCheck\(([^)]*)\))?", src))
    values = merged(re.findall(r"appendValueInput\(" + Q + r"\)\s*(?:\.setCheck\(([^)]*)\))?", src))
    fields = unique([literal(a[-1]) for a in calls(src, "appendField") if len(a) > 1 and literal(a[-1])])
    dummies = unique(re.findall(r"appendDummyInput\(" + Q + r"\)", src))
    mut = method_text(src, "mutationToDom", "domToMutation")
    attrs = unique(re.findall(r"setAttribute\(\s*" + Q, mut or src))
    sources = mutation_sources(mut or "")
    read = unique(re.findall(r"getAttribute\(\s*" + Q, body_of(src, "domToMutation") or src))
    return {"type": btype.group(1) if btype else None, "connections": conn, "statement_inputs": stmts, "value_inputs": values,
            "dummy_inputs": dummies, "fields": fields, "mutation_attributes": attrs, "mutation_sources": sources,
            "mutation_attributes_read_only": [a for a in read if a not in attrs]}


def counter_patterns(ui):
    """The upload dialog finds the counters to create with regexes over the raw XML: the attribute order each expects."""
    src = open(os.path.join(ui, UPLOAD)).read()
    out = []
    for name, rx in re.findall(r"let (attribute_type_input\w*|common_attribute_type_input)\s*=\s*/(.*?)/gi", src):
        out.append({"regex": name, "attribute_order": re.findall(r"\b([a-z_]+)=\"\(", rx)})
    return out


def evidence(xml_files):
    """block type -> {signatures: {attr order: count}, fields: {name: [sample values]}, parents: {input: count}}."""
    ev = collections.defaultdict(lambda: {"signatures": collections.Counter(), "fields": collections.defaultdict(set),
                                          "inside": collections.Counter(), "picked": set()})

    def walk(el, parent_input):
        for ch in el:
            tag = ch.tag.split("}")[-1]
            if tag in ("block", "shadow"):
                t = ch.get("type")
                e = ev[t]
                if parent_input:
                    e["inside"][parent_input] += 1
                for g in ch:
                    gt = g.tag.split("}")[-1]
                    if gt == "mutation":
                        e["signatures"][" ".join(k for k in g.attrib if k != "xmlns")] += 1
                        has_field = any(x.tag.split("}")[-1] == "field" and x.get("name") == "value" for x in ch)
                        if g.get("value") and not has_field and len(e["picked"]) < 6:
                            e["picked"].add(g.get("value")[:40])
                    elif gt == "field" and len(e["fields"][g.get("name")]) < 6:
                        e["fields"][g.get("name")].add((g.text or "")[:40])
                for g in ch:
                    gt = g.tag.split("}")[-1]
                    if gt in ("statement", "value"):
                        walk(g, f"{t}.{g.get('name')}")
                    elif gt == "next":
                        walk(g, parent_input)
            elif tag in ("statement", "value", "next", "xml"):
                walk(ch, parent_input)

    for f in xml_files:
        try:
            walk(ET.fromstring(open(f).read()), None)
        except ET.ParseError:
            continue
    return {t: {"signatures": dict(e["signatures"].most_common()), "fields": {k: sorted(v) for k, v in e["fields"].items()},
                "inside": dict(e["inside"].most_common(6)), "picked": sorted(e["picked"])} for t, e in ev.items()}


def canvas(ui, srcs, config, evidence_dir, live_path):
    blocks = []
    for cat, cls in toolbox(ui, config):
        f, src = srcs[cls]
        b = dict(static_shape(src), category=cat, cls=cls, source=f)
        blocks.append(b)
    live = json.load(open(live_path)) if live_path and os.path.exists(live_path) else {}
    ev = evidence(sorted(glob.glob(os.path.join(evidence_dir, "*.xml"))))
    for b in blocks:
        b["live"] = live.get(b["type"])
        b["evidence"] = ev.get(b["type"])
        sigs = (b["evidence"] or {}).get("signatures", {})
        b["mutation_order"] = mutation_order(b["mutation_attributes"], sigs)
        # attributes every saved instance of the block carries: the UI writes them unconditionally ("null" if unset)
        b["mutation_always"] = sorted(set.intersection(*[set(x.split()) for x in sigs])) if sigs else []
    return {"toolbox": blocks, "config": config}


def source_hashes(ui, ref):
    files = {UPLOAD} | {c["config"] for c in ref["canvases"].values()} | \
        {f"{BLOCKS}/{b['source']}" for c in ref["canvases"].values() for b in c["toolbox"]}
    return {f: hashlib.sha256(open(os.path.join(ui, f), "rb").read()).hexdigest()[:16] for f in sorted(files)}


def build(ui):
    srcs = class_sources(ui)
    ref = {"canvases": {name: canvas(ui, srcs, *spec) for name, spec in CANVASES.items()}, "counter_patterns": counter_patterns(ui)}
    ref["sources"] = {"ui": ui, "hashes": source_hashes(ui, ref)}
    return ref


def _opts(v, n=12):
    if isinstance(v, list):
        return "[" + ", ".join(map(str, v[:n])) + (f", ... {len(v) - n} more" if len(v) > n else "") + "]"
    return str(v)


def live_lines(live):
    """What the running screen offers: base dropdowns, and per selector option the fields that option adds."""
    if not live or "base" not in live:
        return []
    base = live["base"]
    out = []
    drops = {k: v for k, v in base.items() if not k.startswith("<") and isinstance(v, list)}
    if drops:
        out.append("- live dropdowns: " + "; ".join(f"{k} {_opts(v)}" for k, v in drops.items()))
    for sel, per in live.items():
        if sel == "base" or not isinstance(per, dict):
            continue
        for opt, shape in per.items():
            if not isinstance(shape, dict) or "error" in shape:
                continue
            extra = {k: v for k, v in shape.items() if k not in base or base[k] != v}
            if extra:
                out.append(f"  - {sel}={opt} adds: " + "; ".join(f"{k} {_opts(v)}" for k, v in extra.items()))
    return out


def md(ref, name):
    def c(x):
        return "any" if x is None else "|".join(x)
    out = [f"# {name.title()} canvas block reference (generated from gravty-ui source by tools/block_reference.py; do not edit)",
           f"Toolbox: {ref['config']}", "",
           "Connections: a block's `previous` check must share a name with the check of the statement input (or `next`) it",
           "plugs into; `output` blocks plug into value inputs. `any` = no check. You set fields (and `picked` codes for a",
           "lookup popup); the tools write every mutation attribute from them as the UI does.", ""]
    for b in ref["toolbox"]:
        out.append(f"## {b['type']}  ({b['category']}; {b['cls']}, blocks/{b['source']})")
        cn = b["connections"]
        for k, label in (("PreviousStatement", "previous"), ("NextStatement", "next"), ("Output", "output")):
            if cn[k]:
                out.append(f"- {label}: " + " / ".join(c(x) for x in cn[k]))
        if b["statement_inputs"]:
            out.append("- statement inputs: " + ", ".join(f"{s['name']} [{c(s['check'])}]" for s in b["statement_inputs"]))
        if b["value_inputs"]:
            out.append("- value inputs: " + ", ".join(f"{s['name']} [{c(s['check'])}]" for s in b["value_inputs"]))
        if b["fields"]:
            out.append("- fields: " + ", ".join(b["fields"]))
        out += live_lines(b.get("live"))
        ev = b.get("evidence")
        if ev:
            if ev["inside"]:
                out.append("- seen inside: " + ", ".join(f"{k} ({v})" for k, v in ev["inside"].items()))
            if ev.get("picked"):
                out.append("- saved picked codes: " + " / ".join(ev["picked"]))
            if ev["fields"]:
                out.append("- saved fields: " + "; ".join(f"{k}={'/'.join(v)}" for k, v in ev["fields"].items()))
        out.append("")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default=os.environ.get("GRAVTY_UI", os.path.expanduser("~/Downloads/newui-develop/gravty-ui")))
    a = ap.parse_args()
    ref = build(a.ui)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(CONTEXT, exist_ok=True)
    json.dump(ref, open(os.path.join(DATA, "block_reference.json"), "w"), indent=1)
    for name, cv in ref["canvases"].items():
        open(os.path.join(CONTEXT, "block_reference.md" if name == "rules" else f"block_reference_{name}.md"), "w").write(md(cv, name))
        print(name, len(cv["toolbox"]), "blocks")
    print(len(ref["counter_patterns"]), "counter patterns ->", DATA, CONTEXT)


if __name__ == "__main__":
    main()
