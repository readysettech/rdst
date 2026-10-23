"""Agent connect: the prompt, RDST's MCP connection details, the progress feed, and /mcp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from features.agent_connect import discover, projects, scan, session, tools
from features.agent_connect.prompt import (
    claude_command,
    mcp_url,
    render_ask_prompt,
    render_describe_prompt,
    render_discover_prompt,
    render_fix_prompt,
    render_prompt,
    render_setup_prompt,
    render_triage_prompt,
)
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
    "connection URLs. When a monitored database shows no queries because nothing runs against "
    "it, offer to run read-only queries written from its schema so RDST has something to "
    "analyze; never on production, and only once the user says yes."
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


class DiscoverRequest(BaseModel):
    """Which detected agent searches, by its key (``claude:local``, ``codex:wsl``), and
    where the user said to look. With ``scan``, it looks for what that scan missed, and what
    it proposes joins the scan's list."""
    agent: str
    hint: str | None = None
    scan: str | None = None


class DescribeRequest(BaseModel):
    """A database the user knows of but RDST didn't find, in their words."""
    agent: str
    text: str


class AddCandidateRequest(BaseModel):
    candidate: str
    name: str | None = None


class SetupRequest(BaseModel):
    """What the agent sets up: found databases the user picked, by id, and databases already
    in RDST (one a provider import added) that still need getting ready."""
    candidates: list[str] = []
    targets: list[str] = []
    # Which agent sets them up; a search's own agent picks its session back up.
    agent: str | None = None
    # What went wrong when RDST tried to connect it itself, for the agent to start from.
    problem: str | None = None


class FixRequest(BaseModel):
    """Have an agent get databases already in RDST ready, by name, starting from what went
    wrong when RDST tried to connect."""
    agent: str
    targets: list[str]
    problem: str | None = None
    # The project the first one belongs to, when the caller knows it.
    folder: str | None = None


class ContinueRequest(BaseModel):
    """Open a run's agent session in its desktop app."""
    app: str


def _require_this_computer(request: Request) -> None:
    """Starting a process is for the person at this computer, never a remote browser."""
    require_local_request(request)
    if not is_loopback_request(request):
        raise HTTPException(403, "Only RDST on this computer can start a coding agent")


def _search_homes(agent: discover.Agent) -> list[str]:
    """Session folders on the other side of this computer that the agent should read too:
    Windows' for an agent in WSL, and WSL's for one on Windows."""
    if agent.side == "wsl":
        home = discover._wsl_path(Path.home())
        return [f"{home}/.claude", f"{home}/.codex"]
    if os.name == "nt" and discover.wsl_home:
        distro, home = discover.wsl_home
        share = f"\\\\wsl.localhost\\{distro}{home.replace('/', chr(92))}"
        return [f"{share}\\.claude", f"{share}\\.codex"]
    return []


def _free_name(base: str, also: set[str] | None = None) -> str:
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    taken = set(config.list_targets()) | (also or set())
    root = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-") or "database"
    if root not in taken:
        return root
    return next(f"{root}-{n}" for n in range(2, len(taken) + 3) if f"{root}-{n}" not in taken)


@router.get("/agent-connect/agents")
async def agent_connect_agents(request: Request, refresh: bool = False) -> dict:
    """The coding agents RDST can run to search for databases."""
    require_local_request(request)
    agents = await asyncio.to_thread(discover.detect, refresh)
    return {"agents": [agent.as_dict() for agent in agents],
            "apps": await asyncio.to_thread(discover.desktop_apps)}


@router.post("/agent-connect/agents/{key}/sign-in")
async def agent_connect_sign_in(request: Request, key: str) -> dict:
    """Open a terminal where the user signs the agent in; the next detection sees it."""
    _require_this_computer(request)
    agent = await asyncio.to_thread(discover.find_agent, key)
    if agent is None:
        raise HTTPException(404, "RDST can't find that agent on this computer.")
    try:
        await asyncio.to_thread(discover.sign_in, agent)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"opened": True}


def _base_for(request: Request, agent: discover.Agent) -> str:
    server = _server(request)
    base = server["wsl_url"] if agent.side == "wsl" else server["url"]
    if not base:
        raise HTTPException(409, "RDST can't reach WSL yet. Start WSL, then try again.")
    return base


