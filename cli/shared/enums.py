"""Shared enums used across RDST features."""

from enum import Enum


class DatabaseEngine(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"


class QuerySource(str, Enum):
    CLI_INLINE = "cli_inline"
    CLI_FILE = "cli_file"
    CLI_STDIN = "cli_stdin"
    REGISTRY = "registry"
    WEB = "web"
    AUTO = "auto"

