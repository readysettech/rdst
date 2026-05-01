"""RDST Cache commands — manage shallow caches in a deployed Readyset instance.

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

from shared.cli.types import RdstResult
from shared.password_resolver import resolve_password_value
from shared.query_registry import QueryRegistry
from shared.ui import (
    DataTable,
    Icons,
    InlineSQL,
    MessagePanel,
    StyledPanel,
    StyleTokens,
    get_console,
)

from ..events import (
    CacheAddEvent,
    CacheDeleteEvent,
    CacheDropAllEvent,
    CacheEvent,
    CacheListEvent,
)
from ..models import CacheInput, CacheOptions
from ..service import CacheService
from shared.service_events import ErrorEvent, ProgressEvent


class CacheCommands:
    """Manage shallow caches on a deployed Readyset instance."""

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
        """List cached queries in Readyset."""
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
                    from shared.config.targets import TargetsConfig
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
                    metrics_port = tc.get("metrics_port")
                    metrics_line = f"\n  Metrics:  http://{host}:{metrics_port}/metrics" if metrics_port else ""
                    self._console.print(StyledPanel(
                        f"Connect your application to Readyset:\n  {conn_str}{pw_note}{metrics_line}",
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
        """Create a shallow cache for a query in Readyset."""
        if not query:
            return self._error("Missing query argument.", hint="rdst cache add <query-or-hash> --target <name>")
        if not target:
            return self._error("Target is required.", hint="rdst cache add <query> --target <name>")

        # Resolve hash or name from registry if input is not raw SQL
        if not query.strip().upper().startswith(("SELECT", "WITH")):
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
                # Safety: strip AS from table aliases in case the registry entry
                # was stored by an older version that used sqlglot's AS-adding behavior
                sql = re.sub(r'\b(\w+)\s+AS\s+(\w+)', r'\1 \2', sql)
                query = sql
            else:
                q_upper = query.strip().upper()
                if q_upper.startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")):
                    return self._error("Only SELECT queries can be cached by Readyset.")
                return self._error(
                    f"'{query}' was not found in the query registry.",
                    hint="Use a SELECT query, a query name, or a registry hash.",
                )

        # Before creating cache from SQL text, check if Readyset already has
        # this query as a proxied query. Using the proxied query ID ensures
        # the cache matches how the wire protocol sees the query, avoiding
        # normalization mismatches (e.g., sqlglot adds AS to table aliases).
        input_data = CacheInput(target=target, query=query, tag=tag)
        if target_config:
            try:
                import re as _re
                _host = target_config.get("host", "localhost")
                _port = int(target_config.get("port", 5433))
                _password = resolve_password_value(target_config)
                _engine = (target_config or {}).get("engine", "postgresql")

                if _engine == "mysql":
                    import pymysql
                    _conn = pymysql.connect(
                        host=_host, port=_port,
                        user=target_config.get("user", ""),
                        password=_password or "",
                        database=target_config.get("database", ""),
                        connect_timeout=5,
                    )
                else:
                    import psycopg2
                    _conn = psycopg2.connect(
                        host=_host, port=_port,
                        user=target_config.get("user", ""),
                        password=_password or "",
                        database=target_config.get("database", ""),
                        connect_timeout=5,
                    )
                _conn.autocommit = True
                _cur = _conn.cursor()
                _cur.execute("SHOW PROXIED QUERIES")
                _proxied = _cur.fetchall()

                # Normalize for fuzzy matching: collapse whitespace, lowercase,
                # strip all AS keywords, normalize placeholders, strip semicolons
                def _norm(s):
                    s = _re.sub(r'\s+', ' ', str(s).strip().lower()).rstrip(';')
                    # Remove ALL standalone AS (table aliases AND column aliases)
                    s = _re.sub(r'\bas\b', '', s)
                    s = _re.sub(r'\s+', ' ', s)  # re-collapse after AS removal
                    s = _re.sub(r'\$\d+', '?', s)
                    s = _re.sub(r':p\d+', '?', s)
                    s = _re.sub(r'\?', '?', s)
                    return s.strip()

                query_norm = _norm(query)
                for row in _proxied:
                    # SHOW PROXIED QUERIES: (query_id, query_text, supported, count)
                    proxied_text = str(row[1])
                    proxied_id = str(row[0])
                    if _norm(proxied_text) == query_norm:
                        # Match found — create cache directly using query ID
                        # (bypass CacheService which would fail static check on ID)
                        _cur.execute(f"CREATE SHALLOW CACHE FROM {proxied_id}")
                        try:
                            _cur.fetchall()
                        except Exception:
                            pass
                        _cur.close()
                        _conn.close()

                        # Save to registry if tag provided
                        if tag:
                            registry = QueryRegistry()
                            registry.load()
                            registry.add_query(sql=query, tag=tag, target=target)
                            registry.save()

                        self._console.print(StyledPanel(
                            f"Shallow cache created successfully (from proxied query)\n\n"
                            f"  Query ID: {proxied_id}\n"
                            f"  Target: {target}\n\n"
                            f"  View caches: rdst cache show --target {target}\n"
                            f"  Delete:      rdst cache delete {proxied_id} --target {target}",
                            title="Cache Created", variant="success",
                        ))
                        return RdstResult(True, "")

                _cur.close()
                _conn.close()
            except Exception:
                pass  # Fall through to SQL-based cache creation

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
                            f"Query is supported for caching by Readyset.\n\n"
                            f"  Query: {str(InlineSQL(last_event.query, max_length=80))}\n\n"
                            f"  Run without --dry-run to create the cache.",
                            title="Dry Run — Supported", variant="success",
                        ))
                    else:
                        self._console.print(StyledPanel(
                            f"Query is NOT supported for caching by Readyset.\n\n"
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
        """Remove a cache from Readyset by cache ID."""
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
        """Remove all caches from Readyset."""
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

    # ------------------------------------------------------------------
    # rdst cache start / stop / restart
    # ------------------------------------------------------------------

    def _resolve_cache_target(
        self,
        target: Optional[str],
        target_config: Optional[Dict[str, Any]],
    ) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[RdstResult]]:
        """Find the cache target matching `target` (which may be an upstream or cache name).

        Returns (cache_target_name, cache_target_config, error_result_or_None).
        """
        from shared.config.targets import TargetsConfig
        if not target:
            cfg = TargetsConfig()
            cfg.load()
            target = cfg.get_default()
            if target:
                target_config = cfg.get(target)
        if not target:
            return None, None, self._error("No target specified and no default configured.",
                                           hint="rdst cache <command> --target <name>")
        if not target_config:
            return None, None, self._error(f"Target '{target}' not found in configuration.")
        if target_config.get("target_type") == "readyset":
            return target, target_config, None
        # Resolve upstream → cache target
        cfg = TargetsConfig()
        cfg.load()
        for name, config in cfg._data.get("targets", {}).items():
            if config.get("target_type") == "readyset" and config.get("upstream_target") == target:
                return name, config, None
        conventional = f"{target}-cache"
        check = cfg.get(conventional)
        if check and check.get("target_type") == "readyset":
            return conventional, check, None
        return None, None, self._error(
            f"No cache target found for '{target}'.",
            hint=f"Deploy first: rdst cache deploy --target {target} --mode docker",
        )

    def _lifecycle_op(
        self,
        op_name: str,
        target: Optional[str],
        target_config: Optional[Dict[str, Any]],
        json_output: bool,
    ) -> RdstResult:
        """Dispatch start/stop/restart through shared.deploy.lifecycle."""
        from shared.deploy import lifecycle
        cache_target, cache_config, err = self._resolve_cache_target(target, target_config)
        if err:
            return err
        deploy_mode = (cache_config or {}).get("deploy_mode") or "docker"
        upstream = (cache_config or {}).get("upstream_target", cache_target)

        op_fn = getattr(lifecycle, op_name)
        result = op_fn(
            upstream,
            mode=deploy_mode,
            host=(cache_config or {}).get("host"),
            namespace=(cache_config or {}).get("namespace"),
            ssh_key=(cache_config or {}).get("ssh_key"),
            ssh_user=(cache_config or {}).get("ssh_user"),
        )
        if json_output:
            print(json.dumps({
                "success": result.success,
                "state_after": result.state_after.value if result.state_after else None,
                "detail": result.detail,
                "error": result.error,
            }, indent=2))
            return RdstResult(result.success, result.detail or result.error or "")

        if result.success:
            # Match the StyledPanel(title="...", variant=...) presentation used
            # by `cache add` / `cache remove` so lifecycle commands feel the same.
            titles = {
                "start":   "Cache Started",
                "stop":    "Cache Stopped",
                "restart": "Cache Restarted",
            }
            hints = {
                "start":   f"  View caches:  rdst cache show --target {cache_target}\n"
                           f"  Stop again:   rdst cache stop --target {upstream}",
                "stop":    f"  Start again:  rdst cache start --target {upstream}\n"
                           f"  Remove fully: rdst cache remove --target {upstream}",
                "restart": f"  View caches:  rdst cache show --target {cache_target}",
            }
            body = f"{result.detail}\n\n{hints.get(op_name, '')}".rstrip()
            self._console.print(StyledPanel(
                body,
                title=titles.get(op_name, f"Cache {op_name.capitalize()}"),
                variant="success",
            ))
            return RdstResult(True, " ")
        return self._error(result.error or f"cache {op_name} failed")

    def start(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
    ) -> RdstResult:
        """Start a stopped cache container without redeploying."""
        return self._lifecycle_op("start", target, target_config, json_output)

    def stop(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
    ) -> RdstResult:
        """Stop a running cache without removing it."""
        return self._lifecycle_op("stop", target, target_config, json_output)

    def restart(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
    ) -> RdstResult:
        """Restart a deployed cache (preserves config)."""
        return self._lifecycle_op("restart", target, target_config, json_output)

    def remove(
        self,
        target: Optional[str] = None,
        target_config: Optional[Dict[str, Any]] = None,
        json_output: bool = False,
        yes: bool = False,
    ) -> RdstResult:
        """Remove a Readyset cache deployment — stops container (if local) and removes target config."""
        import subprocess

        from shared.config.targets import TargetsConfig

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
            # Target name doesn't exist as a config row at all. Could still be
            # an orphan container the user wants removed. Idempotent path: try
            # to clean any container matching the conventional name and report.
            import subprocess
            container_name = f"rdst-readyset-{target}"
            try:
                r = subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True, text=True, timeout=15,
                )
                if r.returncode == 0:
                    self._console.print(MessagePanel(
                        f"No config row for '{target}'. Removed orphan container "
                        f"'{container_name}'.",
                        variant="success",
                    ))
                    return RdstResult(True, " ")
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
            self._console.print(MessagePanel(
                f"No cache target '{target}' in config and no container to remove. "
                f"Already cleaned up.",
                variant="info",
            ))
            return RdstResult(True, " ")

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
                # Idempotent remove: if there's nothing to clean up, that's a
                # successful no-op (matches rm -f semantics). Also probe Docker
                # in case there's an orphan container we should still flag.
                import subprocess
                container_name = f"rdst-readyset-{target}"
                container_exists = False
                try:
                    r = subprocess.run(
                        ["docker", "inspect", container_name],
                        capture_output=True, timeout=5,
                    )
                    container_exists = r.returncode == 0
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                    pass

                if container_exists:
                    # Config row gone but container still around — clean it up.
                    try:
                        subprocess.run(
                            ["docker", "rm", "-f", container_name],
                            capture_output=True, timeout=15,
                        )
                    except Exception:
                        pass
                    self._console.print(MessagePanel(
                        f"No cache config for '{target}' but container "
                        f"'{container_name}' was orphaned — removed it.",
                        variant="success",
                    ))
                else:
                    self._console.print(MessagePanel(
                        f"Nothing to remove for '{target}' (no cache target config "
                        f"and no container). Already cleaned up — this is fine.",
                        variant="info",
                    ))
                return RdstResult(True, " ")
            target = cache_target
            target_config = cache_config

        container_name = target_config.get("container_name")
        deploy_mode = target_config.get("deploy_mode", "")
        host = target_config.get("host", "")
        upstream = target_config.get("upstream_target", "unknown")

        if not yes:
            from shared.ui import Confirm
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
