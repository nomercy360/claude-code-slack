"""Test ClaudeIntegration facade — per-thread session model."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.claude.facade import ClaudeIntegration
from src.claude.session import InMemorySessionStorage, SessionManager
from src.config.settings import Settings


def _make_mock_response(session_id: str = "new-session-id") -> MagicMock:
    """Create a mock ClaudeResponse with sensible defaults."""
    resp = MagicMock()
    resp.session_id = session_id
    resp.cost = 0.0
    resp.duration_ms = 100
    resp.num_turns = 1
    resp.tools_used = []
    resp.is_error = False
    resp.content = "ok"
    return resp


@pytest.fixture
def config(tmp_path):
    """Create test config."""
    return Settings(
        slack_bot_token="xoxb-test-token",
        slack_app_token="xapp-test-token",
        approved_directory=tmp_path,
        session_timeout_hours=24,
        max_sessions_per_user=5,
    )


@pytest.fixture
def session_manager(config):
    """Create session manager with in-memory storage."""
    storage = InMemorySessionStorage()
    return SessionManager(config, storage)


@pytest.fixture
def facade(config, session_manager):
    """Create facade with mocked SDK manager."""
    sdk_manager = MagicMock()
    integration = ClaudeIntegration(
        config=config,
        sdk_manager=sdk_manager,
        session_manager=session_manager,
    )
    return integration


class TestPerThreadSessions:
    """Each thread gets its own session. No cross-thread auto-resume."""

    async def test_no_session_id_creates_new_session(self, facade):
        """When session_id is None, a new Claude session is created."""
        project = Path("/test/project")

        with patch.object(
            facade, "_execute", return_value=_make_mock_response("sess-abc")
        ):
            result = await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id="U123",
                session_id=None,
            )

        assert result.session_id == "sess-abc"

    async def test_session_id_resumes_existing(self, facade, session_manager):
        """When session_id is provided, the existing session is resumed."""
        from datetime import UTC, datetime

        from src.claude.session import ClaudeSession

        project = Path("/test/project")

        # Seed the session so get_or_create_session can find it
        existing = ClaudeSession(
            session_id="sess-abc",
            user_id="U123",
            project_path=project,
            created_at=datetime.now(UTC),
            last_used=datetime.now(UTC),
        )
        await session_manager.storage.save_session(existing)
        session_manager.active_sessions["sess-abc"] = existing

        with patch.object(
            facade, "_execute", return_value=_make_mock_response("sess-abc")
        ) as mock_exec:
            await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id="U123",
                session_id="sess-abc",
            )

        # Should have been called with session_id for continuation
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["session_id"] == "sess-abc"
        assert call_kwargs["continue_session"] is True

    async def test_resume_failure_falls_back_to_new(self, facade, session_manager):
        """If resuming fails (e.g. session expired), retry as new session."""
        from datetime import UTC, datetime

        from src.claude.session import ClaudeSession

        project = Path("/test/project")

        # Seed session so the facade finds it and attempts to continue
        existing = ClaudeSession(
            session_id="stale-sess",
            user_id="U123",
            project_path=project,
            created_at=datetime.now(UTC),
            last_used=datetime.now(UTC),
        )
        await session_manager.storage.save_session(existing)
        session_manager.active_sessions["stale-sess"] = existing

        call_count = [0]

        async def _side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("session not found")
            return _make_mock_response("fresh-sess")

        with patch.object(facade, "_execute", side_effect=_side_effect):
            result = await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id="U123",
                session_id="stale-sess",
            )

        assert result.session_id == "fresh-sess"
        assert call_count[0] == 2


class TestEmptySessionIdWarning:
    """Verify facade warns when final session_id is empty."""

    async def test_empty_session_id_warning_in_facade(self, facade):
        """When Claude returns no session_id, facade logs a warning."""
        project = Path("/test/project")

        mock_response = _make_mock_response(session_id="")

        with patch.object(facade, "_execute", return_value=mock_response):
            result = await facade.run_command(
                prompt="hello",
                working_directory=project,
                user_id="U456",
                session_id=None,
            )

        assert not result.session_id
