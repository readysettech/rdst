"""MCP tools served at /mcp. Each one calls the same handler as the matching HTTP route."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from features.agent_connect import session

logger = logging.getLogger(__name__)

ABOUT = ("RDST is Readyset's database monitoring app, running locally on this computer; "
         "database passwords stay in this computer's OS keyring and are never returned.")


class ToolError(Exception):
    """A tool failure reported to the agent as an MCP tool error."""


class NeedsSetup(ToolError):
    """Monitoring refused for the reasons the Turn on monitoring dialog would block it."""

    def __init__(self, target: str, state: dict[str, Any]) -> None:
        self.target = target
        self.state = state
        steps = "\n".join(f"- {line}" for line in state.get("instructions") or [])
        super().__init__(
            f"Monitoring is NOT on for {target}. {state.get('blocking_reason')}\n"
            f"Show the user these steps as written:\n{steps}\nAsk before running any of "
            "them. Once they're done, run rdst_check_prerequisites again and turn monitoring "
            "on when it reports ready_to_monitor: true.\n\n"
            + json.dumps(jsonable_encoder(state), default=str))


def internal_request(app: FastAPI, path: str = "/mcp") -> Request:
    """A loopback request for calling a route handler in-process on the agent's behalf.

    /mcp's local-request checks already admitted the call; ``x-rdst-via`` attributes it.
    """
    return Request({"type": "http", "method": "POST", "path": path, "app": app,
                    "headers": [(b"x-rdst-via", b"mcp")], "client": ("127.0.0.1", 0)})


def _detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)


async def _route(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a route handler (sync handlers off the loop), mapping HTTP errors to tool errors."""
    try:
        if asyncio.iscoroutinefunction(call):
            return await call(*args, **kwargs)
        return await asyncio.to_thread(call, *args, **kwargs)
    except HTTPException as exc:
        raise ToolError(_detail(exc)) from exc


def _require(args: dict, name: str) -> Any:
    value = args.get(name)
    if value in (None, ""):
        raise ToolError(f"{name} is required")
    return value


def _configured(name: str) -> dict | None:
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    return config.get(name)


def _require_target(args: dict, key: str = "name") -> str:
    name = str(_require(args, key))
    if _configured(name) is None:
        raise ToolError(f"RDST has no database named {name}. rdst_status lists them.")
    return name


def _read_env_file(path: str, variable: str) -> str:
    """One variable's value from a dotenv-style file, read here so it never passes the agent."""
    file = Path(path).expanduser()
    if not file.is_absolute():
        raise ToolError("env_file must be an absolute path")
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError(f"RDST couldn't read {file.name}: {exc.strerror or exc}") from exc
    for line in lines:
        text = line.strip()
        if text.startswith("export "):
            text = text[len("export "):].lstrip()
        key, sep, value = text.partition("=")
        if not sep or key.strip() != variable:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        return value.split(" #", 1)[0].strip()
    raise ToolError(f"{variable} isn't set in {file.name}")


def _connection_fields(args: dict) -> dict[str, Any]:
    from shared.config.targets import default_port_for, normalize_db_type, parse_connection_string

    fields = {**(args.get("fields") or {}), **{key: args[key] for key in (
        "engine", "host", "port", "database", "user", "password", "tls") if key in args}}
    url = args.get("url")
    if args.get("env_file") or args.get("env_var"):
        url = _read_env_file(str(_require(args, "env_file")), str(_require(args, "env_var")))
    if url:
        scheme, sep, rest = str(url).partition("://")
        # SQLAlchemy-style driver suffixes (postgresql+psycopg2://) name the same database.
        url = f"{scheme.split('+', 1)[0]}{sep}{rest}"
        try:
            parsed = parse_connection_string(url)
        except ValueError as exc:
            # The message names the problem, never the URL itself.
            raise ToolError(f"RDST couldn't read that connection URL: {exc}") from exc
        fields = {**{key: parsed[key] for key in ("engine", "host", "port", "database", "user",
                                                  "tls")},
                  **({"password": parsed["password"]} if parsed.get("password") else {}),
                  **{key: value for key, value in fields.items() if value not in (None, "")}}
    engine = normalize_db_type(fields.get("engine") or "postgresql")
    if engine not in ("postgresql", "mysql"):
        raise ToolError("RDST monitors PostgreSQL and MySQL databases")
    for key in ("host", "database", "user"):
        if not fields.get(key):
            raise ToolError(f"Pass url, env_file and env_var, or {key} with the other fields")
    fields["engine"] = engine
    fields["port"] = int(fields.get("port") or default_port_for(engine))
    return fields


