"""Slack channel synchronization and project resolution.

Maps projects to Slack channels. In "group" mode, creates/manages
channels for each project. In "private" mode, uses a single channel
with thread-per-project routing.
"""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable, Optional, TypeVar

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from ..storage.models import ProjectThreadModel
from ..storage.repositories import ProjectThreadRepository
from ..utils.constants import DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS
from .registry import ProjectDefinition, ProjectRegistry

logger = structlog.get_logger()
T = TypeVar("T")


class ChannelSyncUnavailableError(RuntimeError):
    """Raised when channel sync operations are unavailable."""


# Keep old name as alias for backward compatibility in __init__.py
PrivateTopicsUnavailableError = ChannelSyncUnavailableError


@dataclass
class ChannelSyncResult:
    """Summary of a synchronization run."""

    created: int = 0
    reused: int = 0
    renamed: int = 0
    failed: int = 0
    deactivated: int = 0
    archived: int = 0
    unarchived: int = 0


# Keep old name as alias
TopicSyncResult = ChannelSyncResult


class ProjectThreadManager:
    """Maintains mapping between projects and Slack channels."""

    def __init__(
        self,
        registry: ProjectRegistry,
        repository: ProjectThreadRepository,
        sync_action_interval_seconds: float = (
            DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS
        ),
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.sync_action_interval_seconds = max(0.0, sync_action_interval_seconds)
        self._sync_api_lock = asyncio.Lock()
        self._last_sync_api_call_at: Optional[float] = None

    async def sync_channels(
        self, client: AsyncWebClient, channel_id: str
    ) -> ChannelSyncResult:
        """Create/reconcile Slack channels for all enabled projects.

        In group mode, creates a new channel per project.
        In other modes, posts a thread-starter message per project in the
        given channel.

        For simplicity, this implementation uses thread_ts as the channel
        mapping key when working within a single channel.
        """
        result = ChannelSyncResult()

        enabled = self.registry.list_enabled()
        active_slugs = [project.slug for project in enabled]

        for project in enabled:
            try:
                existing = await self.repository.get_by_channel_project(
                    channel_id,
                    project.slug,
                )

                if existing:
                    handled = await self._sync_existing_mapping(
                        client=client,
                        project=project,
                        mapping=existing,
                        result=result,
                    )
                    if handled:
                        continue

                await self._create_and_map_channel(
                    client=client,
                    project=project,
                    channel_id=channel_id,
                    result=result,
                )

            except SlackApiError as e:
                result.failed += 1
                logger.error(
                    "Failed to sync project channel",
                    project_slug=project.slug,
                    channel_id=channel_id,
                    error=str(e),
                )
            except Exception as e:
                result.failed += 1
                logger.error(
                    "Failed to sync project channel",
                    project_slug=project.slug,
                    channel_id=channel_id,
                    error=str(e),
                )

        # Deactivate stale mappings
        stale_mappings = await self.repository.list_stale_active_mappings(
            channel_id=channel_id,
            active_project_slugs=active_slugs,
        )
        for stale in stale_mappings:
            await self.repository.set_active(
                channel_id=stale.channel_id,
                project_slug=stale.project_slug,
                is_active=False,
            )
            result.deactivated += 1

        return result

    # Keep old method name as alias
    async def sync_topics(
        self, client: AsyncWebClient, chat_id: str
    ) -> ChannelSyncResult:
        """Alias for sync_channels (backward compat)."""
        return await self.sync_channels(client, chat_id)

    async def _call_sync_api(
        self,
        call: Callable[[], Awaitable[T]],
    ) -> T:
        """Call Slack sync API with pacing."""
        async with self._sync_api_lock:
            await self._wait_for_sync_interval()
            self._last_sync_api_call_at = monotonic()
            return await call()

    async def _wait_for_sync_interval(self) -> None:
        """Wait until minimum sync action interval is satisfied."""
        if (
            self.sync_action_interval_seconds <= 0
            or self._last_sync_api_call_at is None
        ):
            return

        elapsed = monotonic() - self._last_sync_api_call_at
        wait_time = self.sync_action_interval_seconds - elapsed
        if wait_time > 0:
            await asyncio.sleep(wait_time)

    async def _sync_existing_mapping(
        self,
        client: AsyncWebClient,
        project: ProjectDefinition,
        mapping: ProjectThreadModel,
        result: ChannelSyncResult,
    ) -> bool:
        """Sync an existing mapping. Returns True if handled without recreate."""
        if not mapping.is_active:
            # Try to reactivate
            await self.repository.set_active(
                channel_id=mapping.channel_id,
                project_slug=mapping.project_slug,
                is_active=True,
            )
            result.unarchived += 1

        # Update topic name if changed
        if mapping.topic_name != project.name:
            await self.repository.upsert_mapping(
                project_slug=project.slug,
                channel_id=mapping.channel_id,
                thread_ts=mapping.thread_ts,
                topic_name=project.name,
                is_active=True,
            )
            result.renamed += 1
        else:
            await self.repository.upsert_mapping(
                project_slug=project.slug,
                channel_id=mapping.channel_id,
                thread_ts=mapping.thread_ts,
                topic_name=mapping.topic_name,
                is_active=True,
            )

        result.reused += 1
        return True

    async def _create_and_map_channel(
        self,
        client: AsyncWebClient,
        project: ProjectDefinition,
        channel_id: str,
        result: ChannelSyncResult,
    ) -> None:
        """Create a thread-starter message and persist mapping."""
        # Post a thread-starter message in the channel
        response = await self._call_sync_api(
            lambda: client.chat_postMessage(
                channel=channel_id,
                text=f"*{project.name}*\n\nThis thread is for the `{project.slug}` project.",
            ),
        )

        thread_ts = response["ts"]

        await self.repository.upsert_mapping(
            project_slug=project.slug,
            channel_id=channel_id,
            thread_ts=thread_ts,
            topic_name=project.name,
            is_active=True,
        )
        result.created += 1

    async def resolve_project(
        self, channel_id: str, thread_ts: str
    ) -> Optional[ProjectDefinition]:
        """Resolve mapped project for channel+thread."""
        mapping = await self.repository.get_by_channel_thread(channel_id, thread_ts)
        if not mapping:
            return None

        project = self.registry.get_by_slug(mapping.project_slug)
        if not project or not project.enabled:
            return None

        return project

    @staticmethod
    def guidance_message(mode: str = "group") -> str:
        """Guidance text for strict routing rejections."""
        context_label = (
            "a mapped project thread in this channel"
            if mode == "private"
            else "a mapped project channel"
        )
        return (
            "*Project Thread Required*\n\n"
            "This bot is configured for strict project threads.\n"
            f"Please send commands in {context_label}.\n\n"
            "If threads are missing or stale, run `/sync_threads`."
        )

    @staticmethod
    def private_topics_unavailable_message() -> str:
        """User guidance when channel sync is unavailable."""
        return (
            "*Channel Sync Unavailable*\n\n"
            "Could not sync project channels. "
            "Check bot permissions and try again with `/sync_threads`."
        )
