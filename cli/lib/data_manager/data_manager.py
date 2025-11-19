import os
import threading
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any, Union, Optional
import queue
try:
    import psycopg2
except ImportError:
    psycopg2 = None
try:
    import pymysql
except ImportError:
    pymysql = None

import pandas as pd
from lib.data_manager_service.data_manager_service_command_sets import (COMMAND_SETS, DEFAULT_TIMEOUT, MAX_RETRIES,
                                               DMSDbType, DataManagerQueryType)
# Import the necessary classes directly to avoid circular imports

# These classes are defined in data_manager_service.py but we need to avoid circular imports
class CommandStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Command:
    def __init__(self,
                 name: str,
                 command_set_name: str,
                 query: str,
                 timeout: int = DEFAULT_TIMEOUT,
                 retries: int = MAX_RETRIES,
                 default_interval_ms: int = 60000,
                 status: CommandStatus = CommandStatus.PENDING,
                 error_message: str = "",
                 execution_time: float = 0.0,
                 result_count: int = 0,
                 metadata: Dict[str, Any] = None,
                 supports_latency_timing: bool = False,
                 remove_backtick: bool = False,
                 last_execution: float = 0.0,
                 next_execution: float = 0.0,
                 default_query: bool = False,
                 description: str = '',
                 query_type: DataManagerQueryType = DataManagerQueryType.UPSTREAM,
                 schema: List[str] = None,
                 override: bool = False):
        self.name = name
        self.command_set_name = command_set_name
        self.query = query
        self.timeout = timeout
        self.retries = retries
        self.default_interval_ms = default_interval_ms
        self.status = status
        self.error_message = error_message
        self.execution_time = execution_time
        self.result_count = result_count
        self.metadata = metadata or {}
        self.supports_latency_timing = supports_latency_timing
        self.remove_backtick = remove_backtick
        self.last_execution = last_execution
        self.next_execution = next_execution
        self.default_query = default_query
        self.description = description
        self.query_type = query_type
        self.schema = schema or []
        self.override = override

class CommandSet:
    def __init__(self,
                 name: str,
                 sync_interval: int = 30000,
                 last_sync_time: float = 0.0,
                 next_sync_time: float = 0.0,
                 sync_time: float = 0.0,
                 failure_count: int = 0,
                 supports_latency_timing: bool = False):
        self.name = name
        self.sync_interval = sync_interval
        self.last_sync_time = last_sync_time
        self.next_sync_time = next_sync_time
        self.sync_time = sync_time
        self.failure_count = failure_count
        self.supports_latency_timing = supports_latency_timing

class ConnectionConfig:
    def __init__(self,
                 host: str,
                 port: int,
                 database: str,
                 username: str,
                 password: str,
                 db_type: str,
                 ssl_mode: str = "prefer",
                 connect_timeout: int = DEFAULT_TIMEOUT,
                 query_type: DataManagerQueryType = DataManagerQueryType.UPSTREAM):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password
        self.db_type = db_type
        self.ssl_mode = ssl_mode
        self.connect_timeout = connect_timeout
        self.query_type = query_type

