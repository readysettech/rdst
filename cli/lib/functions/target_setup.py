from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404 - subprocess required for Docker/database operations
import json
import time
from typing import Dict, Any


def get_target_config(target_name: str = None, **kwargs) -> Dict[str, Any]:
    """
    Get the target database configuration from rdst config.

    Args:
        target_name: Name of the target to get config for
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing target database configuration
    """
    try:
        from ..cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()

        # Get target name
        if not target_name:
            target_name = cfg.get_default()

        if not target_name:
            return {
                "success": False,
                "error": "No target specified and no default configured"
            }

        # Get target config
        target_config = cfg.get(target_name)
        if not target_config:
            return {
                "success": False,
                "error": f"Target '{target_name}' not found"
            }

        return {
            "success": True,
            "engine": target_config.get("engine", "postgresql"),
            "host": target_config.get("host"),
            "port": target_config.get("port"),
            "database": target_config.get("database"),
            "user": target_config.get("user"),
            "password_env": target_config.get("password_env"),
            "target_name": target_name
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to get target config: {str(e)}"
        }


def detect_test_db_container(
    container_name_pattern: str = "rdst-test-db",
    required: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """
    Detect test database Docker container.

    Args:
        container_name_pattern: Pattern to match container names
        required: Whether a container is required
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing container detection results
    """
    try:
        # Check running containers
        result = subprocess.run(
            ['docker', 'ps', '--filter', f'name={container_name_pattern}', '--format', '{{.Names}}\t{{.ID}}\t{{.Status}}'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": "Failed to check Docker containers",
                "container_name": None,
                "running": False,
                "exists": False
            }

        # Check if running container found
        if result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        return {
                            "success": True,
                            "container_name": parts[0],
                            "container_id": parts[1],
                            "running": True,
                            "exists": True
                        }

        # Check stopped containers
        result = subprocess.run(
            ['docker', 'ps', '-a', '--filter', f'name={container_name_pattern}', '--format', '{{.Names}}\t{{.ID}}\t{{.Status}}'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        return {
                            "success": True,
                            "container_name": parts[0],
                            "container_id": parts[1],
                            "running": False,
                            "exists": True
                        }

        # No container found
        return {
            "success": True,
            "container_name": None,
            "container_id": None,
            "running": False,
            "exists": False
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to detect container: {str(e)}",
            "container_name": None,
            "running": False,
            "exists": False
        }


def start_test_db_container(
    container_name: str = None,
    needs_start: bool | str = False,
    needs_create: bool | str = False,
    target_config: Dict[str, Any] = None,
    test_port: int | str = 5434,
    container_name_pattern: str = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Start or create a test database Docker container.

    Args:
        container_name: Name of the container
        needs_start: Whether to start existing container
        needs_create: Whether to create new container
        target_config: Target database configuration
        test_port: Port to expose for test database
        container_name_pattern: Pattern to use for container name when creating
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing container start results
    """
    try:
        # Parse target config from JSON if needed
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        # Convert string booleans
        if isinstance(needs_start, str):
            needs_start = needs_start.lower() in ('true', '1', 'yes')
        if isinstance(needs_create, str):
            needs_create = needs_create.lower() in ('true', '1', 'yes')

        test_port = int(test_port)

        # Already running
        if not needs_start and not needs_create:
            return {
                "success": True,
                "container_name": container_name,
                "created": False,
                "started": False,
                "message": "Container already running"
            }

        # Start existing container
        if needs_start and container_name:
            print(f"Starting test database container: {container_name}...")
            result = subprocess.run(
                ['docker', 'start', container_name],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return {
                    "success": True,
                    "container_name": container_name,
                    "created": False,
                    "started": True
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to start container: {result.stderr}",
                    "container_name": container_name,
                    "created": False,
                    "started": False
                }

        # Create new container
        if needs_create:
            if not target_config or not target_config.get("engine"):
                return {
                    "success": False,
                    "error": "No target database configuration provided",
                    "container_name": None,
                    "created": False,
                    "started": False
                }

            engine = target_config.get("engine", "postgresql")
            target_user = target_config.get("user", "postgres")
            target_db = target_config.get("database", "testdb")

            # Get target password from environment
            import os
            target_password_env = target_config.get("password_env")
            target_password = os.getenv(target_password_env, "testpassword")

            # Use provided container_name_pattern or fall back to passed container_name or default
            if container_name_pattern:
                container_name = container_name_pattern
            elif not container_name:
                container_name = "rdst-test-db"

            print(f"Creating test {engine} container: {container_name}...")

            # Determine Docker image and environment based on engine
            if engine == "postgresql":
                image = "postgres:15"
                env_vars = [
                    '-e', f'POSTGRES_PASSWORD={target_password}',
                    '-e', f'POSTGRES_USER={target_user}',
                    '-e', f'POSTGRES_DB={target_db}'
                ]
                port_mapping = f'{test_port}:5432'
                # PostgreSQL command args for ReadySet logical replication
                pg_args = [
                    'postgres',
                    '-c', 'wal_level=logical',
                    '-c', 'max_replication_slots=10',
                    '-c', 'max_wal_senders=10'
                ]
            elif engine == "mysql":
                # Use MySQL 8.0 and rely on caching_sha2_password authentication (default)
                image = "mysql:8.0"
                env_vars = [
                    '-e', f'MYSQL_ROOT_PASSWORD={target_password}',
                    '-e', f'MYSQL_DATABASE={target_db}',
                    '-e', f'MYSQL_USER={target_user}',
                    '-e', f'MYSQL_PASSWORD={target_password}'
                ]
                port_mapping = f'{test_port}:3306'
                # MySQL command args for binlog (ReadySet replication)
                pg_args = [
                    '--binlog-format=ROW',
                    '--gtid-mode=ON',
                    '--enforce-gtid-consistency=ON'
                ]
            else:
                return {
                    "success": False,
                    "error": f"Unsupported database engine: {engine}",
                    "container_name": None,
                    "created": False,
                    "started": False
                }

            # Create container
            docker_cmd = [
                'docker', 'run',
                '-d',
                '--name', container_name,
                '-p', port_mapping,
                '--add-host=host.docker.internal:host-gateway',  # Allow container to reach host (for Linux)
            ] + env_vars + [image] + pg_args

            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                # For MySQL, wait for it to initialize and update authentication + privileges
                if engine == "mysql":
                    print(f"Waiting for MySQL to initialize...")

                    # Wait for MySQL to actually be ready using mysqladmin ping
                    max_wait = 60  # seconds
                    start_time = time.time()
                    mysql_ready = False

                    while (time.time() - start_time) < max_wait:
                        ping_result = subprocess.run(
                            ['docker', 'exec', container_name, 'mysqladmin', 'ping', '-h', '127.0.0.1', '--silent'],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if ping_result.returncode == 0:
                            mysql_ready = True
                            print(f"✓ MySQL is ready (waited {int(time.time() - start_time)}s)")
                            break
                        time.sleep(2)

                    if not mysql_ready:
                        print(f"⚠️  Warning: MySQL did not become ready in {max_wait}s, proceeding anyway...")

                    # Update authentication plugin and grant replication privileges
                    # These need to succeed for ReadySet to work
                    alter_cmds = [
                        f"ALTER USER 'root'@'%' IDENTIFIED WITH caching_sha2_password BY '{target_password}';",
                        f"ALTER USER '{target_user}'@'%' IDENTIFIED WITH caching_sha2_password BY '{target_password}';",
                        f"GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO '{target_user}'@'%';",
                        f"GRANT REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'root'@'%';",
                        "FLUSH PRIVILEGES;"
                    ]

                    for cmd in alter_cmds:
                        cmd_result = subprocess.run(
                            ['docker', 'exec', container_name, 'mysql', '-uroot', f'-p{target_password}', '-e', cmd],
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if cmd_result.returncode != 0:
                            print(f"⚠️  Warning: MySQL command failed: {cmd}")
                            print(f"   Error: {cmd_result.stderr[:200]}")

                return {
                    "success": True,
                    "container_name": container_name,
                    "created": True,
                    "started": True
                }
            else:
                return {
                    "success": False,
                    "error": f"Failed to create container: {result.stderr}",
                    "container_name": container_name,
                    "created": False,
                    "started": False
                }

        return {
            "success": False,
            "error": "Unexpected state",
            "container_name": container_name,
            "created": False,
            "started": False
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to start/create container: {str(e)}",
            "container_name": container_name,
            "created": False,
            "started": False
        }


def wait_for_database_ready(
    container_name: str = None,
    port: int | str = 5434,
    database_type: str = "postgresql",
    timeout: int | str = 30,
    **kwargs
) -> Dict[str, Any]:
    """
    Wait for database to be ready to accept connections.

    Args:
        container_name: Name of the container
        port: Port database is listening on
        database_type: Type of database (postgresql or mysql)
        timeout: Maximum time to wait in seconds
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing readiness status
    """
    try:
        port = int(port)
        timeout = int(timeout)

        if not container_name:
            return {
                "success": False,
                "ready": False,
                "error": "No container name provided"
            }

        print(f"Waiting for {database_type} to be ready...")

        start_time = time.time()
        while (time.time() - start_time) < timeout:
            # Check if container is still running
            ps_result = subprocess.run(
                ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if ps_result.returncode != 0 or container_name not in ps_result.stdout:
                return {
                    "success": False,
                    "ready": False,
                    "error": f"Container {container_name} not running"
                }

            # Try to connect
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()

                if result == 0:
                    # Port is open, wait a bit more for DB to fully initialize
                    time.sleep(2)
                    print(f"✓ {database_type} is ready!")
                    return {
                        "success": True,
                        "ready": True,
                        "wait_time": time.time() - start_time
                    }
            except Exception:
                pass

            time.sleep(1)

        return {
            "success": False,
            "ready": False,
            "error": f"Database did not become ready within {timeout}s"
        }

    except Exception as e:
        return {
            "success": False,
            "ready": False,
            "error": f"Failed to wait for database: {str(e)}"
        }


def recreate_schema_from_target(
    target_config: Dict[str, Any] = None,
    test_container: str = None,
    test_port: int | str = 5434,
    test_database: str = "testdb",
    **kwargs
) -> Dict[str, Any]:
    """
    Recreate schema from target database into test database.

    Args:
        target_config: Target database configuration
        test_container: Name of test database container
        test_port: Port of test database
        test_database: Name of test database
        **kwargs: Additional workflow parameters

    Returns:
        Dict containing schema recreation results
    """
    try:
        # Parse target config from JSON if needed
        if isinstance(target_config, str):
            target_config = json.loads(target_config)

        test_port = int(test_port)

        engine = target_config.get("engine", "postgresql")
        target_host = target_config.get("host")
        target_port = target_config.get("port")
        target_db = target_config.get("database")
        target_user = target_config.get("user")
        target_password_env = target_config.get("password_env")

        # Get target password from environment
        import os
        target_password = os.getenv(target_password_env, "")

        print(f"Recreating schema from {target_host}:{target_port}/{target_db}...")

        # Translate localhost to host.docker.internal for Docker container access
        # pg_dump runs inside the test container, so localhost would refer to the container itself
        docker_target_host = target_host
        if target_host in ('localhost', '127.0.0.1'):
            docker_target_host = 'host.docker.internal'

        if engine == "postgresql":
            # Try Docker-based pg_dump first (more reliable)
            try:
                print("Using Docker container tools for schema dump...")
                dump_cmd = [
                    'docker', 'exec',
                    '-e', f'PGPASSWORD={target_password}',
                    test_container,
                    'pg_dump',
                    '-h', docker_target_host,
                    '-p', str(target_port),
                    '-U', target_user,
                    '-d', target_db,
                    '--schema-only',
                    '--no-owner',
                    '--no-privileges'
                ]

                dump_result = subprocess.run(
                    dump_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if dump_result.returncode != 0:
                    return {
                        "success": False,
                        "error": f"Failed to dump schema: {dump_result.stderr}"
                    }

                schema_sql = dump_result.stdout

            except Exception as e:
                # Fall back to local pg_dump
                print("Docker approach failed, trying local pg_dump...")
                try:
                    dump_cmd = [
                        'pg_dump',
                        '-h', target_host,
                        '-p', str(target_port),
                        '-U', target_user,
                        '-d', target_db,
                        '--schema-only',
                        '--no-owner',
                        '--no-privileges'
                    ]

                    dump_env = os.environ.copy()
                    dump_env['PGPASSWORD'] = target_password

                    dump_result = subprocess.run(
                        dump_cmd,
                        env=dump_env,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )

                    if dump_result.returncode != 0:
                        return {
                            "success": False,
                            "error": f"Failed to dump target schema: {dump_result.stderr}"
                        }

                    schema_sql = dump_result.stdout

                except FileNotFoundError:
                    return {
                        "success": False,
                        "error": "PostgreSQL tools (pg_dump/psql) not found. Please install PostgreSQL client tools."
                    }

            # Apply schema to test database using docker exec
            restore_cmd = [
                'docker', 'exec', '-i', test_container,
                'psql',
                '-U', target_user,
                '-d', test_database
            ]

            restore_result = subprocess.run(
                restore_cmd,
                input=schema_sql,
                capture_output=True,
                text=True,
                timeout=120
            )

            if restore_result.returncode != 0:
                # Check if it's just warnings
                if "ERROR" in restore_result.stderr:
                    return {
                        "success": False,
                        "error": f"Failed to restore schema: {restore_result.stderr}"
                    }

            print("✓ Schema recreated successfully")

            return {
                "success": True,
                "schema_recreated": True,
                "tables_count": schema_sql.count("CREATE TABLE")
            }

        elif engine == "mysql":
            # Try Docker-based mysqldump first
            try:
                print("Using Docker container tools for schema dump...")
                dump_cmd = [
                    'docker', 'exec', test_container,
                    'mysqldump',
                    '-h', docker_target_host,
                    '-P', str(target_port),
                    '-u', target_user,
                    f'-p{target_password}',
                    '--no-data',
                    '--skip-add-drop-table',
                    target_db
                ]

                dump_result = subprocess.run(
                    dump_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

                if dump_result.returncode != 0:
                    return {
                        "success": False,
                        "error": f"Failed to dump schema: {dump_result.stderr}"
                    }

                schema_sql = dump_result.stdout

            except Exception as e:
                # Fall back to local mysqldump
                print("Docker approach failed, trying local mysqldump...")
                try:
                    dump_cmd = [
                        'mysqldump',
                        '-h', target_host,
                        '-P', str(target_port),
                        '-u', target_user,
                        f'-p{target_password}',
                        '--no-data',
                        '--skip-add-drop-table',
                        target_db
                    ]

                    dump_result = subprocess.run(
                        dump_cmd,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )

                    if dump_result.returncode != 0:
                        return {
                            "success": False,
                            "error": f"Failed to dump target schema: {dump_result.stderr}"
                        }

                    schema_sql = dump_result.stdout

                except FileNotFoundError:
                    return {
                        "success": False,
                        "error": "MySQL tools (mysqldump/mysql) not found. Please install MySQL client tools."
                    }

            # Apply schema to test database using docker exec
            restore_cmd = [
                'docker', 'exec', '-i', test_container,
                'mysql',
                '-u', 'root',
                f'-p{target_password}',
                test_database
            ]

            restore_result = subprocess.run(
                restore_cmd,
                input=schema_sql,
                capture_output=True,
                text=True,
                timeout=120
            )

            if restore_result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to restore schema: {restore_result.stderr}"
                }

            print("✓ Schema recreated successfully")

            return {
                "success": True,
                "schema_recreated": True,
                "tables_count": schema_sql.count("CREATE TABLE")
            }

        else:
            return {
                "success": False,
                "error": f"Unsupported database engine: {engine}"
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Schema dump/restore timed out"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to recreate schema: {str(e)}"
        }


def create_test_db_target_config(
    container_name: str = None,
    database: str = "testdb",
    user: str = "postgres",
    port: int | str = 5434,
    host: str = "localhost",
    engine: str = "postgresql",
    target_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a target configuration for test database.

    Args:
        container_name: Name of the test database container
        database: Database name
        user: Database user
        port: Database port
        host: Database host
        engine: Database engine type
        target_config: Target database configuration (to get password)
        **kwargs: Additional workflow parameters

    Returns:
        Target configuration dict
    """
    import os

    port = int(port)

    # Parse target config from JSON if needed
    if isinstance(target_config, str):
        target_config = json.loads(target_config)

    # Get password from target config
    password = ""
    if target_config:
        password_env = target_config.get("password_env")
        if password_env:
            password = os.getenv(password_env, "testpassword")

    return {
        "success": True,
        "engine": engine,
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        "tls": False,
        "container_name": container_name,
        "is_test_db": True
    }


def return_target_config(
    temp_target_config: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Simple pass-through to rename temp_target_config to target_config.

    Args:
        temp_target_config: The temporary target config
        **kwargs: Additional workflow parameters

    Returns:
        The same config with success flag
    """
    # Parse if it's a JSON string
    if isinstance(temp_target_config, str):
        temp_target_config = json.loads(temp_target_config)

    if not temp_target_config:
        return {"success": False, "error": "No config provided"}

    # Return the config as-is
    return temp_target_config
