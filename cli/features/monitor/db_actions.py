"""Database-level actions for the daily snapshot: what to change on the server rather than in
one query. Each comes from the stored health collectors and the day's recorded numbers, with
its evidence as numbers and the SQL or setting to apply. Nothing here asks a model; the
report's narrative may only rank these and say why, by id.
"""

from __future__ import annotations

import re
from typing import Any

from features.monitor.presentation import format_size, plain

LARGE_TABLE_ROWS = 100_000
VACUUM_MIN_BYTES = 8 * 1024 * 1024
LOW_CACHE_HIT = 0.95
MIN_DISK_READS_PER_S = 50
FULL_SCANS_PER_DAY = 1_000
BAD_SCAN_MULTIPLIER = 10

SEVERITY_RANK = {"bad": 0, "warn": 1, "info": 2}
# Settings that only change on a restart. MySQL resizes its buffer pool, connection limit and
# redo log capacity online.
RESTART = {"shared_buffers", "max_connections", "autovacuum_max_workers"}
MYSQL_RESTART = {"innodb_log_file_size", "innodb_log_files_in_group"}
# Both engines ship a 128 MB cache: shared_buffers and innodb_buffer_pool_size.
DEFAULT_SHARED_BUFFERS_MB = 128
DEFAULT_BUFFERS = {"postgresql": "shared_buffers", "mysql": "innodb_buffer_pool_size"}
SETTING_WORDS: dict[str, tuple[str, str]] = {
    "shared_buffers": (
        "Give PostgreSQL more memory for cached data",
        "shared_buffers is the memory PostgreSQL keeps hot data in. When it's small, more "
        "reads go to disk."),
    "work_mem": (
        "Raise work_mem so sorts stay in memory",
        "Each sort or hash gets this much memory before it spills to disk. Reporting and "
        "grouping queries slow down when it's low."),
    "maintenance_work_mem": (
        "Raise maintenance_work_mem",
        "Vacuum and index builds use it. When it's low they take longer and hold locks "
        "longer."),
    "effective_cache_size": (
        "Tell the planner how much memory the server has",
        "The planner uses it to judge whether an index scan will find its pages cached. "
        "It allocates nothing."),
    "random_page_cost": (
        "Lower random_page_cost for SSD storage",
        "At 4 the planner assumes spinning disks and passes over index scans that would be "
        "faster on SSDs."),
    "log_min_duration_statement": (
        "Turn on slow query logging",
        "With it off, the server keeps no record of slow statements between RDST's "
        "readings."),
    "max_connections": (
        "Review max_connections",
        "Each connection reserves memory. Too many invites contention; too few refuses "
        "the application."),
    "autovacuum": (
        "Turn autovacuum back on",
        "Without it dead rows pile up, tables bloat and statistics go stale."),
    "autovacuum_max_workers": (
        "Give autovacuum more workers",
        "Busy tables wait their turn when every worker is taken."),
    "innodb_buffer_pool_size": (
        "Give InnoDB more memory for cached data",
        "The buffer pool holds hot pages. When it's small, more reads go to disk."),
    "sort_buffer_size": (
        "Lower sort_buffer_size",
        "Every sort in every session allocates it. Large values waste memory under "
        "concurrency."),
    "tmp_table_size": (
        "Raise tmp_table_size so temporary tables stay in memory",
        "GROUP BY and DISTINCT build temporary tables; past this size they go to disk. "
        "max_heap_table_size caps it too."),
    "innodb_redo_log_capacity": (
        "Give InnoDB a larger redo log",
        "A small redo log forces frequent checkpoints, which slow writes during bursts."),
    "innodb_log_file_size": (
        "Give InnoDB larger redo log files",
        "A small redo log forces frequent checkpoints, which slow writes during bursts."),
    "innodb_io_capacity": (
        "Raise innodb_io_capacity for SSD storage",
        "At 200 InnoDB paces background flushing for spinning disks, so dirty pages pile up "
        "on fast storage."),
    "long_query_time": (
        "Tune the slow query log",
        "Without a sensible threshold, the server keeps no useful record of slow statements "
        "between RDST's readings."),
    "innodb_stats_auto_recalc": (
        "Turn automatic statistics back on",
        "Without it InnoDB never refreshes table statistics, so plans drift as data changes."),
}
_SIZE = re.compile(r"([\d.]+)\s*(kB|KB|MB|GB|TB|ms|s|min)?")


def _pct(ratio: float) -> str:
    return f"{ratio * 100:.1f}%"


def _mb(text: str) -> float | None:
    match = _SIZE.search(text or "")
    if not match:
        return None
    number = float(match.group(1))
    return number * {"kB": 1 / 1024, "KB": 1 / 1024, "GB": 1024, "TB": 1024 * 1024}.get(
        match.group(2) or "MB", 1)


