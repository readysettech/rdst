"""AWS credential detection and authentication helpers."""

import os
from pathlib import Path
from typing import Optional, Tuple


def detect_aws_credentials() -> Tuple[bool, str]:
    """Detect if AWS credentials are available.

    Returns (has_creds, message) where message explains the status.
    """
    # Check environment variables
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True, "AWS credentials found via environment variables"

    if os.environ.get("AWS_SESSION_TOKEN"):
        return True, "AWS session credentials found via environment variables"

    # Check EC2 instance metadata (IMDS)
    if os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        return True, "AWS credentials available via ECS task role"

    # Try botocore directly — it handles SSO sessions, credential files,
    # instance profiles, and all other credential sources automatically
    try:
        import botocore.session
        session = botocore.session.get_session()
        # If AWS_PROFILE is set, use it
        profile = os.environ.get("AWS_PROFILE")
        if profile:
            session.set_config_variable("profile", profile)
        creds = session.get_credentials()
        if creds is not None:
            # Try to actually resolve them (SSO may need refresh)
            frozen = creds.get_frozen_credentials()
            if frozen and frozen.access_key:
                return True, f"AWS credentials available (profile: {profile or 'default'})"
    except Exception:
        pass

    # Check AWS credentials file
    creds_path = Path.home() / ".aws" / "credentials"
    if creds_path.exists():
        return True, f"AWS credentials file found: {creds_path}"

    # Check SSO config
    config_path = Path.home() / ".aws" / "config"
    if config_path.exists():
        try:
            content = config_path.read_text()
            if "sso_start_url" in content or "sso_session" in content:
                return False, (
                    "AWS SSO configured but may need login. Run:\n"
                    "  aws sso login"
                )
        except Exception:
            pass

    return False, (
        "No AWS credentials detected. Options:\n"
        "  1. Export env vars: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY\n"
        "  2. Configure AWS CLI: aws configure\n"
        "  3. Use AWS SSO: aws sso login\n"
        "  4. Use CSV import instead: rdst fleet import --from fleet.csv"
    )


def get_botocore_session(region: Optional[str] = None):
    """Get a botocore session. Raises ImportError if botocore not installed."""
    try:
        import botocore.session
    except ImportError:
        raise ImportError(
            "AWS features require botocore. Install with:\n"
            "  pip install botocore\n"
            "Or use CSV import instead:\n"
            "  rdst fleet import --from fleet.csv --password-env FLEET_PASS"
        )

    session = botocore.session.get_session()
    # Respect AWS_PROFILE for SSO-based authentication
    profile = os.environ.get("AWS_PROFILE")
    if profile:
        session.set_config_variable("profile", profile)
    return session


def get_rds_client(region: str):
    """Get a botocore RDS client for the given region."""
    session = get_botocore_session()
    return session.create_client("rds", region_name=region)


def get_secretsmanager_client(region: str):
    """Get a botocore Secrets Manager client."""
    session = get_botocore_session()
    return session.create_client("secretsmanager", region_name=region)
