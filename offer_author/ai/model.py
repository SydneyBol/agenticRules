"""The chat model the agent talks to: Claude on Amazon Bedrock through LangChain (tests/fakes has a scripted one).

The graph (graph.py) needs only `turn(system, messages, tools, on_tool) -> Turn` and, to warm the prompt cache,
`warm(system, tools)`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

DEFAULT_MODEL = "us.anthropic.claude-sonnet-5-5"
# The feature's model settings, fixed for production and every test run (2026-10-27): Sonnet 5.5, effort low, the
# model's own (adaptive) thinking. Measured on the same suites: 215/215 BITs, p95 28.7 s, $0.050/run; effort medium
# 202/215 and p95 34.8 s; Opus low equal accuracy, p95 30.6 s, $0.058 (~/Documents/Gravty/ModelChoice-Sonnet55.md).
DEFAULT_EFFORT = "low"

OnTool = Callable[[str, str, dict[str, Any]], None]
Call = tuple[str | None, str | None, Any]


class LLMError(Exception):
    """The model could not be reached or refused the request's shape (the run ends with an error)."""


@dataclass
class Turn:
    """One model turn.

    Attributes:
        message: The assistant turn (thinking, text, tool calls), resent unchanged on later turns. Its tool calls are
            in the order the model wrote them; a call whose input was not valid JSON is among them with no arguments.
        stop_reason: Why the turn ended (tool_use, end_turn, refusal, ...).
        usage: Token counts as the Messages API reports them.
        invalid: Ids of the tool calls whose input was not valid JSON.
    """

    message: AIMessage
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)
    invalid: frozenset[str] = frozenset()


def ordered_calls(msg: AIMessage) -> list[Call]:
    """The message's tool calls in the order the model wrote them, as (id, name, input).

    A call whose input was not valid JSON is included with input None: it still needs a result.
    """
    calls: list[Call] = [(c["id"], c["name"], c["args"]) for c in msg.tool_calls]
    calls += [(c["id"], c["name"], None) for c in msg.invalid_tool_calls if c.get("id")]
    order = [b.get("id") for b in msg.content if isinstance(b, dict)] if isinstance(msg.content, list) else []
    return sorted(calls, key=lambda call: order.index(call[0]) if call[0] in order else len(order))


def _turn(msg: AIMessage, stop_reason: str, usage: dict[str, int]) -> Turn:
    """A turn whose message lists every tool call, in written order.

    A call that did not parse is listed with no arguments: the request then carries the same empty input as before.
    """
    calls = ordered_calls(msg)
    message = AIMessage(
        content=msg.content,
        tool_calls=[{"id": i, "name": n, "args": a or {}, "type": "tool_call"} for i, n, a in calls],
        response_metadata=msg.response_metadata,
        usage_metadata=msg.usage_metadata,
        id=msg.id,
    )
    return Turn(message, stop_reason, usage, frozenset(i for i, _, a in calls if a is None and i))


def usage_counts(u: dict[str, Any]) -> dict[str, int]:
    """LangChain usage metadata as the Messages API counts it (input without the cache reads and writes)."""
    details = u.get("input_token_details") or {}
    read = details.get("cache_read") or 0
    write = details.get("cache_creation") or (details.get("ephemeral_5m_input_tokens") or 0) + (
        details.get("ephemeral_1h_input_tokens") or 0
    )
    return {
        "input_tokens": (u.get("input_tokens") or 0) - read - write,
        "output_tokens": u.get("output_tokens") or 0,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
    }


def _system_message(system: list[dict[str, Any]]) -> SystemMessage:
    """The system prompt's content blocks as one message."""
    return SystemMessage(content=cast("list[str | dict[Any, Any]]", system))


