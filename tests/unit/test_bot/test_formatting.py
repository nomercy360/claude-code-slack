"""Tests for response formatting utilities (Slack mrkdwn)."""

from unittest.mock import Mock

import pytest

from src.bot.utils.formatting import (
    FormattedMessage,
    ResponseFormatter,
)
from src.bot.utils.slack_format import markdown_to_slack_mrkdwn
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


class TestMarkdownToSlackMrkdwn:
    """Tests for markdown_to_slack_mrkdwn converter."""

    # --- Bold ---

    def test_bold_double_asterisk(self):
        assert markdown_to_slack_mrkdwn("**bold**") == "*bold*"

    def test_bold_double_underscore(self):
        assert markdown_to_slack_mrkdwn("__bold__") == "*bold*"

    def test_bold_multiple(self):
        assert markdown_to_slack_mrkdwn("**one** and **two**") == "*one* and *two*"

    # --- Strikethrough ---

    def test_strikethrough(self):
        assert markdown_to_slack_mrkdwn("~~deleted~~") == "~deleted~"

    # --- Headers ---

    def test_h1(self):
        assert markdown_to_slack_mrkdwn("# Title") == "*Title*"

    def test_h3(self):
        assert markdown_to_slack_mrkdwn("### Section") == "*Section*"

    def test_header_multiline(self):
        text = "## First\nsome text\n### Second"
        result = markdown_to_slack_mrkdwn(text)
        assert result == "*First*\nsome text\n*Second*"

    def test_header_with_bold(self):
        """### 1. **name** should become *1. name* (not nested *1. *name**)."""
        result = markdown_to_slack_mrkdwn("### 1. **keybindings-help**")
        # Bold inside header — bold converts first, then header wraps
        assert "keybindings-help" in result
        assert "###" not in result

    # --- Links ---

    def test_link(self):
        assert (
            markdown_to_slack_mrkdwn("[click](https://example.com)")
            == "<https://example.com|click>"
        )

    def test_link_with_special_chars(self):
        result = markdown_to_slack_mrkdwn("[docs](https://example.com/a?b=1&c=2)")
        assert "<https://example.com/a?b=1&c=2|docs>" == result

    # --- Code protection ---

    def test_inline_code_not_converted(self):
        """Bold markers inside inline code must not be touched."""
        text = "use `**kwargs` in Python"
        result = markdown_to_slack_mrkdwn(text)
        assert "`**kwargs`" in result

    def test_fenced_code_block_not_converted(self):
        """Content inside fenced code blocks must not be touched."""
        text = "text\n```python\nx = **kwargs\n# ## not a header\n```\nmore"
        result = markdown_to_slack_mrkdwn(text)
        assert "**kwargs" in result
        assert "## not a header" in result

    def test_code_block_with_bold_outside(self):
        text = "**bold** then\n```\ncode\n```\nthen **bold**"
        result = markdown_to_slack_mrkdwn(text)
        assert result.startswith("*bold*")
        assert result.endswith("*bold*")
        assert "```\ncode\n```" in result

    # --- Italic (passthrough) ---

    def test_italic_unchanged(self):
        """Single underscores (italic) should pass through as-is."""
        assert markdown_to_slack_mrkdwn("_italic_") == "_italic_"

    # --- Slack token preservation ---

    def test_user_mention_preserved(self):
        """<@U123ABC> must not be mangled."""
        text = "Hello <@U123ABC>, how are you?"
        assert markdown_to_slack_mrkdwn(text) == text

    def test_channel_link_preserved(self):
        """<#C123|general> must not be mangled."""
        text = "See <#C0G9QF9GW|general> for details"
        assert markdown_to_slack_mrkdwn(text) == text

    def test_subteam_mention_preserved(self):
        """<!subteam^SAZ94GDB8|@team> must not be mangled."""
        text = "Ping <!subteam^SAZ94GDB8|@team>"
        assert markdown_to_slack_mrkdwn(text) == text

    def test_slack_url_preserved(self):
        """<https://example.com|click> must not be mangled."""
        text = "Visit <https://example.com|click here> for info"
        assert markdown_to_slack_mrkdwn(text) == text

    def test_slack_tokens_mixed_with_bold(self):
        """Slack tokens survive alongside bold conversion."""
        text = "**Hello** <@U123> in <#C456|ch>"
        result = markdown_to_slack_mrkdwn(text)
        assert "*Hello*" in result
        assert "<@U123>" in result
        assert "<#C456|ch>" in result

    # --- No-op cases ---

    def test_plain_text_unchanged(self):
        assert markdown_to_slack_mrkdwn("hello world") == "hello world"

    def test_empty_string(self):
        assert markdown_to_slack_mrkdwn("") == ""

    # --- Real-world Claude output ---

    def test_typical_claude_response(self):
        """Simulate the kind of response from the screenshot."""
        text = (
            "Based on the system reminder, there are **2 skills** available:\n\n"
            "### 1. **keybindings-help**\n"
            "**When to use:** Customization\n"
            "- Rebind keys\n"
            "- Edit `~/.claude/keybindings.json`\n\n"
            "### 2. **mirror-rotation**\n"
            "---\n"
            "Want to invoke one?"
        )
        result = markdown_to_slack_mrkdwn(text)
        # No raw markdown should remain
        assert "**" not in result
        assert "###" not in result
        # Bold converted
        assert "*2 skills*" in result
        assert "*When to use:*" in result
        # Inline code preserved
        assert "`~/.claude/keybindings.json`" in result