def setting_value(recommended: str) -> tuple[str, str | None] | None:
    """The low end of a recommendation as (number, unit): '16-32 MB' -> ('16', 'MB').
    None when it isn't a concrete value ('25% of RAM')."""
    text = plain(recommended)
    if not text or "%" in text or "RAM" in text:
        return None
    if text.lower() in ("on", "off"):
        return text.lower(), None
    matches = _SIZE.findall(text)
    if not matches:
        return None
    number, unit = matches[0]
    if not unit:
        unit = next((later for _, later in matches[1:] if later), "")
    return number, unit or None


EXAMPLE_RAM_GB = 16


def setting_sql(parameter: str, recommended: str, engine: str) -> str | None:
    value = setting_value(recommended)
    share = re.match(r"(\d+)% of RAM", str(recommended or ""))
    if value is None and share:
        # RDST can't see the server's memory, so the SQL carries a worked example to resize.
        example = EXAMPLE_RAM_GB * int(share.group(1)) // 100
        sql = setting_sql(parameter, f"{example} GB", engine)
        return sql and (f"-- {share.group(1)}% of the server's RAM: {example}GB on a "
                        f"{EXAMPLE_RAM_GB} GB server. Change it to fit yours.\n{sql}")
    if value is None:
        return None
    number, unit = value
    if engine == "mysql":
        if unit in ("kB", "KB", "MB", "GB"):
            factor = {"kB": 1024, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}[unit]
            literal = str(int(float(number) * factor))
        elif unit == "ms":
            literal = str(float(number) / 1000)
        else:
            literal = number
        # PERSIST_ONLY records a restart-only setting for the next start.
        verb = "SET PERSIST_ONLY" if parameter in MYSQL_RESTART else "SET PERSIST"
        return f"{verb} {parameter} = {literal};"
    literal = f"'{number}{unit}'" if unit else number
    sql = f"ALTER SYSTEM SET {parameter} = {literal};"
    if parameter in RESTART:
        return sql
    return sql + "\nSELECT pg_reload_conf();"


def setting_steps(parameter: str, recommended: str, engine: str, restart: bool) -> str:
    """Numbered steps, one per line: the value, where to set it, and when it takes effect."""
    managed = ("On RDS, Aurora, Cloud SQL or Azure, set it in the instance's parameter group "
               "instead; the SQL needs a superuser.")
    run = (f"Run the SQL below to set {parameter}. {managed}")
    if engine == "mysql":
        apply = ("Restart MySQL; the new value takes effect then." if restart else
                 "SET PERSIST applies it now and keeps it across restarts.")
    else:
        apply = ("Restart PostgreSQL in a quiet moment; the new value takes effect then."
                 if restart else "The reload in the SQL applies it; no restart needed.")
    relative = str(recommended or "").replace("of RAM", "of the server's RAM")
    pick = (f"Pick a value near {relative}." if "RAM" in relative else
            f"Set it to {recommended}." if recommended else None)
    return "\n".join(step for step in (pick, run, apply) if step)


# A MySQL table's free space is worth a rebuild from this much (below it, InnoDB's own extent
# allocation dominates), and a purge backlog from this many undo records.
MYSQL_FREE_WARN_BYTES = 64 * 1024 * 1024
MYSQL_HISTORY_WARN = 1_000_000


def mysql_table(schema: Any, table: Any) -> str:
    """A schema-qualified, backtick-quoted MySQL table name."""
    name = f"`{str(table).replace('`', '``')}`"
    return f"`{str(schema).replace('`', '``')}`.{name}" if schema else name


def _action(**fields: Any) -> dict[str, Any]:
    base = {"rows": [], "sql": None, "setting": None, "restart": False, "caveat": None,
            "query_keys": []}
    return {**base, **fields}


def _memory_short(numbers: dict[str, Any]) -> float | None:
    """The read-weighted cache hit when misses are both frequent and a real share of reads."""
    weighted, reads = numbers.get("cache_hit_weighted"), numbers.get("disk_reads_per_s")
    if not isinstance(weighted, (int, float)) or not isinstance(reads, (int, float)):
        return None
    return weighted if weighted < LOW_CACHE_HIT and reads >= MIN_DISK_READS_PER_S else None