@router.post("/agent-connect/discover")
async def agent_connect_discover(request: Request, body: DiscoverRequest) -> dict:
    """Start the agent searching the user's recent sessions for databases."""
    _require_this_computer(request)
    agent = await asyncio.to_thread(discover.find_agent, body.agent)
    if agent is None:
        raise HTTPException(404, "That coding agent isn't installed here.")
    try:
        recent = await asyncio.to_thread(projects.recent_projects, agent)
    except Exception:
        logger.debug("RDST couldn't list recent projects", exc_info=True)
        recent = []
    scanned = discover.get_run(body.scan) if body.scan else None
    if scanned is not None:
        # The agent makes sense of what RDST's scan found, in one conversation the setup
        # carries on: a short list, filled in and checked again before it shows.
        found = list(scanned.candidates or [])

        def finish(run: discover.Run) -> None:
            settled = discover.sanitize_candidates(
                scan.settle(run.candidates or [], found, run.skipped))
            shown = len(found[:60])
            mentioned = {item["found"] for item in run.candidates or []
                         if isinstance(item.get("found"), int)} | set(run.skipped)
            run.counts = {"proposed": len(run.candidates or []), "skipped": len(run.skipped),
                          "unmentioned": len(set(range(shown)) - mentioned)}
            discover.mark_saved(settled)
            run.candidates = settled

        try:
            requests = await asyncio.to_thread(projects.recent_requests)
        except Exception:
            logger.debug("RDST couldn't read what recent sessions were about", exc_info=True)
            requests = {}
        run = await discover.start(agent, render_triage_prompt(found, requests),
                                   mcp_url(_base_for(request, agent)),
                                   title="RDST: find your database",
                                   model=TRIAGE_MODEL.get(agent.id), finish=finish)
    else:
        prompt = render_discover_prompt(_search_homes(agent), recent, body.hint, agent.id)
        run = await discover.start(agent, prompt, mcp_url(_base_for(request, agent)))
    product_telemetry.agent_connect_step("search_started", attempt_id=run.id,
                                         tool=f"rdst_search_{agent.id}")
    return run.as_dict()


# The model a search runs on where the agent lets RDST pick: quick judgment matters more
# than depth for a short list.
TRIAGE_MODEL = {"claude": "sonnet"}


@router.post("/agent-connect/describe")
async def agent_connect_describe(request: Request, body: DescribeRequest) -> dict:
    """Have the user's agent find and set up a database they described."""
    _require_this_computer(request)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Say which database first.")
    agent = await asyncio.to_thread(discover.find_agent, body.agent)
    if agent is None:
        raise HTTPException(404, "That coding agent isn't installed here.")
    run = await discover.start(agent, render_describe_prompt(text),
                               mcp_url(_base_for(request, agent)), kind="setup")
    run.log("you", text)
    product_telemetry.agent_connect_step("setup_started", attempt_id=run.id,
                                         tool=f"rdst_describe_{agent.id}")
    return run.as_dict()


@router.post("/agent-connect/scan")
async def agent_connect_scan(request: Request) -> dict:
    """Start looking for databases on this computer without an agent. The run says where
    RDST is looking as each method finishes; poll it like a search."""
    require_local_request(request)
    run = discover.Run(id=f"scan-{secrets.token_hex(6)}", agent=None, kind="scan",
                       methods=[{"method": method, "label": label, "state": "running",
                                 "found": 0, "looked": [], "seconds": None}
                                for method, label, _ in scan.METHODS])

    def follow(steps: list[dict[str, Any]]) -> None:
        run.methods = steps

    async def work() -> None:
        try:
            # Detection learns where WSL's home is, which the scan reads too.
            await asyncio.to_thread(discover.detect)
            result = await asyncio.to_thread(scan.scan, follow)
            found = discover.sanitize_candidates(result["candidates"])
            discover.mark_saved(found)
            run.candidates = found
            run.methods = result["steps"]
            run.state = "done"
            product_telemetry.database_scan_finished(
                seconds=result["seconds"], candidates=found,
                methods={step["method"]: step["found"] for step in result["steps"]})
        except Exception:
            logger.exception("RDST's scan for databases failed")
            run.candidates = []
            run.state, run.message = "failed", "RDST couldn't finish looking."
        product_telemetry.agent_connect_step("scan_finished", attempt_id=run.id,
                                             ok=bool(run.candidates))

    discover.keep(run)
    run.task = asyncio.create_task(work())
    return run.as_dict()