async def add_database(app: FastAPI, args: dict) -> dict:
    from features.configure.api import routes as configure
    from shared.secret_store_service import SecretStoreService

    name = str(_require(args, "name"))
    fields = _connection_fields(args)
    password = fields.get("password")
    try:
        body = configure.AddTargetRequest(name=name, target=configure.TargetData(**fields))
    except ValidationError as exc:
        # Field names and reasons only: the input values can include the host or password.
        problems = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
                             for error in exc.errors())
        raise ToolError(f"Those connection details aren't valid: {problems}") from exc
    result = await _route(configure.add_target, internal_request(app), body)
    if not getattr(result, "success", False):
        raise ToolError(getattr(result, "message", None) or "RDST couldn't add the database")
    target = getattr(result, "target_name", None) or name
    persisted = getattr(result, "persisted", None)
    if persisted is None:
        if password is None:
            persisted = True
        else:
            saved = _configured(target) or {}
            env = saved.get("password_env")
            persisted = bool(env) and await asyncio.to_thread(
                SecretStoreService().get_secret, env) == password
    session.events.publish("database_added", target=target)
    reply = {"target": target, "added": True, "persisted": bool(persisted),
             "message": getattr(result, "message", None)}
    if not persisted:
        reply["warning"] = ("The OS keyring isn't available, so RDST keeps this password only "
                            "until it restarts. Add it again in RDST's Settings to keep it.")
    return reply


async def prerequisites(name: str) -> dict[str, Any]:
    """The Turn on monitoring dialog's prerequisite result for ``name``, with the verdict.

    A check that can't run blocks, the same as the dialog's unreachable state.
    """
    from features.agent_connect import readiness
    from features.configure.prerequisites import redact

    try:
        return await readiness.check(name)
    except HTTPException as exc:
        error = _detail(exc)
    except Exception as exc:
        logger.debug("The prerequisite check for %s didn't run", name, exc_info=True)
        error = redact(str(exc).strip() or type(exc).__name__)
    return readiness.summarize({"target": name, "engine": _engine_of(name), "ready": False,
                                "checks": [], "connection_error": error})


def _engine_of(name: str) -> str | None:
    try:
        return (_configured(name) or {}).get("engine")
    except Exception:
        return None


# What the UI needs to show the same state as the manual Turn on monitoring dialog.
_SETUP_FIELDS = ("check", "source", "ready_to_monitor", "blocking_reason", "next_step",
                 "instructions", "sql", "needs_restart")


def report_prerequisites(name: str, state: dict[str, Any], via: str | None) -> None:
    """Publish tested or needs_setup for the UI, and count each check's outcome."""
    from shared import product_telemetry

    if state["ready_to_monitor"]:
        session.events.publish("tested", target=name)
    else:
        prereqs = {key: value for key, value in state.items()
                   if key not in _SETUP_FIELDS and key != "failed"}
        session.events.publish("needs_setup", target=name,
                               **{key: state.get(key) for key in _SETUP_FIELDS},
                               prerequisites=prereqs)
    attempt = session.current_attempt() if via in ("mcp", "agent") else None
    for check in state.get("checks") or []:
        product_telemetry.database_add_prereq_result(
            state.get("engine"), check=str(check.get("id")), ok=check.get("status") == "ok",
            via=via, attempt_id=attempt, scope=name)


async def _setup_passed(app: FastAPI, name: str) -> None:
    """A database forced into monitoring before its setup was done gets read right away."""
    try:
        from features.monitor.loop import setup_check_passed

        await setup_check_passed(name, getattr(app.state, "monitor_scheduler", None))
    except Exception:
        logger.debug("Couldn't refresh monitoring for %s", name, exc_info=True)


