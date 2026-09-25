"""§5 Connection Analysis — state breakdown, idle-in-tx, pooler detection.

PostgreSQL: pg_stat_activity for states + application_name for pooler signatures
(pgbouncer, pgpool-II). Flag idle-in-transaction connections older than 60s.

MySQL: performance_schema.threads for state; information_schema.INNODB_TRX joined to
sleeping sessions for idle-in-transaction; connect attributes for ProxySQL or
MySQL Router.
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


# Sessions sitting idle (COMMAND = 'Sleep') while InnoDB holds a transaction open for them:
# MySQL's idle in transaction.
_MYSQL_IDLE_IN_TX_SQL = """
SELECT th.PROCESSLIST_ID, TIMESTAMPDIFF(SECOND, trx.trx_started, NOW()),
       th.PROCESSLIST_USER, th.PROCESSLIST_HOST,
       (SELECT a.ATTR_VALUE FROM performance_schema.session_connect_attrs a
        WHERE a.PROCESSLIST_ID = th.PROCESSLIST_ID AND a.ATTR_NAME = 'program_name' LIMIT 1),
       LEFT(COALESCE(trx.trx_query, ''), 200)
FROM information_schema.INNODB_TRX trx
JOIN performance_schema.threads th ON th.PROCESSLIST_ID = trx.trx_mysql_thread_id
WHERE th.PROCESSLIST_COMMAND = 'Sleep'
ORDER BY trx.trx_started ASC
LIMIT 100
"""
_MYSQL_POOLERS = {"proxysql": "proxysql", "mysqlrouter": "mysqlrouter",
                  "mysql router": "mysqlrouter"}


def _collect_mysql(conn: Any) -> ConnectionAnalysisSection:
    section = ConnectionAnalysisSection()
    with conn.cursor() as cur:
        try:
            cur.execute("SHOW VARIABLES LIKE 'max_connections';")
            row = cur.fetchone()
            if row:
                section.max_connections = int(row[1])
        except Exception:
            pass

        try:
            cur.execute(
                """
                SELECT COALESCE(PROCESSLIST_COMMAND,'Unknown'), COUNT(*)
                FROM performance_schema.threads
                WHERE TYPE = 'FOREGROUND' AND PROCESSLIST_ID IS NOT NULL
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

        try:
            cur.execute(_MYSQL_IDLE_IN_TX_SQL)
            rows = cur.fetchall()
            section.idle_in_transaction_count = len(rows)
            for pid, age, user, host, program, preview in rows:
                if float(age or 0) <= 60:
                    continue
                section.long_running_idle_in_tx.append(
                    LongRunningTransaction(
                        pid=int(pid) if pid is not None else None,
                        state="idle in transaction",
                        duration_seconds=float(age or 0.0),
                        application_name=program or user,
                        client_addr=host,
                        query_preview=preview or None,
                    )
                )
            section.long_running_idle_in_tx = section.long_running_idle_in_tx[:20]
        except Exception:
            pass

        try:
            cur.execute(
                """
                SELECT DISTINCT LOWER(ATTR_VALUE) FROM performance_schema.session_connect_attrs
                WHERE ATTR_NAME IN ('program_name', '_client_name')
                """
            )
            for (value,) in cur.fetchall():
                pooler = next((name for sig, name in _MYSQL_POOLERS.items()
                               if sig in (value or "")), None)
                if pooler:
                    section.pooler_detected, section.pooler_type = True, pooler
                    break
        except Exception:
            pass
    return section
