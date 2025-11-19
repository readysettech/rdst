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
except ImportError:  # During tests, DB drivers might be unavailable; they are patched/mocked
    psycopg2 = None
try:
    import pymysql
except ImportError:  # During tests, DB drivers might be unavailable; they are patched/mocked
    pymysql = None

import pandas as pd
from .data_manager_service_command_sets import (COMMAND_SETS, DEFAULT_TIMEOUT, MAX_RETRIES,
                                               DMSDbType, DataManagerQueryType)

def get_db_type(db_name):
    try:
        db_type = next(db for db in DMSDbType if db.value == db_name.lower())
        return db_type
    except StopIteration:
        print(f"There was a key error {db_name}")
        return None




class CommandStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Command:
    name: str
    command_set_name: str
    query: str
    timeout: int = DEFAULT_TIMEOUT
    retries: int = MAX_RETRIES
    default_interval_ms: int = 60000  # Default to 60 seconds (60000ms)
    status: CommandStatus = CommandStatus.PENDING
    error_message: str = ""
    execution_time: float = 0.0
    result_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    supports_latency_timing: bool = False
    remove_backtick: bool = False
    last_execution: float = 0.0  # Timestamp of last execution
    next_execution: float = 0.0  # Timestamp of the next scheduled execution
    default_query: bool = False
    description:str = ''
    query_type:DataManagerQueryType = DataManagerQueryType.UPSTREAM
    schema: List[str] = field(default_factory=list)
    override: bool = False

@dataclass()
class CommandSet:
    name:str
    sync_interval: int = 30000
    last_sync_time: float = 0.0
    next_sync_time: float = 0.0
    sync_time: float = 0.0
    failure_count: int = 0
    supports_latency_timing: bool = False


@dataclass
class ConnectionConfig:
    host: str
    port: int
    database: str
    username: str
    password: str
    db_type: str
    ssl_mode: str = "prefer"
    connect_timeout: int = DEFAULT_TIMEOUT
    query_type: DataManagerQueryType = DataManagerQueryType.UPSTREAM

