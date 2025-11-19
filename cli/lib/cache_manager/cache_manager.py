import threading
from dataclasses import dataclass
from enum import Enum
from multiprocessing.pool import ThreadPool
import pandas

from lib.data_manager_service import DataManagerService, DMSDbType
from legacy_data_manager import LegacyDataManager
from configuration_manager import ConfigurationManager
from constants import EXCHANGE_BUCKET, GLOBAL_CACHE_SCHEMA
from utils import upload_to_s3
from logger import CustomLogger
import os

def log_call_trace(logger, function_name=None, extra_info=None, num_frames=5):
    """
    Log the call trace showing how a function was called.

    Args:
        logger: Logger instance to use for output
        function_name: Name of the function being traced (optional, will auto-detect if None)
        extra_info: Dictionary of additional info to log (optional)
        num_frames: Number of stack frames to show (default: 5)
    """
    import traceback
    import inspect

    # Get the current stack trace
    stack = traceback.extract_stack()

    # Auto-detect function name if not provided
    if function_name is None:
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back
            function_name = caller_frame.f_code.co_name
        finally:
            del frame

    # Log the trace header
    logger.info(f"=== CALL TRACE FOR {function_name} ===")

    # Log extra info if provided
    if extra_info:
        for key, value in extra_info.items():
            # Truncate long values
            if isinstance(value, str) and len(value) > 100:
                value = value[:100] + "..."
            logger.info(f"{key}: {value}")

    # Log the call chain (lightweight version)
    call_chain = " -> ".join([
        f"{frame.name}({frame.filename.split('/')[-1]}:{frame.lineno})"
        for frame in stack[-(num_frames + 2):-1]  # Exclude current frame
    ])
    logger.info(f"Call chain: {call_chain}")

    # Log detailed frames
    logger.info("Detailed stack frames:")
    for i, frame in enumerate(stack[-(num_frames + 1):-1]):
        logger.info(f"  Frame {i}: {frame.filename.split('/')[-1]}:{frame.lineno} in {frame.name}")
        if frame.line:
            logger.info(f"    Code: {frame.line.strip()}")

    logger.info(f"=== END CALL TRACE ===")


def log_simple_call_trace(logger, function_name=None, extra_info=None):
    """
    Log a simplified call trace showing just the immediate call chain.

    Args:
        logger: Logger instance to use for output
        function_name: Name of the function being traced (optional, will auto-detect if None)
        extra_info: Dictionary of additional info to log (optional)
    """
    import traceback
    import inspect

    # Auto-detect function name if not provided
    if function_name is None:
        frame = inspect.currentframe()
        try:
            caller_frame = frame.f_back
            function_name = caller_frame.f_code.co_name
        finally:
            del frame

    # Get just the calling function names
    stack = traceback.extract_stack()
    call_chain = " -> ".join([
        f"{frame.name}({frame.filename.split('/')[-1]}:{frame.lineno})"
        for frame in stack[-4:-1]
    ])

    # Build log message
    log_parts = [f"{function_name} called via: {call_chain}"]

    if extra_info:
        for key, value in extra_info.items():
            if isinstance(value, str) and len(value) > 100:
                value = value[:100] + "..."
            log_parts.append(f"{key}: {value}")

    logger.info(" | ".join(log_parts))

class CacheOperation(Enum):
    ADD = "add"
    REMOVE = "remove"

@dataclass
class CacheQuery:
    query_id: str
    cache_operation: CacheOperation
    
class ThreadSafeSet:
    def __init__(self):
        self._set = {}
        self._lock = threading.Lock()

    def add(self, cache_query):
        with self._lock:
            self._set[cache_query.query_id] = cache_query

    def remove(self, cache_query):
        with self._lock:
            if cache_query.query_id in self._set:
                del self._set[cache_query.query_id]

    def get_all(self):
        with self._lock:
            return list(self._set.values())
        
        