def _run(run_id: str) -> discover.Run:
    run = discover.get_run(run_id)
    if run is None:
        raise HTTPException(404, "RDST has no search with that id.")
    return run


@router.get("/agent-connect/discover/{run_id}")
async def agent_connect_discover_status(request: Request, run_id: str) -> dict:
    require_local_request(request)
    run = _run(run_id)
    if run.state != "running" and run.candidates:
        # The user may have added or removed one since: say which RDST has now.
        await asyncio.to_thread(discover.mark_saved, run.candidates)
    return run.as_dict()


@router.delete("/agent-connect/discover/{run_id}")
async def agent_connect_discover_cancel(request: Request, run_id: str) -> dict:
    require_local_request(request)
    _run(run_id)
    run = await discover.cancel(run_id)
    return run.as_dict() if run else {}


@router.post("/agent-connect/discover/{run_id}/add")
async def agent_connect_add_candidate(request: Request, run_id: str,
                                      body: AddCandidateRequest) -> dict:
    """Add one database a scan or search found, by itself: RDST reads its URL or password
    where the scan saw it. The reply says whether RDST reached it, and why not when it
    didn't, so the user can sign in to its provider or hand it to their agent."""
    require_local_request(request)
    run = _run(run_id)
    candidate = next((item for item in run.candidates or [] if item["id"] == body.candidate),
                     None)
    if candidate is None:
        raise HTTPException(404, "That database isn't in this search's results.")
    name = _free_name(body.name or candidate.get("name") or candidate.get("database") or "")
    try:
        args = await asyncio.to_thread(scan.connection_args, candidate)
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    if args is None:
        raise HTTPException(409, "RDST needs this database's password. Fill in the form.")
    try:
        # RDST's own Connect: the find view shows what happens, so nothing goes to the
        # progress feed a pasted prompt's agent is followed by.
        added = await tools.add_database(request.app, {"name": name, **args}, announce=False)
    except tools.ConnectionFailed as exc:
        product_telemetry.agent_connect_step("found_connected", attempt_id=run.id,
                                             tool=f"rdst_connect_{candidate.get('provider')}",
                                             ok=False)
        return {"target": None, "added": False, "already_saved": False, "ok": False,
                "message": exc.reason, "category": exc.category}
    except tools.ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    target = added["target"]
    tested = await tools.test_database(request.app, {"name": target}, via="find",
                                       announce=False)
    ok = bool(tested.get("ok"))
    product_telemetry.agent_connect_step("found_connected", attempt_id=run.id,
                                         tool=f"rdst_connect_{candidate.get('provider')}",
                                         ok=ok)
    if not ok:
        # Only a database RDST reaches is kept, and nothing about this attempt is: the user
        # fixes it from the list. One already saved stays as the user had it.
        if added.get("added"):
            await _forget_target(target)
        return {"target": None if added.get("added") else target, "added": False,
                "already_saved": bool(added.get("already_saved")), "ok": False,
                "message": tested.get("message"), "category": tested.get("category")}
    if run.agent is not None and run.session:
        # The search's conversation knows this database: Fix with Claude picks it up.
        discover.remember_session(target, run.agent, run.session, run.folder)
    candidate["saved_as"] = target
    return {**added, "ok": ok, "message": None if ok else tested.get("message"),
            "category": tested.get("category"),
            "ready_to_monitor": tested.get("ready_to_monitor")}


async def _forget_target(name: str) -> None:
    from features.configure.service import ConfigureService

    try:
        async for _event in ConfigureService().remove_target(name):
            pass
    except Exception:
        logger.warning("RDST couldn't remove %s after it failed to connect", name,
                       exc_info=True)


