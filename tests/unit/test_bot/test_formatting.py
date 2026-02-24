"""Tests for response formatting utilities (Slack mrkdwn)."""

from unittest.mock import Mock

import pytest

from src.bot.utils.formatting import (
    FormattedMessage,
    ResponseFormatter,
)
from src.config.settings import Settings


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    settings = Mock(spec=Settings)
    return settings


@pytest.fixture
def formatter(mock_settings):
    """Create response formatter."""
    return ResponseFormatter(mock_settings)


class TestFormattedMessage:
    """Test FormattedMessage dataclass."""

    def test_formatted_message_creation(self):
        """Test FormattedMessage creation."""
        msg = FormattedMessage("Test message")
        assert msg.text == "Test message"

    def test_formatted_message_length(self):
        """Test FormattedMessage length calculation."""
        msg = FormattedMessage("Hello, world!")
        assert len(msg) == 13


class TestResponseFormatter:
    """Test ResponseFormatter functionality."""

    def test_formatter_initialization(self, mock_settings):
        """Test formatter initialization."""
        formatter = ResponseFormatter(mock_settings)
        assert formatter.settings == mock_settings

    def test_format_simple_message(self, formatter):
        """Test formatting simple message."""
        text = "Hello, world!"
        messages = formatter.format_claude_response(text)

        assert len(messages) == 1
        assert messages[0].text == text

    def test_format_code_blocks(self, formatter):
        """Test code block formatting."""
        text = "Here's some code:\n```python\nprint('hello')\n```"
        messages = formatter.format_claude_response(text)

        assert len(messages) == 1
        assert "```" in messages[0].text
        assert "print('hello')" in messages[0].text

    def test_split_long_message(self, formatter):
        """Test splitting long messages."""
        # Create a message longer than max_message_length with newlines
        # (splitter works on newline boundaries)
        line = "A" * 100 + "\n"
        long_text = line * 500  # ~50500 chars
        messages = formatter.format_claude_response(long_text)

        # Should be split into multiple messages
        assert len(messages) > 1

        # Each message should be under the limit
        for msg in messages:
            assert len(msg.text) <= formatter.max_message_length

    def test_format_error_message(self, formatter):
        """Test error message formatting."""
        error_msg = formatter.format_error_message("Something went wrong", "Error")

        assert "Error" in error_msg.text
        assert "Something went wrong" in error_msg.text

    def test_format_success_message(self, formatter):
        """Test success message formatting."""
        success_msg = formatter.format_success_message("Operation completed")

        assert "Success" in success_msg.text
        assert "Operation completed" in success_msg.text

    def test_clean_text(self, formatter):
        """Test text cleaning."""
        messy_text = "Hello\n\n\n\nWorld"
        cleaned = formatter._clean_text(messy_text)

        # Should reduce multiple newlines
        assert "\n\n\n" not in cleaned

    def test_format_empty_response(self, formatter):
        """Test formatting empty response."""
        messages = formatter.format_claude_response("")
        assert len(messages) == 1
        assert "No content" in messages[0].text

    def test_message_splitting_preserves_code_blocks(self, formatter):
        """Test that message splitting properly handles code blocks."""
        # Create a message with a code block that would be split
        code = "x" * (formatter.max_message_length + 1000)
        text = f"Some text\n```\n{code}\n```\nMore text"

        messages = formatter._split_message(text)

        # Should split into multiple messages and handle code block boundaries
        assert len(messages) > 1


SLACK_MESSAGE_LIMIT = 40000


class TestOversizedResponseIntegration:
    """End-to-end tests ensuring no formatted chunk exceeds Slack's message limit.

    These exercise the full format_claude_response pipeline: text cleaning,
    code block handling, and message splitting.
    """

    def test_large_plain_text_stays_under_limit(self, formatter):
        """Plain text response much larger than one message."""
        paragraph = "The quick brown fox jumps over the lazy dog. " * 10 + "\n\n"
        text = paragraph * 100  # ~45 000 chars

        messages = formatter.format_claude_response(text)

        assert len(messages) > 1
        for i, msg in enumerate(messages):
            assert (
                len(msg.text) <= SLACK_MESSAGE_LIMIT
            ), f"Chunk {i} is {len(msg.text)} chars (limit {SLACK_MESSAGE_LIMIT})"

    def test_large_code_block_stays_under_limit(self, formatter):
        """A single huge code block must be split."""
        code_lines = [f"    result += process(item_{i})" for i in range(2000)]
        text = "```python\ndef big_function():\n" + "\n".join(code_lines) + "\n```"

        messages = formatter.format_claude_response(text)

        assert len(messages) > 1
        for i, msg in enumerate(messages):
            assert (
                len(msg.text) <= SLACK_MESSAGE_LIMIT
            ), f"Chunk {i} is {len(msg.text)} chars (limit {SLACK_MESSAGE_LIMIT})"

    def test_mixed_content_stays_under_limit(self, formatter):
        """Mixed markdown: headings, bold, code blocks."""
        sections = []
        for n in range(10):
            sections.append(f"## Section {n}\n\n")
            sections.append(f"Here is an *explanation* of step {n}.\n\n")
            code = "\n".join([f'    print("step {n} line {j}")' for j in range(200)])
            sections.append(f"```python\n{code}\n```\n\n")

        text = "".join(sections)

        messages = formatter.format_claude_response(text)

        assert len(messages) > 1
        for i, msg in enumerate(messages):
            assert (
                len(msg.text) <= SLACK_MESSAGE_LIMIT
            ), f"Chunk {i} is {len(msg.text)} chars (limit {SLACK_MESSAGE_LIMIT})"
        # All content should be present
        full = "".join(m.text for m in messages)
        assert "Section 0" in full
        assert "Section 9" in full
