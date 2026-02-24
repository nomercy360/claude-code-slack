"""Tests for bot core initialization."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.core import ClaudeCodeBot
from src.config import create_test_config


@pytest.mark.asyncio
async def test_initialize_is_idempotent():
    """Repeated initialize calls should not recreate the app."""
    settings = create_test_config()
    deps = {
        "storage": MagicMock(),
        "security": MagicMock(),
    }
    bot = ClaudeCodeBot(settings, deps)

    with patch("src.bot.core.AsyncApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app

        await bot.initialize()
        first_app = bot.app

        await bot.initialize()
        second_app = bot.app

        # Should be the same app instance (idempotent)
        assert first_app is second_app
        mock_app_cls.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_creates_app():
    """Initialize should create a Slack Bolt AsyncApp."""
    settings = create_test_config()
    deps = {}
    bot = ClaudeCodeBot(settings, deps)

    with patch("src.bot.core.AsyncApp") as mock_app_cls:
        mock_app = MagicMock()
        mock_app.middleware = MagicMock()
        mock_app_cls.return_value = mock_app

        await bot.initialize()

        assert bot.app is not None
        mock_app_cls.assert_called_once()
