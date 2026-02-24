"""TTL cache for Slack user and channel metadata.

Avoids redundant users.info and conversations.info API calls.
"""

import time
from typing import Any, Dict, Optional, Tuple

import structlog

logger = structlog.get_logger()


def infer_channel_type(channel_id: str) -> str:
    """Infer Slack channel type from ID prefix (no API call).

    D → im (direct message)
    C → channel (public)
    G → group (private channel or legacy group)
    """
    if channel_id.startswith("D"):
        return "im"
    if channel_id.startswith("C"):
        return "channel"
    if channel_id.startswith("G"):
        return "group"
    return "channel"


class SlackInfoCache:
    """TTL cache for Slack user and channel metadata."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._users: Dict[str, Tuple[float, dict]] = {}
        self._channels: Dict[str, Tuple[float, dict]] = {}
        self._ttl = ttl_seconds

    def _is_fresh(self, entry: Tuple[float, dict]) -> bool:
        return (time.time() - entry[0]) < self._ttl

    async def get_user_name(self, client: Any, user_id: str) -> Optional[str]:
        """Get user display name, cached."""
        cached = self._users.get(user_id)
        if cached and self._is_fresh(cached):
            return cached[1].get("name")

        try:
            info = await client.users_info(user=user_id)
            profile = info["user"]["profile"]
            name = profile.get("display_name") or profile.get("real_name")
            self._users[user_id] = (time.time(), {"name": name})
            return name
        except Exception as e:
            logger.debug("Failed to fetch user info", user_id=user_id, error=str(e))
            return None

    async def get_channel_info(self, client: Any, channel_id: str) -> dict:
        """Get channel metadata, cached."""
        cached = self._channels.get(channel_id)
        if cached and self._is_fresh(cached):
            return cached[1]

        try:
            info = await client.conversations_info(channel=channel_id)
            ch = info["channel"]
            ch_type = "im" if ch.get("is_im") else "channel"
            entry = {
                "name": ch.get("name"),
                "type": ch_type,
                "topic": (ch.get("topic") or {}).get("value"),
            }
            self._channels[channel_id] = (time.time(), entry)
            return entry
        except Exception as e:
            logger.debug(
                "Failed to fetch channel info", channel_id=channel_id, error=str(e)
            )
            return {"type": infer_channel_type(channel_id)}
