"""
Direct database connection helper for RDST.

Handles creating direct psycopg2 or pymysql connections
without using DataManager infrastructure.
"""

import ctypes
import ctypes.util
import functools
import glob
import logging
import math
import os
import select
import socket
import sqlite3
import threading
import time
from contextlib import AbstractContextManager
from typing import Dict, Any, Callable, Iterable, List, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

# Lane tag applied to every RDST-initiated connection so activity views can
# recognize RDST's own sessions (PostgreSQL application_name, MySQL program_name
# connection attribute). Features pass a specific lane such as "rdst/ask" or
# "rdst/compare"; untagged callers fall back to this default.
DEFAULT_LANE = "rdst/unknown"

# Bounds a PostgreSQL cancel: libpq's protocol cancel request, or each step of the side
# connection that carries a pg_cancel_backend.
POSTGRES_CANCEL_SECONDS = 3


def resolve_application_name(
    configured: Optional[str], lane: Optional[str] = None
) -> str:
    """Resolve the effective application name for an RDST-initiated connection.

    Precedence: a user-configured target application_name always wins, then
    the caller's lane tag, then DEFAULT_LANE. Accepted caveat of
    custom-name-wins: sessions carrying a custom name do not match the
    rdst/% exclusion in activity views, so RDST's own queries can surface in
    activity output for such targets.
    """
    return configured or lane or DEFAULT_LANE


def rdst_self_marker(lane: str) -> str:
    """Leading SQL comment identifying an RDST-issued statement.

    Prepended to statements RDST runs against user relations so statement
    stores keep the tag (pg_stat_statements records first-seen text, MySQL
    keeps QUERY_SAMPLE_TEXT) and registry admission can exclude RDST's own
    traffic by its "/*rdst" prefix. The lane's slash becomes a colon inside
    the comment: "rdst/profile" tags statements as "/*rdst:profile*/ ".
    """
    return "/*" + lane.replace("/", ":", 1) + "*/ "


def normalize_engine_name(engine: str) -> str:
    """Normalize engine aliases to the canonical names used by RDST."""
    value = (engine or "").lower()
    if value in ("postgres", "psql"):
        return "postgresql"
    return value


def quote_identifier(name: str, engine: str = "postgresql") -> str:
    """
    Quote a SQL identifier (table, column, or schema name) for the given engine.

    Identifiers cannot be bound as query parameters, so any name that reaches a
    query string has to be quoted for the dialect. MySQL uses backticks, every
    other supported engine uses the SQL-standard double quote; in both cases an
    embedded quote character is escaped by doubling it.

    Args:
        name: Identifier to quote
        engine: Engine name or alias (see normalize_engine_name)

    Returns:
        The identifier wrapped in the dialect's quote character

    Raises:
        ValueError: If name is empty or contains a NUL byte
    """
    if not name:
        raise ValueError("SQL identifier must be a non-empty string")
    if "\x00" in name:
        raise ValueError("SQL identifier must not contain a NUL byte")

    if normalize_engine_name(engine) == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return '"' + name.replace('"', '""') + '"'


