"""Local systemd deployment — generate and execute systemd deployment script.

For local systemd deployment, we generate the script from template and
execute it directly. The script handles binary download, config, unit
file creation, and service start.
"""

from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
import tempfile
from typing import Any, Dict

from .script_generator import generate_script


def deploy_local_systemd(
    target_name: str,
    variables: Dict[str, str],
    password: str,
) -> Dict[str, Any]:
    """Deploy ReadySet locally as a systemd service.

    Generates the systemd deployment script from template, writes to a
    temp file, and executes it. The script handles everything: binary
    download, config, systemd unit, and service start.

    The password is passed via stdin to the script's `read -s` prompt.
    """
    # Check systemd is available
    try:
        systemctl_check = subprocess.run(
            ["systemctl", "--version"],
            capture_output=True,
            timeout=5,
        )
        if systemctl_check.returncode != 0:
            return {
                "success": False,
                "error": (
                    "systemd is not available on this system.\n"
                    "Use Docker mode instead: rdst cache deploy --target <name> --mode docker"
                ),
            }
    except FileNotFoundError:
        return {
            "success": False,
            "error": (
                "systemd is not available on this system.\n"
                "Use Docker mode instead: rdst cache deploy --target <name> --mode docker"
            ),
        }

    # Generate the script
    try:
        script = generate_script("systemd", variables)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    # Write to temp file and execute
    service_name = f"readyset-cache-{target_name}"
    script_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", prefix="rdst-deploy-", delete=False
        ) as f:
            f.write(script)
            script_path = f.name

        # Execute script, piping password to the read prompt
        result = subprocess.run(
            ["bash", script_path],
            input=f"{password}\n",
            capture_output=True,
            text=True,
            timeout=300,
        )

        # Print stdout for user feedback
        if result.stdout:
            print(result.stdout)

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            return {
                "success": False,
                "error": f"Systemd deployment failed:\n{error_msg}",
            }

        return {
            "success": True,
            "service_name": service_name,
            "port": variables["readyset_port"],
            "engine": variables["db_engine"],
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Deployment timed out (5 min). Check system logs.",
        }
    finally:
        if script_path:
            import os
            try:
                os.unlink(script_path)
            except OSError:
                pass