def _settings(config: dict[str, Any], engine: str, lowest_hit: float | None
              ) -> list[dict[str, Any]]:
    actions = []
    for row in config.get("settings") or []:
        if not isinstance(row, dict):
            continue
        parameter = str(row.get("parameter") or "")
        current, recommended = plain(row.get("current")), plain(row.get("recommended"))
        status = row.get("status")
        default_buffers = (parameter == DEFAULT_BUFFERS.get(engine) and
                           (_mb(current) or 0) <= DEFAULT_SHARED_BUFFERS_MB)
        low_hit = isinstance(lowest_hit, (int, float))
        if status not in ("warn", "crit") and not default_buffers:
            continue
        title, why = SETTING_WORDS.get(parameter, (f"Review {parameter}", plain(row.get("note"))))
        evidence = [{"label": parameter, "value": current},
                    {"label": "Recommended", "value": recommended}]
        severity = "bad" if status == "crit" else "warn"
        finding = f"{parameter} is {current}; the check recommends {recommended}."
        if default_buffers and status not in ("warn", "crit"):
            finding = (f"{parameter} is {current}, "
                       f"{'MySQL' if engine == 'mysql' else 'PostgreSQL'}'s out-of-the-box "
                       "default.")
            severity = "warn" if low_hit else "info"
            if low_hit:
                finding = f"Only {_pct(lowest_hit)} of reads found their data in memory. {finding}"
                evidence.append({"label": "Cache hit", "value": _pct(lowest_hit)})
        restart = parameter in (MYSQL_RESTART if engine == "mysql" else RESTART)
        actions.append(_action(
            id=f"setting_{parameter}", check="settings", title=title, severity=severity,
            finding=finding, evidence=evidence, why=why,
            steps=setting_steps(parameter, recommended, engine, restart),
            sql=setting_sql(parameter, recommended, engine), restart=restart,
            setting={"name": parameter, "current": current, "target": recommended},
            caveat=None if default_buffers else plain(row.get("note")) or None))
    return actions


def _indexes(indexes: dict[str, Any], engine: str) -> list[dict[str, Any]]:
    actions = []
    unused = [row for row in indexes.get("unused_indexes") or [] if isinstance(row, dict)
              and not row.get("is_primary") and not row.get("is_unique")]
    duplicates = [row for row in indexes.get("duplicates") or [] if isinstance(row, dict)]

    def drop(schema: Any, table: Any, index: Any) -> str:
        if engine == "mysql":
            return f"ALTER TABLE {mysql_table(schema, table)} DROP INDEX `{index}`;"
        return f"DROP INDEX CONCURRENTLY {schema or 'public'}.{index};"

    if unused:
        total = sum(float(row.get("size_bytes") or 0) for row in unused)
        count = len(unused)
        actions.append(_action(
            id="unused_indexes", check="indexes",
            title=f"Drop {count} unused {'index' if count == 1 else 'indexes'}",
            severity="warn",
            finding=f"{count} {'index has' if count == 1 else 'indexes have'} no reads, "
                    f"{format_size(total)} in all.",
            evidence=[{"label": "Unused indexes", "value": str(count)},
                      {"label": "Space they take", "value": format_size(total)},
                      {"label": "Indexes in all", "value": str(indexes.get("total_indexes") or 0)}],
            why="Every insert and update still writes to them, and they take memory that "
                "hot data could use.",
            steps="Drop them one at a time, starting with the largest.",
            rows=[{"name": str(row.get("index")), "value": format_size(row.get("size_bytes")),
                   "detail": f"on {row.get('table')}, {int(row.get('scans') or 0):,} reads"}
                  for row in unused],
            sql="\n".join(drop(row.get("schema"), row.get("table"), row.get("index"))
                          for row in unused),
            caveat="Read counts start at the last statistics reset, and a replica can use an "
                   "index the primary doesn't. Check both before dropping."))
    if duplicates:
        total = sum(float(row.get("wasted_bytes") or 0) for row in duplicates)
        count = len(duplicates)
        actions.append(_action(
            id="duplicate_indexes", check="indexes",
            title=f"Drop {count} duplicate {'index' if count == 1 else 'indexes'}",
            severity="warn",
            finding=f"{count} {'index repeats' if count == 1 else 'indexes repeat'} another "
                    f"one, {format_size(total)} in all.",
            evidence=[{"label": "Duplicates", "value": str(count)},
                      {"label": "Space they take", "value": format_size(total)}],
            why="The covering index serves the same reads, so the duplicate only costs "
                "writes and memory.",
            steps="Drop the redundant index; keep the one that covers it.",
            rows=[{"name": str(row.get("redundant_index")), "value": format_size(row.get("wasted_bytes")),
                   "detail": f"covered by {row.get('covered_by')}"} for row in duplicates],
            sql="\n".join(drop(row.get("schema"), row.get("table"), row.get("redundant_index"))
                          for row in duplicates)))
    return actions


