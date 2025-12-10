from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import time
import statistics
from typing import Dict, Any, List


def compare_query_performance(
    query: str = None,
    original_db_config: Dict[str, Any] = None,
    readyset_port: int | str = 5433,
    readyset_host: str = "localhost",
    iterations: int | str = 10,
    warmup_iterations: int | str = 2,
    readyset_db_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Compare query performance between original database and ReadySet.

    Executes the query multiple times against both the original database
    and ReadySet, collecting timing statistics.

    Args:
        query: SQL query to benchmark
        original_db_config: Original database configuration
        readyset_port: Port where ReadySet is listening
        readyset_host: Host where ReadySet is running
        iterations: Number of benchmark iterations
        warmup_iterations: Number of warmup runs (not counted in stats)
        readyset_db_config: ReadySet database configuration (for auth). If not provided, uses original_db_config credentials
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing performance comparison results
    """
    try:
        if not query:
            return {
                "success": False,
                "error": "No query provided for performance comparison"
            }

        if not original_db_config:
            return {
                "success": False,
                "error": "Original database configuration required"
            }

        # Parse config if it's a JSON string
        if isinstance(original_db_config, str):
            import json
            original_db_config = json.loads(original_db_config)

        iterations = int(iterations)
        warmup_iterations = int(warmup_iterations)
        readyset_port = int(readyset_port)

        engine = (original_db_config.get('engine') or 'postgresql').lower()

        print(f"Benchmarking query performance ({iterations} iterations)...")
        print(f"  Warmup: {warmup_iterations} iterations")
        print()

        # Warmup - Original DB
        print("Warming up original database...")
        for i in range(warmup_iterations):
            _execute_query_timed(query, original_db_config, is_readyset=False)

        # Warmup - ReadySet
        print("Warming up ReadySet...")
        # Use readyset_db_config if provided (for test container auth), otherwise fall back to original creds
        if readyset_db_config:
            readyset_config = {
                'engine': engine,
                'host': readyset_host,
                'port': readyset_port,
                'database': readyset_db_config.get('database', original_db_config.get('database')),
                'user': readyset_db_config.get('user', original_db_config.get('user')),
                'password': readyset_db_config.get('password', '')
            }
        else:
            readyset_config = {
                'engine': engine,
                'host': readyset_host,
                'port': readyset_port,
                'database': original_db_config.get('database'),
                'user': original_db_config.get('user'),
                'password': original_db_config.get('password', '')
            }
        for i in range(warmup_iterations):
            _execute_query_timed(query, readyset_config, is_readyset=True)

        print()
        print("Running benchmarks...")

        # Benchmark Original DB
        print(f"Testing original database ({original_db_config.get('host')}:{original_db_config.get('port')})...")
        original_times = []
        for i in range(iterations):
            result = _execute_query_timed(query, original_db_config, is_readyset=False)
            if result['success']:
                original_times.append(result['execution_time_ms'])
                print(f"  Run {i+1}/{iterations}: {result['execution_time_ms']:.2f}ms")
            else:
                print(f"  Run {i+1}/{iterations}: FAILED - {result.get('error')}")

        if not original_times:
            return {
                "success": False,
                "error": "All original database queries failed"
            }

        print()

        # Benchmark ReadySet
        print(f"Testing ReadySet ({readyset_host}:{readyset_port})...")
        readyset_times = []
        for i in range(iterations):
            result = _execute_query_timed(query, readyset_config, is_readyset=True)
            if result['success']:
                readyset_times.append(result['execution_time_ms'])
                print(f"  Run {i+1}/{iterations}: {result['execution_time_ms']:.2f}ms")
            else:
                print(f"  Run {i+1}/{iterations}: FAILED - {result.get('error')}")

        if not readyset_times:
            return {
                "success": False,
                "error": "All ReadySet queries failed"
            }

        # Calculate statistics
        original_stats = _calculate_statistics(original_times)
        readyset_stats = _calculate_statistics(readyset_times)

        # Calculate speedup
        speedup = original_stats['mean'] / readyset_stats['mean'] if readyset_stats['mean'] > 0 else 0
        speedup_median = original_stats['median'] / readyset_stats['median'] if readyset_stats['median'] > 0 else 0

        return {
            "success": True,
            "query": query,
            "iterations": iterations,
            "original": {
                "host": original_db_config.get('host'),
                "port": original_db_config.get('port'),
                "stats": original_stats,
                "times": original_times
            },
            "readyset": {
                "host": readyset_host,
                "port": readyset_port,
                "stats": readyset_stats,
                "times": readyset_times
            },
            "speedup": {
                "mean": speedup,
                "median": speedup_median,
                "improvement_pct": ((speedup - 1) * 100) if speedup >= 1 else -((1 - speedup) * 100)
            },
            "winner": "readyset" if speedup > 1 else "original"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Performance comparison failed: {str(e)}"
        }


def _execute_query_timed(
    query: str,
    db_config: Dict[str, Any],
    is_readyset: bool = False
) -> Dict[str, Any]:
    """
    Execute a query and measure its execution time.

    Args:
        query: SQL query to execute
        db_config: Database configuration
        is_readyset: Whether this is a ReadySet connection

    Returns:
        Dict with success, execution_time_ms, and optional error
    """
    try:
        engine = (db_config.get('engine') or 'postgresql').lower()
        host = db_config.get('host', 'localhost')
        port = db_config.get('port')
        database = db_config.get('database')
        user = db_config.get('user')
        password = db_config.get('password', '')

        start_time = time.perf_counter()

        if engine == 'mysql':
            result = _execute_mysql_query(
                query=query,
                host=host,
                port=port,
                user=user,
                database=database,
                password=password
            )
        else:
            result = _execute_postgres_query(
                query=query,
                host=host,
                port=port,
                user=user,
                database=database,
                password=password
            )

        end_time = time.perf_counter()
        execution_time_ms = (end_time - start_time) * 1000

        if result.returncode != 0:
            return {
                "success": False,
                "error": result.stderr.strip() if result.stderr else "Query execution failed",
                "execution_time_ms": execution_time_ms
            }

        return {
            "success": True,
            "execution_time_ms": execution_time_ms,
            "rows": result.stdout.count('\n') if result.stdout else 0
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Query execution timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Query execution failed: {str(e)}"
        }


def _execute_postgres_query(
    query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute query using psycopg2 library or psql client as fallback."""
    # Try psycopg2 first
    try:
        import psycopg2
        return _execute_postgres_query_psycopg2(
            query=query,
            host=host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        # Fall back to psql CLI if psycopg2 not available
        pass

    psql_cmd = [
        'psql',
        '-h', host,
        '-p', str(port),
        '-U', user,
        '-d', database,
        '-c', query,
        '-t',  # Tuples only
        '-A',  # Unaligned
        '-q'   # Quiet
    ]

    import os
    env = os.environ.copy()
    env['PGPASSWORD'] = password if password else ''

    return subprocess.run(
        psql_cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=30
    )


def _execute_postgres_query_psycopg2(
    query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute query using psycopg2 library."""
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
            password=password,
            database=database,
            connect_timeout=30
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchall()

                # Format output similar to psql CLI (pipe-separated rows)
                output_lines = []
                for row in result:
                    output_lines.append('|'.join(str(val) if val is not None else '' for val in row))

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


def _execute_mysql_query(
    query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute query using mysql client or pymysql as fallback."""
    # Ensure TCP is used even if host is "localhost"
    normalized_host = host or "127.0.0.1"
    if normalized_host == "localhost":
        normalized_host = "127.0.0.1"

    # Try pymysql first (works with ReadySet's mysql_native_password auth)
    try:
        import pymysql
        return _execute_mysql_query_pymysql(
            query=query,
            host=normalized_host,
            port=port,
            user=user,
            database=database,
            password=password
        )
    except ImportError:
        # Fall back to mysql CLI if pymysql not available
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
        '--execute', query
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


def _execute_mysql_query_pymysql(
    query: str,
    host: str,
    port: int,
    user: str,
    database: str,
    password: str
):
    """Execute query using PyMySQL library (handles mysql_native_password)."""
    import pymysql
    from dataclasses import dataclass

    @dataclass
    class CompletedProcess:
        returncode: int
        stdout: str
        stderr: str

    try:
        start = time.perf_counter()
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
                cursor.execute(query)
                result = cursor.fetchall()

                # Format output similar to mysql CLI (tab-separated rows)
                output_lines = []
                for row in result:
                    output_lines.append('\t'.join(str(val) if val is not None else 'NULL' for val in row))

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


def _calculate_statistics(times: List[float]) -> Dict[str, float]:
    """Calculate performance statistics from a list of execution times."""
    if not times:
        return {
            "mean": 0,
            "median": 0,
            "min": 0,
            "max": 0,
            "stddev": 0,
            "p50": 0,
            "p95": 0,
            "p99": 0
        }

    sorted_times = sorted(times)

    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "stddev": statistics.stdev(times) if len(times) > 1 else 0,
        "p50": _percentile(sorted_times, 50),
        "p95": _percentile(sorted_times, 95),
        "p99": _percentile(sorted_times, 99)
    }


def _percentile(sorted_data: List[float], percentile: float) -> float:
    """Calculate percentile from sorted data."""
    if not sorted_data:
        return 0

    k = (len(sorted_data) - 1) * (percentile / 100)
    f = int(k)
    c = f + 1

    if c >= len(sorted_data):
        return sorted_data[-1]

    d0 = sorted_data[f]
    d1 = sorted_data[c]

    return d0 + (d1 - d0) * (k - f)


def format_performance_comparison(result: Dict[str, Any]) -> str:
    """
    Format performance comparison results as human-readable text.

    Args:
        result: Performance comparison result dict

    Returns:
        Formatted string with performance comparison
    """
    if not result.get('success'):
        return f"Performance comparison failed: {result.get('error')}"

    lines = []
    lines.append("=" * 60)
    lines.append("Performance Comparison Results")
    lines.append("=" * 60)
    lines.append("")

    # Original DB stats
    orig = result['original']
    lines.append(f"Original Database ({orig['host']}:{orig['port']})")
    lines.append("-" * 60)
    lines.append(f"  Mean:     {orig['stats']['mean']:>8.2f} ms")
    lines.append(f"  Median:   {orig['stats']['median']:>8.2f} ms")
    lines.append(f"  Min:      {orig['stats']['min']:>8.2f} ms")
    lines.append(f"  Max:      {orig['stats']['max']:>8.2f} ms")
    lines.append(f"  Std Dev:  {orig['stats']['stddev']:>8.2f} ms")
    lines.append(f"  P95:      {orig['stats']['p95']:>8.2f} ms")
    lines.append(f"  P99:      {orig['stats']['p99']:>8.2f} ms")
    lines.append("")

    # ReadySet stats
    rs = result['readyset']
    lines.append(f"ReadySet Cache ({rs['host']}:{rs['port']})")
    lines.append("-" * 60)
    lines.append(f"  Mean:     {rs['stats']['mean']:>8.2f} ms")
    lines.append(f"  Median:   {rs['stats']['median']:>8.2f} ms")
    lines.append(f"  Min:      {rs['stats']['min']:>8.2f} ms")
    lines.append(f"  Max:      {rs['stats']['max']:>8.2f} ms")
    lines.append(f"  Std Dev:  {rs['stats']['stddev']:>8.2f} ms")
    lines.append(f"  P95:      {rs['stats']['p95']:>8.2f} ms")
    lines.append(f"  P99:      {rs['stats']['p99']:>8.2f} ms")
    lines.append("")

    # Speedup
    speedup = result['speedup']
    lines.append("Performance Improvement")
    lines.append("-" * 60)

    if speedup['mean'] > 1:
        lines.append(f"  ✓ ReadySet is {speedup['mean']:.2f}x faster (mean)")
        lines.append(f"  ✓ {speedup['improvement_pct']:.1f}% improvement")
    elif speedup['mean'] < 1:
        lines.append(f"  ✗ ReadySet is {(1/speedup['mean']):.2f}x slower (mean)")
        lines.append(f"  ✗ {abs(speedup['improvement_pct']):.1f}% slower")
    else:
        lines.append(f"  = Performance is roughly equal")

    lines.append(f"  Median speedup: {speedup['median']:.2f}x")
    lines.append("")

    # Summary
    winner = result['winner']
    if winner == 'readyset':
        lines.append("🎉 ReadySet cache provides better performance!")
    else:
        lines.append("⚠️  Original database is faster for this query")
        lines.append("   Consider query optimization or check if cache is warmed up")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
