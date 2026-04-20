"""§6 Configuration Audit — current values vs best-practice recommendations.

PostgreSQL parameters checked: shared_buffers, work_mem, effective_cache_size,
maintenance_work_mem, max_connections, random_page_cost,
log_min_duration_statement. Recommendations are scaled from instance RAM (GB)
and vCPU count when provided.

MySQL equivalents: innodb_buffer_pool_size, sort_buffer_size,
innodb_log_file_size, max_connections, slow_query_log threshold.
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
                note = "Below 25% of RAM — PostgreSQL has less room for hot pages"
            elif ratio > 1.5:
                status = "warn"
                note = "Above 25% of RAM — may starve OS page cache"
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
            note = "Low — planner may underestimate cacheability"
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
            "Low for aggregation-heavy queries — causes disk sorts"
            if wm_mb < 16
            else "Adequate for typical workloads"
        )
        settings.append(
            ConfigSetting(
                parameter="work_mem",
                current=_mb_to_str(wm_mb),
                recommended="16–32 MB",
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
            else "Low — slows vacuum and CREATE INDEX"
        )
        settings.append(
            ConfigSetting(
                parameter="maintenance_work_mem",
                current=_mb_to_str(mwm_mb),
                recommended="512 MB–1 GB",
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
            rec_str = f"{rec_upper // 2}–{rec_upper}"
        else:
            rec_str = "100–200"
        status = "ok"
        note = "Within typical range"
        if mc_i > 500:
            status = "warn"
            note = "Very high — use a connection pooler instead of raising the limit"
        elif mc_i < 20:
            status = "warn"
            note = "Very low — may exhaust under minor load"
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
            else "Assumes spinning disk — lower to 1.1 on SSD"
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
            note = "Slow query logging disabled — enable to track regressions"
            current_str = "disabled"
        else:
            current_str = f"{lmd_ms} ms"
            if lmd_ms > 2000:
                status = "warn"
                note = "High threshold — missing medium-slow queries"
            elif lmd_ms == 0:
                status = "warn"
                note = "Logging every statement — expensive, lower signal"
            else:
                note = "Reasonable threshold for capturing slow queries"
        settings.append(
            ConfigSetting(
                parameter="log_min_duration_statement",
                current=current_str,
                recommended="200–500 ms",
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


def _collect_mysql(
    conn: Any, ram_gb: Optional[float], vcpus: Optional[int]
) -> ConfigAuditSection:
    section = ConfigAuditSection(instance_ram_gb=ram_gb, instance_vcpus=vcpus)
    settings: List[ConfigSetting] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SHOW VARIABLES WHERE Variable_name IN (
                    'innodb_buffer_pool_size',
                    'max_connections',
                    'sort_buffer_size',
                    'tmp_table_size',
                    'slow_query_log',
                    'long_query_time'
                );
                """
            )
            raw = {k: v for k, v in cur.fetchall()}
    except Exception:
        return section

    bp = raw.get("innodb_buffer_pool_size")
    if bp is not None:
        try:
            bp_mb = int(bp) / (1024 * 1024)
        except (TypeError, ValueError):
            bp_mb = 0
        rec_mb = (ram_gb or 0) * 1024 * 0.7
        status = "ok"
        if rec_mb > 0 and bp_mb < rec_mb * 0.5:
            status = "warn"
        settings.append(
            ConfigSetting(
                parameter="innodb_buffer_pool_size",
                current=_mb_to_str(bp_mb),
                recommended=_mb_to_str(rec_mb) if rec_mb > 0 else "70% of RAM",
                status=status,
                note="InnoDB hot pages live here — 70% of RAM is typical",
            )
        )

    mc = raw.get("max_connections")
    if mc is not None:
        try:
            mc_i = int(mc)
        except (TypeError, ValueError):
            mc_i = 0
        status = "warn" if mc_i > 500 else "ok"
        settings.append(
            ConfigSetting(
                parameter="max_connections",
                current=str(mc_i),
                recommended="100–200",
                status=status,
                note="Use a connection pooler instead of raising the limit"
                if status == "warn"
                else "Within typical range",
            )
        )

    slow = raw.get("slow_query_log")
    if slow is not None:
        status = "ok" if str(slow).upper() in ("1", "ON") else "warn"
        settings.append(
            ConfigSetting(
                parameter="slow_query_log",
                current=str(slow),
                recommended="ON",
                status=status,
                note="Slow query log captures expensive statements for analysis",
            )
        )

    section.settings = settings
    return section
