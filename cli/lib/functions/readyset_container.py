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
    Start ReadySet container connected to test database.

    ReadySet will snapshot from the test database and cache queries.

    Args:
        test_db_container: Name of test database container
        test_db_config: Test database configuration
        readyset_port: Port to expose ReadySet on
        readyset_container_name: Name for ReadySet container
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing ReadySet container status
    """
    try:
        # Parse test_db_config if it's a JSON string
        if isinstance(test_db_config, str):
            test_db_config = json.loads(test_db_config)

        readyset_port = int(readyset_port)

        # Check if ReadySet container already exists
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

        # Build target database URL for ReadySet
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
                print(f"✓ ReadySet container already running: {readyset_container_name}")
                return {
                    "success": True,
                    "container_name": readyset_container_name,
                    "readyset_url": f"{readyset_url_protocol}://localhost:{readyset_port}",
                    "port": readyset_port,
                    "already_running": True
                }
            elif len(parts) >= 1:
                # Container exists but not running, start it
                print(f"Starting existing ReadySet container...")
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
                        print(f"✓ Successfully started existing ReadySet container")
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
                "error": f"Unsupported database engine for ReadySet: {engine}"
            }

        print(f"Creating ReadySet container: {readyset_container_name}...")
        print(f"  Database Type: {db_type}")
        print(f"  Target DB: {db_type}://host.docker.internal:{port}/{database}")
        print(f"  ReadySet port: {readyset_port}")

        # Create and start ReadySet container using the same pattern as control_plane
        docker_cmd = [
            'docker', 'run',
            '-d',
            '--name', readyset_container_name,
            '-e', f'DATABASE_TYPE={db_type}',
            '-e', f'DATABASE_URL={target_db_url}',
            '-e', f'DATABASE_PORT={readyset_port}',
            '-p', f'{readyset_port}:{readyset_port}',
            '--add-host=host.docker.internal:host-gateway',  # Allow container to reach host
            'public.ecr.aws/g3d8h1n9/readyset/readyset-base-build:latest'
        ]

        print(f"Starting docker run (this may take a while if pulling image)...")
        result = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=300  # Increased from 60s to 300s (5 min) for large image pulls
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()

            # Check for ECR authentication errors
            # Common patterns: "no basic auth", "unauthorized", "authentication required",
            # "failed to resolve reference", "unable to find image" (when not authenticated)
            is_ecr_auth_error = any([
                "no basic auth credentials" in error_msg.lower(),
                "denied: your authorization token has expired" in error_msg.lower(),
                "unauthorized" in error_msg.lower(),
                "authentication required" in error_msg.lower(),
                ("failed to resolve reference" in error_msg.lower() and "public.ecr.aws" in error_msg.lower()),
                ("unable to find image" in error_msg.lower() and "public.ecr.aws" in error_msg.lower())
            ])

            if is_ecr_auth_error:
                error_message = (
                    "Failed to pull ReadySet image from ECR - Authentication required.\n"
                    "\n"
                    "Please authenticate with AWS ECR:\n"
                    "  aws ecr-public get-login-password --region us-east-1 | \\\n"
                    "    docker login --username AWS --password-stdin public.ecr.aws\n"
                    "\n"
                    f"Original error: {error_msg}"
                )
                print(f"\n❌ {error_message}\n")
                raise Exception(error_message)

            # Generic docker error
            error_message = f"Failed to create ReadySet container: {error_msg}"
            print(f"\n❌ {error_message}\n")
            raise Exception(error_message)

        print("✓ ReadySet container created, waiting for initialization...")

        # Give container a moment to start
        time.sleep(2)

        # Test connectivity from inside the container to the database (optional diagnostic)
        print(f"Testing connectivity from ReadySet container to {db_type}://host.docker.internal:{port}...")

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
            print(f"✓ ReadySet container can reach test database")
        else:
            print(f"⚠️  Warning: ReadySet container cannot reach test database")
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

            print(f"\n⚠️  ReadySet container crashed immediately after creation")
            print(f"Container logs:\n{crash_logs}\n")

            return {
                "success": False,
                "error": f"ReadySet container crashed immediately. Logs:\n{crash_logs[:1000]}",
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
            "error": "ReadySet container creation timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to start ReadySet container: {str(e)}"
        }


def wait_for_readyset_ready(
    readyset_container_name: str = "rdst-readyset",
    timeout: int | str = 120,
    **kwargs
) -> Dict[str, Any]:
    """
    Wait for ReadySet to finish snapshotting and be ready for queries.

    Args:
        readyset_container_name: Name of ReadySet container
        timeout: Maximum time to wait in seconds
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing readiness status
    """
    try:
        timeout = int(timeout)
        start_time = time.time()

        print("Waiting for ReadySet snapshot to complete...")

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

                    # Check for ECR auth issues in logs
                    if "no basic auth credentials" in logs.lower() or \
                       "unauthorized" in logs.lower() or \
                       "authentication required" in logs.lower():
                        error_message = (
                            f"ReadySet container failed to start - ECR authentication required.\n"
                            f"Container status: {status}\n"
                            "Please run: aws ecr-public get-login-password --region us-east-1 | "
                            "docker login --username AWS --password-stdin public.ecr.aws"
                        )
                        if logs:
                            error_message += f"\n\nContainer logs:\n{logs[-500:]}"
                        print(f"\n❌ {error_message}\n")
                        raise Exception(error_message)

                    error_message = f"ReadySet container {readyset_container_name} not running (status: {status})"
                    if logs:
                        error_message += f"\n\nContainer logs:\n{logs[-500:]}"
                    print(f"\n❌ {error_message}\n")
                    raise Exception(error_message)

                error_message = f"ReadySet container {readyset_container_name} not running"
                print(f"\n❌ {error_message}\n")
                raise Exception(error_message)

            # Check ReadySet logs for "Streaming replication started" message
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
                    print("✓ ReadySet snapshot complete and ready!")
                    return {
                        "success": True,
                        "ready": True,
                        "wait_time": time.time() - start_time
                    }

            # Also check for errors in ReadySet logs (not Grafana)
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
                error_message = f"ReadySet encountered errors during initialization:\n" + "\n".join(error_lines[-10:])
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
            f"ReadySet did not become ready within {timeout}s.\n"
            f"Container logs (last 30 lines):\n{logs}\n\n"
            f"Check full logs: docker logs {readyset_container_name}"
        )
        print(f"\n❌ {error_message}\n")
        raise Exception(error_message)

    except subprocess.TimeoutExpired as e:
        error_message = f"Timeout waiting for ReadySet: {str(e)}"
        print(f"\n❌ {error_message}\n")
        raise Exception(error_message)
    except Exception:
        # Re-raise exceptions we already formatted
        raise


def check_readyset_container_status(
    readyset_container_name: str = "rdst-readyset",
    **kwargs
) -> Dict[str, Any]:
    """
    Check if ReadySet container exists and is running.

    Args:
        readyset_container_name: Name of ReadySet container
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
            "error": f"Failed to check ReadySet container status: {str(e)}"
        }
