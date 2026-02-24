"""Tests for ReactionManager."""

from unittest.mock import AsyncMock

import pytest

from src.bot.utils.reactions import ReactionManager


@pytest.fixture
def client():
    return AsyncMock()


@pytest.fixture
def rm(client):
    return ReactionManager(client, channel_id="C123", message_ts="1234.5678")


async def test_set_adds_emoji(rm, client):
    await rm.set("eyes")
    client.reactions_add.assert_called_once_with(
        channel="C123", timestamp="1234.5678", name="eyes"
    )
    assert rm.current_emoji == "eyes"


async def test_set_swaps_emoji(rm, client):
    await rm.set("eyes")
    client.reactions_add.reset_mock()
    client.reactions_remove.reset_mock()

    await rm.set("hammer_and_wrench")

    # New added first, then old removed
    client.reactions_add.assert_called_once_with(
        channel="C123", timestamp="1234.5678", name="hammer_and_wrench"
    )
    client.reactions_remove.assert_called_once_with(
        channel="C123", timestamp="1234.5678", name="eyes"
    )
    assert rm.current_emoji == "hammer_and_wrench"


async def test_set_same_emoji_noop(rm, client):
    await rm.set("eyes")
    client.reactions_add.reset_mock()

    await rm.set("eyes")
    client.reactions_add.assert_not_called()


async def test_clear_removes_current(rm, client):
    await rm.set("eyes")
    client.reactions_remove.reset_mock()

    await rm.clear()
    client.reactions_remove.assert_called_once_with(
        channel="C123", timestamp="1234.5678", name="eyes"
    )
    assert rm.current_emoji is None


async def test_clear_noop_when_no_emoji(rm, client):
    await rm.clear()
    client.reactions_remove.assert_not_called()


async def test_add_failure_is_silent(rm, client):
    """Reaction failures are swallowed (best-effort)."""
    client.reactions_add.side_effect = Exception("rate_limited")
    await rm.set("eyes")  # Should not raise
    assert rm.current_emoji == "eyes"


async def test_remove_failure_is_silent(rm, client):
    """Reaction removal failures are swallowed."""
    await rm.set("eyes")
    client.reactions_remove.side_effect = Exception("not_found")
    await rm.set("hammer_and_wrench")  # Should not raise