@dataclass
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

                if df_columns != schema_columns:
                    missing_in_df = schema_columns - df_columns
                    extra_in_df = df_columns - schema_columns

                    error_msg = f"Schema mismatch detected for {self.name}:"
                    if missing_in_df:
                        error_msg += f" Missing columns in DataFrame: {list(missing_in_df)}."
                    if extra_in_df:
                        error_msg += f" Extra columns in DataFrame: {list(extra_in_df)}."

                    raise ValueError(error_msg)
                schema_valid = True

            # Update the schema if needed (only when validation is disabled or no schema exists)
            if not self.schema:
                self.schema = list(df.columns)
            elif not validate_schema:
                # Add any new columns to the schema only if validation is disabled
                for col in df.columns:
                    if col not in self.schema:
                        self.schema.append(col)

            if self._data.empty:
                # If current data is empty, use the new DataFrame
                self._data = df.copy()
            else:
                if merge_on:
                    # Perform a merge operation to override existing values
                    # First, ensure merge columns exist in both DataFrames
                    merge_cols_available = all(col in self._data.columns and col in df.columns for col in merge_on)

                    if not merge_cols_available:
                        missing_in_current = [col for col in merge_on if col not in self._data.columns]
                        missing_in_new = [col for col in merge_on if col not in df.columns]
                        raise ValueError(
                            f"Merge columns not available. Missing in current: {missing_in_current}, Missing in new: {missing_in_new}")

                    # Perform outer merge to include all records from both DataFrames
                    merged = pd.merge(self._data, df, on=merge_on, how='outer', suffixes=('_old', '_new'))

                    # For each column not in merge_on, prefer the new value over the old
                    for col in df.columns:
                        if col not in merge_on:
                            old_col = f"{col}_old"
                            new_col = f"{col}_new"

                            if old_col in merged.columns and new_col in merged.columns:
                                # Use new value where available, otherwise use old value
                                merged[col] = merged[new_col].fillna(merged[old_col])
                                # Drop the suffixed columns
                                merged = merged.drop(columns=[old_col, new_col])
                            elif new_col in merged.columns:
                                # Only a new column exists (new data has this column)
                                merged[col] = merged[new_col]
                                merged = merged.drop(columns=[new_col])
                            elif old_col in merged.columns:
                                # Only the old column exists (existing data has this column)
                                merged[col] = merged[old_col]
                                merged = merged.drop(columns=[old_col])

                    self._data = merged
                else:
                    # Simple concatenation and drop duplicates using configurable dedup_key
                    if validate_schema and not schema_valid:
                        # This should not happen as we already validated above, but kept for safety
                        raise ValueError(f"Schema validation failed for {self.name}")

                    # Ensure both DataFrames have the same columns by adding missing ones with NaN
                    all_columns = list(set(self._data.columns) | set(df.columns))

                    # Add missing columns to both DataFrames
                    for col in all_columns:
                        if col not in self._data.columns:
                            self._data[col] = pd.NA
                        if col not in df.columns:
                            df = df.copy()
                            df[col] = pd.NA

                    # Ensure column order matches the expected schema
                    if hasattr(self, 'schema') and self.schema:
                        # Reorder columns to match schema first, then add any extra columns
                        schema_columns = [col for col in self.schema if col in all_columns]
                        extra_columns = [col for col in all_columns if col not in self.schema]
                        ordered_columns = schema_columns + extra_columns
                    else:
                        ordered_columns = all_columns

                    # Reorder columns to match the expected schema
                    self._data = self._data[ordered_columns]
                    df = df[ordered_columns]

                    # Concatenate and drop duplicates using a configurable dedup_key
                    combined = pd.concat([self._data, df], ignore_index=True)

                    # Use the specified deduplication key or fall back to the first column
                    dedup_column = dedup_key if dedup_key and dedup_key in df.columns else df.columns[0]
                    self._data = combined.drop_duplicates(subset=[dedup_column], keep='last').reset_index(drop=True)

            self._last_update_time = pd.Timestamp.now()

            if self.logger:
                self.logger.debug(f"{self.name}: Updated data with DataFrame containing {len(df)} rows")


    def set(self, key: str, value: Any) -> None:
        with self._lock:
            # Handle existing column
            if key in self._data.columns:
                if hasattr(value, '__len__') and not isinstance(value, (str, bytes)):
                    if len(value) != len(self._data):
                        raise ValueError(f"Value length {len(value)} doesn't match DataFrame length {len(self._data)}")
                    self._data[key] = value
                else:
                    # Single value - let pandas handle the broadcasting
                    self._data[key] = value
            else:
                # Add a new column
                if self._data.empty:
                    # Empty DataFrame - create from scratch
                    if hasattr(value, '__len__') and not isinstance(value, (str, bytes)):
                        self._data = pd.DataFrame({key: value})
                    else:
                        self._data = pd.DataFrame({key: [value]})
                else:
                    if hasattr(value, '__len__') and not isinstance(value, (str, bytes)):
                        if len(value) != len(self._data):
                            raise ValueError(
                                f"Value length {len(value)} doesn't match DataFrame length {len(self._data)}")
                        self._data[key] = value
                    else:
                        # Single value - let pandas handle broadcasting
                        self._data[key] = value

                # Update schema safely (avoid mutation)
                if key not in self.schema:
                    self.schema = self.schema + [key]

            self._last_update_time = pd.Timestamp.now()
            if self.logger:
                self.logger.debug(f"{self.name}: Set {key} = {value}")


    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            # For key-value style data
            if len(self.schema) == 2 and 'key' in self.schema and 'value' in self.schema:
                key_idx = self.schema.index('key')
                value_idx = self.schema.index('value')

                if len(self._data) > 0:
                    matches = self._data.index[self._data.iloc[:, key_idx] == key].tolist()
                    if matches:
                        return self._data.iloc[matches[0], value_idx]
            else:
                # For regular DataFrame, get a column or cell
                if key in self._data.columns:
                    # Return the entire column
                    values = self._data[key].tolist()
                    # If single value, return just that value instead of a list
                    return values[0] if len(values) == 1 else values

            if self.logger:
                self.logger.debug(f"{self.name}: Key not found: {key}")

            return default


    def get_dataframe(self) -> pd.DataFrame:
        with self._lock:
            return self._data.copy()


    def set_dataframe(self, df: pd.DataFrame, validate_schema: bool = True) -> None:
        with self._lock:
            if validate_schema and self.schema:
                # Check if DataFrame has all the required columns
                missing_cols = set(self.schema) - set(df.columns)
                if missing_cols:
                    raise ValueError(f"DataFrame is missing required columns: {missing_cols}")

            self._data = df.copy()

            # Update schema if needed
            if not self.schema:
                self.schema = list(df.columns)
            elif set(df.columns) != set(self.schema):
                # Add any new columns to the schema
                for col in df.columns:
                    if col not in self.schema:
                        self.schema.append(col)

            self._last_update_time = pd.Timestamp.now()


    def set_records(self, records: List[List], schema: List[str] = None) -> None:
        if not records:
            return

        # Use provided schema or existing schema
        columns = schema if schema is not None else self.schema

        # If no schema is available, generate column names
        if not columns and records:
            columns = [f"column_{i}" for i in range(len(records[0]))]

        # Create DataFrame from records
        df = pd.DataFrame(records, columns=columns)

        # Store the DataFrame
        self.set_dataframe(df)


    def to_csv(self, file_path: str = None) -> None:
        if file_path is None:
            file_path = self._file_path

        with self._lock:
            # If DataFrame is empty, and we have a schema, just write the headers
            if self._data.empty and self.schema:
                self.logger.debug(f"Writing schema directly: {self.schema}", highlight=False)
                import csv
                with open(file_path, 'w', newline='') as csvfile:
                    writer = csv.writer(csvfile)
                    writer.writerow(self.schema)

                # Verify the file was written
                import os
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path)
                    self.logger.debug(f"File written successfully. Size: {file_size} bytes", highlight=False)
                    # Read it back to verify
                    with open(file_path, 'r') as f:
                        content = f.read()
                        self.logger.debug(f"File content: '{content}'", highlight=False)
                else:
                    self.logger.debug(f"File was NOT created at {file_path}", highlight=False)

                if self.logger:
                    self.logger.debug(f"{self.name}: Wrote empty CSV with headers: {self.schema}", highlight=False)
            else:
                # Normal case: write the DataFrame
                self.logger.debug(f"Writing DataFrame normally", highlight=False)
                self._data.to_csv(file_path, index=False)

                if self.logger:
                    if self._data.empty:
                        self.logger.debug(f"{self.name}: Wrote empty CSV with headers: {list(self._data.columns)}",
                                         highlight=False)
                    else:
                        self.logger.debug(
                            f"{self.name}: Wrote CSV with {len(self._data)} rows and headers: {list(self._data.columns)}",
                            highlight=False)


    def from_csv(self, file_path: str) -> bool:
        try:
            df = pd.read_csv(file_path)
            self.set_dataframe(df)
            return True
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error loading CSV: {e}")
            return False


    def to_json(self) -> str:
        with self._lock:
            return self._data.to_json(orient="records")


    def to_dict(self) -> Union[Dict, List[Dict]]:
        with self._lock:
            # For key-value style data
            if len(self.schema) == 2 and 'key' in self.schema and 'value' in self.schema:
                key_idx = self.schema.index('key')
                value_idx = self.schema.index('value')
                return {row[key_idx]: row[value_idx] for _, row in self._data.iterrows()}
            else:
                # For regular DataFrame, return records
                return self._data.to_dict(orient="records")


    def last_update_time(self) -> pd.Timestamp:
        """Get the time of the last update"""
        return self._last_update_time


    def is_empty(self) -> bool:
          with self._lock:
            return len(self._data) == 0


    def row_count(self) -> int:
        with self._lock:
            return len(self._data)


    def column_count(self) -> int:
        with self._lock:
            return len(self._data.columns)


    def clear(self) -> None:
        with self._lock:
            self._data = pd.DataFrame(columns=self.schema)
            self._last_update_time = pd.Timestamp.now()


    def __str__(self) -> str:
        """String representation of the data"""
        return f"CommandSetData(name='{self.name}', rows={len(self._data)}, columns={len(self._data.columns)})"

    def __len__(self) -> int:
        """Get the number of rows in the data"""
        return len(self._data)