def _vacuum(vacuum: dict[str, Any], engine: str) -> list[dict[str, Any]]:
    actions = []
    tables = [table for table in vacuum.get("tables") or [] if isinstance(table, dict)]
    mysql = engine == "mysql"
    # Autovacuum keeps small tables in hand; a warning there isn't worth anyone's time.
    bloated = sorted((table for table in tables if table.get("status") in ("warn", "crit")
                      and (not mysql or (table.get("free_bytes") or 0) >= MYSQL_FREE_WARN_BYTES)
                      and (mysql or table.get("status") == "crit"
                           or (table.get("size_bytes") or 0) >= VACUUM_MIN_BYTES)),
                     key=lambda table: -(table.get("dead_ratio_pct") or 0))
    if bloated and mysql:
        worst = bloated[0]
        total = sum(float(table.get("free_bytes") or 0) for table in bloated)
        actions.append(_action(
            id="vacuum", check="vacuum",
            title=f"Rebuild {len(bloated)} {'table' if len(bloated) == 1 else 'tables'} with "
                  "free space",
            severity="bad" if any(t.get("status") == "crit" for t in bloated) else "warn",
            finding=f"{worst.get('table')} is {worst.get('dead_ratio_pct') or 0:.1f}% free "
                    f"space, {format_size(total)} across "
                    f"{'it' if len(bloated) == 1 else f'{len(bloated)} tables'}.",
            evidence=[{"label": "Worst table", "value": str(worst.get("table"))},
                      {"label": "Free space there",
                       "value": f"{worst.get('dead_ratio_pct') or 0:.1f}%"},
                      {"label": "Reclaimable in all", "value": format_size(total)}],
            why="Deleted and updated rows leave free pages behind. Scans still read the "
                "table's full size and the disk holds the space until a rebuild.",
            steps="Rebuild them during a quiet hour. OPTIMIZE TABLE rebuilds InnoDB tables "
                  "online, but it needs free disk for a full copy.",
            rows=[{"name": str(table.get("table")),
                   "value": f"{format_size(table.get('free_bytes'))} free",
                   "detail": f"{table.get('dead_ratio_pct') or 0:.1f}% of "
                             f"{format_size((table.get('size_bytes') or 0) + (table.get('free_bytes') or 0))}"}
                  for table in bloated],
            sql="\n".join(f"OPTIMIZE TABLE {mysql_table(table.get('schema'), table.get('table'))};"
                          for table in bloated)))
    elif bloated:
        worst = bloated[0]
        actions.append(_action(
            id="vacuum", check="vacuum",
            title=f"Vacuum {len(bloated)} bloated {'table' if len(bloated) == 1 else 'tables'}",
            severity="bad" if any(t.get("status") == "crit" for t in bloated) else "warn",
            finding=f"{worst.get('table')} is {worst.get('dead_ratio_pct') or 0:.1f}% dead rows"
                    + (f", and {len(bloated) - 1} more "
                       f"{'table is' if len(bloated) == 2 else 'tables are'} bloated."
                       if len(bloated) > 1 else "."),
            evidence=[{"label": "Worst table", "value": str(worst.get("table"))},
                      {"label": "Dead rows there",
                       "value": f"{worst.get('dead_ratio_pct') or 0:.1f}%"}],
            why="Dead rows make every scan read more pages and push live data out of memory.",
            steps="Vacuum them now, then check that autovacuum keeps up with their writes.",
            rows=[{"name": str(table.get("table")),
                   "value": f"{table.get('dead_ratio_pct') or 0:.1f}% dead",
                   "detail": f"{int(table.get('dead_tuples') or 0):,} dead of "
                             f"{int(table.get('live_tuples') or 0) + int(table.get('dead_tuples') or 0):,} rows"}
                  for table in bloated],
            sql="\n".join(f"VACUUM (ANALYZE) {table.get('schema') or 'public'}.{table.get('table')};"
                          for table in bloated)))
    stale = []
    for table in tables:
        live = int(table.get("live_tuples") or 0)
        changed = table.get("n_mod_since_analyze")
        if isinstance(changed, (int, float)) and changed >= 1000 and live and changed / live >= 0.2:
            stale.append((table, int(changed), live))
    stale.sort(key=lambda entry: -entry[1] / entry[2])
    if stale:
        table, changed, live = stale[0]
        actions.append(_action(
            id="analyze", check="vacuum",
            title=f"Refresh statistics on {len(stale)} {'table' if len(stale) == 1 else 'tables'}",
            severity="warn",
            finding=f"{table.get('table')} changed {changed:,} rows since its last ANALYZE, "
                    f"{changed / live * 100:.0f}% of its {live:,} rows.",
            evidence=[{"label": "Table", "value": str(table.get("table"))},
                      {"label": "Rows changed since ANALYZE", "value": f"{changed:,}"},
                      {"label": "Live rows", "value": f"{live:,}"}],
            why="The planner picks plans from these statistics. Stale ones lead to bad row "
                "estimates and slow plans.",
            steps=("Run ANALYZE TABLE on them. It's quick; InnoDB samples a few pages."
                   if mysql else
                   "Run ANALYZE on them. It's quick and doesn't block reads or writes."),
            rows=[{"name": str(entry[0].get("table")), "value": f"{entry[1]:,} changed",
                   "detail": f"{entry[1] / entry[2] * 100:.0f}% of {entry[2]:,} rows"}
                  for entry in stale],
            sql="\n".join((f"ANALYZE TABLE {mysql_table(entry[0].get('schema'), entry[0].get('table'))};"
                           if mysql else
                           f"ANALYZE {entry[0].get('schema') or 'public'}.{entry[0].get('table')};")
                          for entry in stale)))
    history = vacuum.get("history_list_length")
    if isinstance(history, (int, float)) and history >= MYSQL_HISTORY_WARN:
        actions.append(_action(
            id="purge_lag", check="vacuum", title="Let InnoDB purge old row versions",
            severity="bad" if history >= 10 * MYSQL_HISTORY_WARN else "warn",
            finding=f"{int(history):,} undo records are waiting to be purged.",
            evidence=[{"label": "History list length", "value": f"{int(history):,}"}],
            why="A long-running transaction keeps every old row version it might read. The "
                "undo log grows and reads walk longer version chains.",
            steps="Find the oldest open transactions and end them: SELECT trx_mysql_thread_id, "
                  "trx_started FROM information_schema.innodb_trx ORDER BY trx_started;"))
    pct = vacuum.get("txid_age_pct")
    if isinstance(pct, (int, float)) and pct >= 50:
        actions.append(_action(
            id="wraparound", check="vacuum", title="Freeze old transaction IDs",
            severity="bad" if pct >= 75 else "warn",
            finding=f"Transaction IDs are {pct:.0f}% of the way to wraparound.",
            evidence=[{"label": "Of the way to wraparound", "value": f"{pct:.0f}%"},
                      {"label": "Transaction age", "value": f"{int(vacuum.get('txid_age') or 0):,}"}],
            why="At the limit PostgreSQL stops accepting writes until a vacuum freezes them.",
            steps="Run a freezing vacuum on the oldest tables during a quiet hour.",
            sql="VACUUM (FREEZE, ANALYZE);"))
    if vacuum.get("autovacuum_enabled") is False:
        actions.append(_action(
            id="setting_autovacuum", check="settings", title=SETTING_WORDS["autovacuum"][0],
            severity="bad", finding="autovacuum is off.",
            evidence=[{"label": "autovacuum", "value": "off"}],
            why=SETTING_WORDS["autovacuum"][1],
            steps="Turn it on and reload the configuration.",
            sql="ALTER SYSTEM SET autovacuum = on;\nSELECT pg_reload_conf();",
            setting={"name": "autovacuum", "current": "off", "target": "on"}))
    return actions


