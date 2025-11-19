# Canonical package entry for DataManager Service
# Expose DataManagerService and related symbols from submodules

from .data_manager_service import DataManagerService, DMSDbType, get_db_type, DataManagerQueryType, CommandSetData, ConnectionConfig  # noqa: F401,F403
from .data_manager_service_command_sets import COMMAND_SETS, DEFAULT_TIMEOUT, MAX_RETRIES  # noqa: F401,F403

# Provide lazy access to DataManager to avoid circular imports during package initialization
# Some callers do: `from lib.data_manager_service import DataManager`
# We delay importing until the attribute is actually accessed.
__all__ = [
    "DataManagerService",
    "DMSDbType",
    "get_db_type",
    "DataManagerQueryType",
    "CommandSetData",
    "ConnectionConfig",
    "COMMAND_SETS",
    "DEFAULT_TIMEOUT",
    "MAX_RETRIES",
    "DataManager",
]

def __getattr__(name):  # PEP 562
    if name == "DataManager":
        from lib.data_manager.data_manager import DataManager
        return DataManager
    raise AttributeError(name)
