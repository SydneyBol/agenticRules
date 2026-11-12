"""The sub-prompt DSL: an offer's rules canvas as numbered lines of plain words, and back.

The user's line form (ruleBook.txt, 2025): one statement per line, `<label>, <body>`; Filters before rules; the lines
of one label are ANDed; the lines read top to bottom as the offer's logic. The lines say WHAT is compared and given.
Everything a block needs beyond that comes from code and the catalog, as it does for the JSON tree: which fields a
card, plan or tier condition is kept in (vocabulary.LEVEL_FIELDS), whether a compared number is a value block or a
field (the attribute's type), codes for names (the attribute's value list, the card and plan lists), and what the
compose step fills (OBJECT, levels, the program's account and tag, data types, mutations). No level code (MA_P, MA_O,
MA_CC, ...) or block field name is needed in a line.

    counter, "Bonus Points Earned" number
    counter, "Visits per store" number per h_location of Activity
    counter, "Basket total" number transaction_only
    def, month_key, ((activity.h_bit_date.YEAR * 12) + activity.h_bit_date.MONTH)
    Filter, activity h_bit_type == SPEND
    Filter, activity h_location in [HYD, KOL]
    rule_1 "Gold band", if profile tier_class == Gold
    rule_1, if activity h_bit_amount between 50 and 99.99
    rule_1 or1.1, if activity h_bit_date.DAY_OF_WEEK >= 6
    rule_1 or1.2, if sponsor business_unit == FNB
    rule_1, give_points (activity.h_bit_amount * 2)
    rule_2 "Cap" inside rule_1, if counter "Bonus Points Earned" <= (2000 - counter."Bonus Points Earned")
    rule_2, update_attribute "Bonus Points Earned" INCREMENT_BY (activity.h_bit_amount * 2)
    rule_3, if card "Supra Co-Branded Card" transaction card_lookup == Platinum
    rule_3, if card active == "HDFC Sliver"
    rule_3, if subscription "Gold Plan" plan_status == ACTIVE
    rule_3, if tier Gold_Programme == Gold
    rule_3, for FAMILY_HEAD give_points 50
    payment_rule_4 "HDFC", if payment bank_code in [HDFC]
    activity_2,                      (a second activity: the lines after it belong to it)
    event_1,                         (an On Event section)

Labels: `Filter` (a criterion of the current activity), `rule_N` / `rule_line_N` / `payment_rule_N` (numbered once
across the offer; a rule's first line may carry its name and `inside <label>`), `orK.B` after the label (the line is
in branch B of OR group K; groups nest left to right), `counter`, `def`, `activity_K`, `event_K`.

Conditions, `[if] <subject> <attribute>[.PART] <operator> [<right>] [and <right>]`:
- subjects: `activity`, `sponsor`, `location`, `airport [origin | destination]`, `product`, `payment` (a payment
  item, in a Payment Rule), `third_party <who> [profile]`; the member's own: `profile` (the attribute library),
  `counter "<name>" [at <key> [as <key name>]]`, `transaction_value "<name>"`, `card <card> [any | transaction]`,
  `card active` (the cards the member holds), `bank_account [any | transaction]`, `subscription <plan>`,
  `partner <programme>`, `analytics`, `tier <programme>`.
- PART is a part of a date: DATE, MONTH, YEAR, DAY_OF_WEEK, DAY_OF_MONTH, DAY_OF_YEAR, WEEK_OF_YEAR, HOUR_OF_DAY ...
- right: a code or name (`SPEND`, `"two words"`), a list (`[A, B]`), a number, a date (`2026-03-08`) or any value in
  parentheses (`(activity.h_bit_amount * 2)`).

Actions, `[for <third party>] <action> ...`: `give_points [account=.. tag=..] <value>`,
`update_attribute "<counter>" [at <key>] <INCREMENT_BY | SET | RESET ...> [<value>]`,
`update_attribute [profile] <name> = <value>`, `give_privilege privilege=.. policy=.. validity=.. quantity=..`,
any other action with `key=value`.

Values: numbers, `"text"`, dates, `activity.<attr>[.PART]`, `profile.<attr>[.PART]`, `counter."<name>"`,
`transaction_value."<name>"`, `product.<attr>`, `payment.<attr>`, `airport_pair.<attr>`, `today[.PART]`,
`rate(<conversion>[, <account>])`, `setting(<name>)`, `(a op b)`, `(date + 1 DAYS)`, `$def`.

Anything the forms above cannot say exactly is written as the block itself: `block type(key=value, key=(block))`.
Every line `to_lines` writes is compiled back and compared with its block before it is kept, so the lines never
lose anything the tree holds.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..vocabulary import ACTIVE_CARD_FIELDS, ACTIVE_CARDS, LEVEL_FIELD_ORDER, LEVEL_FIELDS, LEVEL_LISTS

Block = dict[str, Any]

FORM = "3"  # the line form's version: stored lines of another form are written again (harvest, migration)
RULE_TYPES = ("rule", "rule_line", "payment_rule")
PAYMENT = "payment"
MEMBER = "member"  # a member attribute whose level the compose step fills, or `member [<level code>]` for any level
OBJECT_WORDS = ("activity", "sponsor", "location", "airport", "products_attrs", "third_party")
PRODUCT = "product"  # OBJECT products_attrs reading the product itself (ProductOrOfferContext products_attrs)
PRODUCTS = "products_attrs"
OBJECT_LISTS = {"products_attrs": "product"}  # an OBJECT's catalog list when it is not the OBJECT's own name
LEVEL_WORDS = {
    "MA_P": "profile",
    "MA_O": "counter",
    "MA_A": "transaction_value",
    "MA_TO": "tier_counter",
    "MA_CC": "card",
    "MA_CA": "bank_account",
    "MA_SP": "subscription",
    "MA_PP": "partner",
    "MA_S": "analytics",
    "MP_S": "personalization",
    "MR_S": "retention",
    "tier_level": "tier",
}
WORD_LEVELS = {v: k for k, v in LEVEL_WORDS.items()}
COUNTER_LEVELS = ("MA_O", "MA_A", "MA_TO")
PICKERS = {"MA_CC": "card_list", "MA_SP": "subscription_plan", "MA_PP": "partner_program"}
SCOPES = {
    "ALL_CARDS": "any",
    "TRANSACTION_CARD": "transaction",
    "ALL_ACCOUNTS": "any",
    "TRANSACTION_ACCOUNT": "transaction",
}
SCOPE_CODES = {
    "MA_CC": {"any": "ALL_CARDS", "transaction": "TRANSACTION_CARD"},
    "MA_CA": {"any": "ALL_ACCOUNTS", "transaction": "TRANSACTION_ACCOUNT"},
}
ACTIVE = "active"
TIER_LEVEL = "tier_level"  # a tier programme's condition: tiers (the programme), opperater, opperatervalue
TIER_FIELDS = {"attribute": "tiers", "operators": "opperater", "value": "opperatervalue"}
TIER_DEFAULT = ("tierdefault", "name")
ACTIVE_CARDS_ATTRIBUTE = "active_cards"  # the member's active cards, compared by card id

OPERATORS = ("==", "!=", ">=", "<=", ">", "<", "in", "not_in", "between", "null", "not_null", "match", "not_match")
ARITHMETIC = ("*", "/", "+", "-", "%")
PARTS = (
    "DATE",
    "DATETIME",
    "MONTH",
    "YEAR",
    "DAY_OF_WEEK",
    "DAY_OF_MONTH",
    "DAY_OF_YEAR",
    "WEEK_OF_YEAR",
    "HOUR_OF_DAY",
    "MINUTE_OF_HOUR",
)
FIELD_VALUED = ("LOOKUP", "LOV")  # attribute types whose compared value is a code in a field, never a value block
NUMBER = re.compile(r"-?\d+(\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?")
BARE = re.compile(r"[^\s()\[\]{},\"=]+")
OPTION = re.compile(r"([A-Za-z_]\w*)=(.*)", re.S)
LABEL = re.compile(r"(rule_line|payment_rule|rule)_(\d+)")
SECTION = re.compile(r"(activity|event)_(\d+)")
OR_PATH = re.compile(r"or(\d+)\.([12])")
TOKEN = re.compile(r'("(?:[^"\\]|\\.)*")|([()\[\]{},])|([^\s()\[\]{},"]+)')
KEYWORDS = (
    "if",
    "and",
    "for",
    "block",
    "inside",
    "at",
    "as",
    "per",
    "of",
    "any",
    "transaction",
    ACTIVE,
    "transaction_only",
)
RESERVED = frozenset((*KEYWORDS, *OPERATORS, *ARITHMETIC, "=", MEMBER, PAYMENT, PRODUCT, *OBJECT_WORDS, *WORD_LEVELS))

# value blocks read as `<word>.<attribute>`: (block type, its fixed fields, the field that names the attribute)
VALUE_WORDS: dict[str, tuple[str, dict[str, str], str]] = {
    "activity": ("activity_attribute", {}, "ATTRIBUTE"),
    PRODUCT: ("product_attribute", {"ATTRIBUTE_PRODUCTS": "products_attrs"}, "ATTRIBUTE"),
    "product_offer": ("product_attribute", {"ATTRIBUTE_PRODUCTS": "product_offer_attributes"}, "ATTRIBUTE"),
    "airport_pair": ("airport_attribute", {}, "airportpair_attribute"),
    PAYMENT: ("payment_attribute", {}, "payment_attribute"),
    **{w: ("member_attribute", {"ATTRIBUTE_TYPE": code}, "ATTRIBUTE") for code, w in LEVEL_WORDS.items()},
}
TODAY = "today"  # current_date_block: the date (today) or one part of it (today.MONTH)
RATE, SETTING = "rate", "setting"  # currency_converter(conversion_type[, LOYALTY_ACCOUNT]); blueprint_variable

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
BLOCK_OPTIONS = ("PRIVILEGE_QUANTITY",)  # options that hold a value block: a bare number there is a number block
ACTION_TYPES = ("action", "action_third_party")
COUNTER_TYPES = {"number": "NUMBER", "text": "TEXT", "date": "DATE"}
COUNTER_DEFAULT_TARGET = {"MA_O": "NO TARGET"}  # what the screen saves for a counter with no target


class DslError(ValueError):
    """A line that does not parse: its number (1-based), the line and what was expected there."""

    def __init__(self, line: int, text: str, expected: str) -> None:
        super().__init__(f"line {line}: {expected}: {text}")
        self.line, self.text, self.expected = line, text, expected


@dataclass
class Context:
    """What the lines are read and written with.

    Attributes:
        catalog: The tenant catalog, or None: without it names stay ids and codes, and a compared number is a block.
        counters: The counters declared so far, by name.
        defs: The def lines so far, by name.
        brief: Writing only: leave out the account and tag the compose step fills (the program's own).
        ids: Reading only: give each block an id that names its line (L7, L7.2, rule_3).
    """

    catalog: Any = None
    counters: dict[str, dict[str, Any]] = field(default_factory=dict)
    defs: dict[str, Block] = field(default_factory=dict)
    brief: bool = False
    ids: bool = False

    def attr(self, lst: str | None, code: Any) -> Any:
        """The catalog's attribute, or None."""
        if self.catalog is None or not lst or not isinstance(code, str):
            return None
        return self.catalog.attr(lst, code)

    def field_valued(self, lst: str | None, code: Any) -> bool:
        """The attribute's compared value is a code in a field (a LOOKUP or LOV), whatever it looks like."""
        a = self.attr(lst, code)
        return a is not None and a.type in FIELD_VALUED

    def part_field(self, lst: str | None, code: Any, member: bool = False) -> str:
        """The field a date part of the attribute is kept in.

        The attribute's type decides (date_operator for a DATE, datetime_operator for a DATETIME); when the catalog
        does not say, a member's own value keeps it in date_operator and a transaction's in datetime_operator.
        """
        a = self.attr(lst, code)
        kind = a.type if a is not None and a.type in ("DATE", "DATETIME") else ("DATE" if member else "DATETIME")
        return "date_operator" if kind == "DATE" else "datetime_operator"

    def names(self, lst: str | None, code: Any = None) -> dict[str, str]:
        """{code: name} of an attribute's values (lst, code) or of a picker list (lst alone)."""
        if self.catalog is None or not lst:
            return {}
        if code is None:
            found = (getattr(self.catalog, "pickers", None) or {}).get(lst) or {}
        else:
            a = self.attr(lst, code)
            found = (a.values if a is not None else None) or {}
        return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}

    def show(self, code: Any, names: dict[str, str]) -> str:
        """The name of a code when exactly that code has it and it reads back to the code, else the code."""
        code = str(code)
        name = names.get(code)
        if name and self.read(name, names) == code:
            return name
        return code

    @staticmethod
    def read(text: str, names: dict[str, str]) -> str:
        """The code a code or a name stands for: itself when it is a code or names none or several."""
        if text in names:
            return text
        same = [c for c, n in names.items() if n == text] or [c for c, n in names.items() if n.lower() == text.lower()]
        return same[0] if len(same) == 1 else text


