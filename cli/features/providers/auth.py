"""AWS credential detection and authentication helpers."""

import os
from pathlib import Path
from typing import Optional, Tuple


def detect_aws_credentials(profile: Optional[str] = None) -> Tuple[bool, str]:
    """Detect if AWS credentials are available.

    Returns (has_creds, message) where message explains the status. When
    `profile` is given, credentials are resolved from that profile instead
    of the environment.
    """
    if profile is None:
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
        # Explicit profile wins over AWS_PROFILE
        profile = profile or os.environ.get("AWS_PROFILE")
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
            content = config_path.read_text(encoding="utf-8")
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


def get_botocore_session(region: Optional[str] = None, profile: Optional[str] = None):
    """Get a botocore session. Raises ImportError if botocore not installed.

    An explicit `profile` overrides AWS_PROFILE; otherwise AWS_PROFILE is
    respected for SSO-based authentication.
    """
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
    profile = profile or os.environ.get("AWS_PROFILE")
    if profile:
        session.set_config_variable("profile", profile)
    return session


def get_rds_client(region: str, profile: Optional[str] = None):
    """Get a botocore RDS client for the given region."""
    session = get_botocore_session(profile=profile)
    return session.create_client("rds", region_name=region)


def _credential_method(method: Optional[str]) -> Optional[str]:
    """Map botocore's credential method names to a coarse label."""
    if not method:
        return None
    if method == "env":
        return "env"
    if "sso" in method:
        return "sso"
    if "iam-role" in method or "instance" in method or "container" in method:
        return "instance"
    return "profile"


def get_aws_status(sts_timeout: float = 3.0, profile: Optional[str] = None) -> dict:
    """Summarize local AWS credential state without hanging.

    Lists profiles from ~/.aws config, then verifies the credentials for
    `profile` (falling back to AWS_PROFILE, then the default chain) with a
    short-timeout sts:GetCallerIdentity. Callers that let the user pick an
    SSO profile must pass it, or a fresh SSO session on that profile reads
    as signed-out. On any failure, has_credentials stays False but the
    profile list is still returned.
    """
    status: dict = {
        "has_credentials": False,
        "method": None,
        "identity_arn": None,
        "account": None,
        "active_profile": profile or os.environ.get("AWS_PROFILE"),
        "available_profiles": [],
        "region": None,
    }
    try:
        import botocore.session
        from botocore.config import Config
    except ImportError:
        status["detail"] = "boto3 is not installed"
        return status

    try:
        session = botocore.session.get_session()
        if status["active_profile"]:
            session.set_config_variable("profile", status["active_profile"])
        try:
            status["available_profiles"] = sorted(session.available_profiles)
        except Exception:
            pass
        try:
            status["region"] = session.get_config_variable("region")
        except Exception:
            pass

        creds = session.get_credentials()
        if creds is not None:
            status["method"] = _credential_method(getattr(creds, "method", None))

            sts = session.create_client(
                "sts",
                region_name=status["region"] or "us-east-1",
                config=Config(
                    connect_timeout=sts_timeout,
                    read_timeout=sts_timeout,
                    retries={"max_attempts": 1},
                ),
            )
            identity = sts.get_caller_identity()
            from .identity import capture_aws_identity_async

            capture_aws_identity_async(identity.get("Arn"))
            status["has_credentials"] = True
            status["identity_arn"] = identity.get("Arn")
            status["account"] = identity.get("Account")
    except Exception:
        pass

    if not status["has_credentials"] and profile is None:
        # The default chain came up empty, but a signed-in SSO profile may
        # still exist. Report the first profile whose session verifies, so
        # profile-less callers (preflight checks, status chips) agree with
        # the profile-aware sign-in flow instead of claiming signed-out.
        for candidate in status["available_profiles"][:8]:
            candidate_status = get_aws_status(sts_timeout=sts_timeout, profile=candidate)
            if candidate_status["has_credentials"]:
                return candidate_status
    return status


def get_secretsmanager_client(region: str):
    """Get a botocore Secrets Manager client."""
    session = get_botocore_session()
    return session.create_client("secretsmanager", region_name=region)
