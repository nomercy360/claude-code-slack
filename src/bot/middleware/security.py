"""Security middleware for input validation and threat detection."""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def security_middleware(
    next_handler: Callable, body: Any, data: Dict[str, Any]
) -> Any:
    """Validate inputs and detect security threats.

    User text is a prompt to Claude (agentic mode) so we skip text validation.
    This middleware validates file uploads only.
    """
    user_id = data.get("_slack_user_id")
    client = data.get("_slack_client")

    if not user_id:
        logger.warning("No user information in event")
        return await next_handler()

    # Get dependencies from data
    security_validator = data.get("security_validator")
    audit_logger = data.get("audit_logger")

    if not security_validator:
        logger.error("Security validator not available in middleware context")
        return await next_handler()

    # Extract files from event body
    files = None
    if "event" in body:
        files = body["event"].get("files")

    # Validate file uploads if present
    if files:
        for file_info in files:
            is_safe, error_message = await validate_file_upload(
                file_info, security_validator, user_id, audit_logger
            )
            if not is_safe:
                if client:
                    channel = _get_response_channel(body)
                    if channel:
                        await client.chat_postMessage(
                            channel=channel,
                            text=(
                                "*File Upload Blocked*\n\n"
                                f"{error_message}\n\n"
                                "Please ensure your file meets security requirements."
                            ),
                        )
                return  # Block processing

    # Log successful security validation
    logger.debug(
        "Security validation passed",
        user_id=user_id,
        has_files=bool(files),
    )

    # Continue to handler
    return await next_handler()


async def validate_file_upload(
    file_info: dict, security_validator: Any, user_id: str, audit_logger: Any
) -> tuple[bool, str]:
    """Validate file uploads for security.

    Args:
        file_info: Slack file object dict with keys like 'name', 'size',
                   'mimetype', etc.
    """
    filename = file_info.get("name", "unknown")
    file_size = file_info.get("size", 0)
    mime_type = file_info.get("mimetype", "unknown")

    # Validate filename
    is_valid, error_message = security_validator.validate_filename(filename)
    if not is_valid:
        if audit_logger:
            await audit_logger.log_security_violation(
                user_id=user_id,
                violation_type="dangerous_filename",
                details=f"Filename validation failed: {error_message}",
                severity="medium",
                attempted_action="file_upload",
            )

        logger.warning(
            "Dangerous filename detected",
            user_id=user_id,
            filename=filename,
            error=error_message,
        )
        return False, error_message

    # Check file size limits
    max_file_size = 10 * 1024 * 1024  # 10MB
    if file_size > max_file_size:
        if audit_logger:
            await audit_logger.log_security_violation(
                user_id=user_id,
                violation_type="file_too_large",
                details=f"File size {file_size} exceeds limit {max_file_size}",
                severity="low",
                attempted_action="file_upload",
            )

        return False, f"File too large. Maximum size: {max_file_size // (1024*1024)}MB"

    # Check MIME type
    dangerous_mime_types = [
        "application/x-executable",
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-dosexec",
        "application/x-winexe",
        "application/x-sh",
        "application/x-shellscript",
    ]

    if mime_type in dangerous_mime_types:
        if audit_logger:
            await audit_logger.log_security_violation(
                user_id=user_id,
                violation_type="dangerous_mime_type",
                details=f"Dangerous MIME type: {mime_type}",
                severity="high",
                attempted_action="file_upload",
            )

        logger.warning(
            "Dangerous MIME type detected",
            user_id=user_id,
            filename=filename,
            mime_type=mime_type,
        )
        return False, f"File type not allowed: {mime_type}"

    # Log successful file validation
    if audit_logger:
        await audit_logger.log_file_access(
            user_id=user_id,
            file_path=filename,
            action="upload_validated",
            success=True,
            file_size=file_size,
        )

    logger.info(
        "File upload validated",
        user_id=user_id,
        filename=filename,
        file_size=file_size,
        mime_type=mime_type,
    )

    return True, ""


def _get_response_channel(body: dict) -> str:
    """Extract the best channel to respond to from a Slack event body."""
    if "event" in body:
        return body["event"].get("channel", "")
    if "channel_id" in body:
        return body["channel_id"]
    return ""