# ---- writing ---------------------------------------------------------------------------------------------------------
def _looks_like_value(s: str) -> bool:
    """A bare word that would be read as a number, a date, a def or a value block, not as a name."""
    if NUMBER.fullmatch(s) or DATE.fullmatch(s) or s.startswith("$") or s == TODAY:
        return True
    return s.split(".", 1)[0] in (*VALUE_WORDS, MEMBER, TODAY) and "." in s


def val(s: Any) -> str:
    """A name, code or option value: bare when it cannot be read as anything else, else a JSON string."""
    s = "" if s is None else str(s)
    return s if BARE.fullmatch(s) and s not in RESERVED and not s.endswith(".") else json.dumps(s, ensure_ascii=False)


def fval(s: Any) -> str:
    """A compared value kept in a field: as val, and quoted when it would read as a number, date or value block."""
    s = "" if s is None else str(s)
    return json.dumps(s, ensure_ascii=False) if _looks_like_value(s) else val(s)


def _ref(attr: Any) -> str:
    """An attribute after `<word>.`: bare, or a JSON string."""
    s = str(attr)
    return s if BARE.fullmatch(s) and "." not in s and "{" not in s else json.dumps(s, ensure_ascii=False)


def _vector_key(n: Block) -> list[str]:
    """`at <key>` of a vector counter, `as <name>` when the key is shown under another name."""
    name, key = n["vector_counter_name"], n["vector_counter_namespace"]
    return ["at", val(key)] + (["as", val(name)] if name != key else [])


