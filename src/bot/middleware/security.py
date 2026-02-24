"""Security middleware for input validation and threat detection."""

from typing import Any, Callable, Dict

import structlog

logger = structlog.get_logger()


async def security_middleware(
    next_handler: Callable, body: Any, data: Dict[str, Any]
) -> Any:
    """Validate inputs and detect security threats.

    This middleware:
    1. Validates message content for dangerous patterns
    2. Sanitizes file uploads
    3. Detects potential attacks
    4. Logs security violations
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

    # In agentic mode, user text is a prompt to Claude — not a command.
    # Skip input validation so natural conversation works.
    settings = data.get("settings")
    agentic_mode = getattr(settings, "agentic_mode", False) if settings else False

    # Extract text and files from event body
    text = ""
    files = None
    if "event" in body:
        text = body["event"].get("text", "")
        files = body["event"].get("files")
    elif "text" in body:
        text = body.get("text", "")

    # Validate text content (classic mode only)
    if text and not agentic_mode:
        is_safe, violation_type = await validate_message_content(
            text, security_validator, user_id, audit_logger
        )
        if not is_safe:
            if client:
                channel = _get_response_channel(body)
                if channel:
                    await client.chat_postMessage(
                        channel=channel,
                        text=(
                            "*Security Alert*\n\n"
                            "Your message contains potentially dangerous content "
                            "and has been blocked.\n"
                            f"Violation: {violation_type}\n\n"
                            "If you believe this is an error, please contact "
                            "the administrator."
                        ),
                    )
            return  # Block processing

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
        has_text=bool(text),
        has_files=bool(files),
    )

    # Continue to handler
    return await next_handler()


async def validate_message_content(
    text: str, security_validator: Any, user_id: str, audit_logger: Any
) -> tuple[bool, str]:
    """Validate message text content for security threats."""

    # Check for command injection patterns
    dangerous_patterns = [
        r";\s*rm\s+",
        r";\s*del\s+",
        r";\s*format\s+",
        r"`[^`]*`",
        r"\$\([^)]*\)",
        r"&&\s*rm\s+",
        r"\|\s*mail\s+",
        r">\s*/dev/",
        r"curl\s+.*\|\s*sh",
        r"wget\s+.*\|\s*sh",
        r"exec\s*\(",
        r"eval\s*\(",
    ]

    import re

    for pattern in dangerous_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if audit_logger:
                await audit_logger.log_security_violation(
                    user_id=user_id,
                    violation_type="command_injection_attempt",
                    details=f"Dangerous pattern detected: {pattern}",
                    severity="high",
                    attempted_action="message_send",
                )

            logger.warning(
                "Command injection attempt detected",
                user_id=user_id,
                pattern=pattern,
                text_preview=text[:100],
            )
            return False, "Command injection attempt"

    # Check for path traversal attempts
    path_traversal_patterns = [
        r"\.\./.*",
        r"~\/.*",
        r"\/etc\/.*",
        r"\/var\/.*",
        r"\/usr\/.*",
        r"\/sys\/.*",
        r"\/proc\/.*",
    ]

    for pattern in path_traversal_patterns:
        if re.search(pattern, text):
            if audit_logger:
                await audit_logger.log_security_violation(
                    user_id=user_id,
                    violation_type="path_traversal_attempt",
                    details=f"Path traversal pattern detected: {pattern}",
                    severity="high",
                    attempted_action="message_send",
                )

            logger.warning(
                "Path traversal attempt detected",
                user_id=user_id,
                pattern=pattern,
                text_preview=text[:100],
            )
            return False, "Path traversal attempt"

    # Check for suspicious URLs or domains
    suspicious_patterns = [
        r"https?://[^/]*\.ru/",
        r"https?://[^/]*\.tk/",
        r"https?://[^/]*\.ml/",
        r"https?://bit\.ly/",
        r"https?://tinyurl\.com/",
        r"javascript:",
        r"data:text/html",
    ]

    for pattern in suspicious_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            if audit_logger:
                await audit_logger.log_security_violation(
                    user_id=user_id,
                    violation_type="suspicious_url",
                    details=f"Suspicious URL pattern detected: {pattern}",
                    severity="medium",
                    attempted_action="message_send",
                )

            logger.warning("Suspicious URL detected", user_id=user_id, pattern=pattern)
            return False, "Suspicious URL detected"

    # Sanitize content using security validator
    sanitized = security_validator.sanitize_command_input(text)
    if len(sanitized) < len(text) * 0.5:  # More than 50% removed
        if audit_logger:
            await audit_logger.log_security_violation(
                user_id=user_id,
                violation_type="excessive_sanitization",
                details="More than 50% of content was dangerous",
                severity="medium",
                attempted_action="message_send",
            )

        logger.warning(
            "Excessive content sanitization required",
            user_id=user_id,
            original_length=len(text),
            sanitized_length=len(sanitized),
        )
        return False, "Content contains too many dangerous characters"

    return True, ""


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
