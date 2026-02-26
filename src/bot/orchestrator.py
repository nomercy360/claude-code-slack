"""Message orchestrator — single entry point for all Slack events.

Routes messages to Claude in agentic mode. Provides a minimal conversational
interface (slash commands + message events, no inline keyboards).
"""

import asyncio
import re
import time
from typing import Any, Callable, Dict, List, Optional

import structlog
from slack_bolt.async_app import AsyncApp

from ..claude.sdk_integration import StreamUpdate
from ..config.settings import Settings
from .utils.cache import SlackInfoCache, infer_channel_type
from .utils.formatting import ResponseFormatter
from .utils.reactions import ReactionManager
from .utils.retry import slack_api_call
from .utils.thread_history import fetch_thread_context, fetch_unseen_thread_messages

logger = structlog.get_logger()

# Patterns that look like secrets/credentials in CLI arguments
_SECRET_PATTERNS: List[re.Pattern[str]] = [
    # API keys / tokens (sk-ant-..., sk-..., ghp_..., gho_..., github_pat_..., xoxb-...)
    re.compile(
        r"(sk-ant-api\d*-[A-Za-z0-9_-]{10})[A-Za-z0-9_-]*"
        r"|(sk-[A-Za-z0-9_-]{20})[A-Za-z0-9_-]*"
        r"|(ghp_[A-Za-z0-9]{5})[A-Za-z0-9]*"
        r"|(gho_[A-Za-z0-9]{5})[A-Za-z0-9]*"
        r"|(github_pat_[A-Za-z0-9_]{5})[A-Za-z0-9_]*"
        r"|(xoxb-[A-Za-z0-9]{5})[A-Za-z0-9-]*"
    ),
    # AWS access keys
    re.compile(r"(AKIA[0-9A-Z]{4})[0-9A-Z]{12}"),
    # Generic long hex/base64 tokens after common flags/env patterns
    re.compile(
        r"((?:--token|--secret|--password|--api-key|--apikey|--auth)"
        r"[= ]+)['\"]?[A-Za-z0-9+/_.:-]{8,}['\"]?"
    ),
    # Inline env assignments like KEY=value
    re.compile(
        r"((?:TOKEN|SECRET|PASSWORD|API_KEY|APIKEY|AUTH_TOKEN|PRIVATE_KEY"
        r"|ACCESS_KEY|CLIENT_SECRET|WEBHOOK_SECRET)"
        r"=)['\"]?[^\s'\"]{8,}['\"]?"
    ),
    # Bearer / Basic auth headers
    re.compile(r"(Bearer )[A-Za-z0-9+/_.:-]{8,}" r"|(Basic )[A-Za-z0-9+/=]{8,}"),
    # Connection strings with credentials  user:pass@host
    re.compile(r"://([^:]+:)[^@]{4,}(@)"),
]