def _extra(n: Block, own: tuple[str, ...]) -> list[str]:
    return [k for k in n if k not in own and k != "type"]


def _only(n: Block, need: set[str], may: tuple[str, ...] = ()) -> bool:
    """The block has every needed key and nothing outside the needed and allowed ones."""
    own = set(n) - {"type"}
    return need <= own and own <= need | set(may)


class _Writer:
    """Writes blocks as line parts; every special form is compiled back and kept only when it gives the block."""

    def __init__(self, cx: Context) -> None:
        self.cx = cx

    # -- values
    def generic(self, n: Block) -> str:
        """The block itself: `type(key=value, key=(block), key=[blocks])`."""
        return f"{n.get('type')}(" + ", ".join(self.options(n, [k for k in n if k != "type"])) + ")"

    def options(self, n: Block, keys: list[str], alias: bool = False) -> list[str]:
        """`key=value` for each key: a block in parentheses, a list in brackets."""
        out = []
        for k in keys:
            v, name = n[k], (ALIASES.get(k, k) if alias else k)
            if isinstance(v, dict):
                s = self.expr(v)
                bare = alias and k in BLOCK_OPTIONS and NUMBER.fullmatch(s)
                out.append(f"{name}={s if bare or s.startswith('(') else f'({s})'}")
            elif isinstance(v, list):
                out.append(f"{name}=[" + ", ".join(self.expr(x) for x in v) + "]")
            else:
                out.append(f"{name}={val(v)}")
        return out

    def expr(self, n: Block) -> str:
        """A value block as an expression."""
        s = self._special(n)
        if s is not None:
            try:
                r = _Reader(tokens(s), self.cx)
                if r.expression() == n and r.done():
                    return s
            except ValueError:
                pass
        return self.generic(n)

    def _special(self, n: Block) -> str | None:
        t = n.get("type")
        if t == "static_value" and _only(n, {"value"}) and NUMBER.fullmatch(str(n["value"])):
            return str(n["value"])
        if t == "static_string_value" and _only(n, {"value"}):
            return json.dumps(str(n["value"]), ensure_ascii=False)
        if t == "static_date" and _only(n, {"value"}) and DATE.fullmatch(str(n["value"])):
            return str(n["value"])
        if t == "current_date_block" and _only(n, {"current_date_type"}):
            part = n["current_date_type"]
            return TODAY if part == "DATE" else f"{TODAY}.{part}"
        if t == "currency_converter" and _only(n, {"conversion_type"}, ("LOYALTY_ACCOUNT",)):
            account = f", {val(n['LOYALTY_ACCOUNT'])}" if "LOYALTY_ACCOUNT" in n else ""
            return f"{RATE}({val(n['conversion_type'])}{account})"
        if t == "blueprint_variable" and _only(n, {"BLUEPRINT_ATTRIBUTE_TYPE"}):
            return f"{SETTING}({val(n['BLUEPRINT_ATTRIBUTE_TYPE'])})"
        blocks = isinstance(n.get("LHS"), dict) and isinstance(n.get("RHS", {}), dict)
        if (
            t == "arithmetic_generic"
            and _only(n, {"operator", "LHS", "RHS"})
            and n["operator"] in ARITHMETIC
            and blocks
        ):
            return f"({self.expr(n['LHS'])} {n['operator']} {self.expr(n['RHS'])})"
        dated = t == "arithmetic_date" and _only(n, {"operator", "number", "date_quantity", "LHS"}) and blocks
        if dated and n["operator"] in ("+", "-"):
            return f"({self.expr(n['LHS'])} {n['operator']} {n['number']} {n['date_quantity']})"
        for word, (kind, fixed, key) in VALUE_WORDS.items():
            if t == kind and key in n and all(n.get(k) == v for k, v in fixed.items()):
                return self._attribute(n, word, fixed, key)
        return None

    def _attribute(self, n: Block, word: str, fixed: dict[str, str], key: str) -> str:
        """`<word>.<attribute>[.PART]{other fields}`."""
        s, done = f"{word}.{_ref(n[key])}", {key, *fixed}
        lst = "activity" if word == "activity" else LEVEL_LISTS.get(fixed.get("ATTRIBUTE_TYPE", ""))
        part = "date_operator" if n.get("type") == "member_attribute" else self.cx.part_field(lst, n[key])
        if n.get(part) in PARTS:
            s, done = f"{s}.{n[part]}", done | {part}
        rest = [k for k in n if k not in done and k != "type"]
        return s + ("{" + ", ".join(self.options(n, rest)) + "}" if rest else "")

    def right(self, n: Block) -> str:
        """A value block as a condition's right side or an action's value: text goes in parentheses."""
        s = self.expr(n)
        return f"({s})" if s.startswith('"') else s

    # -- conditions
    def condition(self, n: Block, filter_line: bool) -> str:
        """A condition's line body."""
        s = self._condition(n, filter_line)
        if s is not None:
            try:
                r = _Reader(tokens(s), self.cx)
                if _condition(r, filter_line, self.cx) == n and r.done():
                    return s if filter_line else "if " + s
            except ValueError:
                pass
        return ("block " if filter_line else "if block ") + self.generic(n)

    def _condition(self, n: Block, filter_line: bool) -> str | None:
        t = n.get("type")
        if t == "if_payment_attribute":
            if "payment_attribute" not in n:
                return None
            u, subject, done = dict(n), f"{PAYMENT} {val(n['payment_attribute'])}", {"type", "payment_attribute"}
            lst: str | None = "payment_method"
            done.add("operators")
        elif t == ("activity_criteria" if filter_line else "if_generic_adaptive"):
            m = uniform(n)
            if m is None or "attribute" not in m:
                return None
            u = m
            found = self._subject(u)
            if found is None:
                return None
            subject, lst = found[0], found[1]
            done = found[2] | {"type", "operators"}
        else:
            return None
        if u.get("operators") not in OPERATORS:
            return None
        attr = u.get("attribute", u.get("payment_attribute"))
        part = self.cx.part_field(lst, attr, t != "if_payment_attribute" and "OBJECT" not in u)
        if u.get(part) in PARTS and attr != ACTIVE_CARDS_ATTRIBUTE and subject.endswith(" " + str(attr)):
            subject, done = f"{subject}.{u[part]}", done | {part}
        parts = [subject, str(u["operators"]), *self._right(u, lst, attr, done)]
        parts += self.options(u, [k for k in u if k not in done])
        return " ".join(parts)

    def _right(self, u: Block, lst: str | None, attr: Any, done: set[str]) -> list[str]:
        """The compared value: a list, a field value or a value block (with `and` for between)."""
        names = self.cx.names(PICKERS["MA_CC"]) if attr == ACTIVE_CARDS_ATTRIBUTE else self.cx.names(lst, attr)
        if "picked" in u:
            done.add("picked")
            return ["[" + ", ".join(val(self.cx.show(c, names)) for c in str(u["picked"]).split(",")) + "]"]
        if "value" in u:
            done.add("value")
            shown = self.cx.show(u["value"], names)
            plain = self.cx.field_valued(lst, attr) or attr == ACTIVE_CARDS_ATTRIBUTE
            return [val(shown) if plain and (NUMBER.fullmatch(shown) or DATE.fullmatch(shown)) else fval(shown)]
        if isinstance(u.get("RHS_INPUT"), dict):
            done.add("RHS_INPUT")
            out = [self.right(u["RHS_INPUT"])]
            if u["operators"] == "between" and isinstance(u.get("RHS_VALUE"), dict):
                done.add("RHS_VALUE")
                out += ["and", self.right(u["RHS_VALUE"])]
            return out
        return []

    def _subject(self, u: Block) -> tuple[str, str | None, set[str]] | None:  # noqa: C901 - one case per subject
        """(`<subject> <attribute>`, the attribute's catalog list, the keys it says) of a condition in uniform keys."""
        obj, level, attr = u.get("OBJECT"), u.get("attribute_level"), u["attribute"]
        done = {"OBJECT", "attribute_level", "attribute"}
        if obj is not None:
            if obj not in OBJECT_WORDS:
                return None
            word = obj
            if obj == PRODUCTS and u.get("ProductOrOfferContext") == PRODUCTS:
                word, done = PRODUCT, done | {"ProductOrOfferContext"}
            if obj == "third_party":
                if "third_parties_dropdown" not in u:
                    return None
                word, done = f"{word} {val(u['third_parties_dropdown'])}", done | {"third_parties_dropdown"}
            if obj == "airport" and "airporttypes" in u:  # which airport of the journey: origin, destination
                word, done = f"{word} {val(u['airporttypes'])}", done | {"airporttypes"}
            if level is not None:
                word += f" {LEVEL_WORDS[level]}" if level in LEVEL_WORDS else f" [{val(level)}]"
            lst = LEVEL_LISTS.get(str(level)) if level is not None else OBJECT_LISTS.get(obj, obj)
            return f"{word} {val(attr)}", lst, done
        if level is None:
            counter = self.cx.counters.get(str(attr))
            if counter is None:
                return f"{MEMBER} {val(attr)}", "member", done
            level = counter.get("level") or "MA_O"
            if level not in COUNTER_LEVELS:
                return None
        if level not in LEVEL_WORDS:
            return f"{MEMBER} [{val(level)}] {val(attr)}", LEVEL_LISTS.get(str(level)), done
        words = [LEVEL_WORDS[level]]
        if "pick" in u:
            names = self.cx.names(PICKERS.get(level))
            words.append(ACTIVE if u["pick"] == ACTIVE_CARDS else val(self.cx.show(u["pick"], names)))
            done.add("pick")
        if "scope" in u:
            if u["scope"] not in SCOPES:
                return None
            words.append(SCOPES[u["scope"]])
            done.add("scope")
        if attr != ACTIVE_CARDS_ATTRIBUTE:
            words.append(val(attr))
        if level in COUNTER_LEVELS and "vector_counter_name" in u and "vector_counter_namespace" in u:
            words += _vector_key(u)
            done |= {"vector_counter_name", "vector_counter_namespace"}
        if level == TIER_LEVEL and u.get(TIER_DEFAULT[0]) == TIER_DEFAULT[1]:
            done.add(TIER_DEFAULT[0])
        return " ".join(words), LEVEL_LISTS.get(level), done

    # -- actions
    def action(self, n: Block) -> str:
        """An action's line body."""
        dropped = self._filled(n)
        kept = {k: v for k, v in n.items() if k not in dropped}
        s = self._action(kept)
        if s is not None:
            try:
                r = _Reader(tokens(s), self.cx)
                if _action(r, self.cx) == kept and r.done():
                    return s
            except ValueError:
                pass
        return "block " + self.generic(n)

    def _filled(self, n: Block) -> tuple[str, ...]:
        """The account and tag of a give_points the compose step would fill the same (brief writing only)."""
        cat = self.cx.catalog
        if not self.cx.brief or cat is None or n.get("action_type") != "give_points":
            return ()
        account, tag = n.get("LOYALTY_ACCOUNT"), n.get("REWARD_TAGS")
        if isinstance(account, str) and account == str(cat.offer_account) and tag == cat.reward_tag(account):
            return ("LOYALTY_ACCOUNT", "REWARD_TAGS")
        return ()

    def _action(self, n: Block) -> str | None:
        t, kind = n.get("type"), n.get("action_type")
        if t not in ACTION_TYPES or not kind or not BARE.fullmatch(str(kind)) or str(kind) in RESERVED:
            return None
        if t == "action_third_party" and "third_parties_dropdown" not in n:
            return None
        parts = [f"for {val(n['third_parties_dropdown'])}"] if t == "action_third_party" else []
        parts.append(str(kind))
        done, last = {"type", "action_type", "third_parties_dropdown"}, "FORMULA"
        if kind == "update_attribute" and "mta_attribute" in n:
            done.add("mta_attribute")
            if n.get("mta_type_value") in LEVEL_WORDS:
                parts.append(LEVEL_WORDS[n["mta_type_value"]])
                done.add("mta_type_value")
            parts.append(val(n["mta_attribute"]))
            if "vector_counter_name" in n and "vector_counter_namespace" in n:
                parts += _vector_key(n)
                done |= {"vector_counter_name", "vector_counter_namespace"}
            if n.get("action_operator"):
                parts.append(val(n["action_operator"]))
                done.add("action_operator")
                last = "RHS_INPUT_VALUE"
            elif isinstance(n.get("RHS_INPUT"), dict):
                parts.append("=")
                last = "RHS_INPUT"
            elif "value" in n and not isinstance(n["value"], (dict, list)):
                parts += ["=", fval(n["value"])]
                done.add("value")
        tail = n.get(last) if isinstance(n.get(last), dict) else None
        if tail is not None:
            done.add(last)
        parts += self.options(n, [k for k in n if k not in done], alias=True)
        if tail is not None:
            parts.append(self.right(tail))
        return " ".join(parts)

    # -- counters
    def counter(self, c: dict[str, Any]) -> str:
        """A counter line's body."""
        s = self._counter(c)
        if s is not None:
            try:
                r = _Reader(tokens(s), self.cx)
                if _counter(r) == c and r.done():
                    return s
            except ValueError:
                pass
        return val(c.get("name")) + "".join(" " + o for o in self.options(c, _extra(c, ("name",))))

    @staticmethod
    def _counter(c: dict[str, Any]) -> str | None:
        kinds = {v: k for k, v in COUNTER_TYPES.items()}
        level, kind = c.get("level"), c.get("counter_type")
        if c.get("data_type") not in kinds or level not in ("MA_O", "MA_A") or kind not in ("REGULAR", "VECTOR"):
            return None
        parts = [val(c.get("name")), kinds[c["data_type"]]]
        if kind == "VECTOR":
            if "namespace" not in c:
                return None
            parts += ["per", val(c["namespace"])]
            if "entity_level" in c:
                parts += ["of", val(c["entity_level"])]
        if level == "MA_A":
            parts.append("transaction_only")
        if c.get("target_type") != COUNTER_DEFAULT_TARGET.get(level):
            parts.append(f"target={val(c['target_type']) if 'target_type' in c else 'unset'}")
        return " ".join(parts)