def resolve_connection_params(
    target: Optional[str] = None,
    target_config: Optional[Dict[str, Any]] = None,
    *,
    force_fresh_tunnel: bool = False,
    lane: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve all connection parameters from target name or configuration.

    Handles password resolution from environment variables and TLS settings.

    Args:
        target: Target name (loads config from TargetsConfig)
        target_config: Target configuration dict (alternative to target name)
        lane: RDST lane tag (e.g. "rdst/ask") used as the connection's
            application name unless the target config sets its own

    Returns:
        Dict with resolved connection parameters:
            - engine: 'postgresql' or 'mysql'
            - host: TLS identity/database host (real host for verified tunnels)
            - hostaddr: Socket endpoint for verified tunnels, when applicable
            - port: Database port
            - user: Database username
            - password: Resolved password (from env var or direct)
            - database: Database name
            - tls: Boolean TLS flag
            - sslmode: PostgreSQL SSL mode ('require' if tls, else 'prefer')
            - read_only: Boolean read-only flag
            - application_name: Self-identification tag (lane or user override)

    Raises:
        ValueError: If neither target nor target_config provided, or target not found
    """
    # Load config from target name if not provided directly
    if target_config is None:
        if target is None:
            raise ValueError("Either target name or target_config must be provided")
        from shared.config.targets import TargetsConfig
        cfg = TargetsConfig()
        cfg.load()
        target_config = cfg.get(target)
        if target_config is None:
            raise ValueError(f"Target '{target}' not found in configuration")

    engine = normalize_engine_name(target_config.get('engine', 'postgresql'))
    host = target_config.get('host', 'localhost')
    port = target_config.get('port', 5432 if 'postgres' in engine else 3306)
    if "ssh" in target_config:
        from shared.ssh_tunnel import get_tunnel_manager

        real_host = host
        real_port = port
        tunnel_target = target or target_config.get('name') or host
        ensure_kwargs = {"force_fresh": True} if force_fresh_tunnel else {}
        manager = get_tunnel_manager()
        if (lane == "rdst/observe" and manager.failure_reason(str(tunnel_target))
                and not _tunnel_retry_monitored(str(tunnel_target))):
            manager.raise_if_failed(str(tunnel_target))
        host, port = manager.ensure_tunnel(
            str(tunnel_target),
            target_config['ssh'],
            str(real_host),
            int(real_port),
            **ensure_kwargs,
        )
    user = target_config.get('user') or target_config.get('username')
    database = target_config.get('database') or target_config.get('dbname')
    tls = target_config.get('tls', False)
    tls_verify = target_config.get('tls_verify', False)
    tls_ca = target_config.get('tls_ca')
    if tls_verify:
        tls = True
    hostaddr = target_config.get('hostaddr')
    if "ssh" in target_config and tls_verify:
        # Keep the database hostname as the TLS identity while directing the
        # socket to the local tunnel endpoint. libpq consumes hostaddr
        # directly; the PyMySQL helper below uses it to preconnect a socket.
        hostaddr = host
        host = real_host
    read_only = target_config.get('read_only', False)

    # Resolve password: config field -> env var -> keyring
    from shared.password_resolver import resolve_password_value

    password_env = target_config.get('password_env')
    password = resolve_password_value(target_config)

    # Determine SSL mode for PostgreSQL
    sslmode = 'verify-full' if tls_verify else ('require' if tls else 'prefer')

    application_name = resolve_application_name(
        target_config.get('application_name'), lane
    )

    result = {
        'engine': engine,
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'database': database,
        'tls': tls,
        'sslmode': sslmode,
        'tls_verify': tls_verify,
        'tls_ca': tls_ca,
        'read_only': read_only,
        'application_name': application_name,
        'password_env': password_env,  # Keep for error messages
    }
    if "ssh" in target_config:
        result['real_host'] = real_host
        result['real_port'] = real_port
    if hostaddr is not None:
        result['hostaddr'] = hostaddr
    return result


def _tunnel_retry_monitored(target: str) -> bool:
    """Read monitoring eligibility without initializing or migrating its store."""
    from shared.monitor.monitor_store import default_monitor_db_path

    path = default_monitor_db_path()
    if not path.exists():
        return False
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            return connection.execute(
                "SELECT 1 FROM monitor_setting WHERE target=? AND key='enabled' AND value='1'",
                (target,),
            ).fetchone() is not None
        finally:
            connection.close()
    except sqlite3.Error:
        logger.debug("Could not read tunnel retry eligibility for %s", target, exc_info=True)
        return False


def create_direct_connection(
    target_config: Dict[str, Any],
    connect_timeout: int = 10,
    *,
    target: Optional[str] = None,
    force_fresh_tunnel: bool = False,
    lane: Optional[str] = None,
    query_timeout_seconds: float | None = None,
):
    """
    Create a direct database connection from target configuration.

    Args:
        target_config: Target configuration dict with keys:
            - engine: 'postgresql' or 'mysql'
            - host: Database host
            - port: Database port
            - user: Database username
            - database: Database name
            - password_env: Environment variable containing password
            - tls: Enable TLS/SSL (optional, default False)

    Returns:
        Database connection object (psycopg2 or pymysql connection)

    Raises:
        ValueError: If engine is unsupported or config is invalid
        RuntimeError: If connection fails
    """
    params = resolve_connection_params(
        target=target,
        target_config=target_config,
        force_fresh_tunnel=force_fresh_tunnel,
        lane=lane,
    )
    engine = params['engine']
    host = params['host']
    port = params['port']
    user = params['user']
    database = params['database']
    password_env = params['password_env']
    use_tls = params['tls']
    tls_verify = params['tls_verify']
    tls_ca = params['tls_ca']

    # Validate required fields
    if not all([engine, host, port, user, database]):
        raise ValueError("Missing required connection parameters in target config")

    # Resolve password: config field -> env var -> keyring.
    # Only reject when a password_env is configured but unresolved; some targets
    # intentionally use passwordless auth and should pass an empty string through.
    password = params['password']
    if password == "" and password_env:
        raise ValueError("Password not available for target")

    try:
        if engine == 'postgresql':
            conn = _create_postgres_connection(
                host, port, user, password, database, use_tls,
                tls_verify=tls_verify, tls_ca=tls_ca,
                hostaddr=params.get('hostaddr'), connect_timeout=connect_timeout,
                application_name=params['application_name'],
                query_timeout_seconds=query_timeout_seconds,
            )
        elif engine == 'mysql':
            query_timeouts = (
                {}
                if query_timeout_seconds is None
                else {
                    'read_timeout': query_timeout_seconds,
                    'write_timeout': query_timeout_seconds,
                }
            )
            conn = _create_mysql_connection(
                host, port, user, password, database, use_tls,
                tls_verify=tls_verify, tls_ca=tls_ca,
                hostaddr=params.get('hostaddr'), connect_timeout=connect_timeout,
                program_name=params['application_name'],
                **query_timeouts,
            )
        else:
            raise ValueError(f"Unsupported database engine: {engine}")
    except Exception:
        if "ssh" in target_config:
            from shared.ssh_tunnel import get_tunnel_manager

            tunnel_target = str(target or target_config.get('name') or target_config.get('host', 'localhost'))
            get_tunnel_manager().raise_if_failed(tunnel_target)
        raise

    if params.get('read_only'):
        apply_read_only_session(conn, engine)
    if lane == 'rdst/audit':
        apply_diagnostic_statement_timeout(conn, engine)
    return conn


def apply_diagnostic_statement_timeout(connection, engine: str, seconds: int = 30) -> None:
    """Cap per-statement runtime on diagnostic (audit) sessions.

    Audit collection issues many small catalog queries with no user watching
    each one; a single stalled statement must fail the phase visibly instead
    of hanging the run for minutes. Best-effort — a session that rejects the
    setting still gets to run.
    """
    try:
        cursor = connection.cursor()
        if engine == 'postgresql':
            cursor.execute(f"SET statement_timeout = '{int(seconds)}s'")
        elif engine == 'mysql':
            # Applies to SELECTs only, which is what diagnostics issue.
            cursor.execute(f"SET SESSION max_execution_time = {int(seconds) * 1000}")
        cursor.close()
    except Exception as exc:
        logger.debug("Could not set diagnostic statement timeout: %s", exc)


def probe_readyset_status(
    target_config: Dict[str, Any], timeout_seconds: float = 3
) -> List[Any]:
    """Run a Readyset status query with a client-enforced I/O deadline.

    Returns the status rows so callers can read fields such as the snapshot
    state; a caller that only needs liveness can ignore them.
    """
    params = resolve_connection_params(
        target_config=target_config,
        lane="rdst/sandbox-readiness",
    )
    timeout_seconds = max(float(timeout_seconds), 0.001)
    if params['engine'] == 'postgresql':
        return _probe_postgres_readyset_status(params, timeout_seconds)

    connection = create_direct_connection(
        target_config,
        connect_timeout=timeout_seconds,
        lane="rdst/sandbox-readiness",
        query_timeout_seconds=timeout_seconds,
    )
    try:
        cursor = connection.cursor()
        try:
            cursor.execute("SHOW READYSET STATUS")
            return list(cursor.fetchall())
        finally:
            cursor.close()
    finally:
        connection.close()


def _probe_postgres_readyset_status(
    params: Dict[str, Any], timeout_seconds: float
) -> List[Any]:
    """Use libpq's nonblocking mode so a silent server cannot hang readiness."""
    try:
        import psycopg2
        from psycopg2 import extensions
    except ImportError:
        raise RuntimeError(
            "psycopg2-binary not installed. Run: pip install psycopg2-binary"
        )

    deadline = time.monotonic() + timeout_seconds
    timeout_ms = max(1, int(timeout_seconds * 1000))
    connection = psycopg2.connect(
        **postgres_connection_kwargs(
            params,
            connect_timeout=max(1, int(timeout_seconds)),
            options=f'-c statement_timeout={timeout_ms}',
            async_=True,
        )
    )
    try:
        _wait_for_postgres_io(connection, deadline, extensions)
        cursor = connection.cursor()
        try:
            cursor.execute("SHOW READYSET STATUS")
            _wait_for_postgres_io(connection, deadline, extensions)
            return list(cursor.fetchall())
        finally:
            cursor.close()
    finally:
        connection.close()


def _wait_for_postgres_io(connection, deadline: float, extensions) -> None:
    while True:
        state = connection.poll()
        if state == extensions.POLL_OK:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Readyset PostgreSQL readiness probe timed out")
        descriptor = connection.fileno()
        if state == extensions.POLL_READ:
            ready = select.select([descriptor], [], [], remaining)
        elif state == extensions.POLL_WRITE:
            ready = select.select([], [descriptor], [], remaining)
        else:
            raise RuntimeError(
                f"Unexpected PostgreSQL readiness poll state: {state}"
            )
        if not any(ready):
            raise TimeoutError("Readyset PostgreSQL readiness probe timed out")


def apply_read_only_session(connection, engine: str) -> None:
    """Put the session into read-only mode for a target marked read_only.

    This is the only control here the database itself enforces; everything
    else inspects SQL as text and can be argued with. A target the user has
    declared read-only should refuse writes even if something upstream builds
    a statement nobody intended.
    """
    if engine == 'postgresql':
        # Native psycopg2 API; must run before any transaction is open, which
        # is the case on a connection that has just been handed back.
        connection.set_session(readonly=True)
    elif engine == 'mysql':
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")


def postgres_ssl_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return psycopg2 TLS kwargs without changing legacy encryption behavior."""
    result = {'sslmode': params.get('sslmode', 'prefer')}
    if params.get('hostaddr'):
        result['hostaddr'] = params['hostaddr']
    if params.get('tls_verify') and params.get('tls_ca'):
        result['sslrootcert'] = params['tls_ca']
    return result


def mysql_ssl_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return PyMySQL TLS kwargs with optional certificate verification."""
    if not params.get('tls'):
        return {'ssl': None}
    if not params.get('tls_verify'):
        return {'ssl': {'ssl': {}}}

    import ssl

    ssl_context = ssl.create_default_context(cafile=params.get('tls_ca'))
    # Match PyMySQL's compatibility behavior on Python 3.13 while retaining
    # certificate-chain and hostname verification.
    ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return {'ssl': ssl_context}


def postgres_connection_kwargs(
    params: Dict[str, Any],
    *,
    connect_timeout: int = 10,
    **overrides: Any,
) -> Dict[str, Any]:
    """Build complete psycopg2 kwargs from resolved connection parameters."""
    result = {
        'host': params['host'],
        'port': params['port'],
        'user': params['user'],
        'password': params['password'],
        'database': params['database'],
        'connect_timeout': connect_timeout,
        'application_name': params.get('application_name') or DEFAULT_LANE,
        **postgres_ssl_kwargs(params),
    }
    result.update(overrides)
    from shared.agent_activity import pg_connection_kwargs

    return pg_connection_kwargs(result)


def create_mysql_connection_from_params(
    params: Dict[str, Any],
    *,
    connect_timeout: int = 10,
    **overrides: Any,
):
    """Connect with PyMySQL, separating a tunnel socket from the TLS identity."""
    overrides.setdefault(
        'program_name', params.get('application_name') or DEFAULT_LANE
    )
    return _create_mysql_connection(
        params['host'],
        params['port'],
        params['user'],
        params['password'],
        params['database'],
        params.get('tls', False),
        tls_verify=params.get('tls_verify', False),
        tls_ca=params.get('tls_ca'),
        hostaddr=params.get('hostaddr'),
        connect_timeout=connect_timeout,
        connection_defaults=False,
        **overrides,
    )


def _create_postgres_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    use_tls: bool = False,
    tls_verify: bool = False,
    tls_ca: Optional[str] = None,
    hostaddr: Optional[str] = None,
    connect_timeout: int = 10,
    application_name: Optional[str] = None,
    query_timeout_seconds: float | None = None,
):
    """Create PostgreSQL connection using psycopg2."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise RuntimeError("psycopg2-binary not installed. Run: pip install psycopg2-binary")

    try:
        conn_params = {
            'host': host,
            'port': port,
            'user': user,
            'password': password,
            'database': database,
            'connect_timeout': connect_timeout,
            'application_name': application_name or DEFAULT_LANE,
        }
        if query_timeout_seconds is not None:
            timeout_ms = max(1, int(query_timeout_seconds * 1000))
            conn_params['options'] = f'-c statement_timeout={timeout_ms}'

        conn_params.update(
            postgres_ssl_kwargs(
                {
                    'sslmode': 'verify-full' if tls_verify else ('require' if use_tls else 'prefer'),
                    'tls_verify': tls_verify,
                    'tls_ca': tls_ca,
                    'hostaddr': hostaddr,
                }
            )
        )

        from shared.agent_activity import pg_connection_kwargs

        try:
            conn = psycopg2.connect(**pg_connection_kwargs(conn_params))
        except psycopg2.OperationalError as e:
            # Connection poolers (Neon, Supavisor, PgBouncer) reject startup options. A
            # session SET would outlive this client on a transaction-mode pooler and cap the
            # application's own queries, so pooled connections run without the timeout.
            if 'options' not in conn_params or 'unsupported startup parameter' not in str(e):
                raise
            conn_params.pop('options')
            conn = psycopg2.connect(**pg_connection_kwargs(conn_params))
        # Set autocommit for read-only queries
        conn.autocommit = True
        return conn
    except Exception as e:
        raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")


def _create_mysql_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    use_tls: bool = False,
    tls_verify: bool = False,
    tls_ca: Optional[str] = None,
    hostaddr: Optional[str] = None,
    connect_timeout: int = 10,
    connection_defaults: bool = True,
    **overrides: Any,
):
    """Create MySQL connection using pymysql."""
    try:
        import pymysql
        import pymysql.cursors
    except ImportError:
        raise RuntimeError("pymysql not installed. Run: pip install pymysql")
    from shared.agent_activity import instrument_mysql

    try:
        conn_params = {
            'host': host,
            'port': int(port),
            'user': user,
            'password': password,
            'database': database,
            'connect_timeout': connect_timeout,
        }
        if connection_defaults:
            conn_params.update(
                {
                    'autocommit': True,
                    'cursorclass': pymysql.cursors.DictCursor,
                }
            )
        conn_params.update(overrides)
        # Sent to the server as the program_name connection attribute.
        conn_params.setdefault('program_name', DEFAULT_LANE)

        if tls_verify:
            conn_params.update(
                mysql_ssl_kwargs(
                    {'tls': True, 'tls_verify': True, 'tls_ca': tls_ca}
                )
            )
        elif use_tls:
            import ssl
            # Require encryption but don't verify certificate (matches psycopg2 sslmode='require')
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            conn_params['ssl'] = ssl_context

        if hostaddr:
            raw_socket = None
            try:
                raw_socket = socket.create_connection(
                    (hostaddr, int(port)), connect_timeout
                )
                raw_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                conn = pymysql.connect(defer_connect=True, **conn_params)
                conn.connect(sock=raw_socket)
                return instrument_mysql(conn)
            except Exception:
                if raw_socket is not None:
                    raw_socket.close()
                raise

        return instrument_mysql(pymysql.connect(**conn_params))
    except Exception as e:
        raise RuntimeError(f"Failed to connect to MySQL: {e}")


def parse_pg_size(value: str) -> int:
    """Parse PostgreSQL size string like '1kB', '8kB', '1MB' to bytes."""
    value = value.strip()
    upper = value.upper()
    if upper.endswith("KB"):
        return int(value[:-2]) * 1024
    elif upper.endswith("MB"):
        return int(value[:-2]) * 1024 * 1024
    elif upper.endswith("GB"):
        return int(value[:-2]) * 1024 * 1024 * 1024
    return int(value)


def format_db_limit_command(setting_name: str, recommended_bytes: int, db_engine: str) -> str:
    """Format the SQL command to fix a low query size limit setting."""
    if "postgres" in db_engine:
        return (
            f"ALTER SYSTEM SET {setting_name} = "
            f"{recommended_bytes};  -- then restart PostgreSQL"
        )
    return f"SET GLOBAL {setting_name} = {recommended_bytes};"


def query_db_query_size_limit(
    connection, db_engine: str, mode: Literal["realtime", "historical"] = "realtime"
) -> Optional[Tuple[int, str]]:
    """Query the database's query text size limit.

    Returns (limit_bytes, setting_name) or None if not applicable / cannot be determined.

    Args:
        connection: Database connection object
        db_engine: 'postgresql' or 'mysql'
        mode: 'realtime' or 'historical' — determines which settings matter:
            - realtime: PG track_activity_query_size truncates pg_stat_activity.
              MySQL PROCESSLIST.INFO is unlimited — returns None.
            - historical: MySQL performance_schema_max_digest_length truncates digest tables.
              PG pg_stat_statements stores full text (no limit since PG 9.4) — returns None.
    """
    is_pg = "postgres" in db_engine
    is_mysql = "mysql" in db_engine

    # Only check settings that actually cause truncation for this mode
    if mode == "realtime" and not is_pg:
        return None
    if mode == "historical" and not is_mysql:
        return None

    cursor = connection.cursor()
    try:
        if is_pg:
            cursor.execute("SHOW track_activity_query_size")
            row = cursor.fetchone()
            if row:
                return (parse_pg_size(str(row[0])), "track_activity_query_size")
            return None
        elif is_mysql:
            cursor.execute("SELECT @@performance_schema_max_digest_length")
            row = cursor.fetchone()
            return (int(row[0]), "performance_schema_max_digest_length") if row else None
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        return None
    finally:
        cursor.close()
    return None


def close_connection(connection):
    """Safely close a database connection."""
    try:
        if connection:
            connection.close()
    except Exception:
        pass  # Ignore errors during close


class ServerNotAnsweringError(TimeoutError):
    """The database accepted the connection but stopped answering."""


def abort_connection(connection) -> None:
    """Shut down the socket under a psycopg2 or PyMySQL connection, from any thread.

    A driver call blocked in a read on that socket then fails promptly. This never waits:
    psycopg2's close() takes the lock a blocked call holds, and server-side cancellation needs
    a server that answers. The connection is unusable afterwards; close it as usual.
    """
    try:
        # psycopg2 exposes libpq's socket; PyMySQL keeps its socket on _sock.
        raw = getattr(connection, '_sock', None)
        descriptor = raw.fileno() if raw is not None else connection.fileno()
        # A duplicate descriptor shuts down the same socket and leaves the driver's own open.
        sock = socket.fromfd(descriptor, socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        finally:
            sock.close()
    except Exception:
        logger.debug("Could not abort a database connection", exc_info=True)


class IoDeadline:
    """Client-side bound on a block of database calls.

    Server-side timeouts can't end a call blocked on a server that stopped answering (paused,
    wedged), and TCP keepalives don't fire while its kernel still acknowledges. When the block
    runs ``seconds`` past entry or the last renew(), the connection is aborted so the blocked
    call raises, and the block raises ServerNotAnsweringError even if it swallowed that error.
    """

    def __init__(self, connection, seconds: float) -> None:
        self._connection = connection
        self.seconds = seconds
        self.expired = False
        self._lock = threading.Lock()
        self._generation = 0
        self._timer: Optional[threading.Timer] = None

    def renew(self) -> None:
        with self._lock:
            self._arm(active=True)

    def _arm(self, *, active: bool) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._generation += 1
        if active and not self.expired:
            self._timer = threading.Timer(self.seconds, self._expire, (self._generation,))
            self._timer.daemon = True
            self._timer.start()

    def _expire(self, generation: int) -> None:
        # Aborting under the lock keeps __exit__, and so the caller's close, from finishing
        # while the abort still holds the connection's descriptor; the abort never waits.
        with self._lock:
            if generation != self._generation:
                return
            self.expired = True
            abort_connection(self._connection)

    def __enter__(self) -> "IoDeadline":
        self.renew()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        with self._lock:
            self._arm(active=False)
        if self.expired and (exc is None or isinstance(exc, Exception)):
            raise ServerNotAnsweringError(
                "The database accepted the connection but didn't answer within "
                f"{self.seconds:g} s."
            ) from exc
        return False


@functools.lru_cache(maxsize=1)
def _libpq() -> Optional[ctypes.CDLL]:
    """The libpq psycopg2 runs on, or None when it can't be loaded.

    PQgetCancel reads PGconn fields, so only a libpq of the version psycopg2 runs is used. On
    Linux and macOS the extension module's own handle resolves symbols through its dependencies,
    which finds the exact library it links; psycopg2-binary's bundled copy (Windows) and the
    system library are the fallbacks.
    """
    try:
        import psycopg2
        from psycopg2 import _psycopg, extensions
    except ImportError:
        return None
    package = os.path.dirname(psycopg2.__file__)
    candidates = [_psycopg.__file__]
    for pattern in (
        os.path.join(package, os.pardir, "psycopg2_binary.libs", "libpq*"),
        os.path.join(package, ".dylibs", "libpq*"),
        os.path.join(package, "libpq*.dll"),
    ):
        candidates += sorted(glob.glob(pattern))
    candidates += filter(None, (ctypes.util.find_library("pq"), ctypes.util.find_library("libpq")))
    expected = extensions.libpq_version()
    for path in candidates:
        try:
            # CDLL calls release the GIL for their duration.
            lib = ctypes.CDLL(path)
            lib.PQlibVersion.restype = ctypes.c_int
            if lib.PQlibVersion() != expected:
                continue
            lib.PQgetCancel.argtypes = [ctypes.c_void_p]
            lib.PQgetCancel.restype = ctypes.c_void_p
            lib.PQcancel.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            lib.PQcancel.restype = ctypes.c_int
            lib.PQfreeCancel.argtypes = [ctypes.c_void_p]
            lib.PQfreeCancel.restype = None
            return lib
        except (OSError, AttributeError):
            continue
    logger.debug("libpq could not be loaded; PostgreSQL cancels abort the connection")
    return None


class PostgresCancelRequest:
    """libpq's protocol cancel for the statement running on one psycopg2 connection.

    The request goes to the address the connection reached on a fresh socket and carries the
    backend key from the connection's startup, so a pooler routes it to the session it fronts
    and the server needs no free connection slot. psycopg2's conn.cancel() sends the same
    request but holds the GIL while waiting on the server, so a paused server would freeze the
    whole process; here PQcancel runs through ctypes, which releases the GIL, on a daemon thread.
    """

    def __init__(self, lib: ctypes.CDLL, handle: int) -> None:
        self._lib = lib
        self._handle: Optional[int] = handle

    @classmethod
    def for_connection(cls, conn) -> Optional["PostgresCancelRequest"]:
        """The request for ``conn``, or None when libpq or the connection is unavailable.

        The caller keeps ``conn`` open until this returns. PQgetCancel only copies fields set by
        the connection's startup and does no I/O, so it is safe while another thread runs a
        statement on ``conn``.
        """
        lib = _libpq()
        if lib is None or getattr(conn, "closed", True):
            return None
        pointer = getattr(conn, "pgconn_ptr", None)
        if not isinstance(pointer, int) or not pointer:
            return None
        handle = lib.PQgetCancel(pointer)
        return cls(lib, handle) if handle else None

    def send(self, seconds: Optional[float] = None) -> bool:
        """Dispatch the request once; True when the server took it within ``seconds``.

        The thread running PQcancel owns the PGcancel and frees it when PQcancel returns, so a
        request still waiting on a paused server keeps both rather than risk a use-after-free.
        """
        handle, self._handle = self._handle, None
        if handle is None:
            return False
        lib = self._lib
        taken = threading.Event()

        def run() -> None:
            errbuf = ctypes.create_string_buffer(256)
            try:
                if lib.PQcancel(handle, errbuf, len(errbuf)):
                    taken.set()
                else:
                    logger.debug("PostgreSQL cancel request failed: %s",
                                 errbuf.value.decode(errors="replace"))
            finally:
                lib.PQfreeCancel(handle)

        threading.Thread(target=run, name="postgres-cancel", daemon=True).start()
        return taken.wait(POSTGRES_CANCEL_SECONDS if seconds is None else seconds)

    def __del__(self) -> None:
        if self._handle is not None:
            self._lib.PQfreeCancel(self._handle)


def cancel_postgres_connection(conn, seconds: Optional[float] = None) -> bool:
    """Stop the statement running on a psycopg2 connection, from any thread, in bounded time.

    Sends libpq's protocol cancel; when that can't be sent or isn't taken within ``seconds``,
    aborts the connection so the statement blocked on it fails. Returns whether the server
    took the cancel.
    """
    request = PostgresCancelRequest.for_connection(conn)
    if request is not None and request.send(seconds):
        return True
    abort_connection(conn)
    return False


def cancel_query(conn, engine: str, target_config: Optional[dict] = None) -> bool:
    """
    Cancel a running query on the database server.

    PostgreSQL uses cancel_postgres_connection. MySQL runs KILL QUERY on a side connection.

    Args:
        conn: Active database connection with running query
        engine: 'postgresql' or 'mysql'
        target_config: Needed to create the cancel connection for MySQL

    Returns:
        True if cancel was sent, False otherwise
    """
    try:
        if engine == 'postgresql':
            return cancel_postgres_connection(conn)
        elif engine == 'mysql':
            # MySQL requires KILL QUERY via separate connection
            if not target_config:
                return False
            thread_id = conn.thread_id()
            params = resolve_connection_params(target_config=target_config)
            cancel_conn = _create_mysql_connection(
                params['host'],
                params['port'],
                params['user'],
                params['password'],
                params['database'],
                params['tls'],
                tls_verify=params['tls_verify'],
                tls_ca=params['tls_ca'],
                hostaddr=params.get('hostaddr'),
            )
            try:
                cursor = cancel_conn.cursor()
                cursor.execute(f"KILL QUERY {thread_id}")
                cursor.close()
                return True
            finally:
                cancel_conn.close()
    except Exception:
        return False
    return False


Registered = Tuple[Any, str, Optional[dict]]


def cancel_registered(lock: AbstractContextManager[Any], registered: Callable[[], Iterable[Registered]],
                      seconds: Optional[float] = None) -> int:
    """cancel_query for every (conn, engine, target_config) a registry lists, from any thread.

    Owners hold ``lock`` to unregister a connection before closing it. Each request is built
    under ``lock`` and sent outside it, so a connection that closes meanwhile is never touched;
    a PostgreSQL connection whose cancel isn't taken is aborted only while it's still listed.
    Returns how many cancels the server took within ``seconds``.
    """
    seconds = POSTGRES_CANCEL_SECONDS if seconds is None else seconds
    with lock:
        pending = [(conn, engine, _prepared_cancel(conn, engine, config))
                   for conn, engine, config in registered()]
    taken = [threading.Event() for _ in pending]

    def send(index: int, request: Callable[[], bool]) -> None:
        try:
            if request():
                taken[index].set()
        except Exception:
            logger.debug("Could not cancel a database statement", exc_info=True)

    for index, (_conn, _engine, request) in enumerate(pending):
        threading.Thread(target=send, args=(index, request), name="statement-cancel",
                         daemon=True).start()
    deadline = time.monotonic() + seconds
    unanswered = [conn for (conn, engine, _request), event in zip(pending, taken)
                  if not event.wait(max(0.0, deadline - time.monotonic()))
                  and engine == 'postgresql']
    if unanswered:
        with lock:
            listed = {id(conn) for conn, _engine, _config in registered()}
            for conn in unanswered:
                if id(conn) in listed:
                    abort_connection(conn)
    return sum(event.is_set() for event in taken)


def _prepared_cancel(conn, engine: str, target_config: Optional[dict]) -> Callable[[], bool]:
    """A cancel for the statement on ``conn`` that doesn't touch ``conn`` when sent."""
    try:
        if engine == 'postgresql':
            request = PostgresCancelRequest.for_connection(conn)
            if request is not None:
                return request.send
        elif engine == 'mysql' and target_config:
            thread_id = conn.thread_id()
            return lambda: cancel_mysql_by_thread_id(target_config, thread_id)
    except Exception:
        logger.debug("Could not prepare a database cancel", exc_info=True)
    return lambda: False


