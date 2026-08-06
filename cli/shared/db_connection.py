"""
Direct database connection helper for RDST.

Handles creating direct psycopg2 or pymysql connections
without using DataManager infrastructure.
"""

import socket
from typing import Dict, Any, Literal, Optional, Tuple


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
) -> Dict[str, Any]:
    """
    Resolve all connection parameters from target name or configuration.

    Handles password resolution from environment variables and TLS settings.

    Args:
        target: Target name (loads config from TargetsConfig)
        target_config: Target configuration dict (alternative to target name)

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
        host, port = get_tunnel_manager().ensure_tunnel(
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
        'password_env': password_env,  # Keep for error messages
    }
    if "ssh" in target_config:
        result['real_host'] = real_host
        result['real_port'] = real_port
    if hostaddr is not None:
        result['hostaddr'] = hostaddr
    return result


def create_direct_connection(
    target_config: Dict[str, Any],
    connect_timeout: int = 10,
    *,
    target: Optional[str] = None,
    force_fresh_tunnel: bool = False,
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

    if engine == 'postgresql':
        conn = _create_postgres_connection(
            host, port, user, password, database, use_tls,
            tls_verify=tls_verify, tls_ca=tls_ca,
            hostaddr=params.get('hostaddr'), connect_timeout=connect_timeout,
        )
    elif engine == 'mysql':
        conn = _create_mysql_connection(
            host, port, user, password, database, use_tls,
            tls_verify=tls_verify, tls_ca=tls_ca,
            hostaddr=params.get('hostaddr'), connect_timeout=connect_timeout,
        )
    else:
        raise ValueError(f"Unsupported database engine: {engine}")

    if params.get('read_only'):
        apply_read_only_session(conn, engine)
    return conn


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
        **postgres_ssl_kwargs(params),
    }
    result.update(overrides)
    return result


def create_mysql_connection_from_params(
    params: Dict[str, Any],
    *,
    connect_timeout: int = 10,
    **overrides: Any,
):
    """Connect with PyMySQL, separating a tunnel socket from the TLS identity."""
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
        }

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

        conn = psycopg2.connect(**conn_params)
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
                return conn
            except Exception:
                if raw_socket is not None:
                    raw_socket.close()
                raise

        return pymysql.connect(**conn_params)
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


def cancel_query(conn, engine: str, target_config: Optional[dict] = None) -> bool:
    """
    Cancel a running query on the database server.

    Args:
        conn: Active database connection with running query
        engine: 'postgresql' or 'mysql'
        target_config: For MySQL, needed to create cancel connection

    Returns:
        True if cancel was sent, False otherwise
    """
    try:
        if engine == 'postgresql':
            # psycopg2 has built-in cancel support
            conn.cancel()
            return True
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

    This is useful when cancelling from a different thread/connection
    than the one running the query.

    Args:
        conn_params: Connection parameters dict for creating cancel connection
        backend_pid: The backend process ID to cancel
        verbose: If True, print status messages

    Returns:
        True if query was cancelled, False otherwise
    """
    try:
        import psycopg2
        cancel_conn = psycopg2.connect(**conn_params)
        try:
            with cancel_conn.cursor() as cursor:
                cursor.execute(f"SELECT pg_cancel_backend({backend_pid})")  # nosem
                result = cursor.fetchone()
                cancelled = result[0] if result else False
            if verbose:
                if cancelled:
                    print(f"   >> Backend query (PID {backend_pid}) cancelled successfully", flush=True)
                else:
                    print(f"   >> Backend query (PID {backend_pid}) could not be cancelled (may have already finished)", flush=True)
            return cancelled
        finally:
            cancel_conn.close()
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
