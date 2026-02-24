"""Tests for SlackInfoCache."""

from unittest.mock import AsyncMock

import pytest

from src.bot.utils.cache import SlackInfoCache, infer_channel_type


class TestInferChannelType:
    def test_dm(self):
        assert infer_channel_type("D012ABC") == "im"

    def test_channel(self):
        assert infer_channel_type("C012ABC") == "channel"

    def test_group(self):
        assert infer_channel_type("G012ABC") == "group"

    def test_unknown_prefix(self):
        assert infer_channel_type("X012ABC") == "channel"


class TestSlackInfoCache:
    @pytest.fixture
    def cache(self):
        return SlackInfoCache(ttl_seconds=300)

    @pytest.fixture
    def client(self):
        c = AsyncMock()
        c.users_info.return_value = {
            "user": {"profile": {"display_name": "Alice", "real_name": "Alice Smith"}}
        }
        c.conversations_info.return_value = {
            "channel": {"name": "general", "is_im": False, "topic": {"value": "chat"}}
        }
        return c

    async def test_get_user_name_fetches_and_caches(self, cache, client):
        name = await cache.get_user_name(client, "U123")
        assert name == "Alice"
        client.users_info.assert_called_once_with(user="U123")

        # Second call uses cache
        name2 = await cache.get_user_name(client, "U123")
        assert name2 == "Alice"
        assert client.users_info.call_count == 1

    async def test_get_user_name_falls_back_to_real_name(self, cache, client):
        client.users_info.return_value = {
            "user": {"profile": {"display_name": "", "real_name": "Bob Jones"}}
        }
        name = await cache.get_user_name(client, "U456")
        assert name == "Bob Jones"

    async def test_get_user_name_returns_none_on_error(self, cache, client):
        client.users_info.side_effect = Exception("api error")
        name = await cache.get_user_name(client, "U789")
        assert name is None

    async def test_get_channel_info_fetches_and_caches(self, cache, client):
        info = await cache.get_channel_info(client, "C123")
        assert info["name"] == "general"
        assert info["type"] == "channel"
        assert info["topic"] == "chat"
        client.conversations_info.assert_called_once_with(channel="C123")

        # Cached
        info2 = await cache.get_channel_info(client, "C123")
        assert info2["name"] == "general"
        assert client.conversations_info.call_count == 1

    async def test_get_channel_info_im(self, cache, client):
        client.conversations_info.return_value = {
            "channel": {"name": None, "is_im": True, "topic": None}
        }
        info = await cache.get_channel_info(client, "D123")
        assert info["type"] == "im"

    async def test_get_channel_info_fallback_on_error(self, cache, client):
        client.conversations_info.side_effect = Exception("api error")
        info = await cache.get_channel_info(client, "D999")
        assert info["type"] == "im"  # Inferred from D prefix
