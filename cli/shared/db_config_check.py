"""
Database configuration type definitions shared across commands and services.
"""

from typing import NotRequired, Required, TypedDict


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

