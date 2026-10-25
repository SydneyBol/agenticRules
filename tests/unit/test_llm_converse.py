"""ConverseLLM (LangChain, Bedrock Converse API): the request it builds and the Turn it returns, offline."""
from langchain_aws.chat_models.bedrock_converse import _messages_to_bedrock
from langchain_core.messages import AIMessage

from offer_author.ai.llm import ConverseLLM, _Reply

SYSTEM = [{"type": "text", "text": "S"}, {"type": "text", "text": "STATIC", "cache_control": {"type": "ephemeral"}},
          {"type": "text", "text": "CATALOG", "cache_control": {"type": "ephemeral"}}]
CACHE = {"cachePoint": {"type": "default"}}


def reply(calls, text=None, usage=None, stop="tool_use"):
    ai = AIMessage(content=([{"type": "text", "text": text}] if text else []) +
                   [{"type": "tool_use", "id": i, "name": n, "input": x} for i, n, x in calls],
                   tool_calls=[{"id": i, "name": n, "args": x} for i, n, x in calls],
                   usage_metadata=usage or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                   response_metadata={"stopReason": stop})
    return ai


class FakeChat:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def bind_tools(self, tools, tool_choice=None):
        self.tools, self.tool_choice = tools, tool_choice
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        return self.replies.pop(0)


def llm(replies):
    m = ConverseLLM.__new__(ConverseLLM)
    m.model, m.chat = "test", FakeChat(replies)
    return m


def test_cache_points_close_the_system_blocks_and_the_last_two_user_turns():
    first = _Reply([{"type": "tool_use", "id": "t1", "name": "validate", "input": {}}])
    first.message = reply([("t1", "validate", {})])
    history = [{"role": "user", "content": [{"type": "text", "text": "offer"}]}, {"role": "assistant", "content": first},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}]
    msgs, system = _messages_to_bedrock(ConverseLLM._messages(SYSTEM, history), [])
    assert system == [{"text": "S"}, {"text": "STATIC"}, CACHE, {"text": "CATALOG"}, CACHE]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"][-1] == CACHE and msgs[2]["content"][-1] == CACHE
    assert "toolResult" in msgs[2]["content"][0] and msgs[1]["content"][0]["toolUse"]["toolUseId"] == "t1"
    assert sum(b == CACHE for m in msgs for b in m["content"]) + system.count(CACHE) == 4  # the Bedrock maximum


def test_turn_returns_calls_usage_and_the_message_to_send_back():
    m = llm([reply([("t1", "validate", {"canvas": "rules"})], text="checking",
                   usage={"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250,
                          "input_token_details": {"cache_read": 900, "cache_creation": 200}})])
    t = m.turn(SYSTEM, [{"role": "user", "content": "offer"}], tools=[{"name": "validate", "description": "d", "input_schema": {"type": "object"}}])
    assert t.tool_calls == [("t1", "validate", {"canvas": "rules"})] and t.stop_reason == "tool_use"
    assert t.usage == {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 200}
    assert m.chat.tool_choice == "auto" and m.chat.tools[0]["toolSpec"]["inputSchema"] == {"json": {"type": "object"}}
    # the next turn sends the same AIMessage back (thinking blocks must be replayed unchanged)
    m.chat.replies.append(reply([], text="done", stop="end_turn"))
    m.turn(SYSTEM, [{"role": "user", "content": "offer"}, {"role": "assistant", "content": t.content},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]}])
    assert t.content.message in m.chat.seen[1]


def test_filtered_content_is_a_refusal():
    assert llm([reply([], text="", stop="content_filtered")]).turn(SYSTEM, [{"role": "user", "content": "x"}]).stop_reason == "refusal"
