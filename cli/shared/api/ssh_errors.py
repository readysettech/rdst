"""Structured, user-facing SSH errors for API surfaces."""

from __future__ import annotations

import os
import re
from typing import Any


_TLS_VERIFICATION_FAILURE = re.compile(
    r"certificate verify failed|certificate verification failed|"
    r"server certificate.*(?:does not match|mismatch)|hostname mismatch|"
    r"unable to get local issuer certificate|self[- ]signed certificate|"
    r"unknown ca|certificate has expired|certificate is not yet valid|"
    r"root certificate file .* does not exist",
    re.IGNORECASE,
)

TARGET_PASSWORD_REQUIRED_CATEGORY = "target_password_required"
TARGET_PASSWORD_REQUIRED_CODE = "TARGET_PASSWORD_REQUIRED"
_PASSWORD_REQUIRED_FAILURE = re.compile(
    r"password not available for target|password authentication failed|"
    r"authentication failed for user|access denied for user|access denied|"
    r"invalid password|using password:\s*no",
    re.IGNORECASE,
)


def _exception_text(error: object) -> str:
    """Collect driver wrapper and underlying TLS exception messages."""
    parts: list[str] = []
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        parts.append(str(current))
        for attribute in ("original_exception", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if nested is not None:
                pending.append(nested)
    return "\n".join(parts)


def tls_verification_error_payload(
    error: object,
    target_name: str,
) -> dict[str, str] | None:
    """Return actionable guidance for database TLS identity/chain failures."""
    if not _TLS_VERIFICATION_FAILURE.search(_exception_text(error)):
        return None
    return {
        "category": "tls_verification_failed",
        "message": (
            "The database server's TLS certificate could not be verified. "
            "Provide your database provider's CA bundle in the certificate "
            "(tls_ca) field, and confirm the database hostname matches the "
            "certificate. Cloud providers publish CA bundles in their database "
            "TLS/SSL documentation."
        ),
        "target_name": target_name,
    }


def ssh_error_category(exc: Exception) -> str:
    """Map typed tunnel failures to stable API categories."""
    from shared.ssh_tunnel import (
        SshAuthenticationError,
        SshConnectionError,
        SshKeyError,
        SshPassphraseRequired,
    )

    if isinstance(exc, SshPassphraseRequired):
        return "ssh_passphrase_required"
    if isinstance(exc, SshKeyError):
        return "ssh_key_missing"
    if isinstance(exc, SshAuthenticationError):
        return "ssh_auth_failed"
    if isinstance(exc, SshConnectionError):
        return "ssh_jump_unreachable"
    return "ssh_tunnel_error"


def ssh_error_payload(
    exc: Exception,
    target_name: str,
    ssh_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build a safe, actionable payload for a typed tunnel failure."""
    category = ssh_error_category(exc)
    ssh = ssh_config or {}
    jump_host = str(ssh.get("host") or "the configured jump host")
    jump_port = ssh.get("port", 22)
    configured_key_path = ssh.get("key_path")
    key_path = str(
        getattr(exc, "key_path", None)
        or (
            os.path.expandvars(os.path.expanduser(str(configured_key_path)))
            if configured_key_path
            else "the configured SSH key"
        )
    )

    if category == "ssh_passphrase_required":
        message = (
            f"SSH key {key_path} needs a passphrase. Add it to ssh-agent with "
            f"'ssh-add {key_path}' and try again."
        )
    elif category == "ssh_key_missing":
        message = (
            f"SSH key not found: {key_path}. Choose an existing private key "
            f"for jump host {jump_host}."
        )
    elif category == "ssh_auth_failed":
        message = (
            f"SSH authentication failed for jump host {jump_host}. Check the SSH "
            "user and key, and confirm the public key is authorized on the jump host."
        )
    elif category == "ssh_jump_unreachable":
        message = (
            f"SSH jump host {jump_host}:{jump_port} is unreachable. Check its "
            "hostname, port, VPN, and firewall rules."
        )
    else:
        detail = str(exc).splitlines()[0][:300] if str(exc) else "Tunnel setup failed"
        message = f"SSH tunnel setup failed for target '{target_name}': {detail}"

    return {
        "category": category,
        "message": message,
        "target_name": target_name,
    }


def connectivity_error_payload(
    error: object,
    target_name: str,
    target_config: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Categorize a target-connectivity failure without misclassifying SQL/AI errors."""
    target_config = target_config or {}
    from shared.ssh_tunnel import SshTunnelError

    if isinstance(error, SshTunnelError):
        return ssh_error_payload(error, target_name, target_config.get("ssh"))

    tls_payload = tls_verification_error_payload(error, target_name)
    if tls_payload:
        return tls_payload

    from shared.password_resolver import resolve_password

    if (
        not resolve_password(target_config).available
        and _PASSWORD_REQUIRED_FAILURE.search(_exception_text(error))
    ):
        return {
            "code": TARGET_PASSWORD_REQUIRED_CODE,
            "category": TARGET_PASSWORD_REQUIRED_CATEGORY,
            "message": (
                f"No password is available for target '{target_name}' — open its "
                "connection settings and enter the database password."
            ),
            "target_name": target_name,
        }

    from features.allowlist.service import (
        _NETWORK_FAILURE,
        connection_failure_category,
        provider_network_hint,
    )

    provider_category = connection_failure_category(target_config, error)
    if provider_category:
        return {
            "category": provider_category,
            "message": (
                f"The database connection for '{target_name}' may be blocked. "
                f"{provider_network_hint(target_config)}"
            ),
            "target_name": target_name,
        }

    if target_config.get("ssh") or _NETWORK_FAILURE.search(str(error)):
        if target_config.get("ssh"):
            message = (
                f"The SSH tunnel opened for '{target_name}', but the database is "
                "unreachable through it. Check the database host, "
                "port, network rules, and credentials."
            )
        else:
            message = f"The database connection for '{target_name}' failed."
        return {
            "category": "database_connection_failed",
            "message": message,
            "target_name": target_name,
        }
    return None


__all__ = [
    "TARGET_PASSWORD_REQUIRED_CATEGORY",
    "TARGET_PASSWORD_REQUIRED_CODE",
    "connectivity_error_payload",
    "ssh_error_category",
    "ssh_error_payload",
    "tls_verification_error_payload",
]