@dataclass
class _Printer:
    w: _Writer
    lines: list[str] = field(default_factory=list)
    rules: int = 0
    ors: int = 0
    ids: dict[int, str] = field(default_factory=dict)  # a printed block (by identity) -> its id on the canvas
    src: list[str | None] = field(default_factory=list)  # per line: the id of the block the line says

    def add(self, line: str, node: Block | None = None) -> None:
        self.lines.append(line)
        self.src.append(self.ids.get(id(node)) if node is not None else None)

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
                        self.add(f"{label}{here},", c)  # an empty branch
                continue
            self.add(f"{label}{path}, " + self.w.condition(c, filter_line), c)

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
                    self.add(label + ",", r)
                self.rule(a, label)
            else:
                self.add(f"{label}, " + self.w.action(a), a)
        if len(self.lines) == start:
            self.add(label + ",", r)
        self.lines[start] = head + self.lines[start][len(label) :]  # the rule's first line carries name and parent

    def activity(self, a: Block, header: str | None) -> None:
        if header:
            more = self.w.options(a, _extra(a, ("ACTIVITY_CRITERIA", "RULES")))
            self.add(f"{header}," + "".join(" " + o for o in more), a)
        self.conditions(a.get("ACTIVITY_CRITERIA") or [], "Filter", True)
        for r in a.get("RULES") or []:
            if r.get("type") not in RULE_TYPES or not _rule_fits(r):
                raise ValueError("an activity's RULES holds a block that is not a plain rule")
            self.rule(r, None)