class ChatLLM:
    """Claude on Amazon Bedrock through LangChain's `ChatAnthropicBedrock` (InvokeModel with the Messages format).

    Settings: tool_choice auto (a forced choice is rejected on Opus/Sonnet 5.5), effort DEFAULT_EFFORT, adaptive
    thinking, the tenant catalog in a cached system block. Sonnet 5.5 by default (at parity with Opus 5.5 on every
    suite and the hosted QA, 2026-10-25); OFFER_AUTHOR_MODEL=us.anthropic.claude-opus-5-5 switches back.

    Attributes:
        model: The Bedrock model id.
        effort: The output effort (reported by /health).
        thinking: The thinking type (reported by /health).
    """

    def __init__(
        self,
        model: str | None = None,
        region: str | None = None,
        effort: str = DEFAULT_EFFORT,
        thinking: dict[str, Any] | None = None,
        max_tokens: int = 16000,
        retries: int = 2,
        chat: Any = None,
    ) -> None:
        """Sets up the client.

        Args:
            model: The Bedrock model id (default OFFER_AUTHOR_MODEL, else DEFAULT_MODEL).
            region: The AWS region (default AWS_REGION, else us-west-2).
            effort: The output effort.
            thinking: The thinking setting, e.g. {"type": "adaptive"}; None for the model's own.
            max_tokens: The output limit of one turn.
            retries: How often a throttled, failed or dropped request is retried before anything started.
            chat: A LangChain chat model to use instead (tests).
        """
        self.model = model or os.environ.get("OFFER_AUTHOR_MODEL", DEFAULT_MODEL)
        self.effort, self.thinking = effort, (thinking or {}).get("type", "adaptive")
        self.retries = retries
        if chat is None:
            from langchain_aws import ChatAnthropicBedrock

            chat = ChatAnthropicBedrock(  # type: ignore[call-arg]
                model=self.model,
                region_name=region or os.environ.get("AWS_REGION", "us-west-2"),
                max_tokens=max_tokens,
                output_config={"effort": effort},
                thinking=thinking,
                max_retries=0,
                stream_usage=True,
            )
        self.chat = chat

    def turn(
        self,
        system: list[dict[str, Any]],
        messages: Sequence[BaseMessage],
        tools: list[dict[str, Any]] | None = None,
        on_tool: OnTool | None = None,
    ) -> Turn:
        """One model turn, streamed.

        Args:
            system: The system prompt blocks.
            messages: The conversation so far.
            tools: The tools as the model is shown them.
            on_tool: Called with (id, name, input) as soon as each tool call is complete (a later content block has
                started), while the model still writes the rest of the turn.

        Returns:
            The turn.

        Raises:
            LLMError: The request failed. Once a tool has started it is not retried (the tool may have changed the
                run's state).
        """
        from anthropic import APIConnectionError, APIStatusError

        chat = self.chat.bind_tools(tools) if tools else self.chat
        request = [_system_message(system), *messages]
        started: set[str] = set()
        acc: Any = None
        for attempt in range(self.retries + 1):
            acc, open_tools = None, cast("dict[int, str]", {})  # block index -> tool call id, for calls not started yet
            try:
                for chunk in chat.stream(request):
                    acc = chunk if acc is None else acc + chunk
                    if on_tool:
                        self._start_finished(chunk, acc, open_tools, started, on_tool)
                break
            except (APIStatusError, APIConnectionError) as e:  # throttling (429), 5xx and connection drops are retried
                if isinstance(e, APIStatusError) and e.status_code < 500 and e.status_code != 429:
                    raise LLMError(f"Bedrock {e.status_code}: {str(e)[:300]}") from e
                if attempt == self.retries or started:
                    raise LLMError(f"{type(e).__name__}: {str(e)[:300]}") from e
                time.sleep(1.5 * 2**attempt)
        return _turn(acc, acc.response_metadata.get("stop_reason") or "", usage_counts(acc.usage_metadata or {}))

    def warm(self, system: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, int]:
        """Writes the prompt cache at the system blocks' cache points.

        A prefill only (max_tokens 0, not streamed, the same effort and thinking as real turns), with no output.

        Returns:
            The call's usage.
        """
        chat = self.chat.bind_tools(tools) if tools else self.chat
        msg = chat.invoke([_system_message(system), HumanMessage(content="warmup")], max_tokens=0)
        return usage_counts(msg.usage_metadata or {})

    @staticmethod
    def _start_finished(chunk: Any, acc: Any, open_tools: dict[int, str], started: set[str], on_tool: OnTool) -> None:
        """Starts the tool calls a chunk completed.

        LangChain does not surface content_block_stop, so a tool call is complete once a later content block
        shows up.
        """
        for tc in chunk.tool_call_chunks:
            if tc.get("id"):
                open_tools[tc["index"]] = tc["id"]
        seen = (
            [b["index"] for b in chunk.content if isinstance(b, dict) and "index" in b]
            if isinstance(chunk.content, list)
            else []
        )
        seen += [tc["index"] for tc in chunk.tool_call_chunks if tc.get("index") is not None]
        newest = max(seen, default=None)
        for i in [i for i in open_tools if newest is not None and i < newest]:
            tid = open_tools.pop(i)
            call = next((c for c in acc.tool_calls if c["id"] == tid), None)
            if call is not None:
                started.add(tid)
                on_tool(tid, call["name"], call["args"])


def make_llm(**kw: Any) -> ChatLLM:
    """The Bedrock client from the environment.

    OFFER_AUTHOR_EFFORT sets the effort (default DEFAULT_EFFORT); OFFER_AUTHOR_THINKING a thinking type (adaptive, or
    between_tools: thinking off on Sonnet 5.5), default the model's own (adaptive).
    """
    kw.setdefault("effort", os.environ.get("OFFER_AUTHOR_EFFORT", DEFAULT_EFFORT))
    if os.environ.get("OFFER_AUTHOR_THINKING"):
        kw.setdefault("thinking", {"type": os.environ["OFFER_AUTHOR_THINKING"]})
    return ChatLLM(**kw)
