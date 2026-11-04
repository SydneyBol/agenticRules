"""What the screen derives for a block: its mutation, and the fields the tools fill in.

The mutation is derived as the UI's mutationToDom derives it; the filled fields are a condition's object and level and
an action's counter level, account and reward tag. Where each mutation attribute comes from, and their order, come
from the block reference; no block is special-cased here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...domain import Block
from ..vocabulary import ATTRIBUTE_LEVELS, CATALOG_OBJECT, COUNTER_LEVELS, MEMBER_ATTRIBUTE, OFFER_COUNTER, TIER_COUNTER

if TYPE_CHECKING:
    from ..tenant.catalog import Catalog

Fields = dict[str, Any]
Spec = dict[str, Any]
Counters = dict[str, dict[str, Any]]

VECTOR_MODES = ("across", "unique")  # block-definition.service.ts getMAOVectorattributes: the options after the first
CONDITIONS = ("if_generic_adaptive", "activity_criteria")


def data_type(catalog: Catalog | None, obj: str | None, level: str | None, attr: str | None) -> str | None:
    """The attribute's data type as the UI reads it from its attribute library (tenant catalog); None if unknown."""
    if catalog is None or not attr:
        return None
    cobj = CATALOG_OBJECT.get(obj or "")
    if cobj == "member" and level not in ATTRIBUTE_LEVELS:
        return None  # counters (MA_O/MA_A) and other levels: not in the attribute library
    a = catalog.attr(cobj, attr) if cobj else None
    return a.type if a else None


def counter_ref(mut: dict[str, Any]) -> tuple[str | None, str | None]:
    """The (level, name) of the offer or activity counter a block's mutation refers to, else (level, None)."""
    level = mut.get("attribute_level") or mut.get("mta_type_value") or mut.get("attribute_type_input")
    name = mut.get("mta_attribute") if mut.get("mta_type_value") else mut.get("attribute")
    return level, (name if level in COUNTER_LEVELS and name else None)


def _counter_attrs(src: Spec, counter: dict[str, Any], level: str | None) -> dict[str, Any]:
    """What a block that uses an offer counter stores about it, as the UI copies it from the offer's counter list.

    Its data type; a target type for non-TEXT offer counters (MA_O / MA_TO; an activity-level MA_A attribute has
    none); for a VECTOR counter its namespace, entity level and the ladder placeholder the UI saves (`DUMMY VALUE`).
    """
    copied = {"type": counter["data_type"], "attr_type": counter["data_type"]}
    if counter["data_type"] != "TEXT" and level in (OFFER_COUNTER, TIER_COUNTER):
        copied["target_type"] = counter.get("target_type") or "NO TARGET"
    if counter.get("namespace"):
        copied.update(
            vector_counter_namespace=counter["namespace"],
            attribute_entity_map_level=counter.get("entity_level") or "Activity",
            mto_ladder_value=counter.get("ladder") or "DUMMY VALUE",
        )
    return {k: v for k, v in copied.items() if k in src}


def _from_fields(spec: Spec, fields: Fields, given: dict[str, Any]) -> dict[str, Any]:
    """The mutation attributes the UI reads from fields, over the model's own entries.

    Those it always writes are "null" when unset.
    """
    src, always = spec.get("mutation_sources") or {}, set(spec.get("mutation_always") or [])
    saved = {a for sig in ((spec.get("evidence") or {}).get("signatures") or {}) for a in sig.split()}
    readers = Counter(f for s in src.values() for f in s["fields"])  # field -> how many attributes read it
    mut = dict(given)
    for attr, s in src.items():
        f = next((f for f in s["fields"] if f in fields), None)
        # a field several attributes read (value -> value, file_upload_toggle) only fills the ones saved XML shows
        if f is not None and (readers[f] == 1 or attr in saved or attr == f):
            mut[attr] = field_text(fields[f])
    unset = {a for a in always if a not in mut and _from_field_only(src.get(a, {}))}
    return mut | dict.fromkeys(unset, "null")


def _from_field_only(source: dict[str, Any]) -> bool:
    """A mutation attribute the UI takes from a field alone (no constant, not derived): "null" when it is unset."""
    return bool(source.get("fields")) and not source["consts"] and not source["derived"]