@router.post("/agent-connect/discover/{run_id}/setup")
async def agent_connect_setup(request: Request, run_id: str, body: SetupRequest) -> dict:
    """Resume the search's session to set up the databases the user picked: add each, get it
    ready and turn monitoring on, the way the paste prompt has an agent do it."""
    _require_this_computer(request)
    search = _run(run_id)
    if search.kind not in ("search", "scan") or search.state == "running":
        raise HTTPException(409, "Wait for the search to finish first.")
    agent = (await asyncio.to_thread(discover.find_agent, body.agent) if body.agent
             else search.agent)
    if agent is None:
        raise HTTPException(404, "That coding agent isn't installed here.")
    found = {item["id"]: item for item in search.candidates or []}
    picked: list[tuple[str, dict | None]] = []
    taken: set[str] = set()
    for target in dict.fromkeys(body.targets):
        if tools._configured(target) is None:
            raise HTTPException(404, f"RDST has no database named {target}.")
        picked.append((target, None))
    for candidate_id in dict.fromkeys(body.candidates):
        candidate = found.get(candidate_id)
        if candidate is None:
            raise HTTPException(404, "That database isn't in this search's results.")
        name = _free_name(candidate.get("name") or candidate.get("database") or "", taken)
        taken.add(name)
        # Where it was found, as the agent opens it.
        picked.append((name, {**candidate, **{key: discover.agent_path(agent, candidate[key])
                                              for key in ("project", "source", "env_file",
                                                          "password_file")}}))
    if not picked:
        raise HTTPException(400, "Pick at least one database.")
    # The agent works in the project the database was found in. The search's conversation
    # carries on when it ran there too, unless the user took it to the agent's app.
    folder = discover.run_folder(agent, next((found["project"] for _, found in picked
                                              if found and found.get("project")), None))
    # The search's own conversation carries on into the setup, where it ran: it already
    # knows these databases. The user may have taken it to the agent's app instead.
    same = (search.agent is not None and search.agent.key == agent.key
            and search.session is not None and not discover.in_app(search.session))
    target_folder = folder or discover.work_folder(agent)
    if same and search.folder != target_folder:
        # The conversation moves to the picked project, where Claude Code resumes it.
        same = await asyncio.to_thread(discover.move_session, agent, search.session,
                                       search.folder or discover.work_folder(agent),
                                       target_folder)
    names = ", ".join(name for name, _ in picked)
    run = await discover.start(agent, render_setup_prompt(picked, body.problem),
                               mcp_url(_base_for(request, agent)), kind="setup",
                               resume=search.session if same else None,
                               folder=target_folder, title=f"RDST: set up {names}")
    run.log("rdst", "Setting up " + ", ".join(name for name, _ in picked))
    if not body.candidates:
        run.target = picked[0][0]
    product_telemetry.agent_connect_step("setup_started", attempt_id=run.id,
                                         tool=f"rdst_setup_{agent.id}")
    return run.as_dict()


@router.post("/agent-connect/setup")
async def agent_connect_fix(request: Request, body: FixRequest) -> dict:
    """Have the agent get databases already in RDST ready to monitor, picking up the
    conversation it had about the first one when there was one."""
    _require_this_computer(request)
    agent = await asyncio.to_thread(discover.find_agent, body.agent)
    if agent is None:
        raise HTTPException(404, "That coding agent isn't installed here.")
    targets = list(dict.fromkeys(body.targets))
    if not targets:
        raise HTTPException(400, "Name at least one database.")
    for target in targets:
        if tools._configured(target) is None:
            raise HTTPException(404, f"RDST has no database named {target}.")
    # The conversation that set the first one up, in the folder it ran in; a folder that's
    # gone means a new conversation, since Claude Code resumes a session only where it ran.
    earlier = discover.folder_for(targets[0], agent)
    session = discover.session_for(targets[0], agent)
    project = discover.run_folder(agent, discover.agent_path(agent, body.folder)
                                  if body.folder else None)
    folder = project or discover.run_folder(agent, earlier)
    resume = None
    if session and folder == earlier:
        resume = session
    elif session and project and earlier:
        # The conversation moves into the project, where Claude Code resumes it.
        moved = await asyncio.to_thread(discover.move_session, agent, session, earlier,
                                        project)
        resume = session if moved else None
    run = await discover.start(agent, render_setup_prompt([(name, None) for name in targets],
                                                          body.problem),
                               mcp_url(_base_for(request, agent)), kind="setup",
                               resume=resume, folder=folder,
                               title=f"RDST: set up {targets[0]}")
    run.target = targets[0]
    run.log("rdst", "Getting " + ", ".join(targets) + " ready")
    product_telemetry.agent_connect_step("setup_started", attempt_id=run.id,
                                         tool=f"rdst_setup_{agent.id}")
    return run.as_dict()


