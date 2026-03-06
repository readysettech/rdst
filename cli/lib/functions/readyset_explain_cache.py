from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import json
from typing import Dict, Any


def explain_create_cache_readyset(
    query: str = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute EXPLAIN CREATE CACHE against Readyset instance.

    This determines real cacheability from the actual Readyset container,
    not static analysis.

    Args:
        query: SQL query to test
        readyset_port: Port where Readyset is listening
        readyset_host: Host where Readyset is running
        test_db_config: Test database configuration (for connection info)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing cacheability results from Readyset
    """
    try:
        if not query:
            return {
                "success": False,
                "error": "No query provided for Readyset analysis",
            }

        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Get connection details from test DB config
        database = test_db_config.get("database", "testdb")
        user = test_db_config.get("user", "postgres")
        password = test_db_config.get("password", "")
        engine = (test_db_config.get("engine") or "postgresql").lower()

        # Build EXPLAIN CREATE CACHE command
        explain_query = f"EXPLAIN CREATE CACHE FROM {query}"

        print(
            f"Running EXPLAIN CREATE CACHE against Readyset on port {readyset_port}..."
        )

        if engine == "mysql":
            result = _run_explain_mysql(
                explain_query=explain_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )
        else:
            # Default to PostgreSQL client
            result = _run_explain_postgres(
                explain_query=explain_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )

        if result.returncode != 0:
            return {
                "success": False,
                "cacheable": False,
                "error": f"Readyset EXPLAIN CREATE CACHE failed: {result.stderr}",
                "query": query,
            }

        # Parse Readyset response
        output = result.stdout.strip()

        # Readyset returns tab-separated output with format:
        # query_id\tproxied_query\treadyset_supported
        cacheable = False
        confidence = "unknown"
        details = output
        issues = []
        explanation = ""

        # Parse tab-separated or pipe-separated output from EXPLAIN CREATE CACHE
        # Try pipe-separated first (newer format), then tab-separated (older format)
        separator = "|" if "|" in output else "\t" if "\t" in output else None

        if separator:
            # Split by separator - format: query_id, proxied_query, readyset_supported
            parts = output.split(separator)
            if len(parts) >= 3:
                query_id = parts[0].strip()
                readyset_supported = parts[2].lower().strip()

                # Check if Readyset supports this query
                if readyset_supported == "yes":
                    cacheable = True
                    confidence = "high"
                    explanation = (
                        f"Readyset can cache this query (query_id: {query_id})"
                    )
                elif readyset_supported == "cached":
                    cacheable = True
                    confidence = "high"
                    explanation = (
                        f"Query is already cached in Readyset (query_id: {query_id})"
                    )
                elif readyset_supported == "no":
                    cacheable = False
                    confidence = "high"
                    explanation = "Readyset does not support caching this query"
                    issues.append("Query pattern not supported by Readyset")
                else:
                    # Unknown support status
                    cacheable = False
                    confidence = "low"
                    explanation = (
                        f"Unknown Readyset support status: {readyset_supported}"
                    )
            else:
                # Unexpected format
                cacheable = False
                confidence = "unknown"
                explanation = f"Unexpected output format: {output}"
        # Fallback parsing for other response formats
        elif "successfully" in output.lower() or "created" in output.lower():
            cacheable = True
            confidence = "high"
            explanation = "Readyset successfully validated CREATE CACHE statement."
        elif "unsupported" in output.lower():
            cacheable = False
            confidence = "high"
            issues.append(f"Readyset does not support this query pattern")
            explanation = issues[0]
        elif "error" in output.lower() or "failed" in output.lower():
            cacheable = False
            confidence = "high"
            # Extract error details
            issues.append(f"Readyset error: {output}")
            explanation = issues[0]
        else:
            # Try to parse as JSON
            try:
                json_output = json.loads(output)
                cacheable = json_output.get("cacheable", False)
                issues = json_output.get("issues", [])
                details = json_output
                explanation = json_output.get("explanation") or json_output.get(
                    "message", ""
                )
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
            "readyset_port": readyset_port,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "cacheable": False,
            "error": "Readyset EXPLAIN CREATE CACHE timed out",
            "query": query,
        }
    except Exception as e:
        return {
            "success": False,
            "cacheable": False,
            "error": f"Failed to execute EXPLAIN CREATE CACHE: {str(e)}",
            "query": query,
        }


def _run_explain_postgres(
    explain_query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute EXPLAIN CREATE CACHE using psql against a PostgreSQL Readyset endpoint."""
    # Try using psycopg2 library first
    try:
        import psycopg2

        return _run_explain_postgres_psycopg2(
            explain_query=explain_query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password,
        )
    except ImportError:
        pass

    # Fallback to psql command-line tool
    psql_cmd = [
        "psql",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
        "-c",
        explain_query,
        "-t",  # Tuples only (no headers/footers)
        "-A",  # Unaligned output
    ]

    import os

    env = os.environ.copy()
    # Set PGPASSWORD even if empty to prevent interactive prompts
    # Readyset typically doesn't require authentication
    env["PGPASSWORD"] = password if password else ""

    return subprocess.run(psql_cmd, capture_output=True, text=True, env=env, timeout=30)


def _run_explain_postgres_psycopg2(
    explain_query: str, host: str, port: int, user: str, database: str, password: str
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
            password=password or "",
            database=database,
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(explain_query)
                result = cursor.fetchall()

                # Format output similar to psql -t -A (tab-separated, no headers)
                output_lines = []
                for row in result:
                    output_lines.append(
                        "\t".join(str(val) if val is not None else "" for val in row)
                    )

                return CompletedProcess(
                    returncode=0, stdout="\n".join(output_lines), stderr=""
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def _run_explain_mysql(
    explain_query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute EXPLAIN CREATE CACHE using mysql client against a MySQL Readyset endpoint."""
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
            password=password,
        )
    except ImportError:
        pass

    mysql_cmd = [
        "mysql",
        "--protocol=TCP",
        f"--host={normalized_host}",
        f"--port={port}",
        f"--user={user}",
        f"--database={database}",
        "--batch",
        "--skip-column-names",
        "--raw",
        "--execute",
        explain_query,
    ]

    import os

    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password

    return subprocess.run(
        mysql_cmd, capture_output=True, text=True, env=env, timeout=30
    )


def _run_explain_mysql_pymysql(
    explain_query: str, host: str, port: int, user: str, database: str, password: str
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
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(explain_query)
                result = cursor.fetchall()

                # Format output similar to mysql CLI
                output_lines = []
                for row in result:
                    output_lines.append("\t".join(str(val) for val in row))

                return CompletedProcess(
                    returncode=0, stdout="\n".join(output_lines), stderr=""
                )
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def create_cache_readyset(
    query: str = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Execute CREATE CACHE to actually cache a query in Readyset.

    Args:
        query: SQL query to cache
        readyset_port: Port where Readyset is listening
        readyset_host: Host where Readyset is running
        test_db_config: Test database configuration (for connection info)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing cache creation results
    """
    try:
        if not query:
            return {"success": False, "error": "No query provided for cache creation"}

        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Get connection details from test DB config
        database = test_db_config.get("database", "testdb")
        user = test_db_config.get("user", "postgres")
        password = test_db_config.get("password", "")
        engine = (test_db_config.get("engine") or "postgresql").lower()

        # Build CREATE SHALLOW CACHE command (shallow mode - no replication)
        cache_query = f"CREATE SHALLOW CACHE FROM {query}"

        print(f"Creating cache in Readyset on port {readyset_port}...")

        if engine == "mysql":
            result = _run_cache_mysql(
                cache_query=cache_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )
        else:
            # Default to PostgreSQL client
            result = _run_cache_postgres(
                cache_query=cache_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )

        if result.returncode != 0:
            return {
                "success": False,
                "cached": False,
                "error": f"CREATE SHALLOW CACHE failed: {result.stderr}",
                "query": query,
            }

        # Parse Readyset response
        output = result.stdout.strip()

        # Check for success indicators
        if "CREATE CACHE" in output or output == "" or "successfully" in output.lower():
            return {
                "success": True,
                "cached": True,
                "message": "Cache created successfully",
                "query": query,
                "readyset_port": readyset_port,
            }
        else:
            return {
                "success": False,
                "cached": False,
                "error": f"Unexpected response: {output}",
                "query": query,
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "cached": False,
            "error": "CREATE SHALLOW CACHE timed out",
            "query": query,
        }
    except Exception as e:
        return {
            "success": False,
            "cached": False,
            "error": f"Failed to create shallow cache: {str(e)}",
            "query": query,
        }


def drop_cache_readyset(
    cache_name: str = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Drop a cache from Readyset.

    Args:
        cache_name: Name of the cache to drop
        readyset_port: Port where Readyset is listening
        readyset_host: Host where Readyset is running
        test_db_config: Database configuration for connection
        **kwargs: Additional parameters

    Returns:
        Dict with drop result
    """
    try:
        if not cache_name:
            return {"success": False, "error": "No cache name provided"}

        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        database = test_db_config.get("database", "testdb")
        user = test_db_config.get("user", "postgres")
        password = test_db_config.get("password", "")
        engine = (test_db_config.get("engine") or "postgresql").lower()

        drop_query = f"DROP SHALLOW CACHE {cache_name}"

        if engine == "mysql":
            result = _run_cache_mysql(
                cache_query=drop_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )
        else:
            result = _run_cache_postgres(
                cache_query=drop_query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
            )

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"DROP SHALLOW CACHE failed: {result.stderr}",
            }

        return {"success": True, "message": f"Cache {cache_name} dropped"}

    except Exception as e:
        return {"success": False, "error": f"Failed to drop cache: {str(e)}"}


def _run_cache_postgres(
    cache_query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute CREATE CACHE using psql against a PostgreSQL Readyset endpoint."""
    # Try using psycopg2 library first
    try:
        import psycopg2

        return _run_cache_postgres_psycopg2(
            cache_query=cache_query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password,
        )
    except ImportError:
        pass

    # Fallback to psql command-line tool
    psql_cmd = [
        "psql",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        database,
        "-c",
        cache_query,
        "-t",  # Tuples only (no headers/footers)
        "-A",  # Unaligned output
    ]

    import os

    env = os.environ.copy()
    # Set PGPASSWORD even if empty to prevent interactive prompts
    # Readyset typically doesn't require authentication
    env["PGPASSWORD"] = password if password else ""

    return subprocess.run(psql_cmd, capture_output=True, text=True, env=env, timeout=30)


def _run_cache_postgres_psycopg2(
    cache_query: str, host: str, port: int, user: str, database: str, password: str
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
            password=password or "",
            database=database,
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(cache_query)
                connection.commit()

                return CompletedProcess(returncode=0, stdout="CREATE CACHE", stderr="")
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def _run_cache_mysql(
    cache_query: str, host: str, port: int, user: str, database: str, password: str
):
    """Execute CREATE CACHE using mysql client against a MySQL Readyset endpoint."""
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
            password=password,
        )
    except ImportError:
        pass

    mysql_cmd = [
        "mysql",
        "--protocol=TCP",
        f"--host={normalized_host}",
        f"--port={port}",
        f"--user={user}",
        f"--database={database}",
        "--batch",
        "--skip-column-names",
        "--raw",
        "--execute",
        cache_query,
    ]

    import os

    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password

    return subprocess.run(
        mysql_cmd, capture_output=True, text=True, env=env, timeout=30
    )


def _run_cache_mysql_pymysql(
    cache_query: str, host: str, port: int, user: str, database: str, password: str
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
            connect_timeout=30,
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(cache_query)
                connection.commit()

                return CompletedProcess(returncode=0, stdout="CREATE CACHE", stderr="")
        finally:
            connection.close()

    except Exception as e:
        return CompletedProcess(returncode=1, stdout="", stderr=str(e))


def get_cache_id_for_query(
    query: str,
    readyset_port: int,
    db_config: Dict[str, Any],
) -> str | None:
    """
    Query SHOW CACHES to get the cache ID for a specific query.

    Args:
        query: SQL query to find cache for
        readyset_port: Readyset port
        db_config: Database configuration

    Returns:
        Cache ID (query_id) if found, None otherwise
    """
    import os

    try:
        database = db_config.get("database", "testdb")
        user = db_config.get("user", "postgres")
        password = db_config.get("password", "")
        engine = (db_config.get("engine") or "postgresql").lower()

        normalized_query = " ".join(query.strip().split())

        if engine == "mysql":
            cmd = [
                "mysql",
                "--protocol=TCP",
                "--host=localhost",
                f"--port={readyset_port}",
                f"--user={user}",
                f"--database={database}",
                "-e",
                "SHOW CACHES;",
            ]
            env = (
                {**os.environ, "MYSQL_PWD": password} if password else dict(os.environ)
            )
        else:
            cmd = [
                "psql",
                "-h",
                "localhost",
                "-p",
                str(readyset_port),
                "-U",
                user,
                "-d",
                database,
                "-c",
                "SHOW CACHES;",
                "-A",
                "-t",
                "-F",
                "|||",
            ]
            env = (
                {**os.environ, "PGPASSWORD": password} if password else dict(os.environ)
            )

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, env=env
        )

        if result.returncode != 0:
            return None

        lines = result.stdout.strip().split("\n")
        current_cache_id = None
        current_query_parts = []

        for line in lines:
            if not line.strip():
                continue

            parts = line.split("|||")

            if len(parts) >= 3 and parts[0].strip().startswith("q_"):
                if current_cache_id and current_query_parts:
                    full_query = " ".join(current_query_parts)
                    normalized_cache_query = " ".join(full_query.strip().split())
                    if normalized_query.lower() in normalized_cache_query.lower():
                        return current_cache_id

                current_cache_id = parts[0].strip()
                current_query_parts = [parts[2].strip()]
            elif current_cache_id and len(parts) >= 3:
                current_query_parts.append(parts[2].strip())

        if current_cache_id and current_query_parts:
            full_query = " ".join(current_query_parts)
            normalized_cache_query = " ".join(full_query.strip().split())
            if normalized_query.lower() in normalized_cache_query.lower():
                return current_cache_id

        return None

    except Exception:
        return None


def warm_cache_and_measure(
    query: str,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    test_db_config: Dict[str, Any] = None,
    warmup_runs: int = 2,
    measure_runs: int = 3,
    **kwargs,
) -> Dict[str, Any]:
    """
    Warm the cache by running the query and measure performance.

    Shallow caches need to be "warmed" - the first query after CREATE SHALLOW CACHE
    is a cache miss that populates the Moka cache. Subsequent queries are cache hits.

    Uses a single persistent connection to avoid measuring connection overhead.

    Args:
        query: SQL query to run
        readyset_port: Port where Readyset is listening
        readyset_host: Host where Readyset is running
        test_db_config: Test database configuration
        warmup_runs: Number of warmup runs (first run populates cache)
        measure_runs: Number of measurement runs after warmup
        **kwargs: Additional parameters

    Returns:
        Dict containing timing measurements:
            - cold_time_ms: First run time (cache miss)
            - warm_times_ms: List of subsequent run times (cache hits)
            - avg_warm_time_ms: Average of warm times
            - speedup: Ratio of cold_time to avg_warm_time
    """
    try:
        if not query:
            return {"success": False, "error": "No query provided"}

        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        database = test_db_config.get("database", "testdb")
        user = test_db_config.get("user", "postgres")
        password = test_db_config.get("password", "")
        engine = (test_db_config.get("engine") or "postgresql").lower()

        timings = []
        total_runs = warmup_runs + measure_runs

        print(f"Warming cache ({warmup_runs} warmup + {measure_runs} measured runs)...")

        # Use a single persistent connection to avoid measuring connection overhead
        if engine == "mysql":
            result = _run_queries_mysql_persistent(
                query=query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
                num_runs=total_runs,
            )
        else:
            result = _run_queries_postgres_persistent(
                query=query,
                host=readyset_host,
                port=readyset_port,
                user=user,
                database=database,
                password=password,
                num_runs=total_runs,
            )

        if not result.get("success"):
            return result

        timings = result.get("timings_ms", [])

        if len(timings) < total_runs:
            return {
                "success": False,
                "error": f"Expected {total_runs} timings, got {len(timings)}",
            }

        # First run is cold (cache miss), rest are warm (cache hits)
        warm_times_ms = timings[warmup_runs:]  # Skip warmup runs for measurement
        avg_warm_time_ms = sum(warm_times_ms) / len(warm_times_ms) if warm_times_ms else 0

        print(f"  Cached query time: {avg_warm_time_ms:.2f}ms")

        return {
            "success": True,
            "warm_times_ms": [round(t, 2) for t in warm_times_ms],
            "avg_warm_time_ms": round(avg_warm_time_ms, 2),
            "all_timings_ms": [round(t, 2) for t in timings],
        }

    except Exception as e:
        return {"success": False, "error": f"Cache warming failed: {str(e)}"}


def _run_queries_postgres_persistent(
    query: str, host: str, port: int, user: str, database: str, password: str, num_runs: int
) -> Dict[str, Any]:
    """Execute a query multiple times using a single persistent psycopg2 connection and cursor."""
    import time

    try:
        import psycopg2

        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password or "",
            database=database,
            connect_timeout=30,
        )
        # Use autocommit to avoid transaction overhead on each query
        connection.autocommit = True

        timings = []
        cursor = connection.cursor()
        try:
            for _ in range(num_runs):
                start_time = time.perf_counter()
                cursor.execute(query)
                cursor.fetchall()
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                timings.append(elapsed_ms)

            return {"success": True, "timings_ms": timings}
        finally:
            cursor.close()
            connection.close()

    except ImportError:
        return {"success": False, "error": "psycopg2 not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _run_queries_mysql_persistent(
    query: str, host: str, port: int, user: str, database: str, password: str, num_runs: int
) -> Dict[str, Any]:
    """Execute a query multiple times using a single persistent pymysql connection and cursor."""
    import time

    normalized_host = host or "127.0.0.1"
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    try:
        import pymysql

        connection = pymysql.connect(
            host=normalized_host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=30,
            autocommit=True,  # Avoid transaction overhead
        )

        timings = []
        cursor = connection.cursor()
        try:
            for _ in range(num_runs):
                start_time = time.perf_counter()
                cursor.execute(query)
                cursor.fetchall()
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                timings.append(elapsed_ms)

            return {"success": True, "timings_ms": timings}
        finally:
            cursor.close()
            connection.close()

    except ImportError:
        return {"success": False, "error": "pymysql not installed"}
    except Exception as e:
        return {"success": False, "error": str(e)}