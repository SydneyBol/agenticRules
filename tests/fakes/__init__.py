"""Test doubles: a scripted chat model that replays tool calls, and the wire format it records requests in."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from offer_author.ai.model import OnTool, Turn


def wire(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """LangChain messages as the Anthropic Messages API receives them (what ChatAnthropic sends)."""
    from langchain_anthropic.chat_models import _format_messages

    return _format_messages(messages, model=None)[1]


class ScriptedLLM:
    """Replays scripted turns (tests).

    Each item is a list of (name, input) tool calls, or an Exception to raise. Each request's messages are recorded
    in the wire format (`wire`).

    Attributes:
        turns: The turns still to replay.
        model: The model name reported in the diagnostics.
        calls: Each request's messages, as sent.
    """

    def __init__(self, turns: Sequence[Any], model: str = "scripted") -> None:
        """Replays these turns, in order."""
        self.turns, self.model = list(turns), model
        self.calls: list[list[dict[str, Any]]] = []

    def turn(
        self,
        system: list[dict[str, Any]],
        messages: Sequence[BaseMessage],
        tools: list[dict[str, Any]] | None = None,
        on_tool: OnTool | None = None,
    ) -> Turn:
        """The next scripted turn; its tool calls are started as a streamed turn would start them."""
        self.calls.append(wire(messages))
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        calls = [(f"toolu_{len(self.calls)}_{i}", n, x) for i, (n, x) in enumerate(item)]
        for tid, n, x in calls:
            if on_tool:
                on_tool(tid, n, x)
        msg = AIMessage(
            content=[], tool_calls=[{"id": tid, "name": n, "args": x, "type": "tool_call"} for tid, n, x in calls]
        )
        return Turn(msg, "tool_use" if calls else "end_turn", {"input_tokens": 0, "output_tokens": 0})