def _visible_value(catalog: Catalog, obj: str | None, attribute: str | None, value: Any) -> str | None:
    """The labels of popup-picked codes, comma-joined, when the catalog knows every one; else None."""
    a = catalog.attr(CATALOG_OBJECT.get(obj, ""), attribute or "") if obj else None
    labels = (a.values if a else None) or {}
    codes = [c for c in str(value).split(",") if c]
    return ",".join(labels[c] for c in codes) if codes and all(c in labels for c in codes) else None


def derive(node: Block, spec: Spec, catalog: Catalog | None = None, counters: Counters | None = None) -> dict[str, Any]:
    """The block's mutation as mutationToDom writes it.

    Every attribute the UI reads from a field is taken from the field; attributes it always writes are "null" when
    unset; the vector flag follows vector_counter_namespace; the data type and the labels of popup-picked codes come
    from the catalog. The model's own mutation entries are kept only where the UI does not derive them.

    Args:
        node: The block, in the node form.
        spec: Its block reference entry.
        catalog: The tenant catalog, or None (no catalog-derived attributes).
        counters: The offer counters the canvas declares, by name.

    Returns:
        The mutation attributes, in the order they were derived.
    """
    fields, given = node.get("fields") or {}, dict(node.get("mutation") or {})
    if node.get("picked") not in (None, ""):
        given["value"] = field_text(node["picked"])
    src, always = spec.get("mutation_sources") or {}, set(spec.get("mutation_always") or [])
    mut = _from_fields(spec, fields, given)
    level, name = counter_ref(mut)
    counter = (counters or {}).get(name) if name else None
    if counter:
        mut.update(_counter_attrs(src, counter, level))
    if "iscountertypevector" in src and ("iscountertypevector" in always or "iscountertypevector" in given):
        mut["iscountertypevector"] = "yes" if mut.get("vector_counter_namespace") else "no"
    if catalog is not None:
        mut.update(_from_catalog(catalog, mut, src, level, fields, given, counter))
    return mut


def _from_catalog(
    catalog: Catalog,
    mut: dict[str, Any],
    src: Spec,
    level: str | None,
    fields: Fields,
    given: dict[str, Any],
    counter: dict[str, Any] | None,
) -> dict[str, Any]:
    """The mutation attributes the UI takes from the catalog.

    The attribute's data type (not for a counter) and the labels of popup-picked codes.
    """
    out, obj = {}, mut.get("object") or ("activity" if "operators" in mut and "object" not in src else None)
    for key in ("type", "attr_type"):
        if key in src and src[key]["derived"] and not counter:
            t = data_type(catalog, obj or ("member" if level else None), level, mut.get("attribute"))
            if t:
                out[key] = t
    if "value" in given and "visible_value" in src and "visible_value" not in fields:
        labels = _visible_value(catalog, obj, mut.get("attribute"), given["value"])
        if labels is not None:
            out["visible_value"] = labels
    return out


def ui_fields(node: Block, mut: dict[str, Any], counters: Counters | None, catalog: Catalog | None) -> Fields:
    """The block's fields as the stock screen leaves them, where its dropdown values are fixed by data the tools know.

    vector_counter_name (a condition on a VECTOR counter): the options are [the namespace attribute, across, unique];
    the first one's value is the attribute code for a NUMBER counter and its label otherwise, so any other text the
    model gives for "the attribute itself" becomes that value (the screen rewrote ' Location ' to 'h_location').
    """
    fields = node.get("fields") or {}
    v = fields.get("vector_counter_name")
    _, name = counter_ref(mut)
    counter = (counters or {}).get(name) if name else None
    if v is None or not counter or not counter.get("namespace") or str(v).strip() in VECTOR_MODES:
        return fields
    code = counter["namespace"]
    obj = "member" if (counter.get("entity_level") or "Activity") == "Member" else "activity"
    a = catalog.attr(obj, code) if catalog is not None else None
    first = code if counter.get("data_type") == "NUMBER" else (a.label if a else code)
    return dict(fields, vector_counter_name=first)


