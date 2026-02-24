"""Tests for project thread routing in Slack bot.

These tests verify the basic project thread manager integration
points that remain relevant.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import create_test_config
from src.projects import ProjectThreadManager, load_project_registry
from src.storage.repositories import ProjectThreadRepository


@pytest.fixture
def thread_settings(tmp_path: Path):
    approved = tmp_path / "projects"
    approved.mkdir()
    project_root = approved / "project_a"
    project_root.mkdir()

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        "projects:\n"
        "  - slug: project_a\n"
        "    name: Project A\n"
        "    path: project_a\n",
        encoding="utf-8",
    )

    settings = create_test_config(
        approved_directory=str(approved),
        enable_project_threads=True,
        project_threads_mode="private",
        projects_config_path=str(config_file),
    )
    return settings, project_root, config_file, approved


def test_project_registry_loads_from_yaml(thread_settings):
    """Project registry loads projects from YAML config."""
    settings, project_root, config_file, approved = thread_settings

    registry = load_project_registry(config_file, approved)
    projects = registry.list_enabled()

    assert len(projects) == 1
    assert projects[0].slug == "project_a"
    assert projects[0].name == "Project A"


def test_project_thread_manager_initialization(thread_settings):
    """ProjectThreadManager initializes with registry and repository."""
    settings, project_root, config_file, approved = thread_settings

    registry = load_project_registry(config_file, approved)
    repo = MagicMock(spec=ProjectThreadRepository)

    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)

    assert manager.registry is registry
    assert manager.repository is repo


async def test_resolve_project_returns_none_for_unknown_thread(thread_settings):
    """resolve_project returns None for unknown channel+thread combination."""
    settings, project_root, config_file, approved = thread_settings

    registry = load_project_registry(config_file, approved)
    repo = AsyncMock(spec=ProjectThreadRepository)
    repo.get_by_channel_thread = AsyncMock(return_value=None)

    manager = ProjectThreadManager(registry, repo, sync_action_interval_seconds=0.0)
    result = await manager.resolve_project("C123", "1234567890.123456")

    assert result is None
