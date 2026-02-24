"""Emoji reaction status indicators for Slack messages.

Reacts on the user's original message to show processing state:
  eyes → hammer_and_wrench → white_check_mark / x
"""

from typing import Any, Optional

import structlog

logger = structlog.get_logger()


class ReactionManager:
    """Manage emoji reactions on a Slack message as status indicators."""

    def __init__(self, client: Any, channel_id: str, message_ts: str) -> None:
        self.client = client
        self.channel_id = channel_id
        self.message_ts = message_ts
        self.current_emoji: Optional[str] = None

    async def set(self, emoji: str) -> None:
        """Swap current reaction to a new emoji."""
        if emoji == self.current_emoji:
            return
        old = self.current_emoji
        # Add new first so there's no gap
        await self._add(emoji)
        self.current_emoji = emoji
        # Remove old after new is set
        if old:
            await self._remove(old)

    async def clear(self) -> None:
        """Remove current reaction."""
        if self.current_emoji:
            await self._remove(self.current_emoji)
            self.current_emoji = None

    async def _add(self, emoji: str) -> None:
        try:
            await self.client.reactions_add(
                channel=self.channel_id,
                timestamp=self.message_ts,
                name=emoji,
            )
        except Exception as e:
            logger.debug("Failed to add reaction", emoji=emoji, error=str(e))

    async def _remove(self, emoji: str) -> None:
        try:
            await self.client.reactions_remove(
                channel=self.channel_id,
                timestamp=self.message_ts,
                name=emoji,
            )
        except Exception as e:
            logger.debug("Failed to remove reaction", emoji=emoji, error=str(e))