@router.get("/agent-connect/conversation/{target}")
async def agent_connect_conversation(request: Request, target: str) -> dict:
    """Where the conversation about a database is: in the Claude or Codex app once the user
    took it there, so fixing it carries on in that app."""
    require_local_request(request)
    entry = discover.conversation_of(target)
    if entry is None:
        return {"agent": None, "in_app": None}
    return {"agent": entry.get("agent"), "in_app": discover.in_app(entry.get("session"))}


class CheckTargetRequest(BaseModel):
    target: str


@router.post("/agent-connect/check-target")
async def agent_connect_check_target(request: Request, body: CheckTargetRequest) -> dict:
    """Whether RDST can connect to a database it already has, before the find view opens it:
    one that can't connect is fixed from the list, not from its page."""
    require_local_request(request)
    if tools._configured(body.target) is None:
        raise HTTPException(404, f"RDST has no database named {body.target}.")
    tested = await tools.test_database(request.app, {"name": body.target}, via="find",
                                       announce=False)
    ok = bool(tested.get("ok"))
    return {"target": body.target, "ok": ok, "message": None if ok else tested.get("message"),
            "category": tested.get("category")}


class FolderRequest(BaseModel):
    """A project the scan listed by name, for RDST to read now that the user picked it."""
    project: str


@router.post("/agent-connect/discover/{run_id}/folder")
async def agent_connect_folder(request: Request, run_id: str, body: FolderRequest) -> dict:
    """Read one project the scan only listed (on a Mac, one in Documents or another folder
    macOS asks about), and put what it names in the list in its place."""
    _require_this_computer(request)
    run = _run(run_id)
    waiting = next((c for c in run.candidates or [] if c.get("deferred") and
                    c.get("project") == body.project), None)
    if waiting is None:
        raise HTTPException(404, "RDST didn't list that project.")
    found = await asyncio.to_thread(scan.scan_folder, body.project)
    others = [c for c in run.candidates or [] if c is not waiting]
    kept = scan.rank(scan.merge([{**c, "method": "projects"} for c in found] or [{
        "name": waiting.get("name"), "engine": None, "host": None, "provider": "other",
        "project": body.project, "environment": "unknown", "incomplete": True,
        "found_by": "Nothing in the project named a database"}]))
    fresh = discover.sanitize_candidates([*others, *kept])
    discover.mark_saved(fresh)
    run.candidates = fresh
    return run.as_dict()


@router.post("/agent-connect/discover/{run_id}/continue")
async def agent_connect_continue(request: Request, run_id: str, body: ContinueRequest) -> dict:
    """Open the run's agent session in the user's Claude or Codex desktop app."""
    _require_this_computer(request)
    run = _run(run_id)
    url = discover.continue_url(run, body.app)
    if url is None:
        raise HTTPException(409, "That app can't open this session.")
    try:
        # The session RDST ran had its tools for that run only; the app needs them too.
        await asyncio.to_thread(discover.register, run.agent,
                                mcp_url(_base_for(request, run.agent)))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"RDST couldn't connect {run.agent.as_dict()['name']} to "
                                 f"itself: {exc}") from exc
    try:
        await asyncio.to_thread(discover.open_url, url)
    except OSError as exc:
        raise HTTPException(500, f"RDST couldn't open the app: {exc}") from exc
    discover.hand_off(run.session, body.app)
    return {"opened": True}


class OpenAppRequest(BaseModel):
    """Open a new conversation in a desktop app with the paste prompt typed in; with
    ``target``, the prompt that gets that database ready to monitor."""
    app: str
    attempt: str
    target: str | None = None


class FixPromptRequest(BaseModel):
    target: str


class AskOpenRequest(BaseModel):
    """Open the user's agent app to ask about ``target``."""
    app: str
    target: str


class ConnectRequest(BaseModel):
    """Add RDST to this agent's own MCP servers, by its key."""
    agent: str


def _require_target(target: str) -> None:
    if tools._configured(target) is None:
        raise HTTPException(404, f"RDST has no database named {target}.")