def _connections(connections: dict[str, Any], numbers: dict[str, Any], engine: str
                 ) -> list[dict[str, Any]]:
    actions = []
    total = int(connections.get("total_connections") or 0)
    limit = int(connections.get("max_connections") or numbers.get("max_conns") or 0)
    peak = numbers.get("active_conns_peak")
    pressure = max(total / limit if limit else 0,
                   peak / limit if limit and isinstance(peak, (int, float)) else 0)
    if limit and pressure >= 0.8:
        pooler = connections.get("pooler_detected")
        evidence = [{"label": "Connections when checked", "value": f"{total} of {limit}"}]
        if isinstance(peak, (int, float)):
            evidence.append({"label": "Peak active that day", "value": f"{int(peak)}"})
        actions.append(_action(
            id="connections", check="connections",
            title="Pool connections before the server runs out" if not pooler else
                  "Lower the pool size",
            severity="bad" if pressure >= 0.95 else "warn",
            finding=f"{max(total, int(peak or 0))} of {limit} connections in use at the most.",
            evidence=evidence,
            why="Past the limit new connections are refused, and each open one holds memory.",
            steps=(f"Put a pooler such as {'ProxySQL' if engine == 'mysql' else 'PgBouncer'} "
                   "in front of the database, or lower the application's pool size."
                   if not pooler else
                   "Lower the pooler's server pool size, or raise max_connections if the "
                   "server has memory to spare.")))
    idle = int(connections.get("idle_in_transaction_count") or 0)
    sessions = [row for row in connections.get("long_running_idle_in_tx") or []
                if isinstance(row, dict)]
    # A snapshot often lands between two statements of a busy transaction; only a session left
    # idle past a minute is one the application forgot.
    if idle and sessions:
        longest = max((float(row.get("duration_seconds") or 0) for row in sessions), default=0)
        evidence = [{"label": "Sessions idle in a transaction", "value": str(idle)}]
        if longest:
            evidence.append({"label": "Longest", "value": f"{longest / 60:.0f} min"})
        actions.append(_action(
            id="idle_in_transaction", check="connections",
            title="Close transactions the application leaves open",
            severity="warn",
            finding=f"{idle} {'session' if idle == 1 else 'sessions'} sat idle inside an "
                    "open transaction.",
            evidence=evidence,
            why="An open transaction holds its locks and stops vacuum from cleaning rows it "
                "might still see.",
            steps=("Find the code path that leaves them open. MySQL has no idle-transaction "
                   "timeout; wait_timeout ends any idle session, pooled ones included."
                   if engine == "mysql" else
                   "Find the code path that leaves them open. A timeout ends stragglers "
                   "meanwhile."),
            rows=[{"name": str(row.get("application_name") or f"pid {row.get('pid')}"),
                   "value": f"{float(row.get('duration_seconds') or 0) / 60:.0f} min",
                   "detail": plain(row.get("query_preview") or "")} for row in sessions],
            sql=None if engine == "mysql" else
                "ALTER SYSTEM SET idle_in_transaction_session_timeout = '5min';\n"
                "SELECT pg_reload_conf();",
            setting=None if engine == "mysql" else
                {"name": "idle_in_transaction_session_timeout", "current": "",
                 "target": "5min"}))
    return actions


