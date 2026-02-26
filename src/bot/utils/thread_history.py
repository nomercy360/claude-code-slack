"""Fetch Slack thread history to provide context for Claude."""

from typing import Any, List

import structlog

logger = structlog.get_logger()

# Max chars of thread context to prepend to the prompt
MAX_CONTEXT_CHARS = 8000


async def _fetch_thread_messages(
    client: Any,
    channel_id: str,
    thread_ts: str,
    max_messages: int = 50,
) -> List[dict]:
    """Fetch messages from a Slack thread via conversations.replies.

    Returns all messages except the current (last) one, or an empty list
    on error / if no prior messages exist.
    """
    try:
        result = await client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=max_messages + 1,  # +1 because current message is included
        )
    except Exception as e:
        logger.warning(
            "Failed to fetch thread replies",
            channel_id=channel_id,
            thread_ts=thread_ts,
            error=str(e),
        )
        return []

    messages: List[dict] = result.get("messages", [])

    if len(messages) <= 1:
        return []

    # Exclude the last message (it's the current one being processed)
    return messages[:-1]


def _format_messages(
    messages: List[dict],
    bot_user_id: str,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Format a list of Slack messages into a text context block."""
    lines: List[str] = []
    total_chars = 0

    for msg in messages:
        user = msg.get("user", "unknown")
        text = msg.get("text", "")

        if not text:
            continue

        if user == bot_user_id:
            label = "[Assistant]"
        else:
            label = f"[User <@{user}>]"

        line = f"{label}: {text}"

        if total_chars + len(line) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 50:
                lines.append(line[:remaining] + "...")
            break

        lines.append(line)
        total_chars += len(line) + 1

    return "\n".join(lines)


async def fetch_thread_context(
    client: Any,
    channel_id: str,
    thread_ts: str,
    bot_user_id: str,
    max_messages: int = 50,
) -> str:
    """Fetch ALL prior messages from a Slack thread and format as context.

    Used for the first message in a thread (new session) to give Claude
    full context of the conversation so far.

    Returns an empty string if no prior messages exist.
    """
    prior = await _fetch_thread_messages(client, channel_id, thread_ts, max_messages)
    if not prior:
        return ""
    return _format_messages(prior, bot_user_id)


async def fetch_unseen_thread_messages(
    client: Any,
    channel_id: str,
    thread_ts: str,
    bot_user_id: str,
    max_messages: int = 50,
) -> str:
    """Fetch messages the bot hasn't seen — those after its last reply.

    In channels, the bot only processes @mentions. Messages sent without
    a mention are invisible to Claude's session. This function returns
    those "missed" messages so they can be injected as context.

    Returns an empty string if there are no unseen messages.
    """
    prior = await _fetch_thread_messages(client, channel_id, thread_ts, max_messages)
    if not prior:
        return ""

    # Find the last bot message — everything after it is unseen
    last_bot_idx = -1
    for i, msg in enumerate(prior):
        if msg.get("user") == bot_user_id:
            last_bot_idx = i

    if last_bot_idx == -1:
        # Bot never replied in this thread — all messages are unseen
        return _format_messages(prior, bot_user_id)

    unseen = prior[last_bot_idx + 1 :]
    if not unseen:
        return ""

    return _format_messages(unseen, bot_user_id)
