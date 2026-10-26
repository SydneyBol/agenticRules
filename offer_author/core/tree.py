"""Block tree <-> Blockly XML, generically: the model picks blocks and field values, this writes the XML the UI saves.

Node: {"type", "id"?, "fields": {name: value}, "picked"?: "CODE1,CODE2", "inputs": {name: node | [nodes]}}
A statement input holds a list (the blocks chained under it, in order); a value input holds one node. `picked` is the
codes chosen in a lookup popup (the UI keeps them only in the mutation); the offer counters the blocks use are
declared once beside the tree. The whole mutation is derived here as the UI's mutationToDom derives it (`derive`). Which input is which, where each
mutation attribute comes from and their order (the upload dialog finds counters by regex over that order) come from
the block reference generated from the UI source. No block is special-cased here.
"""
import json
import os
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

REF = os.path.join(os.path.dirname(__file__), "data", "block_reference.json")
XMLNS = "https://developers.google.com/blockly/xml"
MUT_NS = "http://www.w3.org/1999/xhtml"

_REF = {}


def reference(canvas="rules"):
    """block type -> generated spec, for one canvas ('rules' or 'segment')."""
    if canvas not in _REF:
        _REF[canvas] = {b["type"]: b for b in json.load(open(REF))["canvases"][canvas]["toolbox"]}
    return _REF[canvas]


class TreeError(ValueError):
    pass


CATALOG_OBJECT = {"activity": "activity", "products_attrs": "product", "member": "member"}


def _data_type(catalog, obj, level, attr):
    """The attribute's data type as the UI reads it from its attribute library (tenant catalog); None if unknown."""
    if catalog is None or not attr:
        return None
    cobj = CATALOG_OBJECT.get(obj)
    if cobj == "member" and level not in (None, "", "null", "MA_P"):
        return None  # counters (MA_O/MA_A) and other levels: not in the attribute library
    a = catalog.attr(cobj, attr) if cobj else None
    if a is None and cobj == "activity" and attr == "h_bit_type":
        return "LOOKUP"
    return a.type if a else None


COUNTER_LEVELS = ("MA_O", "MA_A", "MA_TO")


def counter_ref(mut):
    """(level, name) of the offer/activity counter a block's mutation refers to, else (level, None)."""
    level = mut.get("attribute_level") or mut.get("mta_type_value") or mut.get("attribute_type_input")
    name = mut.get("mta_attribute") if mut.get("mta_type_value") else mut.get("attribute")
    return level, (name if level in COUNTER_LEVELS and name else None)


def _counter_attrs(mut, src, counter, level):
    """What a block that uses an offer counter stores about it, as the UI copies it from the offer's counter list:
    data type; target type for non-TEXT offer counters (MA_O / MA_TO; an activity-level MA_A attribute has none); for a VECTOR counter its namespace, entity level and the ladder
    placeholder the UI saves (`DUMMY VALUE`)."""
    out = {}
    for key in ("type", "attr_type"):
        if key in src:
            out[key] = counter["data_type"]
    if "target_type" in src and counter["data_type"] != "TEXT" and level in ("MA_O", "MA_TO"):
        out["target_type"] = counter.get("target_type") or "NO TARGET"
    if counter.get("namespace"):
        if "vector_counter_namespace" in src:
            out["vector_counter_namespace"] = counter["namespace"]
        if "attribute_entity_map_level" in src:
            out["attribute_entity_map_level"] = counter.get("entity_level") or "Activity"
        if "mto_ladder_value" in src:
            out["mto_ladder_value"] = counter.get("ladder") or "DUMMY VALUE"
    return out


def derive(node, spec, catalog=None, counters=None):
    """The block's mutation as mutationToDom writes it: every attribute the UI reads from a field is taken from the
    field; attributes it always writes are "null" when unset; the vector flag follows vector_counter_namespace; the
    data type and the labels of popup-picked codes come from the catalog. The model's own mutation entries are kept
    only where the UI does not derive them."""
    fields, given = node.get("fields") or {}, dict(node.get("mutation") or {})
    if node.get("picked") not in (None, ""):
        given["value"] = _text(node["picked"])
    src, always = spec.get("mutation_sources") or {}, set(spec.get("mutation_always") or [])
    mut = dict(given)
    saved = {a for sig in ((spec.get("evidence") or {}).get("signatures") or {}) for a in sig.split()}
    readers = {}
    for attr, s in src.items():
        for f in s["fields"]:
            readers.setdefault(f, []).append(attr)
    for attr, s in src.items():
        f = next((f for f in s["fields"] if f in fields), None)
        # a field several attributes read (value -> value, file_upload_toggle) only fills the ones saved XML shows
        if f is not None and (len(readers[f]) == 1 or attr in saved or attr == f):
            mut[attr] = _text(fields[f])
    for attr in always:
        if attr not in mut and src.get(attr, {}).get("fields") and not src[attr]["consts"] and not src[attr]["derived"]:
            mut[attr] = "null"
    level, name = counter_ref(mut)
    counter = (counters or {}).get(name) if name else None
    if counter:
        mut.update(_counter_attrs(mut, src, counter, level))
    if "iscountertypevector" in src and ("iscountertypevector" in always or "iscountertypevector" in given):
        mut["iscountertypevector"] = "yes" if mut.get("vector_counter_namespace") else "no"
    obj = mut.get("object") or ("activity" if "operators" in mut and "object" not in src else None)
    for key in ("type", "attr_type"):
        if key in src and src[key]["derived"] and not counter:
            t = _data_type(catalog, obj or ("member" if level else None), level, mut.get("attribute"))
            if t:
                mut[key] = t
    if "value" in given and "visible_value" in src and catalog is not None and "visible_value" not in fields:
        a = catalog.attr(CATALOG_OBJECT.get(obj, ""), mut.get("attribute", "")) if obj else None
        labels = (a.values if a and a.values else (catalog.activity_types if mut.get("attribute") == "h_bit_type" else {})) or {}
        codes = [c for c in str(given["value"]).split(",") if c]
        if codes and all(c in labels for c in codes):
            mut["visible_value"] = ",".join(labels[c] for c in codes)
    return mut


