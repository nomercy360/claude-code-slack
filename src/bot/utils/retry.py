"""Retry wrapper for Slack API calls with exponential backoff."""

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

import structlog
from slack_sdk.errors import SlackApiError

logger = structlog.get_logger()

T = TypeVar("T")

# HTTP status codes that are worth retrying
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}


async def slack_api_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 2,
    **kwargs: Any,
) -> T:
    """Execute a Slack API call with exponential backoff retry.

    Retries on 429 (rate limit), 500, 502, 503 errors.
    Other errors are raised immediately.
    """
    for attempt in range(retries + 1):
        try:
            return await fn(*args, **kwargs)
        except SlackApiError as e:
            status = getattr(e.response, "status_code", 0)
            if attempt == retries or status not in _RETRYABLE_STATUS_CODES:
                raise
            wait = min(0.5 * (2**attempt), 3.0)
            logger.debug(
                "Slack API call failed, retrying",
                attempt=attempt + 1,
                status=status,
                wait_seconds=wait,
            )
            await asyncio.sleep(wait)
    # Unreachable, but keeps mypy happy
    raise RuntimeError("Exhausted retries")  # pragma: no cover
