"""Shared ReadySet workflow function registry."""

from __future__ import annotations

import json
from typing import Any, Dict

from .readyset_container import (
    check_docker_available,
    check_readyset_container_status,
    format_docker_error,
    start_readyset_container,
    start_readyset_container_direct,
    wait_for_readyset_ready,
    wait_for_readyset_ready_shallow,
)
from .readyset_explain_cache import explain_create_cache_readyset
from .target_setup import (
    create_test_db_target_config,
    detect_test_db_container,
    get_target_config,
    recreate_schema_from_target,
    return_target_config,
    start_test_db_container,
    wait_for_database_ready,
)
from .test_data_generator import (
    check_tables_have_data,
    generate_test_data_with_llm,
    get_test_database_schema,
    load_test_data_to_database,
)


def check_container_needs_start(
    container_detection: Dict[str, Any] | str | None = None, **kwargs
) -> Dict[str, Any]:
    """Check if a test database container needs to be started or created."""
    if isinstance(container_detection, str):
        container_detection = json.loads(container_detection)

    try:
        if not container_detection:
            return {
                "success": True,
                "needs_start": False,
                "needs_create": True,
                "message": "No container detection results provided",
            }

        exists = container_detection.get("exists", False)
        running = container_detection.get("running", False)

        if not exists:
            return {
                "success": True,
                "needs_start": False,
                "needs_create": True,
                "message": "Container needs to be created",
            }

        if exists and not running:
            return {
                "success": True,
                "needs_start": True,
                "needs_create": False,
                "message": "Container exists but is stopped",
            }

        return {
            "success": True,
            "needs_start": False,
            "needs_create": False,
            "message": "Container is already running",
        }

    except Exception as exc:
        return {
            "success": False,
            "needs_start": False,
            "needs_create": False,
            "error": f"Failed to check container status: {exc}",
        }


DATABASE_SETUP_FUNCTIONS = {
    "get_target_config": get_target_config,
    "detect_test_db_container": detect_test_db_container,
    "start_test_db_container": start_test_db_container,
    "wait_for_database_ready": wait_for_database_ready,
    "recreate_schema_from_target": recreate_schema_from_target,
    "create_test_db_target_config": create_test_db_target_config,
    "return_target_config": return_target_config,
    "check_container_needs_start": check_container_needs_start,
    "check_tables_have_data": check_tables_have_data,
    "get_test_database_schema": get_test_database_schema,
    "generate_test_data_with_llm": generate_test_data_with_llm,
    "load_test_data_to_database": load_test_data_to_database,
}

READYSET_FUNCTIONS = {
    "start_readyset_container": start_readyset_container,
    "wait_for_readyset_ready": wait_for_readyset_ready,
    "check_readyset_container_status": check_readyset_container_status,
    "check_docker_available": check_docker_available,
    "format_docker_error": format_docker_error,
    "explain_create_cache_readyset": explain_create_cache_readyset,
    "start_readyset_container_direct": start_readyset_container_direct,
    "wait_for_readyset_ready_shallow": wait_for_readyset_ready_shallow,
}


__all__ = [
    "DATABASE_SETUP_FUNCTIONS",
    "READYSET_FUNCTIONS",
    "check_container_needs_start",
]
