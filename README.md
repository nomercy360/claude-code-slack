# Claude Code Slack Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A Slack bot that gives you remote access to [Claude Code](https://claude.ai/code). Chat naturally with Claude about your projects -- no terminal needed.

## What is this?

This bot connects Slack to Claude Code, providing a conversational AI interface for your codebase:

- **Chat naturally** -- ask Claude to analyze, edit, or explain your code in plain language
- **Works in DMs and channels** -- DMs respond to everything; in channels, only responds to @mentions
- **Maintain context** across conversations with automatic session persistence per project
- **Visual status** -- emoji reactions show processing state (eyes -> wrench -> checkmark)
- **Stay secure** with built-in authentication, directory sandboxing, and audit logging

## Quick Start

### Demo

```
You: @Claude Code Can you help me add error handling to src/api.py?

Bot: 👀 (reacts on your message)
     Working... (3s)
     📖 Read: api.py
     ✏️ Edit: api.py
Bot: ✅ (final reaction)
     I've added error handling to src/api.py...
```

### 1. Prerequisites

- **Python 3.11+** -- [Download here](https://www.python.org/downloads/)
- **Claude Code CLI** -- [Install from here](https://claude.ai/code)
- **Slack App** -- Create one at [api.slack.com/apps](https://api.slack.com/apps)

### 2. Install

```bash
git clone https://github.com/nomercy360/claude-code-slack.git
cd claude-code-slack
make dev  # requires Poetry
```

### 3. Configure Slack App

Use the provided `slack-app-manifest.yaml` to create your Slack app, or configure manually:

**Required Bot Token Scopes:**
- `app_mentions:read`, `chat:write`, `channels:history`, `groups:history`, `im:history`, `mpim:history`
- `reactions:read`, `reactions:write`, `files:read`, `files:write`, `users:read`

**Required Event Subscriptions:**
- `message.channels`, `message.groups`, `message.im`, `message.mpim`, `app_mention`

**Socket Mode:** Must be enabled (uses WebSocket, no public URL needed).

### 4. Environment Variables

```bash
cp .env.example .env
# Edit .env with your settings:
```

**Required:**
```bash
SLACK_BOT_TOKEN=xoxb-...            # Bot User OAuth Token
SLACK_APP_TOKEN=xapp-...            # App-Level Token (Socket Mode)
SLACK_SIGNING_SECRET=...            # Signing Secret
APPROVED_DIRECTORY=/path/to/projects
ALLOWED_USERS=U0123456789           # Comma-separated Slack user IDs
```

### 5. Run

```bash
make run          # Production
make run-debug    # With debug logging
```

DM the bot or @mention it in a channel to get started.

## How It Works

### DMs vs Channels

- **DMs**: Bot responds to every message
- **Channels/Groups**: Bot only responds when @mentioned -- other messages are ignored
- The @mention prefix is automatically stripped from the prompt sent to Claude

### Emoji Status Reactions

The bot reacts on your original message to show progress:

| Emoji | Meaning |
|-------|---------|
| 👀 | Message received, starting to process |
| 🔨 | Claude is using tools (reading/writing files, running commands) |
| ✅ | Completed successfully |
| ❌ | Error occurred |

### Commands

**Slash commands:** `/claude-start`, `/claude-status`, `/claude-verbose`, `/claude-repo`

```
/claude-status           -- Show current directory, session, cost
/claude-verbose 0|1|2    -- Set output verbosity
/claude-repo             -- List repos / switch directory
/claude-repo my-project  -- Switch to specific project
```

### Verbose Output

Use `/claude-verbose 0|1|2` to control how much background activity is shown:

| Level | Shows |
|-------|-------|
| **0** (quiet) | Final response only |
| **1** (normal, default) | Tool names + reasoning snippets in real-time |
| **2** (detailed) | Tool names with inputs + longer reasoning text |

## Configuration

### Common Options

```bash
# Claude
ANTHROPIC_API_KEY=sk-ant-...     # API key (optional if using CLI auth)
CLAUDE_MAX_COST_PER_USER=10.0    # Spending limit per user (USD)
CLAUDE_TIMEOUT_SECONDS=300       # Operation timeout

# Mode
VERBOSE_LEVEL=1                  # 0=quiet, 1=normal (default), 2=detailed

# Rate Limiting
RATE_LIMIT_REQUESTS=10           # Requests per window
RATE_LIMIT_WINDOW=60             # Window in seconds

# Security (trusted environments only)
DISABLE_SECURITY_PATTERNS=false  # Disable input validation patterns
DISABLE_TOOL_VALIDATION=false    # Disable tool allowlist enforcement
```

## Security

This bot implements defense-in-depth security:

- **Access Control** -- Whitelist-based user authentication
- **Directory Isolation** -- Sandboxing to approved directories
- **Rate Limiting** -- Request and cost-based limits
- **Input Validation** -- Injection and path traversal protection
- **Audit Logging** -- Complete tracking of all user actions

## Development

```bash
make dev           # Install all dependencies
make test          # Run tests with coverage
make lint          # Black + isort + flake8 + mypy
make format        # Auto-format code
make run-debug     # Run with debug logging
```

## License

MIT License -- see [LICENSE](LICENSE).

## Acknowledgments

- [Claude](https://claude.ai) by Anthropic
- [Slack Bolt for Python](https://github.com/slackapi/bolt-python)
