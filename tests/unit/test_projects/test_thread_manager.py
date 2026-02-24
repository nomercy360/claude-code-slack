"""Tests for project-thread manager (Slack channel sync)."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from slack_sdk.errors import SlackApiError

import src.projects.thread_manager as thread_manager_module
from src.projects import (
    ChannelSyncUnavailableError,
    ProjectThreadManager,
    load_project_registry,
)
from src.storage.database import DatabaseManager
from src.storage.repositories import ProjectThreadRepository


@pytest.fixture
async def db_manager():
    """Create test database manager."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        manager = DatabaseManager(f"sqlite:///{db_path}")
        await manager.initialize()
        yield manager
        await manager.close()


def _write_registry(tmp_path: Path, approved: Path, projects: str):
    for project in projects.split(","):
        (approved / project.strip()).mkdir(parents=True, exist_ok=True)

    lines = ["projects:"]
    for project in projects.split(","):
        project = project.strip()
        lines.extend(
            [
                f"  - slug: {project}",
                f"    name: {project.title()}",
                f"    path: {project}",
            ]
        )

    config_file = tmp_path / "projects.yaml"
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_file


def _mock_slack_client():
    """Create a mock Slack AsyncWebClient."""
    client = AsyncMock()
    return client


async def test_sync_channels_idempotent(tmp_path: Path, db_manager) -> None:
    approved = tmp_path / "projects"
    approved.mkdir()

    config_file = _write_registry(tmp_path, approved, "app1")
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)

    client = _mock_slack_client()
    client.chat_postMessage = AsyncMock(
        return_value={"ok": True, "ts": "1234567890.123456"}
    )

    first = await manager.sync_channels(client, channel_id="C001")
    second = await manager.sync_channels(client, channel_id="C001")

    assert first.created == 1
    assert first.reused == 0
    assert second.created == 0
    assert second.reused == 1


async def test_resolve_project_by_mapping(tmp_path: Path, db_manager) -> None:
    approved = tmp_path / "projects"
    approved.mkdir()

    config_file = _write_registry(tmp_path, approved, "app1")
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    await repo.upsert_mapping(
        project_slug="app1",
        channel_id="C001",
        thread_ts="1234567890.123456",
        topic_name="App1",
        is_active=True,
    )

    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)
    project = await manager.resolve_project("C001", "1234567890.123456")

    assert project is not None
    assert project.slug == "app1"


async def test_sync_deactivates_stale_projects(tmp_path: Path, db_manager) -> None:
    approved = tmp_path / "projects"
    approved.mkdir()

    initial_file = _write_registry(tmp_path, approved, "app1,app2")
    initial_registry = load_project_registry(initial_file, approved)

    repo = ProjectThreadRepository(db_manager)
    manager = ProjectThreadManager(
        initial_registry,
        repo,
        sync_action_interval_seconds=0.0,
    )

    client = _mock_slack_client()
    call_count = 0

    async def mock_post_message(**kwargs):
        nonlocal call_count
        call_count += 1
        return {"ok": True, "ts": f"100{call_count}.000000"}

    client.chat_postMessage = AsyncMock(side_effect=mock_post_message)

    await manager.sync_channels(client, channel_id="C001")

    reduced_file = tmp_path / "projects_reduced.yaml"
    reduced_file.write_text(
        "projects:\n" "  - slug: app1\n" "    name: App1\n" "    path: app1\n",
        encoding="utf-8",
    )
    reduced_registry = load_project_registry(reduced_file, approved)
    reduced_manager = ProjectThreadManager(
        reduced_registry,
        repo,
        sync_action_interval_seconds=0.0,
    )

    result = await reduced_manager.sync_channels(client, channel_id="C001")
    mappings = await repo.list_by_channel("C001", active_only=False)

    app2 = [m for m in mappings if m.project_slug == "app2"]
    assert result.deactivated == 1
    assert app2
    assert app2[0].is_active is False


async def test_sync_channels_api_error_counted_as_failure(
    tmp_path: Path, db_manager
) -> None:
    """SlackApiError during sync should be counted as a failure."""
    approved = tmp_path / "projects"
    approved.mkdir()

    config_file = _write_registry(tmp_path, approved, "app1")
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)

    client = _mock_slack_client()
    error_response = MagicMock()
    error_response.status_code = 429
    error_response.__getitem__ = lambda self, key: {"ok": False}.get(key)
    client.chat_postMessage = AsyncMock(
        side_effect=SlackApiError("rate_limited", response=error_response)
    )

    result = await manager.sync_channels(client, channel_id="C001")

    assert result.created == 0
    assert result.failed == 1


async def test_sync_renames_existing_mapping(tmp_path: Path, db_manager) -> None:
    """When project name changes, manager renames mapping."""
    approved = tmp_path / "projects"
    approved.mkdir()
    (approved / "app1").mkdir()

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        "projects:\n" "  - slug: app1\n" "    name: Pretty Name\n" "    path: app1\n",
        encoding="utf-8",
    )
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    await repo.upsert_mapping(
        project_slug="app1",
        channel_id="C042",
        thread_ts="1001.000000",
        topic_name="Old Name",
        is_active=True,
    )

    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)
    client = _mock_slack_client()

    result = await manager.sync_channels(client, channel_id="C042")
    mapping = await repo.get_by_channel_project("C042", "app1")

    assert result.reused == 1
    assert result.renamed == 1
    assert result.failed == 0
    assert mapping is not None
    assert mapping.topic_name == "Pretty Name"


async def test_sync_skips_rename_when_name_matches(tmp_path: Path, db_manager) -> None:
    """When DB name already matches, sync should not rename."""
    approved = tmp_path / "projects"
    approved.mkdir()
    (approved / "app1").mkdir()

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        "projects:\n" "  - slug: app1\n" "    name: Pretty Name\n" "    path: app1\n",
        encoding="utf-8",
    )
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    await repo.upsert_mapping(
        project_slug="app1",
        channel_id="C042",
        thread_ts="1001.000000",
        topic_name="Pretty Name",
        is_active=True,
    )

    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)
    client = _mock_slack_client()

    result = await manager.sync_channels(client, channel_id="C042")

    assert result.reused == 1
    assert result.renamed == 0


async def test_sync_create_sends_bootstrap_message(tmp_path: Path, db_manager) -> None:
    """Creating a new channel mapping posts an initial message."""
    approved = tmp_path / "projects"
    approved.mkdir()

    config_file = _write_registry(tmp_path, approved, "app1")
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)

    client = _mock_slack_client()
    client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "101.000000"})

    result = await manager.sync_channels(client, channel_id="C042")

    assert result.created == 1
    client.chat_postMessage.assert_awaited_once()
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C042"


async def test_sync_topics_alias_calls_sync_channels(
    tmp_path: Path, db_manager
) -> None:
    """sync_topics is a backward-compat alias for sync_channels."""
    approved = tmp_path / "projects"
    approved.mkdir()

    config_file = _write_registry(tmp_path, approved, "app1")
    registry = load_project_registry(config_file, approved)

    repo = ProjectThreadRepository(db_manager)
    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)

    client = _mock_slack_client()
    client.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "101.000000"})

    result = await manager.sync_topics(client, chat_id="C042")

    assert result.created == 1
