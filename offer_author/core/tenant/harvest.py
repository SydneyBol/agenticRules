"""Saved offers into a tenant's history (history.py), the way Pulsar's nightly job collects them.

Pulsar collects them in S3 <program>/gpt/data_store; the flaws its review found (~/Documents/Gravty/PulsarRAG-Review.md
§5) are fixed here:

- one version per offer, the newest: a re-saved offer replaces the version stored before, so a corrected offer is no
  longer shown as it was (Pulsar appends a row per update day and keeps the first examples of a shape for good);
- the saved rules XML is the truth about what the offer does, but not about the catalog: the UI copies each block's
  attribute type into the XML when the block is built, and the same attribute or counter is saved with different
  types in different offers and tenants (demov4: counter "First Transaction Date" NUMBER in one offer, DATE in
  another), and launched offers hold half-built blocks (a counter incremented by an empty attribute). `vet` checks a
  saved XML the way the rules screen would load it, against the tenant's catalog as it is now; an offer with a problem
  is not stored, and not shown when the catalog changed after it was stored (Histories.get vets again);
- the manager's description is the prompt typed for that offer (`PromptLog`), else the offer's name and description;
  never a description written by a model.
"""

from __future__ import annotations

import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any

from ...domain import Problem
from ..canvas.checks.grammar import NS, _tag, check, disabled
from ..canvas.fields import counter_ref
from ..vocabulary import ATTRIBUTE_LEVELS, CATALOG_OBJECT, COUNTER_LEVELS
from .structure import FORM

if TYPE_CHECKING:
    from .catalog import Attribute, Catalog

UNSET = (None, "", "null", "undefined")
NAMED_BY = ("attribute", "ATTRIBUTE", "mta_attribute")  # fields that name the attribute or counter a block uses

Record = dict[str, Any]


def _live_blocks(el: ET.Element) -> Iterator[ET.Element]:
    """Every block Blockly turns into code: not under a disabled block."""
    if el.tag == NS + "block":
        if disabled(el):
            return
        yield el
    for ch in el:
        yield from _live_blocks(ch)


def vet(xml: str, catalog: Catalog | None = None) -> list[Problem]:
    """The problems of a saved rules XML.

    What `check` finds (toolbox, nesting, catalog codes), a catalog attribute saved with another type than the catalog
    gives it now, a counter saved with no type or with two, and a block whose attribute or counter field is empty.
    """
    out = check(xml, catalog)
    if out and out[0].code == "not_well_formed":
        return out
    counters: dict[str, set[str | None]] = {}  # counter name -> the data types its blocks were saved with (None: none)
    for b in _live_blocks(ET.fromstring(xml)):
        out += _block_problems(b, catalog, counters)
    for name, types in counters.items():
        if None in types:
            out.append(Problem.of("counter_saved_without_type", counter=name))
        elif len(types) > 1:
            out.append(Problem.of("counter_saved_with_types", counter=name, types=sorted(t or "" for t in types)))
    return out


def _block_problems(b: ET.Element, catalog: Catalog | None, counters: dict[str, set[str | None]]) -> list[Problem]:
    """One block's problems, recording the counter's saved type in `counters`.

    A counter reference with no name, a catalog attribute saved with another type, an empty attribute or counter field.
    """
    m = next((c.attrib for c in b if _tag(c) == "mutation"), {})  # the UI writes it in the XHTML namespace
    where, saved = f"{b.get('type')}#{b.get('id')}", _saved_type(m)
    level, name = counter_ref(m)
    out = []
    if name:
        counters.setdefault(name, set()).add(saved)
    elif level in COUNTER_LEVELS:
        out.append(Problem.of("counter_without_name", at=where, level=level))
    elif saved and (a := _catalog_attr(b.get("type") or "", m, catalog)) and saved != a.type:
        out.append(Problem.of("saved_type_differs", at=where, attribute=a.code, catalog_type=a.type, saved=saved))
    if empty := [
        f.get("name") for f in b if _tag(f) == "field" and f.get("name") in NAMED_BY and not (f.text or "").strip()
    ]:
        out.append(Problem.of("field_empty", at=where, fields=empty))
    return out


def _saved_type(m: Mapping[str, str]) -> str | None:
    """The data type the UI wrote into a block's mutation (type, else attr_type), None when unset."""
    t = m.get("type") if m.get("type") not in UNSET else m.get("attr_type")
    return None if t in UNSET else t