def _rule_fits(r: Block) -> bool:
    return not _extra(r, ("IDENTIFIER", "CONDITION", "ACTION"))


def _without_ids(x: Any, ids: dict[int, str] | None = None) -> Any:
    """The tree without block ids and empty statement lists (lines name blocks by label, not id).

    `ids` collects each copied block's id, by the copy's identity, for the lines' sources (to_pairs).
    """
    if isinstance(x, list):
        return [_without_ids(i, ids) for i in x]
    if isinstance(x, dict):
        out = {k: _without_ids(v, ids) for k, v in x.items() if k != "id" and v != []}
        if ids is not None and x.get("id"):
            ids[id(out)] = str(x["id"])
        return out
    return x


def to_lines(
    tops: list[Block], counters: list[dict[str, Any]] | None = None, catalog: Any = None, brief: bool = False
) -> str:
    """The rules canvas (compact blocks; ids and empty lists are dropped) and its counters as DSL lines.

    Args:
        tops: The canvas's top-level blocks: one offer_generic root.
        counters: The offer counters the canvas declares.
        catalog: The tenant catalog: names for ids and codes, and types; read the lines back with the same catalog.
        brief: Leave out what the compose step fills the same (the program's own account and reward tag).

    Raises:
        ValueError: The canvas is not one offer_generic root, or holds a section the lines cannot say.
    """
    return "\n".join(_print(tops, counters, catalog, brief).lines)


def to_pairs(
    tops: list[Block], counters: list[dict[str, Any]] | None = None, catalog: Any = None, brief: bool = False
) -> list[tuple[str, str | None]]:
    """The lines of to_lines, each with the id of the block it says.

    A condition's or an action's line carries that block's id; a line that only opens a rule, an activity or an
    event carries that one's; a counter line carries None. What a comparison of two offers needs (the feedback on a
    draft): every line with its place in its label and its block.
    """
    p = _print(tops, counters, catalog, brief)
    return list(zip(p.lines, p.src, strict=True))


def split_label(line: str) -> tuple[str, str]:
    """A line's label (with its rule name, parent and OR branches) and its body."""
    i = 0
    while i < len(line):
        if line[i].isspace():
            i += 1
            continue
        m = TOKEN.match(line, i)
        if not m:
            break
        if m.group(2) == ",":
            return line[:i].strip(), line[m.end() :].strip()
        i = m.end()
    return line.strip(), ""


def _print(tops: list[Block], counters: list[dict[str, Any]] | None, catalog: Any, brief: bool) -> _Printer:
    ids: dict[int, str] = {}
    tops = _without_ids(tops, ids)
    if len(tops) != 1 or tops[0].get("type") != "offer_generic" or _extra(tops[0], ("activities", "events")):
        raise ValueError("the DSL writes one offer_generic root")
    cx = Context(catalog=catalog, brief=brief)
    p = _Printer(_Writer(cx), ids=ids)
    for c in counters or []:
        p.add("counter, " + p.w.counter(c))
        if c.get("name"):
            cx.counters[str(c["name"])] = c
    acts, events = tops[0].get("activities") or [], tops[0].get("events") or []
    headed = len(acts) > 1 or any(_extra(a, ("ACTIVITY_CRITERIA", "RULES")) for a in acts)
    for i, a in enumerate(acts, 1):
        if a.get("type") != "activity":
            raise ValueError("an activity slot holds another block")
        before = len(p.lines)
        p.activity(a, f"activity_{i}" if headed else None)
        if len(p.lines) == before:
            p.add(f"activity_{i},", a)
    for i, e in enumerate(events, 1):
        if e.get("type") != "event":
            raise ValueError("an event slot holds another block")
        p.add(f"event_{i}," + "".join(" " + o for o in p.w.options(e, _extra(e, ("RULES",)))), e)
        for r in e.get("RULES") or []:
            p.rule(r, None)
    return p


# ---- level layouts ---------------------------------------------------------------------------------------------------
# A member level that keeps its condition in fields of its own is read and written like any other condition, in the
# uniform keys attribute / operators / value with pick (the card, plan or programme) and scope (which cards or
# accounts); the code puts each part in the field the rules screen keeps it in (vocabulary.LEVEL_FIELDS).
def _level_map(n: Block) -> dict[str, str] | None:
    """{uniform key: the block's own field} for a condition at a level with fields of its own, else None."""
    level = n.get("attribute_level")
    if level == TIER_LEVEL:
        return dict(TIER_FIELDS)
    spec = LEVEL_FIELDS["rules"].get(str(level))
    if spec is None:
        return None
    out = {k: str(spec[k]) for k in ("pick", "scope") if k in spec}
    if spec["attribute"] != "attribute":
        out["attribute"] = str(spec["attribute"])
    return out


def _active_fields(own: dict[str, str]) -> dict[str, str]:
    fields = ACTIVE_CARD_FIELDS["rules"]
    return {**own, "operators": str(fields["operator"]), "value": str(fields["value"])}


def uniform(n: Block) -> Block | None:
    """The condition with its level's own fields under the uniform keys.

    The block itself when its level has none; None when the uniform form would be ambiguous (it is written whole).
    """
    own = _level_map(n)
    if own is None:
        return n
    active = "pick" in own and n.get(own["pick"]) == ACTIVE_CARDS
    if active:
        if "attribute" in n:
            return None
        own = _active_fields(own)
    if any(k != f and k in n for k, f in own.items()):
        return None
    m = {k: v for k, v in n.items() if k not in own.values() or own.get(k) == k}
    for k, f in own.items():
        if f in n:
            m[k] = n[f]
    if active:
        m["attribute"] = ACTIVE_CARDS_ATTRIBUTE
    return m if from_uniform(m) == n else None


def from_uniform(m: Block) -> Block:
    """The condition `uniform` gave, with each part back in its level's own field."""
    own = _level_map(m)
    if own is None:
        return m
    active = m.get("attribute") == ACTIVE_CARDS_ATTRIBUTE and m.get("pick") == ACTIVE_CARDS
    if active:
        own = _active_fields(own)
    n = {k: v for k, v in m.items() if k not in own or own[k] == k}
    if active:
        n.pop("attribute", None)
    for k, f in own.items():
        if k in m and k != f and not (active and k == "attribute"):
            n[f] = m[k]
    return n


