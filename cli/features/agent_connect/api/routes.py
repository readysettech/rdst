"""Agent connect: the prompt, RDST's MCP connection details, the progress feed, and /mcp."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.agent_connect import session, tools
from features.agent_connect.prompt import claude_command, mcp_url, render_prompt
from shared import product_telemetry
from shared.api.guards import is_loopback_host, is_loopback_request, require_local_request

router = APIRouter()
mcp_router = APIRouter()
logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "RDST is Readyset's database monitoring app running on this computer. These tools save a "
    "database connection (the password goes to the OS keyring, never to a remote server), "
    "test it, turn on monitoring, and read what RDST finds. Never print passwords or "
    "connection URLs."
)


class PromptResponse(BaseModel):
    prompt: str
    # Names this prompt in telemetry; its MCP URL carries it as ?a=.
    attempt: str
    issued_at: int
    url: str
    mcp_url: str
    wsl_url: str | None = None


class McpConfigResponse(BaseModel):
    """How an agent reaches RDST's MCP server, for Settings."""
    url: str
    mcp_url: str
    command: str
    wsl_url: str | None = None


def _server(request: Request) -> dict[str, Any]:
    """This server's advertised addresses, falling back to the address the caller used."""
    info = getattr(request.app.state, "agent_connect", None) or {}
    url = info.get("url") or str(request.base_url).rstrip("/")
    return {"url": url, "wsl_url": info.get("wsl_url")}


@router.post("/agent-connect/prompt", response_model=PromptResponse)
async def agent_connect_prompt(request: Request) -> PromptResponse:
    """A paste-ready prompt carrying RDST's MCP URL, and a new attempt."""
    require_local_request(request)
    server = _server(request)
    now = time.time()
    attempt = session.issue_attempt(now)
    product_telemetry.agent_connect_step(
        "prompt_issued", attempt_id=product_telemetry.agent_attempt_id(attempt))
    return PromptResponse(
        prompt=render_prompt(server["url"], attempt, server["wsl_url"]),
        attempt=attempt, issued_at=int(now), url=server["url"],
        mcp_url=mcp_url(server["url"], attempt), wsl_url=server["wsl_url"])


@router.get("/agent-connect/mcp-config", response_model=McpConfigResponse)
async def agent_connect_mcp_config(request: Request) -> McpConfigResponse:
    """RDST's MCP URL and the Claude Code command that registers it."""
    require_local_request(request)
    server = _server(request)
    endpoint = mcp_url(server["url"])
    return McpConfigResponse(url=server["url"], mcp_url=endpoint,
                             command=claude_command(endpoint), wsl_url=server["wsl_url"])


class RecheckRequest(BaseModel):
    target: str


@router.post("/agent-connect/recheck")
async def agent_connect_recheck(request: Request, body: RecheckRequest) -> dict:
    """Run the monitoring prerequisites again and publish tested or needs_setup for the UI.

    The reply is rdst_check_prerequisites' result, so the modal can continue from that step.
    """
    require_local_request(request)
    via = product_telemetry.normalize_via(request.headers.get("x-rdst-via")) or "agent"
    try:
        return await tools.check_prerequisites(request.app, {"name": body.target}, via=via)
    except tools.ToolError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/agent-connect/events")
async def agent_connect_events(request: Request, since: float | None = None):
    """Agent progress as unnamed SSE frames of {step, target?, message?, ts}.

    needs_setup frames also carry check, source, ready_to_monitor, blocking_reason, next_step,
    instructions, sql, needs_restart and prerequisites (the dialog's prerequisite result).

    `since` (epoch seconds, e.g. the prompt's issued_at) replays events from then on;
    Last-Event-ID resumes after that event.
    """
    require_local_request(request)
    previous = request.headers.get("last-event-id")
    since_id = int(previous) if previous and previous.isdecimal() else None
    subscriber, backlog = session.events.subscribe(since_id=since_id, since_ts=since)

    def frame(event: dict) -> dict:
        data = {key: value for key, value in event.items() if key != "id"}
        return {"id": str(event["id"]), "data": json.dumps(data)}

    async def stream():
        try:
            for event in backlog:
                yield frame(event)
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                yield frame(event)
        finally:
            session.events.unsubscribe(subscriber)

    return EventSourceResponse(stream(), ping=15)


