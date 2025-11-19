from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import json
from typing import Dict, Any


def explain_create_cache_readyset(
    query: str = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Execute EXPLAIN CREATE CACHE against ReadySet instance.

    This determines real cacheability from the actual ReadySet container,
    not static analysis.

    Args:
        query: SQL query to test
        readyset_port: Port where ReadySet is listening
        readyset_host: Host where ReadySet is running
        test_db_config: Test database configuration (for connection info)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing cacheability results from ReadySet
    """
    try:
        if not query:
            return {
                "success": False,
                "error": "No query provided for ReadySet analysis"
            }

        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Get connection details from test DB config
        database = test_db_config.get('database', 'testdb')
        user = test_db_config.get('user', 'postgres')
        password = test_db_config.get('password', '')
        engine = (test_db_config.get('engine') or 'postgresql').lower()

        # Build EXPLAIN CREATE CACHE command
        explain_query = f"EXPLAIN CREATE CACHE FROM {query}"

        print(f"Running EXPLAIN CREATE CACHE against ReadySet on port {readyset_port}...")

        if engine == 'mysql':
            result = _run_explain_mysql(
                explain_query=explain_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password
            )
        else:
            # Default to PostgreSQL client
            result = _run_explain_postgres(
                explain_query=explain_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password
            )

        if result.returncode != 0:
            return {
                "success": False,
                "cacheable": False,
                "error": f"ReadySet EXPLAIN CREATE CACHE failed: {result.stderr}",
                "query": query
            }

        # Parse ReadySet response
        output = result.stdout.strip()

        # ReadySet returns tab-separated output with format:
        # query_id\tproxied_query\treadyset_supported
        cacheable = False
        confidence = "unknown"
        details = output
        issues = []
        explanation = ""

        # Parse tab-separated or pipe-separated output from EXPLAIN CREATE CACHE
        # Try pipe-separated first (newer format), then tab-separated (older format)
        separator = '|' if '|' in output else '\t' if '\t' in output else None

        if separator:
            # Split by separator - format: query_id, proxied_query, readyset_supported
            parts = output.split(separator)
            if len(parts) >= 3:
                query_id = parts[0].strip()
                readyset_supported = parts[2].lower().strip()

                # Check if ReadySet supports this query
                if readyset_supported == 'yes':
                    cacheable = True
                    confidence = "high"
                    explanation = f"ReadySet can cache this query (query_id: {query_id})"
                elif readyset_supported == 'cached':
                    cacheable = True
                    confidence = "high"
                    explanation = f"Query is already cached in ReadySet (query_id: {query_id})"
                elif readyset_supported == 'no':
                    cacheable = False
                    confidence = "high"
                    explanation = "ReadySet does not support caching this query"
                    issues.append("Query pattern not supported by ReadySet")
                else:
                    # Unknown support status
                    cacheable = False
                    confidence = "low"
                    explanation = f"Unknown ReadySet support status: {readyset_supported}"
            else:
                # Unexpected format
                cacheable = False
                confidence = "unknown"
                explanation = f"Unexpected output format: {output}"
        # Fallback parsing for other response formats
        elif "successfully" in output.lower() or "created" in output.lower():
            cacheable = True
            confidence = "high"
            explanation = "ReadySet successfully validated CREATE CACHE statement."
        elif "unsupported" in output.lower():
            cacheable = False
            confidence = "high"
            issues.append(f"ReadySet does not support this query pattern")
            explanation = issues[0]
        elif "error" in output.lower() or "failed" in output.lower():
            cacheable = False
            confidence = "high"
            # Extract error details
            issues.append(f"ReadySet error: {output}")
            explanation = issues[0]
        else:
            # Try to parse as JSON
            try:
                json_output = json.loads(output)
                cacheable = json_output.get('cacheable', False)
                issues = json_output.get('issues', [])
                details = json_output
                explanation = json_output.get('explanation') or json_output.get('message', "")
            except json.JSONDecodeError:
                # Plain text response, check for key phrases
                if "cannot cache" in output.lower():
                    cacheable = False
                    issues.append(output)
                    explanation = output
                elif "can cache" in output.lower():
                    cacheable = True
                    explanation = output

        if not explanation:
            if issues:
                explanation = issues[0]
            elif isinstance(details, str):
                explanation = details

        return {
            "success": True,
            "cacheable": cacheable,
            "confidence": confidence,
            "method": "readyset_explain_cache",
            "query": query,
            "details": details,
                "issues": issues,
            "explanation": explanation,
            "readyset_port": readyset_port
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "cacheable": False,
            "error": "ReadySet EXPLAIN CREATE CACHE timed out",
            "query": query
        }
    except Exception as e:
        return {
            "success": False,
            "cacheable": False,
            "error": f"Failed to execute EXPLAIN CREATE CACHE: {str(e)}",
            "query": query
        }


def _run_explain_postgres(
    explain_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute EXPLAIN CREATE CACHE using psql against a PostgreSQL ReadySet endpoint."""
    # Try using psycopg2 library first
    try:
        import psycopg2
        return _run_explain_postgres_psycopg2(
            explain_query=explain_query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        pass

    # Fallback to psql command-line tool
    psql_cmd = [
        'psql',
        '-h', host,
        '-p', str(port),
        '-U', user,
        '-d', database,
        '-c', explain_query,
        '-t',  # Tuples only (no headers/footers)
        '-A'   # Unaligned output
    ]

    import os
    env = os.environ.copy()
    # Set PGPASSWORD even if empty to prevent interactive prompts
    # ReadySet typically doesn't require authentication
    env['PGPASSWORD'] = password if password else ''

    return subprocess.run(
        psql_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )


def _run_explain_postgres_psycopg2(
    explain_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute EXPLAIN CREATE CACHE using psycopg2 library (fallback for environments without psql)."""
    import psycopg2
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        # Connect using psycopg2
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password or '',
            database=database,
            connect_timeout=30
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(explain_query)
                result = cursor.fetchall()

                # Format output similar to psql -t -A (tab-separated, no headers)
                output_lines = []
                for row in result:
                    output_lines.append('\t'.join(str(val) if val is not None else '' for val in row))

                return CompletedProcess(
                    returncode=0,
                    stdout='\n'.join(output_lines),
                    stderr=''
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(
            returncode=1,
            stdout='',
            stderr=str(e)
        )


def _run_explain_mysql(
    explain_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute EXPLAIN CREATE CACHE using mysql client against a MySQL ReadySet endpoint."""
    # Ensure TCP is used even if host is "localhost"
    normalized_host = host or "127.0.0.1"
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    # Use pymysql or mysql.connector as fallback for newer MySQL clients
    # that don't have mysql_native_password plugin
    try:
        import pymysql
        return _run_explain_mysql_pymysql(
            explain_query=explain_query,
            host=normalized_host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        pass

    mysql_cmd = [
        'mysql',
        '--protocol=TCP',
        f'--host={normalized_host}',
        f'--port={port}',
        f'--user={user}',
        f'--database={database}',
        '--batch',
        '--skip-column-names',
        '--raw',
        '--execute', explain_query
    ]

    import os
    env = os.environ.copy()
    if password:
        env['MYSQL_PWD'] = password

    return subprocess.run(
        mysql_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )


def _run_explain_mysql_pymysql(
    explain_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute EXPLAIN CREATE CACHE using PyMySQL library (fallback for newer MySQL clients)."""
    import pymysql
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        # Connect using PyMySQL which handles mysql_native_password
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(explain_query)
                result = cursor.fetchall()

                # Format output similar to mysql CLI
                output_lines = []
                for row in result:
                    output_lines.append('\t'.join(str(val) for val in row))

                return CompletedProcess(
                    returncode=0,
                    stdout='\n'.join(output_lines),
                    stderr=''
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(
            returncode=1,
            stdout='',
            stderr=str(e)
        )


def create_cache_readyset(
    query: str = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Execute CREATE CACHE to actually cache a query in ReadySet.

    Args:
        query: SQL query to cache
        readyset_port: Port where ReadySet is listening
        readyset_host: Host where ReadySet is running
        test_db_config: Test database configuration (for connection info)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing cache creation results
    """
    try:
        if not query:
            return {
                "success": False,
                "error": "No query provided for cache creation"
            }

        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Get connection details from test DB config
        database = test_db_config.get('database', 'testdb')
        user = test_db_config.get('user', 'postgres')
        password = test_db_config.get('password', '')
        engine = (test_db_config.get('engine') or 'postgresql').lower()

        # Build CREATE CACHE command
        cache_query = f"CREATE CACHE FROM {query}"

        print(f"Creating cache in ReadySet on port {readyset_port}...")

        if engine == 'mysql':
            result = _run_cache_mysql(
                cache_query=cache_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password
            )
        else:
            # Default to PostgreSQL client
            result = _run_cache_postgres(
                cache_query=cache_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password
            )

        if result.returncode != 0:
            return {
                "success": False,
                "cached": False,
                "error": f"CREATE CACHE failed: {result.stderr}",
                "query": query
            }

        # Parse ReadySet response
        output = result.stdout.strip()

        # Check for success indicators
        if "CREATE CACHE" in output or output == "" or "successfully" in output.lower():
            return {
                "success": True,
                "cached": True,
                "message": "Cache created successfully",
                "query": query,
                "readyset_port": readyset_port
            }
        else:
            return {
                "success": False,
                "cached": False,
                "error": f"Unexpected response: {output}",
                "query": query
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "cached": False,
            "error": "CREATE CACHE timed out",
            "query": query
        }
    except Exception as e:
        return {
            "success": False,
            "cached": False,
            "error": f"Failed to create cache: {str(e)}",
            "query": query
        }


def _run_cache_postgres(
    cache_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute CREATE CACHE using psql against a PostgreSQL ReadySet endpoint."""
    # Try using psycopg2 library first
    try:
        import psycopg2
        return _run_cache_postgres_psycopg2(
            cache_query=cache_query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        pass

    # Fallback to psql command-line tool
    psql_cmd = [
        'psql',
        '-h', host,
        '-p', str(port),
        '-U', user,
        '-d', database,
        '-c', cache_query,
        '-t',  # Tuples only (no headers/footers)
        '-A'   # Unaligned output
    ]

    import os
    env = os.environ.copy()
    # Set PGPASSWORD even if empty to prevent interactive prompts
    # ReadySet typically doesn't require authentication
    env['PGPASSWORD'] = password if password else ''

    return subprocess.run(
        psql_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )


def _run_cache_postgres_psycopg2(
    cache_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute CREATE CACHE using psycopg2 library."""
    import psycopg2
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password or '',
            database=database,
            connect_timeout=30
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(cache_query)
                connection.commit()

                return CompletedProcess(
                    returncode=0,
                    stdout='CREATE CACHE',
                    stderr=''
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(
            returncode=1,
            stdout='',
            stderr=str(e)
        )


def _run_cache_mysql(
    cache_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute CREATE CACHE using mysql client against a MySQL ReadySet endpoint."""
    # Ensure TCP is used even if host is "localhost"
    normalized_host = host or "127.0.0.1"
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    # Use pymysql or mysql.connector as fallback for newer MySQL clients
    try:
        import pymysql
        return _run_cache_mysql_pymysql(
            cache_query=cache_query,
            host=normalized_host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        pass

    mysql_cmd = [
        'mysql',
        '--protocol=TCP',
        f'--host={normalized_host}',
        f'--port={port}',
        f'--user={user}',
        f'--database={database}',
        '--batch',
        '--skip-column-names',
        '--raw',
        '--execute', cache_query
    ]

    import os
    env = os.environ.copy()
    if password:
        env['MYSQL_PWD'] = password

    return subprocess.run(
        mysql_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )


def _run_cache_mysql_pymysql(
    cache_query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute CREATE CACHE using PyMySQL library."""
    import pymysql
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(cache_query)
                connection.commit()

                return CompletedProcess(
                    returncode=0,
                    stdout='CREATE CACHE',
                    stderr=''
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(
            returncode=1,
            stdout='',
            stderr=str(e)
        )
