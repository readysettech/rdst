"""Cache service — manages ReadySet cache deployment and query caching.

Provides async generator methods consumed by both CLI and web API.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from lib.cli.rdst_cli import TargetsConfig
from lib.services.password_resolver import resolve_password_value
from lib.services.types import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDeployCompleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheInput,
    CacheListEvent,
    CacheOptions,
    CacheRunCompleteEvent,
    CacheStatusEvent,
    ErrorEvent,
    ProgressEvent,
)


def _normalize_for_match(sql: str) -> str:
    """Normalize SQL for matching ReadySet queries against registry entries."""
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
    """Manages ReadySet cache deployment and query caching."""

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

    def _connection_kwargs(self, target_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract connection kwargs from target config."""
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
        """Execute SQL against a ReadySet instance. Synchronous."""
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
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            saved_hash, _is_new = registry.add_query(
                sql=query, tag=tag or "", source="cache", target=target,
            )
            return saved_hash
        except Exception:
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

            # Correlate with registry
            registry_map = await asyncio.to_thread(self._build_registry_map)
            for cache in caches:
                query_text = cache.get("query", "")
                registry_hash = self._lookup_registry_hash(query_text, registry_map)
                cache["registry_hash"] = registry_hash or ""

            yield CacheListEvent(
                type="cache_list", success=True, caches=caches, count=len(caches),
            )
        except Exception as e:
            yield ErrorEvent(type="error", message=str(e), stage="list")

    @staticmethod
    def _build_registry_map() -> Dict[str, str]:
        """Build normalized SQL → registry hash map."""
        try:
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            result = {}
            for entry in registry.list_queries():
                key = _normalize_for_match(entry.sql)
                if key:
                    result[key] = entry.hash
            return result
        except Exception:
            return {}

    @staticmethod
    def _lookup_registry_hash(
        query_text: str, registry_map: Dict[str, str]
    ) -> Optional[str]:
        """Look up a ReadySet cache query in the registry."""
        if not query_text or not registry_map:
            return None
        try:
            key = _normalize_for_match(query_text)
            return registry_map.get(key)
        except Exception:
            return None

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

            # Static cacheability check
            from lib.functions.readyset_cacheability import check_readyset_cacheability

            static = check_readyset_cacheability(query=query)
            if not static.get("cacheable"):
                issues = static.get("issues") or ["Unknown issue"]
                yield ErrorEvent(
                    type="error",
                    message=f"Query not cacheable: {'; '.join(issues)}",
                    stage="add",
                )
                return

            yield ProgressEvent(
                type="progress", stage="explain", percent=30,
                message="Testing query cacheability...",
            )

            # EXPLAIN CREATE CACHE
            explain_result = await asyncio.to_thread(
                self._run_readyset_sql,
                f"EXPLAIN CREATE CACHE FROM {query}",
                **conn,
            )
            if not explain_result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"EXPLAIN CREATE CACHE failed: {explain_result.get('error', '')}",
                    stage="add",
                )
                return

            output = explain_result.get("output", "")
            first_line = output.strip().split("\n")[0].lower()
            is_unsupported = "unsupported" in first_line or re.search(r'\bno\b', first_line)

            if options.dry_run:
                yield CacheAddEvent(
                    type="cache_add",
                    success=True,
                    supported=not is_unsupported,
                    query=query,
                    detail=output,
                )
                return

            if is_unsupported:
                yield CacheAddEvent(
                    type="cache_add",
                    success=True,
                    supported=False,
                    query=query,
                    detail=output,
                )
                return

            # CREATE SHALLOW CACHE
            yield ProgressEvent(
                type="progress", stage="create", percent=60,
                message="Creating shallow cache...",
            )

            create_result = await asyncio.to_thread(
                self._run_readyset_sql,
                f"CREATE SHALLOW CACHE FROM {query}",
                **conn,
            )
            if not create_result["success"]:
                yield ErrorEvent(
                    type="error",
                    message=f"CREATE SHALLOW CACHE failed: {create_result.get('error', '')}",
                    stage="add",
                )
                return

            # Save to registry
            saved_hash = await asyncio.to_thread(
                self._save_to_registry, query, input_data.tag, input_data.target,
            )

            yield CacheAddEvent(
                type="cache_add",
                success=True,
                supported=True,
                query=query,
                query_hash=saved_hash,
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
                result = await asyncio.to_thread(
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
    # Deploy
    # ------------------------------------------------------------------

    async def deploy(
        self, input_data: CacheInput, options: CacheOptions
    ) -> AsyncGenerator[CacheEvent, None]:
        """Deploy ReadySet cache for a database target.

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

            from lib.deploy.script_generator import build_variables

            variables = build_variables(
                target_name=target,
                target_config=target_config,
                password=password,
                port=options.port,
                deploy_config=options.deploy_config,
                namespace=options.namespace or "readyset",
            )

            yield ProgressEvent(
                type="progress", stage="deploying", percent=30,
                message=f"Deploying ReadySet ({options.mode})...",
            )

            if options.mode == "kubernetes":
                from lib.deploy.kubernetes import deploy_kubernetes

                result = await asyncio.to_thread(
                    deploy_kubernetes, target, variables, password,
                    namespace=options.namespace or "readyset",
                    kubeconfig=options.kubeconfig,
                )
            elif options.host:
                from lib.deploy.remote import deploy_remote

                result = await asyncio.to_thread(
                    deploy_remote, target, variables, password,
                    mode=options.mode,
                    host=options.host or "",
                    ssh_key=options.ssh_key,
                    ssh_user=options.ssh_user or "root",
                )
            elif options.mode == "systemd":
                from lib.deploy.local_systemd import deploy_local_systemd

                result = await asyncio.to_thread(
                    deploy_local_systemd, target, variables, password
                )
            elif options.mode == "docker":
                from lib.deploy.local_docker import deploy_local_docker

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
                endpoint_host = "127.0.0.1"
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

            cache_target_name = await asyncio.to_thread(
                self._register_cache_target,
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
        """Auto-register the deployed ReadySet instance as a new target."""
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
        """Run query against origin DB and ReadySet cache, compare latency.

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

            origin_kwargs = self._connection_kwargs(target_config)
            cache_kwargs = self._connection_kwargs(cache_config)

            from queue import Queue, Empty
            from lib.functions.performance_comparison import run_comparison

            progress_queue: Queue = Queue()

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
                ),
            )

            stage_labels = {
                "warmup": "Warming up",
                "origin": "Benchmarking origin",
                "cache": "Benchmarking cache",
            }

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

            result = future.result()

            if not result.get("success"):
                yield ErrorEvent(
                    type="error",
                    message=result.get("error", "Comparison failed"),
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
            yield ErrorEvent(type="error", message=str(e), stage="run")