@router.post("/agent-connect/ask-prompt", response_model=PromptResponse)
async def agent_connect_ask_prompt(request: Request, body: FixPromptRequest) -> PromptResponse:
    """A paste-ready prompt that has the user's own agent answer questions about a database
    from what RDST found."""
    require_local_request(request)
    _require_target(body.target)
    server = _server(request)
    now = time.time()
    attempt = session.issue_attempt(now)
    product_telemetry.agent_connect_step(
        "prompt_issued", attempt_id=product_telemetry.agent_attempt_id(attempt),
        tool="rdst_ask_prompt")
    return PromptResponse(
        prompt=render_ask_prompt(server["url"], body.target, attempt, server["wsl_url"]),
        attempt=attempt, issued_at=int(now), url=server["url"],
        mcp_url=mcp_url(server["url"], attempt), wsl_url=server["wsl_url"])


@router.post("/agent-connect/connect")
async def agent_connect_connect(request: Request, body: ConnectRequest) -> dict:
    """Add RDST to the agent's own MCP servers, so any of its conversations can use RDST."""
    _require_this_computer(request)
    agent = await asyncio.to_thread(discover.find_agent, body.agent)
    if agent is None or agent.side != "local":
        raise HTTPException(404, "That coding agent isn't installed here.")
    try:
        await asyncio.to_thread(discover.register, agent, mcp_url(_base_for(request, agent)))
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(500, f"RDST couldn't add itself to "
                                 f"{agent.as_dict()['name']}: {exc}") from exc
    product_telemetry.agent_connect_step("agent_registered", attempt_id=None,
                                         tool=f"rdst_connect_{agent.id}")
    return {"connected": True, "agent": agent.as_dict()}


@router.post("/agent-connect/ask-open")
async def agent_connect_ask_open(request: Request, body: AskOpenRequest) -> dict:
    """Open the user's Claude or Codex app to ask about a database: the conversation that set
    it up when there was one, else a new one with the ask prompt typed in."""
    _require_this_computer(request)
    _require_target(body.target)
    if body.app not in await asyncio.to_thread(discover.desktop_apps_cached):
        raise HTTPException(404, "That app isn't installed here.")
    agent = await asyncio.to_thread(discover.find_agent, f"{body.app}:local")
    earlier = discover.session_for(body.target, agent, in_background=False) if agent else None
    server = _server(request)
    if agent and earlier:
        url = discover.continue_url(discover.Run(id="ask", agent=agent, session=earlier),
                                    body.app)
        await asyncio.to_thread(discover.register, agent, mcp_url(server["url"]))
        discover.hand_off(earlier, body.app)
    else:
        attempt = session.issue_attempt(time.time())
        url = discover.new_conversation_url(
            body.app, render_ask_prompt(server["url"], body.target, attempt, server["wsl_url"]))
    if url is None:
        raise HTTPException(409, "That app can't start a conversation from RDST.")
    try:
        await asyncio.to_thread(discover.open_url, url)
    except OSError as exc:
        raise HTTPException(500, f"RDST couldn't open the app: {exc}") from exc
    product_telemetry.agent_connect_step("app_opened", attempt_id=None,
                                         tool=f"rdst_ask_{body.app}")
    return {"opened": True, "resumed": bool(agent and earlier)}


@router.post("/agent-connect/fix-prompt", response_model=PromptResponse)
async def agent_connect_fix_prompt(request: Request, body: FixPromptRequest) -> PromptResponse:
    """A paste-ready prompt that has the user's own agent get a database already in RDST
    ready to monitor, and a new attempt the progress feed follows."""
    require_local_request(request)
    if tools._configured(body.target) is None:
        raise HTTPException(404, f"RDST has no database named {body.target}.")
    server = _server(request)
    now = time.time()
    attempt = session.issue_attempt(now)
    product_telemetry.agent_connect_step(
        "prompt_issued", attempt_id=product_telemetry.agent_attempt_id(attempt),
        tool="rdst_fix_prompt")
    return PromptResponse(
        prompt=render_fix_prompt(server["url"], body.target, attempt, server["wsl_url"]),
        attempt=attempt, issued_at=int(now), url=server["url"],
        mcp_url=mcp_url(server["url"], attempt), wsl_url=server["wsl_url"])


