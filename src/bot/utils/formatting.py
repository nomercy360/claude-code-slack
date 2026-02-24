"""Format bot responses for optimal Slack display.

Slack natively supports triple-backtick code blocks and mrkdwn formatting.
Claude's Markdown output is mostly compatible, so we do minimal transformation.
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from ...config.settings import Settings
from ...utils.constants import SAFE_MESSAGE_LENGTH
from .slack_format import markdown_to_slack_mrkdwn


@dataclass
class FormattedMessage:
    """Represents a formatted message for Slack."""

    text: str

    def __len__(self) -> int:
        """Return length of message text."""
        return len(self.text)


class ResponseFormatter:
    """Format Claude responses for Slack display."""

    def __init__(self, settings: Settings):
        """Initialize formatter with settings."""
        self.settings = settings
        self.max_message_length = SAFE_MESSAGE_LENGTH

    def format_claude_response(
        self, text: str, context: Optional[dict] = None
    ) -> List[FormattedMessage]:
        """Format Claude response for Slack."""
        text = self._clean_text(text)

        if not text or not text.strip():
            return [FormattedMessage("_(No content to display)_")]

        messages = self._split_message(text)
        return [m for m in messages if m.text and m.text.strip()]

    def format_error_message(
        self, error: str, error_type: str = "Error"
    ) -> FormattedMessage:
        """Format error message."""
        return FormattedMessage(f"*{error_type}*\n\n{error}")

    def format_success_message(
        self, message: str, title: str = "Success"
    ) -> FormattedMessage:
        """Format success message."""
        return FormattedMessage(f"*{title}*\n\n{message}")

    def format_info_message(
        self, message: str, title: str = "Info"
    ) -> FormattedMessage:
        """Format info message."""
        return FormattedMessage(f"*{title}*\n\n{message}")

    def _clean_text(self, text: str) -> str:
        """Clean text for Slack display."""
        # Convert Markdown bold/strike/links/headers to Slack mrkdwn
        text = markdown_to_slack_mrkdwn(text)
        # Remove excessive whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _split_message(self, text: str) -> List[FormattedMessage]:
        """Split long messages while preserving code blocks."""
        if not text or not text.strip():
            return []
        if len(text) <= self.max_message_length:
            return [FormattedMessage(text)]

        messages: List[FormattedMessage] = []
        current_chunk = ""
        in_code_block = False

        for line in text.split("\n"):
            line_with_nl = line + "\n"

            # Track code block state
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            # Check if adding this line would exceed the limit
            if (
                len(current_chunk) + len(line_with_nl) > self.max_message_length
                and current_chunk
            ):
                # Close code block if we're splitting mid-block
                if in_code_block:
                    current_chunk += "```\n"

                messages.append(FormattedMessage(current_chunk.rstrip()))

                # Reopen code block in next chunk
                current_chunk = "```\n" if in_code_block else ""

            current_chunk += line_with_nl

        if current_chunk.strip():
            messages.append(FormattedMessage(current_chunk.rstrip()))

        return messages
