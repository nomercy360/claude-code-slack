"""Rate limiting middleware for Slack bot."""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def rate_limit_middleware(
    next_handler: Callable, body: Any, data: Dict[str, Any]
) -> Any:
    """Check rate limits before processing messages.

    This middleware:
    1. Checks request rate limits
    2. Estimates and checks cost limits
    3. Logs rate limit violations
    4. Provides helpful error messages
    """
    user_id = data.get("_slack_user_id")
    client = data.get("_slack_client")

    if not user_id:
        logger.warning("No user information in event")
        return await next_handler()

    # Get dependencies from data
    rate_limiter = data.get("rate_limiter")
    audit_logger = data.get("audit_logger")

    if not rate_limiter:
        logger.error("Rate limiter not available in middleware context")
        # Don't block on missing rate limiter
        return await next_handler()

    # Estimate cost based on message content
    estimated_cost = estimate_message_cost(body)

    # Check rate limits
    allowed, message = await rate_limiter.check_rate_limit(
        user_id=user_id, cost=estimated_cost, tokens=1
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded",
            user_id=user_id,
            estimated_cost=estimated_cost,
            message=message,
        )

        # Log rate limit violation
        if audit_logger:
            await audit_logger.log_rate_limit_exceeded(
                user_id=user_id,
                limit_type="combined",
                current_usage=0,
                limit_value=0,
            )

        # Send user-friendly rate limit message
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text=f"Rate limit: {message}",
                )
        return  # Stop processing

    # Rate limit check passed
    logger.debug(
        "Rate limit check passed",
        user_id=user_id,
        estimated_cost=estimated_cost,
    )

    # Continue to handler
    return await next_handler()


def estimate_message_cost(body: dict) -> float:
    """Estimate the cost of processing a message."""
    # Extract text from event or command
    text = ""
    if "event" in body:
        text = body["event"].get("text", "")
    elif "text" in body:
        text = body.get("text", "")
    elif "command" in body:
        text = body.get("text", "")

    # Base cost for any message
    base_cost = 0.01

    # Additional cost based on message length
    length_cost = len(text) * 0.0001

    # File uploads cost more
    if "event" in body and body["event"].get("files"):
        return base_cost + length_cost + 0.05

    # Slash commands cost more
    if "command" in body:
        return base_cost + length_cost + 0.02

    # Check for complex operations keywords
    complex_keywords = [
        "analyze",
        "generate",
        "create",
        "build",
        "compile",
        "test",
        "debug",
        "refactor",
        "optimize",
        "explain",
    ]

    if any(keyword in text.lower() for keyword in complex_keywords):
        return base_cost + length_cost + 0.03

    return base_cost + length_cost


def _get_response_channel(body: dict) -> str:
    """Extract the best channel to respond to from a Slack event body."""
    if "event" in body:
        return body["event"].get("channel", "")
    if "channel_id" in body:
        return body["channel_id"]
    return ""
