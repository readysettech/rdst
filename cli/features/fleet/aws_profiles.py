"""Create AWS CLI profiles for RDST's local SSO login flow."""

from __future__ import annotations

import configparser
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidAwsProfile(ValueError):
    """Raised when profile input is unsafe or malformed."""


class AwsProfileExists(RuntimeError):
    """Raised rather than overwriting an existing profile or SSO session."""


def aws_config_path() -> Path:
    configured = os.environ.get("AWS_CONFIG_FILE")
    return Path(configured).expanduser() if configured else Path.home() / ".aws" / "config"


def _validate_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise InvalidAwsProfile("sso_start_url must be an https:// URL")


def create_sso_profile(
    *,
    name: str,
    sso_start_url: str,
    sso_region: str,
    sso_account_id: str,
    sso_role_name: str,
    region: str,
) -> dict[str, object]:
    """Persist a modern sso-session plus its associated named profile."""
    if PROFILE_NAME_RE.fullmatch(name) is None:
        raise InvalidAwsProfile(
            "name must contain 1-64 letters, numbers, underscores, or hyphens"
        )
    _validate_https_url(sso_start_url)

    path = aws_config_path()
    parser = configparser.RawConfigParser(interpolation=None)
    if path.exists():
        with path.open("r", encoding="utf-8") as config_file:
            parser.read_file(config_file)

    profile_section = f"profile {name}"
    session_section = f"sso-session {name}"
    if (
        parser.has_section(profile_section)
        or (name == "default" and parser.has_section("default"))
        or parser.has_section(session_section)
    ):
        raise AwsProfileExists(f"AWS profile '{name}' already exists")

    parser[session_section] = {
        "sso_start_url": sso_start_url,
        "sso_region": sso_region,
        "sso_registration_scopes": "sso:account:access",
    }
    parser[profile_section] = {
        "sso_session": name,
        "sso_account_id": sso_account_id,
        "sso_role_name": sso_role_name,
        "region": region,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    previous_mode = path.stat().st_mode & 0o777 if existed else 0o600
    fd, temporary_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".aws-config.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as config_file:
            parser.write(config_file)
        os.chmod(temporary_path, previous_mode)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise

    return {"created": True, "profile": name}
