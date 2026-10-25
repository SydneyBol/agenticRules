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

    def turn(self, system, messages, tools=None):
        from anthropic import APIConnectionError, APIStatusError, RateLimitError
        for attempt in range(self.retries + 1):
            try:
                r = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, output_config={"effort": self.effort},
                                                tools=tools, tool_choice={"type": "auto"}, system=system, messages=messages,
                                                **({"thinking": self.thinking} if self.thinking else {}))
                break
            except RateLimitError as e:
                err = e
            except APIStatusError as e:
                if e.status_code < 500:
                    raise LLMError(f"Bedrock {e.status_code}: {str(e)[:300]}") from e
                err = e
            except APIConnectionError as e:
                err = e
            if attempt == self.retries:
                raise LLMError(f"{type(err).__name__}: {str(err)[:300]}") from err
            time.sleep(1.5 * 2 ** attempt)
        u = r.usage
        return Turn(content=r.content, tool_calls=[(b.id, b.name, b.input) for b in r.content if b.type == "tool_use"],
                    stop_reason=r.stop_reason,
                    usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                           "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                           "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0})


class _Reply(list):
    """An assistant turn's content blocks, carrying the LangChain message they came from: sent back unchanged on the
    next turn (thinking blocks are bound to their conversation)."""
    message = None


_CACHE = {"cachePoint": {"type": "default"}}
_LIMITERS = {}


def _limiter(rps):
    """One rate limiter per process and rate, shared by every run (parallel suites and requests queue here)."""
    from langchain_core.rate_limiters import InMemoryRateLimiter
    if rps not in _LIMITERS:
        _LIMITERS[rps] = InMemoryRateLimiter(requests_per_second=rps, check_every_n_seconds=0.1, max_bucket_size=max(1, int(rps * 4)))
    return _LIMITERS[rps]


class ConverseLLM:
    """Claude on Amazon Bedrock through LangChain (langchain-aws ChatBedrockConverse, the Converse API). Same turn()
    contract as BedrockLLM; the library supplies the request, botocore adaptive retries (they back off on throttling),
    a process-wide rate limiter (OFFER_AUTHOR_RPS) and token usage with cache reads and writes.

    Cache points (4, the Bedrock maximum): end of the static system text (the tools render before it, so it covers
    them), end of the tenant catalog, the previous user turn (written a turn earlier, so it is read) and the newest
    user turn (written for the next turn).
    effort: output_config.effort (Opus and Haiku 5.5 default medium, Sonnet 5.5 high). thinking: None = the model's
    default (adaptive); {"type": "between_tools"} turns it off on Sonnet 5.5 (effort high or lower only).
    """

    def __init__(self, model=None, region=None, effort="medium", thinking=None, max_tokens=16000, retries=2, rps=None,
                 client=None):
        from botocore.config import Config
        from langchain_aws import ChatBedrockConverse
        self.model = model or os.environ.get("OFFER_AUTHOR_MODEL", DEFAULT_MODEL)
        self.effort, self.thinking = effort, thinking
        fields = {"output_config": {"effort": effort}, **({"thinking": thinking} if thinking else {})}
        rps = rps or float(os.environ.get("OFFER_AUTHOR_RPS", 2))
        self.chat = ChatBedrockConverse(
            model_id=self.model, region_name=region or os.environ.get("AWS_REGION", "us-west-2"), max_tokens=max_tokens,
            additional_model_request_fields=fields, rate_limiter=_limiter(rps), client=client,
            config=Config(retries={"mode": "adaptive", "max_attempts": retries + 1}, read_timeout=300))

    @staticmethod
    def _tools(tools):
        return [{"toolSpec": {"name": t["name"], "description": t["description"], "inputSchema": {"json": t["input_schema"]}}}
                for t in tools or []]

    @staticmethod
    def _messages(system, messages):
        """The agent's Messages-API history -> LangChain messages, with the cache points of the class docstring."""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        sys = []
        for b in system:
            sys.append({"text": b["text"]})
            if b.get("cache_control"):
                sys.append(_CACHE)
        out, user_ends = [SystemMessage(content=sys)], []
        for m in messages:
            c = m["content"]
            if m["role"] == "assistant":
                out.append(c.message if isinstance(c, _Reply) else AIMessage(content=[b for b in c if b.get("type") == "text"]))
                continue
            for b in [{"type": "text", "text": c}] if isinstance(c, str) else c:
                if b.get("type") == "tool_result":
                    out.append(ToolMessage(content=b["content"], tool_call_id=b["tool_use_id"]))
                else:
                    out.append(HumanMessage(content=[{"text": b["text"]}]))
            user_ends.append(len(out))
        for i in reversed(user_ends[-2:]):  # a cache point closes the previous and the newest user turn
            out.insert(i, HumanMessage(content=[_CACHE]))
        return out

    def turn(self, system, messages, tools=None):
        from botocore.exceptions import BotoCoreError, ClientError
        try:
            ai = self.chat.bind_tools(self._tools(tools), tool_choice="auto").invoke(self._messages(system, messages))
        except ClientError as e:
            raise LLMError(f"Bedrock {e.response.get('Error', {}).get('Code')}: {str(e)[:300]}") from e
        except BotoCoreError as e:
            raise LLMError(f"{type(e).__name__}: {str(e)[:300]}") from e
        u = ai.usage_metadata or {}
        d = u.get("input_token_details") or {}
        read, write = d.get("cache_read", 0) or 0, d.get("cache_creation", 0) or 0
        stop = ai.response_metadata.get("stopReason", "")
        content = _Reply(ai.content if isinstance(ai.content, list) else [{"type": "text", "text": ai.content}])
        content.message = ai
        return Turn(content=content, tool_calls=[(c["id"], c["name"], c["args"]) for c in ai.tool_calls],
                    stop_reason="refusal" if stop in ("content_filtered", "guardrail_intervened") else stop,
                    usage={"input_tokens": max(0, u.get("input_tokens", 0) - read - write), "output_tokens": u.get("output_tokens", 0),
                           "cache_read_input_tokens": read, "cache_creation_input_tokens": write})


def make_llm(**kw):
    """The model client from the environment: OFFER_AUTHOR_CLIENT=converse (LangChain, Converse API) or invoke
    (AnthropicBedrock, default); OFFER_AUTHOR_EFFORT (default medium); OFFER_AUTHOR_THINKING = a thinking type
    (adaptive, or between_tools: thinking off on Sonnet 5.5), default the model's own (adaptive)."""
    kw.setdefault("effort", os.environ.get("OFFER_AUTHOR_EFFORT", "medium"))
    if os.environ.get("OFFER_AUTHOR_THINKING"):
        kw.setdefault("thinking", {"type": os.environ["OFFER_AUTHOR_THINKING"]})
    return ConverseLLM(**kw) if os.environ.get("OFFER_AUTHOR_CLIENT", "invoke") == "converse" else BedrockLLM(**kw)


class ScriptedLLM:
    """Replays scripted turns: each item is a list of (name, input) tool calls, or an Exception to raise."""

    def __init__(self, turns, model="scripted"):
        self.turns, self.model, self.calls = list(turns), model, []

    def turn(self, system, messages, tools=None):
        self.calls.append(messages)
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        calls = [(f"toolu_{len(self.calls)}_{i}", n, x) for i, (n, x) in enumerate(item)]
        content = [{"type": "tool_use", "id": tid, "name": n, "input": x} for tid, n, x in calls]
        return Turn(content=content, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn",
                    usage={"input_tokens": 0, "output_tokens": 0})