def _put(fields: Fields, name: str, value: Any, after: str | None) -> Fields:
    """Fields with name added right after `after` (first when None): where the screen's saved XML has it."""
    out = {name: value} if after is None else {}
    for k, v in fields.items():
        out[k] = v
        if k == after:
            out[name] = value
    out.setdefault(name, value)
    return out


def _fill_condition(f: Fields, catalog: Catalog | None, counters: Counters) -> Fields:
    """A condition's OBJECT and attribute_level, written right after OBJECT as the screen saves them.

    OBJECT is member for a counter or a member attribute, activity for an activity attribute; attribute_level is the
    counter's level, or MA_P for a member attribute. The screen rebuilds the attribute dropdown when the level is set,
    so a level after the attribute clears it (devintbf2 2026-10-26, "Member program level attribute is not selected").
    """
    code = f.get("attribute")
    counter = counters.get(code)  # type: ignore[arg-type]

    def known(obj: str) -> bool:
        return bool(catalog is not None and code and catalog.attr(obj, code))

    if "OBJECT" not in f and (obj := _condition_object(f, counter, known)):
        f = _put(f, "OBJECT", obj, None)
    if f.get("OBJECT") == "member" and "attribute_level" not in f:
        level = (counter.get("level") or OFFER_COUNTER) if counter else MEMBER_ATTRIBUTE if known("member") else None
        if level:
            f = _put(f, "attribute_level", level, "OBJECT")
    return _level_before_attribute(f)


def _condition_object(f: Fields, counter: dict[str, Any] | None, known: Callable[[str], bool]) -> str | None:
    """Member for a counter or a level, activity for an activity attribute, member for a member attribute."""
    if counter or f.get("attribute_level"):
        return "member"
    return "activity" if known("activity") else "member" if known("member") else None


def _level_before_attribute(f: Fields) -> Fields:
    """attribute_level moved right after OBJECT when it comes after the attribute (the screen clears it otherwise)."""
    keys = list(f)
    if not ("attribute_level" in f and "attribute" in f and keys.index("attribute_level") > keys.index("attribute")):
        return f
    rest = {k: v for k, v in f.items() if k != "attribute_level"}
    return _put(rest, "attribute_level", f["attribute_level"], "OBJECT" if "OBJECT" in f else None)


def fill(node: Block, catalog: Catalog | None = None, counters: Counters | None = None) -> Fields:
    """The block's fields with those the model may leave out, filled as the screen would have them.

    - a condition (if_generic_adaptive, activity_criteria): OBJECT and attribute_level (_fill_condition);
    - give_points: LOYALTY_ACCOUNT, the catalog's offer_account, and REWARD_TAGS, that account's reward tag
      (the configured one, or its only one), when the model chose none;
    - update_attribute: mta_type_value, the level of the counter it updates.

    A field the model gave is never changed.
    """
    t, f = node.get("type"), dict(node.get("fields") or {})
    counters, inputs = counters or {}, node.get("inputs") or {}
    if t in CONDITIONS:
        return _fill_condition(f, catalog, counters)
    if t == "action" and f.get("action_type") == "give_points" and catalog is not None:
        if "LOYALTY_ACCOUNT" not in f and "LOYALTY_ACCOUNT" not in inputs and catalog.offer_account:
            f = _put(f, "LOYALTY_ACCOUNT", str(catalog.offer_account), "action_type")
        tag = catalog.reward_tag(f.get("LOYALTY_ACCOUNT"))  # the chosen account's
        if "REWARD_TAGS" not in f and "REWARD_TAGS" not in inputs and tag:
            f = _put(f, "REWARD_TAGS", tag, "LOYALTY_ACCOUNT" if "LOYALTY_ACCOUNT" in f else "action_type")
    elif t == "action" and f.get("action_type") == "update_attribute" and "mta_type_value" not in f:
        counter = counters.get(f.get("mta_attribute"))  # type: ignore[arg-type]
        if counter:
            f = _put(f, "mta_type_value", counter.get("level") or OFFER_COUNTER, "action_type")
    return f


def field_text(v: Any) -> str:
    """A field value as the XML writes it (booleans as true / false)."""
    return ("true" if v else "false") if isinstance(v, bool) else str(v)