# ---- reading ---------------------------------------------------------------------------------------------------------
@dataclass
class _Tok:
    kind: str  # str | punct | word | level
    text: str


def tokens(s: str) -> list[_Tok]:
    """A line's tokens: JSON strings, punctuation ( ) [ ] { } , and words."""
    out, i = [], 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = TOKEN.match(s, i)
        if not m:
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

    def __init__(self, toks: list[_Tok], cx: Context | None = None) -> None:
        self.t, self.i, self.cx = toks, 0, cx or Context()

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

    def at_option(self) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "word" and bool(OPTION.fullmatch(tok.text))

    def expect(self, kind: str, text: str) -> None:
        tok = self.take()
        if tok.kind != kind or tok.text != text:
            raise ValueError(f"expected {text!r}, found {tok.text!r}")

    def done(self) -> bool:
        return self.i >= len(self.t)

    def scalar(self) -> str:
        tok = self.take()
        if tok.kind == "punct":
            raise ValueError(f"expected a name or value, found {tok.text!r}")
        return tok.text

    def option(self) -> tuple[str, Any] | None:
        """`key=value`, `key=(block)` or `key=[block, ...]` at the reader, or None."""
        tok = self.peek()
        m = OPTION.fullmatch(tok.text) if tok is not None and tok.kind == "word" else None
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

    def pairs(self, n: Block, close: str) -> None:
        """`key=value, ...` up to the closing bracket, into the block."""
        while not self.at("punct", close):
            kv = self.option()
            if kv is None:
                raise ValueError(f"expected key=value before {close!r}")
            n[kv[0]] = kv[1]
            if self.at("punct", ","):
                self.i += 1
        self.i += 1

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
        days = a is not None and a.kind == "word" and bool(NUMBER.fullmatch(a.text))
        if op in ("+", "-") and days and a and b and c and b.kind == "word" and c.text == ")":  # (date + 7 DAYS)
            self.i += 3
            return {
                "type": "arithmetic_date",
                "operator": op,
                "number": a.text,
                "date_quantity": b.text,
                "LHS": lhs,
            }
        rhs = self.expression()
        self.expect("punct", ")")
        return {"type": "arithmetic_generic", "operator": op, "LHS": lhs, "RHS": rhs}

    def starts_value(self, calls: bool = True) -> bool:
        """The next token starts a value block (not a name, code or option); `name(` only when calls may stand here."""
        tok = self.peek()
        if tok is None or tok.kind == "str":
            return False
        if tok.kind == "punct":
            return tok.text == "("
        nxt = self.peek(1)
        called = (
            calls and nxt is not None and nxt.kind == "punct" and nxt.text == "(" and not OPTION.fullmatch(tok.text)
        )
        return _looks_like_value(tok.text) or tok.text.endswith(".") or called

    def expression(self) -> Block:
        """A value block."""
        tok = self.peek()
        if tok is None:
            raise ValueError("expected a value, the line ends")
        if tok.kind == "punct" and tok.text == "(":
            return self.group()
        if tok.kind == "str":
            self.i += 1
            return {"type": "static_string_value", "value": tok.text}
        if tok.kind != "word":
            raise ValueError(f"expected a value, found {tok.text!r}")
        self.i += 1
        return self._word(tok.text)

    def _word(self, w: str) -> Block:
        """A value block that starts with a word."""
        if NUMBER.fullmatch(w):
            return {"type": "static_value", "value": w}
        if DATE.fullmatch(w):
            return {"type": "static_date", "value": w}
        if w.startswith("$"):
            if w[1:] not in self.cx.defs:
                known = ", ".join(self.cx.defs) or "none"
                raise ValueError(f"{w} is not defined on a def line before it (defined: {known})")
            return copy.deepcopy(self.cx.defs[w[1:]])
        if w == TODAY or (w.startswith(TODAY + ".") and w[len(TODAY) + 1 :] in PARTS):
            return {"type": "current_date_block", "current_date_type": w[len(TODAY) + 1 :] or "DATE"}
        head, dot, rest = w.partition(".")
        if dot and head in VALUE_WORDS:
            return self._attribute(head, rest)
        if dot and head == MEMBER:
            level, _, attr = rest.partition(".")
            return self._suffix({"type": "member_attribute", "ATTRIBUTE_TYPE": level, "ATTRIBUTE": self._name(attr)})
        if self.at("punct", "("):
            self.i += 1
            return self._call(w)
        raise ValueError(f"expected a value (a number, a date, activity.<attribute>, (a * b), ...), found {w!r}")

    def _call(self, w: str) -> Block:
        """`name(...)` after its opening parenthesis: a rate, a setting, a date or any block."""
        if w == RATE:
            n: Block = {"type": "currency_converter", "conversion_type": self.scalar()}
            if self.at("punct", ","):
                self.i += 1
                n["LOYALTY_ACCOUNT"] = self.scalar()
        elif w == SETTING:
            n = {"type": "blueprint_variable", "BLUEPRINT_ATTRIBUTE_TYPE": self.scalar()}
        elif w == "date":
            n = {"type": "static_date", "value": self.scalar()}
        else:
            n = {"type": w}
            self.pairs(n, ")")
            return n
        self.expect("punct", ")")
        return n

    def _attribute(self, word: str, rest: str) -> Block:
        """`<word>.<attribute>[.PART]{...}`."""
        kind, fixed, key = VALUE_WORDS[word]
        part = None
        if rest:
            attr, dot, last = rest.rpartition(".")
            if dot and last in PARTS:
                part = last
            else:
                attr = rest
        else:
            attr = self._name("")
            nxt = self.peek()
            if nxt is not None and nxt.kind == "word" and nxt.text[:1] == "." and nxt.text[1:] in PARTS:
                part = self.take().text[1:]
        n: Block = {"type": kind, **fixed, key: attr}
        if part is not None:
            lst = "activity" if word == "activity" else LEVEL_LISTS.get(fixed.get("ATTRIBUTE_TYPE", ""))
            n["date_operator" if kind == "member_attribute" else self.cx.part_field(lst, attr)] = part
        return self._suffix(n)

    def _name(self, rest: str) -> str:
        if rest:
            return rest
        tok = self.take()
        if tok.kind != "str":
            raise ValueError("expected an attribute name")
        return tok.text

    def _suffix(self, n: Block) -> Block:
        if self.at("punct", "{"):
            self.i += 1
            self.pairs(n, "}")
        return n

    def subject(self) -> list[_Tok]:
        """The tokens before a condition's operator (a `[level]` is one token of kind level)."""
        expected = f"expected an operator ({' '.join(OPERATORS)})"
        out: list[_Tok] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise ValueError(expected)
            if tok.kind == "word" and tok.text in OPERATORS:
                return out
            if tok.kind == "punct":
                if tok.text != "[":
                    raise ValueError(f"{expected}, found {tok.text!r}")
                self.i += 1
                out.append(_Tok("level", self.scalar()))
                self.expect("punct", "]")
            elif tok.kind == "word" and OPTION.fullmatch(tok.text):
                raise ValueError(f"{expected}, found {tok.text!r}")
            else:
                out.append(self.take())


def _take(pre: list[_Tok], what: str) -> str:
    if not pre or pre[0].kind == "level":
        raise ValueError(f"expected {what}")
    return pre.pop(0).text


def _is(pre: list[_Tok], *words: str) -> bool:
    return bool(pre) and pre[0].kind == "word" and pre[0].text in words


