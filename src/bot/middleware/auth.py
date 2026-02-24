"""Slack bot authentication middleware."""

from datetime import UTC, datetime
from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def auth_middleware(
    next_handler: Callable, body: Any, data: Dict[str, Any]
) -> Any:
    """Check authentication before processing messages.

    This middleware:
    1. Checks if user is authenticated
    2. Attempts authentication if not authenticated
    3. Updates session activity
    4. Logs authentication events
    """
    user_id = data.get("_slack_user_id")
    client = data.get("_slack_client")

    if not user_id:
        logger.warning("No user information in event")
        return

    # Get dependencies from data
    auth_manager = data.get("auth_manager")
    audit_logger = data.get("audit_logger")

    if not auth_manager:
        logger.error("Authentication manager not available in middleware context")
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text="Authentication system unavailable. Please try again later.",
                )
        return

    # Check if user is already authenticated
    if auth_manager.is_authenticated(user_id):
        # Update session activity
        if auth_manager.refresh_session(user_id):
            session = auth_manager.get_session(user_id)
            logger.debug(
                "Session refreshed",
                user_id=user_id,
                auth_provider=session.auth_provider if session else None,
            )

        # Continue to handler
        return await next_handler()

    # User not authenticated - attempt authentication
    logger.info("Attempting authentication for user", user_id=user_id)

    # Try to authenticate (providers will check whitelist and tokens)
    authentication_successful = await auth_manager.authenticate_user(user_id)

    # Log authentication attempt
    if audit_logger:
        await audit_logger.log_auth_attempt(
            user_id=user_id,
            success=authentication_successful,
            method="automatic",
            reason="message_received",
        )

    if authentication_successful:
        session = auth_manager.get_session(user_id)
        logger.info(
            "User authenticated successfully",
            user_id=user_id,
            auth_provider=session.auth_provider if session else None,
        )

        # Welcome message for new session
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text=(
                        f"Welcome! You are now authenticated.\n"
                        f"Session started at {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
                    ),
                )

        # Continue to handler
        return await next_handler()

    else:
        # Authentication failed
        logger.warning("Authentication failed", user_id=user_id)

        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text=(
                        "*Authentication Required*\n\n"
                        "You are not authorized to use this bot.\n"
                        "Please contact the administrator for access.\n\n"
                        f"Your Slack User ID: `{user_id}`\n"
                        "Share this ID with the administrator to request access."
                    ),
                )
        return  # Stop processing


async def require_auth(next_handler: Callable, body: Any, data: Dict[str, Any]) -> Any:
    """Stricter middleware that only allows authenticated users."""
    user_id = data.get("_slack_user_id")
    auth_manager = data.get("auth_manager")
    client = data.get("_slack_client")

    if not auth_manager or not auth_manager.is_authenticated(user_id):
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text="Authentication required to use this command.",
                )
        return

    return await next_handler()


async def admin_required(
    next_handler: Callable, body: Any, data: Dict[str, Any]
) -> Any:
    """Middleware that requires admin privileges."""
    user_id = data.get("_slack_user_id")
    auth_manager = data.get("auth_manager")
    client = data.get("_slack_client")

    if not auth_manager or not auth_manager.is_authenticated(user_id):
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text="Authentication required.",
                )
        return

    session = auth_manager.get_session(user_id)
    if not session or not session.user_info:
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text="Session information unavailable.",
                )
        return

    permissions = session.user_info.get("permissions", [])
    if "admin" not in permissions:
        if client:
            channel = _get_response_channel(body)
            if channel:
                await client.chat_postMessage(
                    channel=channel,
                    text=(
                        "*Admin Access Required*\n\n"
                        "This command requires administrator privileges."
                    ),
                )
        return

    return await next_handler()


def _get_response_channel(body: dict) -> str:
    """Extract the best channel to respond to from a Slack event body."""
    # Events
    if "event" in body:
        return body["event"].get("channel", "")
    # Slash commands
    if "channel_id" in body:
        return body["channel_id"]
    return ""
