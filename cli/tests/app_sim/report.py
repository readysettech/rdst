"""Read back what the workload left in the engines' statistics views, as the monitor user."""

from __future__ import annotations

from typing import Any

from tests.app_sim import docker

PG_STATEMENTS = """
SELECT left(regexp_replace(query, '\\s+', ' ', 'g'), 90), calls,
       round(total_exec_time::numeric, 1), round(mean_exec_time::numeric, 2),
       temp_blks_written
FROM pg_stat_statements s JOIN pg_database d ON d.oid = s.dbid
WHERE d.datname = %s AND query NOT ILIKE '%%pg_stat%%' AND query NOT IN ('BEGIN', 'COMMIT')
ORDER BY total_exec_time DESC LIMIT %s
"""
PG_VERBS = """
SELECT upper(split_part(ltrim(query), ' ', 1)), sum(calls)
FROM pg_stat_statements s JOIN pg_database d ON d.oid = s.dbid
WHERE d.datname = %s GROUP BY 1 ORDER BY 2 DESC
"""
PG_TABLES = """
SELECT relname, n_tup_ins, n_tup_upd, n_tup_hot_upd, n_tup_del, seq_scan, idx_scan, n_live_tup
FROM pg_stat_user_tables ORDER BY n_tup_ins + n_tup_upd + n_tup_del DESC
"""
PG_INDEXES = """
SELECT relname, indexrelname, idx_scan FROM pg_stat_user_indexes ORDER BY relname, indexrelname
"""
MY_STATEMENTS = """
SELECT left(DIGEST_TEXT, 90), COUNT_STAR, round(SUM_TIMER_WAIT / 1e9, 1),
       round(AVG_TIMER_WAIT / 1e9, 2), SUM_CREATED_TMP_DISK_TABLES
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = %s AND DIGEST_TEXT NOT LIKE 'COMMIT%%'
ORDER BY SUM_TIMER_WAIT DESC LIMIT %s
"""
MY_VERBS = """
SELECT upper(substring_index(DIGEST_TEXT, ' ', 1)), sum(COUNT_STAR)
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = %s GROUP BY 1 ORDER BY 2 DESC
"""
MY_TABLES = """
SELECT OBJECT_NAME, COUNT_INSERT, COUNT_UPDATE, COUNT_DELETE, COUNT_FETCH
FROM performance_schema.table_io_waits_summary_by_table
WHERE OBJECT_SCHEMA = %s ORDER BY COUNT_INSERT + COUNT_UPDATE + COUNT_DELETE DESC
"""
MY_INDEXES = """
SELECT OBJECT_NAME, INDEX_NAME, COUNT_STAR
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE OBJECT_SCHEMA = %s AND INDEX_NAME IS NOT NULL ORDER BY OBJECT_NAME, INDEX_NAME
"""


def collect(engine: docker.Engine, top: int = 15) -> dict[str, list[tuple[Any, ...]]]:
    conn = docker.connect(engine, docker.MONITOR_USER, autocommit=True)
    out: dict[str, list[tuple[Any, ...]]] = {}
    with conn.cursor() as cursor:
        if engine is docker.PG:
            queries = {"statements": (PG_STATEMENTS, (docker.DATABASE, top)),
                       "verbs": (PG_VERBS, (docker.DATABASE,)), "tables": (PG_TABLES, None),
                       "indexes": (PG_INDEXES, None)}
        else:
            queries = {"statements": (MY_STATEMENTS, (docker.DATABASE, top)),
                       "verbs": (MY_VERBS, (docker.DATABASE,)),
                       "tables": (MY_TABLES, (docker.DATABASE,)),
                       "indexes": (MY_INDEXES, (docker.DATABASE,))}
        for name, (sql, params) in queries.items():
            cursor.execute(sql, params)
            out[name] = [tuple(row) for row in cursor.fetchall()]
    conn.close()
    return out


def render(engine: docker.Engine, data: dict[str, list[tuple[Any, ...]]]) -> str:
    lines = [f"== {engine.container}"]
    headers = {
        "statements": "query | calls | total_ms | mean_ms | temp_blks_written/tmp_disk_tables",
        "verbs": "verb | calls",
        "tables": ("table | ins | upd | hot_upd | del | seq_scan | idx_scan | live"
                   if engine is docker.PG else "table | ins | upd | del | fetch"),
        "indexes": "table | index | scans",
    }
    for name, rows in data.items():
        lines.append(f"-- {name}: {headers[name]}")
        lines += [" | ".join(str(value) for value in row) for row in rows]
    return "\n".join(lines)