class CacheManager:
    def __init__(self,
                 logger: CustomLogger = None,
                 workflow_id: str = None,
                 config_manager:ConfigurationManager = None,
                 use_thread_pool: bool = False,
                 num_query_threads: int = 5,
                 query_pilot_enabled:bool = False,
                 global_cache_file_path:str = "/opt/readyset/data/global_cache.csv",
                 data_manager_service: DataManagerService = None):

        self.logger = logger
        self.is_active = False
        self.query_pilot_enabled = query_pilot_enabled
        if config_manager:
            self.readyset_data_manager = LegacyDataManager(db_user=config_manager.db_user,
                                                           db_password=config_manager.db_password,
                                                           db_port=config_manager.readyset_port,
                                                           s3_region=config_manager.region_name,
                                                           db_type=config_manager.db_type,
                                                           db_name=config_manager.db_name,
                                                           logger=self.logger,
                                                           s3_operations=data_manager_service.s3_operation)


            if query_pilot_enabled:
                # This will be replaced by the data manager service in the future.
                self.proxysql_data_manager = LegacyDataManager(db_user="admin_proxysql_readyset",
                                                               db_type=config_manager.db_type,
                                                               db_hostname="127.0.0.1",
                                                               db_port=6032,
                                                               db_password="admin_pass",
                                                               s3_region=config_manager.region_name,
                                                               logger=self.logger,
                                                               s3_operations=data_manager_service.s3_operation)
            else:
                self.proxysql_data_manager = None
            self.cluster_id = config_manager.cluster_id
            self.config_manager = config_manager
            self.workflow_id = workflow_id
            self.cache_schema =  ["query_id", "cache_name", "query_text", "fallback_behavior", "count"]
            self.global_cache_schema = GLOBAL_CACHE_SCHEMA
            self.cache_always_schema = ["query_id", "always"]
            self.use_thread_pool = use_thread_pool
            self.num_query_threads = num_query_threads
            self.data_manager_service = data_manager_service
            if use_thread_pool:
                self.thread_pool = ThreadPool(self.num_query_threads)
            self.threadsafe_set = ThreadSafeSet()
            self.is_active = True
            self.global_cache_file_path = global_cache_file_path
            self.readyset_query_pilot_denylist_file_path = "/opt/readyset/readyset_query_pilot_denylist"

    def get_cached_query_ids(self):
        self.check_if_initialized()

        try:
            # Check if the global cache file exists
            import os
            if os.path.exists(self.global_cache_file_path):
                # Read the global cache file
                df = pandas.read_csv(self.global_cache_file_path)

                # Extract and return the query_ids
                if 'query_id' in df.columns and not df.empty:
                    return df['query_id'].tolist()
                else:
                    self.logger.warning("No query_id column found in global cache file or file is empty")
                    return []
            else:
                self.logger.warning(f"Global cache file not found at {self.global_cache_file_path}")
                return []
        except Exception as e:
            self.logger.error(f"Error retrieving query IDs from global cache: {e}")
            return []

    def clear_caches(self):

        self.check_if_initialized()
        try:
            query = f"DROP ALL CACHES"
            self.readyset_data_manager.db_query.execute_query(query=query)
            self.logger.info("Cleared all the cache.", highlight=True)
        except Exception as e:
            self.logger.error(f"Delete cache exception E203: {e}")


    def get_current_activity(self):
        if self.is_active:
            return [str(cache) for cache in self.threadsafe_set.get_all()]
        else:
            return []

    def check_if_initialized(self):
        if not self.is_active:
            raise Exception("Cache manager has not been initialized.")

    def write_readyset_query_cache_status(self, success:bool = True):
        self.check_if_initialized()

        file_name = f"{self.workflow_id}_async_cache.txt"
        file_path = f"/home/{self.config_manager.user}/{file_name}"

        with open(file_path, 'w') as file:
            file.write('success' if success else 'failed')
        
        s3_file_path = f"{self.config_manager.cluster_id}/{self.config_manager.instance_id}/cache_query/{self.workflow_id}_async_cache.txt"
        self.logger.info(file_path)
        upload_to_s3(s3_client=self.data_manager_service.s3_operation,
                     local_file=file_name,
                     file_name=s3_file_path,
                     bucket_name=self.config_manager.readyset_data_exchange_s3_bucket,
                     region_name=self.config_manager.region_name,
                     user=self.config_manager.user)


    def _extract_always_data_from_df_s3(self, df_s3: pandas.DataFrame) -> pandas.DataFrame:
        self.check_if_initialized()

        try:
            # Ensure DataFrame is not empty
            if df_s3 is not None and not df_s3.empty:
                # Handle different column counts
                if len(df_s3.columns) == 9:
                    # For 9-column format (GLOBAL_CACHE_SCHEMA): extract query_id and always columns
                    df_result = pandas.DataFrame({
                        "query_id": df_s3['query_id'],
                        "always": df_s3['always'].astype(str).str.lower()
                    })
                    return df_result
                elif len(df_s3.columns) == 5:
                    # Extract the first column ('query_id') and process the last column ('always')
                    df_s3['always_flag'] = df_s3.iloc[:, -1].astype(
                        str).str.lower()  # Convert to string (ensure 'true', 'false')
                    return df_s3.iloc[:, [0, -1]].rename(
                        columns={df_s3.columns[0]: "query_id", df_s3.columns[-1]: "always"})
                elif len(df_s3.columns) == 4:
                    # Extract the first column ('query_id') and fill the last column with 'False'
                    df_result = pandas.DataFrame({
                        "query_id": df_s3.iloc[:, 0],
                        "always": ['false'] * len(df_s3)  # Default to 'false'
                    })
                    return df_result
                else:
                    # Try to extract by column names if they exist
                    if 'query_id' in df_s3.columns and 'always' in df_s3.columns:
                        df_result = pandas.DataFrame({
                            "query_id": df_s3['query_id'],
                            "always": df_s3['always'].astype(str).str.lower()
                        })
                        return df_result
                    else:
                        self.logger.warning(
                            f"Unexpected DataFrame structure with {len(df_s3.columns)} columns. Cannot find required columns.")
                        return pandas.DataFrame(columns=["query_id", "always"])
            else:
                # Return an empty DataFrame with default column names
                return pandas.DataFrame(columns=["query_id", "always"])
        except Exception as e:
            # Log the exception if needed and return an empty DataFrame
            self.logger.error(f"Error processing DataFrame: {e}")
            return pandas.DataFrame(columns=["query_id", "always"])


    def update_blacklisted_cache_file(self):
        self.check_if_initialized()

        denylist_file_path = self.readyset_query_pilot_denylist_file_path

        try:
            self.logger.info(f"Starting update of ReadySet query pilot denylist file {denylist_file_path}...",
                             file_path=denylist_file_path, highlight=True)

            blacklisted_cache_names = self.get_blacklisted_cache_names()
            self.logger.info(f"ReadySet query pilot denylist file {denylist_file_path} "
                             f"contains {len(blacklisted_cache_names)} blacklisted caches.", highlight=True)
            os.makedirs(os.path.dirname(denylist_file_path), exist_ok=True)

            with open(denylist_file_path, 'w') as file:
                if blacklisted_cache_names:
                    for cache_name in blacklisted_cache_names:
                        # Default values
                        db_name = self.config_manager.db_name

                        # Parse any format like "prefix_dbname_d_0xdigest"
                        if '_d_0x' in cache_name:
                            # Find the position of "_d_0x"
                            d_0x_index = cache_name.find('_d_0x')

                            # Extract the part before "_d_0x"
                            prefix_and_db = cache_name[:d_0x_index]

                            # Find the first underscore to skip the prefix
                            first_underscore = prefix_and_db.find('_')
                            if first_underscore >= 0:
                                # Extract the database name (everything between first underscore and "_d_")
                                db_name = prefix_and_db[first_underscore + 1:]

                            # Extract just the hex digest (everything after "_d_")
                            digest = cache_name[d_0x_index + 3:]  # +3 to skip "_d_"
                            if digest.startswith('0x'):
                                digest = '0x' + digest[2:].upper()
                            else:
                                digest = digest.upper()

                        elif cache_name.startswith('0x'):
                            digest = cache_name
                        else:
                            digest = f"0x{cache_name}"

                        file.write(f"db={db_name} digest={digest}\n")

                    self.logger.info(
                        f"Successfully wrote {len(blacklisted_cache_names)} blacklisted entries to denylist file",
                        entry_count=len(blacklisted_cache_names),
                        file_path=denylist_file_path,
                        db_name=self.config_manager.db_name,
                        sample_entries=blacklisted_cache_names[:3],  # Log first 3 for reference
                        highlight=True)
                else:
                    file.write("")
                    self.logger.info("No blacklisted caches found, created empty denylist file",
                                     file_path=denylist_file_path, highlight=True)

            os.chmod(denylist_file_path, 0o644)

            self.logger.info("Successfully updated ReadySet query pilot denylist file",
                             file_path=denylist_file_path,
                             db_name=self.config_manager.db_name,
                             highlight=True)

        except Exception as e:
            self.logger.error(f"Failed to update ReadySet query pilot denylist file: {e}",
                              error_type=type(e).__name__,
                              file_path=denylist_file_path,
                              highlight=True)
            raise

    def get_deep_strategy_global_caches(self):
        self.check_if_initialized()

        try:
            df_s3 = self.readyset_data_manager.get_s3_object_as_df(
                EXCHANGE_BUCKET[self.config_manager.env],
                f'{self.cluster_id}/global/cached_queries.csv')

            df_s3.to_csv(self.global_cache_file_path, index=False)

            if df_s3 is not None and not df_s3.empty:
                deep_strategy_caches = df_s3[df_s3['strategy'] == 'deep']

                if 'blacklisted' in deep_strategy_caches.columns:
                    deep_strategy_caches = deep_strategy_caches[
                        (deep_strategy_caches['blacklisted'] != 'True') &
                        (deep_strategy_caches['blacklisted'] != True)
                        ]

                if 'query_text' in deep_strategy_caches.columns:
                    deep_strategy_caches.loc[:, 'query_text'] = (
                        deep_strategy_caches['query_text'].replace('\n', ' ', regex=True))
                self.logger.debug(f"Deep strategy caches: {deep_strategy_caches}", highlight=True)
                return deep_strategy_caches
            else:
                self.logger.warning("No data returned from get_s3_object_as_df")
                return pandas.DataFrame(columns=self.global_cache_schema)

        except Exception as err:
            self.logger.warning(f"Error fetching or filtering deep cache data: {err}")
            return pandas.DataFrame(columns=self.global_cache_schema)


    def get_blacklisted_cache_names(self):
        self.check_if_initialized()
        try:
            # Get the full cached_queries.csv which contains the blacklisted column
            df_s3 = self.readyset_data_manager.get_s3_object_as_df(
                EXCHANGE_BUCKET[self.config_manager.env],
                f'{self.cluster_id}/global/cached_queries.csv')

            if df_s3 is not None and not df_s3.empty:
                # Check if blacklisted column exists
                if 'blacklisted' not in df_s3.columns:
                    self.logger.warning("No 'blacklisted' column found in cached queries file")
                    return []

                # Filter to only include blacklisted caches
                blacklisted_caches = df_s3[
                    (df_s3['blacklisted'] == 'True') |
                    (df_s3['blacklisted'] == True)
                    ]

                if blacklisted_caches.empty:
                    self.logger.info("No blacklisted caches found")
                    return []

                # Extract cache names
                if 'cache_name' in blacklisted_caches.columns:
                    cache_names = blacklisted_caches['cache_name'].dropna().tolist()
                    self.logger.info(f"Found {len(cache_names)} blacklisted caches: {cache_names}")
                    return cache_names
                else:
                    self.logger.warning("No 'cache_name' column found in cached queries file")
                    return []
            else:
                self.logger.warning("No data returned from cached_queries.csv")
                return []

        except Exception as err:
            self.logger.error(f"Error fetching blacklisted cache names: {err}")
            return []


    def get_shallow_proxysql_strategy_global_caches(self):
        self.check_if_initialized()

        try:
            config_manager = ConfigurationManager()
            # Get the full cached_queries.csv which contains the strategy column
            df_s3 = self.proxysql_data_manager.get_s3_object_as_df(
                EXCHANGE_BUCKET[self.config_manager.env]
                if not config_manager.external_aws_account_id
                else f"readysetdeployment-{config_manager.local_aws_account_id}",
                f'{self.cluster_id}/global/cached_queries.csv')

            if df_s3 is not None and not df_s3.empty:
                # Filter to only include rows where the strategy is 'shallow_proxysql'
                deep_strategy_caches = df_s3[df_s3['strategy'] == 'shallow_proxysql']

                # Exclude blacklisted caches
                if 'blacklisted' in deep_strategy_caches.columns:
                    deep_strategy_caches = deep_strategy_caches[
                        (deep_strategy_caches['blacklisted'] != 'True') &
                        (deep_strategy_caches['blacklisted'] != True)
                        ]

                # Clean up query text for display
                if 'query_text' in deep_strategy_caches.columns:
                    # Create a proper copy first
                    deep_strategy_caches = deep_strategy_caches.copy()
                    deep_strategy_caches['query_text'] = deep_strategy_caches['query_text'].replace('\n', ' ',
                                                                                                    regex=True)
                return deep_strategy_caches
            else:
                self.logger.warning("No data returned from get_s3_object_as_df")
                return pandas.DataFrame(columns=self.global_cache_schema)

        except Exception as err:
            self.logger.warning(f"Error fetching or filtering deep cache data: {err}")
            return pandas.DataFrame(columns=self.global_cache_schema)


    # Fetch and prepare the cache data
    def fetch_deep_cache_data(self):
        self.check_if_initialized()

        NUM_RELEVANT_COLS = 3
        db_caches = self.readyset_data_manager.get_caches()

        three_cols_schema = self.cache_schema[:NUM_RELEVANT_COLS]
        try:
            if db_caches is not None:
                df_query = pandas.DataFrame(db_caches, columns=self.cache_schema)
                df_query_three_cols = df_query.iloc[:, :NUM_RELEVANT_COLS]
                df_query['query_text'] = df_query['query_text'].replace('\n', ' ', regex=True)
            else:
                self.logger.error("No data returned from get_caches")
                df_query_three_cols = pandas.DataFrame(columns=three_cols_schema)
        except Exception as e:
            self.logger.warning(f"No data returned from get_caches: {e}")
            df_query_three_cols = pandas.DataFrame(columns=three_cols_schema)

        try:
            df_s3 = self.get_deep_strategy_global_caches()

            df_s3_always = self._extract_always_data_from_df_s3(df_s3)
            if df_s3 is not None:
                df_s3_three_cols = df_s3.iloc[:, :NUM_RELEVANT_COLS]
            else:
                self.logger.warning("No data returned from get_s3_object_as_df")
                df_s3_three_cols = pandas.DataFrame(columns=three_cols_schema)

        except Exception as err:
            self.logger.warning(f"Error fetching data from S3 (global/cached_queries.csv): {err}")
            df_s3_three_cols = pandas.DataFrame(columns=three_cols_schema)
            df_s3_always = pandas.DataFrame(columns=self.cache_always_schema)

        return (df_query_three_cols,
                df_s3_three_cols,
                df_s3_always)


    def run_query(self, query:str, cache_query: CacheQuery):
        self.check_if_initialized()
        log_simple_call_trace(logger=self.logger)
        try:
            self.logger.info(f"Executing cache query: {query} for query_id: {cache_query.query_id}")
            result = self.readyset_data_manager.db_query.execute_query(query=query)
            self.logger.info(f"Successfully executed cache query for query_id: {cache_query.query_id}")
            return result
        except Exception as e:
            self.logger.error(f"Failed to execute cache query for query_id: {cache_query.query_id}. Error: {str(e)}")
            self.logger.error(f"Query text: {query}")
            return None
        finally:
            self.threadsafe_set.remove(cache_query)


    def run_query_in_thread(self, query: str, cache_query: CacheQuery):
        self.check_if_initialized()
        # Add the query to the ThreadSafeSet
        self.threadsafe_set.add(cache_query)
        if self.use_thread_pool:
            # Submit a new request to the pool
            self.thread_pool.apply_async(
                # Function to execute in the thread
                func=self.run_query,
                # Arguments to pass to the function
                args=(query, cache_query,)
            )
            if len(self.threadsafe_set.get_all()) > self.num_query_threads:
                self.logger.warning(f"There are more tasks then available query threads. Available threads ({self.num_query_threads}) "
                               f"Number of query tasks {len(self.threadsafe_set.get_all())} .")
        else:
            self.run_query(query, cache_query)
            self.threadsafe_set.remove(cache_query)


    def add_deep_cached_queries(self, df_new_queries, is_async=False, df_s3_always: pandas.DataFrame = None, timeout_seconds=10):
        self.check_if_initialized()

        for _, outer_row in df_new_queries.iterrows():
            try:
                is_always = False
                query_id = outer_row['query_id']
                query = outer_row['query_text']
                cache_name = outer_row['cache_name']

                if not df_s3_always.empty:
                    for _, inner_row in df_s3_always.iterrows():
                        # Check if the 'query_id' matches and the corresponding value is 'true'
                        if inner_row[self.cache_always_schema[0]] == query_id and inner_row[self.cache_always_schema[1]] == 'true':
                            is_always = True
                            break

                cache_modifiers = ""
                commands = []
                if is_async:
                    commands.append("CONCURRENTLY")
                if is_always:
                    commands.append("ALWAYS")
                if commands:
                    cache_modifiers = f" {' '.join(commands)} "

                add_query = f"CREATE CACHE {cache_modifiers}{cache_name} FROM {query}"
                self.logger.info(f"add_query = {add_query}")
                cache_query = CacheQuery(query_id=query_id,cache_operation=CacheOperation.ADD)
                self.run_query_in_thread(query=add_query, cache_query=cache_query)

            except Exception as e:
                self.logger.error(f"Create cache exception E202: {e}")


    def remove_deep_cached_queries(self, df_queries_to_remove):
        self.check_if_initialized()

        for _, row in df_queries_to_remove.iterrows():
            try:
                cache_name = row['cache_name']
                query_id = row['query_id']
                remove_query = f"DROP CACHE {cache_name}"
                cache_query = CacheQuery(query_id=query_id, cache_operation=CacheOperation.REMOVE)
                self.run_query_in_thread(query=remove_query, cache_query=cache_query)
            except Exception as e:
                self.logger.error(f"Delete cache exception E203: {e}")


    def apply_cache_changes(self):
        self.check_if_initialized()

        try:
            self.update_blacklisted_cache_file()

            # Apply deep cache changes.
            self.apply_deep_cache_changes()

            # Apply shallow cache changes.
            self.apply_shallow_proxysql_cache_changes()

            if self.config_manager.enable_async_query_caching:
                self.write_readyset_query_cache_status(success=True)
            if self.data_manager_service and self.data_manager_service.is_running:
                self.data_manager_service.unschedule_command(
                    "cached_queries", "cached_queries", True)
                self.data_manager_service.unschedule_command(
                    "proxied_queries", "proxied_queries", True)
        except Exception as e:
            self.logger.error(f"apply_cache_changes exception {e}")
            if self.config_manager.enable_async_query_caching:
                self.write_readyset_query_cache_status(success=False)


    def apply_deep_cache_changes(self):
        self.check_if_initialized()

        try:
            df_query_three_cols, df_s3_three_cols, df_s3_always = self.fetch_deep_cache_data()
            queries_in_db = set(df_query_three_cols['query_id'])
            queries_in_s3 = set(df_s3_three_cols['query_id'])
            new_queries_in_s3 = queries_in_s3 - queries_in_db
            queries_missing_from_s3 = queries_in_db - queries_in_s3
            df_new_queries_in_s3 = df_s3_three_cols[df_s3_three_cols['query_id'].isin(new_queries_in_s3)]
            df_queries_missing_from_s3 = df_query_three_cols[
                df_query_three_cols['query_id'].isin(queries_missing_from_s3)]

            self.add_deep_cached_queries(df_new_queries=df_new_queries_in_s3,
                             is_async=self.config_manager.enable_async_query_caching,
                             df_s3_always=df_s3_always)

            self.remove_deep_cached_queries(df_queries_missing_from_s3)
        except Exception as e:
            self.logger.error(f"apply_deep_cache_changes exception {e}")
            raise

    def apply_shallow_proxysql_cache_changes(self):
        self.check_if_initialized()
        try:
            if not self.proxysql_data_manager:
                self.logger.warning("ProxySQL data manager is not available, skipping shallow cache changes",
                                    highlight=True)
                return

            # Get global shallow proxysql caches
            global_shallow_proxysql_caches = self.get_shallow_proxysql_strategy_global_caches()

            if global_shallow_proxysql_caches.empty:
                self.logger.warning("No global shallow ProxySQL caches found", highlight=True)
                return

            global_digest_ids = global_shallow_proxysql_caches['query_id'].tolist()
            # Remove 'd_' prefix from global digest IDs for consistent comparison
            clean_global_digest_ids = []
            for digest_id in global_digest_ids:
                # Extract just the hex part after the last 'd_' occurrence
                if 'd_0x' in str(digest_id):
                    clean_global_digest_ids.append(str(digest_id)[str(digest_id).rindex('d_') + 2:])
                else:
                    clean_global_digest_ids.append(str(digest_id))

            db_type_str = "mysql" if self.proxysql_data_manager.db_type == DMSDbType.MySql else "psql"
            query = f"SELECT DISTINCT(digest) FROM {db_type_str}_query_rules WHERE comment LIKE '%shallow_cache_proxysql%';"

            active_digests_result = self.proxysql_data_manager.execute_and_process_query(query=query)

            # Ensure we have a list and extract digest values properly
            if active_digests_result is None:
                self.logger.warning("No active digests returned from database query", highlight=True)
                active_digests_result = []

            # Handle both a list of tuples and a list of strings
            if active_digests_result and isinstance(active_digests_result[0], (tuple, list)):
                active_digests_ids = [str(row[0]) for row in active_digests_result if row and row[0] is not None]
            else:
                active_digests_ids = [str(digest) for digest in active_digests_result if digest is not None]

            # Find all the active_digest_ids that are not in the global_digest_ids
            digests_to_remove = list(set(active_digests_ids) - set(map(str, clean_global_digest_ids)))

            # Remove the digests from the query_rules table
            if digests_to_remove:
                try:
                    # Remove digests one by one to avoid parameter issues
                    rows_affected = 0
                    for digest in digests_to_remove:
                        if 'd_0x' in digest:
                            # Extract just the hex part from any format that contains d_0x
                            clean_digest = digest[digest.index('d_0x') + 2:]
                        else:
                            clean_digest = digest

                        # Split the multi-statement query into separate queries
                        delete_queries = [
                            f"DELETE FROM {db_type_str}_query_rules WHERE digest = '{clean_digest}' AND comment LIKE '%shallow_cache_proxysql%'",
                            f"LOAD MYSQL QUERY RULES TO RUNTIME",
                            f"SAVE MYSQL QUERY RULES TO DISK"
                        ]

                        for delete_query in delete_queries:
                            result = self.proxysql_data_manager.execute_and_process_query(query=delete_query)

                        rows_affected += 1

                    self.logger.info(f"Successfully removed {rows_affected} obsolete digest rules",
                                     rows_affected=rows_affected, highlight=True)

                except Exception as delete_error:
                    self.logger.error(f"Failed to delete obsolete digests: {delete_error}",
                                      error_type=type(delete_error).__name__,
                                      digests_attempted=len(digests_to_remove), highlight=True)
                    raise
            else:
                self.logger.debug("No obsolete digest rules found to remove", highlight=True)

            # Apply all the queries in the global_shallow_proxysql_caches
            if 'proxysql_create_query' not in global_shallow_proxysql_caches.columns:
                self.logger.error("Missing 'proxysql_create_query' column in global shallow ProxySQL caches",
                                  available_columns=list(global_shallow_proxysql_caches.columns), highlight=True)
                raise ValueError("Missing required 'proxysql_create_query' column")

            proxysql_create_query_rules = global_shallow_proxysql_caches['proxysql_create_query'].tolist()
            successful_queries = 0
            failed_queries = 0

            for i, query in enumerate(proxysql_create_query_rules, 1):
                try:
                    self.proxysql_data_manager.execute_and_process_query(query=query)
                    successful_queries += 1

                except Exception as query_error:
                    failed_queries += 1
                    self.logger.error(f"Failed to execute ProxySQL create query {i}: {query_error}",
                                      query_number=i, error_type=type(query_error).__name__,
                                      query_preview=query[:100] + "..." if len(query) > 100 else query,
                                      highlight=True)

        except Exception as e:
            self.logger.error(f"apply_shallow_proxysql_cache_changes exception: {e}",
                              error_type=type(e).__name__,
                              proxysql_manager_available=self.proxysql_data_manager is not None,
                              highlight=True)
            raise

    def clear_all_query_pilot_routes(self):
        self.check_if_initialized()
        
        self.logger.info("Starting clear_all_query_pilot_routes method",
                    method="clear_all_query_pilot_routes", 
                    highlight=True)
        
        try:
            if not self.proxysql_data_manager:
                self.logger.warning("ProxySQL data manager is not available, skipping shallow cache changes",
                              proxysql_manager_available=False,
                              highlight=True)
                return

            self.logger.info("ProxySQL data manager is available, proceeding with route clearing",
                       proxysql_manager_available=True,
                       highlight=True)

            db_type_str = "mysql" if self.proxysql_data_manager.db_type == DMSDbType.MySql else "psql"
            self.logger.info(f"Database type determined as: {db_type_str}",
                       db_type=self.proxysql_data_manager.db_type.value,
                       db_type_str=db_type_str,
                       highlight=True)
    
            # Execute queries separately
            queries = [
                f"DELETE FROM {db_type_str}_query_rules WHERE comment LIKE '%deep_cache%' OR comment LIKE '%shallow_cache_proxysql%'",
                f"LOAD MYSQL QUERY RULES TO RUNTIME",
                f"SAVE MYSQL QUERY RULES TO DISK"
            ]
            
            for i, query in enumerate(queries, 1):
                self.logger.info(f"Executing query {i}/{len(queries)}: {query[:100]}...",
                           query_number=i,
                           total_queries=len(queries),
                           highlight=True)
                
                result = self.proxysql_data_manager.execute_and_process_query(query=query)
                
                self.logger.info(f"Successfully executed query {i}/{len(queries)}",
                           query_number=i,
                           result_type=type(result).__name__ if result is not None else "None",
                           highlight=True)
    
            self.logger.info("Successfully executed all query pilot routes clearing queries",
                       total_queries=len(queries),
                       highlight=True)

        except Exception as e:
            self.logger.error(f"clear_all_query_pilot_routes exception: {e}",
                         error_type=type(e).__name__,
                         error_message=str(e),
                         proxysql_manager_available=self.proxysql_data_manager is not None,
                         db_type_available=hasattr(self, 'proxysql_data_manager') and 
                                         hasattr(self.proxysql_data_manager, 'db_type') if self.proxysql_data_manager else False,
                         highlight=True)
            raise
    
        self.logger.info("Completed clear_all_query_pilot_routes method successfully",
               method="clear_all_query_pilot_routes",
               highlight=True)

__all__ = [
    'CacheManager',
    'CacheOperation',
    'CacheQuery',
    'ThreadSafeSet',
    'log_call_trace',
    'log_simple_call_trace',
]
