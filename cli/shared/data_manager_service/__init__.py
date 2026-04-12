"""Shared data-manager-service package."""

from .data_manager_service import (
    CommandSetData,
    ConnectionConfig,
    DMSDbType,
    DataManagerQueryType,
    DataManagerService,
    get_db_type,
)
from .data_manager_service_command_sets import (
    COMMAND_SETS,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    SYSTEM_COMMAND_SETS,
)

__all__ = [
    "CommandSetData",
    "COMMAND_SETS",
    "ConnectionConfig",
    "DEFAULT_TIMEOUT",
    "DMSDbType",
    "DataManager",
    "DataManagerQueryType",
    "DataManagerService",
    "MAX_RETRIES",
    "SYSTEM_COMMAND_SETS",
    "get_db_type",
]


def __getattr__(name):
    if name == "DataManager":
        from shared.data_manager.data_manager import DataManager

        return DataManager
    raise AttributeError(name)
