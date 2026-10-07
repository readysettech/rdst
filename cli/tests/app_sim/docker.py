"""The two app-sim containers, their users and connections.

Only these two containers and their two named volumes are ever created or removed here.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

PG_IMAGE = "postgres:16-alpine"
MYSQL_IMAGE = "mysql:8.0"
DATABASE = "shop"
ADMIN_PASSWORD = "appsim_admin"
APP_USER, APP_PASSWORD = "shop_app", "shop_app_pw"
MONITOR_USER, MONITOR_PASSWORD = "rdst_monitor", "rdst_monitor_pw"
MIN_FREE_GB = 12.0


@dataclass(frozen=True)
class Engine:
    name: str
    container: str
    volume: str
    port: int
    admin_user: str


PG = Engine("postgresql", "rdst-appsim-pg", "rdst-appsim-pg-data", 15490, "postgres")
MYSQL = Engine("mysql", "rdst-appsim-mysql", "rdst-appsim-mysql-data", 13330, "root")
ENGINES = {"pg": PG, "postgresql": PG, "mysql": MYSQL}

# Settings stay at engine defaults unless noted. work_mem stays at its 4MB default (P9).
PG_ARGS = (
    "-c", "shared_preload_libraries=pg_stat_statements",
    "-c", "pg_stat_statements.track=top",
    "-c", "pg_stat_statements.max=5000",
    "-c", "track_io_timing=on",
    "-c", "work_mem=4MB",
    "-c", "log_temp_files=0",
    # Disk is tight: keep WAL small and skip WAL for the bulk seed.
    "-c", "wal_level=minimal",
    "-c", "max_wal_senders=0",
    "-c", "max_wal_size=256MB",
)
MYSQL_ARGS = (
    "--performance-schema=ON",
    "--performance-schema-consumer-events-statements-history-long=ON",
    "--performance-schema-max-digest-length=4096",
    "--max-digest-length=4096",
    "--innodb-buffer-pool-size=128M",
    # P9: small in-memory temp tables, so the top-customers report goes to disk.
    "--tmp-table-size=1M",
    "--max-heap-table-size=1M",
    "--disable-log-bin",
    "--innodb-redo-log-capacity=104857600",
    "--skip-name-resolve",
)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL)


def free_gb(path: str = "/mnt/c") -> float:
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return shutil.disk_usage("/").free / 1e9


def state(engine: Engine) -> str:
    result = docker("inspect", "-f", "{{.State.Status}}", engine.container, check=False)
    return result.stdout.strip() if result.returncode == 0 else "absent"


def start(engine: Engine) -> None:
    current = state(engine)
    if current == "running":
        return
    if current != "absent":
        docker("start", engine.container)
        return
    if engine is PG:
        docker("run", "-d", "--name", PG.container, "-p", f"{PG.port}:5432",
               "--memory", "768m", "--shm-size", "256m",
               "-e", f"POSTGRES_PASSWORD={ADMIN_PASSWORD}", "-e", f"POSTGRES_DB={DATABASE}",
               "-v", f"{PG.volume}:/var/lib/postgresql/data", PG_IMAGE, "postgres", *PG_ARGS)
    else:
        docker("run", "-d", "--name", MYSQL.container, "-p", f"{MYSQL.port}:3306",
               "--memory", "1g", "-e", f"MYSQL_ROOT_PASSWORD={ADMIN_PASSWORD}",
               "-e", f"MYSQL_DATABASE={DATABASE}",
               "-v", f"{MYSQL.volume}:/var/lib/mysql", MYSQL_IMAGE, *MYSQL_ARGS)


def remove(engine: Engine) -> None:
    if state(engine) != "absent":
        docker("rm", "-f", "-v", engine.container)
    docker("volume", "rm", engine.volume, check=False)


def connect(engine: Engine, user: str | None = None, password: str | None = None,
            database: str = DATABASE, autocommit: bool = False) -> Any:
    user = user or APP_USER
    password = password or (APP_PASSWORD if user == APP_USER else
                            MONITOR_PASSWORD if user == MONITOR_USER else ADMIN_PASSWORD)
    if engine is PG:
        import psycopg2

        conn = psycopg2.connect(host="127.0.0.1", port=PG.port, user=user, password=password,
                                dbname=database, connect_timeout=5,
                                application_name="appsim-shop")
        conn.autocommit = autocommit
        return conn
    import pymysql

    return pymysql.connect(host="127.0.0.1", port=MYSQL.port, user=user, password=password,
                           database=database, connect_timeout=5, autocommit=autocommit,
                           program_name="appsim-shop")


def wait_ready(engine: Engine, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            conn = connect(engine, engine.admin_user, ADMIN_PASSWORD, autocommit=True)
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            conn.close()
            return
        except Exception as exc:
            last = exc
            time.sleep(2)
    raise RuntimeError(f"{engine.container} not ready after {timeout:.0f}s: {last}")


def provision(engine: Engine) -> None:
    """Create the app owner and the read-only monitor user. Safe to rerun."""
    conn = connect(engine, engine.admin_user, ADMIN_PASSWORD, autocommit=True)
    with conn.cursor() as cursor:
        if engine is PG:
            for user, password in ((APP_USER, APP_PASSWORD), (MONITOR_USER, MONITOR_PASSWORD)):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,))
                if not cursor.fetchone():
                    cursor.execute(f"CREATE ROLE {user} LOGIN PASSWORD %s", (password,))
            statements = (
                "CREATE EXTENSION IF NOT EXISTS pg_stat_statements",
                f"ALTER DATABASE {DATABASE} OWNER TO {APP_USER}",
                f"ALTER SCHEMA public OWNER TO {APP_USER}",
                # Monitor: statistics of every user, plus SELECT for EXPLAIN and introspection.
                f"GRANT pg_read_all_stats TO {MONITOR_USER}",
                f"GRANT CONNECT ON DATABASE {DATABASE} TO {MONITOR_USER}",
                f"GRANT USAGE ON SCHEMA public TO {MONITOR_USER}",
                f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {MONITOR_USER}",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {APP_USER} IN SCHEMA public "
                f"GRANT SELECT ON TABLES TO {MONITOR_USER}",
            )
        else:
            statements = (
                f"CREATE USER IF NOT EXISTS '{APP_USER}'@'%' IDENTIFIED BY '{APP_PASSWORD}'",
                f"GRANT ALL PRIVILEGES ON {DATABASE}.* TO '{APP_USER}'@'%'",
                f"CREATE USER IF NOT EXISTS '{MONITOR_USER}'@'%' "
                f"IDENTIFIED BY '{MONITOR_PASSWORD}'",
                # Monitor: processlist and InnoDB metrics, statement and table statistics, index
                # statistics, and SELECT on the app schema for EXPLAIN and introspection.
                f"GRANT PROCESS ON *.* TO '{MONITOR_USER}'@'%'",
                f"GRANT SELECT ON performance_schema.* TO '{MONITOR_USER}'@'%'",
                f"GRANT SELECT ON {DATABASE}.* TO '{MONITOR_USER}'@'%'",
                f"GRANT SELECT ON mysql.innodb_index_stats TO '{MONITOR_USER}'@'%'",
                f"GRANT SELECT ON mysql.innodb_table_stats TO '{MONITOR_USER}'@'%'",
            )
        for statement in statements:
            cursor.execute(statement)
    conn.close()


def reset_stats(engine: Engine) -> None:
    """Zero statement, table and index counters so a run starts from a clean window."""
    conn = connect(engine, engine.admin_user, ADMIN_PASSWORD, autocommit=True)
    with conn.cursor() as cursor:
        if engine is PG:
            cursor.execute("SELECT pg_stat_statements_reset()")
            cursor.execute("SELECT pg_stat_reset()")
        else:
            for table in ("events_statements_summary_by_digest", "events_statements_history_long",
                          "table_io_waits_summary_by_table",
                          "table_io_waits_summary_by_index_usage"):
                cursor.execute(f"TRUNCATE TABLE performance_schema.{table}")
    conn.close()


def db_size_mb(engine: Engine) -> float:
    conn = connect(engine, engine.admin_user, ADMIN_PASSWORD, autocommit=True)
    with conn.cursor() as cursor:
        if engine is PG:
            cursor.execute("SELECT pg_database_size(%s)", (DATABASE,))
        else:
            cursor.execute("SELECT COALESCE(SUM(data_length + index_length), 0) FROM "
                           "information_schema.tables WHERE table_schema = %s", (DATABASE,))
        size = float(cursor.fetchone()[0] or 0)
    conn.close()
    return size / 1e6


def connection_info(engine: Engine) -> dict[str, Any]:
    return {"engine": engine.name, "container": engine.container, "host": "127.0.0.1",
            "port": engine.port, "database": DATABASE,
            "app": f"{APP_USER} / {APP_PASSWORD}",
            "monitor": f"{MONITOR_USER} / {MONITOR_PASSWORD}",
            "admin": f"{engine.admin_user} / {ADMIN_PASSWORD}"}
