"""§5 Connection Analysis — state breakdown, idle-in-tx, pooler detection.

PostgreSQL: pg_stat_activity for states + application_name for pooler signatures
(pgbouncer, pgpool-II). Flag idle-in-transaction connections older than 60s.

MySQL: performance_schema.threads + information_schema.PROCESSLIST for state.
"""

from __future__ import annotations

from typing import Any, Optional

from features.audit.health.models import ConnectionAnalysisSection, LongRunningTransaction


_POOLER_SIGNATURES = {
    "pgbouncer": "pgbouncer",
    "pgpool": "pgpool",
    "pgcat": "pgcat",
}


def collect_connection_analysis(conn: Any, engine: str) -> Optional[ConnectionAnalysisSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn)
    if engine == "mysql":
        return _collect_mysql(conn)
    return None


def _collect_pg(conn: Any) -> ConnectionAnalysisSection:
    section = ConnectionAnalysisSection()
    with conn.cursor() as cur:
        try:
            cur.execute("SHOW max_connections;")
            row = cur.fetchone()
            if row:
                section.max_connections = int(row[0])
        except Exception:
            pass

        try:
            cur.execute(
                """
                SELECT COALESCE(state, 'unknown'), COUNT(*)
                FROM pg_stat_activity
                WHERE backend_type = 'client backend'
                GROUP BY COALESCE(state, 'unknown');
                """
            )
            by_state = {}
            total = 0
            for state, count in cur.fetchall():
                by_state[state] = int(count)
                total += int(count)
            section.by_state = by_state
            section.total_connections = total
            section.idle_in_transaction_count = by_state.get(
                "idle in transaction", 0
            ) + by_state.get("idle in transaction (aborted)", 0)
        except Exception:
            pass

        try:
            cur.execute(
                """
                SELECT
                    pid,
                    state,
                    EXTRACT(EPOCH FROM (now() - xact_start)) AS xact_age,
                    wait_event,
                    application_name,
                    client_addr::text,
                    LEFT(query, 200)
                FROM pg_stat_activity
                WHERE state IN ('idle in transaction','idle in transaction (aborted)')
                  AND xact_start IS NOT NULL
                  AND (now() - xact_start) > interval '60 seconds'
                ORDER BY xact_start ASC
                LIMIT 20;
                """
            )
            for pid, state, age, wait, app, addr, qp in cur.fetchall():
                section.long_running_idle_in_tx.append(
                    LongRunningTransaction(
                        pid=int(pid) if pid is not None else None,
                        state=state or "",
                        duration_seconds=float(age or 0.0),
                        wait_event=wait,
                        application_name=app,
                        client_addr=addr,
                        query_preview=qp,
                    )
                )
        except Exception:
            pass

        try:
            cur.execute(
                """
                SELECT DISTINCT application_name
                FROM pg_stat_activity
                WHERE application_name IS NOT NULL AND application_name != '';
                """
            )
            for (app,) in cur.fetchall():
                lower = (app or "").lower()
                for sig, pooler in _POOLER_SIGNATURES.items():
                    if sig in lower:
                        section.pooler_detected = True
                        section.pooler_type = pooler
                        break
                if section.pooler_detected:
                    break
        except Exception:
            pass

    return section


def _collect_mysql(conn: Any) -> ConnectionAnalysisSection:
    section = ConnectionAnalysisSection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW VARIABLES LIKE 'max_connections';")
            row = cur.fetchone()
            if row:
                try:
                    section.max_connections = int(row[1])
                except (TypeError, ValueError, IndexError):
                    pass

            cur.execute(
                """
                SELECT COALESCE(PROCESSLIST_COMMAND,'Unknown'), COUNT(*)
                FROM performance_schema.threads
                WHERE TYPE = 'FOREGROUND'
                GROUP BY COALESCE(PROCESSLIST_COMMAND,'Unknown');
                """
            )
            by_state = {}
            total = 0
            for state, count in cur.fetchall():
                by_state[state] = int(count)
                total += int(count)
            section.by_state = by_state
            section.total_connections = total
    except Exception:
        pass
    return section
