"""The sub-prompt DSL (v2): an offer's rules canvas as numbered lines, and back (SubpromptDSL-TwoPipeline-Plan.md).

The user's line form (ruleBook.txt, 2025): one statement per line, `<label>, <body>`; Filters before rules; the lines
of one label are ANDed. v2 adds only what a canvas needs and the old form could not say: rule names, counters,
a rule inside a rule, OR groups, several activities and events, code lists, payment rules, and any block by name.
What depends on an attribute's catalog entry (data types, level fields, codes vs labels) is not written here: the
compose step derives it from the catalog, as it does for the JSON tree.

    counter, "Bonus Points Earned" level=MA_O data_type=NUMBER counter_type=REGULAR target_type=ACCUMULATIVE
    Filter, activity h_bit_type == SPEND
    Filter, activity h_location in [HYD, KOL]
    rule_1 "Gold band", if member [MA_P] tier_class == GLDGLW
    rule_1, if activity h_bit_amount between (50) and (99.99)
    rule_1 or1.1, if activity h_bit_date >= (date(2026-03-08)) datetime_operator=DATE
    rule_1 or1.2, if sponsor business_unit == FNB
    rule_1, give_points account=9 tag=BASE (activity.h_bit_amount * 2)
    rule_2 "Cap" inside rule_1, if member "Bonus Points Earned" <= (2000)
    rule_2, update_attribute "Bonus Points Earned" INCREMENT_BY (activity.h_bit_amount * 2)
    payment_rule_3 "HDFC", if payment bank_code in [HDFC]
    activity_2,                      (a second activity: the lines after it belong to it)
    event_1,                         (an On Event section)

Labels: `Filter` (a criterion of the current activity), `rule_N` / `rule_line_N` / `payment_rule_N` (numbered once
across the offer; the first line of a rule may carry its name and `inside <label>`), `orK.B` after the label (the
line is in branch B of OR group K, groups nest left to right), `counter`, `activity_K`, `event_K`.

Bodies:
- condition: `[if] <object> [[<level>]] <attribute> <operator> [<right>] [and <right>] [key=value ...]`; right is a
  value (`SPEND`, `"two words"`), a code list (`[A, B]`) or a block in parentheses (`(100)`, `(a * b)`).
  `member` is the member's own attribute (no OBJECT), `payment` a payment item's attribute (Payment Rule).
- action: `[for <third party>] <action_type> [<counter> <operator>] [key=value ...] [<value block>]`.
- `block <expression>`: any block, `type(key=value, key=(block), key=[block, ...])`.
- expressions: `12.5`, `"text"`, `date(2026-03-08)`, `activity.<attr>`, `member.<level>.<attr>`,
  `(a op b)`, `(date op 1 DAYS)`, `type(...)`, `$name`; `{key=value}` after an attribute gives its other fields.
- `def, <name>, <expression>`: a value written once and used as `$name` in the lines after it (the JSON tree's
  defs); `# ...` lines are notes and build nothing.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..vocabulary import ACTIVE_CARD_FIELDS, ACTIVE_CARDS, LEVEL_FIELDS

Block = dict[str, Any]

RULE_TYPES = ("rule", "rule_line", "payment_rule")
OBJECTS = ("activity", "sponsor", "location", "airport", "products_attrs", "third_party", "product")
MEMBER, PAYMENT = "member", "payment"  # subject words: a condition without OBJECT; a payment item's attribute
OPERATORS = ("==", "!=", ">=", "<=", ">", "<", "in", "not_in", "between", "null", "not_null", "match", "not_match")
ARITHMETIC = ("*", "/", "+", "-", "%")
NUMBER = re.compile(r"-?\d+(\.\d+)?")
BARE = re.compile(r"[^\s()\[\]{},\"=]+")
OPTION = re.compile(r"([A-Za-z_]\w*)=(.*)", re.S)
LABEL = re.compile(r"(rule_line|payment_rule|rule)_(\d+)")
SECTION = re.compile(r"(activity|event)_(\d+)")
OR_PATH = re.compile(r"or(\d+)\.([12])")
RESERVED = frozenset(("if", "and", "for", "block", "inside", *OPERATORS, *ARITHMETIC))
TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|([()\[\]{},])|([^\s()\[\]{},"]+))')

# Short names for action fields a manager reads (the block's own field name otherwise).
ALIASES = {
    "LOYALTY_ACCOUNT": "account",
    "REWARD_TAGS": "tag",
    "PENDING_DAYS_VALUE": "pending_days",
    "SELECTED_PRIVILEGE": "privilege",
    "PRIVILEGE_USAGEPOLICY": "policy",
    "previlege_Validity": "validity",
    "PRIVILEGE_QUANTITY": "quantity",
}
UNALIAS = {v: k for k, v in ALIASES.items()}
RIGHT_KEYS = ("value", "picked", "RHS_INPUT")
ACTION_TYPES = ("action", "action_third_party")


class DslError(ValueError):
    """A line that does not parse: its number (1-based), the line and what was expected there."""

    def __init__(self, line: int, text: str, expected: str) -> None:
        super().__init__(f"line {line}: {expected}: {text}")
        self.line, self.text, self.expected = line, text, expected


# ---- writing ---------------------------------------------------------------------------------------------------------
def val(s: Any) -> str:
    """A field value: bare when it cannot be read as anything else, else a JSON string."""
    s = "" if s is None else str(s)
    return s if BARE.fullmatch(s) and s not in RESERVED and not s.endswith(".") else json.dumps(s, ensure_ascii=False)


def _is_arith(n: Block) -> bool:
    return n.get("type") in ("arithmetic_generic", "arithmetic_date") and _fits(n, _ARITH_KEYS[n["type"]])


_ARITH_KEYS = {
    "arithmetic_generic": ({"operator", "LHS", "RHS"}, {"operator", "LHS", "RHS"}),
    "arithmetic_date": ({"operator", "number", "date_quantity", "LHS"}, {"operator", "number", "date_quantity", "LHS"}),
}


def _fits(n: Block, keys: tuple[set[str], set[str]]) -> bool:
    """The block has every required key and no key outside the allowed ones (besides type)."""
    need, allowed = keys
    own = set(n) - {"type"}
    return need <= own and own <= allowed


def blk(n: Block) -> str:
    """A block where a field value could also stand: in parentheses (an arithmetic block has its own)."""
    s = expr(n)
    return s if s.startswith("(") else f"({s})"


def _opts(n: Block, keys: list[str], alias: bool = False) -> list[str]:
    out = []
    for k in keys:
        v, name = n[k], (ALIASES.get(k, k) if alias else k)
        if isinstance(v, dict):
            out.append(f"{name}={blk(v)}")
        elif isinstance(v, list):
            out.append(f"{name}=[" + ", ".join(expr(x) for x in v) + "]")
        else:
            out.append(f"{name}={val(v)}")
    return out


def _attr_suffix(n: Block, done: set[str]) -> str:
    rest = [k for k in n if k not in done and k != "type"]
    return "{" + ", ".join(_opts(n, rest)) + "}" if rest else ""


def expr(n: Block) -> str:
    """A value block as an expression."""
    t, own = n.get("type"), set(n) - {"type"}
    if t == "static_value" and own == {"value"} and NUMBER.fullmatch(str(n["value"])):
        return str(n["value"])
    if t == "static_string_value" and own == {"value"}:
        return json.dumps(str(n["value"]), ensure_ascii=False)
    if t == "static_date" and own == {"value"} and BARE.fullmatch(str(n["value"])):
        return f"date({n['value']})"
    if t == "activity_attribute" and "ATTRIBUTE" in own and own <= {"ATTRIBUTE", "datetime_operator", "date_operator"}:
        return "activity." + _ref(n["ATTRIBUTE"]) + _attr_suffix(n, {"ATTRIBUTE"})
    if (
        t == "member_attribute"
        and {"ATTRIBUTE_TYPE", "ATTRIBUTE"} <= own
        and own <= {"ATTRIBUTE_TYPE", "ATTRIBUTE", "date_operator"}
        and BARE.fullmatch(str(n["ATTRIBUTE_TYPE"]))
        and "." not in str(n["ATTRIBUTE_TYPE"])
    ):
        return (
            f"member.{n['ATTRIBUTE_TYPE']}." + _ref(n["ATTRIBUTE"]) + _attr_suffix(n, {"ATTRIBUTE_TYPE", "ATTRIBUTE"})
        )
    if _is_arith(n) and isinstance(n["LHS"], dict):
        if t == "arithmetic_generic" and isinstance(n["RHS"], dict) and n["operator"] in ARITHMETIC:
            return f"({expr(n['LHS'])} {n['operator']} {expr(n['RHS'])})"
        if (
            t == "arithmetic_date"
            and n["operator"] in ("+", "-")
            and NUMBER.fullmatch(str(n["number"]))
            and BARE.fullmatch(str(n["date_quantity"]))
        ):
            return f"({expr(n['LHS'])} {n['operator']} {n['number']} {n['date_quantity']})"
    return f"{t}(" + ", ".join(_opts(n, [k for k in n if k != "type"])) + ")"


def _ref(attr: Any) -> str:
    """An attribute after `activity.` / `member.<level>.`: bare, or a JSON string."""
    s = str(attr)
    return s if BARE.fullmatch(s) and "." not in s and "{" not in s else json.dumps(s, ensure_ascii=False)


def _subject(n: Block) -> str | None:
    """`<object> [[level]] <attribute>` of a condition, or None when the block is written whole."""
    t = n["type"]
    if t == "if_payment_attribute":
        if "payment_attribute" not in n or "attribute" in n:
            return None
        return f"{PAYMENT} {val(n['payment_attribute'])}"
    obj = n.get("OBJECT")
    if obj is not None and obj not in OBJECTS:
        return None
    word = obj if obj is not None else MEMBER
    level = f" [{val(n['attribute_level'])}]" if "attribute_level" in n else ""
    return f"{word}{level} {val(n['attribute'])}"


# A member level that keeps its condition in fields of its own is written like any other condition,
# `member [<level>] <attribute> <operator> <value>` with `pick=` (the card, plan or programme) and `scope=` (which cards
# or accounts); the code puts each part in the field the rules screen keeps it in (vocabulary.LEVEL_FIELDS).
TIER_LEVEL = "tier_level"  # a tier programme's condition: tiers (the programme), opperater, opperatervalue
TIER_FIELDS = {"attribute": "tiers", "operators": "opperater", "value": "opperatervalue"}
ACTIVE_CARDS_ATTRIBUTE = "active_cards"  # the member's active cards, compared by card id


def _level_map(n: Block) -> dict[str, str] | None:
    """{uniform key: the block's own field} for a condition at a level with fields of its own, else None."""
    level = n.get("attribute_level")
    if level == TIER_LEVEL:
        return dict(TIER_FIELDS)
    spec = LEVEL_FIELDS["rules"].get(str(level))
    if spec is None:
        return None
    out = {k: spec[k] for k in ("pick", "scope") if k in spec}
    if spec["attribute"] != "attribute":
        out["attribute"] = spec["attribute"]
    return out


def _active(n: Block, own: dict[str, str]) -> bool:
    return "pick" in own and n.get(own["pick"]) == ACTIVE_CARDS


def uniform(n: Block) -> Block | None:
    """The condition with its level's own fields under the uniform keys.

    The block itself when its level has none; None when the uniform form would be ambiguous (it is written whole).
    """
    own = _level_map(n)
    if own is None:
        return n
    if _active(n, own):
        own = {
            **own,
            "operators": str(ACTIVE_CARD_FIELDS["rules"]["operator"]),
            "value": str(ACTIVE_CARD_FIELDS["rules"]["value"]),
        }
        if "attribute" in n:
            return None
    clash = [k for k, f in own.items() if k != f and k in n]
    if clash or not all(f in n for k, f in own.items() if k in ("attribute", "operators") and k != f):
        return None
    m = {k: v for k, v in n.items() if k not in own.values() or own.get(k) == k}
    for k, f in own.items():
        if f in n:
            m[k] = n[f]
    if _active(n, _level_map(n) or {}):
        m["attribute"] = ACTIVE_CARDS_ATTRIBUTE
    return m


def from_uniform(m: Block) -> Block:
    """The condition `uniform` gave, with each part back in its level's own field."""
    own = _level_map(m)
    if own is None:
        return m
    active = m.get("attribute") == ACTIVE_CARDS_ATTRIBUTE and m.get("pick") == ACTIVE_CARDS
    if active:
        own = {
            **own,
            "operators": str(ACTIVE_CARD_FIELDS["rules"]["operator"]),
            "value": str(ACTIVE_CARD_FIELDS["rules"]["value"]),
        }
    n = {k: v for k, v in m.items() if k not in own or own[k] == k}
    if active:
        n.pop("attribute", None)
    for k, f in own.items():
        if k in m and k != f and not (active and k == "attribute"):
            n[f] = m[k]
    return n


def condition(n: Block, filter_line: bool) -> str:
    """A condition's line body."""
    whole_ = ("block " if filter_line else "if block ") + expr(n)
    if n.get("type") in ("activity_criteria", "if_generic_adaptive"):
        u = uniform(n)
        if u is None or from_uniform(u) != n:
            return whole_
        n = u
    t = n.get("type")
    ok_type = t == ("activity_criteria" if filter_line else "if_generic_adaptive") or t == "if_payment_attribute"
    subj_key = "payment_attribute" if t == "if_payment_attribute" else "attribute"
    whole = whole_
    if not ok_type or subj_key not in n or n.get("operators") not in OPERATORS or sum(k in n for k in RIGHT_KEYS) > 1:
        return whole
    subject = _subject(n)
    if subject is None:
        return whole
    parts = [] if filter_line else ["if"]
    parts += [subject, str(n["operators"])]
    done = {"type", "OBJECT", "attribute_level", "attribute", "payment_attribute", "operators"}
    if "picked" in n:
        parts.append("[" + ", ".join(val(c) for c in str(n["picked"]).split(",")) + "]")
        done.add("picked")
    elif "value" in n:
        parts.append(val(n["value"]))
        done.add("value")
    elif isinstance(n.get("RHS_INPUT"), dict):
        parts.append(blk(n["RHS_INPUT"]))
        done.add("RHS_INPUT")
        if n["operators"] == "between" and isinstance(n.get("RHS_VALUE"), dict):
            parts += ["and", blk(n["RHS_VALUE"])]
            done.add("RHS_VALUE")
    parts += _opts(n, [k for k in n if k not in done])
    return " ".join(parts)


def action(n: Block) -> str:
    """An action's line body."""
    t, kind = n.get("type"), n.get("action_type")
    if t not in ACTION_TYPES or not kind or not BARE.fullmatch(str(kind)) or str(kind) in RESERVED:
        return "block " + expr(n)
    if t == "action_third_party" and "third_parties_dropdown" not in n:
        return "block " + expr(n)
    parts = [f"for {val(n['third_parties_dropdown'])}"] if t == "action_third_party" else []
    parts.append(str(kind))
    done = {"type", "action_type", "third_parties_dropdown"}
    if kind == "update_attribute" and "mta_attribute" in n and n.get("action_operator"):
        parts += [val(n["mta_attribute"]), val(n["action_operator"])]
        done |= {"mta_attribute", "action_operator"}
        last = "RHS_INPUT_VALUE"
    else:
        last = "FORMULA"
    tail = n.get(last) if isinstance(n.get(last), dict) else None
    if tail is not None:
        done.add(last)
    parts += _opts(n, [k for k in n if k not in done], alias=True)
    if tail is not None:
        parts.append(expr(tail))
    return " ".join(parts)


@dataclass
class _Printer:
    lines: list[str] = field(default_factory=list)
    rules: int = 0
    ors: int = 0

    def conditions(self, items: list[Block], label: str, filter_line: bool, path: str = "") -> None:
        for c in items:
            if c.get("type") == "or" and set(c) - {"type"} <= {"OPERAND1", "OPERAND2"}:
                self.ors += 1
                k = self.ors
                for b in (1, 2):
                    sub = c.get(f"OPERAND{b}")
                    here = f"{path} or{k}.{b}"
                    if isinstance(sub, list) and sub:
                        self.conditions(sub, label, filter_line, here)
                    else:
                        self.lines.append(f"{label}{here},")  # an empty branch
                continue
            self.lines.append(f"{label}{path}, " + condition(c, filter_line))

    def rule(self, r: Block, parent: str | None) -> None:
        self.rules += 1
        label = f"{r['type']}_{self.rules}"
        head = label + (f" {val(r['IDENTIFIER'])}" if "IDENTIFIER" in r else "")
        head += f" inside {parent}" if parent else ""
        start = len(self.lines)
        self.conditions(r.get("CONDITION") or [], label, False)
        for a in r.get("ACTION") or []:
            if a.get("type") in RULE_TYPES and _rule_fits(a):
                if len(self.lines) == start:
                    self.lines.append(label + ",")
                self.rule(a, label)
            else:
                self.lines.append(f"{label}, " + action(a))
        if len(self.lines) == start:
            self.lines.append(label + ",")
        self.lines[start] = head + self.lines[start][len(label) :]  # the rule's first line carries name and parent

    def activity(self, a: Block, header: str | None) -> None:
        if header:
            self.lines.append(
                f"{header}," + "".join(" " + o for o in _opts(a, _extra(a, ("ACTIVITY_CRITERIA", "RULES"))))
            )
        self.conditions(a.get("ACTIVITY_CRITERIA") or [], "Filter", True)
        for r in a.get("RULES") or []:
            if r.get("type") in RULE_TYPES and _rule_fits(r):
                self.rule(r, None)
            else:
                self.lines.append("loose, block " + expr(r))  # pragma: no cover - no saved offer has one


def _extra(n: Block, own: tuple[str, ...]) -> list[str]:
    return [k for k in n if k not in own and k != "type"]


def _rule_fits(r: Block) -> bool:
    return not _extra(r, ("IDENTIFIER", "CONDITION", "ACTION"))


def _without_ids(x: Any) -> Any:
    """The tree without block ids and empty statement lists (lines name blocks by label, not id)."""
    if isinstance(x, list):
        return [_without_ids(i) for i in x]
    if isinstance(x, dict):
        return {k: _without_ids(v) for k, v in x.items() if k != "id" and v != []}
    return x


def to_lines(tops: list[Block], counters: list[dict[str, Any]] | None = None) -> str:
    """The rules canvas (compact blocks; ids and empty lists are dropped) and its counters as DSL lines.

    Raises:
        ValueError: The canvas is not one offer_generic root, or holds a section the lines cannot say.
    """
    tops = _without_ids(tops)
    if len(tops) != 1 or tops[0].get("type") != "offer_generic" or _extra(tops[0], ("activities", "events")):
        raise ValueError("the DSL writes one offer_generic root")
    p = _Printer()
    for c in counters or []:
        p.lines.append("counter, " + val(c.get("name")) + "".join(" " + o for o in _opts(c, _extra(c, ("name",)))))
    acts, events = tops[0].get("activities") or [], tops[0].get("events") or []
    headed = len(acts) != 1 or bool(events) or any(_extra(a, ("ACTIVITY_CRITERIA", "RULES")) for a in acts)
    for i, a in enumerate(acts, 1):
        if a.get("type") != "activity":
            raise ValueError("an activity slot holds another block")
        before = len(p.lines)
        p.activity(a, f"activity_{i}" if headed else None)
        if len(p.lines) == before:
            p.lines.append(f"activity_{i},")
    for i, e in enumerate(events, 1):
        if e.get("type") != "event" or _extra(e, ("RULES",)):
            p.lines.append(f"event_{i}, " + " ".join(_opts(e, _extra(e, ("RULES",)))))
        else:
            p.lines.append(f"event_{i},")
        for r in e.get("RULES") or []:
            p.rule(r, None)
    return "\n".join(p.lines)


# ---- reading ---------------------------------------------------------------------------------------------------------
@dataclass
class _Tok:
    kind: str  # str | punct | word
    text: str


def tokens(s: str) -> list[_Tok]:
    """A line's tokens: JSON strings, punctuation ( ) [ ] { } , and words."""
    out, i = [], 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = TOKEN.match(s, i)
        if not m or m.end() == i:
            raise ValueError(f"cannot read at {s[i:]!r}")
        if m.group(1) is not None:
            out.append(_Tok("str", json.loads(m.group(1))))
        elif m.group(2) is not None:
            out.append(_Tok("punct", m.group(2)))
        else:
            out.append(_Tok("word", m.group(3)))
        i = m.end()
    return out


class _Reader:
    """Reads one line's tokens."""

    def __init__(self, toks: list[_Tok], defs: dict[str, Block] | None = None) -> None:
        self.t, self.i, self.defs = toks, 0, defs if defs is not None else {}

    def peek(self, k: int = 0) -> _Tok | None:
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise ValueError("the line ends early")
        self.i += 1
        return tok

    def at(self, kind: str, text: str | None = None) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == kind and (text is None or tok.text == text)

    def expect(self, kind: str, text: str) -> None:
        tok = self.take()
        if tok.kind != kind or tok.text != text:
            raise ValueError(f"expected {text!r}, found {tok.text!r}")

    def done(self) -> bool:
        return self.i >= len(self.t)

    def scalar(self) -> str:
        tok = self.take()
        if tok.kind == "punct":
            raise ValueError(f"expected a value, found {tok.text!r}")
        return tok.text

    def option(self) -> tuple[str, Any] | None:
        """`key=value`, `key=(block)` or `key=[block, ...]` at the reader, or None."""
        tok = self.peek()
        if tok is None or tok.kind != "word":
            return None
        m = OPTION.fullmatch(tok.text)
        if not m:
            return None
        self.i += 1
        key, rest = m.group(1), m.group(2)
        if rest:
            return key, rest
        if self.at("punct", "("):
            return key, self.group()
        if self.at("punct", "["):
            return key, self.block_list()
        return key, self.scalar()

    def block_list(self) -> list[Block]:
        self.expect("punct", "[")
        out = []
        while not self.at("punct", "]"):
            out.append(self.expression())
            if self.at("punct", ","):
                self.i += 1
        self.expect("punct", "]")
        return out

    def group(self) -> Block:
        """`(x)`, `(a op b)` or `(date op n UNIT)`."""
        self.expect("punct", "(")
        lhs = self.expression()
        if self.at("punct", ")"):
            self.i += 1
            return lhs
        op = self.scalar()
        a, b, c = self.peek(), self.peek(1), self.peek(2)
        if (
            op in ("+", "-")
            and a is not None
            and a.kind == "word"
            and NUMBER.fullmatch(a.text)
            and b is not None
            and b.kind == "word"
            and c is not None
            and c.kind == "punct"
            and c.text == ")"
        ):
            self.i += 3
            return {"type": "arithmetic_date", "operator": op, "number": a.text, "date_quantity": b.text, "LHS": lhs}
        rhs = self.expression()
        self.expect("punct", ")")
        return {"type": "arithmetic_generic", "operator": op, "LHS": lhs, "RHS": rhs}

    def expression(self) -> Block:
        tok = self.peek()
        if tok is None:
            raise ValueError("expected a value block, the line ends")
        if tok.kind == "punct" and tok.text == "(":
            return self.group()
        if tok.kind == "str":
            self.i += 1
            return {"type": "static_string_value", "value": tok.text}
        if tok.kind != "word":
            raise ValueError(f"expected a value block, found {tok.text!r}")
        self.i += 1
        return self._word(tok.text)

    def _word(self, w: str) -> Block:
        """A value block that starts with a word."""
        if NUMBER.fullmatch(w):
            return {"type": "static_value", "value": w}
        if w.startswith("$"):
            if w[1:] not in self.defs:
                raise ValueError(
                    f"{w} is not defined on a def line before it (defined: {', '.join(self.defs) or 'none'})"
                )
            return copy.deepcopy(self.defs[w[1:]])
        if w == "date" and self.at("punct", "("):
            self.i += 1
            v = self.scalar()
            self.expect("punct", ")")
            return {"type": "static_date", "value": v}
        if w.startswith("activity."):
            n: Block = {"type": "activity_attribute", "ATTRIBUTE": self._ref(w[len("activity.") :])}
            return self._suffix(n)
        if w.startswith("member."):
            level, _, attr = w[len("member.") :].partition(".")
            n = {"type": "member_attribute", "ATTRIBUTE_TYPE": level, "ATTRIBUTE": self._ref(attr)}
            return self._suffix(n)
        if self.at("punct", "("):
            self.i += 1
            n = {"type": w}
            while not self.at("punct", ")"):
                kv = self.option()
                if kv is None:
                    raise ValueError(f"expected key=value in {w}(...)")
                n[kv[0]] = kv[1]
                if self.at("punct", ","):
                    self.i += 1
            self.i += 1
            return n
        raise ValueError(f"expected a value block, found {w!r}")

    def _ref(self, rest: str) -> str:
        if rest:
            return rest
        tok = self.take()
        if tok.kind != "str":
            raise ValueError("expected an attribute name")
        return tok.text

    def _suffix(self, n: Block) -> Block:
        if self.at("punct", "{"):
            self.i += 1
            while not self.at("punct", "}"):
                kv = self.option()
                if kv is None:
                    raise ValueError("expected key=value in {...}")
                n[kv[0]] = kv[1]
                if self.at("punct", ","):
                    self.i += 1
            self.i += 1
        return n

    def right(self) -> tuple[str, Any] | None:
        """A condition's right side: a value, a code list or a block."""
        tok = self.peek()
        if tok is None or (tok.kind == "word" and OPTION.fullmatch(tok.text)):
            return None
        if tok.kind == "punct" and tok.text == "[":
            self.i += 1
            codes = []
            while not self.at("punct", "]"):
                codes.append(self.scalar())
                if self.at("punct", ","):
                    self.i += 1
            self.i += 1
            return "picked", ",".join(codes)
        if tok.kind == "punct" and tok.text == "(":
            return "RHS_INPUT", self.group()
        return "value", self.scalar()


def _condition(r: _Reader, filter_line: bool) -> Block:
    """A condition after its `if` (a Filter line has none)."""
    word = r.scalar()
    if word == PAYMENT:
        n: Block = {"type": "if_payment_attribute", "payment_attribute": r.scalar()}
    else:
        n = {"type": "activity_criteria" if filter_line else "if_generic_adaptive"}
        if word != MEMBER:
            if word not in OBJECTS:
                raise ValueError(f"expected an object ({', '.join((*OBJECTS, MEMBER, PAYMENT))}), found {word!r}")
            n["OBJECT"] = word
        if r.at("punct", "["):
            r.i += 1
            n["attribute_level"] = r.scalar()
            r.expect("punct", "]")
        n["attribute"] = r.scalar()
    op = r.scalar()
    if op not in OPERATORS:
        raise ValueError(f"expected an operator ({' '.join(OPERATORS)}), found {op!r}")
    n["operators"] = op
    side = r.right()
    if side:
        n[side[0]] = side[1]
        if side[0] == "RHS_INPUT" and r.at("word", "and"):
            r.i += 1
            n["RHS_VALUE"] = r.group()
    _rest(r, n, alias=False)
    return from_uniform(n) if n["type"] != "if_payment_attribute" else n


def _rest(r: _Reader, n: Block, alias: bool, last: str | None = None) -> None:
    while not r.done():
        kv = r.option()
        if kv is None:
            if last is None:
                raise ValueError(f"expected key=value, found {r.take().text!r}")
            n[last] = r.expression()
            if not r.done():
                raise ValueError("a value block ends the line")
            return
        n[UNALIAS.get(kv[0], kv[0]) if alias else kv[0]] = kv[1]


def _action(r: _Reader) -> Block:
    n: Block = {"type": "action"}
    if r.at("word", "for"):
        r.i += 1
        n = {"type": "action_third_party", "third_parties_dropdown": r.scalar()}
    kind = r.scalar()
    n["action_type"] = kind
    last = "FORMULA"
    if kind == "update_attribute" and not r.done() and not (r.at("word") and OPTION.fullmatch(r.peek().text)):  # type: ignore[union-attr]
        tok = r.peek()
        if tok is not None and tok.kind in ("word", "str") and not NUMBER.fullmatch(tok.text):
            n["mta_attribute"] = r.scalar()
            n["action_operator"] = r.scalar()
            last = "RHS_INPUT_VALUE"
    _rest(r, n, alias=True, last=last)
    return n


@dataclass
class _Builder:
    root: Block = field(default_factory=lambda: {"type": "offer_generic"})
    counters: list[dict[str, Any]] = field(default_factory=list)
    section: Block | None = None
    rules: dict[str, Block] = field(default_factory=dict)
    ors: dict[str, Block] = field(default_factory=dict)
    defs: dict[str, Block] = field(default_factory=dict)  # def lines: a value block by name, used as $name
    origin: dict[int, int] = field(default_factory=dict)  # id(block) -> line number

    def current(self, n: int) -> Block:
        if self.section is None:
            self.section = self.new_section("activities", "activity", n)
        return self.section

    def new_section(self, key: str, t: str, n: int) -> Block:
        b: Block = {"type": t}
        self.root.setdefault(key, []).append(b)
        self.origin[id(b)] = n
        return b

    def place(self, owner: Block, key: str, b: Block, n: int) -> None:
        owner.setdefault(key, []).append(b)
        self.origin.setdefault(id(b), n)

    def branch(self, base: Block, key: str, path: list[tuple[str, str]], n: int) -> tuple[Block, str]:
        """The block and list a line's OR path leads to, OR groups made where first named."""
        owner, k = base, key
        for g, b in path:
            group = self.ors.get(g)
            if group is None:
                group = {"type": "or"}
                self.ors[g] = group
                self.place(owner, k, group, n)
            owner, k = group, f"OPERAND{b}"
        return owner, k


def from_lines(text: str) -> tuple[list[Block], list[dict[str, Any]], dict[str, int]]:
    """DSL lines as the rules canvas (compact blocks) and its counters.

    Returns:
        (tops, counters, origin): origin maps each block's path (`activities[0].RULES[1].CONDITION[0]`) to the line
        it came from, so a problem with a block can name its line.

    Raises:
        DslError: A line does not parse.
    """
    b = _Builder()
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            _line(b, line, n)
        except DslError:
            raise
        except ValueError as e:
            raise DslError(n, line, str(e)) from None
    origin: dict[str, int] = {}
    _paths(b.root, "", b.origin, origin)
    return [b.root], b.counters, origin


def _paths(node: Any, path: str, by_id: dict[int, int], out: dict[str, int]) -> None:
    if isinstance(node, dict):
        if id(node) in by_id:
            out[path or "root"] = by_id[id(node)]
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                _paths(v, f"{path}.{k}" if path else k, by_id, out)
    elif isinstance(node, list):
        for i, x in enumerate(node):
            _paths(x, f"{path}[{i}]", by_id, out)


LABELS = "Filter, rule_N, rule_line_N, payment_rule_N, counter, def, activity_N, event_N"


def _line(b: _Builder, line: str, n: int) -> None:
    """Builds what one line says into the canvas."""
    toks = tokens(line)
    comma = next((i for i, t in enumerate(toks) if t.kind == "punct" and t.text == ","), None)
    if comma is None:
        raise ValueError("expected '<label>, <body>'")
    head, r = _Reader(toks[:comma]), _Reader(toks[comma + 1 :], b.defs)
    label = head.scalar()
    if label in ("def", "counter") or SECTION.fullmatch(label):
        _declaration(b, label, r, n)
        return
    if label == "Filter":
        owner, key = b.current(n), "ACTIVITY_CRITERIA"
    elif LABEL.fullmatch(label):
        owner, key = _rule(b, label, head, n), "CONDITION"
    else:
        raise ValueError(f"expected a label ({LABELS}), found {label!r}")
    path = _or_path(head)
    if r.done():  # a label alone: the rule exists; an OR branch named this way is empty
        if path:
            o, k = b.branch(owner, key, path, n)
            o.setdefault(k, [])
        return
    filter_line = label == "Filter"
    is_condition = filter_line or r.at("word", "if")
    if is_condition and not filter_line:
        r.i += 1
    whole = _whole(r)
    if is_condition:
        o, k = b.branch(owner, key, path, n)
        b.place(o, k, whole if whole is not None else _condition(r, filter_line), n)
        return
    if path:
        raise ValueError("an action cannot sit in an OR branch")
    b.place(owner, "ACTION", whole if whole is not None else _action(r), n)


def _declaration(b: _Builder, label: str, r: _Reader, n: int) -> None:
    """A def, counter or section line."""
    if label == "def":
        name = r.scalar()
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError(f"a def's name is a word, found {name!r}")
        r.expect("punct", ",")
        b.defs[name] = r.expression()
        if not r.done():
            raise ValueError("a def holds one value block")
    elif label == "counter":
        c: dict[str, Any] = {"name": r.scalar()}
        _rest(r, c, alias=False)
        b.counters.append(c)
    else:
        kind = label.split("_")[0]
        sec = b.new_section("activities" if kind == "activity" else "events", kind, n)
        _rest(r, sec, alias=False)
        b.section = sec


def _rule(b: _Builder, label: str, head: _Reader, n: int) -> Block:
    """The rule a label names, made on its first line (with its name and the rule it sits in)."""
    name: str | None = None
    parent: str | None = None
    nxt = head.peek()
    if nxt is not None and (
        nxt.kind == "str" or (nxt.kind == "word" and nxt.text != "inside" and not OR_PATH.fullmatch(nxt.text))
    ):
        name = head.scalar()
    if head.at("word", "inside"):
        head.i += 1
        parent = head.scalar()
    rule = b.rules.get(label)
    if rule is not None:
        if name is not None or parent is not None:
            raise ValueError(f"{label}'s name and parent go on its first line only")
        return rule
    kind = LABEL.fullmatch(label)
    rule = {"type": kind.group(1) if kind else "rule"}
    if name is not None:
        rule["IDENTIFIER"] = name
    b.rules[label] = rule
    if parent is None:
        b.place(b.current(n), "RULES", rule, n)
    elif parent in b.rules:
        b.place(b.rules[parent], "ACTION", rule, n)
    else:
        raise ValueError(f"{label} is inside {parent}, which has no line before it")
    return rule


def _or_path(head: _Reader) -> list[tuple[str, str]]:
    """The OR branches after a label: [(group, branch)]."""
    path = []
    while not head.done():
        w = head.scalar()
        m = OR_PATH.fullmatch(w)
        if not m:
            raise ValueError(f"expected an OR branch (or<K>.<1|2>) after the label, found {w!r}")
        path.append((m.group(1), m.group(2)))
    return path


def _whole(r: _Reader) -> Block | None:
    """`block <expression>`: a block written whole, else None."""
    if not r.at("word", "block"):
        return None
    r.i += 1
    whole = r.expression()
    if not r.done():
        raise ValueError("a block ends the line")
    return whole