def _redact_secrets(text: str) -> str:
    """Replace likely secrets/credentials with redacted placeholders."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(
            lambda m: next((g + "***" for g in m.groups() if g is not None), "***"),
            result,
        )
    return result


# Tool name -> friendly emoji mapping for verbose output
_TOOL_ICONS: Dict[str, str] = {
    "Read": "\U0001f4d6",
    "Write": "\u270f\ufe0f",
    "Edit": "\u270f\ufe0f",
    "MultiEdit": "\u270f\ufe0f",
    "Bash": "\U0001f4bb",
    "Glob": "\U0001f50d",
    "Grep": "\U0001f50d",
    "LS": "\U0001f4c2",
    "Task": "\U0001f9e0",
    "TaskOutput": "\U0001f9e0",
    "WebFetch": "\U0001f310",
    "WebSearch": "\U0001f310",
    "NotebookRead": "\U0001f4d3",
    "NotebookEdit": "\U0001f4d3",
    "TodoRead": "\u2611\ufe0f",
    "TodoWrite": "\u2611\ufe0f",
}


def _tool_icon(name: str) -> str:
    """Return emoji for a tool, with a default wrench."""
    return _TOOL_ICONS.get(name, "\U0001f527")


def _escape_mrkdwn(text: str) -> str:
    """Minimal escaping for Slack mrkdwn special characters."""
    # Only escape &, <, > which are the truly special chars in Slack mrkdwn
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class MessageOrchestrator:
    """Routes Slack events to Claude. Agentic mode only."""

    def __init__(
        self,
        settings: Settings,
        deps: Dict[str, Any],
        bot_user_id: str = "",
    ):
        self.settings = settings
        self.deps = deps
        self._bot_user_id = bot_user_id
        self._cache = SlackInfoCache()
        # Per-user state (keyed by Slack user ID)
        self._user_state: Dict[str, Dict[str, Any]] = {}
        # Dedup: track recently processed event ts to avoid handling
        # the same message via both `message` and `app_mention` events.
        self._processed_events: Dict[str, float] = {}

    def _get_user_state(self, user_id: str, thread_ts: str = "") -> Dict[str, Any]:
        """Get or create per-user (optionally per-thread) state dict.

        When *thread_ts* is provided the state is scoped to a specific Slack
        thread so that each thread gets its own Claude session. Slash commands
        that lack thread context pass no thread_ts and get global user state.
        """
        key = f"{user_id}:{thread_ts}" if thread_ts else user_id
        if key not in self._user_state:
            # For thread-specific state, inherit current_directory from
            # the user's global state so that /claude-repo switches carry
            # over into new threads.
            if thread_ts and user_id in self._user_state:
                default_dir = self._user_state[user_id]["current_directory"]
            else:
                default_dir = self.settings.approved_directory
            self._user_state[key] = {
                "current_directory": default_dir,
                "claude_session_id": None,
                "verbose_level": None,  # None = use global default
            }
        return self._user_state[key]

    def register_handlers(self, app: AsyncApp) -> None:
        """Register Slack Bolt handlers (agentic mode only)."""
        # Slash commands
        app.command("/claude-start")(self.agentic_start)
        app.command("/claude-status")(self.agentic_status)
        app.command("/claude-verbose")(self.agentic_verbose)
        app.command("/claude-repo")(self.agentic_repo)

        # Message events -> Claude
        app.event("message")(self.handle_message_event)

        # @mention events in channels -> Claude
        app.event("app_mention")(self.handle_app_mention_event)

        # Block Kit action buttons (repo selection)
        app.action(re.compile(r"^cd:"))(self.handle_repo_action)

        logger.info("Agentic handlers registered")

    # --- Slash command handlers ---

    async def agentic_start(self, ack: Callable, command: dict, client: Any) -> None:
        """Brief welcome, no buttons."""
        await ack()

        user_id = command["user_id"]
        channel_id = command["channel_id"]
        state = self._get_user_state(user_id)

        current_dir = state["current_directory"]
        dir_display = f"`{current_dir}/`"

        await client.chat_postMessage(
            channel=channel_id,
            text=(
                f"Hi <@{user_id}>! I'm your AI coding assistant.\n"
                f"Just tell me what you need — I can read, write, and run code.\n\n"
                f"Working in: {dir_display}\n"
                f"Each thread gets its own session.\n"
                f"Commands: /claude-status · /claude-repo"
            ),
        )

    async def agentic_status(self, ack: Callable, command: dict, client: Any) -> None:
        """Compact one-line status."""
        await ack()

        user_id = command["user_id"]
        channel_id = command["channel_id"]
        state = self._get_user_state(user_id)

        current_dir = str(state["current_directory"])
        session_id = state.get("claude_session_id")
        session_status = "active" if session_id else "none"

        # Cost info
        cost_str = ""
        rate_limiter = self.deps.get("rate_limiter")
        if rate_limiter:
            try:
                user_status = rate_limiter.get_user_status(user_id)
                cost_usage = user_status.get("cost_usage", {})
                current_cost = cost_usage.get("current", 0.0)
                cost_str = f" · Cost: ${current_cost:.2f}"
            except Exception:
                pass

        await client.chat_postMessage(
            channel=channel_id,
            text=f"{current_dir} · Session: {session_status}{cost_str}",
        )

    def _get_verbose_level(self, user_id: str) -> int:
        """Return effective verbose level: per-user override or global default."""
        state = self._get_user_state(user_id)
        user_override = state.get("verbose_level")
        if user_override is not None:
            return int(user_override)
        return self.settings.verbose_level

    async def agentic_verbose(self, ack: Callable, command: dict, client: Any) -> None:
        """Set output verbosity: /verbose [0|1|2]."""
        await ack()

        user_id = command["user_id"]
        channel_id = command["channel_id"]
        args_text = command.get("text", "").strip()

        if not args_text:
            current = self._get_verbose_level(user_id)
            labels = {0: "quiet", 1: "normal", 2: "detailed"}
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"Verbosity: *{current}* ({labels.get(current, '?')})\n\n"
                    "Usage: `/verbose 0|1|2`\n"
                    "  0 = quiet (final response only)\n"
                    "  1 = normal (tools + reasoning)\n"
                    "  2 = detailed (tools with inputs + reasoning)"
                ),
            )
            return

        try:
            level = int(args_text.split()[0])
            if level not in (0, 1, 2):
                raise ValueError
        except ValueError:
            await client.chat_postMessage(
                channel=channel_id,
                text="Please use: /verbose 0, /verbose 1, or /verbose 2",
            )
            return

        state = self._get_user_state(user_id)
        state["verbose_level"] = level
        labels = {0: "quiet", 1: "normal", 2: "detailed"}
        await client.chat_postMessage(
            channel=channel_id,
            text=f"Verbosity set to *{level}* ({labels[level]})",
        )

    async def agentic_repo(self, ack: Callable, command: dict, client: Any) -> None:
        """List repos in workspace or switch to one.

        /repo          — list subdirectories with git indicators
        /repo <name>   — switch to that directory, resume session if available
        """
        await ack()

        user_id = command["user_id"]
        channel_id = command["channel_id"]
        args_text = command.get("text", "").strip()
        state = self._get_user_state(user_id)
        base = self.settings.approved_directory
        current_dir = state["current_directory"]

        if args_text:
            target_name = args_text.split()[0]
            target_path = base / target_name
            if not target_path.is_dir():
                await client.chat_postMessage(
                    channel=channel_id,
                    text=f"Directory not found: `{_escape_mrkdwn(target_name)}`",
                )
                return

            state["current_directory"] = target_path

            # Propagate directory change to all existing thread states
            # for this user so future messages in those threads use the
            # new directory (they will start fresh sessions automatically).
            prefix = f"{user_id}:"
            for k, v in self._user_state.items():
                if k.startswith(prefix):
                    v["current_directory"] = target_path

            is_git = (target_path / ".git").is_dir()
            git_badge = " (git)" if is_git else ""

            await client.chat_postMessage(
                channel=channel_id,
                text=f"Switched to `{_escape_mrkdwn(target_name)}/`{git_badge}",
            )
            return

        # No args — list repos
        try:
            entries = sorted(
                [
                    d
                    for d in base.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ],
                key=lambda d: d.name,
            )
        except OSError as e:
            await client.chat_postMessage(
                channel=channel_id,
                text=f"Error reading workspace: {e}",
            )
            return

        if not entries:
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"No repos in `{_escape_mrkdwn(str(base))}`.\n"
                    'Clone one by telling me, e.g. "clone org/repo".'
                ),
            )
            return

        lines: List[str] = []
        current_name = current_dir.name if current_dir != base else None

        for d in entries:
            is_git = (d / ".git").is_dir()
            icon = "\U0001f4e6" if is_git else "\U0001f4c1"
            marker = " \u25c0" if d.name == current_name else ""
            lines.append(f"{icon} `{_escape_mrkdwn(d.name)}/`{marker}")

        # Build Block Kit action buttons for repo selection (2 per row)
        actions_blocks: List[dict] = []
        button_elements: List[dict] = []
        for d in entries:
            button_elements.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": d.name},
                    "action_id": f"cd:{d.name}",
                    "value": d.name,
                }
            )
            if len(button_elements) >= 5:  # Slack max 5 elements per actions block
                actions_blocks.append({"type": "actions", "elements": button_elements})
                button_elements = []
        if button_elements:
            actions_blocks.append({"type": "actions", "elements": button_elements})

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Repos*\n\n" + "\n".join(lines),
                },
            },
            *actions_blocks,
        ]

        await client.chat_postMessage(
            channel=channel_id,
            text="Repos",  # fallback text
            blocks=blocks,
        )

    # --- Message event handlers ---

    def _is_duplicate_event(self, event: dict) -> bool:
        """Check if we already processed this event (dedup message vs app_mention)."""
        ts = event.get("ts", "")
        if not ts:
            return False
        now = time.time()
        # Clean old entries (older than 60s)
        self._processed_events = {
            k: v for k, v in self._processed_events.items() if now - v < 60
        }
        if ts in self._processed_events:
            return True
        self._processed_events[ts] = now
        return False

    async def handle_message_event(
        self, event: dict, say: Callable, client: Any
    ) -> None:
        """Route Slack message events to the appropriate handler."""
        # Ignore bot messages, message_changed, etc.
        # Allow file_share (file uploads come with this subtype).
        subtype = event.get("subtype")
        if subtype is not None and subtype != "file_share":
            return

        if self._is_duplicate_event(event):
            return

        user_id = event.get("user", "")
        if not user_id:
            return

        text = event.get("text", "")
        files = event.get("files")
        channel_id = event.get("channel", "")
        event_ts = event.get("ts", "")
        thread_ts = event.get("thread_ts") or event_ts

        # Mention gating: in channels/groups only respond if @mentioned
        channel_type = event.get("channel_type") or infer_channel_type(channel_id)
        if channel_type in ("channel", "group") and not files:
            if not self._bot_user_id or f"<@{self._bot_user_id}>" not in (text or ""):
                return
            # Strip mention from text
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        if files:
            await self._handle_file_upload(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                event_ts=event_ts,
                files=files,
                caption=text,
                client=client,
            )
        elif text:
            await self._handle_text_message(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                event_ts=event_ts,
                text=text,
                client=client,
            )

    async def handle_app_mention_event(
        self, event: dict, say: Callable, client: Any
    ) -> None:
        """Handle @mentions — delegates to same logic, with dedup."""
        if self._is_duplicate_event(event):
            return

        user_id = event.get("user", "")
        if not user_id:
            return

        text = event.get("text", "")
        # Strip the bot mention prefix (e.g. "<@U0AH554QF3K> hello" -> "hello")
        text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
        if not text:
            return

        channel_id = event.get("channel", "")
        event_ts = event.get("ts", "")
        thread_ts = event.get("thread_ts") or event_ts

        await self._handle_text_message(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            event_ts=event_ts,
            text=text,
            client=client,
        )

    # --- Verbose progress helpers ---

    def _format_verbose_progress(
        self,
        activity_log: List[Dict[str, Any]],
        verbose_level: int,
        start_time: float,
    ) -> str:
        """Build the progress message text based on activity so far."""
        if not activity_log:
            return "Working..."

        elapsed = time.time() - start_time
        lines: List[str] = [f"Working... ({elapsed:.0f}s)\n"]

        for entry in activity_log[-15:]:
            kind = entry.get("kind", "tool")
            if kind == "text":
                snippet = entry.get("detail", "")
                if verbose_level >= 2:
                    lines.append(f"\U0001f4ac {snippet}")
                else:
                    lines.append(f"\U0001f4ac {snippet[:80]}")
            else:
                icon = _tool_icon(entry["name"])
                if verbose_level >= 2 and entry.get("detail"):
                    lines.append(f"{icon} {entry['name']}: {entry['detail']}")
                else:
                    lines.append(f"{icon} {entry['name']}")

        if len(activity_log) > 15:
            lines.insert(1, f"... ({len(activity_log) - 15} earlier entries)\n")

        return "\n".join(lines)

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Return a short summary of tool input for verbose level 2."""
        if not tool_input:
            return ""
        if tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            path = tool_input.get("file_path") or tool_input.get("path", "")
            if path:
                return path.rsplit("/", 1)[-1]
        if tool_name in ("Glob", "Grep"):
            pattern = tool_input.get("pattern", "")
            if pattern:
                return pattern[:60]
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if cmd:
                return _redact_secrets(cmd[:100])[:80]
        if tool_name in ("WebFetch", "WebSearch"):
            return (tool_input.get("url", "") or tool_input.get("query", ""))[:60]
        if tool_name == "Task":
            desc = tool_input.get("description", "")
            if desc:
                return desc[:60]
        for v in tool_input.values():
            if isinstance(v, str) and v:
                return v[:60]
        return ""

    def _make_stream_callback(
        self,
        verbose_level: int,
        client: Any,
        channel_id: str,
        progress_ts: str,
        tool_log: List[Dict[str, Any]],
        start_time: float,
        reactions: Optional[ReactionManager] = None,
    ) -> Optional[Callable[[StreamUpdate], Any]]:
        """Create a stream callback for verbose progress updates.

        Returns None when verbose_level is 0 and no reactions to manage.
        """
        if verbose_level == 0 and reactions is None:
            return None

        last_edit_time = [0.0]
        tool_reaction_set = [False]

        async def _on_stream(update_obj: StreamUpdate) -> None:
            if update_obj.tool_calls:
                for tc in update_obj.tool_calls:
                    name = tc.get("name", "unknown")
                    detail = self._summarize_tool_input(name, tc.get("input", {}))
                    tool_log.append({"kind": "tool", "name": name, "detail": detail})
                # Set tool reaction on first tool call
                if reactions and not tool_reaction_set[0]:
                    tool_reaction_set[0] = True
                    await reactions.set("hammer_and_wrench")

            if update_obj.type == "assistant" and update_obj.content:
                text = update_obj.content.strip()
                if text and verbose_level >= 1:
                    first_line = text.split("\n", 1)[0].strip()
                    if first_line:
                        tool_log.append({"kind": "text", "detail": first_line[:120]})

            # Throttle progress message edits to avoid Slack rate limits
            if verbose_level > 0:
                now = time.time()
                if (now - last_edit_time[0]) >= 2.0 and tool_log:
                    last_edit_time[0] = now
                    new_text = self._format_verbose_progress(
                        tool_log, verbose_level, start_time
                    )
                    try:
                        await client.chat_update(
                            channel=channel_id,
                            ts=progress_ts,
                            text=new_text,
                        )
                    except Exception:
                        pass

        return _on_stream

    # --- Internal handlers ---

    async def _handle_text_message(
        self,
        user_id: str,
        channel_id: str,
        thread_ts: str,
        text: str,
        client: Any,
        event_ts: str = "",
    ) -> None:
        """Direct Claude passthrough. Simple progress. No suggestions."""
        logger.info(
            "Agentic text message",
            user_id=user_id,
            message_length=len(text),
        )

        state = self._get_user_state(user_id, thread_ts)

        # Emoji reaction on the user's original message
        reactions: Optional[ReactionManager] = None
        if event_ts:
            reactions = ReactionManager(client, channel_id, event_ts)
            await reactions.set("eyes")

        # Rate limit check
        rate_limiter = self.deps.get("rate_limiter")
        if rate_limiter:
            allowed, limit_message = await rate_limiter.check_rate_limit(user_id, 0.001)
            if not allowed:
                if reactions:
                    await reactions.set("x")
                await slack_api_call(
                    client.chat_postMessage,
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"Rate limit: {limit_message}",
                )
                return

        # Post "Working..." progress message
        progress_result = await slack_api_call(
            client.chat_postMessage,
            channel=channel_id,
            thread_ts=thread_ts,
            text="Working...",
        )
        progress_ts = progress_result["ts"]

        claude_integration = self.deps.get("claude_integration")
        if not claude_integration:
            if reactions:
                await reactions.set("x")
            await client.chat_update(
                channel=channel_id,
                ts=progress_ts,
                text="Claude integration not available. Check configuration.",
            )
            return

        current_dir = state["current_directory"]
        session_id = state.get("claude_session_id")

        verbose_level = self._get_verbose_level(user_id)
        tool_log: List[Dict[str, Any]] = []
        start_time = time.time()
        on_stream = self._make_stream_callback(
            verbose_level,
            client,
            channel_id,
            progress_ts,
            tool_log,
            start_time,
            reactions=reactions,
        )

        # Fetch thread history for context.
        # - New session: fetch ALL prior messages in the thread.
        # - Resumed session: fetch only messages after the bot's last reply
        #   (user messages without @mention that Claude hasn't seen).
        if not session_id:
            thread_context = await fetch_thread_context(
                client, channel_id, thread_ts, self._bot_user_id
            )
        else:
            thread_context = await fetch_unseen_thread_messages(
                client, channel_id, thread_ts, self._bot_user_id
            )

        if thread_context:
            if session_id:
                header = (
                    "[New messages in this Slack thread since your last reply"
                    " — the user did not @mention you for these]"
                )
            else:
                header = "[Thread context — prior messages in this Slack thread]"
            prompt = f"{header}\n{thread_context}\n\n[Current message]\n{text}"
        else:
            prompt = text

        success = True
        try:
            claude_response = await claude_integration.run_command(
                prompt=prompt,
                working_directory=current_dir,
                user_id=user_id,
                session_id=session_id,
                on_stream=on_stream,
            )

            state["claude_session_id"] = claude_response.session_id

            # Store interaction
            storage = self.deps.get("storage")
            if storage:
                try:
                    await storage.save_claude_interaction(
                        user_id=user_id,
                        session_id=claude_response.session_id,
                        prompt=text,
                        response=claude_response,
                        ip_address=None,
                    )
                except Exception as e:
                    logger.warning("Failed to log interaction", error=str(e))

            response_text = claude_response.content or "(no response)"

        except Exception as e:
            success = False
            logger.error("Claude integration failed", error=str(e), user_id=user_id)
            response_text = f"Error: {str(e)[:500]}"

        # Set final reaction
        if reactions:
            await reactions.set("white_check_mark" if success else "x")

        # Delete progress message and send final response
        try:
            await client.chat_delete(channel=channel_id, ts=progress_ts)
        except Exception:
            pass

        # Format (Markdown -> Slack mrkdwn, clean whitespace, split long messages)
        formatter = ResponseFormatter(self.settings)
        formatted = formatter.format_claude_response(response_text)

        for i, msg in enumerate(formatted):
            await slack_api_call(
                client.chat_postMessage,
                channel=channel_id,
                thread_ts=thread_ts,
                text=msg.text,
            )
            if i < len(formatted) - 1:
                await asyncio.sleep(0.5)

        # Audit log
        audit_logger = self.deps.get("audit_logger")
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id,
                command="text_message",
                args=[text[:100]],
                success=success,
            )

    async def _handle_file_upload(
        self,
        user_id: str,
        channel_id: str,
        thread_ts: str,
        files: List[dict],
        caption: str,
        client: Any,
        event_ts: str = "",
    ) -> None:
        """Process file upload -> Claude."""
        logger.info(
            "Agentic file upload",
            user_id=user_id,
            file_count=len(files),
        )

        state = self._get_user_state(user_id, thread_ts)

        # Emoji reaction on the user's original message
        reactions: Optional[ReactionManager] = None
        if event_ts:
            reactions = ReactionManager(client, channel_id, event_ts)
            await reactions.set("eyes")

        # Security validation on first file
        file_info = files[0]
        filename = file_info.get("name", "unknown")
        file_size = file_info.get("size", 0)

        security_validator = self.deps.get("security_validator")
        if security_validator:
            valid, error = security_validator.validate_filename(filename)
            if not valid:
                if reactions:
                    await reactions.set("x")
                await slack_api_call(
                    client.chat_postMessage,
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=f"File rejected: {error}",
                )
                return

        max_size = 10 * 1024 * 1024
        if file_size > max_size:
            if reactions:
                await reactions.set("x")
            await slack_api_call(
                client.chat_postMessage,
                channel=channel_id,
                thread_ts=thread_ts,
                text=f"File too large ({file_size / 1024 / 1024:.1f}MB). Max: 10MB.",
            )
            return

        progress_result = await slack_api_call(
            client.chat_postMessage,
            channel=channel_id,
            thread_ts=thread_ts,
            text="Working...",
        )
        progress_ts = progress_result["ts"]

        # Download file content
        try:
            url_private = file_info.get("url_private")
            if not url_private:
                if reactions:
                    await reactions.set("x")
                await client.chat_update(
                    channel=channel_id,
                    ts=progress_ts,
                    text="Could not access file URL.",
                )
                return

            import aiohttp

            headers = {"Authorization": f"Bearer {self.settings.slack_bot_token_str}"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url_private, headers=headers) as resp:
                    if resp.status != 200:
                        if reactions:
                            await reactions.set("x")
                        await client.chat_update(
                            channel=channel_id,
                            ts=progress_ts,
                            text=f"Failed to download file (HTTP {resp.status}).",
                        )
                        return
                    file_bytes = await resp.read()

            try:
                content = file_bytes.decode("utf-8")
                if len(content) > 50000:
                    content = content[:50000] + "\n... (truncated)"
                file_caption = caption or "Please review this file:"
                prompt = (
                    f"{file_caption}\n\n**File:** `{filename}`\n\n"
                    f"```\n{content}\n```"
                )
            except UnicodeDecodeError:
                if reactions:
                    await reactions.set("x")
                await client.chat_update(
                    channel=channel_id,
                    ts=progress_ts,
                    text="Unsupported file format. Must be text-based (UTF-8).",
                )
                return

        except Exception as e:
            if reactions:
                await reactions.set("x")
            await client.chat_update(
                channel=channel_id,
                ts=progress_ts,
                text=f"Failed to process file: {str(e)[:200]}",
            )
            return

        # Process with Claude
        claude_integration = self.deps.get("claude_integration")
        if not claude_integration:
            if reactions:
                await reactions.set("x")
            await client.chat_update(
                channel=channel_id,
                ts=progress_ts,
                text="Claude integration not available. Check configuration.",
            )
            return

        current_dir = state["current_directory"]
        session_id = state.get("claude_session_id")

        verbose_level = self._get_verbose_level(user_id)
        tool_log: List[Dict[str, Any]] = []
        start_time = time.time()
        on_stream = self._make_stream_callback(
            verbose_level,
            client,
            channel_id,
            progress_ts,
            tool_log,
            start_time,
            reactions=reactions,
        )

        success = True
        try:
            claude_response = await claude_integration.run_command(
                prompt=prompt,
                working_directory=current_dir,
                user_id=user_id,
                session_id=session_id,
                on_stream=on_stream,
            )

            state["claude_session_id"] = claude_response.session_id

            response_text = claude_response.content or "(no response)"

        except Exception as e:
            success = False
            logger.error("Claude file processing failed", error=str(e), user_id=user_id)
            response_text = f"Error: {str(e)[:500]}"

        # Set final reaction
        if reactions:
            await reactions.set("white_check_mark" if success else "x")

        # Delete progress message and send final response
        try:
            await client.chat_delete(channel=channel_id, ts=progress_ts)
        except Exception:
            pass

        # Format (Markdown -> Slack mrkdwn, clean whitespace, split long messages)
        formatter = ResponseFormatter(self.settings)
        formatted = formatter.format_claude_response(response_text)

        for i, msg in enumerate(formatted):
            await slack_api_call(
                client.chat_postMessage,
                channel=channel_id,
                thread_ts=thread_ts,
                text=msg.text,
            )
            if i < len(formatted) - 1:
                await asyncio.sleep(0.5)

    # --- Block Kit action handler ---

    async def handle_repo_action(self, ack: Callable, body: dict, client: Any) -> None:
        """Handle cd: button clicks — switch directory and resume session."""
        await ack()

        action = body["actions"][0]
        project_name = action["value"]
        user_id = body["user"]["id"]
        channel_id = body["channel"]["id"]

        state = self._get_user_state(user_id)
        base = self.settings.approved_directory
        new_path = base / project_name

        if not new_path.is_dir():
            await client.chat_postMessage(
                channel=channel_id,
                text=f"Directory not found: `{_escape_mrkdwn(project_name)}`",
            )
            return

        state["current_directory"] = new_path

        is_git = (new_path / ".git").is_dir()
        git_badge = " (git)" if is_git else ""

        # Update the original message to show selection
        message_ts = body.get("message", {}).get("ts")
        if message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=f"Switched to `{_escape_mrkdwn(project_name)}/`{git_badge}",
                blocks=[],  # Remove buttons
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                text=f"Switched to `{_escape_mrkdwn(project_name)}/`{git_badge}",
            )

        # Audit log
        audit_logger = self.deps.get("audit_logger")
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id,
                command="cd",
                args=[project_name],
                success=True,
            )

    @staticmethod
    def _split_message(text: str, max_length: int) -> List[str]:
        """Split a long message into chunks, trying to break at newlines."""
        if len(text) <= max_length:
            return [text]

        chunks: List[str] = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break

            # Try to find a newline to break at
            split_pos = text.rfind("\n", 0, max_length)
            if split_pos < max_length // 2:
                # No good newline break, split at max_length
                split_pos = max_length

            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip("\n")

        return chunks
