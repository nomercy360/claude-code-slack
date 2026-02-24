"""Main Slack bot class.

Features:
- Slack Bolt AsyncApp with Socket Mode
- Middleware registration (auth, rate limit, security)
- Handler management via orchestrator
- Graceful shutdown
"""

import asyncio
from typing import Any, Callable, Dict, Optional

import structlog
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from ..config.settings import Settings
from ..exceptions import ClaudeCodeSlackError
from .features.registry import FeatureRegistry
from .orchestrator import MessageOrchestrator

logger = structlog.get_logger()


class ClaudeCodeBot:
    """Main bot orchestrator."""

    def __init__(self, settings: Settings, dependencies: Dict[str, Any]):
        """Initialize bot with settings and dependencies."""
        self.settings = settings
        self.deps = dependencies
        self.app: Optional[AsyncApp] = None
        self.socket_handler: Optional[AsyncSocketModeHandler] = None
        self.is_running = False
        self.feature_registry: Optional[FeatureRegistry] = None
        self.orchestrator = MessageOrchestrator(settings, dependencies)

    async def initialize(self) -> None:
        """Initialize bot application. Idempotent — safe to call multiple times."""
        if self.app is not None:
            return

        logger.info("Initializing Slack bot")

        # Create Slack Bolt async app
        self.app = AsyncApp(
            token=self.settings.slack_bot_token_str,
            signing_secret=self.settings.slack_signing_secret_str,
        )

        # Initialize feature registry
        self.feature_registry = FeatureRegistry(
            config=self.settings,
            storage=self.deps.get("storage"),
            security=self.deps.get("security"),
        )

        # Add feature registry to dependencies
        self.deps["features"] = self.feature_registry

        # Add middleware
        self._add_middleware()

        # Register handlers
        self._register_handlers()

        logger.info("Bot initialization complete")

    def _register_handlers(self) -> None:
        """Register handlers via orchestrator (mode-aware)."""
        self.orchestrator.register_handlers(self.app)

    def _add_middleware(self) -> None:
        """Add middleware to application.

        Slack Bolt middleware runs in registration order.
        Each middleware must call ``await next()`` to pass to the next one.
        """
        from .middleware.auth import auth_middleware
        from .middleware.rate_limit import rate_limit_middleware
        from .middleware.security import security_middleware

        # Wrap each middleware so it receives our dependencies dict
        self.app.middleware(
            self._create_middleware_handler(security_middleware)
        )
        self.app.middleware(
            self._create_middleware_handler(auth_middleware)
        )
        self.app.middleware(
            self._create_middleware_handler(rate_limit_middleware)
        )

        logger.info("Middleware added to bot")

    def _create_middleware_handler(self, middleware_func: Callable) -> Callable:
        """Create a Slack Bolt middleware that injects dependencies.

        Slack Bolt global middleware signature:
            async def mw(body, next, context, client, logger, ...)

        If the wrapped ``middleware_func`` does *not* call ``await next()``,
        the request is effectively rejected (subsequent middleware + handlers
        are skipped).
        """
        deps = self.deps
        settings = self.settings

        async def middleware_wrapper(
            body: dict,
            next: Callable,
            context: dict,
            client: Any,
            **kwargs: Any,
        ) -> None:
            # Extract user_id from the event payload
            user_id: Optional[str] = None
            if "event" in body:
                evt = body["event"]
                # Events with subtypes (message_changed, message_deleted, etc.)
                # are system events — pass them through without auth checks.
                subtype = evt.get("subtype")
                if subtype is not None:
                    await next()
                    return
                user_id = evt.get("user")
                # bot_id present → message from a bot; skip middleware
                if evt.get("bot_id"):
                    logger.debug(
                        "Skipping bot-originated event in middleware",
                        middleware=middleware_func.__name__,
                    )
                    return  # do NOT call next() → silently drop
            elif "user_id" in body:
                # Slash commands put user_id at body root
                user_id = body.get("user_id")

            # Build a "data" dict that mirrors what the old middleware
            # expected so we can reuse the same auth / rate-limit / security
            # logic with minimal changes.
            data: Dict[str, Any] = {**deps, "settings": settings}

            # Attach Slack-specific helpers so middleware can respond to users
            data["_slack_client"] = client
            data["_slack_body"] = body
            data["_slack_context"] = context
            data["_slack_user_id"] = user_id

            # Track whether the middleware allowed the request through
            handler_called = False

            async def dummy_next() -> None:
                nonlocal handler_called
                handler_called = True

            # Call the middleware function
            await middleware_func(dummy_next, body, data)

            if handler_called:
                await next()

        return middleware_wrapper

    async def start(self) -> None:
        """Start the bot via Socket Mode."""
        if self.is_running:
            logger.warning("Bot is already running")
            return

        await self.initialize()

        logger.info("Starting bot", mode="socket_mode")

        try:
            self.is_running = True

            self.socket_handler = AsyncSocketModeHandler(
                self.app, self.settings.slack_app_token_str
            )

            await self.socket_handler.start_async()

            # Keep running until manually stopped
            while self.is_running:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error running bot", error=str(e))
            raise ClaudeCodeSlackError(f"Failed to start bot: {str(e)}") from e
        finally:
            self.is_running = False

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        if not self.is_running:
            logger.warning("Bot is not running")
            return

        logger.info("Stopping bot")

        try:
            self.is_running = False

            # Shutdown feature registry
            if self.feature_registry:
                self.feature_registry.shutdown()

            if self.socket_handler:
                await self.socket_handler.close_async()

            logger.info("Bot stopped successfully")
        except Exception as e:
            logger.error("Error stopping bot", error=str(e))
            raise ClaudeCodeSlackError(f"Failed to stop bot: {str(e)}") from e

    async def get_bot_info(self) -> Dict[str, Any]:
        """Get bot information."""
        if not self.app:
            return {"status": "not_initialized"}

        try:
            from slack_sdk.web.async_client import AsyncWebClient

            client: AsyncWebClient = self.app.client
            result = await client.auth_test()

            return {
                "status": "running" if self.is_running else "initialized",
                "bot_id": result.get("bot_id"),
                "user_id": result.get("user_id"),
                "team": result.get("team"),
                "team_id": result.get("team_id"),
                "url": result.get("url"),
            }
        except Exception as e:
            logger.error("Failed to get bot info", error=str(e))
            return {"status": "error", "error": str(e)}

    async def health_check(self) -> bool:
        """Perform health check."""
        try:
            if not self.app:
                return False

            await self.app.client.auth_test()
            return True
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            return False
