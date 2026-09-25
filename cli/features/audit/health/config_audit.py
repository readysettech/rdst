"""§6 Configuration Audit — current values vs best-practice recommendations.

PostgreSQL parameters checked: shared_buffers, work_mem, effective_cache_size,
maintenance_work_mem, max_connections, random_page_cost,
log_min_duration_statement. Recommendations are scaled from instance RAM (GB)
and vCPU count when provided.

MySQL equivalents: innodb_buffer_pool_size, sort_buffer_size, tmp_table_size,
innodb_redo_log_capacity (or innodb_log_file_size), innodb_io_capacity, max_connections,
the slow query log threshold and innodb_stats_auto_recalc.
"""

from __future__ import annotations

from typing import Any, List, Optional

from features.audit.health.models import ConfigAuditSection, ConfigSetting


def _mb_to_str(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{int(mb)} MB"


def _parse_pg_size(value: str) -> Optional[float]:
    """Parse a pg_settings value expressed in 8kB blocks or MB. Returns MB."""
    try:
        if not value:
            return None
        s = str(value).strip()
        # pg_settings returns numeric strings in a unit context; shared_buffers
        # setting is in 8kB pages by default. We do best-effort parsing.
        if s.endswith("GB"):
            return float(s[:-2].strip()) * 1024
        if s.endswith("MB"):
            return float(s[:-2].strip())
        if s.endswith("kB"):
            return float(s[:-2].strip()) / 1024
        # Plain integer — caller must know the unit
        return float(s)
    except (TypeError, ValueError):
        return None


def collect_config_audit(
    conn: Any,
    engine: str,
    instance_ram_gb: Optional[float] = None,
    instance_vcpus: Optional[int] = None,
) -> Optional[ConfigAuditSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn, instance_ram_gb, instance_vcpus)
    if engine == "mysql":
        return _collect_mysql(conn, instance_ram_gb, instance_vcpus)
    return None


_PG_PARAMS = [
    "shared_buffers",
    "work_mem",
    "effective_cache_size",
    "maintenance_work_mem",
    "max_connections",
    "random_page_cost",
    "log_min_duration_statement",
    "autovacuum",
    "autovacuum_max_workers",
]


def _collect_pg(
    conn: Any, ram_gb: Optional[float], vcpus: Optional[int]
) -> ConfigAuditSection:
    section = ConfigAuditSection(instance_ram_gb=ram_gb, instance_vcpus=vcpus)
    raw: dict = {}
    raw_units: dict = {}
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(_PG_PARAMS))
            cur.execute(
                f"""
                SELECT name, setting, unit
                FROM pg_settings
                WHERE name IN ({placeholders});
                """,
                _PG_PARAMS,
            )
            for name, setting, unit in cur.fetchall():
                raw[name] = setting
                raw_units[name] = unit
    except Exception:
        return section

    def _current_mb(name: str) -> Optional[float]:
        val = raw.get(name)
        unit = raw_units.get(name)
        if val is None:
            return None
        try:
            n = float(val)
        except (TypeError, ValueError):
            return None
        if unit == "8kB":
            return n * 8 / 1024
        if unit == "kB":
            return n / 1024
        if unit == "MB":
            return n
        if unit == "GB":
            return n * 1024
        return n  # unknown unit

    settings: List[ConfigSetting] = []

    # shared_buffers → recommend 25% of RAM
    sb_mb = _current_mb("shared_buffers")
    if sb_mb is not None:
        rec_mb = (ram_gb or 0) * 1024 * 0.25
        rec_str = _mb_to_str(rec_mb) if rec_mb > 0 else "25% of RAM"
        status = "ok"
        note = "25% of RAM is standard"
        if rec_mb > 0:
            ratio = sb_mb / rec_mb
            if ratio < 0.5:
                status = "warn"
                note = "Below 25% of RAM - PostgreSQL has less room for hot pages"
            elif ratio > 1.5:
                status = "warn"
                note = "Above 25% of RAM - may starve OS page cache"
        settings.append(
            ConfigSetting(
                parameter="shared_buffers",
                current=_mb_to_str(sb_mb),
                recommended=rec_str,
                status=status,
                note=note,
            )
        )

    # effective_cache_size → recommend 75% of RAM
    ec_mb = _current_mb("effective_cache_size")
    if ec_mb is not None:
        rec_mb = (ram_gb or 0) * 1024 * 0.75
        rec_str = _mb_to_str(rec_mb) if rec_mb > 0 else "75% of RAM"
        status = "ok"
        note = "Used by planner; not an allocation"
        if rec_mb > 0 and ec_mb < rec_mb * 0.6:
            status = "warn"
            note = "Low - planner may underestimate cacheability"
        settings.append(
            ConfigSetting(
                parameter="effective_cache_size",
                current=_mb_to_str(ec_mb),
                recommended=rec_str,
                status=status,
                note=note,
            )
        )

    # work_mem — recommend 16–32 MB for analytical workloads
    wm_mb = _current_mb("work_mem")
    if wm_mb is not None:
        status = "ok" if wm_mb >= 16 else "warn"
        note = (
            "Low for aggregation-heavy queries - causes disk sorts"
            if wm_mb < 16
            else "Adequate for typical workloads"
        )
        settings.append(
            ConfigSetting(
                parameter="work_mem",
                current=_mb_to_str(wm_mb),
                recommended="16 - 32 MB",
                status=status,
                note=note,
            )
        )

    # maintenance_work_mem — 512 MB–1 GB
    mwm_mb = _current_mb("maintenance_work_mem")
    if mwm_mb is not None:
        status = "ok" if mwm_mb >= 256 else "warn"
        note = (
            "Adequate for vacuum and index builds"
            if mwm_mb >= 256
            else "Low - slows vacuum and CREATE INDEX"
        )
        settings.append(
            ConfigSetting(
                parameter="maintenance_work_mem",
                current=_mb_to_str(mwm_mb),
                recommended="512 MB - 1 GB",
                status=status,
                note=note,
            )
        )

    # max_connections — heuristic: 100–200 for 2 vCPU, pooler recommended above
    mc = raw.get("max_connections")
    if mc is not None:
        try:
            mc_i = int(mc)
        except (TypeError, ValueError):
            mc_i = 0
        if vcpus and vcpus > 0:
            rec_upper = max(100, vcpus * 50)
            rec_str = f"{rec_upper // 2} - {rec_upper}"
        else:
            rec_str = "100 - 200"
        status = "ok"
        note = "Within typical range"
        if mc_i > 500:
            status = "warn"
            note = "Very high - use a connection pooler instead of raising the limit"
        elif mc_i < 20:
            status = "warn"
            note = "Very low - may exhaust under minor load"
        settings.append(
            ConfigSetting(
                parameter="max_connections",
                current=str(mc_i),
                recommended=rec_str,
                status=status,
                note=note,
            )
        )

    # random_page_cost — 1.1 on SSD/gp3
    rpc = raw.get("random_page_cost")
    if rpc is not None:
        try:
            rpc_f = float(rpc)
        except (TypeError, ValueError):
            rpc_f = 0
        status = "ok" if 1.0 <= rpc_f <= 1.5 else "warn"
        note = (
            "Correct for SSD / gp3 storage"
            if status == "ok"
            else "Assumes spinning disk - lower to 1.1 on SSD"
        )
        settings.append(
            ConfigSetting(
                parameter="random_page_cost",
                current=str(rpc),
                recommended="1.1",
                status=status,
                note=note,
            )
        )

    # log_min_duration_statement — 200–500 ms
    lmd = raw.get("log_min_duration_statement")
    if lmd is not None:
        try:
            lmd_ms = int(lmd)
        except (TypeError, ValueError):
            lmd_ms = -1
        status = "ok"
        if lmd_ms < 0:
            status = "warn"
            note = "Slow query logging disabled - enable to track regressions"
            current_str = "disabled"
        else:
            current_str = f"{lmd_ms} ms"
            if lmd_ms > 2000:
                status = "warn"
                note = "High threshold - missing medium-slow queries"
            elif lmd_ms == 0:
                status = "warn"
                note = "Logging every statement - expensive, lower signal"
            else:
                note = "Reasonable threshold for capturing slow queries"
        settings.append(
            ConfigSetting(
                parameter="log_min_duration_statement",
                current=current_str,
                recommended="200 - 500 ms",
                status=status,
                note=note,
            )
        )

    # autovacuum on/off
    av = raw.get("autovacuum")
    if av is not None:
        status = "ok" if av == "on" else "crit"
        settings.append(
            ConfigSetting(
                parameter="autovacuum",
                current=str(av),
                recommended="on",
                status=status,
                note="Autovacuum must stay enabled to prevent bloat and TXID wraparound"
                if status == "crit"
                else "Enabled",
            )
        )

    section.settings = settings
    return section


