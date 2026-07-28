"""Providers feature slice - cloud-provider sign-in and database discovery."""

from .auth import (
    detect_aws_credentials,
    get_botocore_session,
    get_rds_client,
    get_secretsmanager_client,
)
from .discovery import _ENGINE_MAP, _parse_instance, discover_aurora_cluster, discover_rds_instances
from .secrets import DEFAULT_TTL_SECONDS, clear_cache, resolve_secret
from .service import ACCOUNT_PROVIDERS, ProvidersService

__all__ = [
    "ACCOUNT_PROVIDERS",
    "DEFAULT_TTL_SECONDS",
    "ProvidersService",
    "_ENGINE_MAP",
    "_parse_instance",
    "clear_cache",
    "detect_aws_credentials",
    "discover_aurora_cluster",
    "discover_rds_instances",
    "get_botocore_session",
    "get_rds_client",
    "get_secretsmanager_client",
    "resolve_secret",
]