async def check_prerequisites(app: FastAPI, args: dict, via: str | None = "mcp") -> dict:
    name = _require_target(args)
    state = await prerequisites(name)
    report_prerequisites(name, state, via)
    if state["ready_to_monitor"]:
        await _setup_passed(app, name)
    return state


async def test_database(app: FastAPI, args: dict, via: str = "mcp") -> dict:
    from features.configure.api import routes as configure

    name = _require_target(args)
    response = await _route(configure.test_connection, internal_request(app), name, None)
    final: dict[str, Any] | None = None
    async for frame in response.body_iterator:
        if not isinstance(frame, dict) or not frame.get("data"):
            continue
        kind, event = frame.get("event"), json.loads(frame["data"])
        if kind == "connection_test" and event.get("status") != "in_progress":
            final = event
        elif kind == "error":
            final = {"status": "failed", "message": event.get("message")}
    if final is None:
        final = {"status": "failed", "message": "RDST didn't get a result from the test"}
    ok = final.get("status") == "success"
    reply = {"target": name, "ok": ok,
             **{key: final.get(key) for key in ("message", "server_version", "category",
                                                "privileges")
                if final.get(key) is not None}}
    if not ok:
        reply.update(ready_to_monitor=False, next_step="fix_connection",
                     blocking_reason=f"RDST couldn't connect: {final.get('message')}")
        session.events.publish("error", target=name, message=final.get("message"))
        return reply
    state = await check_prerequisites(app, {"name": name}, via)
    reply.update({key: state.get(key) for key in _SETUP_FIELDS})
    reply["prerequisites"] = {key: state.get(key) for key in (
        "engine", "provider", "provider_name", "ready", "checks", "connection_error")}
    return reply


async def _put_settings(app: FastAPI, name: str, update: dict) -> dict:
    from features.monitor.api import routes as monitor

    try:
        body = monitor.MonitorSettingsUpdate(**update)
    except ValueError as exc:
        raise ToolError(f"Those settings aren't valid: {exc}") from exc
    return await _route(monitor.put_settings, name, body, internal_request(app))


async def _require_ready(name: str) -> None:
    state = await prerequisites(name)
    if not state["ready_to_monitor"]:
        raise NeedsSetup(name, state)


AWAITING_USER = ("RDST is showing the Turn on monitoring screen. Ask the user to read it and "
                 "click Turn on monitoring in the RDST app.")
AWAITING_SIGNIN = ("RDST is showing the Turn on monitoring screen. Monitoring's AI is free with "
                   "a Readyset account, so ask the user to sign up or sign in there and turn "
                   "monitoring on in the RDST app.")


def _signed_in() -> bool:
    from shared import account_session

    try:
        return account_session.is_signed_in_locally()
    except Exception:
        return False


def _needs_user(name: str) -> str | None:
    """Why the user turns monitoring on in the app, or None when the agent may.

    The first time, the Turn on monitoring dialog explains what RDST reads and runs,
    including EXPLAIN ANALYZE. Signed out, it asks the user to sign in for monitoring's AI.
    Once the user has turned a database on, the agent may turn it back on.
    """
    from features.monitor.api import routes as monitor

    if monitor._store().levers(name)["enabled"] or monitor.monitoring_approved(name):
        return None
    return AWAITING_USER if _signed_in() else AWAITING_SIGNIN


async def _await_user(name: str) -> dict | None:
    """Hand the turn-on to the user: the app opens its dialog for ``name``."""
    message = await asyncio.to_thread(_needs_user, name)
    if message is None:
        return None
    session.events.publish("awaiting_user", target=name, message=message)
    return {"target": name, "enabled": False, "waiting_for_user": True,
            "signed_in": message is not AWAITING_SIGNIN,
            "message": f"{message} Then call rdst_status until {name} shows monitoring: true."}