def _postgres_cancel_backend(
    conn_params: Dict[str, Any], backend_pid: int, seconds: Optional[float] = None
) -> bool:
    """pg_cancel_backend over a side connection bounded by ``seconds`` at every step.

    Raises when the server can't be reached or doesn't answer in time.
    """
    import psycopg2

    seconds = POSTGRES_CANCEL_SECONDS if seconds is None else seconds
    cancel_conn = psycopg2.connect(
        **{**conn_params, 'connect_timeout': max(1, math.ceil(seconds))}
    )
    try:
        with IoDeadline(cancel_conn, seconds), cancel_conn.cursor() as cursor:
            cursor.execute("SELECT pg_cancel_backend(%s)", (backend_pid,))
            row = cursor.fetchone()
        return bool(row and row[0])
    finally:
        cancel_conn.close()


def probe_target_connection(
    target_config: Dict[str, Any], connect_timeout: int = 3
) -> Dict[str, Any]:
    """Attempt a direct connection and return a small verification state."""
    engine = normalize_engine_name(target_config.get("engine", ""))
    host = target_config.get("host")
    port = int(target_config.get("port") or 0)
    database = target_config.get("database")

    if engine == "postgresql":
        port = port or 5432
    elif engine == "mysql":
        port = port or 3306
    else:
        return {
            "attempted": True,
            "success": False,
            "error": f"Unsupported engine: {engine}",
            "engine": engine,
            "host": host,
            "port": port,
            "database": database,
        }

    normalized_target = dict(target_config)
    normalized_target["engine"] = engine
    normalized_target["port"] = port

    try:
        connection = create_direct_connection(
            normalized_target,
            connect_timeout=connect_timeout,
        )
    except Exception as exc:
        return {
            "attempted": True,
            "success": False,
            "error": str(exc),
            "engine": engine,
            "host": host,
            "port": port,
            "database": database,
        }

    try:
        return {
            "attempted": True,
            "success": True,
            "error": None,
            "engine": engine,
            "host": host,
            "port": port,
            "database": database,
        }
    finally:
        close_connection(connection)


