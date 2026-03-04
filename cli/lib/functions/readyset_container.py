from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import time
import json
from typing import Dict, Any


def start_readyset_container(
    test_db_container: str = None,
    test_db_config: Dict[str, Any] = None,
    readyset_port: int | str = 5433,
    readyset_container_name: str = "rdst-readyset",
    **kwargs
) -> Dict[str, Any]:
    """
    Start Readyset container connected to test database.

    Readyset will snapshot from the test database and cache queries.

    Args:
        test_db_container: Name of test database container
        test_db_config: Test database configuration
        readyset_port: Port to expose Readyset on
        readyset_container_name: Name for Readyset container
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing Readyset container status
    """
    try:
        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Check if Readyset container already exists
        check_cmd = [
            'docker', 'ps', '-a',
            '--filter', f'name={readyset_container_name}',
            '--format', '{{.Names}}\t{{.Status}}'
        ]

        result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Build target database URL for Readyset
        engine = test_db_config.get('engine', 'postgresql')

        # Determine the readyset_url protocol based on engine
        if engine == 'mysql':
            readyset_url_protocol = 'mysql'
        else:
            readyset_url_protocol = 'postgresql'

        # If container exists and is running, return it
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split('\t')
            if len(parts) >= 2 and 'Up' in parts[1]:
                print(f"✓ Readyset container already running: {readyset_container_name}")
                return {
                    "success": True,
                    "container_name": readyset_container_name,
                    "readyset_url": f"{readyset_url_protocol}://localhost:{readyset_port}",
                    "port": readyset_port,
                    "already_running": True
                }
            elif len(parts) >= 1:
                # Container exists but not running, start it
                print(f"Starting existing Readyset container...")
                start_result = subprocess.run(
                    ['docker', 'start', readyset_container_name],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if start_result.returncode == 0:
                    # Verify container actually started and is still running
                    # (it might crash immediately after starting)
                    time.sleep(1)  # Give it a moment to crash if it's going to
                    verify_result = subprocess.run(
                        ['docker', 'ps', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )

                    if verify_result.returncode == 0 and readyset_container_name in verify_result.stdout:
                        print(f"✓ Successfully started existing Readyset container")
                        return {
                            "success": True,
                            "container_name": readyset_container_name,
                            "readyset_url": f"{readyset_url_protocol}://localhost:{readyset_port}",
                            "port": readyset_port,
                            "started": True
                        }
                    else:
                        # Container started but crashed immediately - remove it and create fresh
                        print(f"⚠️  Container started but immediately crashed, removing and recreating...")
                        subprocess.run(['docker', 'rm', '-f', readyset_container_name], capture_output=True, timeout=10)
                        # Fall through to create new container below
        host = test_db_config.get('host', 'localhost')
        port = test_db_config.get('port', 5434)
        database = test_db_config.get('database', 'testdb')
        user = test_db_config.get('user', 'postgres')
        password = test_db_config.get('password', '')

        # Map engine to DATABASE_TYPE
        if engine == 'postgresql':
            db_type = 'postgresql'
            # Use host.docker.internal to connect to test DB from inside container
            target_db_url = f"postgresql://{user}:{password}@host.docker.internal:{port}/{database}"
        elif engine == 'mysql':
            db_type = 'mysql'
            target_db_url = f"mysql://{user}:{password}@host.docker.internal:{port}/{database}"
        else:
            return {
                "success": False,
                "error": f"Unsupported database engine for Readyset: {engine}"
            }

        print(f"Creating Readyset container: {readyset_container_name}...")
        print(f"  Database Type: {db_type}")
        print(f"  Target DB: {db_type}://host.docker.internal:{port}/{database}")
        print(f"  Readyset port: {readyset_port}")

        # Create and start Readyset container
        # Use --pull always to ensure we get the latest image
        # Official readysettech/readyset image uses UPSTREAM_DB_URL and LISTEN_ADDRESS
        listen_address = f"0.0.0.0:{readyset_port}"
        docker_cmd = [
            'docker', 'run',
            '-d',
            '--pull', 'always',
            '--name', readyset_container_name,
            '-e', f'UPSTREAM_DB_URL={target_db_url}',
            '-e', f'LISTEN_ADDRESS={listen_address}',
            '-p', f'{readyset_port}:{readyset_port}',
            '--add-host=host.docker.internal:host-gateway',  # Allow container to reach host
            'docker.io/readysettech/readyset:latest'
        ]

        print(f"Starting docker run (pulling latest image if needed)...")
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=300  # Increased from 60s to 300s (5 min) for large image pulls
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            error_message = f"Failed to create Readyset container: {error_msg}"
            print(f"\n❌ {error_message}\n")
            raise Exception(error_message)

        print("✓ Readyset container created, waiting for initialization...")

        # Give container a moment to start
        time.sleep(2)

        # Test connectivity from inside the container to the database (optional diagnostic)
        print(f"Testing connectivity from Readyset container to {db_type}://host.docker.internal:{port}...")

        if db_type == 'mysql':
            # Test MySQL connection from inside container
            test_cmd = [
                'docker', 'exec', readyset_container_name,
                'mysql', '-h', 'host.docker.internal', '-P', str(port),
                '-u', user, f'-p{password}', '-e', 'SELECT 1;'
            ]
        else:
            # Test PostgreSQL connection from inside container
            test_cmd = [
                'docker', 'exec', readyset_container_name,
                'bash', '-c', f'PGPASSWORD={password} psql -h host.docker.internal -p {port} -U {user} -d {database} -c "SELECT 1;"'
            ]

        conn_test = subprocess.run(test_cmd, capture_output=True, text=True, timeout=10)
        if conn_test.returncode == 0:
            print(f"✓ Readyset container can reach test database")
        else:
            print(f"⚠️  Warning: Readyset container cannot reach test database")
            print(f"  Error: {conn_test.stderr[:200]}")

        # Quick check if container is still running
        verify_result = subprocess.run(
            ['docker', 'ps', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if verify_result.returncode != 0 or readyset_container_name not in verify_result.stdout:
            # Container crashed immediately - get logs
            logs_result = subprocess.run(
                ['docker', 'logs', readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            crash_logs = (logs_result.stdout + logs_result.stderr) if logs_result.returncode == 0 else "Unable to retrieve logs"

            print(f"\n⚠️  Readyset container crashed immediately after creation")
            print(f"Container logs:\n{crash_logs}\n")

            return {
                "success": False,
                "error": f"Readyset container crashed immediately. Logs:\n{crash_logs[:1000]}",
                "container_name": readyset_container_name,
                "crash_logs": crash_logs
            }

        return {
            "success": True,
            "container_name": readyset_container_name,
            "readyset_url": f"{readyset_url_protocol}://localhost:{readyset_port}",
            "port": readyset_port,
            "created": True,
            "target_db_url": target_db_url.replace(f':{password}@', ':***@')  # Hide password in logs
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Readyset container creation timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to start Readyset container: {str(e)}"
        }


def wait_for_readyset_ready(
    readyset_container_name: str = "rdst-readyset",
    timeout: int | str = 120,
    **kwargs
) -> Dict[str, Any]:
    """
    Wait for Readyset to finish snapshotting and be ready for queries.

    Args:
        readyset_container_name: Name of Readyset container
        timeout: Maximum time to wait in seconds
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing readiness status
    """
    try:
        timeout = int(timeout)
        start_time = time.time()

        print("Waiting for Readyset snapshot to complete...")

        while (time.time() - start_time) < timeout:
            # Check if container is still running
            result = subprocess.run(
                ['docker', 'ps', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0 or readyset_container_name not in result.stdout:
                # Container not running - check if it exited
                inspect_result = subprocess.run(
                    ['docker', 'inspect', readyset_container_name, '--format', '{{.State.Status}} {{.State.ExitCode}}'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if inspect_result.returncode == 0:
                    status_parts = inspect_result.stdout.strip().split()
                    status = status_parts[0] if status_parts else "unknown"

                    # Get last 50 lines of logs to show why it failed (increased from 20)
                    logs_result = subprocess.run(
                        ['docker', 'logs', '--tail', '50', readyset_container_name],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )

                    logs = logs_result.stdout + logs_result.stderr if logs_result.returncode == 0 else ""

                    error_message = f"Readyset container {readyset_container_name} not running (status: {status})"
                    if logs:
                        error_message += f"\n\nContainer logs:\n{logs[-500:]}"
                    print(f"\n❌ {error_message}\n")
                    raise Exception(error_message)

                error_message = f"Readyset container {readyset_container_name} not running"
                print(f"\n❌ {error_message}\n")
                raise Exception(error_message)

            # Check Readyset logs for "Streaming replication started" message
            # Avoid shell=True for security - use list form and do filtering in Python
            log_result = subprocess.run(
                ["docker", "logs", readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Filter logs in Python instead of using shell pipes
            if log_result.returncode == 0:
                log_output = log_result.stdout + log_result.stderr
                if "streaming replication started" in log_output.lower():
                    print("✓ Readyset snapshot complete and ready!")
                    return {
                        "success": True,
                        "ready": True,
                        "wait_time": time.time() - start_time
                    }

            # Also check for errors in Readyset logs (not Grafana)
            # Filter in Python for security (avoid shell=True)
            error_lines = []
            if log_result.returncode == 0:
                log_output = log_result.stdout + log_result.stderr
                lines = log_output.split('\n')
                for i, line in enumerate(lines):
                    if "readyset" in line.lower() and ("error" in line.lower() or "fatal" in line.lower()):
                        # Capture this line and next 10 lines for context
                        error_lines.append(line)
                        for j in range(1, 11):
                            if i + j < len(lines):
                                error_lines.append(lines[i + j])

            if error_lines:
                error_message = f"Readyset encountered errors during initialization:\n" + "\n".join(error_lines[-10:])
                print(f"\n❌ {error_message}\n")
                raise Exception(error_message)

            time.sleep(3)

        # Timeout - get logs for debugging
        logs_result = subprocess.run(
            ['docker', 'logs', '--tail', '30', readyset_container_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        logs = (logs_result.stdout + logs_result.stderr)[-1000:] if logs_result.returncode == 0 else ""

        error_message = (
            f"Readyset did not become ready within {timeout}s.\n"
            f"Container logs (last 30 lines):\n{logs}\n\n"
            f"Check full logs: docker logs {readyset_container_name}"
        )
        print(f"\n❌ {error_message}\n")
        raise Exception(error_message)

    except subprocess.TimeoutExpired as e:
        error_message = f"Timeout waiting for Readyset: {str(e)}"
        print(f"\n❌ {error_message}\n")
        raise Exception(error_message)
    except Exception:
        # Re-raise exceptions we already formatted
        raise


def start_readyset_container_direct(
    target_config: Dict[str, Any] = None,
    readyset_port: int | str = 5433,
    readyset_container_name: str = "rdst-readyset",
    **kwargs
) -> Dict[str, Any]:
    """
    Start Readyset container connected directly to upstream target database.

    This function supports shallow caching mode where Readyset connects directly
    to the production/target database without requiring a test sub-container.
    No snapshotting is required - Readyset can create shallow caches immediately.

    Args:
        target_config: Target database configuration with host, port, user, password, etc.
        readyset_port: Port to expose Readyset on
        readyset_container_name: Name for Readyset container
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing Readyset container status
    """
    try:
        import os

        # Parse target_config if it's a JSON string
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        readyset_port = int(readyset_port)

        # Get target database details
        engine = target_config.get('engine', 'postgresql')
        host = target_config.get('host', 'localhost')
        port = target_config.get('port', 5432 if engine == 'postgresql' else 3306)
        database = target_config.get('database', 'postgres')
        user = target_config.get('user', 'postgres')

        # Get password - either directly or from environment variable
        password = target_config.get('password', '')
        password_env = target_config.get('password_env')
        if password_env and not password:
            password = os.getenv(password_env, '')

        # Determine the readyset_url protocol based on engine
        if engine == 'mysql':
            readyset_url_protocol = 'mysql'
            db_type = 'mysql'
        else:
            readyset_url_protocol = 'postgresql'
            db_type = 'postgresql'

        # Check if Readyset container already exists and is running
        check_cmd = [
            'docker', 'ps', '-a',
            '--filter', f'name={readyset_container_name}',
            '--format', '{{.Names}}\t{{.Status}}'
        ]

        result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            timeout=5
        )

        # Remove existing container to ensure we use latest image with shallow cache support
        if result.returncode == 0 and result.stdout.strip():
            from lib.ui import console
            console.print(f"[dim]Removing existing container to ensure latest image...[/dim]")
            subprocess.run(['docker', 'rm', '-f', readyset_container_name], capture_output=True, timeout=10)

        # Build target database URL for Readyset (direct to upstream)
        # For Docker, we need to handle localhost specially
        docker_host = host
        if host in ('localhost', '127.0.0.1'):
            docker_host = 'host.docker.internal'

        if engine == 'mysql':
            target_db_url = f"mysql://{user}:{password}@{docker_host}:{port}/{database}"
        else:
            target_db_url = f"postgresql://{user}:{password}@{docker_host}:{port}/{database}"

        from lib.ui import console
        console.print(f"[dim]Creating Readyset container (shallow mode): {readyset_container_name}[/dim]")

        # Create and start Readyset container
        # Use --pull always to ensure we get the latest image (required for shallow cache support)
        # Official readysettech/readyset image uses UPSTREAM_DB_URL and LISTEN_ADDRESS
        listen_address = f"0.0.0.0:{readyset_port}"
        docker_cmd = [
            'docker', 'run',
            '-d',
            '--pull', 'always',
            '--name', readyset_container_name,
            '-e', f'UPSTREAM_DB_URL={target_db_url}',
            '-e', f'LISTEN_ADDRESS={listen_address}',
            '-p', f'{readyset_port}:{readyset_port}',
            '--add-host=host.docker.internal:host-gateway',
            'docker.io/readysettech/readyset:latest'
        ]

        console.print("[dim]Starting docker run...[/dim]")
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            error_message = f"Failed to create Readyset container: {error_msg}"
            console.print(f"[red]Error: {error_message}[/red]")
            raise Exception(error_message)

        console.print("[green]Readyset container created (shallow mode)[/green]")

        time.sleep(2)

        # Quick check if container is still running
        verify_result = subprocess.run(
            ['docker', 'ps', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if verify_result.returncode != 0 or readyset_container_name not in verify_result.stdout:
            logs_result = subprocess.run(
                ['docker', 'logs', readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            crash_logs = (logs_result.stdout + logs_result.stderr) if logs_result.returncode == 0 else "Unable to retrieve logs"

            console.print("[yellow]Readyset container crashed immediately after creation[/yellow]")

            return {
                "success": False,
                "error": f"Readyset container crashed immediately. Logs:\n{crash_logs[:1000]}",
                "container_name": readyset_container_name,
                "crash_logs": crash_logs
            }

        return {
            "success": True,
            "container_name": readyset_container_name,
            "readyset_url": f"{readyset_url_protocol}://localhost:{readyset_port}",
            "port": readyset_port,
            "created": True,
            "shallow_mode": True,
            "target_db_url": target_db_url.replace(f':{password}@', ':***@')
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Readyset container creation timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to start Readyset container (direct mode): {str(e)}"
        }


def wait_for_readyset_ready_shallow(
    readyset_container_name: str = "rdst-readyset",
    timeout: int | str = 60,
    **kwargs
) -> Dict[str, Any]:
    """
    Wait for Readyset to be ready for shallow cache operations.

    In shallow mode with the latest container, Readyset doesn't do full
    replication - it just needs to connect and be ready for queries.
    We wait for "listening on" or "ready to accept connections".

    Args:
        readyset_container_name: Name of Readyset container
        timeout: Maximum time to wait in seconds (default 60s)
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing readiness status
    """
    from lib.ui import console

    try:
        timeout = int(timeout)
        start_time = time.time()

        console.print("[dim]Waiting for Readyset to be ready...[/dim]")

        while (time.time() - start_time) < timeout:
            # Check if container is still running
            result = subprocess.run(
                ['docker', 'ps', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0 or readyset_container_name not in result.stdout:
                inspect_result = subprocess.run(
                    ['docker', 'inspect', readyset_container_name, '--format', '{{.State.Status}} {{.State.ExitCode}}'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if inspect_result.returncode == 0:
                    status_parts = inspect_result.stdout.strip().split()
                    status = status_parts[0] if status_parts else "unknown"

                    logs_result = subprocess.run(
                        ['docker', 'logs', '--tail', '50', readyset_container_name],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )

                    logs = logs_result.stdout + logs_result.stderr if logs_result.returncode == 0 else ""

                    error_message = f"Readyset container {readyset_container_name} not running (status: {status})"
                    if logs:
                        error_message += f"\n\nContainer logs:\n{logs[-500:]}"
                    raise Exception(error_message)

                error_message = f"Readyset container {readyset_container_name} not running"
                raise Exception(error_message)

            # Check Readyset logs for ready indicator
            log_result = subprocess.run(
                ["docker", "logs", readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            if log_result.returncode == 0:
                log_output = (log_result.stdout + log_result.stderr).lower()

                # Ready indicators for shallow mode
                if any(indicator in log_output for indicator in [
                    "listening on",
                    "ready to accept connections",
                    "streaming replication started",
                ]):
                    elapsed = time.time() - start_time
                    console.print(f"[green]Readyset is ready ({elapsed:.1f}s)[/green]")
                    return {
                        "success": True,
                        "ready": True,
                        "wait_time": elapsed,
                        "shallow_mode": True
                    }

                # Check for fatal errors
                if "fatal" in log_output:
                    error_lines = []
                    lines = (log_result.stdout + log_result.stderr).split('\n')
                    for i, line in enumerate(lines):
                        if "fatal" in line.lower():
                            error_lines.append(line)
                            for j in range(1, 5):
                                if i + j < len(lines):
                                    error_lines.append(lines[i + j])
                            break

                    if error_lines:
                        error_message = f"Readyset encountered fatal error:\n" + "\n".join(error_lines[-10:])
                        raise Exception(error_message)

            time.sleep(1)

        # Timeout
        logs_result = subprocess.run(
            ['docker', 'logs', '--tail', '30', readyset_container_name],
            capture_output=True,
            text=True,
            timeout=5
        )
        logs = (logs_result.stdout + logs_result.stderr)[-1000:] if logs_result.returncode == 0 else ""

        error_message = (
            f"Readyset did not become ready within {timeout}s.\n"
            f"Container logs (last 30 lines):\n{logs}\n\n"
            f"Check full logs: docker logs {readyset_container_name}"
        )
        raise Exception(error_message)

    except subprocess.TimeoutExpired as e:
        error_message = f"Timeout waiting for Readyset: {str(e)}"
        raise Exception(error_message)
    except Exception:
        raise


def check_readyset_container_status(
    readyset_container_name: str = "rdst-readyset",
    **kwargs
) -> Dict[str, Any]:
    """
    Check if Readyset container exists and is running.

    Args:
        readyset_container_name: Name of Readyset container
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing container status
    """
    try:
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', f'name={readyset_container_name}', '--format', '{{.Names}}\t{{.Status}}\t{{.ID}}'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return {
                "success": False,
                "exists": False,
                "running": False,
                "error": "Failed to check Docker containers"
            }

        if not result.stdout.strip():
            return {
                "success": True,
                "exists": False,
                "running": False
            }

        parts = result.stdout.strip().split('\t')
        is_running = 'Up' in parts[1] if len(parts) >= 2 else False

        return {
            "success": True,
            "exists": True,
            "running": is_running,
            "container_name": parts[0] if parts else None,
            "container_id": parts[2] if len(parts) >= 3 else None,
            "status": parts[1] if len(parts) >= 2 else None
        }

    except Exception as e:
        return {
            "success": False,
            "exists": False,
            "running": False,
            "error": f"Failed to check Readyset container status: {str(e)}"
        }


def check_docker_available() -> Dict[str, Any]:
    """
    Check if Docker is available and running.

    Returns:
        Dict with 'available' bool and optional 'error' message
    """
    try:
        result = subprocess.run(
            ['docker', 'info'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return {"available": True}

        # Docker command exists but daemon not running
        if "Cannot connect to the Docker daemon" in result.stderr:
            return {
                "available": False,
                "error": "Docker daemon is not running. Please start Docker Desktop or the Docker service."
            }

        return {
            "available": False,
            "error": f"Docker returned error: {result.stderr.strip()}"
        }

    except FileNotFoundError:
        return {
            "available": False,
            "error": "Docker is not installed. Please install Docker to use --readyset-cache."
        }
    except subprocess.TimeoutExpired:
        return {
            "available": False,
            "error": "Docker command timed out. Docker may be unresponsive."
        }
    except Exception as e:
        return {
            "available": False,
            "error": f"Failed to check Docker availability: {str(e)}"
        }
