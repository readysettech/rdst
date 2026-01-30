import asyncio
import json
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .target_guard import TargetGuard, require_target_optional, require_target_body

router = APIRouter(prefix="/readyset", tags=["readyset"])


class SetupRequest(BaseModel):
    target: Optional[str] = None


class CacheRequest(BaseModel):
    query: str
    target: Optional[str] = None


class ContainerStatus(BaseModel):
    running: bool
    test_db_running: bool
    readyset_running: bool
    readyset_port: Optional[int] = None
    test_db_port: Optional[int] = None
    target: Optional[str] = None


@router.get("/status")
async def get_status(guard: TargetGuard = Depends(require_target_optional)) -> ContainerStatus:
    import subprocess

    target_name, target_config = guard

    engine = target_config.get("engine", "postgresql").lower()
    container_name = (
        f"rdst-test-{'mysql' if engine == 'mysql' else 'psql'}-{target_name}"
    )
    readyset_container_name = f"rdst-readyset-{target_name}"

    test_db_status = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={container_name}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    test_db_running = (
        test_db_status.returncode == 0 and container_name in test_db_status.stdout
    )

    readyset_status = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            f"name={readyset_container_name}",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    readyset_running = (
        readyset_status.returncode == 0
        and readyset_container_name in readyset_status.stdout
    )

    test_db_port = None
    readyset_port = None

    if test_db_running:
        port_result = subprocess.run(
            ["docker", "port", container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if port_result.returncode == 0:
            for line in port_result.stdout.strip().split("\n"):
                if "->" in line:
                    test_db_port = int(line.split(":")[-1])
                    break

    if readyset_running:
        port_result = subprocess.run(
            ["docker", "port", readyset_container_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if port_result.returncode == 0:
            for line in port_result.stdout.strip().split("\n"):
                if "->" in line:
                    readyset_port = int(line.split(":")[-1])
                    break

    return ContainerStatus(
        running=test_db_running and readyset_running,
        test_db_running=test_db_running,
        readyset_running=readyset_running,
        readyset_port=readyset_port,
        test_db_port=test_db_port,
        target=target_name,
    )


async def _setup_generator(
    target_name: str, target_config: dict
) -> AsyncGenerator[dict, None]:
    from ...cli.readyset_setup import setup_readyset_containers

    yield {
        "event": "progress",
        "data": json.dumps(
            {"stage": "starting", "message": "Setting up Readyset containers..."}
        ),
    }

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: setup_readyset_containers(
            target_name=target_name,
            target_config=target_config,
            test_data_rows=100,
            llm_model=None,
        ),
    )

    if not result.get("success"):
        yield {
            "event": "error",
            "data": json.dumps({"message": result.get("error", "Setup failed")}),
        }
        return

    yield {
        "event": "complete",
        "data": json.dumps(
            {
                "success": True,
                "readyset_port": result.get("readyset_port"),
                "test_db_port": result.get("test_port"),
                "already_running": result.get("already_running", False),
            }
        ),
    }


@router.post("/setup")
async def setup_containers(request: SetupRequest, guard: TargetGuard = Depends(require_target_body)):
    return EventSourceResponse(_setup_generator(guard.target_name, guard.target_config))


async def _explain_generator(
    query: str, target_name: str, target_config: dict
) -> AsyncGenerator[dict, None]:
    from ...cli.readyset_setup import setup_readyset_containers
    from ...functions.readyset_explain_cache import explain_create_cache_readyset
    import os

    yield {
        "event": "progress",
        "data": json.dumps(
            {"stage": "setup", "message": "Checking Readyset containers..."}
        ),
    }

    loop = asyncio.get_event_loop()
    setup_result = await loop.run_in_executor(
        None,
        lambda: setup_readyset_containers(
            target_name=target_name,
            target_config=target_config,
            test_data_rows=100,
            llm_model=None,
        ),
    )

    if not setup_result.get("success"):
        yield {
            "event": "error",
            "data": json.dumps(
                {"message": setup_result.get("error", "Container setup failed")}
            ),
        }
        return

    readyset_port = setup_result.get("readyset_port")
    test_db_config = setup_result.get("target_config", {})

    password = target_config.get("password", "")
    password_env = target_config.get("password_env")
    if password_env:
        password = os.environ.get(password_env, "")
    if not test_db_config.get("password"):
        test_db_config["password"] = password

    yield {
        "event": "progress",
        "data": json.dumps(
            {"stage": "explaining", "message": "Running EXPLAIN CREATE CACHE..."}
        ),
    }

    explain_result = await loop.run_in_executor(
        None,
        lambda: explain_create_cache_readyset(
            query=query,
            readyset_port=readyset_port,
            test_db_config=test_db_config,
        ),
    )

    yield {
        "event": "complete",
        "data": json.dumps(
            {
                "success": explain_result.get("success", False),
                "cacheable": explain_result.get("cacheable", False),
                "confidence": explain_result.get("confidence", "unknown"),
                "explanation": explain_result.get("explanation", ""),
                "issues": explain_result.get("issues", []),
                "readyset_port": readyset_port,
            }
        ),
    }


@router.post("/explain")
async def explain_cache(request: CacheRequest, guard: TargetGuard = Depends(require_target_body)):
    return EventSourceResponse(
        _explain_generator(request.query, guard.target_name, guard.target_config)
    )


async def _create_cache_generator(
    query: str, target_name: str, target_config: dict
) -> AsyncGenerator[dict, None]:
    from ...cli.readyset_setup import setup_readyset_containers
    from ...functions.readyset_explain_cache import (
        create_cache_readyset,
        get_cache_id_for_query,
    )
    import os

    yield {
        "event": "progress",
        "data": json.dumps(
            {"stage": "setup", "message": "Checking Readyset containers..."}
        ),
    }

    loop = asyncio.get_event_loop()
    setup_result = await loop.run_in_executor(
        None,
        lambda: setup_readyset_containers(
            target_name=target_name,
            target_config=target_config,
            test_data_rows=100,
            llm_model=None,
        ),
    )

    if not setup_result.get("success"):
        yield {
            "event": "error",
            "data": json.dumps(
                {"message": setup_result.get("error", "Container setup failed")}
            ),
        }
        return

    readyset_port = setup_result.get("readyset_port")
    test_db_config = setup_result.get("target_config", {})

    password = target_config.get("password", "")
    password_env = target_config.get("password_env")
    if password_env:
        password = os.environ.get(password_env, "")
    if not test_db_config.get("password"):
        test_db_config["password"] = password

    yield {
        "event": "progress",
        "data": json.dumps(
            {"stage": "creating", "message": "Creating cache in Readyset..."}
        ),
    }

    create_result = await loop.run_in_executor(
        None,
        lambda: create_cache_readyset(
            query=query,
            readyset_port=readyset_port,
            test_db_config=test_db_config,
        ),
    )

    cache_id = None
    if create_result.get("cached"):
        cache_id = await loop.run_in_executor(
            None,
            lambda: get_cache_id_for_query(
                query, readyset_port, test_db_config
            ),
        )

    yield {
        "event": "complete",
        "data": json.dumps(
            {
                "success": create_result.get("success", False),
                "cached": create_result.get("cached", False),
                "cache_id": cache_id,
                "message": create_result.get("message", ""),
                "error": create_result.get("error"),
                "readyset_port": readyset_port,
            }
        ),
    }


@router.post("/cache")
async def create_cache(request: CacheRequest, guard: TargetGuard = Depends(require_target_body)):
    return EventSourceResponse(
        _create_cache_generator(request.query, guard.target_name, guard.target_config)
    )
