"""
Direct database connection helper for RDST.

Handles creating direct psycopg2 or pymysql connections
without using DataManager infrastructure.
"""

import os
from typing import Dict, Any, Optional, Tuple


def resolve_connection_params(target: Optional[str] = None, target_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Resolve all connection parameters from target name or configuration.

    Handles password resolution from environment variables and TLS settings.

    Args:
        target: Target name (loads config from TargetsConfig)
        target_config: Target configuration dict (alternative to target name)

    Returns:
        Dict with resolved connection parameters:
            - engine: 'postgresql' or 'mysql'
            - host: Database host
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
        from lib.cli.rdst_cli import TargetsConfig
        cfg = TargetsConfig()
        cfg.load()
        target_config = cfg.get(target)
        if target_config is None:
            raise ValueError(f"Target '{target}' not found in configuration")

    engine = target_config.get('engine', 'postgresql').lower()
    host = target_config.get('host', 'localhost')
    port = target_config.get('port', 5432 if 'postgres' in engine else 3306)
    user = target_config.get('user') or target_config.get('username')
    database = target_config.get('database') or target_config.get('dbname')
    tls = target_config.get('tls', False)
    read_only = target_config.get('read_only', False)

    # Resolve password from environment variable or direct value
    password_env = target_config.get('password_env')
    password = os.environ.get(password_env) if password_env else target_config.get('password')

    # Determine SSL mode for PostgreSQL
    sslmode = 'require' if tls else 'prefer'

    return {
        'engine': engine,
        'host': host,
        'port': port,
        'user': user,
        'password': password,
        'database': database,
        'tls': tls,
        'sslmode': sslmode,
        'read_only': read_only,
        'password_env': password_env,  # Keep for error messages
    }


def create_direct_connection(target_config: Dict[str, Any]):
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
    engine = target_config.get('engine', '').lower()
    host = target_config.get('host')
    port = target_config.get('port')
    user = target_config.get('user')
    database = target_config.get('database')
    password_env = target_config.get('password_env')
    use_tls = target_config.get('tls', False)

    # Validate required fields
    if not all([engine, host, port, user, database]):
        raise ValueError("Missing required connection parameters in target config")

    # Get password from environment
    password = os.environ.get(password_env) if password_env else None
    if not password and password_env:
        raise ValueError(f"Password environment variable '{password_env}' not set")

    if engine == 'postgresql':
        return _create_postgres_connection(host, port, user, password, database, use_tls)
    elif engine == 'mysql':
        return _create_mysql_connection(host, port, user, password, database, use_tls)
    else:
        raise ValueError(f"Unsupported database engine: {engine}")


def _create_postgres_connection(host: str, port: int, user: str, password: str, database: str, use_tls: bool = False):
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
            'connect_timeout': 10,
        }

        if use_tls:
            conn_params['sslmode'] = 'require'

        conn = psycopg2.connect(**conn_params)
        # Set autocommit for read-only queries
        conn.autocommit = True
        return conn
    except Exception as e:
        raise RuntimeError(f"Failed to connect to PostgreSQL: {e}")


def _create_mysql_connection(host: str, port: int, user: str, password: str, database: str, use_tls: bool = False):
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
            'connect_timeout': 10,
            'autocommit': True,
            'cursorclass': pymysql.cursors.DictCursor,  # Return results as dicts
        }

        if use_tls:
            import ssl
            # Require encryption but don't verify certificate (matches psycopg2 sslmode='require')
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            conn_params['ssl'] = ssl_context

        conn = pymysql.connect(**conn_params)
        return conn
    except Exception as e:
        raise RuntimeError(f"Failed to connect to MySQL: {e}")


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
            cancel_conn = _create_mysql_connection(
                target_config['host'],
                target_config['port'],
                target_config['user'],
                os.environ.get(target_config.get('password_env', '')),
                target_config['database']
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


def cancel_mysql_by_thread_id(target_config: Dict[str, Any], thread_id: int, verbose: bool = False) -> bool:
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
        cancel_conn = _create_mysql_connection(
            target_config['host'],
            target_config['port'],
            target_config['user'],
            os.environ.get(target_config.get('password_env', '')),
            target_config['database'],
            target_config.get('tls', False)
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
