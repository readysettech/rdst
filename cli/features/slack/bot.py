"""
Slack bot using Socket Mode.
"""

import logging
import signal
import sys
from typing import Optional

from .config import AgentConfig, SlackCredentials
from .handler import SlackEventHandler

logger = logging.getLogger(__name__)


class SlackBot:
    """Slack bot using Socket Mode for real-time messaging."""

    def __init__(
        self,
        agent_config: AgentConfig,
        credentials: SlackCredentials,
    ):
        """
        Initialize the Slack bot.

        Args:
            agent_config: Configuration for this agent.
            credentials: Slack OAuth credentials.
        """
        self.agent_config = agent_config
        self.credentials = credentials
        self.event_handler = SlackEventHandler(agent_config)

        self._app = None
        self._socket_handler = None
        self._running = False

    def _ensure_slack_bolt(self) -> None:
        """Ensure slack_bolt is installed."""
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            raise ImportError(
                "slack-bolt is required for Slack integration.\n"
                "Install with: pip install rdst[slack]\n"
                "Or: pip install slack-bolt slack-sdk"
            )

    def _create_app(self) -> None:
        """Create the Slack Bolt app and register handlers."""
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        # Create Bolt app with bot token
        self._app = App(
            token=self.credentials.bot_token,
            # Disable token verification for Socket Mode
            token_verification_enabled=False,
        )

        # Register event handlers
        @self._app.event("app_mention")
        def handle_mention(event, say, logger):
            """Handle @bot mentions in channels."""
            try:
                self.event_handler.handle_mention(event, say)
            except Exception as e:
                logger.exception("Error handling mention")
                say(f":x: Error: {e}")

        @self._app.event("message")
        def handle_message(event, say, logger):
            """Handle direct messages."""
            # Only handle DMs (channel_type == 'im')
            if event.get("channel_type") == "im":
                try:
                    self.event_handler.handle_dm(event, say)
                except Exception as e:
                    logger.exception("Error handling DM")
                    say(f":x: Error: {e}")

        # Create Socket Mode handler with app token
        self._socket_handler = SocketModeHandler(
            app=self._app,
            app_token=self.credentials.app_token,
        )

    def start(self) -> None:
        """
        Start the bot (blocking).

        This method blocks until stop() is called or SIGINT/SIGTERM is received.
        """
        self._ensure_slack_bolt()
        self._create_app()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self._running = True

        try:
            logger.info(
                f"Starting Slack bot '{self.agent_config.name}' "
                f"(workspace: {self.credentials.workspace_name or self.credentials.workspace_id})"
            )
            # Start Socket Mode (blocks until closed)
            self._socket_handler.start()
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            logger.info("Slack bot stopped")

    def stop(self) -> None:
        """Stop the bot gracefully."""
        if self._socket_handler:
            logger.info("Stopping Slack bot...")
            self._socket_handler.close()
        self._running = False

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)

    @property
    def is_running(self) -> bool:
        """Check if the bot is running."""
        return self._running


def validate_credentials(credentials: SlackCredentials) -> tuple[bool, str]:
    """
    Validate Slack credentials by making a test API call.

    Args:
        credentials: The credentials to validate.

    Returns:
        Tuple of (is_valid, message).
    """
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        client = WebClient(token=credentials.bot_token)

        # Test auth
        response = client.auth_test()

        if response["ok"]:
            return True, f"Connected to workspace: {response.get('team', 'Unknown')}"
        else:
            return False, f"Auth failed: {response.get('error', 'Unknown error')}"

    except SlackApiError as e:
        return False, f"Slack API error: {e.response['error']}"
    except ImportError:
        return False, "slack-sdk not installed. Run: pip install rdst[slack]"
    except Exception as e:
        return False, f"Error: {e}"


def validate_app_token(app_token: str) -> tuple[bool, str]:
    """
    Validate an app-level token.

    Args:
        app_token: The app token (xapp-...) to validate.

    Returns:
        Tuple of (is_valid, message).
    """
    if not app_token.startswith("xapp-"):
        return False, "App token should start with 'xapp-'"

    # We can't easily validate app tokens without connecting,
    # so just do basic format check
    if len(app_token) < 50:
        return False, "App token seems too short"

    return True, "App token format looks valid"
