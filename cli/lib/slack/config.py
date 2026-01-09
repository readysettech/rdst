"""
Slack configuration and credential management.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tomli
except ImportError:
    import tomllib as tomli  # Python 3.11+

try:
    import tomli_w
except ImportError:
    tomli_w = None


SLACK_CONFIG_DIR = Path.home() / ".rdst" / "slack"
CREDENTIALS_FILE = SLACK_CONFIG_DIR / "credentials.json"
AGENTS_DIR = SLACK_CONFIG_DIR / "agents"


@dataclass
class AgentConfig:
    """Configuration for a Slack agent."""

    name: str
    target: str  # Database target from ~/.rdst/config.toml
    workspace_id: str
    description: str = ""
    max_rows: int = 50
    timeout_seconds: int = 30

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        return cls(
            name=data["name"],
            target=data["target"],
            workspace_id=data["workspace_id"],
            description=data.get("description", ""),
            max_rows=data.get("max_rows", 50),
            timeout_seconds=data.get("timeout_seconds", 30),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "workspace_id": self.workspace_id,
            "description": self.description,
            "max_rows": self.max_rows,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class SlackCredentials:
    """OAuth credentials for a Slack workspace."""

    workspace_id: str
    bot_token: str  # xoxb-...
    app_token: str  # xapp-...
    workspace_name: str = ""
    installed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @classmethod
    def from_dict(cls, workspace_id: str, data: dict) -> "SlackCredentials":
        return cls(
            workspace_id=workspace_id,
            bot_token=data["bot_token"],
            app_token=data["app_token"],
            workspace_name=data.get("workspace_name", ""),
            installed_at=data.get("installed_at", datetime.utcnow().isoformat()),
        )

    def to_dict(self) -> dict:
        return {
            "bot_token": self.bot_token,
            "app_token": self.app_token,
            "workspace_name": self.workspace_name,
            "installed_at": self.installed_at,
        }


def ensure_slack_dirs() -> None:
    """Create Slack config directories if they don't exist."""
    SLACK_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)


def load_credentials(workspace_id: Optional[str] = None) -> dict[str, SlackCredentials]:
    """
    Load Slack credentials from ~/.rdst/slack/credentials.json.

    Args:
        workspace_id: If provided, return only credentials for this workspace.

    Returns:
        Dict mapping workspace_id to SlackCredentials.
    """
    if not CREDENTIALS_FILE.exists():
        return {}

    with open(CREDENTIALS_FILE, "r") as f:
        data = json.load(f)

    credentials = {}
    for wid, cred_data in data.items():
        if workspace_id and wid != workspace_id:
            continue
        credentials[wid] = SlackCredentials.from_dict(wid, cred_data)

    return credentials


def save_credentials(credentials: SlackCredentials) -> None:
    """
    Save or update credentials for a workspace.

    Args:
        credentials: The credentials to save.
    """
    ensure_slack_dirs()

    # Load existing credentials
    existing = {}
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE, "r") as f:
            existing = json.load(f)

    # Update with new credentials
    existing[credentials.workspace_id] = credentials.to_dict()

    # Save back
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(existing, f, indent=2)

    # Set restrictive permissions (owner read/write only)
    os.chmod(CREDENTIALS_FILE, 0o600)


def load_agent_config(agent_name: str) -> Optional[AgentConfig]:
    """
    Load agent configuration from ~/.rdst/slack/agents/<name>.toml.

    Args:
        agent_name: Name of the agent.

    Returns:
        AgentConfig if found, None otherwise.
    """
    agent_file = AGENTS_DIR / f"{agent_name}.toml"
    if not agent_file.exists():
        return None

    with open(agent_file, "rb") as f:
        data = tomli.load(f)

    return AgentConfig.from_dict(data)


def save_agent_config(config: AgentConfig) -> None:
    """
    Save agent configuration to ~/.rdst/slack/agents/<name>.toml.

    Args:
        config: The agent configuration to save.
    """
    ensure_slack_dirs()

    agent_file = AGENTS_DIR / f"{config.name}.toml"

    if tomli_w is None:
        # Fallback: write TOML manually
        lines = [
            f'name = "{config.name}"',
            f'target = "{config.target}"',
            f'workspace_id = "{config.workspace_id}"',
            f'description = "{config.description}"',
            f"max_rows = {config.max_rows}",
            f"timeout_seconds = {config.timeout_seconds}",
        ]
        with open(agent_file, "w") as f:
            f.write("\n".join(lines) + "\n")
    else:
        with open(agent_file, "wb") as f:
            tomli_w.dump(config.to_dict(), f)


def list_agents() -> list[AgentConfig]:
    """
    List all configured agents.

    Returns:
        List of AgentConfig objects.
    """
    if not AGENTS_DIR.exists():
        return []

    agents = []
    for agent_file in AGENTS_DIR.glob("*.toml"):
        agent_name = agent_file.stem
        config = load_agent_config(agent_name)
        if config:
            agents.append(config)

    return agents


def delete_agent(agent_name: str) -> bool:
    """
    Delete an agent configuration.

    Args:
        agent_name: Name of the agent to delete.

    Returns:
        True if deleted, False if not found.
    """
    agent_file = AGENTS_DIR / f"{agent_name}.toml"
    if agent_file.exists():
        agent_file.unlink()
        return True
    return False
