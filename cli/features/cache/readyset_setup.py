"""Shared Readyset container setup utilities."""

from __future__ import annotations

import socket
import subprocess  # nosemgrep: gitlab.bandit.B404
from pathlib import Path
from typing import Any, Dict, Tuple

from .readyset_container import (
    check_docker_available,
    check_readyset_container_status,
    format_docker_error,
)
from .readyset_workflow_functions import (
    DATABASE_SETUP_FUNCTIONS,
    READYSET_FUNCTIONS,
)
from shared.workflow_manager import get_workflow_components


def is_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("localhost", port))
            return True
    except OSError:
        return False


def find_available_port(
    start_port: int, max_attempts: int = 10, exclude: set | None = None
) -> int:
    """Find an available port starting from start_port."""
    exclude = exclude or set()
    for offset in range(max_attempts):
        port = start_port + offset
        if port not in exclude and is_port_available(port):
            return port
    raise RuntimeError(
        f"No available port found in range {start_port}-{start_port + max_attempts - 1}"
    )


def find_two_available_ports(
    port1_start: int, port2_start: int, max_attempts: int = 10
) -> Tuple[int, int]:
    """Find two available ports that don't overlap."""
    port1 = find_available_port(port1_start, max_attempts)
    port2 = find_available_port(port2_start, max_attempts, exclude={port1})
    return port1, port2


def setup_readyset_containers(
    target_name: str,
    target_config: Dict[str, Any],
    test_data_rows: int = 100,
    llm_model: str | None = None,
) -> Dict[str, Any]:
    """Set up test database and Readyset containers for a target database."""
    try:
        workflow_manager_class, default_functions, _ = get_workflow_components()

        docker_check = check_docker_available()
        if not docker_check.get("success"):
            return docker_check

        engine = target_config.get("engine", "postgresql").lower()
        if "database" not in target_config or not target_config["database"]:
            return {
                "success": False,
                "error": (
                    f"Database name not configured for target '{target_name}'. "
                    f"Please update your target configuration with "
                    f"'rdst configure edit --target {target_name}' and specify the "
                    "database name."
                ),
            }

        target_user = target_config.get(
            "user", "postgres" if engine == "postgresql" else "root"
        )

        if engine == "mysql":
            container_name = f"rdst-test-mysql-{target_name}"
            try:
                test_port, readyset_port = find_two_available_ports(3308, 3307)
            except RuntimeError as exc:
                return {
                    "success": False,
                    "error": f"No available ports for MySQL containers: {exc}",
                }
        else:
            container_name = f"rdst-test-psql-{target_name}"
            try:
                test_port, readyset_port = find_two_available_ports(5434, 5433)
            except RuntimeError as exc:
                return {
                    "success": False,
                    "error": f"No available ports for PostgreSQL containers: {exc}",
                }

        readyset_container_name = f"rdst-readyset-{target_name}"

        test_db_status = subprocess.run(
            ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        test_db_running = (
            test_db_status.returncode == 0 and container_name in test_db_status.stdout
        )

        readyset_status = check_readyset_container_status(
            readyset_container_name=readyset_container_name
        )
        readyset_running = readyset_status.get("running", False)

        if test_db_running and readyset_running:
            print(
                f"✓ Test database and Readyset containers already running for '{target_name}'"
            )

            test_port_result = subprocess.run(
                ["docker", "port", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            readyset_port_result = subprocess.run(
                ["docker", "port", readyset_container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )

            actual_test_port = test_port
            if test_port_result.returncode == 0:
                for line in test_port_result.stdout.strip().split("\n"):
                    if "->" in line:
                        actual_test_port = int(line.split(":")[-1])
                        break

            actual_readyset_port = readyset_port
            if readyset_port_result.returncode == 0:
                for line in readyset_port_result.stdout.strip().split("\n"):
                    if "->" in line:
                        actual_readyset_port = int(line.split(":")[-1])
                        break

            password = ""
            env_vars = (
                ["MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"]
                if engine == "mysql"
                else ["POSTGRES_PASSWORD", "PGPASSWORD"]
            )

            inspect_result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_name,
                    "--format",
                    "{{range .Config.Env}}{{println .}}{{end}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if inspect_result.returncode == 0:
                for line in inspect_result.stdout.strip().split("\n"):
                    for env_var in env_vars:
                        if line.startswith(f"{env_var}="):
                            password = line.split("=", 1)[1]
                            break
                    if password:
                        break

            target_database = target_config["database"]
            test_db_config = {
                "engine": engine,
                "host": "localhost",
                "port": actual_test_port,
                "database": target_database,
                "user": target_user,
                "password": password,
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
                "already_running": True,
            }

        workflow_functions = {
            **default_functions,
            **DATABASE_SETUP_FUNCTIONS,
            **READYSET_FUNCTIONS,
        }

        # __file__ is rdst/features/cache/readyset_setup.py, so .parent.parent.parent
        # is rdst/. The workflow JSON moved to shared/workflows/ during the
        # lib/ → features/+shared/ refactor (commit kvwuyrzr) but this path
        # was left behind, breaking /api/readyset/setup.
        workflow_path = (
            Path(__file__).resolve().parent.parent.parent
            / "shared"
            / "workflows"
            / "install_readyset_for_target.json"
        )
        if not workflow_path.exists():
            return {
                "success": False,
                "error": f"Readyset workflow file not found: {workflow_path}",
            }

        mgr = workflow_manager_class.from_file(str(workflow_path), resources=workflow_functions)
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

        print(f"Setting up test database and Readyset containers for '{target_name}'...")
        setup_result = mgr.run(readyset_input)
        if not setup_result:
            return {
                "success": False,
                "error": "Readyset setup workflow failed to return results",
            }

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
            "setup_result": setup_result,
        }
    except Exception as exc:
        return format_docker_error(str(exc), "Failed to setup Readyset containers")


def get_container_ports(engine: str, find_available: bool = False) -> tuple[int, int]:
    """Get the test database and Readyset ports for a database engine."""
    if engine.lower() == "mysql":
        default_test, default_readyset = 3308, 3307
    else:
        default_test, default_readyset = 5434, 5433

    if find_available:
        return find_available_port(default_test), find_available_port(default_readyset)
    return default_test, default_readyset


def get_container_names(target_name: str, engine: str) -> tuple[str, str]:
    """Get the test database and Readyset container names for a target."""
    if engine.lower() == "mysql":
        container_name = f"rdst-test-mysql-{target_name}"
    else:
        container_name = f"rdst-test-psql-{target_name}"

    readyset_container_name = f"rdst-readyset-{target_name}"
    return container_name, readyset_container_name
