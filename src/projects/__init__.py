"""Project registry and Slack channel management."""

from .registry import ProjectDefinition, ProjectRegistry, load_project_registry
from .thread_manager import (
    ChannelSyncUnavailableError,
    PrivateTopicsUnavailableError,
    ProjectThreadManager,
)

__all__ = [
    "ProjectDefinition",
    "ProjectRegistry",
    "load_project_registry",
    "ProjectThreadManager",
    "ChannelSyncUnavailableError",
    "PrivateTopicsUnavailableError",
]
