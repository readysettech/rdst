"""
Direct database connection helper for RDST.

Handles creating direct psycopg2 or pymysql connections
without using DataManager infrastructure.
"""

import os
from typing import Dict, Any, Optional


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
            # Create SSL context that verifies server certificate
            ssl_context = ssl.create_default_context()
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