def _replication(replication: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    lag = replication.get("upstream_lag_seconds")
    if isinstance(lag, (int, float)) and lag >= 30:
        actions.append(_action(
            id="replica_lag", check="replication", title="Find why the replica lags",
            severity="bad" if lag >= 300 else "warn",
            finding=f"The replica was {lag:.0f} seconds behind its primary.",
            evidence=[{"label": "Replica lag", "value": f"{lag:.0f} s"}],
            why="Reads from a lagging replica return old data, and failover loses what it "
                "hasn't applied.",
            steps="Check the replica's disk and CPU, and long queries on it that hold back "
                  "replay."))
    slots = [slot for slot in replication.get("slots") or []
             if isinstance(slot, dict) and not slot.get("active")]
    if slots or int(replication.get("inactive_slots") or 0):
        retained = sum(float(slot.get("retained_bytes") or 0) for slot in slots)
        count = len(slots) or int(replication.get("inactive_slots") or 0)
        evidence = [{"label": "Inactive slots", "value": str(count)}]
        if retained:
            evidence.append({"label": "WAL they hold", "value": format_size(retained)})
        actions.append(_action(
            id="inactive_slots", check="replication",
            title=f"Drop {count} unused replication {'slot' if count == 1 else 'slots'}",
            severity="warn",
            finding=f"{count} replication {'slot is' if count == 1 else 'slots are'} not "
                    "being read.",
            evidence=evidence,
            why="An unread slot keeps WAL on disk until it's read or dropped, and the disk "
                "can fill.",
            steps="Drop a slot only once nothing will reconnect to it.",
            rows=[{"name": str(slot.get("slot_name")), "value": format_size(slot.get("retained_bytes")),
                   "detail": str(slot.get("slot_type") or "")} for slot in slots],
            sql="\n".join(f"SELECT pg_drop_replication_slot('{slot.get('slot_name')}');"
                          for slot in slots) or None))
    return actions


def _workload(numbers: dict[str, Any], prior: dict[str, Any], engine: str
              ) -> list[dict[str, Any]]:
    actions = []
    lowest = numbers.get("cache_hit_ratio_min")
    reads = numbers.get("disk_reads_per_s")
    quiet = isinstance(reads, (int, float)) and reads < MIN_DISK_READS_PER_S
    if isinstance(lowest, (int, float)) and lowest < 0.9 and not quiet:
        evidence = [{"label": "Lowest cache hit", "value": _pct(lowest)}]
        average = numbers.get("cache_hit_ratio_avg")
        if isinstance(average, (int, float)):
            evidence.append({"label": "Day's average", "value": _pct(average)})
        actions.append(_action(
            id="cache_hit", check="cache", title="Find what pushed hot data out of memory",
            severity="bad" if lowest < 0.8 else "warn",
            finding=f"Cache hit fell to {_pct(lowest)} at its lowest.",
            evidence=evidence,
            why="Each miss is a disk read. Queries slow down across the board when the hot "
                "data no longer fits in memory.",
            steps="Look for large scans in the day's queries first. If the working set grew, "
                  "give the database more memory."))
    rollbacks, before = numbers.get("xact_rollback"), prior.get("xact_rollback")
    if isinstance(rollbacks, (int, float)) and rollbacks >= 100 and (
            not isinstance(before, (int, float)) or rollbacks >= 2 * before):
        evidence = [{"label": "Rollbacks", "value": f"{int(rollbacks):,}"}]
        if isinstance(before, (int, float)):
            evidence.append({"label": "The day before", "value": f"{int(before):,}"})
        actions.append(_action(
            id="rollbacks", check="transactions", title="Find out why transactions roll back",
            severity="warn",
            finding=f"{int(rollbacks):,} transactions rolled back.",
            evidence=evidence,
            why="Each rollback is work thrown away, and a rise usually means errors in the "
                "application.",
            steps="Check the application's error logs for the hours the rollbacks rose; the "
                  "chart's rollback line shows when."))
    deadlocks = numbers.get("deadlocks")
    if isinstance(deadlocks, (int, float)) and deadlocks > 0:
        actions.append(_action(
            id="deadlocks", check="transactions", title="Find the transactions that deadlock",
            severity="bad" if deadlocks >= 10 else "warn",
            finding=f"{int(deadlocks):,} {'deadlock' if deadlocks == 1 else 'deadlocks'}.",
            evidence=[{"label": "Deadlocks", "value": f"{int(deadlocks):,}"}],
            why="The database kills one side of each deadlock, so the application sees an "
                "error and retries.",
            steps="Log lock waits to see which statements collide, then take locks in one "
                  "order in the application.",
            sql="SET PERSIST innodb_print_all_deadlocks = ON;" if engine == "mysql" else
                "ALTER SYSTEM SET log_lock_waits = on;\nSELECT pg_reload_conf();",
            setting={"name": "innodb_print_all_deadlocks", "current": "", "target": "ON"}
            if engine == "mysql" else
                {"name": "log_lock_waits", "current": "", "target": "on"}))
    return actions


def _sequential_scans(section: dict[str, Any]) -> list[dict[str, Any]]:
    actions = []
    for row in section.get("deltas", []):
        rows, scans = row["rows"], row["scans_day"]
        if rows < LARGE_TABLE_ROWS or scans < FULL_SCANS_PER_DAY:
            continue
        table = f"{row['schema']}.{row['table']}"
        queries = [query for query in section.get("queries", [])
                   if table in query.get("tables", []) or row["table"] in query.get("tables", [])][:3]
        actions.append(_action(
            id=f"sequential_scans_{table}", check="sequential_scans",
            title=f"Review full scans of {table}",
            severity="bad" if rows >= BAD_SCAN_MULTIPLIER * LARGE_TABLE_ROWS and
                     scans >= BAD_SCAN_MULTIPLIER * FULL_SCANS_PER_DAY else "warn",
            finding=f"{table} had {scans:,} full scans since yesterday with about {rows:,} rows.",
            evidence=[{"label": "Full scans since yesterday", "value": f"{scans:,}"},
                      {"label": "Estimated rows", "value": f"{rows:,}"}],
            why="Repeated full scans read the whole table and compete with other queries for memory and disk.",
            steps="Review filters and joins in these queries and check whether an index would help.",
            rows=[{"name": query.get("display_name") or plain(query.get("normalized_sql") or ""),
                   "value": "Touches this table", "detail": query["query_key"]} for query in queries],
            query_keys=[query["query_key"] for query in queries]))
    return actions


def database_actions(collectors: dict[str, Any] | None, numbers: dict[str, Any],
                     prior: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every database-level action the day's evidence supports, worst first."""
    collectors = collectors or {}
    engine = str(collectors.get("engine") or "postgresql").lower()
    errors = collectors.get("section_errors") or {}
    usable = {name: section for name, section in collectors.items()
              if isinstance(section, dict) and name not in errors}
    actions = _workload(numbers, prior or {}, engine)
    actions += _settings(usable.get("config_audit") or {}, engine, _memory_short(numbers))
    actions += _indexes(usable.get("index_health") or {}, engine)
    actions += _vacuum(usable.get("vacuum_bloat") or {}, engine)
    actions += _connections(usable.get("connections") or {}, numbers, engine)
    actions += _replication(usable.get("replication") or {})
    actions += _sequential_scans(usable.get("sequential_scans") or {})
    seen: set[str] = set()
    unique = []
    for action in actions:
        if action["id"] not in seen:
            seen.add(action["id"])
            unique.append(action)
    # A low cache hit and a small cache setting are one story; the setting carries it.
    ids = {action["id"] for action in unique}
    buffers = f"setting_{DEFAULT_BUFFERS.get(engine, 'shared_buffers')}"
    if "cache_hit" in ids and buffers in ids:
        cache = next(action for action in unique if action["id"] == "cache_hit")
        unique = [action for action in unique if action["id"] != "cache_hit"]
        for action in unique:
            if action["id"] == buffers:
                known = {row["label"] for row in action["evidence"]}
                action["evidence"] += [row for row in cache["evidence"]
                                       if row["label"] not in known]
                action["severity"] = min(action["severity"], cache["severity"],
                                         key=SEVERITY_RANK.__getitem__)
                if not action["finding"].startswith("Only "):
                    action["finding"] = f"{cache['finding']} {action['finding']}"
    unique.sort(key=lambda action: SEVERITY_RANK[action["severity"]])
    return unique


CHECKS = (("cache", "Cache hit"), ("connections", "Connections"),
          ("vacuum", "Vacuum and bloat"), ("indexes", "Indexes"), ("settings", "Settings"),
          ("replication", "Replication"), ("transactions", "Rollbacks and deadlocks"),
          ("sequential_scans", "Full table scans"))
SECTION_OF = {"vacuum": "vacuum_bloat", "indexes": "index_health", "connections": "connections",
              "settings": "config_audit", "replication": "replication",
              "sequential_scans": "sequential_scans"}


def health_overview(collectors: dict[str, Any] | None, numbers: dict[str, Any],
                    actions: list[dict[str, Any]], open_events: int = 0) -> dict[str, Any]:
    """The day's health in one look: a verdict, a line, and each check with its value."""
    collectors = collectors or {}
    errors = collectors.get("section_errors") or {}
    failed = bool(collectors.get("collection_error"))
    by_check: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        if action["severity"] != "info":
            by_check.setdefault(action["check"], []).append(action)
    checks = []
    mysql = str(collectors.get("engine") or "").lower() == "mysql"
    for check, label in CHECKS:
        if mysql:
            label = MYSQL_LABELS.get(check, label)
        found = by_check.get(check, [])
        section = SECTION_OF.get(check)
        lowest = numbers.get("cache_hit_ratio_min")
        if check == "cache" and isinstance(lowest, (int, float)) and lowest < 0.9:
            # A low cache hit can be carried by the shared_buffers action; the check still
            # shows what the day measured.
            checks.append({"check": check, "label": label,
                           "status": "bad" if lowest < 0.8 else "warn",
                           "value": f"Fell to {_pct(lowest)}"})
            continue
        if found:
            status = "bad" if any(a["severity"] == "bad" for a in found) else "warn"
            value = found[0]["finding"].rstrip(".")
            if len(found) > 1:
                value = f"{len(found)} things to change"
        elif section and (failed or section in errors or not isinstance(collectors.get(section), dict)):
            status, value = "unknown", "Not checked"
        else:
            status, value = "ok", _ok_value(check, collectors, numbers)
        checks.append({"check": check, "label": label, "status": status, "value": value})
    worst = min((SEVERITY_RANK.get(check["status"], 3) for check in checks), default=3)
    status = "bad" if worst == 0 else "warn" if worst == 1 or open_events else "ok"
    flagged = [action for action in actions if action["severity"] != "info"]
    if flagged:
        headline = " ".join(action["finding"] for action in flagged[:2])
    else:
        headline = "Every check passed."
    return {"status": status,
            "label": {"bad": "Needs attention", "warn": "Worth a look", "ok": "Healthy"}[status],
            "headline": headline, "checks": checks}


# MySQL has no vacuum: InnoDB purges old rows itself, and a rebuild returns free space.
MYSQL_LABELS = {"vacuum": "Free space and purge"}


def _ok_value(check: str, collectors: dict[str, Any], numbers: dict[str, Any]) -> str:
    if check == "cache":
        lowest = numbers.get("cache_hit_ratio_min")
        return f"Lowest {_pct(lowest)}" if isinstance(lowest, (int, float)) else "No readings"
    if check == "connections":
        peak, limit = numbers.get("active_conns_peak"), numbers.get("max_conns")
        if isinstance(peak, (int, float)) and limit:
            return f"Peak {int(peak)} of {int(limit)}"
        return f"Peak {int(peak)}" if isinstance(peak, (int, float)) else "No readings"
    mysql = str(collectors.get("engine") or "").lower() == "mysql"
    if check == "vacuum":
        return "Nothing to reclaim, purge keeping up" if mysql else "No bloated tables"
    if check == "indexes":
        total = (collectors.get("index_health") or {}).get("total_indexes")
        return f"{total} indexes, all read" if total else "None unused"
    if check == "sequential_scans":
        if (collectors.get(check) or {}).get("compared"):
            return "No frequent scans of large tables"
        return "Learning scan counters"
    if check == "settings":
        return "In line with the checks"
    if check == "replication":
        replication = collectors.get("replication") or {}
        if replication.get("is_replica"):
            return "Replica in sync"
        return "No replica lag" if mysql else "No lag, no idle slots"
    rollbacks, deadlocks = numbers.get("xact_rollback"), numbers.get("deadlocks")
    if isinstance(rollbacks, (int, float)):
        return f"{int(rollbacks):,} rollbacks, {int(deadlocks or 0)} deadlocks"
    return "No readings"