def _nodes(root):
    """Every node of a tree with the input that holds it: [(node, container, key)], container a dict or list."""
    out = []

    def walk(node, holder, key):  # depth first, inputs and chains in order: the order to_xml writes the blocks in
        if not isinstance(node, dict):
            return
        out.append((node, holder, key))
        for name, child in (node.get("inputs") or {}).items():
            if isinstance(child, list):
                for i, c in enumerate(child):
                    walk(c, child, i)
            else:
                walk(child, node["inputs"], name)

    walk(root, None, None)
    return out


def number(root):
    """A copy of the tree with an id on every block: the model's own ids are kept, the rest get the next free b<n>.
    Problems name blocks by these ids, and compile edits refer to them."""
    root = json.loads(json.dumps(root))
    nodes = [n for n, _, _ in _nodes(root)]
    used, k = {str(n["id"]) for n in nodes if n.get("id")}, 0
    for n in nodes:  # the root first
        if not n.get("id"):
            k += 1
            while f"b{k}" in used:
                k += 1
            n["id"] = f"b{k}"
            used.add(n["id"])
    return root


def apply_edits(root, edits):
    """The last tree with edits applied, by block id: {"id", "fields": {...}} merges fields, "picked" replaces the
    picked codes, "replace": node swaps the block (and what hangs under it), "remove": true drops it."""
    root = json.loads(json.dumps(root))
    for e in edits or []:
        if not isinstance(e, dict) or not e.get("id"):
            raise TreeError("every edit needs the id of a block in the last tree")
        hit = next(((n, h, k) for n, h, k in _nodes(root) if str(n.get("id")) == str(e["id"])), None)
        if hit is None:
            raise TreeError(f"no block {e['id']} in the last tree")
        node, holder, key = hit
        if e.get("remove"):
            if holder is None:
                raise TreeError("the root block cannot be removed")
            if isinstance(holder, list):
                holder.pop(key)
            else:
                holder.pop(key)
            continue
        if e.get("replace") is not None:
            new = dict(e["replace"])
            if holder is None:
                root = new
            else:
                holder[key] = new
            node = new
        if e.get("fields"):
            node["fields"] = {**(node.get("fields") or {}), **e["fields"]}
        if "picked" in e:
            node["picked"] = e["picked"]
    return root


VECTOR_MODES = ("across", "unique")  # block-definition.service.ts getMAOVectorattributes: the options after the first


def ui_fields(node, mut, counters, catalog):
    """The block's fields as the stock screen leaves them, where its dropdown values are fixed by data the tools know.
    vector_counter_name (a condition on a VECTOR counter): the options are [the namespace attribute, across, unique];
    the first one's value is the attribute code for a NUMBER counter and its label otherwise, so any other text the
    model gives for "the attribute itself" becomes that value (the screen rewrote ' Location ' to 'h_location')."""
    fields = node.get("fields") or {}
    v = fields.get("vector_counter_name")
    level, name = counter_ref(mut)
    counter = (counters or {}).get(name) if name else None
    if v is None or not counter or not counter.get("namespace") or str(v).strip() in VECTOR_MODES:
        return fields
    code = counter["namespace"]
    obj = "member" if (counter.get("entity_level") or "Activity") == "Member" else "activity"
    a = catalog.attr(obj, code) if catalog is not None else None
    first = code if counter.get("data_type") == "NUMBER" else (a.label if a else code)
    return dict(fields, vector_counter_name=first)


