"""Tests for the MessageOrchestrator."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.orchestrator import MessageOrchestrator, _redact_secrets
from src.config import create_test_config


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def agentic_settings(tmp_dir):
    return create_test_config(approved_directory=str(tmp_dir))


@pytest.fixture
def deps():
    return {
        "claude_integration": MagicMock(),
        "storage": MagicMock(),
        "security_validator": MagicMock(),
        "rate_limiter": MagicMock(),
        "audit_logger": MagicMock(),
    }


def test_register_handlers(agentic_settings, deps):
    """Handlers are registered on the Slack Bolt app."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()

    orchestrator.register_handlers(app)

    # Slash commands
    app.command.assert_any_call("/claude-start")
    app.command.assert_any_call("/claude-status")
    app.command.assert_any_call("/claude-verbose")
    app.command.assert_any_call("/claude-repo")

    # Message event
    app.event.assert_any_call("message")

    # Action handler for repo selection buttons
    app.action.assert_called()


async def test_agentic_start_sends_welcome(agentic_settings, deps):
    """Agentic /start sends a brief welcome message."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    ack = AsyncMock()
    client = AsyncMock()
    command = {
        "user_id": "U123",
        "channel_id": "C456",
        "text": "",
    }

    await orchestrator.agentic_start(ack=ack, command=command, client=client)

    ack.assert_called_once()
    client.chat_postMessage.assert_called_once()
    call_kwargs = client.chat_postMessage.call_args.kwargs
    assert call_kwargs["channel"] == "C456"
    assert "<@U123>" in call_kwargs["text"]


async def test_agentic_status_compact(agentic_settings, deps):
    """Agentic /status returns compact one-line status."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    ack = AsyncMock()
    client = AsyncMock()
    command = {
        "user_id": "U123",
        "channel_id": "C456",
        "text": "",
    }

    await orchestrator.agentic_status(ack=ack, command=command, client=client)

    ack.assert_called_once()
    call_kwargs = client.chat_postMessage.call_args.kwargs
    text = call_kwargs["text"]
    assert "Session: none" in text


async def test_agentic_verbose_shows_current(agentic_settings, deps):
    """Agentic /verbose with no args shows current level."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    ack = AsyncMock()
    client = AsyncMock()
    command = {
        "user_id": "U123",
        "channel_id": "C456",
        "text": "",
    }

    await orchestrator.agentic_verbose(ack=ack, command=command, client=client)

    ack.assert_called_once()
    text = client.chat_postMessage.call_args.kwargs["text"]
    assert "Verbosity" in text


async def test_agentic_verbose_sets_level(agentic_settings, deps):
    """Agentic /verbose 2 sets verbose level."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    ack = AsyncMock()
    client = AsyncMock()
    command = {
        "user_id": "U123",
        "channel_id": "C456",
        "text": "2",
    }

    await orchestrator.agentic_verbose(ack=ack, command=command, client=client)

    state = orchestrator._get_user_state("U123")
    assert state["verbose_level"] == 2


async def test_handle_message_event_ignores_bot(agentic_settings, deps):
    """Bot messages (subtype set) are ignored."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    say = AsyncMock()
    client = AsyncMock()
    event = {
        "subtype": "bot_message",
        "text": "hi",
        "user": "U123",
        "channel": "C456",
        "ts": "1234567890.123456",
    }

    await orchestrator.handle_message_event(event=event, say=say, client=client)

    # Should not call Claude
    client.chat_postMessage.assert_not_called()


async def test_handle_message_event_ignores_no_user(agentic_settings, deps):
    """Messages without a user are ignored."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    say = AsyncMock()
    client = AsyncMock()
    event = {
        "text": "hi",
        "channel": "C456",
        "ts": "1234567890.123456",
    }

    await orchestrator.handle_message_event(event=event, say=say, client=client)

    client.chat_postMessage.assert_not_called()


# --- _redact_secrets / _summarize_tool_input tests ---


class TestRedactSecrets:
    """Ensure sensitive substrings are redacted from Bash command summaries."""

    def test_safe_command_unchanged(self):
        assert (
            _redact_secrets("poetry run pytest tests/ -v")
            == "poetry run pytest tests/ -v"
        )

    def test_anthropic_api_key_redacted(self):
        key = "sk-ant-api03-abc123def456ghi789jkl012mno345"
        cmd = f"ANTHROPIC_API_KEY={key}"
        result = _redact_secrets(cmd)
        assert key not in result
        assert "***" in result

    def test_sk_key_redacted(self):
        cmd = "curl -H 'Authorization: Bearer sk-1234567890abcdefghijklmnop'"
        result = _redact_secrets(cmd)
        assert "sk-1234567890abcdefghijklmnop" not in result
        assert "***" in result

    def test_github_pat_redacted(self):
        cmd = "git clone https://ghp_abcdefghijklmnop1234@github.com/user/repo"
        result = _redact_secrets(cmd)
        assert "ghp_abcdefghijklmnop1234" not in result
        assert "***" in result

    def test_aws_key_redacted(self):
        cmd = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = _redact_secrets(cmd)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "***" in result

    def test_flag_token_redacted(self):
        cmd = "mycli --token=supersecretvalue123"
        result = _redact_secrets(cmd)
        assert "supersecretvalue123" not in result
        assert "--token=" in result or "--token" in result

    def test_password_env_redacted(self):
        cmd = "PASSWORD=MyS3cretP@ss! ./run.sh"
        result = _redact_secrets(cmd)
        assert "MyS3cretP@ss!" not in result
        assert "***" in result

    def test_bearer_token_redacted(self):
        cmd = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig'"
        result = _redact_secrets(cmd)
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in result

    def test_connection_string_redacted(self):
        cmd = "psql postgresql://admin:secret_password@db.host:5432/mydb"
        result = _redact_secrets(cmd)
        assert "secret_password" not in result

    def test_summarize_tool_input_bash_redacts(self, agentic_settings, deps):
        """_summarize_tool_input applies redaction to Bash commands."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Bash",
            {"command": "curl --token=mysupersecrettoken123 https://api.example.com"},
        )
        assert "mysupersecrettoken123" not in result
        assert "***" in result

    def test_summarize_tool_input_non_bash_unchanged(self, agentic_settings, deps):
        """Non-Bash tools don't go through redaction."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Read", {"file_path": "/home/user/.env"}
        )
        assert result == ".env"


