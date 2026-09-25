"""Monitoring prerequisites: can RDST see the queries running on a target?

Monitoring reads statement statistics every minute (pg_stat_statements on
PostgreSQL, performance_schema on MySQL). Each check below reads the real
server state, and the visibility probe runs a tagged statement on a second
session and confirms the statistics picked it up, so a pass means RDST will
actually see new queries. Failed checks carry the fix for the detected
provider (see prerequisite_fixes).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from features.configure.prerequisite_fixes import Fix, fix_for, provider_name
from shared.query_capture_limits import DB_QUERY_SIZE_WARN_THRESHOLD

logger = logging.getLogger(__name__)

LANE = "rdst/prereq"
PROBE_ALIAS = "rdst_visibility_probe"
POSTGRES_PROBE_SQL = (
    f"/*rdst:prereq*/ SELECT pg_catalog.md5(pg_catalog.pg_backend_pid()::text) AS {PROBE_ALIAS}"
)
MYSQL_PROBE_SQL = f"/*rdst:prereq*/ SELECT MD5(CONNECTION_ID()) AS {PROBE_ALIAS}"
PROBE_WAIT_SECONDS = 1.5
INSUFFICIENT = "<insufficient privilege>"
LONG_QUERIES_PROBLEM = "Long queries aren't captured in full right now"

# Status vocabulary shared with the web checker.
OK, MISSING, WARN, UNKNOWN, WAITING = "ok", "missing", "warn", "unknown", "waiting"

MYSQL_REQUIRED_CONSUMERS = ("global_instrumentation", "statements_digest")
MYSQL_SAMPLE_CONSUMERS = (
    "thread_instrumentation",
    "events_statements_current",
    "events_statements_history",
    "events_statements_history_long",
)


@dataclass
class Check:
    id: str
    label: str
    status: str
    detail: str
    blocking: bool = True
    fix: Optional[Fix] = None
    # Recommendations improve what RDST captures; the rest are what monitoring needs.
    recommended: bool = False
    # The check as a plain statement of what's wrong right now, for a check that isn't ok
    # ("Long queries aren't captured in full right now"). `label` names the goal.
    problem: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fix"] = self.fix.as_dict() if self.fix else None
        # Plain-text fix for older readers of `remediation`.
        data["remediation"] = self.fix.plain() if self.fix and self.status != OK else None
        if self.status == OK:
            data["problem"] = None
        return data


@dataclass
class Context:
    engine: str
    provider: str
    user: str
    database: str
    extras: dict[str, Any] = field(default_factory=dict)


def _rows(conn: Any, sql: str, params: Optional[tuple] = None) -> list[tuple]:
    cursor = conn.cursor()
    try:
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        rows = cursor.fetchall() or []
        return [tuple(row.values()) if isinstance(row, dict) else tuple(row) for row in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass


def _one(conn: Any, sql: str, params: Optional[tuple] = None) -> Any:
    rows = _rows(conn, sql, params)
    return rows[0][0] if rows and rows[0] else None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "on", "true", "yes", "t"}


def _host_provider(host: str) -> Optional[str]:
    host = (host or "").lower()
    if host.endswith(".rds.amazonaws.com"):
        return "aurora" if ".cluster-" in host else "rds"
    if host.endswith(".supabase.co") or host.endswith(".supabase.com"):
        return "supabase"
    if host.endswith(".neon.tech"):
        return "neon"
    if host.endswith(".db.ondigitalocean.com"):
        return "digitalocean"
    if host.endswith(".database.azure.com"):
        return "azure"
    return None


def _tag_provider(target_config: dict[str, Any]) -> Optional[str]:
    for tag in target_config.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("provider:"):
            value = tag.split(":", 1)[1].strip().lower()
            aliases = {"aws": "rds", "rds-aurora": "aurora", "gcp": "cloudsql"}
            return aliases.get(value, value) or None
    return None


def detect_provider(conn: Any, engine: str, target_config: dict[str, Any]) -> str:
    """Best guess of where the database runs: host and tags first, then SQL signals."""
    by_host = _host_provider(str(target_config.get("host") or ""))
    try:
        signal = _postgres_provider(conn) if engine == "postgresql" else _mysql_provider(conn)
    except Exception as exc:
        logger.debug("provider detection query failed: %s", exc)
        signal = None
    # SQL signals beat the host for RDS vs Aurora (custom endpoints hide ".cluster-").
    if signal in {"rds", "aurora"} and by_host in {None, "rds", "aurora"}:
        return signal
    return by_host or _tag_provider(target_config) or signal or "self_hosted"


def _postgres_provider(conn: Any) -> Optional[str]:
    roles = {
        row[0]
        for row in _rows(
            conn,
            "SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN "
            "('rds_superuser', 'cloudsqlsuperuser', 'azure_pg_admin', 'supabase_admin', "
            "'neon_superuser', 'doadmin')",
        )
    }
    if "rds_superuser" in roles:
        aurora = _one(
            conn, "SELECT count(*) FROM pg_catalog.pg_proc WHERE proname = 'aurora_version'"
        )
        return "aurora" if aurora else "rds"
    for role, provider in (
        ("cloudsqlsuperuser", "cloudsql"),
        ("azure_pg_admin", "azure"),
        ("supabase_admin", "supabase"),
        ("neon_superuser", "neon"),
        ("doadmin", "digitalocean"),
    ):
        if role in roles:
            return provider
    return None


def _mysql_provider(conn: Any) -> Optional[str]:
    variables = {
        str(row[0]).lower(): str(row[1] or "")
        for row in _rows(
            conn,
            "SHOW GLOBAL VARIABLES WHERE Variable_name IN "
            "('basedir', 'aurora_version', 'azure_server_name', 'logical_server_name') "
            "OR Variable_name LIKE 'cloudsql%'",
        )
    }
    if "aurora_version" in variables:
        return "aurora"
    if "rdsdbbin" in variables.get("basedir", "").lower():
        return "rds"
    if any(name.startswith("cloudsql") for name in variables):
        return "cloudsql"
    if "azure_server_name" in variables or "logical_server_name" in variables:
        return "azure"
    try:
        _one(conn, "SELECT mysql.rds_version()")
        return "rds"
    except Exception:
        return None


# PostgreSQL ----------------------------------------------------------------


def _pgss_schema(conn: Any) -> Optional[str]:
    return _one(
        conn,
        "SELECT n.nspname FROM pg_catalog.pg_extension e "
        "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'pg_stat_statements'",
    )


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _probe_calls_postgres(conn: Any, schema: str) -> int:
    value = _one(
        conn,
        f"SELECT coalesce(sum(calls), 0) FROM {_quote_ident(schema)}.pg_stat_statements "
        "WHERE dbid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()) "
        "AND query LIKE %s",
        (f"%{PROBE_ALIAS}%",),
    )
    return int(value or 0)


def _run_probe(open_probe: Callable[[], Any], sql: str) -> None:
    probe = open_probe()
    try:
        _rows(probe, sql)
    finally:
        try:
            probe.close()
        except Exception:
            pass


def _visibility_probe(
    before: int,
    read_calls: Callable[[], int],
    open_probe: Callable[[], Any],
    sql: str,
    sleep: Callable[[float], None],
) -> bool:
    _run_probe(open_probe, sql)
    deadline = time.monotonic() + PROBE_WAIT_SECONDS
    while True:
        if read_calls() > before:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.25)


def check_postgres(
    conn: Any,
    ctx: Context,
    open_probe: Callable[[], Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Check]:
    checks: list[Check] = []
    fix = lambda check_id: fix_for(check_id, ctx)  # noqa: E731

    available = bool(
        _one(
            conn,
            "SELECT count(*) FROM pg_catalog.pg_available_extensions "
            "WHERE name = 'pg_stat_statements'",
        )
    )
    schema = _pgss_schema(conn)
    ctx.extras["schema"] = schema
    ctx.extras["available"] = available

    preload_setting: Optional[str] = None
    try:
        preload_setting = _one(conn, "SELECT current_setting('shared_preload_libraries')")
    except Exception:
        preload_setting = None  # Needs pg_read_all_settings on some servers.
    ctx.extras["preload"] = preload_setting

    read_error: Optional[str] = None
    if schema:
        try:
            _one(conn, f"SELECT 1 FROM {_quote_ident(schema)}.pg_stat_statements LIMIT 1")
        except Exception as exc:
            read_error = str(exc).strip().splitlines()[0] if str(exc).strip() else "error"

    not_loaded = bool(read_error and "shared_preload_libraries" in read_error)
    if preload_setting is not None:
        loaded = "pg_stat_statements" in [
            part.strip().strip('"') for part in preload_setting.split(",")
        ]
    elif schema and read_error is None:
        loaded = True
    elif not_loaded:
        loaded = False
    else:
        loaded = None

    preload_label = "pg_stat_statements is loaded at server start"
    if loaded is True:
        checks.append(Check("pg_stat_statements_preload", preload_label, OK,
                            "It's in shared_preload_libraries."))
    elif loaded is False:
        checks.append(Check(
            "pg_stat_statements_preload", preload_label, MISSING,
            "pg_stat_statements isn't in shared_preload_libraries, so PostgreSQL doesn't "
            "collect statement statistics. Changing it needs a restart.",
            fix=fix("pg_stat_statements_preload"),
        ))
    else:
        checks.append(Check(
            "pg_stat_statements_preload", preload_label, WAITING,
            f"{ctx.user} can't read shared_preload_libraries, so RDST confirms this once the "
            "extension is created.",
        ))

    ext_label = f"pg_stat_statements is created in {ctx.database}"
    if schema:
        checks.append(Check("pg_stat_statements", ext_label, OK,
                            f"Installed in the {schema} schema."))
    elif not available:
        checks.append(Check(
            "pg_stat_statements", ext_label, MISSING,
            "The extension isn't available on this server. Install the PostgreSQL contrib "
            "package, then create it.",
            fix=fix("pg_stat_statements"),
        ))
    else:
        checks.append(Check(
            "pg_stat_statements", ext_label, MISSING,
            f"The extension is available but hasn't been created in {ctx.database}.",
            fix=fix("pg_stat_statements"),
        ))

    # Privileges: other sessions' statements and activity.
    superuser = bool(_one(
        conn, "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user"))
    member = bool(_one(
        conn,
        "SELECT pg_catalog.pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')",
    )) if not superuser else True
    hidden = 0
    try:
        hidden += int(_one(
            conn,
            "SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE query = %s",
            (INSUFFICIENT,),
        ) or 0)
        if schema and read_error is None:
            hidden += int(_one(
                conn,
                f"SELECT count(*) FROM {_quote_ident(schema)}.pg_stat_statements "
                "WHERE query = %s",
                (INSUFFICIENT,),
            ) or 0)
    except Exception as exc:
        logger.debug("hidden statement count failed: %s", exc)
    perm_label = "Permission to read every user's statements and activity"
    if superuser or member:
        how = "a superuser" if superuser else "a member of pg_read_all_stats"
        checks.append(Check("read_all_stats", perm_label, OK, f"{ctx.user} is {how}."))
    elif hidden:
        noun = "statement" if hidden == 1 else "statements"
        checks.append(Check(
            "read_all_stats", perm_label, MISSING,
            f"{hidden} {noun} from other users show as <insufficient privilege>. RDST can "
            "time them but can't show or analyze their SQL.",
            blocking=False,
            fix=fix("read_all_stats"),
        ))
    else:
        checks.append(Check(
            "read_all_stats", perm_label, WARN,
            f"Right now every statement comes from {ctx.user}, so RDST sees them all. "
            "Statements from other users will show without their SQL.",
            blocking=False,
            fix=fix("read_all_stats"),
        ))

    # Query text length in pg_stat_activity (running queries).
    size_label = "Long queries are captured in full"
    try:
        from shared.db_connection import parse_pg_size

        size = parse_pg_size(str(_one(conn, "SHOW track_activity_query_size")))
        if size >= DB_QUERY_SIZE_WARN_THRESHOLD:
            checks.append(Check("query_text_size", size_label, OK,
                                f"track_activity_query_size is {size} bytes.",
                                blocking=False, recommended=True))
        else:
            checks.append(Check(
                "query_text_size", size_label, WARN,
                f"track_activity_query_size is {size} bytes, so running queries longer than "
                f"that are cut off. {DB_QUERY_SIZE_WARN_THRESHOLD} or more is recommended.",
                blocking=False, recommended=True,
                fix=fix("query_text_size"), problem=LONG_QUERIES_PROBLEM,
            ))
    except Exception as exc:
        logger.debug("track_activity_query_size unreadable: %s", exc)

    # The real test: a new statement from another session shows up.
    probe_label = "RDST sees new statements as they run"
    if not schema or loaded is False or read_error:
        detail = "Runs once pg_stat_statements is loaded and created."
        if schema and read_error and not not_loaded:
            detail = f"Reading pg_stat_statements failed: {read_error}"
        checks.append(Check("visibility_probe", probe_label,
                            MISSING if (schema and read_error and not not_loaded) else WAITING,
                            detail))
        return checks
    track = None
    try:
        track = str(_one(conn, "SELECT current_setting('pg_stat_statements.track', true)") or "")
    except Exception:
        track = None
    try:
        before = _probe_calls_postgres(conn, schema)
        seen = _visibility_probe(
            before,
            lambda: _probe_calls_postgres(conn, schema),
            open_probe,
            POSTGRES_PROBE_SQL,
            sleep,
        )
    except Exception as exc:
        checks.append(Check("visibility_probe", probe_label, UNKNOWN,
                            f"The probe couldn't run: {str(exc).strip() or 'error'}"))
        return checks
    if seen:
        checks.append(Check("visibility_probe", probe_label, OK,
                            "A test statement from a second session appeared in "
                            "pg_stat_statements."))
    elif track == "none":
        checks.append(Check(
            "visibility_probe", probe_label, MISSING,
            "pg_stat_statements.track is none, so no statements are recorded.",
            fix=fix("pg_stat_statements_track"),
        ))
    else:
        checks.append(Check(
            "visibility_probe", probe_label, MISSING,
            "A test statement from a second session didn't appear in pg_stat_statements.",
            fix=fix("pg_stat_statements_track"),
        ))
    return checks


# MySQL ---------------------------------------------------------------------


def _probe_calls_mysql(conn: Any) -> int:
    value = _one(
        conn,
        "SELECT COALESCE(SUM(COUNT_STAR), 0) FROM "
        "performance_schema.events_statements_summary_by_digest WHERE DIGEST_TEXT LIKE %s",
        (f"%{PROBE_ALIAS}%",),
    )
    return int(value or 0)


def _mysql_denied(exc: Exception) -> bool:
    text = str(exc).lower()
    code = exc.args[0] if getattr(exc, "args", None) else None
    return code in (1142, 1143, 1227, 1044) or "denied" in text


def check_mysql(
    conn: Any,
    ctx: Context,
    open_probe: Callable[[], Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Check]:
    checks: list[Check] = []
    fix = lambda check_id: fix_for(check_id, ctx)  # noqa: E731

    ps_on = _truthy(_one(conn, "SELECT @@GLOBAL.performance_schema"))
    ps_label = "performance_schema is on"
    if ps_on:
        checks.append(Check("performance_schema", ps_label, OK, "performance_schema is ON."))
    else:
        checks.append(Check(
            "performance_schema", ps_label, MISSING,
            "performance_schema is OFF, so MySQL doesn't collect statement statistics. "
            "Turning it on needs a restart.",
            fix=fix("performance_schema"),
        ))

    grant_label = "Permission to read performance_schema"
    readable = False
    grant_error: Optional[Exception] = None
    if ps_on:
        try:
            _rows(conn, "SELECT 1 FROM "
                        "performance_schema.events_statements_summary_by_digest LIMIT 1")
            readable = True
        except Exception as exc:
            grant_error = exc
    if readable:
        checks.append(Check("read_all_stats", grant_label, OK,
                            f"{ctx.user} can read the statement digest tables."))
    elif grant_error is not None:
        detail = (f"{ctx.user} can't read performance_schema." if _mysql_denied(grant_error)
                  else f"Reading performance_schema failed: {str(grant_error).strip()}")
        checks.append(Check("read_all_stats", grant_label, MISSING, detail,
                            fix=fix("read_all_stats")))
    else:
        checks.append(Check("read_all_stats", grant_label, WAITING,
                            "Checked once performance_schema is on."))

    consumer_label = "Statement statistics are being collected"
    enabled: dict[str, bool] = {}
    if readable:
        try:
            for name, value in _rows(
                conn,
                "SELECT NAME, ENABLED FROM performance_schema.setup_consumers",
            ):
                enabled[str(name)] = _truthy(value)
        except Exception as exc:
            logger.debug("setup_consumers unreadable: %s", exc)
    if not readable:
        checks.append(Check("statement_consumers", consumer_label, WAITING,
                            "Checked once RDST can read performance_schema."))
    elif not enabled:
        checks.append(Check("statement_consumers", consumer_label, UNKNOWN,
                            "RDST couldn't read performance_schema.setup_consumers."))
    else:
        off = [name for name in MYSQL_REQUIRED_CONSUMERS if not enabled.get(name, False)]
        ctx.extras["consumers_off"] = off
        if off:
            checks.append(Check(
                "statement_consumers", consumer_label, MISSING,
                "These consumers are off: " + ", ".join(off) + ".",
                fix=fix("statement_consumers"),
            ))
        else:
            checks.append(Check("statement_consumers", consumer_label, OK,
                                "statements_digest is on."))
        samples_off = [name for name in MYSQL_SAMPLE_CONSUMERS if not enabled.get(name, False)]
        ctx.extras["sample_consumers_off"] = samples_off
        sample_label = "Recent statements are kept for analysis"
        if samples_off:
            checks.append(Check(
                "statement_history", sample_label, WARN,
                "These consumers are off: " + ", ".join(samples_off) + ". Deep analysis "
                "uses recent statements to pick realistic parameter values.",
                blocking=False, recommended=True,
                fix=fix("statement_history"),
                problem="Recent statements aren't kept for analysis right now",
            ))
        else:
            checks.append(Check("statement_history", sample_label, OK,
                                "Statement history consumers are on.",
                                blocking=False, recommended=True))

    size_label = "Long queries are captured in full"
    try:
        size = int(_one(conn, "SELECT @@GLOBAL.performance_schema_max_digest_length") or 0)
        if size >= DB_QUERY_SIZE_WARN_THRESHOLD:
            checks.append(Check("query_text_size", size_label, OK,
                                f"performance_schema_max_digest_length is {size}.",
                                blocking=False, recommended=True))
        elif size:
            checks.append(Check(
                "query_text_size", size_label, WARN,
                f"performance_schema_max_digest_length is {size}, so longer statements are "
                f"cut off. {DB_QUERY_SIZE_WARN_THRESHOLD} or more is recommended.",
                blocking=False, recommended=True,
                fix=fix("query_text_size"), problem=LONG_QUERIES_PROBLEM,
            ))
    except Exception as exc:
        logger.debug("digest length unreadable: %s", exc)

    probe_label = "RDST sees new statements as they run"
    consumers_ok = bool(enabled) and not ctx.extras.get("consumers_off")
    if not (ps_on and readable and consumers_ok):
        checks.append(Check("visibility_probe", probe_label, WAITING,
                            "Runs once the checks above pass."))
        return checks
    try:
        before = _probe_calls_mysql(conn)
        seen = _visibility_probe(before, lambda: _probe_calls_mysql(conn), open_probe,
                                 MYSQL_PROBE_SQL, sleep)
    except Exception as exc:
        checks.append(Check("visibility_probe", probe_label, UNKNOWN,
                            f"The probe couldn't run: {str(exc).strip() or 'error'}"))
        return checks
    if seen:
        checks.append(Check("visibility_probe", probe_label, OK,
                            "A test statement from a second session appeared in "
                            "performance_schema."))
    else:
        checks.append(Check(
            "visibility_probe", probe_label, MISSING,
            "A test statement from a second session didn't appear in the statement digest "
            "table. Statement instruments may be disabled.",
            fix=fix("statement_instruments"),
        ))
    return checks


# Entry point ---------------------------------------------------------------


def summarize(checks: list[Check]) -> dict[str, Any]:
    """`ready`: monitoring can start. `attention`: it can, but some SQL stays hidden."""
    blocking_failed = [c for c in checks if c.blocking and c.status != OK]
    attention = [c for c in checks if not c.blocking and c.status == MISSING]
    warnings = [c for c in checks if not c.blocking and c.status in (WARN, UNKNOWN)]
    return {
        "ready": bool(checks) and not blocking_failed,
        "blocking": len(blocking_failed),
        "attention": len(attention),
        "warnings": len(warnings),
    }


def check_target(
    target_name: str,
    target_config: dict[str, Any],
    *,
    connect: Optional[Callable[..., Any]] = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run every prerequisite check for one saved target."""
    from shared.db_connection import (
        close_connection,
        create_direct_connection,
        normalize_engine_name,
    )

    connect = connect or create_direct_connection
    engine = normalize_engine_name(str(target_config.get("engine") or ""))
    base = {
        "target": target_name,
        "engine": engine,
        "database": target_config.get("database"),
        "checked_at": int(clock()),
    }
    if engine not in ("postgresql", "mysql"):
        return {**base, "provider": None, "provider_name": None, "ready": False,
                "blocking": 0, "attention": 0, "warnings": 0, "checks": [],
                "connection_error": f"Unsupported engine: {engine or 'unknown'}"}

    def open_connection():
        return connect(target_config, connect_timeout=5, target=target_name, lane=LANE,
                       query_timeout_seconds=10)

    try:
        conn = open_connection()
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        return {**base, "provider": _host_provider(str(target_config.get("host") or "")),
                "provider_name": None, "ready": False, "blocking": 0, "attention": 0, "warnings": 0,
                "checks": [], "connection_error": redact(message)}
    try:
        if engine == "mysql" and hasattr(conn, "autocommit"):
            try:
                conn.autocommit(True)
            except TypeError:
                pass
        provider = detect_provider(conn, engine, target_config)
        ctx = Context(
            engine=engine,
            provider=provider,
            user=str(target_config.get("user") or "the RDST user"),
            database=str(target_config.get("database") or "this database"),
        )
        runner = check_postgres if engine == "postgresql" else check_mysql
        checks = runner(conn, ctx, open_connection, sleep=sleep)
    except Exception as exc:
        logger.warning("prerequisite check failed for %s: %s", target_name, exc)
        return {**base, "provider": None, "provider_name": None, "ready": False,
                "blocking": 0, "attention": 0, "warnings": 0, "checks": [],
                "connection_error": redact(str(exc).strip() or type(exc).__name__)}
    finally:
        close_connection(conn)
    return {
        **base,
        "provider": provider,
        "provider_name": provider_name(provider, engine),
        **summarize(checks),
        "checks": [check.as_dict() for check in checks],
        "connection_error": None,
    }


_REDACT = re.compile(r"password=\S+", re.IGNORECASE)


def redact(message: str) -> str:
    return _REDACT.sub("password=***", message)
