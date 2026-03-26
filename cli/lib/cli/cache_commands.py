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
                        upstream = (target_config or {}).get("upstream_target", "")
                        run_hint = f"  Benchmark:   rdst query run {saved_hash} --target {target}\n" if saved_hash else ""
                        compare_hint = f"  Compare:     rdst query run {saved_hash} --target {upstream}\n" if saved_hash and upstream else ""
                        self._console.print(StyledPanel(
                            f"Shallow cache created successfully\n\n"
                            f"  Query: {str(InlineSQL(last_event.query, max_length=80))}\n"
                            f"  Target: {target}\n"
                            + (f"  Hash: {saved_hash}\n" if saved_hash else "")
                            + f"\n  View caches: rdst cache show --target {target}\n"
                            f"  Delete:      rdst cache delete <cache_id> --target {target}\n"
                            + run_hint + compare_hint,
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
