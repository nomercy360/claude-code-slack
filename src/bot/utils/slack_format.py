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

    # Protect Slack tokens: <@U123>, <#C123|name>, <http...>, <!subteam^...>, etc.
    def _protect_slack_token(m: re.Match) -> str:  # type: ignore[type-arg]
        return _placeholder(m.group(0))

    text = re.sub(r"<[!@#][^>]*>|<https?://[^>]+>", _protect_slack_token, text)

    # Protect fenced code blocks (```...```)
    def _protect_fenced(m: re.Match) -> str:  # type: ignore[type-arg]
        return _placeholder(m.group(0))

    text = re.sub(r"```[\s\S]*?```", _protect_fenced, text)

    # Protect inline code (`...`)
    def _protect_inline(m: re.Match) -> str:  # type: ignore[type-arg]
        return _placeholder(m.group(0))

    text = re.sub(r"`[^`\n]+`", _protect_inline, text)

    # Convert headers first: # Header -> *Header*
    # Strip any bold markers (**) inside the header text to avoid nested *...*
    def _convert_header(m: re.Match) -> str:  # type: ignore[type-arg]
        content = m.group(1).replace("**", "")
        return f"*{content}*"

    text = re.sub(r"^#{1,6}\s+(.+)$", _convert_header, text, flags=re.MULTILINE)

    # Convert bold: **text** -> *text*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # __text__ -> *text*
    text = re.sub(r"__(.+?)__", r"*\1*", text)

    # Convert strikethrough: ~~text~~ -> ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)

    # Convert links: [text](url) -> <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)

    # Convert Markdown tables to fixed-width text blocks.
    # Matches a header row, separator row (|---|---|), and body rows.
    text = _convert_tables(text)

    # Restore placeholders
    for key, content in placeholders:
        text = text.replace(key, content)

    return text


def _convert_tables(text: str) -> str:
    """Convert Markdown tables to monospaced text blocks for Slack.

    Slack has no table support, so we render them as ```code blocks```
    with aligned columns.
    """
    table_pattern = re.compile(
        r"((?:^\|.+\|[ \t]*\n)+"  # one or more pipe-delimited rows
        r"(?:^\|[-| :]+\|[ \t]*\n)"  # separator row  |---|---|
        r"(?:^\|.+\|[ \t]*\n?)*)",  # remaining body rows (last may lack \n)
        re.MULTILINE,
    )

    def _format_table(m: re.Match) -> str:  # type: ignore[type-arg]
        block = m.group(0)
        rows: list[list[str]] = []
        for line in block.strip().splitlines():
            # Skip separator rows (|---|---|)
            stripped = line.strip().strip("|")
            if re.match(r"^[\s|:-]+$", stripped):
                continue
            cells = [c.strip() for c in stripped.split("|")]
            rows.append(cells)

        if not rows:
            return block

        # Calculate column widths
        n_cols = max(len(r) for r in rows)
        col_widths = [0] * n_cols
        for row in rows:
            for i, cell in enumerate(row):
                if i < n_cols:
                    col_widths[i] = max(col_widths[i], len(cell))

        # Format rows with padding
        formatted: list[str] = []
        for ri, row in enumerate(rows):
            parts = []
            for i in range(n_cols):
                cell = row[i] if i < len(row) else ""
                parts.append(cell.ljust(col_widths[i]))
            formatted.append("  ".join(parts))
            # Add separator after header row
            if ri == 0:
                formatted.append("  ".join("-" * w for w in col_widths))

        return "```\n" + "\n".join(formatted) + "\n```\n"

    return table_pattern.sub(_format_table, text)
