"""
Slack App Manifest generation.
"""

import json
from typing import Optional


def generate_manifest(
    app_name: str = "RDST Database Agent",
    bot_name: str = "rdst",
    description: Optional[str] = None,
) -> dict:
    """
    Generate a Slack App Manifest for rdst.

    Args:
        app_name: Display name of the Slack app.
        bot_name: Display name of the bot user.
        description: App description.

    Returns:
        Manifest as a dictionary.
    """
    if description is None:
        description = "Query your database using natural language"

    return {
        "display_information": {
            "name": app_name,
            "description": description,
            "background_color": "#1a1a2e",
        },
        "features": {
            "bot_user": {
                "display_name": bot_name,
                "always_online": False,
            },
            "app_home": {
                "home_tab_enabled": False,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
        },
        "oauth_config": {
            "scopes": {
                "bot": [
                    "app_mentions:read",  # See when someone @mentions the bot
                    "chat:write",  # Send messages
                    "im:history",  # Read DM history for context
                    "im:read",  # See DM metadata
                    "im:write",  # Send DMs
                    "users:read",  # Get user info for formatting
                ]
            }
        },
        "settings": {
            "event_subscriptions": {
                "bot_events": [
                    "app_mention",  # Trigger on @mentions in channels
                    "message.im",  # Trigger on direct messages
                ]
            },
            "interactivity": {
                "is_enabled": False,  # No buttons/modals for now
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,  # Required for Socket Mode
            "token_rotation_enabled": False,
        },
    }


def manifest_to_json(manifest: dict, indent: int = 2) -> str:
    """
    Convert manifest to JSON string.

    Args:
        manifest: The manifest dictionary.
        indent: JSON indentation level.

    Returns:
        JSON string.
    """
    return json.dumps(manifest, indent=indent)


def manifest_to_yaml(manifest: dict) -> str:
    """
    Convert manifest to YAML string.

    Slack accepts both JSON and YAML for manifests.

    Args:
        manifest: The manifest dictionary.

    Returns:
        YAML string.
    """
    try:
        import yaml

        return yaml.dump(manifest, default_flow_style=False, sort_keys=False)
    except ImportError:
        # Fallback to JSON if PyYAML not installed
        return manifest_to_json(manifest)


def get_setup_instructions(agent_name: str = "data-agent") -> str:
    """
    Get human-readable setup instructions.

    Args:
        agent_name: Suggested name for the agent.

    Returns:
        Instructions string.
    """
    manifest = generate_manifest(bot_name=agent_name)
    manifest_json = manifest_to_json(manifest)

    return f"""
Slack Bot Setup
===============

Step 1: Create a Slack App
--------------------------
1. Go to: https://api.slack.com/apps
2. Click "Create New App"
3. Select "From an app manifest"
4. Choose your workspace
5. Paste this manifest (JSON format):

{manifest_json}

6. Click "Create"
7. Click "Install to Workspace"
8. Click "Allow"

Step 2: Enable Socket Mode
--------------------------
1. Go to "Socket Mode" in the left sidebar
2. Toggle "Enable Socket Mode" to ON
3. Create an App-Level Token:
   - Name: "rdst-socket"
   - Scope: "connections:write"
   - Click "Generate"
   - Copy the token (starts with xapp-)

Step 3: Get Your Bot Token
--------------------------
1. Go to "OAuth & Permissions" in the left sidebar
2. Copy the "Bot User OAuth Token" (starts with xoxb-)

Step 4: Save Your Tokens
------------------------
You'll need both tokens:
- Bot Token (xoxb-...): For sending messages
- App Token (xapp-...): For Socket Mode connection
"""
