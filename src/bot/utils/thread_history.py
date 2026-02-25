"""Fetch Slack thread history to provide context for Claude."""

from typing import Any, List

import structlog

logger = structlog.get_logger()

# Max chars of thread context to prepend to the prompt
MAX_CONTEXT_CHARS = 8000


async def fetch_thread_context(
    client: Any,
    channel_id: str,
    thread_ts: str,
    bot_user_id: str,
    max_messages: int = 50,
) -> str:
    """Fetch prior messages from a Slack thread and format as context.

    Returns an empty string if the thread has no prior messages (i.e. this
    is the first message or a top-level message).
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
        return ""

    messages: List[dict] = result.get("messages", [])

    if len(messages) <= 1:
        # No prior messages — this is the first or only message
        return ""

    # Exclude the last message (it's the current one being processed)
    prior = messages[:-1]

    lines: List[str] = []
    total_chars = 0

    for msg in prior:
        user = msg.get("user", "unknown")
        text = msg.get("text", "")

        if not text:
            continue

        # Skip bot's own messages
        if user == bot_user_id:
            label = "[Assistant]"
        else:
            label = f"[User <@{user}>]"

        line = f"{label}: {text}"

        # Truncate if we'd exceed the budget
        if total_chars + len(line) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total_chars
            if remaining > 50:
                lines.append(line[:remaining] + "...")
            break

        lines.append(line)
        total_chars += len(line) + 1  # +1 for newline

    if not lines:
        return ""

    return "\n".join(lines)