async def enable_monitoring(app: FastAPI, args: dict) -> dict:
    name = _require_target(args)
    await _require_ready(name)
    if waiting := await _await_user(name):
        return waiting
    # Deep analysis defaults on, the same as turning monitoring on in the app.
    update: dict[str, Any] = {"enabled": True, "deep_analysis": True}
    for key in ("deep_analysis", "analysis_mode"):
        if args.get(key) is not None:
            update[key] = args[key]
    values = await _put_settings(app, name, update)
    session.events.publish("monitoring_enabled", target=name)
    return {"target": name, **values}


async def disable_monitoring(app: FastAPI, args: dict) -> dict:
    name = _require_target(args)
    values = await _put_settings(app, name, {"enabled": False})
    session.events.publish("settings_changed", target=name)
    return {"target": name, **values}


async def get_monitor_settings(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    return await _route(monitor.settings, _require_target(args))


async def update_monitor_settings(app: FastAPI, args: dict) -> dict:
    name = _require_target(args)
    update = {key: value for key, value in (args.get("settings") or {}).items()
              if key != "dev_learning_period_s"}
    if not update:
        raise ToolError("Pass the settings to change in settings")
    if update.get("enabled") is True:
        await _require_ready(name)
        if waiting := await _await_user(name):
            return waiting
    values = await _put_settings(app, name, update)
    session.events.publish("settings_changed", target=name)
    return {"target": name, **values}


async def status(app: FastAPI, args: dict) -> dict:
    from features.account.api import routes as account
    from features.monitor import database_limit
    from features.monitor.api import routes as monitor

    def local() -> dict:
        from shared.password_resolver import resolve_password

        store = monitor._store()
        monitored = store.monitored_targets()
        targets = []
        for entry in monitor._configured_targets():
            name = str(entry.get("name"))
            targets.append({
                "name": name, "engine": entry.get("engine"),
                "has_password": resolve_password(entry).available,
                "monitoring": name in monitored,
                "ai_monitoring": (database_limit.display_state(store, name)
                                  if name in monitored else None)})
        return {"targets": targets}

    reply = await asyncio.to_thread(local)
    try:
        account_status = await asyncio.wait_for(
            account.account_status(internal_request(app)), timeout=10)
        data = jsonable_encoder(account_status)
        reply["ai_access"] = {key: data.get(key) for key in ("signed_in", "detail")
                              if key in data}
    except Exception:
        reply["ai_access"] = {"detail": "RDST couldn't check AI access right now"}
    reply["own_api_key"] = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return reply


async def list_inbox(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    limit = max(1, min(int(args.get("limit") or 25), 100))
    page = await _route(monitor.inbox, state=args.get("state") or "open",
                        target=args.get("target"), include_muted=False, include_digest=False,
                        incidents=False)
    items = page["items"]
    return {"items": items[:limit], "total": len(items)}


async def get_inbox_item(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    return await _route(monitor.inbox_item, int(_require(args, "id")))


async def inbox_action(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    body = monitor.InboxAction(until=args.get("until"))
    verb = str(_require(args, "verb"))
    if verb not in ("seen", "snooze", "dismiss", "resolve", "reopen"):
        raise ToolError("verb must be seen, snooze, dismiss, resolve or reopen")
    return await _route(monitor.inbox_action, int(_require(args, "id")), verb, body)


async def list_actions(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    return await _route(monitor.global_actions, db=args.get("target"))


ACTION_VERBS = ("did_it", "dismiss", "undid_it")


async def action(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    target = _require_target(args, "target")
    verb = str(_require(args, "verb"))
    if verb not in ACTION_VERBS:
        raise ToolError("verb must be did_it, dismiss or undid_it")
    body = monitor.ActionVerb(reason=args.get("reason"))
    return await _route(monitor.action_verb, target, int(_require(args, "id")), verb, body)


async def get_index_plan(app: FastAPI, args: dict) -> dict | None:
    from features.monitor.api import routes as monitor

    return await _route(monitor.index_plan, _require_target(args, "target"))


async def target_health(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    name = _require_target(args, "target")
    health = await _route(monitor.health, name, internal_request(app),
                          range=args.get("range") or "1h", window=None, from_=None, to=None)
    # The per-bucket series are for charts; the agent gets the totals and readings.
    return {key: value for key, value in health.items()
            if key not in ("sparkline", "points", "series", "timeline")}


async def top_queries(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    name = _require_target(args, "target")
    limit = max(1, min(int(args.get("limit") or 20), 100))
    return await _route(monitor.queries, name, range=args.get("range") or "24h", window=None,
                        sort=args.get("sort") or "db_time_share", filter="", q="",
                        limit=limit, offset=0, from_=None, to=None, keys=None, locate=None)


async def query_detail(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    return await _route(monitor.query, _require_target(args, "target"),
                        str(_require(args, "key")), range=args.get("range"), from_=None, to=None)


# Points returned per series; the newest are kept when a range has more.
MAX_SERIES_POINTS = 500


async def query_timeseries(app: FastAPI, args: dict) -> dict:
    from features.monitor.api import routes as monitor

    name = _require_target(args, "target")
    key = str(_require(args, "key"))
    series = jsonable_encoder(await _route(monitor.series, name, key,
                                           range=args.get("range") or "24h", from_=None,
                                           to=None))
    points = series.get("points") or []
    return {"target": name, "key": key, "range": series.get("range") or args.get("range"),
            "resolution_s": series.get("res"),
            "units": {"calls": "calls in the bucket", "qps": "calls per second",
                      "mean_ms": "mean latency, ms", "total_ms": "database time, ms",
                      "temp_blks": "temp blocks written (PostgreSQL)"},
            "points": points[-MAX_SERIES_POINTS:], "truncated": len(points) > MAX_SERIES_POINTS,
            "markers": series.get("markers"), "fix": series.get("fix"),
            "history": series.get("history"), "detail_note": series.get("detail_note")}


async def latest_report(app: FastAPI, args: dict) -> dict | None:
    from features.monitor.api import routes as monitor

    return await _route(monitor.latest_report, _require_target(args, "target"), daily=False,
                        view="quick")


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return schema


_NAME = {"name": {"type": "string", "description": "The database's name in RDST."}}
_TARGET = {"target": {"type": "string", "description": "The database's name in RDST."}}
_RANGE = {"range": {"type": "string",
                    "enum": ["5m", "15m", "1h", "24h", "7d", "30d", "90d", "all"]}}
_SETTING_FIELDS = {
    "deep_analysis": {"type": "boolean",
                      "description": "Let RDST's AI analyze slow queries in depth."},
    "analysis_mode": {"type": "string", "enum": ["plans_only", "execute"],
                      "description": "plans_only reads EXPLAIN plans; execute also runs "
                                     "EXPLAIN ANALYZE inside a rolled-back transaction."},
}

Handler = Callable[[FastAPI, dict], Awaitable[Any]]

TOOLS: dict[str, tuple[str, dict, Handler]] = {
    "rdst_status": (
        "What RDST has: each database, whether its password is saved and whether it's "
        "monitored, plus whether the user is signed in to Readyset, which monitoring's AI "
        "needs.",
        _schema(), status),
    "rdst_add_database": (
        "Save a PostgreSQL or MySQL database in RDST. Pass url, or env_file plus env_var so "
        "RDST reads the URL from that file itself, or host, port, database, user and "
        "password. The password goes to the OS keyring; `persisted` is false when the "
        "keyring is unavailable and the password only lasts until RDST restarts.",
        _schema({
            "name": {"type": "string", "description": "A short name for the database."},
            "url": {"type": "string", "description": "postgresql:// or mysql:// URL."},
            "env_file": {"type": "string",
                         "description": "Absolute path of a .env-style file holding the URL."},
            "env_var": {"type": "string", "description": "The variable in env_file."},
            "engine": {"type": "string", "enum": ["postgresql", "mysql"]},
            "host": {"type": "string"}, "port": {"type": "integer"},
            "database": {"type": "string"}, "user": {"type": "string"},
            "password": {"type": "string"}, "tls": {"type": "boolean"},
            "fields": {"type": "object",
                       "description": "The same connection fields, grouped."},
        }, ["name"]), add_database),
    "rdst_test_database": (
        "Connect to a saved database, then run rdst_check_prerequisites on it. "
        "ready_to_monitor tells whether monitoring can start; when it's false, "
        "blocking_reason says why and instructions say what the user has to do first.",
        _schema(_NAME, ["name"]), test_database),
    "rdst_check_prerequisites": (
        "Check whether RDST can monitor a saved database: the same checks the app runs "
        "before it turns monitoring on (pg_stat_statements on PostgreSQL, performance_schema "
        "statement digests on MySQL). ready_to_monitor is the verdict and blocking_reason "
        "says why not in one line. Each check has its status, the fix for the detected "
        "provider and fix_kind: in_database (SQL run in the database), server_config (a "
        "server setting and a restart) or manual. instructions lists the fixes as plain "
        "lines and sql the in-database statements.",
        _schema(_NAME, ["name"]), check_prerequisites),
    "rdst_enable_monitoring": (
        "Turn on monitoring for a saved database: RDST reads its query statistics in the "
        "background and files findings in its Inbox. Refused, with the prerequisite result, "
        "until rdst_check_prerequisites reports ready_to_monitor: true. The first time for a "
        "database the user turns it on in the app: the reply has waiting_for_user: true and "
        "RDST shows the user its Turn on monitoring screen, which asks them to sign up or sign "
        "in when they're signed out (monitoring's AI is free with an account). Pass on the "
        "reply's message, then call rdst_status until the database shows monitoring: true.",
        _schema({**_NAME, **_SETTING_FIELDS}, ["name"]), enable_monitoring),
    "rdst_disable_monitoring": (
        "Turn off monitoring for a database. Its history stays.",
        _schema(_NAME, ["name"]), disable_monitoring),
    "rdst_get_monitor_settings": (
        "A monitored database's settings: analysis mode, caps, notifications.",
        _schema(_NAME, ["name"]), get_monitor_settings),
    "rdst_update_monitor_settings": (
        "Change a database's monitoring settings; only the fields passed change. enabled: "
        "true works like rdst_enable_monitoring, including waiting_for_user the first time.",
        _schema({**_NAME, "settings": {
            "type": "object", "properties": {
                **_SETTING_FIELDS, "enabled": {"type": "boolean"},
                "auto_analyze_new": {"type": "boolean"},
                "analysis_daily_cap": {"type": "integer"},
                "attention_every_min": {"type": "integer", "enum": [5, 15, 60]},
                "nightly_report": {"type": "boolean"},
                "nightly_hour": {"type": "integer"}}}}, ["name", "settings"]),
        update_monitor_settings),
    "rdst_list_inbox": (
        "Findings RDST filed: slow or changed queries, missing indexes, connection problems.",
        _schema({"state": {"type": "string", "enum": ["open", "muted", "done"]},
                 **_TARGET, "limit": {"type": "integer"}}), list_inbox),
    "rdst_get_inbox_item": (
        "One Inbox finding with its evidence and suggested fix.",
        _schema({"id": {"type": "integer"}}, ["id"]), get_inbox_item),
    "rdst_inbox_action": (
        "Mark an Inbox finding seen, snoozed (pass until, epoch seconds), dismissed, "
        "resolved or reopened.",
        _schema({"id": {"type": "integer"},
                 "verb": {"type": "string",
                          "enum": ["seen", "snooze", "dismiss", "resolve", "reopen"]},
                 "until": {"type": "integer"}}, ["id", "verb"]), inbox_action),
    "rdst_list_actions": (
        "RDST's ranked plan of changes (indexes and query rewrites) with their SQL, for every "
        "monitored database or one. After applying a fix, call rdst_action with did_it so "
        "RDST updates right away.",
        _schema(_TARGET), list_actions),
    "rdst_action": (
        "Tell RDST what happened to one action from rdst_list_actions: did_it after applying "
        "its change (RDST reads the database now and marks it applied, so measuring starts), "
        "dismiss to drop it, or undid_it after reverting an applied change. reason is "
        "optional, in the user's words.",
        _schema({**_TARGET, "id": {"type": "integer"},
                 "verb": {"type": "string", "enum": list(ACTION_VERBS)},
                 "reason": {"type": "string", "maxLength": 500}},
                ["target", "id", "verb"]), action),
    "rdst_get_index_plan": (
        "The latest index plan for a database: indexes to add or drop and why. After "
        "applying a fix, call rdst_action with did_it so RDST updates right away.",
        _schema(_TARGET, ["target"]), get_index_plan),
    "rdst_target_health": (
        "A database's load, latency and connection readings over a range.",
        _schema({**_TARGET, **_RANGE}, ["target"]), target_health),
    "rdst_top_queries": (
        "A database's queries ranked by share of database time (or another sort).",
        _schema({**_TARGET, **_RANGE, "limit": {"type": "integer"},
                 "sort": {"type": "string", "enum": ["db_time_share", "qps", "mean_ms",
                                                     "change"]}}, ["target"]), top_queries),
    "rdst_query_detail": (
        "One query's statistics and RDST's assessment, by its key from rdst_top_queries.",
        _schema({**_TARGET, "key": {"type": "string"}, **_RANGE}, ["target", "key"]),
        query_detail),
    "rdst_query_timeseries": (
        "One query's time series, the same one its chart on the database page shows: per "
        "bucket calls, calls per second, mean latency and total database time, plus the "
        "chart's markers and whether an applied fix helped. key comes from rdst_top_queries.",
        _schema({**_TARGET, "key": {"type": "string"}, **_RANGE}, ["target", "key"]),
        query_timeseries),
    "rdst_latest_report": (
        "The newest checkup report for a database, or null before the first one.",
        _schema(_TARGET, ["target"]), latest_report),
}


# Tools whose failures the Add database dialog shows while it follows the agent.
PROGRESS_TOOLS = ("rdst_add_database", "rdst_test_database", "rdst_check_prerequisites",
                  "rdst_enable_monitoring")


def tool_list() -> list[dict]:
    return [{"name": name, "description": f"{description} {ABOUT}", "inputSchema": schema}
            for name, (description, schema, _handler) in TOOLS.items()]


async def call_tool(app: FastAPI, name: str, args: dict | None) -> dict:
    """An MCP tools/call result for ``name``."""
    entry = TOOLS.get(name)
    if entry is None:
        raise KeyError(name)
    args = dict(args or {})
    try:
        result = await entry[2](app, args)
    except NeedsSetup as exc:
        report_prerequisites(exc.target, exc.state, "mcp")
        _count(name, False, "needs_setup")
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except ToolError as exc:
        if name in PROGRESS_TOOLS:
            session.events.publish("error", target=args.get("name"), message=str(exc))
        _count(name, False, "unknown_target" if "has no database named" in str(exc)
               else "tool_error")
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except (TypeError, ValueError) as exc:
        _count(name, False, "invalid_arguments")
        return {"content": [{"type": "text", "text": f"Invalid arguments: {exc}"}],
                "isError": True}
    except Exception:
        _count(name, False, "unexpected")
        raise
    _count(name, *_outcome(result))
    text = json.dumps(jsonable_encoder(result), default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _outcome(result: Any) -> tuple[bool, str | None]:
    """Whether a tool that answered normally got what the agent was after."""
    from shared import product_telemetry

    if not isinstance(result, dict):
        return True, None
    if result.get("ok") is False:
        return False, product_telemetry.connection_error_class(result.get("category"),
                                                               result.get("message"))
    if result.get("connection_error"):
        return False, product_telemetry.connection_error_class(result["connection_error"])
    if result.get("ready_to_monitor") is False:
        return False, "needs_setup"
    if result.get("waiting_for_user"):
        return False, "awaiting_user" if result.get("signed_in", True) else "signin_required"
    return True, None


def _count(tool: str, ok: bool, error_class: str | None = None) -> None:
    from shared import product_telemetry

    product_telemetry.agent_connect_step("tool_called", attempt_id=session.current_attempt(),
                                         tool=tool, ok=ok, error_class=error_class)