def _rpc_error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _server_version() -> str:
    from shared.api.routes.status import _get_version

    return _get_version()


async def _handle(request: Request, message: Any) -> dict | None:
    """One JSON-RPC message; None for notifications and responses, which get no reply."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid Request")
    method = message.get("method")
    id_ = message.get("id")
    if method is None or "id" not in message:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_PROTOCOL_VERSIONS else \
            SUPPORTED_PROTOCOL_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "rdst", "title": "RDST", "version": _server_version()},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": tools.tool_list()}}
    if method == "tools/call":
        name = params.get("name")
        try:
            result = await tools.call_tool(request.app, name, params.get("arguments"))
        except KeyError:
            return _rpc_error(id_, -32602, f"Unknown tool: {name}")
        except Exception:
            logger.exception("MCP tool %s failed", name)
            result = {"content": [{"type": "text", "text": "RDST hit an unexpected error "
                                   "running this tool. Its log has the details."}],
                      "isError": True}
        return {"jsonrpc": "2.0", "id": id_, "result": result}
    return _rpc_error(id_, -32601, f"Method not found: {method}")


def _hostname(url: str | None) -> str:
    """The host ``url`` names (a Host header value may skip the scheme), or ""."""
    if url and "//" not in url:
        url = f"//{url}"
    try:
        return (urlsplit(url or "").hostname or "").rstrip(".")
    except ValueError:
        return ""


def _authorize(request: Request) -> None:
    """Admit local agents only: no browser pages, no other machines, no rebound DNS names.

    An Authorization header is ignored, so agents registered with one keep working.
    """
    wsl_url = _server(request)["wsl_url"]
    wsl_host = _hostname(wsl_url)
    host = _hostname(request.headers.get("host"))
    # Only an address of this server: a DNS-rebound name reaching 127.0.0.1 is refused.
    if not (is_loopback_host(host) or (wsl_host and host == wsl_host)):
        raise HTTPException(403, "Host not allowed")
    if not is_loopback_request(request):
        # The WSL listener's clients are the WSL VM; anything else stays loopback-only.
        server = request.scope.get("server") or ("",)
        if not wsl_host or host != wsl_host or str(server[0]) != wsl_host:
            raise HTTPException(403, "RDST's MCP endpoint only answers this computer")
    origin = request.headers.get("origin")
    if origin and not is_loopback_host(_hostname(origin)):
        # A web page can't drive /mcp: browsers always send their Origin here.
        raise HTTPException(403, "Origin not allowed")


@mcp_router.post("/mcp", include_in_schema=False)
async def mcp_post(request: Request) -> Response:
    """Streamable HTTP MCP: JSON-RPC in the body, a JSON reply (no server-initiated stream).

    The `a` query parameter names the prompt the agent's URL came from; others are ignored.
    """
    _authorize(request)
    if session.agent_request(request.query_params.get("a")):
        session.events.publish("connected")
        product_telemetry.agent_connect_step("connected", attempt_id=session.current_attempt(),
                                             ok=True)
    try:
        payload = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    if isinstance(payload, list):
        replies = [reply for reply in [await _handle(request, item) for item in payload]
                   if reply is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await _handle(request, payload)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)


@mcp_router.get("/mcp", include_in_schema=False)
async def mcp_get(request: Request) -> Response:
    _authorize(request)
    # No server-initiated messages: the spec's answer is 405 on the GET stream.
    return Response(status_code=405, headers={"Allow": "POST"})


@mcp_router.delete("/mcp", include_in_schema=False)
async def mcp_delete(request: Request) -> Response:
    _authorize(request)
    return Response(status_code=405, headers={"Allow": "POST"})