SUBJECTS = ", ".join((*OBJECT_WORDS, PRODUCT, PAYMENT, MEMBER, *WORD_LEVELS))


def _subject(pre: list[_Tok], filter_line: bool, cx: Context) -> tuple[Block, str | None]:
    """The condition a subject names (in uniform keys, without operator) and its attribute's catalog list."""
    if not pre or pre[0].kind != "word":
        raise ValueError(f"expected a subject ({SUBJECTS})")
    word = pre.pop(0).text
    if word == PAYMENT:
        return {
            "type": "if_payment_attribute",
            "payment_attribute": _take(pre, "a payment attribute"),
        }, "payment_method"
    n: Block = {"type": "activity_criteria" if filter_line else "if_generic_adaptive"}
    lst: str | None
    if word in OBJECT_WORDS or word == PRODUCT:
        lst = _object_subject(n, word, pre)
    elif word == MEMBER:
        lst = "member"
        if pre and pre[0].kind == "level":
            n["attribute_level"] = pre.pop(0).text
            lst = LEVEL_LISTS.get(n["attribute_level"])
        n["attribute"] = _take(pre, "an attribute")
    elif word in WORD_LEVELS:
        lst = LEVEL_LISTS.get(WORD_LEVELS[word])
        _level_subject(n, WORD_LEVELS[word], pre, cx)
    else:
        raise ValueError(f"expected a subject ({SUBJECTS}), found {word!r}")
    if pre:
        raise ValueError(f"expected an operator ({' '.join(OPERATORS)}), found {pre[0].text!r}")
    return n, lst


def _object_subject(n: Block, word: str, pre: list[_Tok]) -> str | None:
    """`<object> [<who>] [<level>] <attribute>`; returns the attribute's catalog list."""
    n["OBJECT"] = PRODUCTS if word == PRODUCT else word
    lst: str | None = OBJECT_LISTS.get(n["OBJECT"], n["OBJECT"])
    if word == "third_party":
        n["third_parties_dropdown"] = _take(pre, "the third party")
    if word == "airport" and len(pre) > 1 and pre[0].kind != "level":
        n["airporttypes"] = _take(pre, "origin or destination")
    level = None
    if pre and pre[0].kind == "level":
        level = pre.pop(0).text
    elif _is(pre, *WORD_LEVELS) and len(pre) > 1:
        level = WORD_LEVELS[pre.pop(0).text]
    if level is not None:
        n["attribute_level"], lst = level, LEVEL_LISTS.get(level)
    n["attribute"] = _take(pre, "an attribute")
    if word == PRODUCT:
        n["ProductOrOfferContext"] = PRODUCTS
    return lst


def _level_subject(n: Block, level: str, pre: list[_Tok], cx: Context) -> None:
    """A member level's subject: the card, plan or programme, the scope, the attribute, a vector counter's key."""
    n["attribute_level"] = level
    if level in COUNTER_LEVELS:
        _counter_subject(n, level, pre, cx)
        return
    names = [t for t in pre if not (t.kind == "word" and t.text in ("any", "transaction"))]
    scopes = [t.text for t in pre if t.kind == "word" and t.text in ("any", "transaction")]
    scope = scopes[0] if scopes else None
    del pre[:]
    if len(scopes) > 1:
        raise ValueError("expected one of `any` and `transaction`")
    if any(t.kind == "level" for t in names):
        raise ValueError("a level in brackets goes after `member`")
    first_active = bool(names) and names[0].kind == "word" and names[0].text == ACTIVE
    if level in PICKERS and (len(names) > 1 or first_active):
        first = names.pop(0)
        n["pick"] = ACTIVE_CARDS if first_active else Context.read(first.text, cx.names(PICKERS[level]))
        if first_active and not names:
            n["attribute"] = ACTIVE_CARDS_ATTRIBUTE
    if scope is not None:
        if level not in SCOPE_CODES:
            raise ValueError(f"`{scope}` goes with a card or a bank_account")
        n["scope"] = SCOPE_CODES[level][scope]
    if "attribute" not in n:
        if len(names) != 1:
            raise ValueError("expected one attribute before the operator")
        n["attribute"] = names[0].text
    if level == TIER_LEVEL:
        n[TIER_DEFAULT[0]] = TIER_DEFAULT[1]


def _counter_subject(n: Block, level: str, pre: list[_Tok], cx: Context) -> None:
    """`<counter word> "<name>" [at <key> [as <key name>]]`."""
    name = _take(pre, "the counter's name")
    declared = cx.counters.get(name)
    if declared is not None and (declared.get("level") or "MA_O") == level:
        del n["attribute_level"]  # the compose step fills a declared counter's level
    n["attribute"] = name
    if _is(pre, "at"):
        pre.pop(0)
        n["vector_counter_name"] = n["vector_counter_namespace"] = _take(pre, "the counter's key")
        if _is(pre, "as"):
            pre.pop(0)
            n["vector_counter_name"] = _take(pre, "the key's name")


def _condition(r: _Reader, filter_line: bool, cx: Context) -> Block:
    """A condition after its `if` (a Filter line has none)."""
    pre = r.subject()
    part = None
    if pre and pre[-1].kind == "word":
        head, dot, last = pre[-1].text.rpartition(".")
        if dot and head and last in PARTS:
            pre[-1], part = _Tok("word", head), last
    n, lst = _subject(pre, filter_line, cx)
    attr = n.get("attribute", n.get("payment_attribute"))
    if part is not None:
        n[cx.part_field(lst, attr, n["type"] != "if_payment_attribute" and "OBJECT" not in n)] = part
    n["operators"] = r.scalar()
    names = cx.names(PICKERS["MA_CC"]) if attr == ACTIVE_CARDS_ATTRIBUTE else cx.names(lst, attr)
    plain = cx.field_valued(lst, attr) or attr == ACTIVE_CARDS_ATTRIBUTE
    if r.at("punct", "["):
        r.i += 1
        codes = []
        while not r.at("punct", "]"):
            codes.append(Context.read(r.scalar(), names))
            if r.at("punct", ","):
                r.i += 1
        r.i += 1
        n["picked"] = ",".join(codes)
    elif r.starts_value() and not (plain and not r.at("punct", "(")):
        n["RHS_INPUT"] = r.expression()
        if r.at("word", "and"):
            r.i += 1
            n["RHS_VALUE"] = r.expression()
    elif not r.done() and not r.at_option():
        n["value"] = Context.read(r.scalar(), names)
    _rest(r, n, alias=False)
    return _in_screen_order(from_uniform(n)) if n["type"] != "if_payment_attribute" else n


# The order the rules screen builds a condition's fields in: each dropdown hangs on the one before it, so a field
# written before the one it hangs on is cleared when the canvas loads.
CONDITION_ORDER = (
    "type",
    "OBJECT",
    "third_parties_dropdown",
    "airporttypes",
    "attribute_level",
    "ProductOrOfferContext",
    *LEVEL_FIELD_ORDER,
    "tiers",
    "tierdefault",
    "attribute",
    "datetime_operator",
    "date_operator",
)


def _in_screen_order(n: Block) -> Block:
    """The condition with the fields the screen builds first in the screen's order, the rest as written."""
    first = [k for k in CONDITION_ORDER if k in n]
    return {k: n[k] for k in (*first, *(k for k in n if k not in first))}