def _catalog_attr(block_type: str, m: Mapping[str, str], catalog: Catalog | None) -> Attribute | None:
    """The catalog attribute a condition or attribute block reads (not a counter), or None."""
    obj = CATALOG_OBJECT.get(m.get("object") or "") or CATALOG_OBJECT.get(block_type.removesuffix("_attribute"))
    if catalog is None or not obj or m.get("attribute") in UNSET or m.get("attribute_level") not in ATTRIBUTE_LEVELS:
        return None
    return catalog.attr(obj, m["attribute"])


class PromptLog:
    """The descriptions managers typed for each offer (the GenAI popup).

    Kept in <root>/<tenant>/prompts.jsonl, one per line: {offer_id, prompt, at}. Pulsar keeps them in S3
    prompts/<offer_id>/offerrules_bin; here they stay until the offer's history entry is rebuilt, never deleted on
    read.
    """

    def __init__(self, root: str | None) -> None:
        self.root, self.lock = root, threading.Lock()

    def _path(self, tenant: str) -> str:
        """The tenant's prompts file."""
        return os.path.join(self.root or "", tenant, "prompts.jsonl")

    def add(self, tenant: str | None, offer_id: Any, prompt: str | None) -> None:
        """Records a prompt typed for an offer (nothing without a root, tenant, offer or prompt)."""
        if not (self.root and tenant and offer_id and (prompt or "").strip()):
            return
        line = json.dumps(
            {"offer_id": str(offer_id), "prompt": (prompt or "").strip(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        )
        with self.lock:
            os.makedirs(os.path.dirname(self._path(tenant)), exist_ok=True)
            with open(self._path(tenant), "a") as f:
                f.write(line + "\n")

    def last(self, tenant: str) -> dict[str, str]:
        """The last prompt typed for each offer: {offer_id: prompt}."""
        return {r["offer_id"]: r["prompt"] for r in read(self._path(tenant))}


def read(path: str) -> list[Record]:
    """A JSONL file's records ([] when there is no file)."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def harvest(
    saved: Iterable[Record],
    store: str,
    catalog: Catalog | None = None,
    prompts: Mapping[str, str] | None = None,
    subprompt: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Merges saved offers into a history file.

    Per offer: kept as stored when nothing newer was saved; else the newest version replaces it when `vet` finds no
    problem, and the stored one is dropped when it does (it no longer is the offer).

    Args:
        saved: [{id, name, description, xml, updated, prompt?}], any number of versions per offer (the newest, by
            `updated`, is the offer).
        store: The history file.
        catalog: The tenant catalog the offers are vetted against.
        prompts: {offer_id: prompt} (PromptLog.last), used when a version has none.
        subprompt: xml -> Pulsar's sub-prompt lines (api/harvest.py), stored with every offer that has none yet or has
            them in an earlier form (structure.FORM): the agent is shown an earlier offer only with them (history.py).

    Returns:
        {added, replaced, kept, rejected: {id: [Problem]}, removed: [id]}.
    """
    newest = {str(r["id"]): r for r in sorted(saved, key=_updated)}  # the last one wins
    stored = {str(r["id"]): r for r in read(store)}
    report: dict[str, Any] = {"added": 0, "replaced": 0, "kept": 0, "rejected": {}, "removed": []}
    for k, r in newest.items():
        old = stored.get(k)
        if old and _updated(old) >= _updated(r):
            report["kept"] += 1
        elif problems := vet(r["xml"], catalog) if r.get("xml") else [Problem.of("no_saved_rules")]:
            report["rejected"][k] = problems
            if stored.pop(k, None) is not None:
                report["removed"].append(k)
        else:
            report["replaced" if old else "added"] += 1
            stored[k] = {
                "id": k,
                "prompt": r.get("prompt") or (prompts or {}).get(k, ""),
                "xml": r["xml"],
                **{f: r.get(f) or "" for f in ("name", "description", "updated", "launched")},
            }
    if subprompt:
        for r in stored.values():
            if not r.get("subprompt") or r.get("subprompt_form") != FORM:
                r["subprompt"], r["subprompt_form"] = subprompt(r["xml"]), FORM
    _write(store, stored.values())
    return report


def _updated(r: Record) -> str:
    """When a version was saved ("" when unknown)."""
    return r.get("updated") or ""


def _write(path: str, rows: Iterable[Record]) -> None:
    """A JSONL file written whole and then renamed: readers (Histories) see the old file or the new one, never half."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(f"{path}.tmp", "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    os.replace(f"{path}.tmp", path)
