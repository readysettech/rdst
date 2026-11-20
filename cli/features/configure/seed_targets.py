"""Databases a server-mode RDST is told about at install time.

The Helm chart's ``targets`` list arrives as JSON in ``RDST_SEED_TARGETS``, each password as
its own environment variable from a Kubernetes Secret. On startup RDST adds or updates those
databases through the same routes the UI uses, pointing each at its environment variable so
the password never lands on disk, and turns monitoring on the first time it's asked for, so
a user who turns it off later keeps it off. Databases added in the UI are left alone, and a
database dropped from the list stays in RDST.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger("uvicorn.error")

ENV_VAR = "RDST_SEED_TARGETS"
ENGINES = {"postgresql": 5432, "mysql": 3306}
_FIELDS = ("engine", "host", "port", "database", "user", "tls", "tls_verify", "read_only")


SEEDED = "seeded-targets.json"
MONITORING_ENABLED = "seeded-monitoring.json"


def _state_path(name: str = SEEDED) -> Path:
    from shared.constants import rdst_data_dir

    return rdst_data_dir() / name


def _seeded(state: str = SEEDED) -> set[str]:
    try:
        return set(json.loads(_state_path(state).read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def _remember(names: set[str], state: str = SEEDED) -> None:
    from shared.constants import ensure_private_file

    path = _state_path(state)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(sorted(names)))
    ensure_private_file(tmp)
    os.replace(tmp, path)


def parse(raw: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """The valid entries in ``raw``, and a problem line for each one left out."""
    from shared.config.targets import validate_target_name
    from shared.password_resolver import check_password_env, derive_password_env

    if not raw or not raw.strip():
        return [], []
    try:
        items = json.loads(raw)
    except ValueError:
        return [], [f"{ENV_VAR} isn't valid JSON; no databases were added from it."]
    if not isinstance(items, list):
        return [], [f"{ENV_VAR} must be a JSON list of databases."]
    entries: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        label = f"targets[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{label} isn't an object; skipped.")
            continue
        name = str(item.get("name") or "").strip()
        try:
            validate_target_name(name)
        except ValueError as exc:
            problems.append(f"{label} has an invalid name {name!r}: {exc}")
            continue
        label = f"targets[{index}] ({name})"
        if name.lower() in seen:
            problems.append(f"{label} repeats a name; skipped.")
            continue
        engine = str(item.get("engine") or "postgresql").lower()
        engine = {"postgres": "postgresql", "pg": "postgresql"}.get(engine, engine)
        if engine not in ENGINES:
            problems.append(f"{label} has engine {engine!r}; use postgresql or mysql.")
            continue
        missing = [key for key in ("host", "database", "user") if not item.get(key)]
        if missing:
            problems.append(f"{label} is missing {', '.join(missing)}; skipped.")
            continue
        try:
            port = int(item.get("port") or ENGINES[engine])
        except (TypeError, ValueError):
            problems.append(f"{label} has a port that isn't a number; skipped.")
            continue
        password_env = str(item.get("passwordEnv") or derive_password_env(name))
        try:
            check_password_env(password_env)
        except ValueError as exc:
            problems.append(f"{label} can't read its password from {password_env}: {exc}")
            continue
        seen.add(name.lower())
        entries.append({
            "name": name, "engine": engine, "host": str(item["host"]), "port": port,
            "database": str(item["database"]), "user": str(item["user"]),
            "password_env": password_env, "tls": bool(item.get("tls", False)),
            "tls_verify": bool(item.get("tlsVerify", False)),
            "read_only": bool(item.get("readOnly", False)),
            "monitoring": bool(item.get("monitoring", False)),
        })
    return entries, problems


async def _save(app: FastAPI, entry: dict[str, Any], existing: dict | None) -> bool:
    """Add or update ``entry`` through the configure routes. True when it saved."""
    from features.agent_connect.tools import internal_request
    from features.configure.api import routes as configure

    target = configure.TargetData(**{key: entry[key] for key in _FIELDS},
                                  password_env=entry["password_env"])
    if existing is None:
        result = await configure.add_target(
            internal_request(app, "/api/configure/targets"),
            configure.AddTargetRequest(name=entry["name"], target=target))
    else:
        if all(existing.get(key) == entry[key] for key in (*_FIELDS, "password_env")):
            return True
        result = await configure.update_target(
            internal_request(app, f"/api/configure/targets/{entry['name']}"),
            entry["name"], configure.UpdateTargetRequest(target=target))
    if not getattr(result, "success", False):
        logger.warning("RDST couldn't save database %s from the Helm targets: %s",
                       entry["name"], getattr(result, "message", "no reason given"))
        return False
    if existing is not None:
        _keep_password_env(entry)
    return True


def _keep_password_env(entry: dict[str, Any]) -> None:
    """Point the target back at the chart's password variable.

    The update route drops a saved password pointer when the host, port or user changes
    and no password comes with it, which protects browser and MCP callers. The chart sets
    this pointer itself and the variable still holds the password, so it stays.
    """
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    saved = config.get(entry["name"])
    if saved is not None and saved.get("password_env") != entry["password_env"]:
        config.upsert(entry["name"], {**saved, "password_env": entry["password_env"]})
        config.save()


def _install_request(app: FastAPI, path: str):
    """An in-process request for the chart's own settings, attributed to the install."""
    from starlette.requests import Request

    return Request({"type": "http", "method": "PUT", "path": path, "app": app,
                    "headers": [(b"x-rdst-via", b"helm")], "client": ("127.0.0.1", 0)})


async def _enable(app: FastAPI, name: str) -> None:
    from features.monitor.api import routes as monitor

    if name in monitor._store().monitored_targets():
        return
    body = monitor.MonitorSettingsUpdate(enabled=True, deep_analysis=True)
    await monitor.put_settings(name, body, _install_request(app, f"/api/monitor/{name}"))


async def seed(app: FastAPI, raw: str | None = None) -> list[str]:
    """Add the install-time databases. Never raises: a bad entry is logged and skipped."""
    from fastapi import HTTPException

    from shared.config.targets import TargetsConfig

    raw = os.environ.get(ENV_VAR) if raw is None else raw
    entries, problems = parse(raw)
    for problem in problems:
        logger.warning("Helm targets: %s", problem)
    if not entries:
        return []
    seeded = _seeded()
    monitoring_enabled = _seeded(MONITORING_ENABLED)
    done: list[str] = []
    for entry in entries:
        name = entry["name"]
        try:
            config = TargetsConfig()
            config.load()
            existing = config.get(name)
            if existing is not None and name not in seeded:
                logger.warning("Helm targets: %s already exists in RDST (added in the UI), so "
                               "the chart's entry is ignored.", name)
                continue
            if not os.environ.get(entry["password_env"]):
                logger.warning("Helm targets: %s has no password in %s yet; RDST saves it and "
                               "connects once the variable is set.", name,
                               entry["password_env"])
            if not await _save(app, entry, existing):
                continue
            seeded.add(name)
            _remember(seeded)
            if entry["monitoring"] and name not in monitoring_enabled:
                try:
                    await _enable(app, name)
                    monitoring_enabled.add(name)
                    _remember(monitoring_enabled, MONITORING_ENABLED)
                except HTTPException as exc:
                    logger.warning("Helm targets: monitoring for %s didn't turn on: %s",
                                   name, exc.detail)
            done.append(name)
        except Exception:
            logger.warning("Helm targets: %s couldn't be set up", name, exc_info=True)
    if done:
        logger.info("Helm targets: %s ready in RDST", ", ".join(done))
    return done