# DataManager class has been moved to lib/data_manager/legacy_data_manager.py
# Avoid importing DataManager at module import time to prevent circular imports.
# We will import DataManager lazily inside __init__ when needed.


class DataManagerService:

    def __init__(self,
                 global_logger=None,
                 connection_configs: Optional[List[ConnectionConfig]] = None,
                 command_sets: List[str] = None,
                 instance_s3_data_folder: str = None,
                 output_directory: str = "./data",
                 disabled: bool = True,
                 max_workers: int = 4,
                 queue_size: int = 100,
                 s3_operation: 'S3Operations' = None,
                 disable_s3_sync: bool = False,):

        self.is_running = False
        self._lock = threading.Lock()
        self.logger = global_logger
        self._start_time = time.time()
        self.connection_configs = connection_configs or []
        self.instance_s3_data_folder = instance_s3_data_folder

        if not disabled:
            self.logger.info(f"Initializing DataManagerService...")
            # Lazy import to avoid circular imports during package initialization
            from lib.data_manager.data_manager import DataManager
            self.data_manager = DataManager(
                global_logger=global_logger,
                connection_config=connection_configs,
                command_sets=command_sets,
                data_directory=output_directory,
                max_workers=max_workers,
                instance_s3_data_folder=instance_s3_data_folder,
                s3_operation=s3_operation,
            )

            self.command_queue = queue.Queue(maxsize=queue_size)
            self.result_queue = queue.Queue()
            self.worker_threads = []
            self.scheduler_thread = None
            self.scheduled_commands = {}
            self.scheduled_command_sets = {}
            self._lock = threading.Lock()
            self.logger: global_logger
            if not disable_s3_sync:
                if s3_operation is None:
                    raise ValueError("S3Operation object is required for DataManagerService")
                else:
                    self.s3_operation = s3_operation
                self.instance_s3_data_folder = instance_s3_data_folder

    def _scheduler(self):
        self.logger.info(f"Scheduler thread started ...")

        # Track commands currently being executed to prevent duplicates
        executing_commands = set()

        try:
            # Initialize all commands for scheduling
            current_time = int(time.time() * 1000)  # Current time in milliseconds

            with self._lock:
                for command_name, command in self.data_manager.commands.items():
                    # Set initial next_execution time if not set
                    if not command.next_execution:
                        command.next_execution = current_time + (command.default_interval_ms or DEFAULT_TIMEOUT)
                    # Add to scheduled commands
                    self.scheduled_commands[command_name] = command
                    self.logger.debug(f"Scheduled command: {command_name}")
                for command_set_name, command_set_data in self.data_manager.command_set_data_list.items():
                    command_set = CommandSet(name=command_set_name)
                    command_set.sync_interval = command_set_data.get('sync_interval')
                    command_set.next_sync_time = current_time + (command_set.sync_interval or DEFAULT_TIMEOUT)
                    self.scheduled_command_sets[command_set_name] = command_set

            self.logger.info(f"Initialized {len(self.scheduled_commands)} commands for scheduling")

            while self.is_running:
                try:
                    command_to_execute = None

                    with self._lock:
                        current_time = time.time()
                        self.logger.debug(
                            f"--------->>>>Scheduler thread checking for scheduled commands at "
                            f"{current_time} ...{self.scheduled_commands}",
                            highlight="false")

                        # Sort commands by next_execution time (earliest first - most overdue)
                        sorted_commands = sorted(
                            self.scheduled_commands.items(),
                            key=lambda item: item[1].next_execution if item[1].next_execution is not None else float(
                                'inf')
                        )

                        self.logger.debug(
                            f"--------->>>>Sorted commands by next_execution: "
                            f"{[(name, cmd.next_execution) for name, cmd in sorted_commands]}")

                        # Check commands in order of urgency (earliest next_execution first)
                        for command_name, command_obj in sorted_commands:
                            # Skip commands that are already running
                            if command_obj.status == CommandStatus.RUNNING:
                                continue
                            # Skip commands that are currently being executed
                            if command_name in executing_commands:
                                continue
                            # Skip commands that are disabled due to failures
                            if command_obj.next_execution is None:
                                continue
                            # Check if it's time to execute this command
                            if current_time >= (command_obj.next_execution / 1000):


                                # Mark as Running
                                executing_commands.add(command_name)
                                command_obj.status = CommandStatus.RUNNING
                                command_obj.last_execution = current_time
                                command_to_execute = command_name
                                self.logger.debug(f"Executing most overdue command: {command_name}")
                                break  # Execute the first (most overdue) eligible command

                    # Execute the command outside the lock
                    if command_to_execute:
                        try:
                            self.logger.debug(f"Executing scheduled command: {command_to_execute}")

                            # Execute the command using DataManager
                            result = self.data_manager.execute_command(
                                command_set_name=command_obj.command_set_name,
                                command_name=command_obj.name)

                            # Get FRESH current time AFTER execution completes
                            completion_time = int(time.time() * 1000)

                            # Update command status and schedule next execution on success
                            with self._lock:
                                if command_to_execute in self.scheduled_commands:
                                    cmd = self.scheduled_commands[command_to_execute]
                                    if result.get('success', False):
                                        cmd.status = CommandStatus.COMPLETED
                                        # Reset failure count on success
                                        if hasattr(cmd, 'failure_count'):
                                            cmd.failure_count = 0
                                    else:
                                        cmd.status = CommandStatus.FAILED
                                        cmd.error_message = result.get('error', 'Unknown error')

                                    # Schedule the next execution using FRESH completion time
                                    cmd.next_execution = completion_time + (cmd.default_interval_ms or DEFAULT_TIMEOUT)
                                    self.logger.debug(
                                        f"Command {cmd.name} completed at {completion_time}, "
                                        f"next execution at {cmd.next_execution}", highlight="false")

                            # Put the result in the result queue for processing by other components
                            self.result_queue.put((command_to_execute, result))

                        except Exception as e:
                            self.logger.error(f"Error executing command {command_to_execute.name}: {str(e)}")

                            # Get FRESH current time AFTER failure
                            failure_time = int(time.time() * 1000)

                            # Update command status on error
                            with self._lock:
                                if command_to_execute.name in self.scheduled_commands:
                                    cmd = self.scheduled_commands[command_to_execute.name]
                                    cmd.status = CommandStatus.FAILED
                                    cmd.error_message = str(e)

                                    # Track consecutive failures
                                    if not hasattr(cmd, 'failure_count'):
                                        cmd.failure_count = 0
                                    cmd.failure_count += 1

                                    # Stop scheduling after 3 consecutive failures
                                    if cmd.failure_count >= 3:
                                        cmd.next_execution = None
                                        self.logger.error(
                                            f"Command {command_to_execute.name} failed {cmd.failure_count} "
                                            f"times. Permanently disabled.")
                                    else:
                                        # Exponential backoff for retries using FRESH failure time
                                        base_interval = cmd.default_interval_ms or DEFAULT_TIMEOUT
                                        backoff_delay = base_interval * (2 ** cmd.failure_count)
                                        cmd.next_execution = failure_time + backoff_delay
                                        self.logger.warning(
                                            f"Command {command_to_execute.name} failed {cmd.failure_count} "
                                            f"times, retrying at {cmd.next_execution} (in {backoff_delay}ms)")

                            # Put error result in the queue
                            self.result_queue.put((command_to_execute, None))

                        finally:
                            # Always remove from an executing set when done
                            executing_commands.discard(command_to_execute)

                    # Sync command sets.
                    for command_set_name, command_set in self.scheduled_command_sets.items():
                        if current_time >= (command_set.next_sync_time / 1000):
                            try:
                                self.logger.debug(f"Syncing command set: {command_set_name}")
                                result = self.data_manager.sync_to_s3_folder(command_set_name)
                                if result.get('success', False):
                                    self.logger.debug(f"Synced command set: {command_set_name}")
                                    # Convert seconds to milliseconds
                                    command_set.next_sync_time = (current_time + (
                                                command_set.sync_interval or DEFAULT_TIMEOUT)) * 1000
                                else:
                                    error_message = result.get('error', 'Unknown error')
                                    self.logger.error(
                                        f"Error syncing command set: {command_set_name} - {error_message}")
                                    # Convert seconds to milliseconds
                                    command_set.next_sync_time = (current_time + (
                                                command_set.sync_interval or DEFAULT_TIMEOUT)) * 1000
                                    command_set.failure_count += 1
                            except Exception as e:
                                error_message = str(e)
                                self.logger.error(f"Error syncing command set: {command_set_name} - {error_message}")
                                self.scheduled_command_sets[command_set_name].failure_count += 1
                                # Convert seconds to milliseconds
                                self.scheduled_command_sets[command_set_name].next_sync_time = (
                                        (current_time + (self.scheduled_command_sets[
                                                             command_set_name].sync_interval or DEFAULT_TIMEOUT)) * 1000
                                )

                    # Sleep briefly to avoid high CPU usage
                    time.sleep(0.5)

                except Exception as e:
                    self.logger.error(f"Error in scheduler loop: {str(e)}")
                    time.sleep(1)  # Sleep longer on error

        except Exception as e:
            self.logger.error(f"Scheduler thread encountered an exception: {str(e)}")
        finally:
            self.logger.info(f"Scheduler thread stopped")

    def start(self) -> bool:
        with self._lock:
            self.logger.info(f"Starting data manager service.")

            if self.is_running:
                self.logger.warning(f"Service is already running")
                return True

            # Connect to database
            if not self.data_manager.connect(DataManagerQueryType.UPSTREAM):
                self.logger.error(f"Failed to connect to database for service {DataManagerQueryType.UPSTREAM.value}")
                return False
            self.logger.info(f"Successfully connected to database for service {DataManagerQueryType.UPSTREAM.value}")

            self.logger.info(f"Starting worker threads for service")
            # Initialize the command queue and worker threads list
            self.command_queue = queue.Queue()
            self.worker_threads = []

            # Mark service as running before starting threads
            self.is_running = True

            # Start a scheduler thread
            self.scheduler_thread = threading.Thread(
                target=self._scheduler,
                name=f"data_manager_service_scheduler",
                daemon=True
            )
            self.scheduler_thread.start()

            self.logger.info(
                f"Data Manager Service started with {len(self.worker_threads)} workers")
            return True

    def stop(self):
        try:
            with self._lock:
                if not self.is_running:
                    return

                self.is_running = False

                # Wait for scheduler to finish
                if self.scheduler_thread:
                    self.scheduler_thread.join(timeout=5)

                self.worker_threads.clear()
                self.scheduler_thread = None
                for query_type in DataManagerQueryType:
                    self.data_manager.disconnect(query_type=query_type)

                self.logger.debug("DataManagerService stopped")
        except Exception as e:
            self.logger.error(f"Error stopping DataManagerService: {str(e)}")

    def get_data(self, command_set_name: str, command_name: str) -> Union[list[Any], Any]:
        data_list = self.data_manager.command_set_data_list.get(command_set_name)
        self.logger.info(f"Data list: {data_list}")
        if data_list:
            return data_list.get(command_name, [])
        else:
            return []

    def get_data_set(self, command_set_name: str) -> CommandSetData:
        data_set = self.data_manager.command_set_data_list.get(command_set_name)
        return data_set

    def get_connection_status(self, query_type:DataManagerQueryType) -> Dict[str, Any]:
        """Get connection status information."""
        # Check if the service is disabled or not properly initialized
        if not hasattr(self, 'data_manager') or not hasattr(self, '_lock') or not hasattr(self, 'logger'):
            return {
                'is_connected': False,
                'success_connection': False,
                'connection_error': f'DataManagerService is disabled or not properly initialized',
                'last_check': time.time(),
                'uptime_seconds': 0,
                'service_running': False
            }

        try:
            with self._lock:
                self.logger.debug(f"Checking connection status for query type: {query_type.value}")
                is_connected = self.data_manager.is_connected(query_type)
                connection_error = self.data_manager.get_connection_error(query_type)
                success_connection = self.data_manager.get_success_connection(query_type)
                return {
                    'is_connected': is_connected,
                    'connection_error': connection_error,
                    'success_connection': success_connection,
                    'last_check': time.time(),
                    'uptime_seconds': time.time() - getattr(self, '_start_time', time.time()),
                    'service_running': self.is_running
                }
        except Exception as e:
            self.logger.error(f"Error checking connection status: {e}")
            return {
                'is_connected': is_connected,
                'success_connection': False,
                'connection_error': f"Error checking status: {str(e)}",
                'last_check': time.time(),
                'uptime_seconds': 0,
                'service_running': getattr(self, 'is_running', False)
            }

    def unschedule_command(self, command_set_name: str, command_name: str, sync_to_s3:bool = False) -> bool:
        try:
            with (self._lock):
               result = self.data_manager.execute_command(command_set_name, command_name)
               if sync_to_s3:
                    self.data_manager.sync_to_s3_folder(command_set_name)
               return result.get('success', False)
        except Exception as e:
            self.logger.error(f"Error unscheduling command {command_name}: {str(e)}")
        return False
