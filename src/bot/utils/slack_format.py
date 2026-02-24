"""Slack mrkdwn formatting utilities.

Slack's mrkdwn format is similar to Markdown but has a few differences:
- Bold: *text* (not **text**)
- Italic: _text_ (same)
- Strike: ~text~ (not ~~text~~)
- Code: `code` and ```code blocks``` (same)
- Links: <url|text> (not [text](url))
- Only &, <, > need escaping in regular text
"""

import re


def escape_mrkdwn(text: str) -> str:
    """Escape special characters for Slack mrkdwn.

    Only &, <, > are truly special in Slack messages.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn.

    Claude's output is standard Markdown. Slack supports a subset
    (mrkdwn) that's slightly different. This converts the common patterns.

    Slack natively supports triple-backtick code blocks, so those
    pass through unchanged.
    """
    # Extract code blocks and inline code to protect them from conversion
    placeholders: list[tuple[str, str]] = []
    counter = 0

    def _placeholder(content: str) -> str:
        nonlocal counter
        key = f"\x00PH{counter}\x00"
        counter += 1
        placeholders.append((key, content))
        return key

    # Protect fenced code blocks (```...```)
    def _protect_fenced(m: re.Match) -> str:  # type: ignore[type-arg]
        return _placeholder(m.group(0))

    text = re.sub(r"```[\s\S]*?```", _protect_fenced, text)

    # Protect inline code (`...`)
    def _protect_inline(m: re.Match) -> str:  # type: ignore[type-arg]
        return _placeholder(m.group(0))

    text = re.sub(r"`[^`\n]+`", _protect_inline, text)

    # Convert bold: **text** -> *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # __text__ -> *text*
    text = re.sub(r"__(.+?)__", r"*\1*", text)

    # Convert strikethrough: ~~text~~ -> ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # Convert links: [text](url) -> <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # Convert headers: # Header -> *Header*
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # Restore placeholders
    for key, content in placeholders:
        text = text.replace(key, content)

    return text
