from __future__ import annotations

from typing import Dict, Any, Tuple
from pathlib import Path
import socket


"""
Shared ReadySet Container Setup Utilities

Provides reusable functions for setting up test database and ReadySet containers
across different commands (analyze, cache, etc.).
"""


def is_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', port))
            return True
    except OSError:
        return False


def find_available_port(start_port: int, max_attempts: int = 10, exclude: set = None) -> int:
    """
    Find an available port starting from start_port.

    Args:
        start_port: Port to start checking from
        max_attempts: Maximum number of ports to try (default: 10)
        exclude: Set of ports to skip (already allocated)

    Returns:
        An available port number

    Raises:
        RuntimeError: If no available port found within max_attempts
    """
    exclude = exclude or set()
    for offset in range(max_attempts):
        port = start_port + offset
        if port not in exclude and is_port_available(port):
            return port
    raise RuntimeError(f"No available port found in range {start_port}-{start_port + max_attempts - 1}")


def find_two_available_ports(port1_start: int, port2_start: int, max_attempts: int = 10) -> Tuple[int, int]:
    """
    Find two available ports that don't overlap.

    Args:
        port1_start: Starting port for first allocation
        port2_start: Starting port for second allocation
        max_attempts: Maximum attempts per port (default: 10)

    Returns:
        Tuple of (port1, port2) that are both available and different

    Raises:
        RuntimeError: If unable to find two non-overlapping available ports
    """
    # Find first port
    port1 = find_available_port(port1_start, max_attempts)

    # Find second port, excluding the first one
    port2 = find_available_port(port2_start, max_attempts, exclude={port1})

    return port1, port2