def cancel_postgres_by_pid(conn_params: Dict[str, Any], backend_pid: int, verbose: bool = False) -> bool:
    """
    Cancel a PostgreSQL query by its backend PID.

    Runs pg_cancel_backend on a side connection, so the pid must come from the server
    (pg_backend_pid(), pg_stat_activity): through a pooler, a connection's get_backend_pid()
    is the pooler's own. With the connection at hand, use cancel_postgres_connection.

    Args:
        conn_params: Connection parameters dict for creating cancel connection
        backend_pid: The backend process ID to cancel
        verbose: If True, print status messages

    Returns:
        True if query was cancelled, False otherwise
    """
    try:
        cancelled = _postgres_cancel_backend(conn_params, backend_pid)
        if verbose:
            if cancelled:
                print(f"   >> Backend query (PID {backend_pid}) cancelled successfully", flush=True)
            else:
                print(f"   >> Backend query (PID {backend_pid}) could not be cancelled (may have already finished)", flush=True)
        return cancelled
    except Exception as e:
        if verbose:
            print(f"   >> Error cancelling PostgreSQL query: {e}", flush=True)
        return False


def cancel_mysql_by_thread_id(
    target_config: Dict[str, Any],
    thread_id: int,
    verbose: bool = False,
    *,
    target: Optional[str] = None,
) -> bool:
    """
    Cancel a MySQL query by its connection thread ID.

    This is useful when cancelling from a different connection
    than the one running the query.

    Args:
        target_config: Target configuration for creating cancel connection
        thread_id: The MySQL connection thread ID to cancel
        verbose: If True, print status messages

    Returns:
        True if KILL QUERY was executed, False otherwise
    """
    try:
        params = resolve_connection_params(
            target=target,
            target_config=target_config,
        )
        cancel_conn = _create_mysql_connection(
            params['host'],
            params['port'],
            params['user'],
            params['password'],
            params['database'],
            params['tls'],
            tls_verify=params['tls_verify'],
            tls_ca=params['tls_ca'],
            hostaddr=params.get('hostaddr'),
        )
        try:
            cursor = cancel_conn.cursor()
            cursor.execute(f"KILL QUERY {thread_id}")  # nosem
            cursor.close()
            if verbose:
                print(f"   >> MySQL query (thread {thread_id}) killed successfully", flush=True)
            return True
        finally:
            cancel_conn.close()
    except Exception as e:
        if verbose:
            print(f"   >> Error killing MySQL query: {e}", flush=True)
        return False