# --- Stream callback tests ---


class TestStreamCallback:
    """Verify stream callback behavior."""

    def test_make_stream_callback_returns_none_when_quiet(self, agentic_settings, deps):
        """Verbose level 0 returns None callback."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        callback = orchestrator._make_stream_callback(
            verbose_level=0,
            client=AsyncMock(),
            channel_id="C123",
            progress_ts="1234567890.123456",
            tool_log=[],
            start_time=0.0,
        )
        assert callback is None

    def test_make_stream_callback_returns_callable(self, agentic_settings, deps):
        """Verbose level 1+ returns a callable callback."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        callback = orchestrator._make_stream_callback(
            verbose_level=1,
            client=AsyncMock(),
            channel_id="C123",
            progress_ts="1234567890.123456",
            tool_log=[],
            start_time=0.0,
        )
        assert callback is not None
        assert callable(callback)

    def test_make_stream_callback_returns_callable_when_reactions_provided(
        self, agentic_settings, deps
    ):
        """Even verbose 0 returns a callback when reactions are provided."""
        from src.bot.utils.reactions import ReactionManager

        orchestrator = MessageOrchestrator(agentic_settings, deps)
        rm = ReactionManager(AsyncMock(), "C123", "1234.5678")
        callback = orchestrator._make_stream_callback(
            verbose_level=0,
            client=AsyncMock(),
            channel_id="C123",
            progress_ts="1234567890.123456",
            tool_log=[],
            start_time=0.0,
            reactions=rm,
        )
        assert callback is not None


# --- Mention gating tests ---


class TestMentionGating:
    """Verify mention gating in channels."""

    @pytest.fixture
    def async_deps(self):
        """Dependencies with AsyncMock for async methods."""
        rate_limiter = MagicMock()
        rate_limiter.check_rate_limit = AsyncMock(return_value=(True, ""))
        audit_logger = MagicMock()
        audit_logger.log_command = AsyncMock()

        claude_integration = AsyncMock()
        response = MagicMock()
        response.session_id = "sess-1"
        response.content = "Hello!"
        claude_integration.run_command = AsyncMock(return_value=response)

        storage = MagicMock()
        storage.save_claude_interaction = AsyncMock()

        return {
            "claude_integration": claude_integration,
            "storage": storage,
            "security_validator": MagicMock(),
            "rate_limiter": rate_limiter,
            "audit_logger": audit_logger,
        }

    async def test_dm_always_responds(self, agentic_settings, async_deps):
        """DMs (channel_type=im) are always processed."""
        orchestrator = MessageOrchestrator(
            agentic_settings, async_deps, bot_user_id="UBOT"
        )
        say = AsyncMock()
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1234.0001"}
        client.conversations_replies.return_value = {"messages": []}

        event = {
            "text": "hello",
            "user": "U123",
            "channel": "D456",
            "channel_type": "im",
            "ts": "9999.0001",
        }

        await orchestrator.handle_message_event(event=event, say=say, client=client)
        client.chat_postMessage.assert_called()

    async def test_channel_without_mention_ignored(self, agentic_settings, async_deps):
        """Channel messages without @mention are ignored."""
        orchestrator = MessageOrchestrator(
            agentic_settings, async_deps, bot_user_id="UBOT"
        )
        say = AsyncMock()
        client = AsyncMock()

        event = {
            "text": "hello everyone",
            "user": "U123",
            "channel": "C456",
            "channel_type": "channel",
            "ts": "9999.0002",
        }

        await orchestrator.handle_message_event(event=event, say=say, client=client)
        client.chat_postMessage.assert_not_called()

    async def test_channel_with_mention_responds(self, agentic_settings, async_deps):
        """Channel messages with @mention are processed."""
        orchestrator = MessageOrchestrator(
            agentic_settings, async_deps, bot_user_id="UBOT"
        )
        say = AsyncMock()
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1234.0002"}
        client.conversations_replies.return_value = {"messages": []}

        event = {
            "text": "<@UBOT> hello",
            "user": "U123",
            "channel": "C456",
            "channel_type": "channel",
            "ts": "9999.0003",
        }

        await orchestrator.handle_message_event(event=event, say=say, client=client)
        client.chat_postMessage.assert_called()

    async def test_dedup_prevents_double_processing(self, agentic_settings, async_deps):
        """Same event ts is not processed twice."""
        orchestrator = MessageOrchestrator(
            agentic_settings, async_deps, bot_user_id="UBOT"
        )
        say = AsyncMock()
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1234.0003"}
        client.conversations_replies.return_value = {"messages": []}

        event = {
            "text": "hello",
            "user": "U123",
            "channel": "D456",
            "channel_type": "im",
            "ts": "9999.0004",
        }

        await orchestrator.handle_message_event(event=event, say=say, client=client)
        call_count = client.chat_postMessage.call_count

        # Second call with same ts should be deduped
        await orchestrator.handle_message_event(event=event, say=say, client=client)
        assert client.chat_postMessage.call_count == call_count