def setup_readyset_containers(
    target_name: str,
    target_config: Dict[str, Any],
    test_data_rows: int = 100,
    llm_model: str = None  # Use provider's default model
) -> Dict[str, Any]:
    """
    Set up test database and ReadySet containers for a target database.

    This function handles the complete workflow to:
    1. Start a test database container (PostgreSQL or MySQL) with the same schema as target
    2. Start a ReadySet container connected to the test database
    3. Wait for both containers to be ready
    4. Return configuration for connecting to both containers

    Args:
        target_name: Name of the target database
        target_config: Target database configuration dict
        test_data_rows: Number of test rows to generate (default: 100)
        llm_model: LLM model to use for schema analysis (default: provider's default)

    Returns:
        Dict containing:
            - success: bool - Whether setup succeeded
            - target_config: dict - Test database connection config
            - readyset_port: int - Port where ReadySet is listening
            - readyset_host: str - Host where ReadySet is running
            - container_name: str - Test database container name
            - readyset_container_name: str - ReadySet container name
            - error: str - Error message if setup failed (only if success=False)

    Example:
        >>> result = setup_readyset_containers("prod", prod_config)
        >>> if result["success"]:
        ...     test_db = result["target_config"]
        ...     readyset_port = result["readyset_port"]
    """
    try:
        from ..workflow_manager.workflow_manager import WorkflowManager, DEFAULT_FUNCTIONS
        from ..functions import DATABASE_SETUP_FUNCTIONS, READYSET_FUNCTIONS
        from ..functions.readyset_container import check_readyset_container_status
        import subprocess  # nosemgrep: gitlab.bandit.B404 - subprocess used for ReadySet container status checks only

        # Get database engine from target config
        engine = target_config.get("engine", "postgresql").lower()

        # Validate that database name is configured (required for --readyset-cache)
        if "database" not in target_config or not target_config["database"]:
            return {
                "success": False,
                "error": f"Database name not configured for target '{target_name}'. Please update your target configuration with 'rdst configure edit --target {target_name}' and specify the database name."
            }

        # Get user from target config
        target_user = target_config.get("user", "postgres" if engine == "postgresql" else "root")

        # Determine database-specific configuration with target-specific naming
        # Find available ports (try default, then increment if busy)
        # IMPORTANT: Find both ports together to ensure they don't overlap
        if engine == "mysql":
            container_name = f"rdst-test-mysql-{target_name}"
            try:
                test_port, readyset_port = find_two_available_ports(3308, 3307)
            except RuntimeError as e:
                return {
                    "success": False,
                    "error": f"No available ports for MySQL containers: {e}"
                }
        else:  # postgresql
            container_name = f"rdst-test-psql-{target_name}"
            try:
                test_port, readyset_port = find_two_available_ports(5434, 5433)
            except RuntimeError as e:
                return {
                    "success": False,
                    "error": f"No available ports for PostgreSQL containers: {e}"
                }

        # Prepare workflow input with target-specific container names
        readyset_container_name = f"rdst-readyset-{target_name}"

        # Check if containers are already running
        test_db_status = subprocess.run(
            ['docker', 'ps', '--filter', f'name={container_name}', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            timeout=5
        )
        test_db_running = test_db_status.returncode == 0 and container_name in test_db_status.stdout

        readyset_status = check_readyset_container_status(readyset_container_name=readyset_container_name)
        readyset_running = readyset_status.get("running", False)

        if test_db_running and readyset_running:
            print(f"✓ Test database and ReadySet containers already running for '{target_name}'")

            # Get actual ports from running containers
            test_port_result = subprocess.run(
                ['docker', 'port', container_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            readyset_port_result = subprocess.run(
                ['docker', 'port', readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Parse actual test DB port (format: "5432/tcp -> 0.0.0.0:5437")
            actual_test_port = test_port  # fallback
            if test_port_result.returncode == 0:
                for line in test_port_result.stdout.strip().split('\n'):
                    if '->' in line:
                        actual_test_port = int(line.split(':')[-1])
                        break

            # Parse actual ReadySet port
            actual_readyset_port = readyset_port  # fallback
            if readyset_port_result.returncode == 0:
                for line in readyset_port_result.stdout.strip().split('\n'):
                    if '->' in line:
                        actual_readyset_port = int(line.split(':')[-1])
                        break

            # Get password from running container
            # Docker postgres images use POSTGRES_PASSWORD, mysql uses MYSQL_ROOT_PASSWORD
            password = ""
            if engine == "mysql":
                env_vars = ["MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"]
            else:  # postgresql
                env_vars = ["POSTGRES_PASSWORD", "PGPASSWORD"]

            inspect_result = subprocess.run(
                ['docker', 'inspect', container_name, '--format', '{{range .Config.Env}}{{println .}}{{end}}'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if inspect_result.returncode == 0:
                for line in inspect_result.stdout.strip().split('\n'):
                    for env_var in env_vars:
                        if line.startswith(f"{env_var}="):
                            password = line.split('=', 1)[1]
                            break
                    if password:
                        break

            # Return existing configuration without running full workflow
            # Extract database name from target config
            target_database = target_config["database"]

            test_db_config = {
                "engine": engine,
                "host": "localhost",
                "port": actual_test_port,
                "database": target_database,
                "user": target_user,
                "password": password
            }

            return {
                "success": True,
                "target_config": test_db_config,
                "readyset_port": actual_readyset_port,
                "readyset_host": "localhost",
                "container_name": container_name,
                "readyset_container_name": readyset_container_name,
                "test_port": actual_test_port,
                "engine": engine,
                "already_running": True
            }

        # Set up workflow manager with ReadySet functions
        workflow_functions = {
            **DEFAULT_FUNCTIONS,
            **DATABASE_SETUP_FUNCTIONS,
            **READYSET_FUNCTIONS,
        }

        # Load the ReadySet setup workflow
        workflow_path = Path(__file__).parent.parent / "workflows" / "install_readyset_for_target.json"

        if not workflow_path.exists():
            return {
                "success": False,
                "error": f"ReadySet workflow file not found: {workflow_path}"
            }

        mgr = WorkflowManager.from_file(str(workflow_path), resources=workflow_functions)
        # Extract database name from target config, fallback to testdb
        target_database = target_config.get("database", "testdb")

        readyset_input = {
            "target_name": target_name,
            "target_config": target_config,
            "container_name_pattern": container_name,
            "default_database": target_database,
            "default_user": target_user,
            "default_port": test_port,
            "readyset_port": readyset_port,
            "readyset_container_name": readyset_container_name,
            "test_data_rows": test_data_rows,
            "llm_model": llm_model,
        }

        # Run the ReadySet setup workflow
        print(f"Setting up test database and ReadySet containers for '{target_name}'...")
        setup_result = mgr.run(readyset_input)

        if not setup_result:
            return {
                "success": False,
                "error": "ReadySet setup workflow failed to return results"
            }

        # Extract configuration from workflow results
        test_db_config = setup_result.get("target_config", {})

        return {
            "success": True,
            "target_config": test_db_config,
            "readyset_port": readyset_port,
            "readyset_host": "localhost",
            "container_name": container_name,
            "readyset_container_name": readyset_container_name,
            "test_port": test_port,
            "engine": engine,
            "setup_result": setup_result  # Include full result for advanced use cases
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to setup ReadySet containers: {str(e)}"
        }


def get_container_ports(engine: str, find_available: bool = False) -> tuple[int, int]:
    """
    Get the test database and ReadySet ports for a given database engine.

    Args:
        engine: Database engine ("mysql" or "postgresql")
        find_available: If True, find available ports starting from defaults

    Returns:
        Tuple of (test_db_port, readyset_port)

    Example:
        >>> test_port, readyset_port = get_container_ports("postgresql")
        >>> print(test_port, readyset_port)  # 5434, 5433

        >>> # Find available ports if defaults are busy
        >>> test_port, readyset_port = get_container_ports("postgresql", find_available=True)
    """
    if engine.lower() == "mysql":
        default_test, default_readyset = 3308, 3307
    else:  # postgresql
        default_test, default_readyset = 5434, 5433

    if find_available:
        return find_available_port(default_test), find_available_port(default_readyset)
    return default_test, default_readyset


def get_container_names(target_name: str, engine: str) -> tuple[str, str]:
    """
    Get the container names for test database and ReadySet containers.

    Args:
        target_name: Name of the target database
        engine: Database engine ("mysql" or "postgresql")

    Returns:
        Tuple of (test_db_container_name, readyset_container_name)

    Example:
        >>> db_name, rs_name = get_container_names("prod", "postgresql")
        >>> print(db_name, rs_name)  # rdst-test-psql-prod, rdst-readyset-prod
    """
    if engine.lower() == "mysql":
        container_name = f"rdst-test-mysql-{target_name}"
    else:  # postgresql
        container_name = f"rdst-test-psql-{target_name}"

    readyset_container_name = f"rdst-readyset-{target_name}"

    return container_name, readyset_container_name
