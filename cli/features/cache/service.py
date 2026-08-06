"""Cache service — manages Readyset cache deployment and query caching.

Provides async generator methods consumed by both CLI and web API.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from shared.config.targets import TargetsConfig
from shared.deploy.docker_topology import DockerTopology
from shared.password_resolver import resolve_password_value
from shared.service_events import ErrorEvent, ProgressEvent
from shared.async_utils import run_blocking

from .events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDeployCompleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheLifecycleEvent,
    CacheListEvent,
    CacheRunCompleteEvent,
    CacheStatusEvent,
)
from .models import CacheInput, CacheOptions


def _normalize_for_match(sql: str) -> str:
    """Normalize SQL for matching Readyset queries against registry entries."""
    s = sql.strip()
    s = re.sub(r'\$(\d+)', r':p\1', s)
    s = re.sub(r'\?', ':p', s)
    s = re.sub(r'\s+', ' ', s).lower().strip()
    s = s.rstrip(';').strip()
    return s


def _parse_show_caches(output: str) -> List[Dict[str, str]]:
    """Parse SHOW CACHES tab-separated output into list of dicts."""
    if not output:
        return []
    caches = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) >= 3:
            fallback = parts[3] if len(parts) > 3 else ""
            cache_type, ttl = _parse_fallback(fallback)
            caches.append({
                "cache_id": parts[0],
                "cache_name": parts[1],
                "query": parts[2],
                "type": cache_type or "shallow",
                "ttl": ttl or "-",
                "fallback": fallback,
            })
    return caches


def _parse_fallback(fallback: str) -> Tuple[str, str]:
    """Parse fallback string like 'shallow, ttl 10000 ms'."""
    if not fallback:
        return ("", "")
    parts = [p.strip() for p in fallback.split(",")]
    cache_type = parts[0] if parts else ""
    ttl_display = ""
    for part in parts[1:]:
        part = part.strip()
        if part.startswith("ttl "):
            try:
                ms = int(part.split()[1])
                ttl_display = f"{ms // 1000}s" if ms >= 1000 else f"{ms}ms"
            except (ValueError, IndexError):
                ttl_display = part
    if not ttl_display and "fallback allowed" in fallback.lower():
        cache_type = "full"
        ttl_display = "-"
    return (cache_type, ttl_display)


class CacheService:
    """Manages Readyset cache deployment and query caching."""

    def __init__(
        self, cache_target_config: Dict[str, Any] | None = None
    ) -> None:
        self._cache_target_config = cache_target_config

    # ------------------------------------------------------------------
    # Target resolution
    # ------------------------------------------------------------------

    def _resolve_cache_target(
        self, database_target: str
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Find the cache target for a database target.

        Tries "{target}-cache" naming convention first, then searches
        all targets for one with upstream_target matching.
        """
        if self._cache_target_config is not None:
            return (database_target, self._cache_target_config)

        config = TargetsConfig()
        config.load()

        # Check if target itself is already a readyset target
        direct_config = config.get(database_target)
        if direct_config and direct_config.get("target_type") == "readyset":
            return (database_target, direct_config)

        # Try convention name first
        cache_name = f"{database_target}-cache"
        cache_config = config.get(cache_name)
        if cache_config and cache_config.get("target_type") == "readyset":
            return (cache_name, cache_config)

        # Fallback: search by upstream_target
        for name in config.list_targets():
            entry = config.get(name)
            if (
                entry
                and entry.get("target_type") == "readyset"
                and entry.get("upstream_target") == database_target
            ):
                return (name, entry)

        return None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _connection_kwargs(
        self,
        target_config: Dict[str, Any],
        target_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extract connection kwargs from target config."""
        if target_config.get("target_type") != "readyset":
            from shared.db_connection import resolve_connection_params

            params = resolve_connection_params(
                target=target_name,
                target_config=target_config,
            )
            return {
                "host": params["host"],
                "port": int(params["port"]),
                "engine": params["engine"],
                "user": params["user"],
                "database": params["database"],
                "password": params["password"],
            }
        return {
            "host": target_config.get("host", "localhost"),
            "port": int(target_config.get("port", 5433)),
            "engine": target_config.get("engine", "postgresql"),
            "user": target_config.get("user", "postgres"),
            "database": target_config.get("database", ""),
            "password": resolve_password_value(target_config),
        }

    def _build_endpoint(self, target_config: Dict[str, Any]) -> str:
        """Build connection endpoint string."""
        engine = target_config.get("engine", "postgresql")
        port = target_config.get("port", 5433)
        user = target_config.get("user", "")
        db = target_config.get("database", "")
        host = target_config.get("host", "localhost")
        proto = "mysql" if engine == "mysql" else "postgresql"
        return f"{proto}://{user}@{host}:{port}/{db}"

    def _run_readyset_sql(
        self,
        sql: str,
        *,
        host: str,
        port: int,
        engine: str,
        user: str,
        database: str,
        password: str,
    ) -> Dict[str, Any]:
        """Execute SQL against a Readyset instance. Synchronous."""
        conn = None
        try:
            if engine == "mysql":
                import pymysql
                import pymysql.cursors

                conn = pymysql.connect(
                    host=host, port=int(port), user=user,
                    password=password, database=database,
                    connect_timeout=10, read_timeout=30,
                    autocommit=True, cursorclass=pymysql.cursors.Cursor,
                )
            else:
                import psycopg2

                conn = psycopg2.connect(
                    host=host, port=int(port), user=user,
                    password=password, database=database,
                    connect_timeout=10, options="-c statement_timeout=30000",
                )
                conn.autocommit = True

            with conn.cursor() as cursor:
                cursor.execute(sql)
                if cursor.description:
                    rows = cursor.fetchall()
                    output = "\n".join(
                        "\t".join(
                            str(col) if col is not None else "" for col in row
                        )
                        for row in rows
                    )
                    return {"success": True, "output": output}
                return {"success": True, "output": ""}
        except ImportError:
            driver = "pymysql" if engine == "mysql" else "psycopg2-binary"
            return {"success": False, "error": f"{driver} not installed"}
        except Exception as e:
            return {"success": False, "error": str(e).strip()}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _save_to_registry(
        self, query: str, tag: Optional[str], target: str
    ) -> Optional[str]:
        """Save query to registry. Returns hash or None."""
        try:
            from shared.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            saved_hash, _is_new = registry.add_query(
                sql=query, tag=tag or "", source="cache", target=target,
            )
            return saved_hash
        except Exception:
            return None

    def _update_registry_readyset_identity(
        self,
        query_hash: str,
        readyset_query_id: str,
        readyset_supported: str,
        cache_target: str,
    ) -> None:
        """Persist ReadySet's canonical q_<hash> on the registry row.

        Sparse update — only called when we've interacted with a ReadySet container
        and have an authoritative ID. Used as the source-of-truth ID for DROP CACHE
        and other cache lifecycle ops.
        """
        try:
            from shared.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            registry.update_readyset_identity(
                query_hash=query_hash,
                readyset_query_id=readyset_query_id,
                readyset_supported=readyset_supported,
                cache_target=cache_target,
            )
        except Exception:
            pass

    @staticmethod
    def _resolve_cache_id_for_drop(cache_id: str) -> Optional[str]:
        """Translate a user-supplied cache identifier to ReadySet's `q_<hash>` form.

        Accepts:
          - `q_<hex>` (ReadySet's canonical id) → returned as-is
          - `<8-16 hex>` (RDST's client-side query hash) → looked up in registry
            for the stored `readyset_query_id`. Fixes CLD-1754 where DROP CACHE
            was called with our hash and silently failed.

        Returns the resolved ReadySet id, or None if no mapping is known.
        """
        if not cache_id:
            return None
        if cache_id.startswith("q_"):
            return cache_id
        if re.fullmatch(r"[0-9a-fA-F]{8,16}", cache_id):
            try:
                from shared.query_registry import QueryRegistry
                registry = QueryRegistry()
                registry.load()
                entry = registry._queries.get(cache_id)
                if entry and entry.readyset_query_id:
                    return entry.readyset_query_id
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(
        self, input_data: CacheInput
    ) -> AsyncGenerator[CacheEvent, None]:
        """Check cache deployment status for a database target.

        Yields: CacheStatusEvent
        """
        try:
            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield CacheStatusEvent(
                    type="cache_status", deployed=False, running=False,
                )
                return

            cache_name, cache_config = resolved
            container_name = cache_config.get(
                "container_name", f"rdst-readyset-{input_data.target}"
            )
            host = cache_config.get("host", "")

            if not host:
                # Endpoint not configured yet (non-local deploy)
                yield CacheStatusEvent(
                    type="cache_status",
                    deployed=True,
                    running=False,
                    endpoint=None,
                    cache_target=cache_name,
                    container_name=container_name,
                )
                return

            endpoint = self._build_endpoint(cache_config)
            running = await asyncio.to_thread(
                self._check_cache_reachable, cache_config
            )

            yield CacheStatusEvent(
                type="cache_status",
                deployed=True,
                running=running,
                endpoint=endpoint,
                cache_target=cache_name,
                container_name=container_name,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="status")

    def _check_cache_reachable(self, cache_config: Dict[str, Any]) -> bool:
        """Check if the cache is reachable by opening a TCP connection.

        Works for any deploy method — Docker, k8s, SSH remote, systemd, etc.
        """
        import socket

        host = cache_config.get("host", "127.0.0.1")
        port = int(cache_config.get("port", 5433))
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
            return True
        except (OSError, socket.timeout):
            return False

    # ------------------------------------------------------------------
    # List caches
    # ------------------------------------------------------------------

    async def list_caches(
        self, input_data: CacheInput
    ) -> AsyncGenerator[CacheEvent, None]:
        """List cached queries.

        Yields: CacheListEvent or ErrorEvent
        """
        try:
            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache deployed for target '{input_data.target}'.",
                    stage="list",
                )
                return

            _cache_name, cache_config = resolved
            conn = self._connection_kwargs(cache_config)

            result = await asyncio.to_thread(
                self._run_readyset_sql, "SHOW CACHES", **conn
            )
            if not result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"SHOW CACHES failed: {result.get('error', '')}",
                    stage="list",
                )
                return

            caches = _parse_show_caches(result.get("output", ""))

            # Correlate with registry. Two-key match:
            #   1. by ReadySet's `q_<hash>` (precise, populated when we cache through `cache add`)
            #   2. by normalized SQL text (fallback for legacy entries / orphan caches)
            registry_map_by_qid, registry_map_by_sql = await asyncio.to_thread(
                self._build_registry_maps
            )
            for cache in caches:
                cache["registry_hash"] = self._correlate_registry_hash(
                    cache, registry_map_by_qid, registry_map_by_sql
                )

            yield CacheListEvent(
                type="cache_list", success=True, caches=caches, count=len(caches),
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="list")

    @staticmethod
    def _build_registry_maps() -> Tuple[Dict[str, str], Dict[str, str]]:
        """Build two registry lookup maps:
          - readyset_query_id → registry hash (precise, sparsely populated)
          - normalized SQL    → registry hash (fallback, always populated)
        Returns (by_qid, by_sql).
        """
        try:
            from shared.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            by_qid: Dict[str, str] = {}
            by_sql: Dict[str, str] = {}
            for entry in registry.list_queries():
                if entry.readyset_query_id:
                    by_qid[entry.readyset_query_id] = entry.hash
                key = _normalize_for_match(entry.sql)
                if key:
                    by_sql[key] = entry.hash
            return by_qid, by_sql
        except Exception:
            return {}, {}

    # Legacy single-map builder kept for any external callers.
    @staticmethod
    def _build_registry_map() -> Dict[str, str]:
        """DEPRECATED: use _build_registry_maps. Returns the SQL-keyed map only."""
        return CacheService._build_registry_maps()[1]

    @staticmethod
    def _lookup_registry_hash(
        query_text: str, registry_map: Dict[str, str]
    ) -> Optional[str]:
        """Look up a Readyset cache query in the registry."""
        if not query_text or not registry_map:
            return None
        try:
            key = _normalize_for_match(query_text)
            return registry_map.get(key)
        except Exception:
            return None

    @staticmethod
    def _correlate_registry_hash(
        cache: Dict[str, str],
        by_qid: Dict[str, str],
        by_sql: Dict[str, str],
    ) -> str:
        """Map a parsed cache to its registry hash.

        Matches on ReadySet's canonical query_id first (`cache_id`, the
        `q_<hash>` from SHOW CACHES), which is exact even when the cached
        query's parameters are reordered (`:p2 ... :p1` becomes `$1 ... $2`).
        Falls back to normalized-SQL matching only for caches with no stored
        query_id; that fallback maps `$N -> :pN` by number, so it rescues
        literal-SQL orphans but not parameterized ones with reordered
        placeholders. Returns "" when nothing correlates.
        """
        qid = cache.get("cache_id", "")
        return (
            by_qid.get(qid)
            or CacheService._lookup_registry_hash(cache.get("query", ""), by_sql)
            or ""
        )

    # ------------------------------------------------------------------
    # Add cache
    # ------------------------------------------------------------------

    async def add_cache(
        self, input_data: CacheInput, options: CacheOptions
    ) -> AsyncGenerator[CacheEvent, None]:
        """Add a cache for a query (or dry-run check).

        Yields: ProgressEvent → CacheAddEvent or ErrorEvent
        """
        try:
            if not input_data.query:
                yield ErrorEvent(type="error", message="Missing query.", stage="add")
                return

            # The query text reaches ReadySet interpolated into CREATE SHALLOW
            # CACHE FROM, and much of it originates in the registry (captured
            # from the database). Only a single read-only statement is cacheable
            # anyway, so refuse anything else before it is interpolated.
            from features.query_registry.service import benchmark_read_only_reason

            not_read_only = benchmark_read_only_reason(input_data.query)
            if not_read_only:
                yield ErrorEvent(
                    type="error",
                    message=f"This query cannot be cached. {not_read_only}",
                    stage="add",
                )
                return

            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache deployed for target '{input_data.target}'.",
                    stage="add",
                )
                return

            cache_name, cache_config = resolved
            conn = self._connection_kwargs(cache_config)
            query = input_data.query

            # The static check is advisory only. ReadySet's own EXPLAIN result is
            # authoritative; rejecting here can produce false negatives when the
            # static rules lag ReadySet's supported query surface.
            from .readyset_cacheability import check_readyset_cacheability

            try:
                static = check_readyset_cacheability(query=query)
            except Exception as exc:
                static = {
                    "cacheable": None,
                    "issues": [f"Static cacheability check unavailable: {exc}"],
                }
            static_issues = static.get("issues") or []
            static_cacheable = static.get("cacheable")
            static_advisory = (
                "Static advisory: "
                f"cacheable={static_cacheable if static_cacheable is not None else 'unknown'}"
                + (f"; issues={'; '.join(static_issues)}" if static_issues else "")
            )

            yield ProgressEvent(
                type="progress", stage="explain", percent=30,
                message="Testing query cacheability...",
            )

            # Convert :pN placeholders to engine-specific ReadySet form
            # ($N for Postgres, ? for MySQL). Fixes CLD-1748: previously we
            # sent `IN (?)` which Postgres-mode ReadySet rejects.
            from shared.query_registry.sql_normalizer import (
                denormalize_for_readyset,
                parse_query_id_from_explain,
                parse_supported_from_explain,
            )
            engine = (cache_config or {}).get("engine", "postgresql")
            readyset_query = denormalize_for_readyset(query, engine=engine)

            # EXPLAIN CREATE SHALLOW CACHE — also returns the canonical `q_<hash>`
            # query_id (fixes CLD-1754: stop using our own hash for DROP CACHE).
            explain_result = await asyncio.to_thread(
                self._run_readyset_sql,
                f"EXPLAIN CREATE SHALLOW CACHE FROM {readyset_query}",
                **conn,
            )
            if not explain_result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=(
                        "EXPLAIN CREATE SHALLOW CACHE failed: "
                        f"{explain_result.get('error', '')}"
                    ),
                    stage="add",
                )
                return

            output = explain_result.get("output", "")
            readyset_query_id = parse_query_id_from_explain(output) or ""
            supported_status = parse_supported_from_explain(output)
            is_unsupported = supported_status.startswith("unsupported") if supported_status else (
                # Fallback: scan first line for "unsupported"
                "unsupported" in output.strip().split("\n")[0].lower()
            )

            if options.dry_run:
                yield CacheAddEvent(
                    type="cache_add",
                    success=True,
                    supported=not is_unsupported,
                    query=query,
                    detail=f"{output}\n{static_advisory}".strip(),
                )
                return

            if is_unsupported:
                yield CacheAddEvent(
                    type="cache_add",
                    success=True,
                    supported=False,
                    query=query,
                    detail=f"{output}\n{static_advisory}".strip(),
                )
                return

            # CREATE SHALLOW CACHE
            yield ProgressEvent(
                type="progress", stage="create", percent=60,
                message="Creating shallow cache...",
            )

            create_result = await asyncio.to_thread(
                self._run_readyset_sql,
                f"CREATE SHALLOW CACHE FROM {readyset_query}",
                **conn,
            )
            if not create_result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"CREATE SHALLOW CACHE failed: {create_result.get('error', '')}",
                    stage="add",
                )
                return

            # Save to registry, then attach the canonical ReadySet query_id
            saved_hash = await asyncio.to_thread(
                self._save_to_registry, query, input_data.tag, input_data.target,
            )
            if readyset_query_id:
                await asyncio.to_thread(
                    self._update_registry_readyset_identity,
                    saved_hash, readyset_query_id, supported_status or "yes",
                    cache_name,
                )

            yield CacheAddEvent(
                type="cache_add",
                success=True,
                supported=True,
                query=query,
                query_hash=saved_hash,
                detail=static_advisory,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="add")

    # ------------------------------------------------------------------
    # Delete cache
    # ------------------------------------------------------------------

    async def delete_cache(
        self, input_data: CacheInput
    ) -> AsyncGenerator[CacheEvent, None]:
        """Delete a single cache by ID.

        Yields: CacheDeleteEvent or ErrorEvent
        """
        try:
            cache_id = input_data.cache_id
            if not cache_id:
                yield ErrorEvent(type="error", message="Missing cache ID.", stage="delete")
                return

            # Validate cache_id format (prevent SQL injection)
            if not re.match(r'^[a-zA-Z0-9_]+$', cache_id):
                yield ErrorEvent(
                    type="error",
                    message=f"Invalid cache ID format: '{cache_id}'",
                    stage="delete",
                )
                return

            # Translate our 8-16 hex query_hash → ReadySet's q_<hash> via registry.
            # Fixes CLD-1754: previously we ran DROP CACHE with our own hash, which
            # ReadySet doesn't recognize. Now we resolve the canonical id first.
            resolved_id = self._resolve_cache_id_for_drop(cache_id)
            if resolved_id is None:
                yield ErrorEvent(
                    type="error",
                    message=(
                        f"Cannot resolve cache ID '{cache_id}' to a ReadySet query_id. "
                        f"Pass a `q_<hash>` from `rdst cache show`, or use a registry hash "
                        f"that has been cached at least once."
                    ),
                    stage="delete",
                )
                return
            # The resolved id comes from the registry, so it gets the same
            # format check as the caller-supplied one before it reaches DDL.
            if not re.match(r'^[a-zA-Z0-9_]+$', resolved_id):
                yield ErrorEvent(
                    type="error",
                    message=f"Invalid resolved cache ID format: '{resolved_id}'",
                    stage="delete",
                )
                return
            cache_id = resolved_id

            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache deployed for target '{input_data.target}'.",
                    stage="delete",
                )
                return

            _cache_name, cache_config = resolved
            conn = self._connection_kwargs(cache_config)

            result = await asyncio.to_thread(
                self._run_readyset_sql, f"DROP CACHE {cache_id}", **conn
            )
            if not result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"DROP CACHE failed: {result.get('error', '')}",
                    stage="delete",
                )
                return

            yield CacheDeleteEvent(
                type="cache_delete", success=True, cache_id=cache_id,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="delete")

    # ------------------------------------------------------------------
    # Drop all caches
    # ------------------------------------------------------------------

    async def drop_all(
        self, input_data: CacheInput
    ) -> AsyncGenerator[CacheEvent, None]:
        """Drop all caches.

        Yields: CacheDropAllEvent or ErrorEvent
        """
        try:
            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache deployed for target '{input_data.target}'.",
                    stage="drop_all",
                )
                return

            _cache_name, cache_config = resolved
            conn = self._connection_kwargs(cache_config)

            # Get current count
            show_result = await asyncio.to_thread(
                self._run_readyset_sql, "SHOW CACHES", **conn
            )
            if not show_result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"SHOW CACHES failed: {show_result.get('error', '')}",
                    stage="drop_all",
                )
                return

            caches = _parse_show_caches(show_result.get("output", ""))
            count = len(caches)

            if count > 0:
                result = await run_blocking(
                    self._run_readyset_sql, "DROP ALL CACHES", **conn
                )
                if not result["success"]:
                    yield ErrorEvent(
                        type="error",
                        message=f"DROP ALL CACHES failed: {result.get('error', '')}",
                        stage="drop_all",
                    )
                    return

            yield CacheDropAllEvent(
                type="cache_drop_all", success=True, count=count,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="drop_all")

    # ------------------------------------------------------------------
    # Remove cache target
    # ------------------------------------------------------------------

    async def remove_cache_target(
        self, input_data: CacheInput
    ) -> AsyncGenerator[CacheEvent, None]:
        """Remove the cache target from config and stop the container.

        Yields: CacheDeleteEvent or ErrorEvent
        """
        try:
            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache target found for '{input_data.target}'.",
                    stage="remove",
                )
                return

            cache_name, cache_config = resolved
            container_name = cache_config.get(
                "container_name", f"rdst-readyset-{input_data.target}"
            )

            # Stop and remove Docker container (best effort)
            await asyncio.to_thread(self._remove_container, container_name)

            # Remove target from config
            await asyncio.to_thread(self._delete_target_config, cache_name)

            yield CacheDeleteEvent(
                type="cache_delete", success=True, cache_id=cache_name,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="remove")

    @staticmethod
    def _remove_container(container_name: str) -> None:
        """Stop and remove a Docker container. Best effort."""
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    @staticmethod
    def _delete_target_config(target_name: str) -> None:
        """Remove a target from the config file."""
        config = TargetsConfig()
        config.load()
        if config.get(target_name):
            config.remove(target_name)
            config.save()

    # ------------------------------------------------------------------
    # Lifecycle (start / stop / restart)
    # ------------------------------------------------------------------

    async def lifecycle(
        self, input_data: CacheInput, operation: str
    ) -> AsyncGenerator[CacheEvent, None]:
        """Start, stop, or restart a deployed cache without redeploying.

        Yields: CacheLifecycleEvent or ErrorEvent
        """
        try:
            if operation not in ("start", "stop", "restart"):
                yield ErrorEvent(
                    type="error",
                    message=f"Unknown lifecycle operation '{operation}'.",
                    stage="lifecycle",
                )
                return

            resolved = self._resolve_cache_target(input_data.target)
            if resolved is None:
                yield ErrorEvent(
                    type="error",
                    message=f"No cache target found for '{input_data.target}'.",
                    stage=operation,
                )
                return

            cache_name, cache_config = resolved
            result = await asyncio.to_thread(
                self._run_lifecycle_op, operation, cache_name, cache_config
            )
            if result.success:
                yield CacheLifecycleEvent(
                    type="cache_lifecycle",
                    operation=operation,
                    success=True,
                    state=result.state_after.value if result.state_after else None,
                    detail=result.detail,
                )
            else:
                yield ErrorEvent(
                    type="error",
                    message=result.error or f"cache {operation} failed",
                    stage=operation,
                )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage=operation)

    @staticmethod
    def _run_lifecycle_op(
        operation: str, cache_name: str, cache_config: Dict[str, Any]
    ):
        """Dispatch a lifecycle op through shared.deploy.lifecycle.

        Containers and units are named after the upstream database target,
        so pass that name rather than the cache target's.
        """
        from shared.deploy import lifecycle

        deploy_mode = cache_config.get("deploy_mode") or "docker"
        upstream = cache_config.get("upstream_target", cache_name)
        op_fn = getattr(lifecycle, operation)
        return op_fn(
            upstream,
            mode=deploy_mode,
            host=cache_config.get("host"),
            namespace=cache_config.get("namespace"),
            ssh_key=cache_config.get("ssh_key"),
            ssh_user=cache_config.get("ssh_user"),
        )

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    async def deploy(
        self, input_data: CacheInput, options: CacheOptions
    ) -> AsyncGenerator[CacheEvent, None]:
        """Deploy Readyset cache for a database target.

        Yields: ProgressEvent → CacheDeployCompleteEvent or ErrorEvent
        """
        try:
            target = input_data.target

            # Load target config
            config = TargetsConfig()
            config.load()
            target_config = config.get(target)
            if not target_config:
                available = ", ".join(config.list_targets())
                yield ErrorEvent(
                    type="error",
                    message=f"Target '{target}' not found. Available: {available}",
                    stage="deploy",
                )
                return

            password = resolve_password_value(target_config)

            yield ProgressEvent(
                type="progress", stage="preparing", percent=10,
                message="Preparing deployment...",
            )

            from shared.deploy.script_generator import build_variables

            variables = build_variables(
                target_name=target,
                target_config=target_config,
                password=password,
                port=options.port,
                deploy_config=options.deploy_config,
                namespace=options.namespace or "readyset",
                no_request_path=options.no_request_path,
                memory_bytes=options.memory_bytes,
                cpus=options.cpus or "2",
            )

            yield ProgressEvent(
                type="progress", stage="deploying", percent=30,
                message=f"Deploying Readyset ({options.mode})...",
            )

            if options.mode == "kubernetes":
                from shared.deploy.kubernetes import deploy_kubernetes

                result = await run_blocking(
                    deploy_kubernetes, target, variables, password,
                    namespace=options.namespace or "readyset",
                    kubeconfig=options.kubeconfig,
                )
            elif options.host:
                from shared.deploy.remote import deploy_remote

                result = await run_blocking(
                    deploy_remote, target, variables, password,
                    mode=options.mode,
                    host=options.host or "",
                    ssh_key=options.ssh_key,
                    ssh_user=options.ssh_user or "root",
                )
            elif options.mode == "systemd":
                from shared.deploy.local_systemd import deploy_local_systemd

                result = await run_blocking(
                    deploy_local_systemd, target, variables, password
                )
            elif options.mode == "docker":
                from shared.deploy.local_docker import deploy_local_docker

                result = await asyncio.to_thread(
                    deploy_local_docker, target, variables, password
                )
            else:
                yield ErrorEvent(
                    type="error",
                    message=(
                        f"Deploy mode '{options.mode}' is not supported. "
                        "Use: docker, systemd, kubernetes"
                    ),
                    stage="deploy",
                )
                return

            if not result.get("success"):
                yield ErrorEvent(
                    type="error",
                    message=result.get("error", "Deployment failed"),
                    stage="deploy",
                )
                return

            is_local = options.mode in ("docker", "systemd") and not options.host

            if is_local:
                endpoint_host = (
                    DockerTopology.from_environment().published_host
                    if options.mode == "docker"
                    else "127.0.0.1"
                )
            else:
                # Non-local: register with empty host — user will provide it
                endpoint_host = ""

            container_name = result.get("container_name", "")
            if not container_name and options.mode == "docker":
                container_name = variables.get("container_name", "")

            yield ProgressEvent(
                type="progress", stage="registering", percent=80,
                message="Registering cache target...",
            )

            cache_target_name = self._register_cache_target(
                target, target_config, variables, endpoint_host,
            )

            if is_local:
                cache_cfg = {
                    "engine": variables.get("db_engine", "postgresql"),
                    "host": endpoint_host,
                    "port": variables.get("readyset_port", 5433),
                    "user": variables.get("db_user", ""),
                    "database": variables.get("db_name", ""),
                }
                endpoint = self._build_endpoint(cache_cfg)
            else:
                endpoint = None

            yield CacheDeployCompleteEvent(
                type="deploy_complete",
                success=True,
                deployed=True,
                running=is_local,
                endpoint=endpoint,
                cache_target=cache_target_name or f"{target}-cache",
                container_name=container_name,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="deploy")

    async def register_cache_endpoint(
        self, input_data: CacheInput, host: str, port: int,
    ) -> AsyncGenerator[CacheEvent, None]:
        """Register a cache target with a user-provided endpoint.

        Used after non-local deploys (k8s, SSH) where we don't know the
        endpoint the user will connect from.
        """
        try:
            target = input_data.target
            config = TargetsConfig()
            config.load()
            target_config = config.get(target)
            if not target_config:
                yield ErrorEvent(
                    type="error", message=f"Target '{target}' not found.",
                    stage="register",
                )
                return

            # Verify endpoint is reachable before registering
            reachable = self._check_cache_reachable(
                {"host": host, "port": port}
            )
            if not reachable:
                yield ErrorEvent(
                    type="error",
                    message=f"Cannot connect to {host}:{port}. Check the endpoint and try again.",
                    stage="register",
                )
                return

            cache_target_name = f"{target}-cache"
            config.upsert(cache_target_name, {
                "name": cache_target_name,
                "target_type": "readyset",
                "engine": target_config.get("engine", "postgresql"),
                "host": host,
                "port": port,
                "user": target_config.get("user", ""),
                "database": target_config.get("database", ""),
                "password": target_config.get("password", ""),
                "password_env": target_config.get("password_env", ""),
                "upstream_target": target,
            })
            config.save()

            cache_cfg = {
                "engine": target_config.get("engine", "postgresql"),
                "host": host, "port": port,
                "user": target_config.get("user", ""),
                "database": target_config.get("database", ""),
            }

            yield CacheStatusEvent(
                type="cache_status",
                deployed=True,
                running=True,
                endpoint=self._build_endpoint(cache_cfg),
                cache_target=cache_target_name,
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="register")

    def _register_cache_target(
        self,
        original_target: str,
        target_config: Dict[str, Any],
        variables: Dict[str, Any],
        host: str = "127.0.0.1",
    ) -> Optional[str]:
        """Auto-register the deployed Readyset instance as a new target."""
        cache_target_name = f"{original_target}-cache"
        try:
            config = TargetsConfig()
            config.load()
            if config.get(cache_target_name):
                return cache_target_name

            config.upsert(cache_target_name, {
                "name": cache_target_name,
                "target_type": "readyset",
                "engine": target_config.get("engine", "postgresql"),
                "host": host,
                "port": int(variables.get("readyset_port", 5433)),
                "metrics_port": int(variables.get("metrics_port", 6034)),
                "user": target_config.get("user", ""),
                "database": target_config.get("database", ""),
                "password": target_config.get("password", ""),
                "password_env": target_config.get("password_env", ""),
                "upstream_target": original_target,
                "container_name": variables.get("container_name", ""),
            })
            config.save()
            return cache_target_name
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Performance comparison (origin vs cache)
    # ------------------------------------------------------------------

    async def run_comparison(
        self,
        input_data: CacheInput,
        iterations: int = 5,
        warmup: int = 2,
    ) -> AsyncGenerator[CacheEvent, None]:
        """Run query against origin DB and Readyset cache, compare latency.

        Yields: ProgressEvent -> CacheRunCompleteEvent or ErrorEvent
        """
        try:
            target = input_data.target
            query = input_data.query
            if not query:
                yield ErrorEvent(
                    type="error", message="Query is required.", stage="run",
                )
                return

            # Load origin target config
            config = TargetsConfig()
            config.load()
            target_config = config.get(target)
            if not target_config:
                yield ErrorEvent(
                    type="error",
                    message=f"Target '{target}' not found.",
                    stage="run",
                )
                return

            # Resolve cache target
            cache_result = self._resolve_cache_target(target)
            if not cache_result:
                yield ErrorEvent(
                    type="error",
                    message=(
                        f"No cache target found for '{target}'. "
                        "Deploy a cache first or add a cache target."
                    ),
                    stage="run",
                )
                return

            _cache_name, cache_config = cache_result

            yield ProgressEvent(
                type="progress", stage="connecting", percent=5,
                message="Connecting...",
            )

            origin_kwargs = self._connection_kwargs(target_config, target)
            cache_kwargs = self._connection_kwargs(cache_config)

            from queue import Queue, Empty
            from .performance_comparison import ComparisonController, run_comparison

            progress_queue: Queue = Queue()
            controller = ComparisonController()

            def _on_progress(stage: str, current: int, total: int):
                progress_queue.put((stage, current, total))

            # Run in thread, poll progress queue
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(
                None,
                lambda: run_comparison(
                    query=query,
                    original_db_config=origin_kwargs,
                    readyset_db_config=cache_kwargs,
                    iterations=iterations,
                    warmup_iterations=warmup,
                    on_progress=_on_progress,
                    controller=controller,
                ),
            )

            stage_labels = {
                "warmup": "Warming up",
                "origin": "Benchmarking origin",
                "cache": "Benchmarking cache",
            }

            try:
                while not future.done():
                    try:
                        stage, current, total = progress_queue.get_nowait()
                        label = stage_labels.get(stage, stage)
                        pct = int((current / total) * 100) if total > 0 else 0
                        # Map stages to overall percent: warmup 5-20, origin 20-60, cache 60-95
                        if stage == "warmup":
                            overall = 5 + int(pct * 0.15)
                        elif stage == "origin":
                            overall = 20 + int(pct * 0.40)
                        else:
                            overall = 60 + int(pct * 0.35)
                        yield ProgressEvent(
                            type="progress", stage=stage, percent=overall,
                            message=f"{label} ({current}/{total})",
                        )
                    except Empty:
                        await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                controller.cancel()
                future.cancel()
                raise

            result = future.result()

            if not result.get("success"):
                from shared.api.ssh_errors import connectivity_error_payload

                failure = connectivity_error_payload(
                    RuntimeError(result.get("error", "Comparison failed")),
                    target,
                    target_config,
                )
                yield ErrorEvent(
                    type="error",
                    message=(
                        failure["message"]
                        if failure
                        else result.get("error", "Comparison failed")
                    ),
                    code=failure["category"] if failure else None,
                    stage="run",
                )
                return

            yield CacheRunCompleteEvent(
                type="cache_run_complete",
                success=True,
                query=query,
                iterations=result["iterations"],
                origin_stats=result["original"]["stats"],
                cache_stats=result["readyset"]["stats"],
                speedup_mean=result["speedup"]["mean"],
                speedup_median=result["speedup"]["median"],
                improvement_pct=result["speedup"]["improvement_pct"],
                winner=result["winner"],
            )
        except Exception as e:
            from shared.api.ssh_errors import connectivity_error_payload

            failure = connectivity_error_payload(
                e,
                input_data.target,
                locals().get("target_config") or {},
            )
            yield ErrorEvent(
                type="error",
                message=failure["message"] if failure else str(e),
                code=failure["category"] if failure else None,
                stage="run",
            )
