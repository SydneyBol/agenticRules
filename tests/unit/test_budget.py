"""The hard limit on Bedrock model calls (ai/budget.py)."""

import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from offer_author.ai.budget import MAX_LLM_CALLS, Ledger
from offer_author.ai.model import ChatLLM, LLMError


class _Chat:
    """A chat model that answers every request with one text chunk and counts the requests."""

    def __init__(self):
        self.sent = 0

    def bind_tools(self, tools):
        return self

    def stream(self, request):
        self.sent += 1
        yield AIMessageChunk(content="ok", response_metadata={"stop_reason": "end_turn"})

    def invoke(self, request, **kw):
        self.sent += 1
        return AIMessage(content="")


def test_the_limit_is_500_calls():
    assert MAX_LLM_CALLS == 500


def test_every_request_takes_a_call_and_none_is_sent_past_the_limit(tmp_path):
    ledger = Ledger(tmp_path / "calls.json")
    chat = _Chat()
    llm = ChatLLM(model="x", chat=chat, ledger=ledger)
    llm.turn([{"type": "text", "text": "s"}], [HumanMessage(content="hi")])
    llm.warm([{"type": "text", "text": "s"}])
    assert ledger.calls() == 2 and chat.sent == 2
    (tmp_path / "calls.json").write_text(json.dumps({"calls": MAX_LLM_CALLS - 1}))
    llm.turn([{"type": "text", "text": "s"}], [HumanMessage(content="hi")])
    with pytest.raises(LLMError, match="budget spent: 500 of 500"):
        llm.turn([{"type": "text", "text": "s"}], [HumanMessage(content="hi")])
    with pytest.raises(LLMError):
        llm.warm([{"type": "text", "text": "s"}])
    assert chat.sent == 3 and ledger.calls() == MAX_LLM_CALLS


def test_a_given_test_chat_model_counts_nothing_unless_handed_a_ledger():
    assert ChatLLM(model="x", chat=_Chat()).ledger is None
