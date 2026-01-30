"""TopService - Async generator-based top queries service.

This service provides real-time and historical query monitoring,
exposing an async generator interface that yields events during execution.
Supports both CLI and Web API consumers.
"""

import asyncio
import logging
import os
import re
import tempfile
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from .types import (
    TopCompleteEvent,
    TopConnectedEvent,
    TopErrorEvent,
    TopEvent,
    TopInput,
    TopOptions,
    TopQueriesEvent,
    TopQueryData,
    TopQuerySavedEvent,
    TopSourceFallbackEvent,
    TopStatusEvent,
)

logger = logging.getLogger(__name__)


class TopService:
    """Service for top queries with async event streaming.

    Supports both historical (one-shot) and real-time (streaming) modes.
    Both CLI and Web API can consume the same event stream.

    Usage:
        service = TopService()

        # Historical one-shot
        async for event in service.get_top_queries(input, options):
            handle_event(event)

        # Real-time streaming
        async for event in service.stream_realtime(input, options, duration=30):
            handle_event(event)
    """

    def __init__(self) -> None:
        """Initialize the top service."""
        pass

    async def get_top_queries(
        self,
        input: TopInput,
        options: TopOptions,
    ) -> AsyncGenerator[TopEvent, None]:
        """One-shot historical snapshot. Yields events and completes.

        Flow:
        1. TopStatusEvent("Loading configuration...")
        2. TopConnectedEvent(target, engine, source)
        3. TopSourceFallbackEvent (if fallback needed)
        4. TopStatusEvent("Fetching queries...")
        5. TopQueriesEvent(queries)
        6. TopQuerySavedEvent (for each new query saved)
        7. TopCompleteEvent

        Args:
            input: TopInput with target and source
            options: TopOptions with limit, sort, filter, etc.

        Yields:
            TopEvent: Typed events during execution
        """
        try:
            # 1. Status: Loading configuration
            yield TopStatusEvent(type="status", message="Loading configuration...")

            # Load config
            target_name, target_config, db_engine = await self._load_config(
                input.target
            )
            if target_name is None:
                yield TopErrorEvent(
                    type="error",
                    message="No target specified and no default configured. Run 'rdst configure' first.",
                    stage="config",
                )
                return

            if target_config is None:
                yield TopErrorEvent(
                    type="error",
                    message=f"Target '{target_name}' not found",
                    stage="config",
                )
                return

            # Auto-select source if needed
            source = input.source
            if source == "auto":
                source = self._auto_select_source(db_engine, target_config)

            # Validate source
            if not self._validate_source_for_engine(source, db_engine):
                valid_sources = self._get_valid_sources_for_engine(db_engine)
                yield TopErrorEvent(
                    type="error",
                    message=f"Source '{source}' not supported for {db_engine}. Valid sources: {', '.join(valid_sources)}",
                    stage="validation",
                )
                return

            # 2. Execute query and handle fallback
            yield TopStatusEvent(type="status", message="Connecting to database...")

            data, actual_source, fallback_event = await self._execute_top_query(
                target_config, db_engine, source
            )

            # 3. Connected event
            yield TopConnectedEvent(
                type="connected",
                target_name=target_name,
                db_engine=db_engine,
                source=actual_source,
            )

            # 4. Fallback event if applicable
            if fallback_event:
                yield fallback_event

            # 5. Fetching queries status
            yield TopStatusEvent(type="status", message="Processing query data...")

            # Process data
            processed_data = self._process_top_data(
                data, actual_source, options.limit, options.sort, options.filter_pattern
            )

            # Convert to TopQueryData
            queries = [
                TopQueryData(
                    query_hash=q["query_hash"],
                    query_text=q["query_text"],
                    normalized_query=q.get("normalized_query", q["query_text"]),
                    freq=q["freq"],
                    total_time=q["total_time"],
                    avg_time=q["avg_time"],
                    pct_load=q["pct_load"],
                )
                for q in processed_data
            ]

            # 6. Queries event
            yield TopQueriesEvent(
                type="queries",
                queries=queries,
                source=actual_source,
                target_name=target_name,
                db_engine=db_engine,
            )

            # 7. Auto-save queries to registry
            newly_saved = 0
            if options.auto_save_registry:
                for q in processed_data:
                    is_new = await self._save_query_to_registry(
                        q, target_name, "top-historical"
                    )
                    if is_new:
                        newly_saved += 1
                        yield TopQuerySavedEvent(
                            type="query_saved",
                            query_hash=q["query_hash"],
                            is_new=True,
                        )

            # 8. Complete event
            yield TopCompleteEvent(
                type="complete",
                success=True,
                queries=queries,
                source=actual_source,
                newly_saved=newly_saved,
            )

        except Exception as e:
            logger.exception("Error in get_top_queries")
            yield TopErrorEvent(
                type="error",
                message=str(e),
                stage="execution",
            )

    async def stream_realtime(
        self,
        input: TopInput,
        options: TopOptions,
        duration: Optional[int] = None,
    ) -> AsyncGenerator[TopEvent, None]:
        """Real-time streaming. Yields continuous query updates.

        Flow:
        1. TopConnectedEvent
        2. Polling loop (every poll_interval_ms):
           - TopQueriesEvent (with updated metrics)
           - TopQuerySavedEvent (for new queries)
        3. If duration specified, TopCompleteEvent after N seconds

        For Web API: Client receives SSE events
        For CLI: TopRenderer updates Live display

        Args:
            input: TopInput with target
            options: TopOptions with poll_interval_ms, limit, etc.
            duration: Optional duration in seconds (None for indefinite)

        Yields:
            TopEvent: Continuous events during monitoring
        """
        connection = None
        try:
            # Load config
            yield TopStatusEvent(type="status", message="Loading configuration...")

            target_name, target_config, db_engine = await self._load_config(
                input.target
            )
            if target_name is None:
                yield TopErrorEvent(
                    type="error",
                    message="No target specified and no default configured",
                    stage="config",
                )
                return

            if target_config is None:
                yield TopErrorEvent(
                    type="error",
                    message=f"Target '{target_name}' not found",
                    stage="config",
                )
                return

            # Connect to database
            yield TopStatusEvent(type="status", message="Connecting to database...")

            connection = await asyncio.to_thread(
                self._create_direct_connection, target_config
            )

            # Connected event
            yield TopConnectedEvent(
                type="connected",
                target_name=target_name,
                db_engine=db_engine,
                source="activity",  # Real-time always uses activity source
            )

            # Create collector and tracker
            from ..top_monitor import ActivityQueryCollector, QueryTracker

            collector = ActivityQueryCollector(db_engine, connection)
            tracker = QueryTracker()

            # Track saved query hashes
            saved_hashes: Set[str] = set()
            if options.auto_save_registry:
                saved_hashes = await self._load_existing_registry_hashes()

            newly_saved = 0
            start_time = time.time()
            poll_interval = options.poll_interval_ms / 1000.0

            # Polling loop
            while True:
                # Check duration limit
                elapsed = time.time() - start_time
                if duration is not None and elapsed >= duration:
                    break

                # Poll database
                try:
                    query_data = await asyncio.to_thread(collector.fetch_active_queries)
                    tracker.update(query_data)
                except Exception as e:
                    logger.debug("Poll failed: %s", e)
                    # Continue with existing data

                # Get top queries
                top_queries = tracker.get_top_n(options.limit, sort_by="max")
                runtime = tracker.get_runtime_seconds()
                total_tracked = tracker.get_total_queries_tracked()

                # Convert to TopQueryData
                queries = [
                    TopQueryData(
                        query_hash=self._compute_registry_hash(q.query_text),
                        query_text=q.query_text,
                        normalized_query=q.normalized_query,
                        freq=q.observation_count,
                        total_time=f"{q.max_duration_seen / 1000:.3f}s",
                        avg_time=f"{q.avg_duration / 1000:.3f}s",
                        pct_load="0.0%",  # Not calculated in realtime mode
                        max_duration_ms=q.max_duration_seen,
                        current_instances=q.current_instances_running,
                        observation_count=q.observation_count,
                    )
                    for q in top_queries
                ]

                # Yield queries event
                yield TopQueriesEvent(
                    type="queries",
                    queries=queries,
                    source="activity",
                    target_name=target_name,
                    db_engine=db_engine,
                    runtime_seconds=runtime,
                    total_tracked=total_tracked,
                )

                # Auto-save new queries
                if options.auto_save_registry:
                    for q in top_queries:
                        registry_hash = self._compute_registry_hash(q.query_text)
                        if registry_hash not in saved_hashes:
                            is_new = await self._save_query_to_registry(
                                {
                                    "query_hash": registry_hash,
                                    "query_text": q.query_text,
                                },
                                target_name,
                                "top",
                            )
                            if is_new:
                                saved_hashes.add(registry_hash)
                                newly_saved += 1
                                yield TopQuerySavedEvent(
                                    type="query_saved",
                                    query_hash=registry_hash,
                                    is_new=True,
                                )

                # Sleep until next poll
                await asyncio.sleep(poll_interval)

            # Complete event (only if duration was specified)
            if duration is not None:
                top_queries = tracker.get_top_n(options.limit, sort_by="max")
                final_queries = [
                    TopQueryData(
                        query_hash=self._compute_registry_hash(q.query_text),
                        query_text=q.query_text,
                        normalized_query=q.normalized_query,
                        freq=q.observation_count,
                        total_time=f"{q.max_duration_seen / 1000:.3f}s",
                        avg_time=f"{q.avg_duration / 1000:.3f}s",
                        pct_load="0.0%",
                        max_duration_ms=q.max_duration_seen,
                        current_instances=q.current_instances_running,
                        observation_count=q.observation_count,
                    )
                    for q in top_queries
                ]
                yield TopCompleteEvent(
                    type="complete",
                    success=True,
                    queries=final_queries,
                    source="activity",
                    newly_saved=newly_saved,
                )

        except Exception as e:
            logger.exception("Error in stream_realtime")
            yield TopErrorEvent(
                type="error",
                message=str(e),
                stage="execution",
            )
        finally:
            # Clean up connection
            if connection:
                try:
                    from ..db_connection import close_connection

                    close_connection(connection)
                except Exception:
                    pass

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _load_config(
        self, target: Optional[str]
    ) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
        """Load target configuration.

        Args:
            target: Target name or None for default

        Returns:
            Tuple of (target_name, target_config, db_engine) or (None, None, None)
        """
        from ..cli.rdst_cli import TargetsConfig, normalize_db_type

        cfg = TargetsConfig()
        cfg.load()
        target_name = target or cfg.get_default()

        if not target_name:
            return None, None, None

        target_config = cfg.get(target_name)
        if not target_config:
            return target_name, None, None

        db_engine = normalize_db_type(target_config.get("engine"))
        return target_name, target_config, db_engine

    def _auto_select_source(
        self, db_engine: str, target_config: Dict[str, Any]
    ) -> str:
        """Auto-select the best source for the database engine."""
        if db_engine == "postgresql":
            return "pg_stat"  # Try pg_stat_statements first, fallback in execution
        elif db_engine == "mysql":
            return "digest"  # performance_schema digest is best for MySQL
        else:
            return "activity"

    def _validate_source_for_engine(self, source: str, db_engine: str) -> bool:
        """Validate that the source is supported for the database engine."""
        valid_sources = self._get_valid_sources_for_engine(db_engine)
        return source in valid_sources

    def _get_valid_sources_for_engine(self, db_engine: str) -> List[str]:
        """Get valid sources for a database engine."""
        if db_engine == "postgresql":
            return ["auto", "pg_stat", "activity"]
        elif db_engine == "mysql":
            return ["auto", "digest", "activity"]
        else:
            return ["auto", "activity"]

    def _get_command_set_for_source(self, db_engine: str, source: str) -> str:
        """Get the appropriate command set name for the database engine and source."""
        if db_engine == "postgresql":
            if source in ["pg_stat", "auto"]:
                return "rdst_top_pg_stat"
            elif source == "activity":
                return "rdst_top_pg_activity"
        elif db_engine == "mysql":
            if source in ["digest", "auto"]:
                return "rdst_top_mysql_digest"
            elif source == "activity":
                return "rdst_top_mysql_activity"

        raise ValueError(
            f"No command set available for engine='{db_engine}' source='{source}'"
        )

    async def _execute_top_query(
        self, target_config: Dict[str, Any], db_engine: str, source: str
    ) -> tuple[Dict[str, Any], str, Optional[TopSourceFallbackEvent]]:
        """Execute the top query using DataManager.

        Returns:
            Tuple of (data dict, actual_source, optional fallback event)
        """
        result = await asyncio.to_thread(
            self._execute_top_query_sync, target_config, db_engine, source
        )
        return result

    def _execute_top_query_sync(
        self, target_config: Dict[str, Any], db_engine: str, source: str
    ) -> tuple[Dict[str, Any], str, Optional[TopSourceFallbackEvent]]:
        """Synchronous execution of top query (runs in thread)."""
        from ..data_manager.data_manager import DataManager
        from ..data_manager_service import (
            ConnectionConfig,
            DMSDbType,
            DataManagerQueryType,
        )

        # Get password from environment
        password = None
        if target_config.get("password_env"):
            password = os.getenv(target_config["password_env"])
        elif target_config.get("password"):
            password = target_config["password"]

        if not password:
            raise ValueError(
                f"No password found. Set environment variable {target_config.get('password_env', 'DB_PASSWORD')}"
            )

        # Create connection config
        connection_config = ConnectionConfig(
            host=target_config["host"],
            port=target_config["port"],
            database=target_config["database"],
            username=target_config["user"],
            password=password,
            db_type=DMSDbType.MySql if db_engine == "mysql" else DMSDbType.PostgreSQL,
            query_type=DataManagerQueryType.UPSTREAM,
        )

        # Get command set name
        fallback_event = None
        actual_source = source
        try:
            command_set_name = self._get_command_set_for_source(db_engine, source)
        except ValueError:
            # Fallback
            if db_engine == "postgresql" and source == "pg_stat":
                fallback_event = TopSourceFallbackEvent(
                    type="source_fallback",
                    from_source="pg_stat",
                    to_source="activity",
                    reason="pg_stat_statements extension not found",
                )
                command_set_name = self._get_command_set_for_source(
                    db_engine, "activity"
                )
                actual_source = "activity"
            else:
                raise

        # Create temporary output directory
        output_dir = tempfile.mkdtemp(prefix="rdst_")

        try:
            # Create a simple logger wrapper for DataManager
            class SimpleLoggerWrapper:
                def __init__(self):
                    self.logger = logging.getLogger("rdst_data_manager")
                    self.logger.setLevel(logging.INFO)

                def info(self, msg, **kwargs):
                    self.logger.info(msg)

                def debug(self, msg, **kwargs):
                    self.logger.debug(msg)

                def warning(self, msg, **kwargs):
                    if "S3 sync" in str(msg):
                        return
                    self.logger.warning(msg)

                def error(self, msg, **kwargs):
                    self.logger.error(msg)

            dm_logger = SimpleLoggerWrapper()

            # Initialize DataManager
            dm = DataManager(
                connection_config={DataManagerQueryType.UPSTREAM: connection_config},
                global_logger=dm_logger,
                command_sets=[command_set_name],
                data_directory=output_dir,
                cli_mode=True,
            )

            # Get the command name from the command set
            command_name = list(
                dm._available_commands[command_set_name]["commands"].keys()
            )[0]

            # Execute command
            import contextlib
            import io
            import sys

            if db_engine == "postgresql" and source == "pg_stat":
                # Suppress stderr for the first attempt
                stderr_capture = io.StringIO()
                with contextlib.redirect_stderr(stderr_capture):
                    result = dm.execute_command(command_set_name, command_name)
            else:
                result = dm.execute_command(command_set_name, command_name)

            # Check if command failed and we can fallback
            if (
                not result.get("success")
                and db_engine == "postgresql"
                and source == "pg_stat"
            ):
                error_msg = result.get("error", "")
                if "pg_stat_statements" in error_msg:
                    fallback_event = TopSourceFallbackEvent(
                        type="source_fallback",
                        from_source="pg_stat",
                        to_source="activity",
                        reason="pg_stat_statements extension not available",
                    )

                    # Retry with activity source
                    command_set_name = self._get_command_set_for_source(
                        db_engine, "activity"
                    )
                    command_name = list(
                        dm._available_commands[command_set_name]["commands"].keys()
                    )[0]

                    # Re-create DataManager with activity command set
                    dm = DataManager(
                        connection_config={
                            DataManagerQueryType.UPSTREAM: connection_config
                        },
                        global_logger=dm_logger,
                        command_sets=[command_set_name],
                        data_directory=output_dir,
                        cli_mode=True,
                    )
                    result = dm.execute_command(command_set_name, command_name)
                    actual_source = "activity"

            # Add source info to result
            result["source"] = actual_source
            return result, actual_source, fallback_event

        finally:
            # Clean up temporary directory
            import shutil

            try:
                shutil.rmtree(output_dir)
            except Exception:
                pass

    def _process_top_data(
        self,
        data: Dict[str, Any],
        source: str,
        limit: int,
        sort: str,
        filter_pattern: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Process and format the top queries data."""
        if not data.get("success") or data.get("data") is None:
            return []

        df = data["data"]
        if df.empty:
            return []

        # Filter out queries with insufficient privileges
        if "query_text" in df.columns:
            insufficient_mask = df["query_text"].str.contains(
                "<insufficient", case=False, na=False
            )
            df = df[~insufficient_mask].copy()

        if df.empty:
            return []

        # For activity sources, remove duplicates and system noise
        if source == "activity":
            if "duration_ms" in df.columns:
                df = df.sort_values("duration_ms", ascending=False).drop_duplicates(
                    "query_hash", keep="first"
                )
            else:
                df = df.drop_duplicates("query_hash", keep="first")

        # Apply filter if specified
        if filter_pattern:
            try:
                pattern = re.compile(filter_pattern, re.IGNORECASE)
                mask = df["query_text"].str.contains(pattern, na=False)
                df = df[mask]
            except re.error:
                mask = df["query_text"].str.contains(
                    filter_pattern, case=False, na=False
                )
                df = df[mask]

        # Normalize column names based on source
        if source in ["pg_stat", "digest"]:
            if "calls" in df.columns:
                df["freq"] = df["calls"]
            elif "count_star" in df.columns:
                df["freq"] = df["count_star"]

            if "total_time" in df.columns:
                df["total_time_sort"] = df["total_time"]
            elif "sum_timer_wait" in df.columns:
                df["total_time_sort"] = df["sum_timer_wait"]
                df["total_time"] = df["sum_timer_wait"]

            if "mean_time" in df.columns:
                df["avg_time"] = df["mean_time"]
            elif "avg_timer_wait" in df.columns:
                df["avg_time"] = df["avg_timer_wait"]
        else:
            # Activity sources
            if "time" in df.columns:
                df["freq"] = 1
                df["total_time_sort"] = df["time"].astype(float)
                df["total_time"] = df["total_time_sort"]
                df["avg_time"] = df["total_time_sort"]
                df.loc[df["total_time_sort"] == 0, "total_time_sort"] = 0.001
                df.loc[df["total_time"] == 0, "total_time"] = 0.001
                df.loc[df["avg_time"] == 0, "avg_time"] = 0.001
            elif "duration_ms" in df.columns:
                df["freq"] = 1
                df["total_time_sort"] = df["duration_ms"].astype(float) / 1000.0
                df["total_time"] = df["total_time_sort"]
                df["avg_time"] = df["total_time_sort"]

        # Calculate percentage load
        if "pct_load" not in df.columns:
            if source == "activity" and "total_time_sort" in df.columns:
                total_activity_time = df["total_time_sort"].sum()
                if total_activity_time > 0:
                    df["pct_load"] = (
                        df["total_time_sort"] / total_activity_time * 100
                    ).round(1)
                else:
                    df["pct_load"] = 0.0
            else:
                df["pct_load"] = 0.0

        # Sort the data
        sort_column_map = {
            "freq": "freq",
            "total_time": "total_time_sort",
            "avg_time": "avg_time",
            "load": "pct_load",
        }
        sort_col = sort_column_map.get(sort, "total_time_sort")
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False)

        # Limit results
        df = df.head(limit)

        # Convert to list of dicts
        results = []
        for _, row in df.iterrows():
            query_text = str(row.get("query_text", ""))
            our_hash = self._compute_registry_hash(query_text) if query_text else ""

            results.append(
                {
                    "query_hash": our_hash,
                    "query_text": query_text,
                    "normalized_query": query_text,  # Will be normalized by registry
                    "freq": int(row.get("freq", 0)),
                    "total_time": f"{float(row.get('total_time', 0)):.3f}s",
                    "avg_time": f"{float(row.get('avg_time', 0)):.3f}s",
                    "pct_load": f"{float(row.get('pct_load', 0)):.1f}%",
                }
            )

        return results

    def _create_direct_connection(self, target_config: Dict[str, Any]):
        """Create a direct database connection."""
        from ..db_connection import create_direct_connection

        return create_direct_connection(target_config)

    def _compute_registry_hash(self, query_text: str) -> str:
        """Compute the registry hash for a query."""
        try:
            from ..query_registry import hash_sql

            return hash_sql(query_text)
        except ImportError:
            import hashlib

            return hashlib.md5(query_text.encode()).hexdigest()[:12]

    async def _load_existing_registry_hashes(self) -> Set[str]:
        """Load existing query hashes from registry."""
        try:
            from ..query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            return {entry.hash for entry in registry.list_queries()}
        except Exception:
            return set()

    async def _save_query_to_registry(
        self, query_data: Dict[str, Any], target_name: str, source: str
    ) -> bool:
        """Save a query to the registry.

        Returns:
            True if query was newly saved, False if it already existed
        """
        try:
            from ..query_registry import QueryRegistry

            registry = QueryRegistry()
            query_hash, is_new = registry.add_query(
                sql=query_data["query_text"],
                source=source,
                target=target_name,
            )
            return is_new
        except ValueError as e:
            logger.debug("Query exceeds size limit: %s", e)
            return False
        except Exception as e:
            logger.debug("Failed to save query: %s", e)
            return False
