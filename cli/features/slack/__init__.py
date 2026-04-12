"""
rdst Slack integration module.

Provides Slack bot functionality for querying databases via natural language.
"""

from .config import (
    AgentConfig,
    SlackCredentials,
    delete_agent,
    list_agents,
    load_agent_config,
    load_credentials,
    save_agent_config,
    save_credentials,
)
from .manifest import generate_manifest, get_setup_instructions

__all__ = [
    "AgentConfig",
    "SlackCredentials",
    "delete_agent",
    "list_agents",
    "load_agent_config",
    "load_credentials",
    "save_agent_config",
    "save_credentials",
    "generate_manifest",
    "get_setup_instructions",
]