def to_xml(root, catalog=None, counters=None, canvas="rules"):
    """-> (xml, paths): paths maps every block id to where it sits, for readable problem reports.
    counters: the offer counters the rules use, {name: {data_type, counter_type, target_type?, namespace?, entity_level?}}."""
    ref, paths, n = reference(canvas), {}, [0]
    if isinstance(root, list):
        if len(root) != 1:
            raise TreeError("the tree must have exactly one root block (offer_generic)")
        root = root[0]

    def block(node, where):
        if not isinstance(node, dict) or not node.get("type"):
            raise TreeError(f"{where}: every block needs a type")
        t = node["type"]
        spec = ref.get(t, {})
        n[0] += 1
        bid = str(node.get("id") or f"b{n[0]}")
        label = t + (f" '{node['fields']['IDENTIFIER']}'" if (node.get("fields") or {}).get("IDENTIFIER") else "")
        here = f"{where} > {label}" if where else label
        paths[bid] = here
        parts = [f"<block type={quoteattr(t)} id={quoteattr(bid)}>"]
        mut = derive(node, spec, catalog, counters) if spec else dict(node.get("mutation") or {})
        if spec:
            fixed = ui_fields(node, mut, counters, catalog)
            if fixed is not (node.get("fields") or {}):
                node = dict(node, fields=fixed)
                mut = derive(node, spec, catalog, counters)
        if mut:
            order = spec.get("mutation_order") or []
            keys = [k for k in order if k in mut] + [k for k in mut if k not in order]
            parts.append(f'<mutation xmlns="{MUT_NS}"' + "".join(f" {k}={quoteattr(_text(mut[k]))}" for k in keys) + "></mutation>")
        for k, v in (node.get("fields") or {}).items():
            parts.append(f"<field name={quoteattr(k)}>{escape(_text(v))}</field>")
        statements = {i["name"] for i in spec.get("statement_inputs") or []}
        for name, child in (node.get("inputs") or {}).items():
            kids = child if isinstance(child, list) else [child]
            if name in statements:
                parts.append(f"<statement name={quoteattr(name)}>{chain(kids, f'{here}.{name}')}</statement>")
            else:
                if len(kids) != 1:
                    raise TreeError(f"{here}: value input {name} takes exactly one block")
                parts.append(f"<value name={quoteattr(name)}>" + "".join(block(kids[0], f"{here}.{name}")) + "</block></value>")
        return parts

    def chain(nodes, where):
        if not nodes:
            return ""
        head = block(nodes[0], where)
        rest = chain(nodes[1:], where)
        return "".join(head) + (f"<next>{rest}</next>" if rest else "") + "</block>"

    body = chain([root], "")
    return f'<xml xmlns="{XMLNS}">{body}</xml>', paths


def _text(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def from_xml(xml):
    """Blockly XML -> the root block as a tree (ids and canvas positions dropped)."""
    root = ET.fromstring(xml)
    tag = lambda e: e.tag.split("}")[-1]  # noqa: E731

    def node(b):
        out = {"type": b.get("type")}
        for ch in b:
            k = tag(ch)
            if k == "mutation":
                out["mutation"] = dict(ch.attrib)
            elif k == "field":
                out.setdefault("fields", {})[ch.get("name")] = ch.text or ""
            elif k == "statement":
                out.setdefault("inputs", {})[ch.get("name")] = chain(next((c for c in ch if tag(c) == "block"), None))
            elif k == "value":
                kid = next((c for c in ch if tag(c) in ("block", "shadow")), None)
                if kid is not None:
                    out.setdefault("inputs", {})[ch.get("name")] = node(kid)
        return out

    def chain(b):
        out = []
        while b is not None:
            out.append(node(b))
            nxt = next((c for c in b if tag(c) == "next"), None)
            b = next((c for c in nxt if tag(c) == "block"), None) if nxt is not None else None
        return out

    tops = [b for b in root if tag(b) == "block"]
    return node(tops[0]) if len(tops) == 1 else [node(b) for b in tops]


def selections(xml):
    """Saved XML -> (tree, counters) in the form the model writes: fields, picked codes and inputs per block, and the
    offer counters the blocks refer to; every other mutation attribute is left for `derive`."""
    root = from_xml(xml)
    counters = {}

    def walk(n):
        if isinstance(n, list):
            return [walk(c) for c in n]
        n = dict(n)
        m = n.pop("mutation", None) or {}
        if "value" in m and "value" not in (n.get("fields") or {}):
            n["picked"] = m["value"]
        level, name = counter_ref(m)
        if name and (m.get("type") or m.get("attr_type")):
            c = counters.setdefault(name, {"name": name, "level": level, "data_type": m.get("type") or m.get("attr_type")})
            if m.get("target_type"):
                c["target_type"] = m["target_type"]
            if m.get("vector_counter_namespace"):
                c.update(counter_type="VECTOR", namespace=m["vector_counter_namespace"])
                if m.get("attribute_entity_map_level"):
                    c["entity_level"] = m["attribute_entity_map_level"]
            c.setdefault("counter_type", "REGULAR")
        n["inputs"] = {k: walk(v) for k, v in (n.get("inputs") or {}).items()}
        if not n["inputs"]:
            n.pop("inputs")
        return n
    return walk(root), list(counters.values())
