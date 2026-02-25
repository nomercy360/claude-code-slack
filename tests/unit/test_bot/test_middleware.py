"""Tests for Slack Bolt middleware handler stop behavior and bot-originated guards.

Verifies that when middleware rejects a request (auth failure, security
violation, rate limit exceeded), the next() callback is NOT called to
prevent subsequent middleware and handlers from processing the event.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.core import ClaudeCodeBot
from src.bot.middleware.rate_limit import estimate_message_cost
from src.config import create_test_config
from src.config.settings import Settings


@pytest.fixture
def mock_settings():
    """Minimal Settings mock for ClaudeCodeBot."""
    settings = MagicMock(spec=Settings)
    settings.slack_bot_token_str = "xoxb-test-token"
    settings.slack_signing_secret_str = "test-signing-secret"
    settings.slack_app_token_str = "xapp-test-token"
    settings.enable_mcp = False
    settings.enable_api_server = False
    settings.enable_scheduler = False
    settings.approved_directory = "/tmp/test"
    return settings


@pytest.fixture
def bot(mock_settings):
    """Create a ClaudeCodeBot instance with mock dependencies."""
    deps = {
        "auth_manager": MagicMock(),
        "security_validator": MagicMock(),
        "rate_limiter": MagicMock(),
        "audit_logger": MagicMock(),
        "storage": MagicMock(),
        "claude_integration": MagicMock(),
    }
    return ClaudeCodeBot(mock_settings, deps)


def _make_slack_body(user_id="U999999", text="hello", bot_id=None):
    """Create a mock Slack event body."""
    event = {"user": user_id, "text": text, "channel": "C123"}
    if bot_id:
        event["bot_id"] = bot_id
    return {"event": event}


class TestMiddlewareBlocksSubsequentHandlers:
    """Verify middleware rejection prevents next() from being called."""

    async def test_auth_rejection_does_not_call_next(self, bot):
        """Auth middleware must not call next() on rejection."""

        async def rejecting_auth(handler, body, data):
            # Middleware does NOT call handler (next) -> rejection
            return

        wrapper = bot._create_middleware_handler(rejecting_auth)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body()
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is False

    async def test_security_rejection_does_not_call_next(self, bot):
        """Security middleware must not call next() on dangerous input."""

        async def rejecting_security(handler, body, data):
            return

        wrapper = bot._create_middleware_handler(rejecting_security)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body()
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is False

    async def test_rate_limit_rejection_does_not_call_next(self, bot):
        """Rate limit middleware must not call next()."""

        async def rejecting_rate_limit(handler, body, data):
            return

        wrapper = bot._create_middleware_handler(rejecting_rate_limit)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body()
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is False

    async def test_allowed_request_calls_next(self, bot):
        """Middleware that calls the handler must result in next() being called."""

        async def allowing_middleware(handler, body, data):
            return await handler()

        wrapper = bot._create_middleware_handler(allowing_middleware)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body()
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is True

    async def test_real_auth_middleware_rejection(self, bot):
        """Integration test: actual auth_middleware rejects unauthorized user."""
        from src.bot.middleware.auth import auth_middleware

        auth_manager = MagicMock()
        auth_manager.is_authenticated.return_value = False
        auth_manager.authenticate_user = AsyncMock(return_value=False)
        bot.deps["auth_manager"] = auth_manager

        audit_logger = AsyncMock()
        bot.deps["audit_logger"] = audit_logger

        wrapper = bot._create_middleware_handler(auth_middleware)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body(user_id="U999999")
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is False

    async def test_real_auth_middleware_allows_authenticated_user(self, bot):
        """Integration test: auth_middleware allows an authenticated user through."""
        from src.bot.middleware.auth import auth_middleware

        auth_manager = MagicMock()
        auth_manager.is_authenticated.return_value = True
        auth_manager.refresh_session.return_value = True
        auth_manager.get_session.return_value = MagicMock(auth_provider="whitelist")
        bot.deps["auth_manager"] = auth_manager

        wrapper = bot._create_middleware_handler(auth_middleware)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body(user_id="U123456")
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is True

    async def test_real_rate_limit_middleware_rejection(self, bot):
        """Integration test: rate_limit_middleware rejects when limit exceeded."""
        from src.bot.middleware.rate_limit import rate_limit_middleware

        rate_limiter = MagicMock()
        rate_limiter.check_rate_limit = AsyncMock(
            return_value=(False, "Rate limit exceeded. Try again in 30s.")
        )
        bot.deps["rate_limiter"] = rate_limiter

        audit_logger = AsyncMock()
        bot.deps["audit_logger"] = audit_logger

        wrapper = bot._create_middleware_handler(rate_limit_middleware)

        next_called = False

        async def mock_next():
            nonlocal next_called
            next_called = True

        body = _make_slack_body(user_id="U999999")
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)
        assert next_called is False

    async def test_dependencies_injected_before_middleware_runs(self, bot):
        """Verify dependencies are available in data when middleware executes."""
        captured_data = {}

        async def capturing_middleware(handler, body, data):
            captured_data.update(data)
            return await handler()

        wrapper = bot._create_middleware_handler(capturing_middleware)

        async def mock_next():
            pass

        body = _make_slack_body()
        context = {}
        client = AsyncMock()

        await wrapper(body=body, next=mock_next, context=context, client=client)

        assert "auth_manager" in captured_data
        assert "security_validator" in captured_data
        assert "rate_limiter" in captured_data
        assert "settings" in captured_data


@pytest.mark.asyncio
async def test_middleware_wrapper_stops_bot_originated_events() -> None:
    """Middleware wrapper should skip events sent by bots."""
    settings = create_test_config()
    claude_bot = ClaudeCodeBot(settings, {})

    middleware_called = False

    async def fake_middleware(handler, body, data):
        nonlocal middleware_called
        middleware_called = True
        return await handler()

    wrapper = claude_bot._create_middleware_handler(fake_middleware)

    next_called = False

    async def mock_next():
        nonlocal next_called
        next_called = True

    body = _make_slack_body(user_id="U123", bot_id="B123")
    context = {}
    client = AsyncMock()

    await wrapper(body=body, next=mock_next, context=context, client=client)

    assert middleware_called is False
    assert next_called is False


@pytest.mark.asyncio
async def test_middleware_wrapper_runs_for_user_events() -> None:
    """Middleware wrapper should execute middleware for user events."""
    settings = create_test_config()
    claude_bot = ClaudeCodeBot(settings, {})

    middleware_called = False

    async def allowing_middleware(handler, body, data):
        nonlocal middleware_called
        middleware_called = True
        return await handler()

    wrapper = claude_bot._create_middleware_handler(allowing_middleware)

    next_called = False

    async def mock_next():
        nonlocal next_called
        next_called = True

    body = _make_slack_body(user_id="U456")
    context = {}
    client = AsyncMock()

    await wrapper(body=body, next=mock_next, context=context, client=client)

    assert middleware_called is True


def test_estimate_message_cost_handles_empty_text() -> None:
    """Cost estimation should not fail on events without text."""
    body = {"event": {"user": "U123", "channel": "C123"}}

    cost = estimate_message_cost(body)

    assert cost >= 0.01
