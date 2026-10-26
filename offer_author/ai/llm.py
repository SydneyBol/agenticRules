"""Model clients. The agent talks to anything with `turn(system, messages, tools) -> Turn`, so tests use scripted fakes."""
import os
import time
from dataclasses import dataclass, field


@dataclass
class Turn:
    content: list  # assistant content blocks, sent back verbatim on a repair round
    tool_calls: list  # [(tool_use_id, name, input)]
    stop_reason: str
    usage: dict = field(default_factory=dict)


class LLMError(Exception):
    pass


DEFAULT_MODEL = "us.anthropic.claude-sonnet-5-5"


class BedrockLLM:
    """Claude on Amazon Bedrock (bedrock-runtime via AnthropicBedrock; Mantle isn't enabled on the account).

    Settings verified in the spike: tool_choice auto (forced choice 400s on Opus/Sonnet 5.5), effort medium,
    tenant catalog in a cached system block. Default Sonnet 5.5: at parity with Opus 5.5 on every suite and the hosted
    QA (2026-10-25); OFFER_AUTHOR_MODEL=us.anthropic.claude-opus-5-5 switches back.
    """

    def __init__(self, model=None, region=None, effort="medium", thinking=None, max_tokens=16000, retries=2, client=None):
        from anthropic import AnthropicBedrock
        self.model = model or os.environ.get("OFFER_AUTHOR_MODEL", DEFAULT_MODEL)
        self.effort, self.thinking, self.max_tokens, self.retries = effort, thinking, max_tokens, retries
        self.client = client or AnthropicBedrock(aws_region=region or os.environ.get("AWS_REGION", "us-west-2"))

    def turn(self, system, messages, tools=None, on_tool=None):
        """One model turn, streamed. on_tool(id, name, input) is called as soon as each tool call is complete, while
        the model is still writing the rest of the turn (the agent starts the tool then). Once a tool has started, a
        failure is not retried: the tool may have changed the session."""
        from anthropic import APIConnectionError, APIStatusError, RateLimitError
        started = []
        for attempt in range(self.retries + 1):
            try:
                with self.client.messages.stream(model=self.model, max_tokens=self.max_tokens, output_config={"effort": self.effort},
                                                 tools=tools, tool_choice={"type": "auto"}, system=system, messages=messages,
                                                 **({"thinking": self.thinking} if self.thinking else {})) as stream:
                    for event in stream:
                        if on_tool and event.type == "content_block_stop" and event.content_block.type == "tool_use":
                            b = event.content_block
                            started.append(b.id)
                            on_tool(b.id, b.name, b.input)
                    r = stream.get_final_message()
                break
            except RateLimitError as e:
                err = e
            except APIStatusError as e:
                if e.status_code < 500:
                    raise LLMError(f"Bedrock {e.status_code}: {str(e)[:300]}") from e
                err = e
            except APIConnectionError as e:
                err = e
            if attempt == self.retries or started:
                raise LLMError(f"{type(err).__name__}: {str(err)[:300]}") from err
            time.sleep(1.5 * 2 ** attempt)
        u = r.usage
        return Turn(content=r.content, tool_calls=[(b.id, b.name, b.input) for b in r.content if b.type == "tool_use"],
                    stop_reason=r.stop_reason,
                    usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                           "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                           "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0})


def make_llm(**kw):
    """The Bedrock client from the environment: OFFER_AUTHOR_EFFORT (default medium); OFFER_AUTHOR_THINKING = a thinking
    type (adaptive, or between_tools: thinking off on Sonnet 5.5), default the model's own (adaptive)."""
    kw.setdefault("effort", os.environ.get("OFFER_AUTHOR_EFFORT", "medium"))
    if os.environ.get("OFFER_AUTHOR_THINKING"):
        kw.setdefault("thinking", {"type": os.environ["OFFER_AUTHOR_THINKING"]})
    return BedrockLLM(**kw)


class ScriptedLLM:
    """Replays scripted turns: each item is a list of (name, input) tool calls, or an Exception to raise."""

    def __init__(self, turns, model="scripted"):
        self.turns, self.model, self.calls = list(turns), model, []

    def turn(self, system, messages, tools=None, on_tool=None):
        self.calls.append(messages)
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        calls = [(f"toolu_{len(self.calls)}_{i}", n, x) for i, (n, x) in enumerate(item)]
        for tid, n, x in calls if on_tool else []:
            on_tool(tid, n, x)
        content = [{"type": "tool_use", "id": tid, "name": n, "input": x} for tid, n, x in calls]
        return Turn(content=content, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn",
                    usage={"input_tokens": 0, "output_tokens": 0})
