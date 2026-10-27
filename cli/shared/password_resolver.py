"""Unified password resolution for RDST targets."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping, NamedTuple, Optional, Union

from .aws_secrets import resolve_secret
from .config.targets import default_port_for, normalize_db_type
from .secret_store_service import SecretStoreService

logger = logging.getLogger(__name__)

# Credentials RDST reads or stores for itself, and common provider and cloud keys. A target whose
# password_env names one would send that credential to the target's server as its password.
_RESERVED_POINTERS = frozenset({
    "API_KEY", "ANTHROPIC_AUTH_TOKEN", "DIGITALOCEAN_TOKEN", "DIGITALOCEAN_ACCESS_TOKEN",
    "SUPABASE_ACCESS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "HF_TOKEN", "NPM_TOKEN",
    "VAULT_TOKEN",
})
_RESERVED_SUFFIXES = ("_API_KEY", "_SECRET_ACCESS_KEY", "_ACCESS_KEY_ID", "_OAUTH_TOKEN",
                      "_REFRESH_TOKEN", "_CLIENT_SECRET", "_BOT_TOKEN", "_APP_TOKEN")
# RDST's own settings and secrets, and the AWS credential chain. Database passwords kept under
# these prefixes (RDST_<TARGET>_PASSWORD, the per-target default) stay usable.
_RESERVED_PREFIXES = ("RDST_", "AWS_")
_PASSWORD_SUFFIXES = ("PASSWORD", "PASSWD", "_PASS", "_PWD")
_warned_reserved: set[str] = set()


# What derive_password_env and unique_password_env name: RDST_<TARGET>_PASSWORD, with _<n> after
# it when another target already has that name.
_RDST_POINTER = re.compile(r"RDST_[A-Z0-9_]+_PASSWORD(_[0-9]+)?")


def derive_password_env(name: str) -> str:
    """Return the conventional environment variable for a target password."""
    normalized = re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_")
    return f"RDST_{normalized or 'TARGET'}_PASSWORD"


def unique_password_env(name: str, taken: set[str]) -> str:
    """The conventional pointer for ``name``, numbered past the ones in ``taken``."""
    base = derive_password_env(name)
    pointer, suffix = base, 2
    while pointer in taken:
        pointer, suffix = f"{base}_{suffix}", suffix + 1
    return pointer


def implicit_password_envs(targets: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """The pointer password setup assigns each target that has no password source yet: its
    conventional one, numbered past every stored pointer and earlier assignment, in the order
    given (the config's target order)."""
    used = {str(config.get("password_env") or "").strip() for config in targets.values()}
    assigned: dict[str, str] = {}
    for name, config in targets.items():
        if (str(config.get("password_env") or "").strip() or config.get("password")
                or config.get("password_secret_arn")):
            continue
        assigned[name] = unique_password_env(name, used)
        used.add(assigned[name])
    return assigned


def password_env_owner(
    pointer: Optional[str],
    name: str,
    saved: Mapping[str, Mapping[str, Any]],
    *,
    secret_store: SecretStoreService | None = None,
) -> Optional[str]:
    """The saved target other than ``name`` whose password ``pointer`` holds, or None.

    That is one storing it as its password_env, or one with no password source whose implicit
    pointer it is once a secret is saved under it (password setup saves before it records the
    pointer). The same rule numbers new pointers and refuses ones a request picks.
    """
    if not pointer:
        return None
    others = {other: config for other, config in saved.items() if other != name}
    for other, config in others.items():
        if str(config.get("password_env") or "").strip() == pointer:
            return other
    claimant = next((other for other, implicit in implicit_password_envs(others).items()
                     if implicit == pointer), None)
    if claimant and (secret_store or SecretStoreService()).get_secret(pointer):
        return claimant
    return None


def new_password_env(target_name: str, config: Any = None) -> str:
    """The pointer to save a new password for ``target_name`` under: the conventional one,
    numbered past any that holds another saved target's password (a renamed target keeps its
    old pointer), so saving it never replaces another database's password. ``config`` is a
    loaded TargetsConfig; the saved one is read when omitted."""
    if config is None:
        from .config.targets import TargetsConfig

        config = TargetsConfig()
        try:
            config.load()
        except RuntimeError:
            return derive_password_env(target_name)
    saved = {other: config.get(other) or {} for other in config.list_targets()}
    base = derive_password_env(target_name)
    pointer, suffix = base, 2
    while password_env_owner(pointer, target_name, saved):
        pointer, suffix = f"{base}_{suffix}", suffix + 1
    return pointer


def is_rdst_password_env(name: Optional[str]) -> bool:
    """Whether a pointer has the shape RDST gives the passwords it saves for targets."""
    return bool(_RDST_POINTER.fullmatch(str(name or "")))


def is_reserved_password_env(name: Optional[str]) -> bool:
    """Whether a password pointer names a credential other than a database password."""
    folded = str(name or "").strip().upper()
    if not folded:
        return False
    # Keyring entries RDST keys by workspace or chunk (Slack tokens, long access tokens).
    if ":" in folded or "#" in folded:
        return True
    if folded in _RESERVED_POINTERS or folded.endswith(_RESERVED_SUFFIXES):
        return True
    # A numbered pointer (RDST_<TARGET>_PASSWORD_2) is still a password.
    unnumbered = re.sub(r"_[0-9]+$", "", folded)
    return folded.startswith(_RESERVED_PREFIXES) and not unnumbered.endswith(_PASSWORD_SUFFIXES)


def check_password_env(name: Optional[str]) -> None:
    """Raise ValueError for a password pointer that names another kind of credential."""
    if is_reserved_password_env(name):
        raise ValueError(
            f"'{name}' holds another credential, not a database password. Enter the database "
            "password instead, or name a variable that holds only that password."
        )


def credential_endpoint(target: Mapping[str, Any]) -> tuple:
    """Where a target's password is sent: engine, server address, user and jump host."""
    engine = normalize_db_type(target.get("engine")) or "postgresql"
    try:
        port = int(target.get("port") or default_port_for(engine))
    except (TypeError, ValueError):
        port = target.get("port")
    ssh = target.get("ssh") or {}
    return (
        engine,
        str(target.get("host") or "").strip().casefold(),
        port,
        str(target.get("user") or target.get("username") or "").strip(),
        str(ssh.get("profile") or "").casefold(),
        str(ssh.get("host") or "").strip().casefold(),
        str(ssh.get("port") or 22) if ssh.get("host") else "",
        str(ssh.get("user") or ""),
    )


def bind_target_credentials(
    name: str,
    proposed: dict[str, Any],
    existing: Optional[Mapping[str, Any]],
    saved: Mapping[str, Mapping[str, Any]],
    *,
    password_submitted: bool,
) -> dict[str, Any]:
    """``proposed`` limited to credentials saved for the server it connects to.

    For callers that cannot be trusted with the saved secrets (the local API, MCP). A target
    that moves to another server, user or jump host keeps its saved password only when the
    caller submits it again. Raises ValueError for a pointer that names another kind of
    credential, another saved database's password, or a new place to read this one's from.
    A pointer the target already has stays valid, including one RDST shares between a
    database and its Readyset cache.
    """
    result = dict(proposed)
    pointer = result.get("password_env") or None
    check_password_env(pointer)
    endpoint = credential_endpoint(result)
    introduced = pointer and (existing is None or pointer != existing.get("password_env"))
    # Users may share their own variable between databases; RDST's pointers are per database.
    owner = password_env_owner(pointer, name, saved) if (
        introduced and is_rdst_password_env(pointer)) else None
    if owner and credential_endpoint(saved[owner]) != endpoint:
        raise ValueError(f"'{pointer}' holds the password of {owner}.")
    if existing is None:
        return result
    saved_endpoint = credential_endpoint(existing)
    if saved_endpoint != endpoint:
        if not password_submitted:
            for field in ("password", "password_env"):
                result.pop(field, None)
        # A Secrets Manager secret is the database server's own (discovery found it on that
        # instance), so it stays while engine, host and port do, as when a jump host is added
        # to a private instance. A submitted password replaces it.
        if password_submitted or saved_endpoint[:3] != endpoint[:3]:
            for field in ("password_secret_arn", "password_secret_key"):
                result.pop(field, None)
    elif (pointer and pointer != existing.get("password_env")
          and pointer != derive_password_env(name) and not password_submitted):
        raise ValueError("Enter the password to read it from a different place.")
    return result


def store_target_password(
    target_name: str,
    password: str | None,
    password_env: str | None = None,
    *,
    secret_store: SecretStoreService | None = None,
    config: Any = None,
) -> str | None:
    """Store a submitted target password and return its config pointer.

    Explicit pointers remain power-user overrides. A password submitted without
    one gets the target's own new pointer (see new_password_env, which reads
    ``config``); an absent password and pointer leave the target passwordless.
    """
    check_password_env(password_env)
    if password is None:
        return password_env or None

    resolved_env = password_env or new_password_env(target_name, config)
    (secret_store or SecretStoreService()).set_secret(
        resolved_env,
        password,
        persist=True,
    )
    return resolved_env


class PasswordResolution(NamedTuple):
    available: bool
    source: str


def _extract_password_fields(
    target_config: Union[dict, Any],
) -> tuple[str | None, str | None]:
    if isinstance(target_config, dict):
        password, password_env = target_config.get("password"), target_config.get("password_env")
    else:
        password = getattr(target_config, "password", None)
        password_env = getattr(target_config, "password_env", None)
    if is_reserved_password_env(password_env):
        if password_env not in _warned_reserved:
            _warned_reserved.add(password_env)
            logger.warning("Ignoring password_env %s: it names another kind of credential",
                           password_env)
        password_env = None
    return password, password_env


def resolve_password(
    target_config: Union[dict, Any],
    secret_store: SecretStoreService | None = None,
) -> PasswordResolution:
    password, password_env = _extract_password_fields(target_config)

    if password:
        return PasswordResolution(available=True, source="config")

    if password_env:
        if os.environ.get(password_env):
            return PasswordResolution(available=True, source="process_env")

        store = secret_store or SecretStoreService()
        if store.get_secret(password_env):
            return PasswordResolution(available=True, source="secure_store")

    secret_arn = (
        target_config.get("password_secret_arn")
        if isinstance(target_config, dict)
        else getattr(target_config, "password_secret_arn", None)
    )
    if secret_arn:
        return PasswordResolution(available=True, source="aws_secrets_manager")

    return PasswordResolution(available=False, source="missing")


def resolve_password_value(
    target_config: Union[dict, Any],
    secret_store: SecretStoreService | None = None,
) -> str:
    password, password_env = _extract_password_fields(target_config)

    if password:
        return password

    if password_env:
        env_val = os.environ.get(password_env)
        if env_val:
            return env_val

    secret_arn = (
        target_config.get("password_secret_arn")
        if isinstance(target_config, dict)
        else getattr(target_config, "password_secret_arn", None)
    )
    if secret_arn:
        try:
            secret_key = (
                target_config.get("password_secret_key", "password")
                if isinstance(target_config, dict)
                else getattr(target_config, "password_secret_key", "password")
            )
            secret_val = resolve_secret(secret_arn, secret_key=secret_key)
            if secret_val:
                if password_env:
                    os.environ[password_env] = secret_val
                return secret_val
        except Exception:
            pass

    if password_env:
        store = secret_store or SecretStoreService()
        secret_val = store.get_secret(password_env)
        if secret_val:
            os.environ[password_env] = secret_val
            return secret_val

    return ""
