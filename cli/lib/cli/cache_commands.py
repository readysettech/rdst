"""RDST Cache commands — manage shallow caches in a deployed ReadySet instance.

Subcommands:
    add       Create a shallow cache for a query
    show      List cached queries
    delete    Remove a cache by ID
    drop-all  Remove all caches
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from lib.ui import (
    get_console,
    DataTable,
    Icons,
    InlineSQL,
    MessagePanel,
    StyledPanel,
)

from .rdst_cli import RdstResult


def _normalize_for_match(sql: str) -> str:
    """Normalize SQL for matching ReadySet queries against registry entries.

    ReadySet uses $1/$2 placeholders, the registry uses :p1/:p2.
    Convert both to a common form, collapse whitespace, and lowercase.
    """
    s = sql.strip()
    # Convert $1, $2, ... → :p1, :p2, ...
    s = re.sub(r'\$(\d+)', r':p\1', s)
    # Convert ? placeholders → :p (for MySQL prepared statements)
    s = re.sub(r'\?', ':p', s)
    # Collapse whitespace and lowercase
    s = re.sub(r'\s+', ' ', s).lower().strip()
    # Remove trailing semicolons
    s = s.rstrip(';').strip()
    return s


class CacheCommands:
    """Manage shallow caches on a deployed ReadySet instance."""

    def __init__(self):
        self._console = get_console()

    def _error(self, message: str, hint: Optional[str] = None) -> RdstResult:
        """Display a Rich-formatted error and return a failed RdstResult.

        Prints MessagePanel to console and returns RdstResult with empty
        message so rdst.py doesn't duplicate the output.
        """
        self._console.print(MessagePanel(message, variant="error", hint=hint))
        return RdstResult(False, "")

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
            return self._error(
                "Missing query argument.",
                hint="rdst cache add <query-or-hash> --target <name>",
            )
        if not target or not target_config:
            return self._error(
                "Target is required.",
                hint="rdst cache add <query> --target <name>",
            )

        # 1. Validate target is ReadySet type
        error = self._validate_readyset_target(target, target_config)
        if error is not None:
            return error

        # 2. Resolve query (hash from registry or direct SQL)
        try:
            resolved_query = self._resolve_query(query)
        except ValueError as e:
            return self._error(str(e))

        # 3. Static cacheability pre-check (fast, no network)
        from lib.functions.readyset_cacheability import check_readyset_cacheability

        static = check_readyset_cacheability(query=resolved_query)
        if not static.get("cacheable"):
            issues = static.get("issues") or ["Unknown issue"]
            return self._error(f"Query not cacheable: {'; '.join(issues)}")

        # 4. Get connection details
        conn = self._connection_kwargs(target_config)

        # 5. EXPLAIN CREATE CACHE (test against running ReadySet)
        self._console.print(f"\n{Icons.TOOL} Testing query cacheability...")

        explain_result = self._run_readyset_sql(
            f"EXPLAIN CREATE CACHE FROM {resolved_query}", **conn,
        )
        if not explain_result["success"]:
            return self._error(
                f"EXPLAIN CREATE CACHE failed:\n\n{explain_result['error']}",
            )

        # Check if supported — ReadySet EXPLAIN returns "unsupported" or "no"
        # as standalone indicators; use word boundary to avoid false positives
        output = explain_result.get("output", "")
        first_line = output.strip().split("\n")[0].lower()
        if "unsupported" in first_line or re.search(r'\bno\b', first_line):
            if dry_run:
                if json_output:
                    print(json.dumps({"success": True, "supported": False, "query": resolved_query, "detail": output}, indent=2))
                else:
                    self._console.print(StyledPanel(
                        f"Query is NOT supported for caching by ReadySet.\n\n"
                        f"  Query:  {str(InlineSQL(resolved_query, max_length=80))}\n"
                        f"  Detail: {output}\n",
                        title="Dry Run — Not Supported",
                        variant="error",
                    ))
                return RdstResult(True, "")
            return self._error(
                f"Query not supported for shallow caching by ReadySet.\n\n{output}",
            )

        # --dry-run: stop here if just checking cacheability
        if dry_run:
            if json_output:
                print(json.dumps({"success": True, "supported": True, "query": resolved_query, "detail": output}, indent=2))
            else:
                self._console.print(StyledPanel(
                    f"Query is supported for caching by ReadySet.\n\n"
                    f"  Query: {str(InlineSQL(resolved_query, max_length=80))}\n\n"
                    f"  Run without --dry-run to create the cache.",
                    title="Dry Run — Supported",
                    variant="success",
                ))
            return RdstResult(True, "")

        # 6. CREATE SHALLOW CACHE
        self._console.print(f"{Icons.ROCKET} Creating shallow cache...")

        create_result = self._run_readyset_sql(
            f"CREATE SHALLOW CACHE FROM {resolved_query}", **conn,
        )
        if not create_result["success"]:
            return self._error(
                f"CREATE SHALLOW CACHE failed:\n\n{create_result['error']}",
            )

        # 7. Save to query registry if not already there
        saved_hash = self._save_to_registry(resolved_query, tag, target)

        # 8. Display success
        if json_output:
            result_data = {
                "success": True,
                "query": resolved_query,
                "query_hash": saved_hash,
                "target": target,
                "create_output": create_result.get("output", ""),
            }
            print(json.dumps(result_data, indent=2))
        else:
            run_hint = f"  Benchmark:   rdst query run {saved_hash} --target {target}\n" if saved_hash else ""
            upstream = target_config.get("upstream_target", "")
            compare_hint = f"  Compare:     rdst query run {saved_hash} --target {upstream}\n" if saved_hash and upstream else ""
            self._console.print(StyledPanel(
                f"Shallow cache created successfully\n\n"
                f"  Query: {str(InlineSQL(resolved_query, max_length=80))}\n"
                f"  Target: {target}\n"
                + (f"  Hash: {saved_hash}\n" if saved_hash else "")
                + f"\n  View caches: rdst cache show --target {target}\n"
                f"  Delete:      rdst cache delete <cache_id> --target {target}\n"
                + run_hint
                + compare_hint,
                title="Cache Created",
                variant="success",
            ))

        return RdstResult(True, "")

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
        if not target or not target_config:
            return self._error(
                "Target is required.",
                hint="rdst cache show --target <name>",
            )

        error = self._validate_readyset_target(target, target_config)
        if error is not None:
            return error

        conn = self._connection_kwargs(target_config)

        result = self._run_readyset_sql("SHOW CACHES", **conn)
        if not result["success"]:
            return self._error(f"SHOW CACHES failed:\n\n{result['error']}")

        output = result.get("output", "").strip()
        caches = self._parse_show_caches(output, conn["engine"])

        # Correlate caches with query registry hashes
        registry_map = self._build_registry_map()
        for cache in caches:
            query_text = cache.get("query", "")
            registry_hash = self._lookup_registry_hash(query_text, registry_map)
            cache["registry_hash"] = registry_hash or ""

        if json_output:
            print(json.dumps({"success": True, "caches": caches, "count": len(caches)}, indent=2))
        else:
            if not caches:
                self._console.print(MessagePanel(
                    f"No caches found on target '{target}'.\n\n"
                    f"  Create one: python3 rdst.py cache add <query> --target {target}",
                    variant="info",
                ))
            else:
                columns = ["Hash", "Cache Name", "Query", "Type", "TTL"]
                rows = []
                for cache in caches:
                    fb = cache.get("fallback", "")
                    cache_type, ttl = self._parse_fallback(fb)
                    reg_hash = cache.get("registry_hash", "")
                    rows.append((
                        reg_hash[:8] if reg_hash else "-",
                        cache.get("cache_name", cache.get("cache_id", "")),
                        cache.get("query", "")[:70],
                        cache_type,
                        ttl,
                    ))
                table = DataTable(
                    columns=columns,
                    rows=rows,
                    title=f"Caches on {target} ({len(caches)} total)",
                )
                self._console.print(table)
                # Show run hint if any hashes were found
                hashes_with_registry = [c["registry_hash"][:8] for c in caches if c.get("registry_hash")]
                if hashes_with_registry:
                    self._console.print(
                        f"\n  Benchmark: python3 rdst.py query run {hashes_with_registry[0]} --target {target}"
                    )

        return RdstResult(True, "")

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
            return self._error(
                "Missing cache ID.",
                hint="rdst cache delete <cache_id> --target <name>",
            )
        # Validate cache_id is a safe identifier (prevent SQL injection)
        if not re.match(r'^[a-zA-Z0-9_]+$', cache_id):
            return self._error(
                f"Invalid cache ID format: '{cache_id}'",
                hint="Use 'rdst cache show --target <name>' to list valid cache IDs.",
            )
        if not target or not target_config:
            return self._error(
                "Target is required.",
                hint="rdst cache delete <id> --target <name>",
            )

        error = self._validate_readyset_target(target, target_config)
        if error is not None:
            return error

        conn = self._connection_kwargs(target_config)

        result = self._run_readyset_sql(f"DROP CACHE {cache_id}", **conn)
        if not result["success"]:
            return self._error(f"DROP CACHE failed:\n\n{result['error']}")

        if json_output:
            print(json.dumps({"success": True, "cache_id": cache_id, "action": "deleted"}, indent=2))
        else:
            self._console.print(MessagePanel(
                f"Cache '{cache_id}' deleted from target '{target}'.",
                variant="success",
            ))

        return RdstResult(True, "")

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
        if not target or not target_config:
            return self._error(
                "Target is required.",
                hint="rdst cache drop-all --target <name>",
            )

        error = self._validate_readyset_target(target, target_config)
        if error is not None:
            return error

        conn = self._connection_kwargs(target_config)

        # Get current cache count for confirmation
        show_result = self._run_readyset_sql("SHOW CACHES", **conn)
        if not show_result["success"]:
            return self._error(f"SHOW CACHES failed:\n\n{show_result['error']}")

        caches = self._parse_show_caches(
            show_result.get("output", "").strip(), conn["engine"],
        )

        if not caches:
            self._console.print(MessagePanel(
                f"No caches to remove on target '{target}'.",
                variant="info",
            ))
            return RdstResult(True, "")

        # Confirm unless --yes
        if not yes:
            self._console.print(MessagePanel(
                f"About to drop {len(caches)} cache(s) from target '{target}'.",
                variant="warning",
            ))
            try:
                answer = input("\n  Continue? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("y", "yes"):
                return RdstResult(True, "Cancelled.")

        result = self._run_readyset_sql("DROP ALL CACHES", **conn)
        if not result["success"]:
            return self._error(f"DROP ALL CACHES failed:\n\n{result['error']}")

        if json_output:
            print(json.dumps({
                "success": True,
                "action": "drop-all",
                "count": len(caches),
            }, indent=2))
        else:
            self._console.print(MessagePanel(
                f"All {len(caches)} cache(s) dropped from target '{target}'.",
                variant="success",
            ))

        return RdstResult(True, "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_readyset_target(self, target: str, target_config: Dict[str, Any]) -> Optional[RdstResult]:
        """Validate that target is a ReadySet instance. Returns error RdstResult or None."""
        target_type = target_config.get("target_type", "database")
        if target_type != "readyset":
            upstream = target_config.get("upstream_target", target)
            return self._error(
                f"Target '{target}' is a database target (target_type={target_type}).\n"
                f"Cache commands require a ReadySet target.",
                hint=(
                    f"Deploy ReadySet first:\n"
                    f"  rdst cache deploy --target {upstream} --mode docker\n\n"
                    f"Then use the auto-registered target (e.g., {upstream}-cache)."
                ),
            )
        return None

    @staticmethod
    def _parse_fallback(fallback: str) -> tuple[str, str]:
        """Parse fallback string like 'shallow, ttl 10000 ms, refresh 5000 ms, coalesce 5000 ms'.

        Returns (cache_type, ttl_display).
        """
        if not fallback:
            return ("", "")

        parts = [p.strip() for p in fallback.split(",")]
        cache_type = parts[0] if parts else ""

        ttl_display = ""
        for part in parts[1:]:
            part = part.strip()
            if part.startswith("ttl "):
                # "ttl 10000 ms" → "10s"
                try:
                    ms = int(part.split()[1])
                    ttl_display = f"{ms // 1000}s" if ms >= 1000 else f"{ms}ms"
                except (ValueError, IndexError):
                    ttl_display = part

        if not ttl_display and "fallback allowed" in fallback.lower():
            cache_type = "full"
            ttl_display = "-"

        return (cache_type, ttl_display)

    @staticmethod
    def _build_registry_map() -> Dict[str, str]:
        """Build a map of normalized SQL → registry hash for correlation.

        ReadySet stores queries with $1/$2 placeholders while the registry
        uses :p1/:p2. We normalize both sides to match them up.
        """
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
    def _lookup_registry_hash(query_text: str, registry_map: Dict[str, str]) -> Optional[str]:
        """Look up a ReadySet cache query in the registry."""
        if not query_text or not registry_map:
            return None
        try:
            key = _normalize_for_match(query_text)
            return registry_map.get(key)
        except Exception:
            return None

    def _connection_kwargs(self, target_config: Dict[str, Any]) -> Dict[str, Any]:
        """Extract connection kwargs from target config for _run_readyset_sql."""
        return {
            "host": target_config.get("host", "localhost"),
            "port": int(target_config.get("port", 5433)),
            "engine": target_config.get("engine", "postgresql"),
            "user": target_config.get("user", "postgres"),
            "database": target_config.get("database", ""),
            "password": self._resolve_password(target_config),
            "password_env": target_config.get("password_env", ""),
        }

    def _resolve_query(self, query: str) -> str:
        """Resolve query from registry hash (or prefix) or direct SQL.

        Accepts 4-12 hex character hash prefixes (like git short hashes).
        Uses get_executable_query to substitute stored parameter values,
        since ReadySet needs literal values (not :p1 placeholders).
        """
        if re.match(r"^[0-9a-f]{4,12}$", query.lower()):
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            entry = registry.get_query(query)
            if not entry:
                raise ValueError(f"Query hash '{query}' not found in registry. Use 'rdst query list' to see available queries.")
            # get_executable_query substitutes :p1 → stored literal values
            resolved = registry.get_executable_query(query, interactive=False)
            if not resolved:
                raise ValueError(
                    f"Query '{query}' has placeholders but no stored parameter values.\n"
                    f"Pass the full query with literal values instead:\n"
                    f"  rdst cache add \"<full SQL>\" --target <name>"
                )
            return resolved
        return query

    def _resolve_password(self, target_config: Dict[str, Any]) -> str:
        """Resolve password from environment variable."""
        password_env = target_config.get("password_env", "")
        if password_env:
            return os.environ.get(password_env, "")
        return ""

    def _save_to_registry(self, query: str, tag: Optional[str], target: str) -> Optional[str]:
        """Save query to registry if not already there. Returns hash or None."""
        try:
            from lib.query_registry.query_registry import QueryRegistry

            registry = QueryRegistry()
            registry.load()
            saved_hash, _is_new = registry.add_query(
                sql=query, tag=tag or "", source="cache", target=target,
            )
            return saved_hash
        except Exception as e:
            self._console.print(f"  [dim]Note: Could not save to query registry: {e}[/dim]")
            return None

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
        password_env: str = "",
    ) -> Dict[str, Any]:
        """Execute a SQL command against a ReadySet instance via Python DB driver."""
        conn = None
        try:
            if engine == "mysql":
                import pymysql
                import pymysql.cursors
                conn = pymysql.connect(
                    host=host, port=int(port), user=user,
                    password=password, database=database,
                    connect_timeout=10, read_timeout=30,
                    autocommit=True,
                    cursorclass=pymysql.cursors.Cursor,
                )
            else:
                import psycopg2
                conn = psycopg2.connect(
                    host=host, port=int(port), user=user,
                    password=password, database=database,
                    connect_timeout=10,
                    options="-c statement_timeout=30000",
                )
                conn.autocommit = True

            with conn.cursor() as cursor:
                cursor.execute(sql)
                if cursor.description:
                    rows = cursor.fetchall()
                    # Return as tab-separated text (matches existing parsers)
                    output = "\n".join(
                        "\t".join(str(col) if col is not None else "" for col in row)
                        for row in rows
                    )
                    return {"success": True, "output": output}
                return {"success": True, "output": ""}

        except ImportError:
            driver = "pymysql" if engine == "mysql" else "psycopg2-binary"
            return {"success": False, "error": f"{driver} not installed. Run: pip install {driver}"}
        except Exception as e:
            error = str(e).strip()
            if "access denied" in error.lower() or "authentication failed" in error.lower():
                hint = "\n\nCheck that your database password is set"
                if password_env:
                    hint += f":\n  export {password_env}=<password>"
                else:
                    hint += " in the target's password_env configuration."
                error += hint
            return {"success": False, "error": error}
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _parse_show_caches(self, output: str, engine: str) -> List[Dict[str, str]]:
        """Parse SHOW CACHES output into list of dicts.

        Columns (tab-separated from _run_readyset_sql):
            query_id | cache_name | query_text | fallback_behavior | count
        """
        if not output:
            return []

        caches = []
        lines = output.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            parts = [p.strip() for p in line.split("\t")]

            if len(parts) >= 3:
                cache = {
                    "cache_id": parts[0],
                    "cache_name": parts[1],
                    "query": parts[2],
                    "fallback": parts[3] if len(parts) > 3 else "",
                    "count": parts[4] if len(parts) > 4 else "",
                }
                caches.append(cache)

        return caches
