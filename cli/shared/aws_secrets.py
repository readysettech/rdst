"""Shared AWS Secrets Manager resolver with TTL caching."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_cache: Dict[str, Tuple[str, float]] = {}
DEFAULT_TTL_SECONDS = 900


def resolve_secret(
    secret_arn: str,
    secret_key: str = "password",
    region: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Optional[str]:
    cached = _cache.get(secret_arn)
    if cached:
        value, expiry = cached
        if time.time() < expiry:
            return value

    if not region:
        region = _extract_region_from_arn(secret_arn)
    if not region:
        region = "us-east-1"

    try:
        from features.fleet.auth import get_secretsmanager_client

        client = get_secretsmanager_client(region)
        response = client.get_secret_value(SecretId=secret_arn)

        secret_string = response.get("SecretString")
        if not secret_string:
            logger.warning("Secret %s has no SecretString", secret_arn)
            return None

        try:
            secret_data = json.loads(secret_string)
            value = secret_data.get(secret_key)
            if value is None:
                for alt_key in [
                    "password",
                    "Password",
                    "pass",
                    "db_password",
                    "master_password",
                ]:
                    if alt_key in secret_data:
                        value = secret_data[alt_key]
                        break
            if value is None:
                logger.warning(
                    "Key '%s' not found in secret %s. Available keys: %s",
                    secret_key,
                    secret_arn,
                    list(secret_data.keys()),
                )
                return None
        except json.JSONDecodeError:
            value = secret_string

        _cache[secret_arn] = (str(value), time.time() + ttl_seconds)
        return str(value)

    except ImportError:
        logger.error("botocore not installed. Install with: pip install botocore")
        return None
    except Exception as e:
        error_msg = str(e)
        if "AccessDeniedException" in error_msg or "not authorized" in error_msg.lower():
            logger.debug("Cannot access secret %s: check IAM permissions", secret_arn)
        elif "ResourceNotFoundException" in error_msg:
            logger.debug("Secret not found: %s", secret_arn)
        elif "ExpiredTokenException" in error_msg:
            logger.debug("AWS credentials expired for secret %s", secret_arn)
        else:
            logger.debug("Failed to resolve secret %s: %s", secret_arn, e)
        return None


def clear_cache():
    _cache.clear()


def _extract_region_from_arn(arn: str) -> Optional[str]:
    match = re.match(r"arn:aws:secretsmanager:([a-z0-9-]+):", arn)
    return match.group(1) if match else None
