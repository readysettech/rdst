"""The monitoring prerequisites, as the Turn on monitoring dialog reads them, for an agent.

This runs the same checks as GET /api/configure/targets/{name}/prerequisites
(pg_stat_statements on PostgreSQL, performance_schema statement digests on MySQL) and blocks
in the same cases the dialog does: until `ready` is true, including when the check couldn't
connect. On top of the dialog's result it adds a top-level verdict and the fixes as plain
instruction lines an agent can show the user as they are.
"""

from __future__ import annotations

import asyncio
from typing import Any

CHECK = "query_stats"
SOURCES = {"postgresql": "pg_stat_statements", "mysql": "performance_schema"}
# A check waiting on an earlier one; listed only when nothing else failed.
DEPENDENT_STATUSES = ("waiting",)


def fix_kind(fix: dict[str, Any] | None) -> str | None:
    """in_database: SQL run in the database fixes it. server_config: a server setting and a
    restart. manual: console or support steps only."""
    if not fix:
        return None
    if fix.get("restart"):
        return "server_config"
    return "in_database" if fix.get("sql") else "manual"


def _instructions(failed: list[dict[str, Any]]) -> tuple[list[str], list[str], bool]:
    """Instruction lines, the in-database SQL, and whether a restart is part of the fix."""
    lines: list[str] = []
    sql: list[str] = []
    restart = False
    for check in failed:
        fix = check.get("fix") or {}
        lines.append(str(check.get("detail") or check.get("label")))
        if not fix:
            continue
        restart = restart or bool(fix.get("restart"))
        lines.extend(part for part in [fix.get("summary"), *fix.get("steps", [])] if part)
        for statement in fix.get("sql", []):
            lines.append(f"SQL: {statement}")
            if not fix.get("restart"):
                sql.append(statement)
        lines.extend(fix.get("commands", []))
        lines.extend(fix.get("after", []))
        if fix.get("note"):
            lines.append(str(fix["note"]))
        if fix.get("docs_url"):
            lines.append(f"Docs: {fix['docs_url']}")
    return lines, list(dict.fromkeys(sql)), restart


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """The dialog's result plus ready_to_monitor, blocking_reason, next_step, the failed
    check ids, instruction lines, in-database SQL, needs_restart and each check's fix_kind."""
    engine = str(result.get("engine") or "")
    checks = [{**check, "fix_kind": fix_kind(check.get("fix")) if check.get("status") != "ok"
               else None} for check in result.get("checks") or []]
    blocking = [check for check in checks
                if check.get("blocking", True) and check.get("status") != "ok"]
    actionable = [check for check in blocking if check.get("status") not in DEPENDENT_STATUSES]
    failed = actionable or blocking
    instructions, sql, restart = _instructions(failed)
    error = result.get("connection_error")
    ready = bool(result.get("ready")) and not error and bool(checks)
    if ready:
        reason, next_step = None, "enable_monitoring"
    elif error:
        reason, next_step = f"RDST couldn't connect to check query statistics: {error}", \
            "fix_connection"
        instructions = instructions or ["Check that the database is running and reachable, "
                                        "then check again."]
    else:
        first = failed[0] if failed else {}
        problem = first.get("detail") or first.get("label") or "a required check failed"
        reason = f"Query statistics aren't available, so RDST can't monitor yet: {problem}"
        next_step = "enable_query_stats"
    return {**result, "checks": checks, "ready_to_monitor": ready, "blocking_reason": reason,
            "next_step": next_step, "check": CHECK, "source": SOURCES.get(engine),
            "failed": [check.get("id") for check in failed], "instructions": instructions,
            "sql": sql, "needs_restart": restart}


async def check(name: str) -> dict[str, Any]:
    """Run the prerequisite checks for the saved database ``name``; see ``summarize``."""
    from features.configure import prerequisites
    from shared.api.target_guard import ensure_target_password

    guard = await asyncio.to_thread(ensure_target_password, name)
    result = await asyncio.to_thread(prerequisites.check_target, guard.target_name,
                                     guard.target_config)
    return summarize(result)
