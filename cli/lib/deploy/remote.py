"""Remote SSH deployment — SCP script + execute with streaming output.

Uses subprocess + ssh/scp (not paramiko) to respect user's ~/.ssh/config
and ssh-agent.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
import sys
import tempfile
from typing import Any, Dict, Optional

from .script_generator import generate_script


def deploy_remote(
    target_name: str,
    variables: Dict[str, str],
    password: str = "",
    mode: str = "docker",
    host: str = "",
    ssh_key: Optional[str] = None,
    ssh_user: str = "root",
) -> Dict[str, Any]:
    """Deploy ReadySet to a remote host via SSH.

    Steps:
    1. Generate deployment script from template
    2. Patch script to inject password (avoid interactive read -s over SSH)
    3. SCP script to remote host
    4. SSH execute with real-time output streaming
    5. Clean up remote script
    """
    if not host:
        return {"success": False, "error": "Remote host is required."}

    if mode == "kubernetes":
        return {
            "success": False,
            "error": "Kubernetes deployment doesn't use SSH. Remove --host flag.",
        }

    # Build SSH args
    ssh_opts = _build_ssh_opts(ssh_key)
    remote_dest = f"{ssh_user}@{host}"

    # 1. Test SSH connectivity
    conn_result = _test_connection(remote_dest, ssh_opts)
    if not conn_result["success"]:
        return conn_result

    # 2. Generate script
    try:
        script = generate_script(mode, variables)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    # Save original (without password) for management script upload later
    original_script = script

    # 3. Patch script to inject password (replace the interactive read prompt)
    if password:
        script = _inject_password(script, password)

    # 4. SCP script to remote
    remote_script_path = f"/tmp/rdst-deploy-{target_name}.sh"

    scp_result = _scp_script(script, remote_dest, remote_script_path, ssh_opts)
    if not scp_result["success"]:
        return scp_result

    # 5. Execute remotely with streaming output
    exec_result = _execute_remote(remote_dest, remote_script_path, ssh_opts)

    # 6. Re-upload the original script (with read -s prompt, NOT the
    #    password-injected version) as a management script the user can run
    mgmt_path = f"/opt/rdst/deploy-{target_name}.sh"
    try:
        subprocess.run(
            ["ssh", *ssh_opts, remote_dest, "mkdir", "-p", "/opt/rdst"],
            capture_output=True, timeout=10,
        )
        _scp_script(original_script, remote_dest, mgmt_path, ssh_opts)
        subprocess.run(
            ["ssh", *ssh_opts, remote_dest, "chmod", "+x", mgmt_path],
            capture_output=True, timeout=10,
        )
        exec_result["management_script"] = mgmt_path
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError, FileNotFoundError):
        pass  # Best effort — management script upload is optional

    # 7. Clean up the password-injected temp script
    _cleanup_remote(remote_dest, remote_script_path, ssh_opts)

    return exec_result


def _inject_password(script: str, password: str) -> str:
    """Replace the interactive password prompt with a pre-set variable.

    The templates use `read -s -p "..." DB_PASSWORD` for interactive use.
    For remote SSH execution (no TTY), we replace that block with a direct
    assignment. Uses shlex.quote to prevent shell injection.
    """
    escaped = shlex.quote(password)
    replacement = f"DB_PASSWORD={escaped}"

    if 'read -s' not in script:
        return script

    lines = script.split('\n')
    new_lines = []
    skip_until_fi = False
    injected = False
    for line in lines:
        if 'read -s' in line and 'DB_PASSWORD' in line:
            new_lines.append(replacement)
            skip_until_fi = True
            injected = True
            continue
        if skip_until_fi:
            if line.strip() == 'fi':
                skip_until_fi = False
            continue
        new_lines.append(line)

    if not injected:
        raise RuntimeError(
            "Could not inject password into deployment script. "
            "Template may have changed — 'read -s' with DB_PASSWORD not found."
        )

    return '\n'.join(new_lines)


def _build_ssh_opts(ssh_key: Optional[str]) -> list:
    """Build common SSH options list."""
    opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
    ]
    if ssh_key:
        opts.extend(["-i", ssh_key])
    return opts


def _test_connection(remote_dest: str, ssh_opts: list) -> Dict[str, Any]:
    """Test SSH connectivity to remote host."""
    try:
        result = subprocess.run(
            ["ssh", *ssh_opts, remote_dest, "echo", "ok"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            error = result.stderr.strip()
            return {
                "success": False,
                "error": f"Cannot connect to {remote_dest}:\n{error}",
            }
        return {"success": True}
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"SSH connection to {remote_dest} timed out.",
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "ssh command not found. Install OpenSSH client.",
        }


def _scp_script(
    script: str,
    remote_dest: str,
    remote_path: str,
    ssh_opts: list,
) -> Dict[str, Any]:
    """Write script to temp file and SCP to remote host."""
    local_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", prefix="rdst-deploy-", delete=False
        ) as f:
            f.write(script)
            local_path = f.name

        result = subprocess.run(
            ["scp", *ssh_opts, local_path, f"{remote_dest}:{remote_path}"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Failed to copy script to {remote_dest}:\n{result.stderr.strip()}",
            }

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "SCP timed out."}
    finally:
        if local_path:
            try:
                os.unlink(local_path)
            except OSError:
                pass


def _execute_remote(
    remote_dest: str,
    remote_script_path: str,
    ssh_opts: list,
) -> Dict[str, Any]:
    """Execute the deployment script remotely with real-time output streaming."""
    try:
        proc = subprocess.Popen(
            ["ssh", *ssh_opts, remote_dest, "bash", remote_script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Stream output line by line
        output_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            print(line)
            sys.stdout.flush()
            output_lines.append(line)

        proc.wait(timeout=300)

        if proc.returncode != 0:
            return {
                "success": False,
                "error": f"Remote deployment failed (exit code {proc.returncode}).",
                "output": "\n".join(output_lines),
            }

        return {
            "success": True,
            "output": "\n".join(output_lines),
        }

    except subprocess.TimeoutExpired:
        proc.kill()
        return {"success": False, "error": "Remote execution timed out (5 min)."}


def _cleanup_remote(
    remote_dest: str,
    remote_script_path: str,
    ssh_opts: list,
) -> None:
    """Remove deployment script from remote host (best effort, removes password)."""
    try:
        subprocess.run(
            ["ssh", *ssh_opts, remote_dest, "rm", "-f", remote_script_path],
            capture_output=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as e:
        print(f"Warning: could not remove temp script {remote_script_path} from remote host: {e}")
