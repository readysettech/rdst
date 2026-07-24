"""Cached-vs-upstream benchmark for audited targets' captured queries.

Shared machinery behind `rdst audit` / `rdst fleet audit` Readyset testing
and the API audit paths. The benchmark itself assumes a running cache
container; `run_ephemeral_benchmark` acquires the process-wide managed
sandbox for exactly one target's benchmark.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import time
import uuid
from typing import Any


class _NullConsole:
    """Console stand-in that retains the last diagnostic for API callers."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.messages.append(" ".join(str(arg) for arg in args))

    @property
    def last_message(self) -> str | None:
        if not self.messages:
            return None
        plain = re.sub(r"\[/?[a-zA-Z0-9 _-]+\]", "", self.messages[-1])
        return " ".join(plain.split()) or None


def docker_available() -> bool:
    return shutil.which("docker") is not None


def substitute_params(sql: str) -> str | None:
    """Replace $1, $2, etc. with sample values so parameterized queries can be
    executed.

    Returns the substituted SQL, or the original if no params are found.
    System-query exclusion belongs to the capture layer; the benchmark must
    consume that capture as-is rather than maintaining a second filter list.
    """
    # MySQL DIGEST_TEXT adds spaces around function parens and dot notation
    # that break execution: "SUM ( x )" -> "SUM(x)", "`u` . `name`" -> "`u`.`name`"
    # Fix these before param substitution.
    result_sql = sql
    result_sql = re.sub(r'(\w)\s+\(', r'\1(', result_sql)      # SUM ( -> SUM(
    result_sql = re.sub(r'`\s+\.\s+`', '`.`', result_sql)       # ` . ` -> `.`

    if (
        "$" not in result_sql
        and "?" not in result_sql
        and not re.search(r":p\d+", result_sql)
    ):
        return result_sql

    # Replace $N parameters with sample values based on context
    def _replace_param(match):
        # Look at surrounding context to guess a reasonable value
        pos = match.start()
        before = sql[max(0, pos - 40):pos].lower()
        if any(k in before for k in ["limit", "offset"]):
            return "10"
        if any(k in before for k in [">", "<", ">=", "<="]):
            return "100"
        if any(k in before for k in ["= '", "like", "ilike"]):
            return "'sample'"
        if "in (" in before:
            return "1"
        if "between" in before:
            return "'2026-01-01'"
        return "1"

    result = re.sub(r'\$\d+', _replace_param, result_sql)
    result = result.replace("?", "1")
    # Replace :p1, :p2 style params too
    result = re.sub(r':p\d+', '1', result)
    return result


def _static_cacheability_advisory(sql: str) -> tuple[bool | None, str | None]:
    """Return the static heuristic verdict without making it authoritative."""
    try:
        from features.cache.readyset_cacheability import check_readyset_cacheability

        check = check_readyset_cacheability(query=sql)
        issues = check.get("issues") or []
        warnings = check.get("warnings") or []
        reasons = [str(reason) for reason in [*issues, *warnings] if reason]
        return bool(check.get("cacheable")), "; ".join(reasons) or None
    except Exception as exc:
        return None, f"Static advisory unavailable: {exc}"


def _deep_cacheability_advisory(
    service: Any, cache_cfg: dict[str, Any], sql: str,
) -> tuple[bool | None, str | None]:
    """Ask ReadySet about deep-cache support, never gating shallow timing."""
    try:
        from shared.query_registry.sql_normalizer import (
            denormalize_for_readyset,
            parse_supported_from_explain,
        )

        engine = (cache_cfg or {}).get("engine", "postgresql")
        readyset_query = denormalize_for_readyset(sql, engine=engine)
        result = service._run_readyset_sql(
            f"EXPLAIN CREATE DEEP CACHE FROM {readyset_query}",
            **service._connection_kwargs(cache_cfg),
        )
        if not result.get("success"):
            return None, result.get("error") or "Deep-cache advisory failed"

        output = result.get("output", "")
        status = parse_supported_from_explain(output)
        first_line = output.strip().splitlines()[0].lower() if output.strip() else ""
        cached_status = any(
            re.search(r"supported\s*[|\t]\s*cached", line, re.IGNORECASE)
            for line in output.splitlines()
        )
        if status == "yes" or first_line in {"yes", "cached"} or cached_status:
            return True, None
        if status.startswith("unsupported") or first_line.startswith("unsupported"):
            reason = status or first_line
            return False, reason.split(":", 1)[1].strip() if ":" in reason else reason
        if status == "pending" or first_line.startswith("pending"):
            return None, "Deep-cache support is pending"
        return None, "Deep-cache support could not be determined"
    except Exception as exc:
        return None, f"Deep-cache advisory unavailable: {exc}"