_MYSQL_PARAMS = (
    "innodb_buffer_pool_size",
    "max_connections",
    "sort_buffer_size",
    "tmp_table_size",
    "max_heap_table_size",
    "innodb_redo_log_capacity",
    "innodb_log_file_size",
    "innodb_log_files_in_group",
    "innodb_io_capacity",
    "slow_query_log",
    "long_query_time",
    "innodb_stats_persistent",
    "innodb_stats_auto_recalc",
)


def _collect_mysql(
    conn: Any, ram_gb: Optional[float], vcpus: Optional[int]
) -> ConfigAuditSection:
    """The MySQL counterparts of the PostgreSQL checks: buffer pool (shared_buffers), sort
    and temp-table memory (work_mem), redo log (checkpoint I/O), I/O capacity
    (random_page_cost), max_connections, the slow query log (log_min_duration_statement)
    and automatic statistics (autovacuum's ANALYZE)."""
    section = ConfigAuditSection(instance_ram_gb=ram_gb, instance_vcpus=vcpus)
    settings: List[ConfigSetting] = []
    try:
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(_MYSQL_PARAMS))
            cur.execute(f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({placeholders})",
                        _MYSQL_PARAMS)
            raw = {str(k): v for k, v in cur.fetchall()}
    except Exception:
        return section

    def _mb(name: str) -> Optional[float]:
        try:
            return int(raw[name]) / (1024 * 1024)
        except (KeyError, TypeError, ValueError):
            return None

    bp_mb = _mb("innodb_buffer_pool_size")
    if bp_mb is not None:
        rec_mb = (ram_gb or 0) * 1024 * 0.7
        status, note = "ok", "70% of RAM is typical on a dedicated server"
        if rec_mb > 0 and bp_mb < rec_mb * 0.5:
            status, note = "warn", "Below half of 70% of RAM - InnoDB has less room for hot pages"
        settings.append(ConfigSetting(
            parameter="innodb_buffer_pool_size", current=_mb_to_str(bp_mb),
            recommended=_mb_to_str(rec_mb) if rec_mb > 0 else "70% of RAM",
            status=status, note=note))

    sort_mb = _mb("sort_buffer_size")
    if sort_mb is not None:
        settings.append(ConfigSetting(
            parameter="sort_buffer_size", current=_mb_to_str(sort_mb) if sort_mb >= 1
            else f"{int(sort_mb * 1024)} KB", recommended="256 KB-2 MB",
            status="warn" if sort_mb > 8 else "ok",
            note="Allocated per sort in every session; large values waste memory under "
                 "concurrency" if sort_mb > 8 else "Adequate for typical workloads"))

    tmp_mb, heap_mb = _mb("tmp_table_size"), _mb("max_heap_table_size")
    if tmp_mb is not None:
        limit = min(tmp_mb, heap_mb) if heap_mb is not None else tmp_mb
        settings.append(ConfigSetting(
            parameter="tmp_table_size", current=_mb_to_str(limit), recommended="32-64 MB",
            status="warn" if limit < 16 else "ok",
            note="Low - GROUP BY and DISTINCT spill temporary tables to disk"
            if limit < 16 else "Adequate for in-memory temporary tables"))

    redo_mb = _mb("innodb_redo_log_capacity")
    if redo_mb is None and _mb("innodb_log_file_size") is not None:
        try:
            redo_mb = _mb("innodb_log_file_size") * int(raw.get("innodb_log_files_in_group") or 2)
        except (TypeError, ValueError):
            redo_mb = None
    if redo_mb is not None:
        settings.append(ConfigSetting(
            parameter="innodb_redo_log_capacity" if "innodb_redo_log_capacity" in raw
            else "innodb_log_file_size", current=_mb_to_str(redo_mb), recommended="1-4 GB",
            status="warn" if redo_mb < 512 else "ok",
            note="Small - write bursts force frequent checkpoints" if redo_mb < 512
            else "Enough room for write bursts"))

    io = raw.get("innodb_io_capacity")
    if io is not None:
        try:
            io_i = int(io)
        except (TypeError, ValueError):
            io_i = 0
        settings.append(ConfigSetting(
            parameter="innodb_io_capacity", current=str(io_i), recommended="1000-2000",
            status="warn" if 0 < io_i <= 200 else "ok",
            note="200 assumes spinning disks - raise it on SSD storage" if 0 < io_i <= 200
            else "Suits SSD storage"))

    mc = raw.get("max_connections")
    if mc is not None:
        try:
            mc_i = int(mc)
        except (TypeError, ValueError):
            mc_i = 0
        rec_upper = max(100, vcpus * 50) if vcpus and vcpus > 0 else 200
        status, note = "ok", "Within typical range"
        if mc_i > 500:
            status, note = "warn", "Very high - use a connection pooler instead of raising the limit"
        elif mc_i < 20:
            status, note = "warn", "Very low - may exhaust under minor load"
        settings.append(ConfigSetting(
            parameter="max_connections", current=str(mc_i),
            recommended=f"{rec_upper // 2}-{rec_upper}", status=status, note=note))

    slow = raw.get("slow_query_log")
    if slow is not None:
        on = str(slow).upper() in ("1", "ON")
        try:
            threshold_ms = float(raw.get("long_query_time") or 10) * 1000
        except (TypeError, ValueError):
            threshold_ms = 10_000
        status, current = "ok", f"{threshold_ms:g} ms"
        note = "Reasonable threshold for capturing slow queries"
        if not on:
            status, current = "warn", "disabled"
            note = "Slow query logging disabled - enable to track regressions"
        elif threshold_ms > 2000:
            status, note = "warn", "High threshold - missing medium-slow queries"
        elif threshold_ms == 0:
            status, note = "warn", "Logging every statement - expensive, lower signal"
        settings.append(ConfigSetting(
            parameter="long_query_time", current=current, recommended="200-500 ms",
            status=status, note=note))

    persistent = str(raw.get("innodb_stats_persistent", "ON")).upper() in ("1", "ON")
    recalc = raw.get("innodb_stats_auto_recalc")
    if recalc is not None:
        on = str(recalc).upper() in ("1", "ON")
        settings.append(ConfigSetting(
            parameter="innodb_stats_auto_recalc", current="ON" if on else "OFF",
            recommended="ON", status="ok" if on or not persistent else "crit",
            note="Enabled" if on else
            "Statistics never refresh on their own - plans drift as tables change"))

    section.settings = settings
    return section