class CommandSetData:
    def __init__(self, name: str, data_folder:str, schema: List[str] = None, global_logger=None):
        self.name = name
        self.schema = schema if schema else []
        self.logger = global_logger
        self._lock = threading.Lock()
        self._file_path = f"{data_folder}/{name}.csv"

        # Initialize empty DataFrame with schema if provided
        if schema:
            self._data = pd.DataFrame(columns=schema)
        else:
            self._data = pd.DataFrame()

        self._last_update_time = pd.Timestamp.now()

    def update_with_dataframe(self, df: pd.DataFrame, merge_on: List[str] = None, dedup_key: str = "key", validate_schema: bool = True) -> None:
        with self._lock:
            if df.empty:
                return

            # Perform schema validation once at the beginning
            schema_valid = True
            if validate_schema and self.schema:
                # Check if DataFrame columns match the schema exactly
                df_columns = set(df.columns)
                schema_columns = set(self.schema)

                # If columns don't match, log a warning and try to adapt
                if df_columns != schema_columns:
                    if self.logger:
                        self.logger.warning(f"DataFrame columns {df_columns} don't match schema {schema_columns}")
                    
                    # Try to reindex columns to match schema if possible
                    try:
                        df = df.reindex(columns=self.schema)
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"Failed to adapt DataFrame to schema: {e}")
                        schema_valid = False

            # If schema validation failed, return without updating
            if not schema_valid:
                if self.logger:
                    self.logger.error("Schema validation failed, not updating data")
                return

            # If this is the first data, just set it
            if self._data.empty:
                self._data = df.copy()
                self._last_update_time = pd.Timestamp.now()
                return

            # If merge_on columns are specified, use them for merging
            if merge_on:
                try:
                    # Ensure all merge columns exist in both DataFrames
                    for col in merge_on:
                        if col not in self._data.columns or col not in df.columns:
                            if self.logger:
                                self.logger.error(f"Merge column '{col}' not found in one of the DataFrames")
                            return

                    # Perform an outer merge and update existing rows
                    merged = pd.merge(self._data, df, on=merge_on, how='outer', suffixes=('_old', ''))
                    
                    # For each column in the original schema
                    for col in self._data.columns:
                        if col in merge_on:
                            continue  # Skip merge columns
                        
                        # If the column exists in the new data, update non-null values
                        if col in df.columns:
                            col_old = f"{col}_old"
                            # Where new values are not null, use them, otherwise keep old values
                            merged[col] = merged[col].fillna(merged[col_old])
                            # Drop the old column
                            merged = merged.drop(col_old, axis=1)
                    
                    # Update the data
                    self._data = merged
                    
                except Exception as e:
                    if self.logger:
                        self.logger.error(f"Error during merge operation: {e}")
                    return
            else:
                # If no merge columns specified, use dedup_key if it exists
                if dedup_key in self._data.columns and dedup_key in df.columns:
                    # Get existing keys
                    existing_keys = set(self._data[dedup_key])
                    
                    # Filter new data to only include rows with keys not in existing data
                    new_rows = df[~df[dedup_key].isin(existing_keys)]
                    
                    # Append only new rows
                    if not new_rows.empty:
                        self._data = pd.concat([self._data, new_rows], ignore_index=True)
                else:
                    # If no dedup_key, just append all rows
                    self._data = pd.concat([self._data, df], ignore_index=True)

            self._last_update_time = pd.Timestamp.now()

    def set(self, key: str, value: Any) -> None:
        """Set a value in the data by key."""
        with self._lock:
            # If the data is empty, initialize it with columns
            if self._data.empty:
                if self.schema:
                    self._data = pd.DataFrame(columns=self.schema)
                else:
                    # Default schema for key-value pairs
                    self._data = pd.DataFrame(columns=["key", "value"])

            # Convert value to DataFrame if it's not already
            if isinstance(value, pd.DataFrame):
                df_value = value
            else:
                # Handle different value types
                if isinstance(value, (list, tuple)) and len(value) > 0:
                    if isinstance(value[0], (list, tuple)):
                        # It's a list of rows
                        if self.schema:
                            df_value = pd.DataFrame(value, columns=self.schema)
                        else:
                            df_value = pd.DataFrame(value)
                    else:
                        # It's a single row
                        if self.schema:
                            df_value = pd.DataFrame([value], columns=self.schema)
                        else:
                            df_value = pd.DataFrame([value])
                else:
                    # It's a scalar value, store as key-value pair
                    df_value = pd.DataFrame([{"key": key, "value": str(value)}])

            # Check if the key already exists
            if "key" in self._data.columns:
                key_exists = key in self._data["key"].values
                if key_exists:
                    # Update existing row
                    self._data.loc[self._data["key"] == key, "value"] = str(value)
                else:
                    # Append new row
                    self._data = pd.concat([self._data, df_value], ignore_index=True)
            else:
                # If no key column, just append the data
                self._data = pd.concat([self._data, df_value], ignore_index=True)

            self._last_update_time = pd.Timestamp.now()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the data by key."""
        with self._lock:
            if self._data.empty or "key" not in self._data.columns:
                return default

            # Find rows matching the key
            matches = self._data[self._data["key"] == key]
            if matches.empty:
                return default

            # Return the value from the first matching row
            if "value" in matches.columns:
                return matches.iloc[0]["value"]
            else:
                # If no value column, return the entire row as a dict
                return matches.iloc[0].to_dict()

    def get_dataframe(self) -> pd.DataFrame:
        """Get a copy of the entire DataFrame."""
        with self._lock:
            return self._data.copy()

    def set_dataframe(self, df: pd.DataFrame, validate_schema: bool = True) -> None:
        """Replace the entire DataFrame."""
        with self._lock:
            if validate_schema and self.schema:
                # Check if DataFrame columns match the schema
                df_columns = set(df.columns)
                schema_columns = set(self.schema)

                # If columns don't match, log a warning and try to adapt
                if df_columns != schema_columns:
                    if self.logger:
                        self.logger.warning(f"DataFrame columns {df_columns} don't match schema {schema_columns}")
                    
                    # Try to reindex columns to match schema
                    try:
                        df = df.reindex(columns=self.schema)
                    except Exception as e:
                        if self.logger:
                            self.logger.error(f"Failed to adapt DataFrame to schema: {e}")
                        return

            # Replace the data
            self._data = df.copy()
            self._last_update_time = pd.Timestamp.now()

    def set_records(self, records: List[List], schema: List[str] = None) -> None:
        """Set data from a list of records."""
        with self._lock:
            # Use provided schema or fall back to instance schema
            cols = schema if schema else self.schema
            
            if cols:
                # If we have a schema, use it
                df = pd.DataFrame(records, columns=cols)
            else:
                # Otherwise, let pandas infer column names
                df = pd.DataFrame(records)
            
            self._data = df
            self._last_update_time = pd.Timestamp.now()

    def to_csv(self, file_path: str = None) -> str:
        """Export data to CSV file."""
        with self._lock:
            # Use provided path or default
            path = file_path or self._file_path
            
            try:
                # Ensure directory exists
                os.makedirs(os.path.dirname(path), exist_ok=True)
                
                # Write to CSV
                self._data.to_csv(path, index=False)
                
                if self.logger:
                    self.logger.debug(f"Data exported to CSV: {path}")
                
                return path
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to export data to CSV: {e}")
                return ""

    def from_csv(self, file_path: str) -> bool:
        """Import data from CSV file."""
        with self._lock:
            try:
                self._data = pd.read_csv(file_path)
                self._last_update_time = pd.Timestamp.now()
                
                if self.logger:
                    self.logger.debug(f"Data imported from CSV: {file_path}")
                
                return True
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to import data from CSV: {e}")
                return False

    def to_json(self) -> str:
        """Convert data to JSON string."""
        with self._lock:
            return self._data.to_json(orient="records")

    def to_dict(self) -> Dict:
        """Convert data to dictionary."""
        with self._lock:
            if "key" in self._data.columns and "value" in self._data.columns:
                # If data has key-value format, convert to dict
                return dict(zip(self._data["key"], self._data["value"]))
            else:
                # Otherwise, return as records
                return self._data.to_dict(orient="records")

    def last_update_time(self) -> pd.Timestamp:
        """Get the timestamp of the last update."""
        return self._last_update_time

    def is_empty(self) -> bool:
        """Check if the data is empty."""
        with self._lock:
            return self._data.empty

    def row_count(self) -> int:
        """Get the number of rows in the data."""
        with self._lock:
            return len(self._data)

    def column_count(self) -> int:
        """Get the number of columns in the data."""
        with self._lock:
            return len(self._data.columns)

    def clear(self) -> None:
        """Clear all data."""
        with self._lock:
            if self.schema:
                self._data = pd.DataFrame(columns=self.schema)
            else:
                self._data = pd.DataFrame()

    def __str__(self) -> str:
        """String representation of the data"""
        return f"CommandSetData(name='{self.name}', rows={len(self._data)}, columns={len(self._data.columns)})"

    def __len__(self) -> int:
        """Get the number of rows in the data"""
        return len(self._data)

class DataManager:
    def __init__(self,
                 connection_config: {},
                 global_logger=None,
                 command_sets: List[str] = None,
                 data_directory: str = "./output",
                 max_workers: int = 4,
                 available_commands: Dict = None,
                 instance_s3_data_folder: str = None,
                 s3_operation: 'S3Operations' = None,
                 cli_mode: bool = False):
        self.instance_s3_data_folder = instance_s3_data_folder
        self.s3_operation = s3_operation
        self.connection_configs:{} = connection_config
        self.data_directory = data_directory
        self.max_workers = max_workers
        self.connections = {}
        self.cli_mode = cli_mode
        for query_type in DataManagerQueryType:
            self.connections[query_type] = None

        self.commands = {}
        self._lock = threading.Lock()

        self.logger = global_logger

        # Connection state tracking
        self._is_connected = {}
        for query_type in DataManagerQueryType:
            self._is_connected[query_type] = False
        # Indicates that the connection was successful
        # during the last query attempt.
        self._success_connections = {}
        for query_type in DataManagerQueryType:
            self._success_connections[query_type] = False

        # Tracks whether a connection attempt has been made (success or failure)
        self._connection_attempted = {}
        for query_type in DataManagerQueryType:
            self._connection_attempted[query_type] = False

        self._connection_error = {}
        for query_type in DataManagerQueryType:
            self._connection_error[query_type] = None

        # Use provided command definitions or fall back to global COMMAND_SETS
        self._available_commands = available_commands or COMMAND_SETS

        # Set command sets to use - either provided list or all available
        self.command_sets = command_sets or list(self._available_commands.keys())

        # Initialize as a dictionary instead of a list
        self.command_set_data_list: Dict[str, CommandSetData] = {}

        # Create CommandSetData objects for each command set
        for command_set_name in self.command_sets:
            command_set_config = self._available_commands.get(command_set_name, {})
            schema = command_set_config.get('schema', [])

            self.logger.debug(f"---->>>>>>Creating CommandSetData for command set "
                        f"'{command_set_name}' with schema: {schema}", highlight=True)
            command_set_data = CommandSetData(name=command_set_name, schema=schema,
                                              data_folder=self.data_directory, global_logger=self.logger)
            self.command_set_data_list[command_set_name] = command_set_data
            self.command_set_data_list[command_set_name].clear()



        # Create an output directory if it doesn't exist
        os.makedirs(data_directory, exist_ok=True)

        # Load commands from specified command sets
        self._load_commands()
        if self.instance_s3_data_folder is None and not self.cli_mode:
            raise ValueError("Instance S3 data folder must be specified.")

        # Perform initial connection checks for configured databases
        self._initial_connection_check()

    def _initial_connection_check(self) -> None:
        """Attempt to connect to all configured databases upon initialization.
        This validates connectivity early and records status/error per query type.
        Connections are closed immediately after a successful check to avoid
        keeping idle connections open.
        """
        try:
            def has_config(qt: DataManagerQueryType) -> bool:
                if isinstance(self.connection_configs, dict):
                    return self.connection_configs.get(qt) is not None
                elif isinstance(self.connection_configs, list):
                    return any(getattr(c, 'query_type', None) == qt for c in self.connection_configs)
                return False

            for qt in DataManagerQueryType:
                if qt in (DataManagerQueryType.SYSTEM, DataManagerQueryType.UNKNOWN):
                    continue
                if not has_config(qt):
                    continue
                if self.logger:
                    self.logger.debug(f"Initial connection check for {qt}")
                ok = self.connect(qt)
                if ok:
                    self.disconnect(qt)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Initial connection check encountered an error: {e}")

    def connect(self, query_type: DataManagerQueryType) -> bool:
        # System queries don't require a connection
        if query_type == DataManagerQueryType.SYSTEM:
            self._is_connected[query_type] = True
            # Mark attempted for completeness when explicitly connecting to SYSTEM
            self._connection_attempted[query_type] = True
            self._success_connections[query_type] = True
            self._connection_error[query_type] = None
            return True

        # Add specific logging for ReadySet connection attempts
        if query_type == DataManagerQueryType.READYSET:
            self.logger.debug(f"Starting ReadySet connection process for query_type: {query_type}")
            self.logger.debug(f"Connection configs type: {type(self.connection_configs)}")
            if isinstance(self.connection_configs, dict):
                self.logger.debug(f"Available connection config keys: {list(self.connection_configs.keys())}")
            elif isinstance(self.connection_configs, list):
                self.logger.debug(f"Connection configs list length: {len(self.connection_configs)}")
                for i, config in enumerate(self.connection_configs):
                    if hasattr(config, 'query_type'):
                        self.logger.debug(f"Config {i}: query_type = {config.query_type}")
                    else:
                        self.logger.debug(f"Config {i}: no query_type attribute")

        # Get connection config - handle both dict and list formats
        connection_config = None
        if isinstance(self.connection_configs, dict):
            connection_config = self.connection_configs.get(query_type, None)
            if query_type == DataManagerQueryType.READYSET:
                self.logger.debug(f"Dict lookup result for READYSET: {connection_config is not None}")
        elif isinstance(self.connection_configs, list):
            # Find the config with matching query_type
            for config in self.connection_configs:
                if hasattr(config, 'query_type') and config.query_type == query_type:
                    connection_config = config
                    break
            if query_type == DataManagerQueryType.READYSET:
                self.logger.debug(f"List search result for READYSET: {connection_config is not None}")

        if not connection_config:
            # Record attempt even if no configuration was provided
            self._connection_attempted[query_type] = True
            error_msg = f"No connection configuration found for query_type: {query_type}"
            if query_type == DataManagerQueryType.READYSET:
                self.logger.error(f"READYSET CONNECTION FAILURE: {error_msg}")
                self.logger.error(f"Connection configs structure: {self.connection_configs}")
            else:
                self.logger.error(error_msg)
            self._connection_error[query_type] = error_msg
            self._is_connected[query_type] = False
            self._success_connections[query_type] = False
            return False

        # Initialize the connection error if not set
        if self._connection_error.get(query_type, None) is None:
            self._connection_error[query_type] = ""

        # Log connection attempt with sanitized details
        connection_info = {
            "host": connection_config.host,
            "port": connection_config.port,
            "database": connection_config.database,
            "username": connection_config.username,
            "db_type": connection_config.db_type.value if hasattr(connection_config.db_type, 'value') else str(
                connection_config.db_type),
            "ssl_mode": getattr(connection_config, 'ssl_mode', 'default'),
            "connect_timeout": getattr(connection_config, 'connect_timeout', 'default')
        }

        if query_type == DataManagerQueryType.READYSET:
            self.logger.info("READYSET CONNECTION ATTEMPT: Starting database connection",
                             connection_details=connection_info, )
        else:
            self.logger.debug("Attempting database connection", connection_details=connection_info)

        try:
            # Mark that a connection attempt is being made
            self._connection_attempted[query_type] = True
            self.logger.debug(f"CONNECTION CONFIG: {connection_config}")
            if connection_config.db_type == DMSDbType.MySql:
                if query_type == DataManagerQueryType.READYSET:
                    self.logger.debug("READYSET: Initiating MySQL connection")
                else:
                    self.logger.debug("Initiating MySQL connection")

                self.connections[query_type] = pymysql.connect(
                    host=connection_config.host,
                    port=connection_config.port,
                    user=connection_config.username,
                    password=connection_config.password,
                    database=connection_config.database,
                    connect_timeout=connection_config.connect_timeout
                )
                if query_type == DataManagerQueryType.READYSET:
                    self.logger.debug(
                        f"READYSET SUCCESS: Connected to MySQL database '{connection_config.database}' at {connection_config.host}:{connection_config.port}",
                    )
                else:
                    self.logger.debug(
                        f"Successfully connected to MySQL database '{connection_config.database}' at {connection_config.host}:{connection_config.port}")

            elif connection_config.db_type == DMSDbType.PostgreSQL:
                if query_type == DataManagerQueryType.READYSET:
                    self.logger.debug("READYSET: Initiating PostgreSQL connection")
                else:
                    self.logger.debug("Initiating PostgreSQL connection")
                self.connections[query_type] = psycopg2.connect(
                    host=connection_config.host,
                    port=connection_config.port,
                    user=connection_config.username,
                    password=connection_config.password,
                    database=connection_config.database,
                    connect_timeout=connection_config.connect_timeout,
                    sslmode=connection_config.ssl_mode
                )
                if query_type == DataManagerQueryType.READYSET:
                    self.logger.debug(
                        f"READYSET SUCCESS: Connected to PostgreSQL database '{connection_config.database}' at {connection_config.host}:{connection_config.port}",
                        highlight=True)
                else:
                    self.logger.debug(
                        f"Successfully connected to PostgreSQL database '{connection_config.database}' at {connection_config.host}:{connection_config.port}")

            else:
                error_msg = f"Unsupported database type: {connection_config.db_type}"
                self._connection_error[query_type] = error_msg
                self._is_connected[query_type] = False
                if query_type == DataManagerQueryType.READYSET:
                    self.logger.error(f"READYSET ERROR: {error_msg}",
                                      connection_details=connection_info, highlight=True)
                else:
                    self.logger.error(error_msg, connection_details=connection_info)
                self._success_connections[query_type] = False
                return False

            self._is_connected[query_type] = True
            self._success_connections[query_type] = True
            self._connection_error[query_type] = None

            # Log successful connection
            if query_type == DataManagerQueryType.READYSET:
                self.logger.debug(f"READYSET FINAL SUCCESS: Connected to {connection_config.db_type.value} database",
                                 connection_details=connection_info,
                                 connection_status="established",
                                 highlight=True)
            else:
                self.logger.debug(f"Successfully connected to {connection_config.db_type.value} database",
                                 connection_details=connection_info,
                                 connection_status="established")
            return True

        except (psycopg2.OperationalError, pymysql.OperationalError) as e:
            self.logger.info(f"Connection attempt failed: {str(connection_config)}")
            error_str = str(e)
            self._connection_error[query_type] = error_str
            self._is_connected[query_type] = False
            if query_type == DataManagerQueryType.READYSET:
                self.logger.error(f"READYSET OPERATIONAL ERROR: Database operational error during connection",
                                  connection_details=connection_info,
                                  error_type="OperationalError",
                                  error_message=error_str,
                                  highlight=True)
            else:
                self.logger.error(f"Database operational error during connection",
                                  connection_details=connection_info,
                                  error_type="OperationalError",
                                  error_message=error_str)
            self._success_connections[query_type] = False
            return False

        except Exception as e:
            self.logger.info(f"Connection attempt failed: {str(connection_config)}")
            error_str = str(e)
            self._connection_error[query_type] = error_str
            self._is_connected[query_type] = False
            if query_type == DataManagerQueryType.READYSET:
                self.logger.error(f"READYSET UNEXPECTED ERROR: Unexpected error during connection",
                                  connection_details=connection_info,
                                  error_type=type(e).__name__,
                                  error_message=error_str,
                                  highlight=True)
            else:
                self.logger.error(f"Unexpected error during connection",
                                  connection_details=connection_info,
                                  error_type=type(e).__name__,
                                  error_message=error_str)
            self._success_connections[query_type] = False
            return False


    def _load_commands(self):
        """Load all the commands from all sets"""
        self.commands = {}
        for command_set_name in self.command_sets:
            command_set = self._available_commands.get(command_set_name)
            if not command_set:
                raise ValueError(f"Command set '{command_set_name}' not found in available commands")

            query_type = command_set.get('query_type', DataManagerQueryType.SYSTEM)
            override_data = command_set.get('override', False)
            for command_name, command_config in command_set.get('commands', {}).items():
                command_obj = Command(
                    name=command_name,
                    command_set_name=command_set_name,
                    query=command_config.get('query', ''),
                    default_interval_ms=command_config.get('default_interval_ms', 60000),
                    supports_latency_timing=command_config.get('supports_latency_timing', False),
                    remove_backtick=command_config.get('remove_backtick', False),
                    default_query=command_config.get('default_query', False),
                    description=command_config.get('description', ''),
                    query_type=query_type,
                    schema=command_set.get('schema', []),
                    override=override_data
                )
                self.commands[f"{command_set_name}_{command_obj.name}"] = command_obj
            if not self.cli_mode:
                self.sync_to_s3_folder(command_set_name)

    def sync_to_s3_folder(self, command_set_name: str):
        command_set_data = None
        start_time = time.time()
        try:
            command_set_data = self.command_set_data_list[command_set_name]
            filename = self._available_commands.get(command_set_name).get('filename', f"{command_set_data.name}.csv")

            # Check if sync_s3_folder is None or empty
            if not self.instance_s3_data_folder:
                self.logger.warning(f"S3 sync folder not configured. Skipping sync for {command_set_name} until next cycle")
                return {
                    "success": True,  # Return success to avoid retrying
                    "message": "S3 sync folder not configured - skipped",
                    "command_set_name": command_set_name,
                    "sync_time": time.time() - start_time,
                    "skipped": True
                }

            # Create an S3 key for the file
            s3_key = f"{self.instance_s3_data_folder}/{filename}"
            # Get the file from the command set.
            df = command_set_data.get_dataframe()

            self.s3_operation.write_csv(s3_key, df)

            self.logger.debug(f"Successfully synced {filename} to {s3_key}")

            return {
                "success": True,
                "error": "",
                "command_set_name": command_set_name,
                "sync_time": time.time() - start_time,
            }

        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"Failed to sync {'' if command_set_data is None else command_set_data.name} to S3: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "command_set_name": command_set_name,
                "sync_time": time.time() - start_time,
            }

    def _get_query_for_db_type(self, cmd_config: Dict[str, Any]) -> str:
        """Get the appropriate query for the current database type."""
        # First, try to get a generic query that works for all database types
        query = cmd_config.get('query', '')

        # If no generic query exists, fall back to database-specific queries
        if not query:
            if self.connection_configs.db_type == DMSDbType.MySql:
                query = cmd_config.get('query_mysql', '')
            elif self.connection_configs.db_type == DMSDbType.PostgreSQL:
                query = cmd_config.get('query_postgresql', '')

        return query

    def _execute_system_command(self, command: str) -> List[List[str]]:
        """Execute a system command and return results as a list of rows."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self.logger.error(f"System command failed: {command}, Error: {result.stderr}")
                return []

            output = result.stdout.strip()
            if not output:
                return []

            # Split by lines, then by commas (for CSV-like output)
            rows = []
            for line in output.split('\n'):
                if ',' in line:
                    rows.append(line.split(','))
                else:
                    rows.append([line])

            return rows

        except subprocess.TimeoutExpired:
            self.logger.error(f"System command timed out: {command}")
            return []
        except Exception as e:
            self.logger.error(f"Error executing system command: {command}, Error: {e}")
            return []

    def disconnect(self, query_type: DataManagerQueryType) -> None:
        """Close database connection and update state."""
        if self.connections:
            try:
                if self.connections[query_type] is not None:
                    self.connections[query_type].close()
                    self.logger.debug("Database connection closed")
            except Exception as e:
                self.logger.error(f"Error closing database connection: {e}")
            finally:
                self.connections[query_type] = None
                self._is_connected[query_type] = False
                self._connection_error[query_type] = None

    def is_connected(self, query_type: DataManagerQueryType) -> bool:
        """Check if connected to a database for a given query type."""

        # System queries don't require a connection
        if query_type == DataManagerQueryType.SYSTEM:
            return True

        # Check if we have connection state tracking for this query type
        if query_type not in self._is_connected:
            return False

        is_connected_flag = self._is_connected[query_type]

        return is_connected_flag

    def get_success_connection(self, query_type: DataManagerQueryType) -> bool:
        """Check if the last connection attempt was successful for a given query type."""
        if query_type == DataManagerQueryType.SYSTEM:
            return True
        # Check if we have connection state tracking for this query type
        if query_type not in self._success_connections:
            return False

        success_connection_flag = self._success_connections[query_type]
        return success_connection_flag

    def get_connection_error(self, query_type: DataManagerQueryType) -> str:
        """Get a connection error for a given query type."""

        # Check if we have error tracking for this query type
        if query_type not in self._connection_error:
            return f"No error tracking configured for query type: {query_type}"
        if query_type == DataManagerQueryType.READYSET:
            self.logger.debug(f"There error is {self._connection_error[query_type]}")
        return self._connection_error[query_type]

    def get_connection_attempted(self, query_type: DataManagerQueryType) -> bool:
        """Return True if a connection attempt was made for the given query type during this session."""
        if query_type == DataManagerQueryType.SYSTEM:
            # We consider SYSTEM always attempted/successful when asked explicitly
            return True if self._connection_attempted.get(query_type, False) else False
        return bool(self._connection_attempted.get(query_type, False))

    def get_connection_state(self, query_type: DataManagerQueryType) -> Dict[str, Any]:
        """Return a small status dict summarizing connection attempt, success, and error (if any)."""
        return {
            "attempted": bool(self._connection_attempted.get(query_type, False)),
            "success": bool(self._success_connections.get(query_type, False)),
            "error": self._connection_error.get(query_type)
        }

    def _convert_result_to_dataframe(self, result, command_obj):
        schema = command_obj.schema if command_obj.schema else None

        # If requested, replace all backticks in the result data with a space
        if command_obj.remove_backtick and result:
            # Check if the result is a list of rows (tuples or lists)
            new_result = []
            for row in result:
                # Replace backticks in string values only
                if isinstance(row, (list, tuple)):
                    new_row = [
                        value.replace("`", '"') if isinstance(value, str) else value
                        for value in row
                    ]
                    # For tuples, preserve type
                    if isinstance(row, tuple):
                        new_row = tuple(new_row)
                    new_result.append(new_row)
                else:
                    # If just a single value (not expected), handle as string
                    new_result.append(row.replace("`", '"') if isinstance(row, str) else row)
            result = new_result

        # Convert a result directly to DataFrame
        if schema:
            return pd.DataFrame(result, columns=schema)
        else:
            # If no schema, let pandas infer or create generic column names
            return pd.DataFrame(result)

    def execute_command(self, command_set_name: str, command_name: str) -> Dict[str, Any]:
        """Execute a single command."""
        command = f"{command_set_name}_{command_name}"
        self.logger.debug(f"Executing command: {command}", highlight=True)
        command_obj: Command = self.commands.get(command)

        if command_obj is None:
            self.logger.error(f"Command '{command_name}' not found in command set '{command_set_name}'")
            return {
                'success': False,
                'error': "command name not found in available command list",
                'command': command_name
            }
        self.logger.debug(f"Command details: {command_obj}", highlight=False)
        self.logger.debug(f"Starting execution of command: {command_name} with query: {command_obj.query}", highlight=True)
        self.logger.debug(f"Available handlers: {list(self.commands.keys())}")

        command_obj.status = CommandStatus.RUNNING
        start_time = time.time()

        try:
            if command_obj.query_type == DataManagerQueryType.SYSTEM:
                self.logger.debug(f"Executing system command: {command_obj.query}", highlight=True)
                result = self._execute_system_command(command_obj.query)
                self.logger.debug(f"System command result: {result}", highlight=True)
            else:
                # Connect to a database if not already connected
                try:
                    self.connect(command_obj.query_type)
                except pymysql.OperationalError as e:
                    self.logger.warning(f"Operational error during connection: {e}")
                    return {}
                self.logger.debug(f"Query type for {command} is {command_obj.query_type}", highlight=False)
                if self.connections[command_obj.query_type] is None:
                    return {
                        'success': False,
                        'error': f"no connection found for query type {command_obj.query_type}",
                        'command': command_name
                    }

                # Check if the query_type exists in connections
                self.logger.debug(f"Query type: {command_obj.query_type}", highlight=False)
                if command_obj.query_type not in self.connections:
                    raise ValueError(f"Unknown connection type: {command_obj.query_type}")

                # Execute the query using the appropriate connection
                conn = self.connections[command_obj.query_type]
                cursor = conn.cursor()
                cursor.execute(command_obj.query)
                result = cursor.fetchall()
                cursor.close()
                conn.close()

            result = self._convert_result_to_dataframe(result, command_obj)
            self.logger.debug(f"result from command_obj : {result}", highlight=True)

            command_obj.execution_time = time.time() - start_time
            # Fix: a result is a tuple/list, not a dict - count the rows
            if hasattr(result, 'empty'):  # Check if it's a panda DataFrame
                command_obj.result_count = len(result) if not result.empty else 0
            else:
                command_obj.result_count = len(result) if result else 0

            command_obj.status = CommandStatus.COMPLETED
            # Get or create the command set data

            if command_set_name not in self.command_set_data_list:
                schema = self._available_commands.get(command_set_name, {}).get('schema', [])
                self.command_set_data_list[command_set_name] = CommandSetData(
                    name=command_set_name,
                    schema=schema,
                    data_folder=self.data_directory
                )

            # Update the command set data with a result
            if command_obj.override:
                self.command_set_data_list[command_set_name].clear()
            self.command_set_data_list[command_set_name].update_with_dataframe(result)
            # log if the data was updated
            self.logger.debug(f"The command set data is {self.command_set_data_list[command_set_name]}", highlight=True)
            self.command_set_data_list[command_set_name].to_csv()
            return {
                'success': True,
                'data': result,
                'command': command_obj.name,
                'row_count': command_obj.result_count,
                'execution_time': command_obj.execution_time
            }

        except Exception as e:
            command_obj.status = CommandStatus.FAILED
            command_obj.error_message = str(e)
            command_obj.execution_time = time.time() - start_time

            self.logger.error(f"Command {command_obj.name} failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'command': command_obj.name
            }

    def execute_commands(self, commands: List[Command]) -> Dict[str, Any]:
        """Execute multiple commands concurrently."""
        results = {}
        failed_commands = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_command = {
                executor.submit(self.execute_command, cmd.command_set_name, cmd.name): cmd
                for cmd in commands
            }

            for future in as_completed(future_to_command):
                command = future_to_command[future]
                try:
                    result = future.result()

                    # Get or create the command set data
                    if command.command_set_name not in self.command_set_data_list:
                        schema = self._available_commands.get('schema', [])
                        self.command_set_data_list[command.command_set_name] = CommandSetData(
                            name=command.command_set_name,
                            schema=schema,
                            data_folder=self.data_directory
                        )

                    # Update the command set data with a result
                    if result.get('data'):
                        self.command_set_data_list[command.command_set_name].set(command.name, result.get('data'))

                    if not result.get('success', False):
                        failed_commands.append(command.name)
                except Exception as e:
                    error_msg = f"Command {command.name} raised exception: {e}"
                    self.logger.error(error_msg)
                    results[command.name] = {'success': False, 'error': error_msg}
                    failed_commands.append(command.name)

        summary = {
            'success': len(failed_commands) == 0,
            'total_commands': len(commands),
            'successful_commands': len(commands) - len(failed_commands),
            'failed_commands': failed_commands,
            'results': results
        }

        return summary