def _rest(r: _Reader, n: Block, alias: bool, last: str | None = None) -> None:
    """The line's remaining `key=value` options and, for an action, its value block."""
    while not r.done():
        kv = r.option()
        if kv is None:
            if last is None:
                raise ValueError(f"expected key=value, found {r.take().text!r}")
            n[last] = r.expression()
            if not r.done():
                raise ValueError("a value ends the line")
            return
        key = UNALIAS.get(kv[0], kv[0]) if alias else kv[0]
        value = kv[1]
        if alias and key in BLOCK_OPTIONS and isinstance(value, str) and NUMBER.fullmatch(value):
            value = {"type": "static_value", "value": value}
        n[key] = value


def _action(r: _Reader, cx: Context) -> Block:
    n: Block = {"type": "action"}
    if r.at("word", "for"):
        r.i += 1
        n = {"type": "action_third_party", "third_parties_dropdown": r.scalar()}
    kind = r.scalar()
    if kind in RESERVED:
        raise ValueError(f"expected an action (give_points, update_attribute, give_privilege, ...), found {kind!r}")
    n["action_type"] = kind
    last = "FORMULA"
    if kind == "update_attribute" and not r.done() and not r.at_option() and not r.starts_value():
        tok = r.peek()
        if tok is not None and tok.kind == "word" and tok.text in WORD_LEVELS:
            n["mta_type_value"] = WORD_LEVELS[r.scalar()]
        n["mta_attribute"] = r.scalar()
        if r.at("word", "at"):
            r.i += 1
            n["vector_counter_name"] = n["vector_counter_namespace"] = r.scalar()
            if r.at("word", "as"):
                r.i += 1
                n["vector_counter_name"] = r.scalar()
        if r.at("word", "="):
            r.i += 1
            last = "RHS_INPUT"
            if not r.done() and not r.starts_value() and not r.at_option():
                n["value"] = r.scalar()
        elif not r.done() and not r.at_option() and not r.starts_value(calls=False):
            n["action_operator"] = r.scalar()
            last = "RHS_INPUT_VALUE"
    _rest(r, n, alias=True, last=last)
    return n


def _counter(r: _Reader) -> dict[str, Any]:
    """A counter line's declaration."""
    c: dict[str, Any] = {"name": r.scalar()}
    if r.at_option() or r.done():  # every field as key=value
        _rest(r, c, alias=False)
        return c
    kind = r.scalar()
    if kind not in COUNTER_TYPES:
        raise ValueError(f"expected the counter's type ({', '.join(COUNTER_TYPES)}), found {kind!r}")
    c.update(level="MA_O", data_type=COUNTER_TYPES[kind], counter_type="REGULAR")
    if r.at("word", "per"):
        r.i += 1
        c.update(counter_type="VECTOR", namespace=r.scalar())
        if r.at("word", "of"):
            r.i += 1
            c["entity_level"] = r.scalar()
    if r.at("word", "transaction_only"):
        r.i += 1
        c["level"] = "MA_A"
    if c["level"] in COUNTER_DEFAULT_TARGET:
        c["target_type"] = COUNTER_DEFAULT_TARGET[c["level"]]
    more: dict[str, Any] = {}
    _rest(r, more, alias=False)
    target = more.pop("target", None)
    if target == "unset":
        c.pop("target_type", None)
    elif target is not None:
        c["target_type"] = target
    c.update(more)
    return c


@dataclass
class _Builder:
    cx: Context
    root: Block = field(default_factory=lambda: {"type": "offer_generic"})
    counters: list[dict[str, Any]] = field(default_factory=list)
    section: Block | None = None
    rules: dict[str, Block] = field(default_factory=dict)
    ors: dict[str, Block] = field(default_factory=dict)
    origin: dict[int, int] = field(default_factory=dict)  # id(block) -> line number

    def current(self, n: int) -> Block:
        if self.section is None:
            self.section = self.new_section("activities", "activity", n)
        return self.section

    def new_section(self, key: str, t: str, n: int) -> Block:
        b: Block = {"type": t}
        self.root.setdefault(key, []).append(b)
        if self.cx.ids:
            b["id"] = f"{t}_{len(self.root[key])}"
        self.origin[id(b)] = n
        return b

    def place(self, owner: Block, key: str, b: Block, n: int) -> None:
        owner.setdefault(key, []).append(b)
        self.origin.setdefault(id(b), n)
        if self.cx.ids:
            self._stamp(b, n, [0])

    def _stamp(self, b: Any, n: int, k: list[int]) -> None:
        """An id on each block of a line that has none: L<n>, then L<n>.2, L<n>.3 ... for the blocks inside it."""
        if isinstance(b, list):
            for x in b:
                self._stamp(x, n, k)
        elif isinstance(b, dict) and "type" in b:
            if "id" not in b:
                k[0] += 1
                b["id"] = f"L{n}" if k[0] == 1 else f"L{n}.{k[0]}"
            for v in list(b.values()):
                self._stamp(v, n, k)

    def branch(self, base: Block, key: str, path: list[tuple[str, str]], n: int) -> tuple[Block, str]:
        """The block and list a line's OR path leads to, OR groups made where first named."""
        owner, k = base, key
        for g, b in path:
            group = self.ors.get(g)
            if group is None:
                group = {"type": "or"}
                if self.cx.ids:
                    group["id"] = f"or{g}"
                self.ors[g] = group
                self.place(owner, k, group, n)
            owner, k = group, f"OPERAND{b}"
        return owner, k


def from_lines(
    text: str, catalog: Any = None, ids: bool = False
) -> tuple[list[Block], list[dict[str, Any]], dict[str, int]]:
    """DSL lines as the rules canvas (compact blocks) and its counters.

    Args:
        text: The lines.
        catalog: The tenant catalog: names are read as their codes and ids, and an attribute's type decides how its
            compared value and date parts are kept.
        ids: Give each block an id that names its line (L7, L7.2 for a block inside it; a rule's is its label), so a
            problem with a block points at the line to change.

    Returns:
        (tops, counters, origin): origin maps each block's path (`activities[0].RULES[1].CONDITION[0]`) to its line.

    Raises:
        DslError: A line does not parse.
    """
    b = _Builder(Context(catalog=catalog, ids=ids))
    if ids:
        b.root["id"] = "offer"
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            _line(b, line, n)
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
    head, r = _Reader(toks[:comma]), _Reader(toks[comma + 1 :], b.cx)
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
        b.place(o, k, whole if whole is not None else _condition(r, filter_line, b.cx), n)
        return
    if path:
        raise ValueError("an action cannot sit in an OR branch")
    b.place(owner, "ACTION", whole if whole is not None else _action(r, b.cx), n)


def _declaration(b: _Builder, label: str, r: _Reader, n: int) -> None:
    """A def, counter or section line."""
    if label == "def":
        name = r.scalar()
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError(f"a def's name is a word, found {name!r}")
        r.expect("punct", ",")
        b.cx.defs[name] = r.expression()
        if not r.done():
            raise ValueError("a def holds one value")
    elif label == "counter":
        c = _counter(r)
        b.counters.append(c)
        b.cx.counters[str(c["name"])] = c
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
    if nxt is not None and (nxt.kind == "str" or (nxt.text != "inside" and not OR_PATH.fullmatch(nxt.text))):
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
    if b.cx.ids:
        rule["id"] = label
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
