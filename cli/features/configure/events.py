"""Configure feature events."""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union


@dataclass
class ConfigureStatusEvent:
    type: Literal["status"]
    message: str


@dataclass
class ConfigureTargetListEvent:
    type: Literal["target_list"]
    targets: List[Dict[str, Any]]
    default_target: Optional[str] = None


@dataclass
class ConfigureTargetDetailEvent:
    type: Literal["target_detail"]
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


@dataclass
class ConfigureConnectionTestEvent:
    type: Literal["connection_test"]
    target_name: str
    status: Literal["in_progress", "success", "failed"]
    message: Optional[str] = None
    server_version: Optional[str] = None


@dataclass
class ConfigureSuccessEvent:
    type: Literal["success"]
    operation: str
    target_name: Optional[str] = None
    message: Optional[str] = None


@dataclass
class ConfigureErrorEvent:
    type: Literal["error"]
    message: str
    operation: Optional[str] = None
    target_name: Optional[str] = None


@dataclass
class ConfigureInputNeededEvent:
    type: Literal["input_needed"]
    prompt: str
    field_name: str
    field_type: str
    choices: Optional[List[str]] = None
    default: Optional[str] = None


ConfigureEvent = Union[
    ConfigureStatusEvent,
    ConfigureTargetListEvent,
    ConfigureTargetDetailEvent,
    ConfigureConnectionTestEvent,
    ConfigureSuccessEvent,
    ConfigureErrorEvent,
    ConfigureInputNeededEvent,
]