def _normalize_cache_query(sql: str) -> str:
    """Normalize captured and SHOW CACHES SQL for identity matching."""
    normalized = sql.strip().rstrip(";")
    normalized = re.sub(r"\$\d+|:p\d+|\?", "?", normalized)
    return re.sub(r"\s+", " ", normalized).lower().strip()


def _list_caches(
    service: Any, CacheInput: Any, target: str,
) -> tuple[list[dict], str | None]:
    """Return SHOW CACHES rows, preserving an ErrorEvent as a reason."""
    caches: list[dict] = []
    error: str | None = None

    async def _list() -> None:
        nonlocal caches, error
        async for event in service.list_caches(CacheInput(target=target)):
            if event.type == "error":
                error = getattr(event, "message", None) or "SHOW CACHES failed"
            elif event.type == "cache_list" and getattr(event, "success", False):
                caches = list(event.caches or [])

    try:
        asyncio.run(_list())
    except Exception as exc:
        error = f"SHOW CACHES raised: {exc}"
    return caches, error


def _cache_id(cache: dict[str, Any]) -> str:
    return cache.get("cache_id") or cache.get("query_id") or cache.get("id", "")


def _verify_shallow_cache(
    service: Any,
    CacheInput: Any,
    target: str,
    sql: str,
    known_cache_ids: set[str],
) -> tuple[bool, str, set[str]]:
    """Verify that the cache just added is reported as shallow by SHOW CACHES."""
    caches, error = _list_caches(service, CacheInput, target)
    current_ids = {_cache_id(cache) for cache in caches if _cache_id(cache)}
    if error:
        return False, f"Could not verify shallow cache: {error}", current_ids

    new_caches = [cache for cache in caches if _cache_id(cache) not in known_cache_ids]
    query_key = _normalize_cache_query(sql)
    matching_new = [
        cache for cache in new_caches
        if _normalize_cache_query(cache.get("query", "")) == query_key
    ]
    matching_any = [
        cache for cache in caches
        if _normalize_cache_query(cache.get("query", "")) == query_key
    ]
    candidates = matching_new or (new_caches if len(new_caches) == 1 else []) or matching_any
    if not candidates:
        return False, "SHOW CACHES did not report the created cache", current_ids

    cache_type = str(candidates[0].get("type") or "").lower().strip()
    if cache_type != "shallow":
        reported = cache_type or "unknown"
        return False, f"SHOW CACHES reported cache type '{reported}', not shallow", current_ids
    return True, "", current_ids


