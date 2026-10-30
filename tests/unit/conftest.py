"""Unit tests find catalog values by their words: the value-search models (ai/value_search.py) are left out unless a
test passes its own search."""

import pytest

from offer_author.ai import agent


@pytest.fixture(autouse=True)
def words_only_lookup(monkeypatch):
    monkeypatch.setattr(agent, "searcher", lambda settings: None)
