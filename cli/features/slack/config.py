"""
Slack configuration and credential management.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    import tomli
except ImportError:
    import tomllib as tomli  # Python 3.11+

try:
    import tomli_w
except ImportError:
    tomli_w = None

from pathlib import Path

from shared.constants import rdst_data_dir
from shared.persistence import update_json, write_text
from shared.secret_store_service import SecretStoreService


def slack_config_dir() -> Path:
    """Return the Slack config directory (``~/.rdst/slack``)."""
    return rdst_data_dir() / "slack"


def credentials_file() -> Path:
    """Return the Slack credentials file (``~/.rdst/slack/credentials.json``)."""
    return slack_config_dir() / "credentials.json"


def slack_agents_dir() -> Path:
    """Return the Slack agents directory (``~/.rdst/slack/agents``)."""
    return slack_config_dir() / "agents"


def _secret_store() -> SecretStoreService:
    return SecretStoreService(service_name="rdst-slack")


def _token_key(workspace_id: str, kind: str, version: str | None = None) -> str:
    suffix = f":{version}" if version else ""
    return f"{workspace_id}{suffix}:{kind}-token"


def _credential_version() -> str:
    return uuid.uuid4().hex


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
    installed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def from_dict(cls, workspace_id: str, data: dict) -> "SlackCredentials":
        return cls(
            workspace_id=workspace_id,
            bot_token=data["bot_token"],
            app_token=data["app_token"],
            workspace_name=data.get("workspace_name", ""),
            installed_at=data.get(
                "installed_at", datetime.now(timezone.utc).isoformat()
            ),
        )

    def to_dict(self) -> dict:
        return {
            "workspace_name": self.workspace_name,
            "installed_at": self.installed_at,
        }


def ensure_slack_dirs() -> None:
    """Create Slack config directories if they don't exist."""
    slack_config_dir().mkdir(parents=True, exist_ok=True)
    slack_agents_dir().mkdir(parents=True, exist_ok=True)


def load_credentials(workspace_id: str | None = None) -> dict[str, SlackCredentials]:
    """
    Load Slack credentials from ~/.rdst/slack/credentials.json.

    Args:
        workspace_id: If provided, return only credentials for this workspace.

    Returns:
        Dict mapping workspace_id to SlackCredentials.
    """
    creds_file = credentials_file()
    if not creds_file.exists():
        return {}

    with open(creds_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    store = _secret_store()
    credentials = {}
    migrated_workspaces: set[str] = set()
    for wid, cred_data in data.items():
        if workspace_id and wid != workspace_id:
            continue
        version = cred_data.get("secret_version")
        bot_token = cred_data.get("bot_token") or store.get_secret(
            _token_key(wid, "bot", version)
        )
        app_token = cred_data.get("app_token") or store.get_secret(
            _token_key(wid, "app", version)
        )
        if not bot_token or not app_token:
            continue
        if "bot_token" in cred_data and store.is_available():
            new_version = _credential_version()
            bot_result = store.set_secret(
                _token_key(wid, "bot", new_version),
                bot_token,
                persist=True,
                apply_to_environment=False,
            )
            app_result = store.set_secret(
                _token_key(wid, "app", new_version),
                app_token,
                persist=True,
                apply_to_environment=False,
            )
            if bot_result.get("persisted") and app_result.get("persisted"):
                data[wid] = {
                    "workspace_name": cred_data.get("workspace_name", ""),
                    "installed_at": cred_data.get(
                        "installed_at", datetime.now(timezone.utc).isoformat()
                    ),
                    "secret_version": new_version,
                }
                migrated_workspaces.add(wid)
        credentials[wid] = SlackCredentials(
            workspace_id=wid,
            bot_token=bot_token,
            app_token=app_token,
            workspace_name=cred_data.get("workspace_name", ""),
            installed_at=cred_data.get(
                "installed_at", datetime.now(timezone.utc).isoformat()
            ),
        )

    if migrated_workspaces:

        def remove_plaintext_tokens(latest):
            for wid in migrated_workspaces:
                entry = latest.get(wid)
                if isinstance(entry, dict):
                    entry.pop("bot_token", None)
                    entry.pop("app_token", None)
                    entry["secret_version"] = data[wid]["secret_version"]
            return latest

        update_json(creds_file, remove_plaintext_tokens)
    return credentials


def save_credentials(credentials: SlackCredentials) -> None:
    """
    Save or update credentials for a workspace.

    Args:
        credentials: The credentials to save.
    """
    ensure_slack_dirs()

    creds_file = credentials_file()

    store = _secret_store()
    if not store.is_available():
        raise RuntimeError("Slack credentials require an available secure keyring")

    version = _credential_version()
    for kind, token in (("bot", credentials.bot_token), ("app", credentials.app_token)):
        result = store.set_secret(
            _token_key(credentials.workspace_id, kind, version),
            token,
            persist=True,
            apply_to_environment=False,
        )
        if not result.get("persisted"):
            raise RuntimeError("Failed to save Slack credentials in the secure keyring")

    def update(existing):
        existing[credentials.workspace_id] = {
            **credentials.to_dict(),
            "secret_version": version,
        }
        return existing

    update_json(creds_file, update)


def load_agent_config(agent_name: str) -> AgentConfig | None:
    """
    Load agent configuration from ~/.rdst/slack/agents/<name>.toml.

    Args:
        agent_name: Name of the agent.

    Returns:
        AgentConfig if found, None otherwise.
    """
    agent_file = slack_agents_dir() / f"{agent_name}.toml"
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

    agent_file = slack_agents_dir() / f"{config.name}.toml"

    if tomli_w is None:
        lines = [
            f'name = "{config.name}"',
            f'target = "{config.target}"',
            f'workspace_id = "{config.workspace_id}"',
            f'description = "{config.description}"',
            f"max_rows = {config.max_rows}",
            f"timeout_seconds = {config.timeout_seconds}",
        ]
        content = "\n".join(lines) + "\n"
    else:
        content = tomli_w.dumps(config.to_dict())
    write_text(agent_file, content)


def list_agents() -> list[AgentConfig]:
    """
    List all configured agents.

    Returns:
        List of AgentConfig objects.
    """
    agents_dir = slack_agents_dir()
    if not agents_dir.exists():
        return []

    agents = []
    for agent_file in agents_dir.glob("*.toml"):
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
    agent_file = slack_agents_dir() / f"{agent_name}.toml"
    if agent_file.exists():
        agent_file.unlink()
        return True
    return False
