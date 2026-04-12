"""
Database configuration type definitions shared across commands and services.
"""

from typing import TypedDict

try:
    # Python 3.11+
    from typing import NotRequired, Required
except ImportError:
    # Python 3.8-3.10
    from typing_extensions import NotRequired, Required


class TargetConfig(TypedDict):
    """Type definition for database target configuration."""

    engine: Required[str]
    host: Required[str]
    port: Required[int]
    database: Required[str]
    user: Required[str]

    password: NotRequired[str]
    password_env: NotRequired[str]
    tls: NotRequired[bool]
    proxy: NotRequired[str]
    name: NotRequired[str]

