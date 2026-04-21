"""Configure feature models.

Read this file first when you want to understand the configure feature:
inputs, operations, and the target data shapes all live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Union


class ConfigureOperation(str, Enum):
    LIST = "list"
    GET = "get"
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"
    SET_DEFAULT = "set_default"
    TEST = "test"


@dataclass
class ConfigureInput:
    """Input for configure service."""

    target_name: Optional[str] = None


@dataclass
class ConfigureOptions:
    """Options for configure service execution."""

    operation: str | ConfigureOperation = ConfigureOperation.LIST
    target_data: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class TargetConfigInput:
    engine: str = "postgresql"
    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    password_env: Optional[str] = None
    tls: bool = False
    read_only: bool = False


@dataclass(frozen=True)
class TargetSummary:
    name: str
    engine: str
    host: str
    port: int | str
    database: str
    proxy: str
    endpoint_verified: bool
    verified: bool
    has_password: bool
    is_default: bool


@dataclass(frozen=True)
class TargetDetail:
    target_name: str
    engine: str
    host: str
    port: int
    database: str
    user: str
    has_password: bool
    is_default: bool
    password_env: Optional[str] = None
    tls: bool = False
    read_only: bool = False
