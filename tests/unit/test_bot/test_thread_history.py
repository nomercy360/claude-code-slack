"""Tests for thread history fetcher."""

from unittest.mock import AsyncMock

import pytest

from src.bot.utils.thread_history import MAX_CONTEXT_CHARS, fetch_thread_context

BOT_USER = "B001"


@pytest.fixture
def mock_client():
    return AsyncMock()


def _replies(*messages):
    """Helper to build a conversations_replies response."""
    return {"messages": [{"user": u, "text": t} for u, t in messages]}


async def test_no_prior_messages(mock_client):
    """Single message in thread -> empty context."""
    mock_client.conversations_replies.return_value = _replies(("U1", "hello"))
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert result == ""


async def test_two_messages_returns_first(mock_client):
    """Two messages -> first is returned as context."""
    mock_client.conversations_replies.return_value = _replies(
        ("U1", "first msg"), ("U1", "second msg")
    )
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert "first msg" in result
    assert "second msg" not in result


async def test_bot_messages_labeled_assistant(mock_client):
    """Bot messages get [Assistant] label."""
    mock_client.conversations_replies.return_value = _replies(
        ("U1", "question"), (BOT_USER, "answer"), ("U1", "followup")
    )
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert "[Assistant]" in result
    assert "[User <@U1>]" in result
    # Last message (followup) should NOT be in context
    assert "followup" not in result


async def test_truncation_at_budget(mock_client):
    """Context is truncated to MAX_CONTEXT_CHARS."""
    long_text = "x" * 5000
    mock_client.conversations_replies.return_value = _replies(
        ("U1", long_text), ("U1", long_text), ("U1", "current")
    )
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert len(result) <= MAX_CONTEXT_CHARS + 100  # small margin for label


async def test_api_error_returns_empty(mock_client):
    """API errors should not crash, just return empty."""
    mock_client.conversations_replies.side_effect = Exception("rate limited")
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert result == ""


async def test_empty_text_messages_skipped(mock_client):
    """Messages with empty text are skipped."""
    mock_client.conversations_replies.return_value = _replies(
        ("U1", ""), ("U1", "real msg"), ("U1", "current")
    )
    result = await fetch_thread_context(mock_client, "C1", "1.0", BOT_USER)
    assert "real msg" in result
