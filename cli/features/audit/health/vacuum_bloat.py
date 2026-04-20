"""§3 Vacuum & Bloat Health — dead tuples, last vacuum/analyze, TXID age, workers.

PostgreSQL: pg_stat_user_tables, pg_database.age(datfrozenxid), pg_stat_activity
(for active autovacuum workers), pg_settings for autovacuum + max_workers.

MySQL: information_schema.TABLES provides DATA_FREE as a rough bloat proxy and
UPDATE_TIME for recency, but MySQL doesn't have vacuum — autovacuum fields
remain None.
"""

from __future__ import annotations

from typing import Any, List, Optional

from features.audit.health.models import TableHealth, VacuumBloatSection


_PG_TABLE_SQL = """
SELECT
    schemaname,
    relname,
    COALESCE(n_live_tup, 0) AS live_tup,
    COALESCE(n_dead_tup, 0) AS dead_tup,
    last_vacuum,
    last_autovacuum,
    last_analyze,
    last_autoanalyze,
    COALESCE(n_mod_since_analyze, 0) AS mod_since_analyze,
    pg_total_relation_size(schemaname || '.' || relname) AS size_bytes
FROM pg_stat_user_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY COALESCE(n_dead_tup, 0) DESC, COALESCE(n_live_tup, 0) DESC
LIMIT 50;
"""

_PG_TXID_SQL = """
SELECT MAX(age(datfrozenxid)) AS max_age
FROM pg_database
WHERE datallowconn;
"""

_PG_AV_WORKERS_SQL = """
SELECT COUNT(*)
FROM pg_stat_activity
WHERE backend_type = 'autovacuum worker';
"""

_PG_AV_SETTINGS_SQL = """
SELECT name, setting
FROM pg_settings
WHERE name IN ('autovacuum', 'autovacuum_max_workers');
"""


def _classify(dead: int, live: int, mod_since: int) -> str:
    total = dead + max(live, 0)
    if total <= 0:
        return "healthy"
    ratio = dead / total
    if ratio >= 0.30:
        return "crit"
    if ratio >= 0.15 or mod_since > max(10_000, live // 5):
        return "warn"
    return "healthy"


def collect_vacuum_bloat(conn: Any, engine: str) -> Optional[VacuumBloatSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn)
    if engine == "mysql":
        return _collect_mysql(conn)
    return None


def _collect_pg(conn: Any) -> VacuumBloatSection:
    section = VacuumBloatSection()

    with conn.cursor() as cur:
        try:
            cur.execute(_PG_TABLE_SQL)
            tables: List[TableHealth] = []
            for row in cur.fetchall():
                (
                    schema,
                    name,
                    live,
                    dead,
                    lv,
                    lav,
                    la,
                    laa,
                    mod_since,
                    size,
                ) = row
                total = (dead or 0) + max(live or 0, 0)
                ratio = round(((dead or 0) / total) * 100, 1) if total > 0 else 0.0
                tables.append(
                    TableHealth(
                        schema=schema,
                        table=name,
                        live_tuples=int(live or 0),
                        dead_tuples=int(dead or 0),
                        dead_ratio_pct=ratio,
                        last_vacuum=lv.isoformat() if lv else None,
                        last_autovacuum=lav.isoformat() if lav else None,
                        last_analyze=la.isoformat() if la else None,
                        last_autoanalyze=laa.isoformat() if laa else None,
                        n_mod_since_analyze=int(mod_since or 0),
                        size_bytes=int(size) if size is not None else None,
                        status=_classify(int(dead or 0), int(live or 0), int(mod_since or 0)),
                    )
                )
            section.tables = tables
        except Exception:
            pass

        try:
            cur.execute(_PG_TXID_SQL)
            row = cur.fetchone()
            if row and row[0] is not None:
                section.txid_age = int(row[0])
                section.txid_age_pct = round(
                    (section.txid_age / section.txid_wraparound_limit) * 100, 2
                )
        except Exception:
            pass

        try:
            cur.execute(_PG_AV_WORKERS_SQL)
            row = cur.fetchone()
            if row:
                section.autovacuum_workers_active = int(row[0] or 0)
        except Exception:
            pass

        try:
            cur.execute(_PG_AV_SETTINGS_SQL)
            for name, setting in cur.fetchall():
                if name == "autovacuum":
                    section.autovacuum_enabled = setting == "on"
                elif name == "autovacuum_max_workers":
                    try:
                        section.autovacuum_max_workers = int(setting)
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

    # Summary
    crit = sum(1 for t in section.tables if t.status == "crit")
    warn = sum(1 for t in section.tables if t.status == "warn")
    if section.txid_age_pct and section.txid_age_pct > 50:
        section.summary_status = "crit"
    elif crit > 0 or (section.txid_age_pct and section.txid_age_pct > 25):
        section.summary_status = "crit"
    elif warn > 0:
        section.summary_status = "warn"
    else:
        section.summary_status = "healthy"

    return section


def _collect_mysql(conn: Any) -> VacuumBloatSection:
    section = VacuumBloatSection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    TABLE_SCHEMA,
                    TABLE_NAME,
                    COALESCE(TABLE_ROWS, 0),
                    COALESCE(DATA_FREE, 0),
                    UPDATE_TIME,
                    DATA_LENGTH + INDEX_LENGTH
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA NOT IN ('mysql','information_schema','performance_schema','sys')
                  AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY COALESCE(DATA_FREE, 0) DESC
                LIMIT 50;
                """
            )
            for schema, name, rows, data_free, update_time, size in cur.fetchall():
                total = (rows or 0) + 1
                ratio = round((float(data_free or 0) / max(float(size or 1), 1.0)) * 100, 1)
                section.tables.append(
                    TableHealth(
                        schema=schema,
                        table=name,
                        live_tuples=int(rows or 0),
                        dead_tuples=int(data_free or 0),  # bytes, not tuples — MySQL proxy
                        dead_ratio_pct=ratio,
                        last_autoanalyze=update_time.isoformat() if update_time else None,
                        size_bytes=int(size) if size is not None else None,
                        status="warn" if ratio > 20 else "healthy",
                    )
                )
    except Exception:
        pass
    return section