@router.post("/agent-connect/open-app")
async def agent_connect_open_app(request: Request, body: OpenAppRequest) -> dict:
    """Stage the paste prompt in a new conversation in the user's Claude or Codex app. The
    user sends it, and the prompt's attempt follows the agent the way a pasted one does."""
    _require_this_computer(request)
    if body.app not in await asyncio.to_thread(discover.desktop_apps_cached):
        raise HTTPException(404, "That app isn't installed here.")
    if not session.attempt_known(body.attempt):
        raise HTTPException(404, "RDST has no prompt with that id. Get a new one.")
    server = _server(request)
    if body.target is not None and tools._configured(body.target) is None:
        raise HTTPException(404, f"RDST has no database named {body.target}.")
    prompt = (render_fix_prompt(server["url"], body.target, body.attempt, server["wsl_url"])
              if body.target else render_prompt(server["url"], body.attempt, server["wsl_url"]))
    url = discover.new_conversation_url(body.app, prompt)
    if url is None:
        raise HTTPException(409, "That app can't start a conversation from RDST.")
    try:
        await asyncio.to_thread(discover.open_url, url)
    except OSError as exc:
        raise HTTPException(500, f"RDST couldn't open the app: {exc}") from exc
    product_telemetry.agent_connect_step(
        "app_opened", attempt_id=product_telemetry.agent_attempt_id(body.attempt),
        tool=f"rdst_app_{body.app}")
    return {"opened": True}


class ReplyRequest(BaseModel):
    """What the user answers the agent, in the same conversation."""
    text: str


REPLY_LIMIT = 2000


@router.post("/agent-connect/discover/{run_id}/reply")
async def agent_connect_reply(request: Request, run_id: str, body: ReplyRequest) -> dict:
    """Send the user's answer to a finished run's agent, continuing its conversation. A
    setup carries on with the same database."""
    _require_this_computer(request)
    previous = _run(run_id)
    text = body.text.strip()[:REPLY_LIMIT]
    if not text:
        raise HTTPException(400, "Type an answer first.")
    if previous.agent is None or not previous.session:
        raise HTTPException(409, "There's no conversation to answer yet.")
    if previous.state == "running":
        raise HTTPException(409, "Wait for the agent to finish first.")
    if app := discover.in_app(previous.session):
        where = "a terminal" if app == "terminal" else f"the {discover.AGENT_NAMES[app]} app"
        raise HTTPException(409, f"This conversation is open in {where} now, so answer it "
                                 "there.")
    run = await discover.start(previous.agent, text, mcp_url(_base_for(request, previous.agent)),
                               kind=previous.kind if previous.kind != "scan" else "setup",
                               resume=previous.session, folder=previous.folder,
                               finish=previous.finish)
    run.target = previous.target
    # One conversation on screen: what came before, then the answer.
    run.activity = list(previous.activity)
    run.log("you", text)
    product_telemetry.agent_connect_step("reply_sent", attempt_id=run.id,
                                         tool=f"rdst_{run.kind}_{previous.agent.id}")
    return run.as_dict()


@router.post("/agent-connect/discover/{run_id}/terminal")
async def agent_connect_terminal(request: Request, run_id: str) -> dict:
    """Open a terminal window that continues the run's agent conversation."""
    _require_this_computer(request)
    run = _run(run_id)
    if run.agent is None:
        raise HTTPException(409, "RDST looked by itself; there's no conversation to continue.")
    try:
        await asyncio.to_thread(discover.open_terminal, run)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(409, str(exc)) from exc
    # The terminal owns the conversation now, like an app does.
    if run.session:
        discover.hand_off(run.session, "terminal")
    return {"opened": True}


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
    try:
        payload = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    # A search RDST started names itself with d=; it isn't the agent the dialog waits for.
    # Nor is every session that has RDST registered: one without the prompt's a= counts
    # once it calls a tool, not when it merely starts up and lists them.
    search = request.query_params.get("d")
    nonce = request.query_params.get("a")
    calls = [item for item in (payload if isinstance(payload, list) else [payload])
             if isinstance(item, dict) and item.get("method") == "tools/call"]
    if not search and (nonce or calls) and session.agent_request(nonce):
        session.events.publish("connected")
        product_telemetry.agent_connect_step("connected", attempt_id=session.current_attempt(),
                                             ok=True)
    discover.current_run.set(search)
    session.run_source.set(search)
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
