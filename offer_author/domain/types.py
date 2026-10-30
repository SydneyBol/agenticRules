"""The shapes the layers exchange, as types.

Blocks and tool inputs stay JSON-like dicts (the toolbox grammar is data from the stock UI's block sources, not a
fixed schema); counters and composed canvases are typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict

from .problem import Problem

Block = dict[str, Any]
"""One block of a canvas: {"type", "id"?, "fields"?, "picked"?, "inputs"?} (or the model's compact form)."""

ToolInput = dict[str, Any]
"""A tool call's input as the tools read it: plain values, the fields the model left out absent."""

Result = dict[str, Any]
"""A tool result: facts and Problems, worded for the model by offer_author.ai.render."""


class CounterDecl(TypedDict):
    """An offer or activity counter the blocks refer to, as the model declares it."""

    name: str
    data_type: str
    counter_type: str
    level: NotRequired[str | None]
    target_type: NotRequired[str | None]
    namespace: NotRequired[str | None]
    entity_level: NotRequired[str | None]


class LooseBlock(TypedDict):
    """A block on a canvas that hangs from nothing, written disabled, and why it is loose."""

    id: str
    type: str
    reason: Problem


@dataclass
class Composed:
    """One compose of a canvas: the XML the tools wrote, and what the checks found in it.

    Attributes:
        xml: The canvas XML the tools wrote.
        problems: What the model must fix; empty for a clean compose.
        counters: The counters the canvas declared.
        loose: The canvas's loose blocks.
    """

    xml: str
    problems: list[Problem]
    counters: list[CounterDecl]
    loose: list[LooseBlock] = field(default_factory=list)

    @property
    def built(self) -> bool:
        """The canvas holds blocks besides its root (rules, conditions, or loose blocks)."""
        return self.xml.count("<block ") > 1
