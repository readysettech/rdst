"""RDST Cache commands — manage shallow caches in a deployed ReadySet instance.

Delegates all business logic to CacheService; this module handles CLI
rendering (Rich console output) and RdstResult conversion.

Subcommands:
    add       Create a shallow cache for a query
    show      List cached queries
    delete    Remove a cache by ID
    drop-all  Remove all caches
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

from lib.services.cache_service import CacheService
from lib.services.types import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheInput,
    CacheListEvent,
    CacheOptions,
    ErrorEvent,
    ProgressEvent,
)
from lib.ui import (
    get_console,
    DataTable,
    Icons,
    InlineSQL,
    MessagePanel,
    StyledPanel,
    StyleTokens,
)

from .rdst_cli import RdstResult


class CacheCommands:
    """Manage shallow caches on a deployed ReadySet instance."""

    def __init__(self):
        self._console = get_console()

    def _error(self, message: str, hint: Optional[str] = None) -> RdstResult:
        self._console.print(MessagePanel(message, variant="error", hint=hint))
        return RdstResult(False, "")

    # ------------------------------------------------------------------
    # rdst cache show
    # ------------------------------------------------------------------

    def show(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
    ) -> RdstResult:
        """List cached queries in ReadySet."""
        if not target:
            return self._error("Target is required.", hint="rdst cache show --target <name>")

        input_data = CacheInput(target=target)
        success, data, error_msg = asyncio.run(self._execute_list(input_data, json_output, target))
        if not success:
            if error_msg:
                return self._error(error_msg)
            return RdstResult(False, "")
        return RdstResult(True, "")

    async def _execute_list(self, input_data: CacheInput, json_output: bool, target: str):
        service = CacheService()
        last_event = None
        async for event in service.list_caches(input_data):
            last_event = event

        if isinstance(last_event, ErrorEvent):
            return (False, None, last_event.message)

        if isinstance(last_event, CacheListEvent):
            caches = last_event.caches
            if json_output:
                print(json.dumps({"success": True, "caches": caches, "count": last_event.count}, indent=2))
            else:
                if not caches:
                    self._console.print(MessagePanel(
                        f"No caches found on target '{target}'.\n\n"
                        f"  Create one: rdst cache add <query> --target {target}",
                        variant="info",
                    ))
                else:
                    columns = ["Hash", "Cache Name", "Query", "Type", "TTL"]
                    rows = []
                    for cache in caches:
                        fb = cache.get("fallback", "")
                        cache_type = cache.get("type", "")
                        ttl = cache.get("ttl", "")
                        reg_hash = cache.get("registry_hash", "")
                        rows.append((
                            reg_hash[:8] if reg_hash else "-",
                            cache.get("cache_name", cache.get("cache_id", "")),
                            cache.get("query", "")[:70],
                            cache_type,
                            ttl,
                        ))
                    table = DataTable(
                        columns=columns, rows=rows,
                        title=f"Caches on {target} ({last_event.count} total)",
                    )
                    self._console.print(table)

                    # Show connection string
                    from lib.services.password_resolver import resolve_password_value
                    from .rdst_cli import TargetsConfig
                    cfg = TargetsConfig()
                    cfg.load()
                    tc = cfg.get(target) or {}
                    engine = tc.get("engine", "postgresql")
                    proto = "mysql" if engine == "mysql" else "postgresql"
                    host = tc.get("host", "localhost")
                    port = tc.get("port")
                    user = tc.get("user", "")
                    db = tc.get("database", "")
                    pw_env = tc.get("password_env", "")
                    pw_display = f"${{{pw_env}}}" if pw_env else "<password>"
                    conn_str = f"{proto}://{user}:{pw_display}@{host}:{port}/{db}"
                    pw_note = f"\n  Password: Use ${pw_env} (same as upstream)" if pw_env else ""
                    self._console.print(StyledPanel(
                        f"Connect your application to Readyset:\n  {conn_str}{pw_note}",
                        title="Readyset Connection String",
                    ))

                    # Show compare hint
                    hashes_with_registry = [c.get("registry_hash", "")[:8] for c in caches if c.get("registry_hash")]
                    upstream = tc.get("upstream_target", target)
                    if hashes_with_registry:
                        self._console.print(
                            f"\n  Compare: rdst query cache-compare {hashes_with_registry[0]} --target {upstream} --count 100"
                        )
            return (True, caches, None)

        return (False, None, "Unexpected response")

    # ------------------------------------------------------------------
    # rdst cache add
    # ------------------------------------------------------------------

    def add(
        self,
        query: Optional[str] = None,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        tag: Optional[str] = None,
        dry_run: bool = False,
        json_output: bool = False,
    ) -> RdstResult:
        """Create a shallow cache for a query in ReadySet."""
        if not query:
            return self._error("Missing query argument.", hint="rdst cache add <query-or-hash> --target <name>")
        if not target:
            return self._error("Target is required.", hint="rdst cache add <query> --target <name>")

        # Resolve hash or name from registry if input is not raw SQL
        if not query.strip().upper().startswith(("SELECT", "WITH")):
            from lib.query_registry.query_registry import QueryRegistry
            import re
            registry = QueryRegistry()
            registry.load()
            entry = registry.get_query_by_tag(query) or registry.get_query(query)
            if entry:
                sql = entry.sql
                # Convert :p1/:p2 registry placeholders to engine-appropriate format
                if ":p" in sql:
                    engine = (target_config or {}).get("engine", "mysql")
                    if engine == "mysql":
                        sql = re.sub(r':p\d+', '?', sql)
                    else:
                        counter = [0]
                        def _pg_placeholder(m):
                            counter[0] += 1
                            return f'${counter[0]}'
                        sql = re.sub(r':p\d+', _pg_placeholder, sql)
                query = sql
            else:
                q_upper = query.strip().upper()
                if q_upper.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")):
                    return self._error("Only SELECT queries can be cached by Readyset.")
                return self._error(
                    f"'{query}' was not found in the query registry.",
                    hint="Use a SELECT query, a query name, or a registry hash.",
                )

        input_data = CacheInput(target=target, query=query, tag=tag)
        options = CacheOptions(dry_run=dry_run, json_output=json_output)
        success, data, error_msg = asyncio.run(
            self._execute_add(input_data, options, target, target_config)
        )
        if not success:
            if error_msg:
                return self._error(error_msg)
            return RdstResult(False, "")
        return RdstResult(True, "")

    async def _execute_add(
        self, input_data: CacheInput, options: CacheOptions,
        target: str, target_config: Optional[Dict],
    ):
        service = CacheService()
        last_event = None
        async for event in service.add_cache(input_data, options):
            if isinstance(event, ProgressEvent):
                self._console.print(f"\n{Icons.TOOL} {event.message}")
            last_event = event

        if isinstance(last_event, ErrorEvent):
            return (False, None, last_event.message)

        if isinstance(last_event, CacheAddEvent):
            if options.dry_run:
                if options.json_output:
                    print(json.dumps({
                        "success": True, "supported": last_event.supported,
                        "query": last_event.query, "detail": last_event.detail,
                    }, indent=2))
                else:
                    if last_event.supported:
                        self._console.print(StyledPanel(
                            f"Query is supported for caching by ReadySet.\n\n"
                            f"  Query: {str(InlineSQL(last_event.query, max_length=80))}\n\n"
                            f"  Run without --dry-run to create the cache.",
                            title="Dry Run — Supported", variant="success",
                        ))
                    else:
                        self._console.print(StyledPanel(
                            f"Query is NOT supported for caching by ReadySet.\n\n"
                            f"  Query:  {str(InlineSQL(last_event.query, max_length=80))}\n"
                            f"  Detail: {last_event.detail or ''}\n",
                            title="Dry Run — Not Supported", variant="error",
                        ))
            else:
                if last_event.success and last_event.supported:
                    if options.json_output:
                        print(json.dumps({
                            "success": True, "query": last_event.query,
                            "query_hash": last_event.query_hash, "target": target,
                        }, indent=2))
                    else:
                        saved_hash = last_event.query_hash
                        upstream = (target_config or {}).get("upstream_target", target)
                        compare_hint = f"  Compare: rdst query cache-compare {saved_hash} --target {upstream} --count 100\n" if saved_hash else ""
                        self._console.print(StyledPanel(
                            f"Shallow cache created successfully\n\n"
                            f"  Query: {str(InlineSQL(last_event.query, max_length=80))}\n"
                            f"  Target: {target}\n"
                            + (f"  Hash: {saved_hash}\n" if saved_hash else "")
                            + f"\n  View caches: rdst cache show --target {target}\n"
                            f"  Delete:      rdst cache delete <cache_id> --target {target}\n"
                            + compare_hint,
                            title="Cache Created", variant="success",
                        ))
                elif not last_event.supported:
                    return (False, None, f"Query not supported: {last_event.detail or ''}")
            return (True, None, None)

        return (False, None, "Unexpected response")

    # ------------------------------------------------------------------
    # rdst cache delete
    # ------------------------------------------------------------------

    def delete(
        self,
        cache_id: Optional[str] = None,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
    ) -> RdstResult:
        """Remove a cache from ReadySet by cache ID."""
        if not cache_id:
            return self._error("Missing cache ID.", hint="rdst cache delete <cache_id> --target <name>")
        if not target:
            return self._error("Target is required.", hint="rdst cache delete <id> --target <name>")

        input_data = CacheInput(target=target, cache_id=cache_id)
        success, _, error_msg = asyncio.run(
            self._execute_delete(input_data, json_output, target)
        )
        if not success:
            if error_msg:
                return self._error(error_msg)
            return RdstResult(False, "")
        return RdstResult(True, "")

    async def _execute_delete(self, input_data: CacheInput, json_output: bool, target: str):
        service = CacheService()
        last_event = None
        async for event in service.delete_cache(input_data):
            last_event = event

        if isinstance(last_event, ErrorEvent):
            return (False, None, last_event.message)

        if isinstance(last_event, CacheDeleteEvent):
            if json_output:
                print(json.dumps({"success": True, "cache_id": last_event.cache_id, "action": "deleted"}, indent=2))
            else:
                self._console.print(MessagePanel(
                    f"Cache '{last_event.cache_id}' deleted from target '{target}'.",
                    variant="success",
                ))
            return (True, None, None)

        return (False, None, "Unexpected response")

    # ------------------------------------------------------------------
    # rdst cache drop-all
    # ------------------------------------------------------------------

    def drop_all(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
        yes: bool = False,
    ) -> RdstResult:
        """Remove all caches from ReadySet."""
        if not target:
            return self._error("Target is required.", hint="rdst cache drop-all --target <name>")

        # Interactive confirmation stays in CLI layer
        if not yes:
            self._console.print(MessagePanel(
                f"About to drop all caches from target '{target}'.",
                variant="warning",
            ))
            try:
                answer = input("\n  Continue? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("y", "yes"):
                return RdstResult(True, "Cancelled.")

        input_data = CacheInput(target=target)
        success, _, error_msg = asyncio.run(
            self._execute_drop_all(input_data, json_output, target)
        )
        if not success:
            if error_msg:
                return self._error(error_msg)
            return RdstResult(False, "")
        return RdstResult(True, "")

    async def _execute_drop_all(self, input_data: CacheInput, json_output: bool, target: str):
        service = CacheService()
        last_event = None
        async for event in service.drop_all(input_data):
            last_event = event

        if isinstance(last_event, ErrorEvent):
            return (False, None, last_event.message)

        if isinstance(last_event, CacheDropAllEvent):
            if json_output:
                print(json.dumps({"success": True, "action": "drop-all", "count": last_event.count}, indent=2))
            else:
                if last_event.count == 0:
                    self._console.print(MessagePanel(
                        f"No caches to remove on target '{target}'.",
                        variant="info",
                    ))
                else:
                    self._console.print(MessagePanel(
                        f"All {last_event.count} cache(s) dropped from target '{target}'.",
                        variant="success",
                    ))
            return (True, None, None)

        return (False, None, "Unexpected response")

    # ------------------------------------------------------------------
    # rdst cache remove
    # ------------------------------------------------------------------

    def remove(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
        yes: bool = False,
    ) -> RdstResult:
        """Remove a Readyset cache deployment — stops container (if local) and removes target config."""
        import subprocess

        from .rdst_cli import TargetsConfig

        # Fall back to default target if none specified
        if not target:
            cfg = TargetsConfig()
            cfg.load()
            target = cfg.get_default()
            if target:
                target_config = cfg.get(target)

        if not target:
            return self._error("No target specified and no default configured.", hint="rdst cache remove --target <name>")
        if not target_config:
            return self._error(f"Target '{target}' not found in configuration.")

        # If they passed an upstream target, find its cache target
        if target_config.get("target_type") != "readyset":
            cfg = TargetsConfig()
            cfg.load()
            cache_target = None
            cache_config = None
            for name, config in cfg._data.get("targets", {}).items():
                if config.get("target_type") == "readyset" and config.get("upstream_target") == target:
                    cache_target = name
                    cache_config = config
                    break
            if not cache_config:
                conventional = f"{target}-cache"
                check = cfg.get(conventional)
                if check and check.get("target_type") == "readyset":
                    cache_target = conventional
                    cache_config = check
            if not cache_config:
                return self._error(
                    f"No cache found for target '{target}'.",
                    hint=f"Nothing to remove. Deploy first with:\n  rdst cache deploy --target {target} --mode docker",
                )
            target = cache_target
            target_config = cache_config

        container_name = target_config.get("container_name")
        deploy_mode = target_config.get("deploy_mode", "")
        host = target_config.get("host", "")
        upstream = target_config.get("upstream_target", "unknown")

        if not yes:
            from lib.ui import Confirm
            msg = f"Remove cache target '{target}'"
            if deploy_mode == "docker" and container_name:
                msg += f" and stop Docker container '{container_name}'"
            msg += "?"
            if not Confirm.ask(msg, default=False):
                return RdstResult(False, "Cancelled")

        container_stopped = False
        if deploy_mode == "docker" and container_name:
            try:
                result = subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    container_stopped = True
                else:
                    self._console.print(f"[{StyleTokens.WARNING}]Could not remove container '{container_name}': {result.stderr.strip()}[/{StyleTokens.WARNING}]")
            except Exception as e:
                self._console.print(f"[{StyleTokens.WARNING}]Could not remove container: {e}[/{StyleTokens.WARNING}]")
        elif deploy_mode == "systemd":
            self._console.print(f"[{StyleTokens.MUTED}]Systemd service — stop manually:\n  sudo systemctl stop readyset-{upstream}[/{StyleTokens.MUTED}]")
        elif deploy_mode in ("kubernetes", "remote") or (host and host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0")):
            self._console.print(f"[{StyleTokens.MUTED}]Remote/K8s cache at {host} — not managed locally.\nRemove the Readyset instance manually if needed.[/{StyleTokens.MUTED}]")
        elif container_name:
            try:
                result = subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    container_stopped = True
            except Exception:
                pass

        # Remove target from config
        cfg = TargetsConfig()
        cfg.load()
        if target in cfg._data.get("targets", {}):
            del cfg._data["targets"][target]
            if cfg.get_default() == target:
                cfg.set_default(upstream)
            cfg.save()

        if json_output:
            print(json.dumps({"success": True, "target_removed": target, "container_stopped": container_stopped, "upstream": upstream}, indent=2))
        else:
            parts = [f"Cache target '{target}' removed."]
            if container_stopped:
                parts.append(f"Docker container '{container_name}' stopped and removed.")
            self._console.print(MessagePanel("\n".join(parts), variant="success"))

        return RdstResult(True, " ")