def benchmark_queries(
    target: str,
    queries: list[dict[str, Any]],
    max_queries: int = 20,
    console=None,
    cache_config: dict[str, Any] | None = None,
    cleanup_errors: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    """Benchmark captured queries against a running Readyset cache.

    Caller manages the container lifecycle. ``cache_config`` allows managed
    sandboxes that are not registered as persistent targets; callers without
    it retain target-based resolution. Caches created here are dropped
    afterwards by diffing SHOW CACHES against the pre-benchmark state and
    dropping by ReadySet's own cache ids, so a reused pre-existing container
    is restored to the caches it had before (CLD-1754).

    The upstream side is the capture-window ``avg_time_ms``; this function
    never executes benchmark queries against the production upstream.

    Returns per-query shallow support/timing plus static and deep advisory
    metadata, or None if Readyset is not usable.
    """
    from features.cache.service import CacheService
    from features.cache.models import CacheInput, CacheOptions

    console = console or _NullConsole()
    service = (
        CacheService(cache_target_config=cache_config)
        if cache_config is not None
        else CacheService()
    )
    results: list[dict[str, Any]] = []

    # Wait for Readyset to be ready and verify connection
    resolved = service._resolve_cache_target(target)
    if not resolved:
        console.print("[yellow]  Cache target not found after deploy[/yellow]")
        return None
    _cache_name, cache_cfg = resolved
    console.print("[dim]  Waiting for Readyset to be ready...[/dim]")
    ready = False
    last_err = None
    for _attempt in range(20):
        time.sleep(1)
        try:
            from shared.db_connection import create_direct_connection
            test_conn = create_direct_connection(cache_cfg)
            test_conn.close()
            ready = True
            break
        except Exception as e:
            last_err = e
    if not ready:
        err_str = str(last_err)
        if "Access denied" in err_str or "password" in err_str.lower():
            console.print(
                "[yellow]  Cannot connect to the Readyset cache. "
                "Set its password, then try again.[/yellow]"
            )
        else:
            console.print(f"[yellow]  Readyset not ready after 20s: {last_err}[/yellow]")
        return None

    # Step 2: Record pre-existing caches so cleanup can restore this state
    pre_existing_ids = _list_cache_ids(service, CacheInput, target)
    known_cache_ids = set(pre_existing_ids)

    # Step 3: Create caches and benchmark
    query_count = min(len(queries), max_queries)
    console.print(
        f"\n[dim]  Testing {query_count} queries against Readyset...[/dim]"
    )
    conn_error_shown = False

    for q in queries[:max_queries]:
        sql = q.get("query_text") or q.get("normalized_query", "")
        q_hash = q.get("query_hash", q.get("hash", ""))[:12]
        upstream_ms = q.get("avg_time_ms", 0.0)

        if not sql.strip():
            continue

        static_cacheable, static_reason = _static_cacheability_advisory(sql)
        deep_supported, deep_reason = _deep_cacheability_advisory(
            service, cache_cfg, sql,
        )
        result_base = {
            "query_hash": q_hash,
            "query_text": sql[:200],
            # Keep baseline_ms for existing raw-result consumers. Both values
            # are the capture-window average, never a fresh upstream execution.
            "baseline_ms": upstream_ms,
            "upstream_ms": upstream_ms,
            "upstream_source": "capture_window",
            "static_cacheable": static_cacheable,
            "static_reason": static_reason,
            "deep_supported": deep_supported,
            "deep_reason": deep_reason,
        }

        def _skipped(reason: str) -> dict[str, Any]:
            return {
                **result_base,
                "cacheable": False,
                "not_cacheable_reason": reason,
                "readyset_ms": 0,
                "speedup": 0,
            }

        cache_add_event = None
        cache_create_error: str | None = None
        try:
            async def _add_cache() -> None:
                nonlocal cache_add_event, cache_create_error
                async for event in service.add_cache(
                    CacheInput(target=target, query=sql),
                    CacheOptions(),
                ):
                    if event.type == "error":
                        cache_create_error = (
                            getattr(event, "message", None) or "CREATE SHALLOW CACHE failed"
                        )
                    elif event.type == "cache_add":
                        cache_add_event = event
            asyncio.run(_add_cache())
        except Exception as exc:
            cache_create_error = f"CREATE SHALLOW CACHE raised: {exc}"

        if cache_create_error:
            results.append(_skipped(cache_create_error))
            continue
        if cache_add_event is None:
            results.append(_skipped("Cache service returned no CacheAddEvent"))
            continue
        if not getattr(cache_add_event, "success", False):
            results.append(_skipped(
                getattr(cache_add_event, "detail", None) or "CREATE SHALLOW CACHE failed"
            ))
            continue
        if not getattr(cache_add_event, "supported", False):
            results.append(_skipped(
                getattr(cache_add_event, "detail", None)
                or "ReadySet does not support a shallow cache for this query"
            ))
            continue

        verified, verify_reason, current_ids = _verify_shallow_cache(
            service, CacheInput, target, sql, known_cache_ids,
        )
        known_cache_ids.update(current_ids)
        if not verified:
            results.append(_skipped(verify_reason))
            continue

        # Benchmark against Readyset (3 runs: 1 warmup + 2 measured)
        # For parameterized queries, substitute sample values so we can execute them
        readyset_ms = 0.0
        bench_sql = substitute_params(sql)
        if not bench_sql:
            results.append(_skipped("Query is empty after parameter substitution"))
            continue
        execution_error: Exception | None = None
        conn = None
        times: list[float] = []
        try:
            from shared.db_connection import close_connection, create_direct_connection

            conn = create_direct_connection(cache_cfg)
            for i in range(3):
                cursor = None
                start = time.perf_counter()
                try:
                    cursor = conn.cursor()
                    cursor.execute(bench_sql)
                    cursor.fetchall()
                except Exception as exc:
                    execution_error = exc
                    break
                finally:
                    if cursor is not None:
                        cursor.close()
                elapsed_ms = (time.perf_counter() - start) * 1000
                if i > 0:  # Skip warmup
                    times.append(elapsed_ms)
        except Exception as exc:
            execution_error = exc
        finally:
            if conn is not None:
                close_connection(conn)

        if execution_error is not None:
            if not conn_error_shown:
                console.print(
                    f"[yellow]  Could not execute against Readyset cache: "
                    f"{execution_error}[/yellow]"
                )
                conn_error_shown = True
            results.append(_skipped(f"Readyset execution failed: {execution_error}"))
            continue
        if not times:
            results.append(_skipped("Readyset benchmark produced no measured samples"))
            continue
        readyset_ms = sum(times) / len(times)

        # Timing precision is ~0.1ms; sub-precision readings round to 0.0ms.
        # Floor the denominator at the precision limit so the fastest cache
        # hits report a large-but-defensible speedup instead of 0x or a
        # five-digit multiplier the measurement cannot support.
        speedup = upstream_ms / max(readyset_ms, 0.1) if upstream_ms > 0 else 0.0

        results.append({
            **result_base,
            "cacheable": True,
            "not_cacheable_reason": "",
            "readyset_ms": round(readyset_ms, 3),
            "speedup": round(speedup, 1),
        })

    errors = _drop_created_caches(service, CacheInput, target, pre_existing_ids)
    if cleanup_errors is not None:
        cleanup_errors.extend(errors)

    cached_count = sum(1 for r in results if r["cacheable"])
    if cached_count > 0:
        speedups = [r["speedup"] for r in results if r["cacheable"] and r["speedup"] > 0]
        avg = sum(speedups) / len(speedups) if speedups else 0
        console.print(f"  [green]{cached_count} queries cached, avg speedup {avg:.1f}x[/green]")
    else:
        console.print(f"  [dim]No queries cacheable[/dim]")

    return results if results else None


def _list_cache_ids(service, CacheInput, target: str) -> set[str]:
    """Collect ReadySet's own cache ids currently present on the target."""
    caches, _error = _list_caches(service, CacheInput, target)
    return {_cache_id(cache) for cache in caches if _cache_id(cache)}


def _drop_created_caches(
    service, CacheInput, target: str, pre_existing_ids: set[str]
) -> list[str]:
    """Drop and verify every cache created by this benchmark."""
    caches, list_error = _list_caches(service, CacheInput, target)
    if list_error:
        return [list_error]
    created = {
        _cache_id(cache)
        for cache in caches
        if _cache_id(cache) and _cache_id(cache) not in pre_existing_ids
    }
    errors: list[str] = []
    for cid in created:
        try:
            delete_error: str | None = None

            async def _delete(cache_id=cid):
                nonlocal delete_error
                async for event in service.delete_cache(
                    CacheInput(target=target, cache_id=cache_id)
                ):
                    if event.type == "error":
                        delete_error = (
                            getattr(event, "message", None)
                            or f"DROP CACHE {cache_id} failed"
                        )

            asyncio.run(_delete())
            if delete_error:
                errors.append(delete_error)
        except Exception as exc:
            errors.append(f"DROP CACHE {cid} raised: {exc}")

    remaining, verify_error = _list_caches(service, CacheInput, target)
    if verify_error:
        errors.append(verify_error)
    else:
        remaining_ids = {_cache_id(cache) for cache in remaining}
        leaked = sorted(created & remaining_ids)
        if leaked:
            errors.append(f"Temporary caches remain after cleanup: {', '.join(leaked)}")
    return errors


async def _benchmark_queries_settled(*args, **kwargs):
    """Keep the lease until the blocking benchmark worker has stopped."""
    worker = asyncio.create_task(asyncio.to_thread(benchmark_queries, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError as cancellation:
        worker_error: Exception | None = None
        while True:
            try:
                await asyncio.shield(worker)
                break
            except asyncio.CancelledError:
                continue
            except Exception as exc:
                worker_error = exc
                break
        if worker_error is not None:
            cleanup_errors = kwargs.get("cleanup_errors")
            if isinstance(cleanup_errors, list):
                cleanup_errors.append(
                    "Benchmark worker failed during cancellation: "
                    f"{worker_error}"
                )
        raise cancellation


async def run_managed_benchmark(
    target: str,
    queries: list[dict[str, Any]],
    max_queries: int = 20,
    console=None,
) -> dict[str, Any]:
    """Benchmark one target while holding the process-wide sandbox lease."""
    if not docker_available():
        return {"status": "docker_unavailable", "detail": "Docker is not installed"}

    from shared.deploy.sandbox_manager import SandboxPriority, sandbox_manager

    await sandbox_manager.start()
    try:
        async with sandbox_manager.lease(
            target=target,
            owner_id=f"audit-benchmark-{target}-{uuid.uuid4().hex}",
            purpose="audit_benchmark",
            priority=SandboxPriority.AUDIT_BATCH,
        ) as lease:
            benchmark_console = console or _NullConsole()
            cleanup_errors: list[str] = []
            try:
                results = await _benchmark_queries_settled(
                    target,
                    queries,
                    max_queries=max_queries,
                    console=benchmark_console,
                    cache_config=lease.connection.as_target_config(),
                    cleanup_errors=cleanup_errors,
                )
            except asyncio.CancelledError:
                if cleanup_errors:
                    await asyncio.shield(
                        lease.mark_dirty("Audit benchmark cache cleanup failed")
                    )
                raise
            except Exception as exc:
                await lease.mark_dirty(
                    f"Audit benchmark failed before cleanup: {type(exc).__name__}"
                )
                return {"status": "error", "detail": str(exc)}

            if cleanup_errors:
                await lease.mark_dirty("Audit benchmark cache cleanup failed")
            if not results:
                detail = (
                    benchmark_console.last_message
                    if isinstance(benchmark_console, _NullConsole)
                    else None
                )
                return {
                    "status": "error",
                    "detail": detail or "Benchmark produced no results",
                }
            outcome: dict[str, Any] = {"status": "ok", "results": results}
            if cleanup_errors:
                outcome["cleanup_warning"] = "; ".join(cleanup_errors)
            return outcome
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    finally:
        await sandbox_manager.stop()


def run_ephemeral_benchmark(
    target: str,
    queries: list[dict[str, Any]],
    max_queries: int = 20,
    console=None,
) -> dict[str, Any]:
    """Synchronous entry point for CLI and compatibility callers."""
    return asyncio.run(
        run_managed_benchmark(
            target, queries, max_queries=max_queries, console=console
        )
    )


def build_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Map raw benchmark results to the readyset_comparison payload."""
    queries = [
        {
            "query_hash": r.get("query_hash", ""),
            "query_text": r.get("query_text", ""),
            "supported": bool(r.get("cacheable")),
            "reason": r.get("not_cacheable_reason") or None,
            "upstream_ms": r.get("upstream_ms", r.get("baseline_ms", 0)),
            "upstream_source": r.get("upstream_source", "capture_window"),
            "readyset_ms": r.get("readyset_ms", 0),
            "speedup": r.get("speedup", 0),
            "static_cacheable": r.get("static_cacheable"),
            "static_reason": r.get("static_reason"),
            "deep_supported": r.get("deep_supported"),
            "deep_reason": r.get("deep_reason"),
        }
        for r in results
    ]
    supported = [q for q in queries if q["supported"]]
    speedups = [q["speedup"] for q in supported if q["speedup"] > 0]
    deep_supported_count = sum(q["deep_supported"] is True for q in queries)
    deep_unsupported_count = sum(q["deep_supported"] is False for q in queries)
    return {
        "queries_tested": len(queries),
        "supported_count": len(supported),
        "unsupported_count": len(queries) - len(supported),
        "deep_supported_count": deep_supported_count,
        "deep_unsupported_count": deep_unsupported_count,
        "deep_unknown_count": (
            len(queries) - deep_supported_count - deep_unsupported_count
        ),
        "avg_speedup": round(sum(speedups) / len(speedups), 1) if speedups else 0.0,
        "queries": queries,
    }
