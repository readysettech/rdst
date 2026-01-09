"""
Slack event handler - routes messages to Ask3Engine.
"""

import logging
import os
import re
from typing import Any, Callable, Optional

from .config import AgentConfig
from .formatter import SlackFormatter

logger = logging.getLogger(__name__)


class SlackEventHandler:
    """Handles Slack events and routes to Ask3Engine."""

    def __init__(self, agent_config: AgentConfig):
        """
        Initialize the event handler.

        Args:
            agent_config: Configuration for this agent.
        """
        self.config = agent_config
        self.formatter = SlackFormatter()
        self._target_config: Optional[dict] = None
        self._db_type: Optional[str] = None

    def handle_mention(self, event: dict, say: Callable) -> None:
        """
        Handle @bot mention in a channel.

        Args:
            event: Slack event payload.
            say: Function to send a message.
        """
        text = event.get("text", "")
        question = self._extract_question(text)

        if not question:
            say(self.formatter.format_help())
            return

        self._process_question(question, event, say)

    def handle_dm(self, event: dict, say: Callable) -> None:
        """
        Handle direct message to the bot.

        Args:
            event: Slack event payload.
            say: Function to send a message.
        """
        # Ignore messages from bots (including ourselves)
        if event.get("bot_id"):
            return

        text = event.get("text", "")
        if not text.strip():
            say(self.formatter.format_help())
            return

        self._process_question(text.strip(), event, say)

    def _process_question(
        self, question: str, event: dict, say: Callable
    ) -> None:
        """
        Process a natural language question.

        Args:
            question: The user's question.
            event: Slack event payload.
            say: Function to send a message.
        """
        # Load target config if not already loaded
        if self._target_config is None:
            self._load_target_config()

        if self._target_config is None:
            say(
                self.formatter._error_block(
                    f"Database target '{self.config.target}' not found. "
                    "Please check your rdst configuration."
                )
            )
            return

        try:
            # Import Ask3Engine here to avoid circular imports
            from ..engines.ask3 import Ask3Engine

            # Create engine with no presenter (we format ourselves)
            engine = Ask3Engine(presenter=None)

            # Run the query
            ctx = engine.run(
                question=question,
                target=self.config.target,
                target_config=self._target_config,
                db_type=self._db_type or "postgresql",
                max_retries=1,  # Limit retries for responsiveness
                timeout_seconds=self.config.timeout_seconds,
                max_rows=self.config.max_rows,
                verbose=False,
                no_interactive=True,  # Never prompt in Slack
                agent_mode=False,  # Use fast linear flow
            )

            # Format and send response
            response = self.formatter.format_response(ctx)
            say(response)

        except Exception as e:
            logger.exception("Error processing question")
            say(self.formatter.format_error(e))

    def _extract_question(self, text: str) -> str:
        """
        Extract the question from a mention message.

        Removes the @mention and any leading/trailing whitespace.

        Args:
            text: Raw message text (e.g., "<@U123> how many users?")

        Returns:
            Extracted question.
        """
        # Remove user mentions (<@U123456>)
        cleaned = re.sub(r"<@[A-Z0-9]+>", "", text)
        # Remove extra whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    def _load_target_config(self) -> None:
        """Load the database target configuration."""
        try:
            # Import here to avoid circular imports
            from ..cli.rdst_cli import TargetsConfig

            config = TargetsConfig()
            config.load()

            target_data = config.get(self.config.target)
            if target_data:
                self._target_config = target_data
                self._db_type = target_data.get("engine", "postgresql")
            else:
                logger.error(f"Target '{self.config.target}' not found in config")

        except Exception as e:
            logger.exception("Error loading target config")
            self._target_config = None
