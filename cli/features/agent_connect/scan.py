"""Find the user's databases on this computer without an agent.

Each method reads one kind of evidence and says where it looked, so the dialog can show it:
- projects: every file in the folders the user's recent Claude Code and Codex sessions ran
  in that can name a database, in any language: .env and Compose files, config, infrastructure,
  source, the drivers its manifest pulls in and the secret manager it uses
- history: the user's recent agent sessions and shell history, for databases they connected to
- docker: running PostgreSQL and MySQL containers and the ports they publish
- ports: PostgreSQL and MySQL servers listening on this computer, told apart by how each
  answers a connection (no login is attempted)
- clients: connections saved in database tools, ~/.pgpass, pg_service.conf and ~/.my.cnf

On Windows each method also covers WSL. On a Mac, a project in a folder macOS guards (like
Documents) is listed by name and read only when the user picks it, so macOS asks then, once.
Nothing here connects to a database or keeps a password: a finding says where its password
is, and RDST reads it only when the user adds the database.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from features.agent_connect import discover, projects, traces
from features.agent_connect.traces import LOCAL_HOSTS, parse_url, provider_of

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 8
PROBE_TIMEOUT_SECONDS = 0.6
PORT_LIMIT = 200
# The end of each recent agent session, where its latest connections are.
SESSION_READ_BYTES = 6 << 20
SESSIONS_PER_PROJECT = 4
HISTORY_READ_BYTES = 4 << 20
DB_IMAGE = re.compile(r"(^|/)(postgres|postgis|timescaledb|pgvector|supabase/postgres|"
                      r"mysql|mariadb|percona)(:|$|-)", re.IGNORECASE)
# Variables naming one database's parts: PGHOST and friends, DB_HOST and friends, and so on.
GROUPS = (("PG", ("HOST",), ("PORT",), ("DATABASE",), ("USER",), ("PASSWORD",)),
          *((prefix, ("HOST", "HOSTNAME", "SERVER"), ("PORT",),
             ("NAME", "DATABASE", "DB", "DBNAME"), ("USER", "USERNAME"),
             ("PASSWORD", "PASS"))
            for prefix in ("DB_", "DATABASE_", "POSTGRES_", "POSTGRESQL_", "MYSQL_",
                           "MARIADB_")))
SUPABASE_URL = re.compile(r"^https://([a-z0-9]{20})\.supabase\.co/?$")

def _environment(*names: str) -> str:
    text = " ".join(name for name in names if name).lower()
    if "prod" in text:
        return "production"
    if "stag" in text:
        return "staging"
    return "development"


def _project_name(folder: str) -> str:
    parts = [part for part in re.split(r"[\\/]", folder) if part]
    # "app" or "backend" says little; its parent names the project.
    if len(parts) > 1 and parts[-1].lower() in {"app", "backend", "server", "api", "web"}:
        return f"{parts[-2]}-{parts[-1]}"
    return parts[-1] if parts else "database"


def parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", key):
            continue
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def from_dotenv(path: str, values: dict[str, str], project: str) -> list[dict[str, Any]]:
    """The databases one .env-style file names, each pointing at where its secret is."""
    found: list[dict[str, Any]] = []
    name = _project_name(project)
    file_name = os.path.basename(path)
    for key, value in values.items():
        if "${" in value:
            continue
        parsed = parse_url(value)
        if parsed is None:
            continue
        found.append({
            "name": name, "engine": parsed["engine"], "host": parsed["host"],
            "port": parsed["port"], "database": parsed["database"], "user": parsed["user"],
            "provider": provider_of(parsed["host"]),
            "environment": _environment(key, file_name),
            **({"env_file": path, "env_var": key} if parsed["has_password"] else {}),
            "found_by": f"{key} in {file_name}"})
    upper = {key.upper(): key for key in values}
    for prefix, hosts, ports, databases, users, passwords in GROUPS:
        def pick(suffixes: tuple[str, ...]) -> str | None:
            return next((upper[prefix + suffix] for suffix in suffixes
                         if prefix + suffix in upper), None)

        host_key = pick(hosts)
        if host_key is None or not values[host_key] or "$" in values[host_key]:
            continue
        port_key, database_key = pick(ports), pick(databases)
        user_key, password_key = pick(users), pick(passwords)
        port = values.get(port_key or "", "")
        kind = (values.get(upper.get(f"{prefix}CONNECTION", ""), "") +
                values.get(upper.get(f"{prefix}ENGINE", ""), "") + prefix).lower()
        engine = "mysql" if ("mysql" in kind or "maria" in kind or port == "3306") \
            else "postgresql"
        found.append({
            "name": name, "engine": engine, "host": values[host_key],
            "port": int(port) if port.isdigit() else None,
            "database": values.get(database_key or "") or None,
            "user": values.get(user_key or "") or None,
            "provider": provider_of(values[host_key]),
            "environment": _environment(file_name),
            **({"password_file": path, "password_var": password_key}
               if password_key and values.get(password_key) else {}),
            "found_by": f"{host_key} in {file_name}"})
    for key, value in values.items():
        match = SUPABASE_URL.match(value.strip()) if key.upper().endswith("SUPABASE_URL") \
            else None
        if not match:
            continue
        password_key = next((upper[k] for k in ("SUPABASE_DB_PASSWORD", "POSTGRES_PASSWORD")
                             if k in upper and values[upper[k]]), None)
        found.append({
            "name": name, "engine": "postgresql", "host": f"db.{match.group(1)}.supabase.co",
            "port": 5432, "database": "postgres", "user": "postgres", "provider": "supabase",
            "environment": _environment(file_name),
            **({"password_file": path, "password_var": password_key} if password_key else {}),
            "note": f"Supabase project {match.group(1)}",
            "found_by": f"{key} in {file_name}"})
    return found


def _port_of(entry: Any, inner: int) -> int | None:
    """The host port a Compose ports entry publishes for container port ``inner``."""
    if isinstance(entry, dict):
        target, published = entry.get("target"), entry.get("published")
        return int(published) if str(target) == str(inner) and str(published).isdigit() \
            else None
    text = re.sub(r"\$\{[A-Za-z0-9_]+:-([0-9]+)\}", r"\1", str(entry)).split("/")[0]
    parts = text.split(":")
    if len(parts) >= 2 and parts[-1] == str(inner) and parts[-2].isdigit():
        return int(parts[-2])
    return None


def from_compose(path: str, text: str, project: str) -> list[dict[str, Any]]:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    found = []
    for service, spec in services.items():
        if not isinstance(spec, dict) or not DB_IMAGE.search(str(spec.get("image") or "")):
            continue
        mysql = re.search(r"mysql|maria|percona", str(spec["image"]), re.IGNORECASE)
        inner = 3306 if mysql else 5432
        port = next((p for entry in spec.get("ports") or [] if (p := _port_of(entry, inner))),
                    None)
        if port is None:
            continue
        env = spec.get("environment") or {}
        if isinstance(env, list):
            env = dict(item.split("=", 1) for item in env if isinstance(item, str) and "=" in item)
        env = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
        found.append({
            "name": f"{_project_name(project)}-{service}", "engine":
            "mysql" if mysql else "postgresql", "host": "localhost", "port": port,
            "database": env.get("MYSQL_DATABASE" if mysql else "POSTGRES_DB") or None,
            "user": (env.get("MYSQL_USER") or "root") if mysql
            else env.get("POSTGRES_USER") or "postgres",
            "provider": "docker", "environment": "development",
            "note": f"Compose service {service}",
            "found_by": f"service {service} in {os.path.basename(path)}"})
    return found


def from_database_yml(path: str, text: str, project: str) -> list[dict[str, Any]]:
    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    found = []
    for env_name, spec in (data or {}).items() if isinstance(data, dict) else []:
        if not isinstance(spec, dict) or env_name == "test":
            continue
        adapter = str(spec.get("adapter") or "")
        engine = "mysql" if "mysql" in adapter else "postgresql" if "postg" in adapter else None
        if engine is None or "<%" in str(spec.get("host", "")):
            continue
        host = str(spec.get("host") or "localhost")
        found.append({
            "name": _project_name(project), "engine": engine, "host": host,
            "port": spec.get("port"), "database": spec.get("database"),
            "user": spec.get("username"), "provider": provider_of(host),
            "environment": _environment(str(env_name)),
            "found_by": f"{env_name} in database.yml"})
    return found


_kind = traces.kind
_project_files = traces.project_files


def _read(path: str, limit: int = traces.CONFIG_READ_LIMIT) -> str | None:
    return traces.read(path, limit)


VIA = {"url": "A connection URL", "settings": "Connection settings",
       "command": "A psql or mysql command", "host": "{host}", "cloudsql": "Cloud SQL {instance}"}


def _trace_item(trace: dict[str, Any], **extra: Any) -> dict[str, Any] | None:
    """One trace as a finding. A host with no dots outside this computer is a Compose
    service or a placeholder, not a database RDST can reach."""
    host = trace.get("host")
    if host:
        host = traces.database_host(host)
        if "." not in host and host.lower() not in LOCAL_HOSTS:
            return None
    provider = trace.get("provider") or provider_of(host)
    return {"engine": trace["engine"], "host": host, "port": trace.get("port"),
            "database": trace.get("database"), "user": trace.get("user"),
            "provider": provider, "instance": trace.get("instance"),
            "incomplete": host is None or None, **extra}


def _file_items(entry: dict[str, Any], project: str) -> list[dict[str, Any]]:
    """The databases one project file's traces name, whatever language it's in. Code and
    scripts name test and example databases as often as real ones: what they name on this
    computer, or on a server RDST can't place, counts only when something running or a
    config file backs it up."""
    found = []
    for trace in entry["traces"]:
        where = VIA[trace["via"]].format(host=trace.get("host"), instance=trace.get("instance"))
        item = _trace_item(trace, name=_project_name(project), project=project,
                           source=entry["path"],
                           environment=_environment(entry["rel"], trace.get("host") or "",
                                                    trace.get("database") or ""),
                           found_by=f"{where} in {entry['rel']}")
        if item is None:
            continue
        if trace["has_password"]:
            item.update(url_file=entry["path"], url_host=item["host"])
        if item["provider"] in ("local", "other"):
            item["weak"] = True
        found.append(item)
    return found


def _in_wsl(folder: str | None) -> bool | None:
    """Whether a folder, as RDST opens it, is inside WSL."""
    return bool(folder and folder.lower().startswith("\\\\wsl")) or None


def _project_items(folder: str, read: dict[str, Any]) -> list[dict[str, Any]]:
    """Every database one project names, from its parsed files and its traces. A project
    that clearly uses a database but doesn't say where still gets an entry, for its agent to
    finish."""
    items: list[dict[str, Any]] = []
    for path in read["structured"]:
        file_kind = _kind(os.path.basename(path), os.path.dirname(path))
        text = _read(path)
        if text is None:
            continue
        found = (from_dotenv(path, parse_dotenv(text), folder) if file_kind == "dotenv"
                 else from_compose(path, text, folder) if file_kind == "compose"
                 else from_database_yml(path, text, folder))
        managers = traces.secret_managers(os.path.basename(path), text)
        read["managers"] = sorted(set(read["managers"]) | managers)
        for item in found:
            item.setdefault("project", folder)
            item.setdefault("source", path)
        items += found
    for entry in read["files"]:
        items += _file_items(entry, folder)
    for item in items:
        if item.get("provider") in ("local", "docker"):
            item.setdefault("wsl", _in_wsl(folder))
    secrets = ", ".join(read["managers"]) or None
    if secrets:
        for item in items:
            if not any(item.get(k) for k in ("env_file", "password_file", "url_file")):
                item["secrets"] = secrets
    if any(item.get("host") and not item.get("weak") for item in items):
        return items
    name = _project_name(folder)
    for entry in read["infra"]:
        items.append({"name": entry.get("label") or name, "engine": entry["engine"],
                      "host": None, "provider": entry["provider"], "project": folder,
                      "environment": _environment(entry.get("label") or "", entry["rel"]),
                      "incomplete": True, "secrets": secrets,
                      "found_by": f"Declared in {entry['rel']}"})
    if not read["infra"] and not any(item.get("host") for item in items) and read["uses"]:
        engines = read["engines"]
        items.append({"name": name, "engine": engines[0] if len(engines) == 1 else None,
                      "host": None, "provider": "other", "project": folder,
                      "environment": "development", "incomplete": True, "secrets": secrets,
                      "found_by": f"A database library in {read['uses']}" +
                                  (f", secrets in {secrets}" if secrets else "")})
    return items


def scan_folder(folder: str) -> list[dict[str, Any]]:
    """Every database one project folder names."""
    if folder.lower().startswith("\\\\wsl") and discover.wsl_home:
        reads = _wsl_read_projects([folder])
        return _project_items(folder, reads[folder]) if folder in reads else []
    return _project_items(folder, traces.read_project(folder))


def _wsl_python(request: dict[str, Any], name: str, timeout: int) -> list[dict[str, Any]]:
    """Run this side's trace reader inside WSL with python3, where WSL's files are fast to
    read, and parse its JSON lines. None when WSL has no python3."""
    if os.name != "nt" or not discover.wsl_home or not shutil.which("wsl.exe"):
        return []
    folder = discover.rdst_data_dir() / "wsl"
    folder.mkdir(parents=True, exist_ok=True)
    reader = folder / "rdst_traces.py"
    try:
        # Packaged, the module is compiled; its source ships next to it for this.
        source = Path(traces.__file__).with_name("traces.py").read_text(encoding="utf-8")
        reader.write_text(source, encoding="utf-8", newline="\n")
    except OSError:
        logger.warning("RDST couldn't copy its trace reader into WSL", exc_info=True)
        return []
    import shlex

    script = (f"command -v python3 >/dev/null 2>&1 || exit 3\n"
              f"exec python3 {shlex.quote(discover._wsl_path(reader))} "
              f"{shlex.quote(json.dumps(request))}\n")
    try:
        command = discover.wsl_script_command(script, name, discover.wsl_home[0])
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    lines = []
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            lines.append(parsed)
    return lines


def _share(path: str) -> str:
    return f"\\\\wsl.localhost\\{discover.wsl_home[0]}" + path.replace("/", "\\")


def _wsl_read_projects(folders: list[str]) -> dict[str, dict[str, Any]]:
    """WSL project folders read inside WSL, keyed by their share paths."""
    linux = {discover.agent_path(discover.Agent("codex", "wsl", "", discover.wsl_home[0]),
                                 folder): folder for folder in folders}
    out = {}
    for read in _wsl_python({"mode": "projects", "folders": list(linux)}, "projects.sh",
                            WSL_READ_TIMEOUT_SECONDS):
        folder = linux.get(read.get("folder"))
        if folder is None:
            continue
        read["structured"] = [_share(path) for path in read["structured"]]
        for entry in read["files"]:
            entry["path"] = _share(entry["path"])
            entry["rel"] = entry["rel"].replace("/", "\\")
        out[folder] = read
    return out


WSL_READ_TIMEOUT_SECONDS = 30


def scan_projects(report: Callable[[str], None]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    folders = projects.recent_projects(None)
    # A project in a folder macOS guards is read when the user picks it.
    waiting = [folder for folder in folders if projects.protected(folder)]
    readable = [folder for folder in folders if folder not in waiting]
    in_wsl = [folder for folder in readable if folder.lower().startswith("\\\\wsl")]
    for folder in in_wsl:
        report(folder)
    reads = _wsl_read_projects(in_wsl) if in_wsl else {}

    def one(folder: str) -> list[dict[str, Any]]:
        if folder in in_wsl:
            return _project_items(folder, reads[folder]) if folder in reads else []
        report(folder)
        return _project_items(folder, traces.read_project(folder))

    with ThreadPoolExecutor(max_workers=6) as pool:
        for items in pool.map(one, readable):
            found += items
    for folder in waiting:
        found.append({"name": _project_name(folder), "engine": None, "host": None,
                      "provider": "other", "project": folder, "environment": "unknown",
                      "deferred": True,
                      "found_by": f"A recent project in {projects.protected(folder)}"})
    return found


def _shell_histories() -> list[Path]:
    home = Path.home()
    paths = [home / name for name in traces.SHELL_HISTORIES]
    if os.name == "nt":
        paths.append(Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) /
                     "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" /
                     "ConsoleHost_history.txt")
    return [path for path in paths if path.is_file()]


def _homes() -> set[str]:
    homes = {str(Path.home()).rstrip("\\/").lower()}
    if discover.wsl_home:
        homes |= {discover.wsl_home[1].rstrip("/").lower(),
                  (projects.here_path(discover.wsl_home[1]) or "").rstrip("\\/").lower()}
    return homes


GENERIC_DATABASES = {"postgres", "defaultdb", "mysql", "main", "db", "app", "neondb",
                     "verceldb", "railway"}


def _trace_name(trace: dict[str, Any]) -> str:
    """What to call a database seen in a session or a command: its own database name, else
    the telling part of its host (a Supabase project's ref, a Neon endpoint)."""
    database = trace.get("database")
    if database and database.lower() not in GENERIC_DATABASES:
        return database
    host = traces.database_host(trace.get("host") or "") or trace.get("instance") or ""
    labels = [label for label in host.split(".") if label not in ("db", "private")]
    # DigitalOcean wraps a cluster's name: private-<name>-do-user-<id>-0.
    name = re.sub(r"^private-|-do-user-\d+(-\d+)?$", "", labels[0]) if labels else ""
    return (name or "database")[:40]


def _history_items(found: list[dict[str, Any]], folder: str | None, agent: str,
                   source: str | None) -> list[dict[str, Any]]:
    # A session in the home folder wasn't in a project.
    if folder and folder.rstrip("\\/").lower() in _homes():
        folder = None
    items = []
    for trace in found:
        where = f"Used in a {agent} session" if agent != "shell" else \
            "In your shell history"
        item = _trace_item(trace, name=_trace_name(trace),
                           environment=_environment(trace.get("host") or "",
                                                    trace.get("database") or ""),
                           project=folder, history=True, found_by=where)
        if item is None:
            continue
        if trace["has_password"] and source:
            item.update(url_file=source, url_host=item["host"])
        items.append(item)
    return items


def scan_history(report: Callable[[str], None]) -> list[dict[str, Any]]:
    """The databases the user's recent agent sessions and shell commands connected to."""
    found: list[dict[str, Any]] = []
    report("Your recent Claude Code and Codex sessions")
    sessions = projects.recent_sessions(SESSIONS_PER_PROJECT)

    def read(entry: tuple[Path, str | None, str]) -> list[dict[str, Any]]:
        return traces.find_all(traces.snippets(traces.tail(str(entry[0]),
                                                           SESSION_READ_BYTES)))

    with ThreadPoolExecutor(max_workers=4) as pool:
        for (path, folder, agent), traced in zip(sessions, pool.map(read, sessions)):
            found += _history_items(traced, folder, agent, str(path))
    report("Your shell history")
    for path in _shell_histories():
        traced = traces.find_all(traces.snippets(traces.tail(str(path), HISTORY_READ_BYTES)))
        found += _history_items(traced, None, "shell", str(path))
    if discover.wsl_home:
        report("Sessions and shell history in WSL")
        for entry in _wsl_python({"mode": "history", "days": projects.RECENT_DAYS,
                                  "per_project": SESSIONS_PER_PROJECT,
                                  "session_bytes": SESSION_READ_BYTES,
                                  "history_bytes": HISTORY_READ_BYTES}, "history.sh",
                                 WSL_READ_TIMEOUT_SECONDS):
            cwd = entry.get("folder")
            # A password in WSL's logs stays there: the agent reads it when it's needed.
            found += _history_items(entry.get("traces") or [],
                                    projects.here_path(cwd) if cwd else None,
                                    entry.get("agent") or "Codex", None)
    return found


def _run(command: list[str]) -> str | None:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=COMMAND_TIMEOUT_SECONDS,
                                stdin=subprocess.DEVNULL, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _wsl(script: str, name: str) -> str | None:
    if os.name != "nt" or not discover.wsl_home or not shutil.which("wsl.exe"):
        return None
    try:
        command = discover.wsl_script_command(script, name, discover.wsl_home[0])
    except OSError:
        return None
    return _run(command)


def _docker() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    if os.name == "nt":
        bundled = [Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" /
                   "Docker" / "resources" / "bin" / "docker.exe"]
    else:
        # An app started from the Mac's Finder doesn't get the shell's PATH.
        bundled = [Path("/usr/local/bin/docker"), Path("/opt/homebrew/bin/docker"),
                   Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
                   Path.home() / ".orbstack" / "bin" / "docker",
                   Path.home() / ".rd" / "bin" / "docker"]
    return next((str(path) for path in bundled if path.is_file()), None)


DOCKER_PS = "docker ps --no-trunc --format '{{json .}}' 2>/dev/null"


def from_docker_ps(text: str, side: str) -> list[dict[str, Any]]:
    found = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        image = str(row.get("Image") or "")
        if not DB_IMAGE.search(image):
            continue
        mysql = re.search(r"mysql|maria|percona", image, re.IGNORECASE)
        inner = 3306 if mysql else 5432
        published = re.search(rf"(?:[\d.]+|\[?::\]?):(\d+)->{inner}/tcp", str(row.get("Ports")))
        if not published:
            continue
        labels = dict(item.split("=", 1) for item in str(row.get("Labels") or "").split(",")
                      if "=" in item)
        working_dir = labels.get("com.docker.compose.project.working_dir")
        name = str(row.get("Names") or "container").split(",")[0]
        found.append({
            "id": row.get("ID"), "name": name, "engine": "mysql" if mysql else "postgresql",
            "host": "localhost", "port": int(published.group(1)),
            "user": "root" if mysql else "postgres", "provider": "docker",
            "environment": "development", "note": f"Docker container {name}",
            "wsl": side == "wsl" or None,
            "project": working_dir if side == "here" else
            discover.windows_path_for(discover.Agent("codex", "wsl", "", discover.wsl_home[0]
                                                     if discover.wsl_home else ""),
                                      working_dir) if working_dir else None,
            "found_by": f"Docker container {name} ({image})"})
    return found


def _in_app_data(path: str) -> bool:
    """Whether ``path`` is in another Mac app's own data (its container), which macOS blocks
    RDST, and every program RDST starts, from reading."""
    home = str(Path.home())
    return any(path == root or path.startswith(root + "/") for root in (
        f"{home}/Library/Containers", f"{home}/Library/Group Containers"))


def _docker_reachable_quietly() -> bool:
    """On a Mac, whether the Docker CLI would talk to its engine without reaching into
    Docker Desktop's container: an older Docker Desktop keeps its socket there, and macOS
    blocks RDST and tells the user. Containers still show up through their ports then."""
    if sys.platform != "darwin" or os.environ.get("DOCKER_HOST"):
        return True
    home = Path.home()
    for socket_path in (home / ".docker" / "run" / "docker.sock",
                        Path("/var/run/docker.sock")):
        try:
            if os.path.lexists(socket_path):
                return not _in_app_data(os.path.realpath(socket_path))
        except OSError:
            continue
    return True


def scan_docker(report: Callable[[str], None]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    docker = _docker() if _docker_reachable_quietly() else None
    if docker:
        report("Docker on this computer")
        out = _run([docker, "ps", "--no-trunc", "--format", "{{json .}}"])
        for item in from_docker_ps(out or "", "here"):
            found[item["id"]] = item
    if discover.wsl_home:
        report("Docker in WSL")
        # Docker Desktop shares one engine with WSL, so a container can show up twice.
        for item in from_docker_ps(_wsl(DOCKER_PS, "docker.sh") or "", "wsl"):
            found.setdefault(item["id"], item)
    for item in found.values():
        item.pop("id", None)
    return list(found.values())


def _listening_here() -> set[int]:
    import psutil

    try:
        return {c.laddr.port for c in psutil.net_connections("tcp")
                if c.status == psutil.CONN_LISTEN and c.laddr}
    except (psutil.AccessDenied, OSError):
        pass
    # macOS lists other processes' sockets only for root; lsof shows the user's own.
    out = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"]) or ""
    return {int(m.group(1)) for m in re.finditer(r":(\d+) \(LISTEN\)", out)}


WSL_LISTENING = "ss -ltnH 2>/dev/null || netstat -ltn 2>/dev/null"


def _listening_in_wsl() -> set[int]:
    out = _wsl(WSL_LISTENING, "ports.sh") or ""
    return {int(m.group(1)) for m in re.finditer(r"[\d.*\]]:(\d+)\s", out)}


def probe(port: int, host: str = "127.0.0.1") -> str | None:
    """Which database answers on ``port``: MySQL greets a new connection first, and
    PostgreSQL answers a request for TLS with S or N. Neither is asked to log in."""
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS) as conn:
            conn.settimeout(PROBE_TIMEOUT_SECONDS)
            try:
                greeting = conn.recv(128)
            except socket.timeout:
                greeting = b""
            if len(greeting) > 5 and greeting[4] == 10 and b"\x00" in greeting[5:]:
                return "mysql"
            if greeting:
                return None
            conn.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
            answer = conn.recv(1)
            return "postgresql" if answer in (b"S", b"N") else None
    except OSError:
        return None


# Checking whether each found database answers: a bounded pool and an overall budget, so a
# list of a hundred can't stall the scan.
REACH_WORKERS = 24
REACH_LIMIT = 150
REACH_BUDGET_SECONDS = 4.0
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}


def knock(host: str, port: int) -> str:
    """Whether a database answers at ``host``:``port``, without logging in: "answering",
    "refused" (nothing listens), "gone" (the name doesn't resolve) or "unreachable" (it
    doesn't answer from here, like a private network or an allowlist)."""
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return "gone"
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS) as conn:
            conn.settimeout(PROBE_TIMEOUT_SECONDS)
            try:
                greeting = conn.recv(128)
            except socket.timeout:
                greeting = b""
            if len(greeting) > 5 and greeting[4] == 10 and b"\x00" in greeting[5:]:
                return "answering"
            if greeting:
                return "unreachable"
            conn.sendall(b"\x00\x00\x00\x08\x04\xd2\x16\x2f")
            return "answering" if conn.recv(1) in (b"S", b"N") else "unreachable"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "unreachable"


def check_reach(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark each found database "answering", or "unreachable" from here, and drop the dead:
    nothing listening on this computer, or a host that no longer exists. A database RDST
    already has stays whatever it says."""
    from concurrent.futures import wait

    checked = [item for item in items if item.get("host")][:REACH_LIMIT]
    pool = ThreadPoolExecutor(max_workers=REACH_WORKERS)
    futures = {pool.submit(knock, traces.database_host(str(item["host"])),
                           int(item.get("port") or DEFAULT_PORTS.get(item.get("engine") or "",
                                                                     5432))): id(item)
               for item in checked}
    done, _ = wait(futures, timeout=REACH_BUDGET_SECONDS)
    pool.shutdown(wait=False, cancel_futures=True)
    answers = {futures[future]: future.result() for future in done}
    kept = []
    for item in items:
        if not item.get("host"):
            kept.append(item)
            continue
        answer = answers.get(id(item), "unreachable")
        local = provider_of(str(item["host"])) == "local"
        if answer in ("gone", "refused" if local else "") and not item.get("saved_as"):
            continue
        item["reach"] = "answering" if answer == "answering" else "unreachable"
        kept.append(item)
    return kept


def scan_ports(report: Callable[[str], None]) -> list[dict[str, Any]]:
    report("Ports listening on this computer")
    here = _listening_here()
    ports = set(here)
    in_wsl: set[int] = set()
    if discover.wsl_home:
        report("Ports listening in WSL")
        in_wsl = _listening_in_wsl() - here
        ports |= in_wsl
    ports = set(sorted(ports)[:PORT_LIMIT])
    with ThreadPoolExecutor(max_workers=32) as pool:
        engines = dict(zip(ports, pool.map(probe, ports)))
    return [{"name": f"{'postgres' if engine == 'postgresql' else 'mysql'}-{port}",
             "engine": engine, "host": "localhost", "port": port, "provider": "local",
             "wsl": port in in_wsl or None,
             "environment": "development", "note": f"Running on port {port}",
             "found_by": f"{'PostgreSQL' if engine == 'postgresql' else 'MySQL'} answering on "
                         f"port {port}"}
            for port, engine in sorted(engines.items()) if engine]



def _client_homes() -> list[tuple[Path, str]]:
    homes = [(Path.home(), "here")]
    if os.name == "nt" and discover.wsl_home:
        distro, home = discover.wsl_home
        homes.append((Path(f"\\\\wsl.localhost\\{distro}{home.replace('/', chr(92))}"), "wsl"))
    return homes


def from_dbeaver(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    found = []
    for spec in (data.get("connections") or {}).values() if isinstance(data, dict) else []:
        provider = str(spec.get("provider") or spec.get("driver") or "").lower()
        engine = "mysql" if "mysql" in provider or "maria" in provider else \
            "postgresql" if "postg" in provider else None
        config = spec.get("configuration") or {}
        if engine is None or not config.get("host"):
            continue
        host = str(config["host"])
        port = str(config.get("port") or "")
        found.append({
            "name": spec.get("name"), "engine": engine, "host": host,
            "port": int(port) if port.isdigit() else None,
            "database": config.get("database") or None, "provider": provider_of(host),
            "environment": {"prod": "production", "test": "staging"}.get(
                str(config.get("type") or ""), "development"),
            "note": f"Saved in DBeaver as {spec.get('name')}",
            "found_by": f"DBeaver connection {spec.get('name')}"})
    return found


def from_pgpass(text: str) -> list[dict[str, Any]]:
    found = []
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) < 5 or line.startswith("#") or parts[0] in ("*", ""):
            continue
        host, port, database, user = parts[:4]
        found.append({
            "name": database if database != "*" else host.split(".")[0], "engine": "postgresql",
            "host": host, "port": int(port) if port.isdigit() else None,
            "database": None if database == "*" else database,
            "user": None if user == "*" else user, "provider": provider_of(host),
            "environment": "unknown", "found_by": "~/.pgpass"})
    return found


def from_ini(text: str, found_by: str, engine: str) -> list[dict[str, Any]]:
    """pg_service.conf's services and ~/.my.cnf's client sections."""
    import configparser

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    found = []
    for section in parser.sections():
        spec = parser[section]
        host = spec.get("host")
        if not host or host.startswith("/"):
            continue
        port = spec.get("port", "")
        database = spec.get("dbname") or spec.get("database")
        found.append({"name": section if engine == "postgresql" else database or host,
                      "engine": engine, "host": host,
                      "port": int(port) if port.isdigit() else None, "database": database,
                      "user": spec.get("user"), "provider": provider_of(host),
                      "environment": _environment(section, host),
                      "found_by": f"{found_by} [{section}]"})
    return found


def from_settings_json(text: str, label: str) -> list[dict[str, Any]]:
    """VS Code's SQLTools and Azure Data Studio connections, from a settings.json."""
    cleaned = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    try:
        data = json.loads(cleaned)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    found = []
    entries = [*(data.get("sqltools.connections") or []),
               *(data.get("datasource.connections") or [])]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        options = entry.get("options") if isinstance(entry.get("options"), dict) else entry
        driver = str(entry.get("driver") or entry.get("providerName") or "").lower()
        engine = "mysql" if "mysql" in driver or "maria" in driver else \
            "postgresql" if "pg" in driver or "postg" in driver else None
        host = options.get("server") or options.get("host")
        if engine is None or not host:
            continue
        port = str(options.get("port") or "")
        name = entry.get("name") or options.get("connectionName") or host
        found.append({"name": name, "engine": engine, "host": host,
                      "port": int(port) if port.isdigit() else None,
                      "database": options.get("database") or options.get("dbname"),
                      "user": options.get("username") or options.get("user"),
                      "provider": provider_of(host), "environment": _environment(name, host),
                      "found_by": f"{label} connection {name}"})
    return found


def from_sqlite(path: Path, query: str, label: str) -> list[dict[str, Any]]:
    """Connections a tool keeps in its own SQLite file, read without changing it."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        try:
            rows = conn.execute(query).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    found = []
    for name, kind, host, port, database, user in rows:
        kind = str(kind or "").lower()
        engine = "mysql" if "mysql" in kind or "maria" in kind else \
            "postgresql" if "pg" in kind or "postg" in kind or not kind else None
        if engine is None or not host:
            continue
        found.append({"name": name or host, "engine": engine, "host": str(host),
                      "port": int(port) if str(port or "").isdigit() else None,
                      "database": database or None, "user": user or None,
                      "provider": provider_of(str(host)),
                      "environment": _environment(str(name or ""), str(host)),
                      "found_by": f"{label} connection {name or host}"})
    return found


def from_workbench(text: str) -> list[dict[str, Any]]:
    found = []
    for block in re.findall(r'<value type="object" struct-name="db\.mgmt\.Connection".*?'
                            r'</value>\s*</value>', text, re.DOTALL):
        values = dict(re.findall(r'key="(\w+)">([^<]*)<', block))
        host = values.get("hostName")
        if not host:
            continue
        port = values.get("port", "")
        name = values.get("name") or host
        found.append({"name": name, "engine": "mysql", "host": host,
                      "port": int(port) if port.isdigit() else None,
                      "database": values.get("schema") or None,
                      "user": values.get("userName") or None, "provider": provider_of(host),
                      "environment": _environment(name, host),
                      "found_by": f"MySQL Workbench connection {name}"})
    return found


def from_tableplus(path: Path) -> list[dict[str, Any]]:
    import plistlib

    try:
        with open(path, "rb") as handle:
            entries = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return []
    found = []
    for entry in entries if isinstance(entries, list) else []:
        driver = str(entry.get("Driver") or "").lower()
        engine = "mysql" if "mysql" in driver or "maria" in driver else \
            "postgresql" if "postg" in driver else None
        host = entry.get("DatabaseHost")
        if engine is None or not host:
            continue
        name = entry.get("ConnectionName") or host
        port = str(entry.get("DatabasePort") or "")
        found.append({"name": name, "engine": engine, "host": host,
                      "port": int(port) if port.isdigit() else None,
                      "database": entry.get("DatabaseName") or None,
                      "user": entry.get("DatabaseUser") or None, "provider": provider_of(host),
                      "environment": _environment(name, str(entry.get("Enviroment") or "")),
                      "found_by": f"TablePlus connection {name}"})
    return found


def _app_data(home: Path, side: str) -> list[Path]:
    """Where apps keep their settings on each kind of computer."""
    if side == "wsl":
        return [home / ".config", home / ".local" / "share"]
    if os.name == "nt":
        return [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support"]
    return [home / ".config", home / ".local" / "share"]


PGADMIN = ("select name, 'postgres', host, port, maintenance_db, username from server")
BEEKEEPER = ("select name, connectionType, host, port, defaultDatabase, username "
             "from saved_connection")


def scan_clients(report: Callable[[str], None]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for home, side in _client_homes():
        report("Saved connections" + (" in WSL" if side == "wsl" else ""))
        data = _app_data(home, side)
        for root in [*(d / "DBeaverData" for d in data), home / ".local" / "share" /
                     "DBeaverData", home / "Library" / "DBeaverData"]:
            for path in root.glob("workspace*/*/.dbeaver/data-sources*.json"):
                found += from_dbeaver(_read(str(path)) or "")
        for d in data:
            # DataGrip and the other JetBrains IDEs: global data sources.
            for path in d.glob("JetBrains/*/options/dataSources*.xml"):
                found += [{**item, "name": item.get("database") or item["host"] or "database",
                           "project": None, "weak": None,
                           "found_by": f"Saved in {path.parent.parent.name}"}
                          for item in _file_items({"path": str(path), "rel": path.name,
                                                   "traces": traces.find_all(
                                                       _read(str(path)) or "")}, "")]
            for app in ("Code", "Cursor", "Windsurf", "azuredatastudio", "VSCodium"):
                path = d / app / "User" / "settings.json"
                if path.is_file():
                    found += from_settings_json(_read(str(path)) or "",
                                                "Azure Data Studio" if app ==
                                                "azuredatastudio" else app)
            for path in (d / "MySQL" / "Workbench" / "connections.xml",
                         d / "beekeeper-studio" / "app.db", d / "pgAdmin" / "pgadmin4.db",
                         d / "com.tinyapp.TablePlus" / "Data" / "Connections.plist"):
                if not path.is_file():
                    continue
                if path.name == "connections.xml":
                    found += from_workbench(_read(str(path), 4 << 20) or "")
                elif path.name == "app.db":
                    found += from_sqlite(path, BEEKEEPER, "Beekeeper Studio")
                elif path.name == "pgadmin4.db":
                    found += from_sqlite(path, PGADMIN, "pgAdmin")
                else:
                    found += from_tableplus(path)
        for path in (home / ".pgadmin" / "pgadmin4.db",):
            if path.is_file():
                found += from_sqlite(path, PGADMIN, "pgAdmin")
        workbench = home / ".mysql" / "workbench" / "connections.xml"
        if workbench.is_file():
            found += from_workbench(_read(str(workbench), 4 << 20) or "")
        for path in (home / ".pgpass",
                     Path(os.environ.get("APPDATA", home)) / "postgresql" / "pgpass.conf"
                     if side == "here" else home / ".pgpass"):
            if path.is_file():
                found += from_pgpass(_read(str(path)) or "")
        for path in (home / ".pg_service.conf",
                     Path(os.environ.get("APPDATA", home)) / "postgresql" /
                     ".pg_service.conf" if side == "here" and os.name == "nt" else
                     Path("/etc/postgresql-common/pg_service.conf")):
            if path.is_file():
                found += from_ini(_read(str(path)) or "", "pg_service.conf", "postgresql")
        my_cnf = home / ".my.cnf"
        if my_cnf.is_file():
            found += from_ini(_read(str(my_cnf)) or "", "~/.my.cnf", "mysql")
    return found


METHODS: tuple[tuple[str, str, Callable[[Callable[[str], None]], list[dict[str, Any]]]], ...] \
    = (("projects", "Your recent projects", scan_projects),
       ("history", "Your agent and terminal history", scan_history),
       ("docker", "Docker containers", scan_docker),
       ("ports", "Databases running on this computer", scan_ports),
       ("clients", "Saved connections", scan_clients))


def _key(item: dict[str, Any]) -> tuple:
    host = str(item.get("host") or "").lower()
    if not host:
        return (None, item.get("project"), item.get("instance") or item.get("provider"),
                item.get("name"))
    return ("localhost" if host in LOCAL_HOSTS else host, item.get("port"))


def merge(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per database: findings for the same server and database combine, and a
    server known only by its port joins whatever names it. A database seen only in history
    stays when it's a managed one, or running here."""
    merged: list[dict[str, Any]] = []
    # Firm findings first, so a script's or a session's mention joins the database they name.
    items = sorted(items, key=lambda item: bool(item.get("weak") or item.get("history")))
    for item in items:
        same = next((m for m in merged if _key(m) == _key(item) and (
            not m.get("database") or not item.get("database")
            or m["database"] == item["database"])), None)
        if same is None and item.get("host"):
            # A host named without its port joins a finding with one, either way round.
            same = next((m for m in merged if str(m.get("host") or "").lower() ==
                         str(item["host"]).lower() and (not m.get("port") or
                                                        not item.get("port"))), None)
        if same is None and item.get("host") and item.get("project") and \
                item.get("provider") in ("local", "docker"):
            # One project's local server is one entry, whichever databases its files name.
            same = next((m for m in merged if m.get("project") == item["project"] and
                         _key(m) == _key(item)), None)
        weak = bool(item.get("weak") or item.get("history"))
        if same is None and weak and item.get("host"):
            # A test script's database on a server something else found joins that entry.
            same = next((m for m in merged if _key(m)[0] == _key(item)[0] and
                         _key(m)[1] == _key(item)[1]), None)
        if same is None:
            merged.append({**item, "found_by": [item["found_by"]],
                           "methods": {item.get("method")}, "strong": not weak})
            continue
        if weak and same.get("database") and item.get("database") != same.get("database"):
            item = {**item, "database": same["database"]}
        same["strong"] = same["strong"] or not weak
        same["methods"].add(item.get("method"))
        if item["found_by"] not in same["found_by"]:
            same["found_by"].append(item["found_by"])
        secret = ("env_file", "env_var", "password_file", "password_var")
        if not any(same.get(k) for k in secret) and any(item.get(k) for k in secret):
            same.update({k: item.get(k) for k in secret})
        for k, v in item.items():
            if k not in ("found_by", "method", "history", "weak") and v not in (None, "") and \
                    same.get(k) in (None, ""):
                same[k] = v
        # A project's own files name it better than a session did.
        if same.get("history") and not item.get("history") and item.get("name"):
            same["name"] = item["name"]
        # A container or Compose service names the database better than a bare port.
        if same.get("provider") == "local" and item.get("provider") == "docker":
            same["provider"] = "docker"
    kept = []
    for item in merged:
        methods = item.pop("methods")
        strong = item.pop("strong") or bool(methods & {"docker", "ports", "clients"})
        item.pop("weak", None)
        if not strong and item.get("provider") in ("local", "other"):
            continue
        item["history"] = methods == {"history"} or None
        if item.get("host") and item.get("incomplete"):
            item["incomplete"] = None
        kept.append(item)
    # A project's placeholder entry goes once the project has a real database.
    with_hosts = {item.get("project") for item in kept if item.get("host")}
    return [item for item in kept if item.get("host") or item.get("deferred") or
            item.get("project") not in with_hosts]


def scan(on_step: Callable[[list[dict[str, Any]]], None] | None = None) -> dict[str, Any]:
    """Run every method at once; ``on_step`` hears each one start and finish."""
    steps = [{"method": method, "label": label, "state": "running", "found": 0,
              "looked": [], "seconds": None} for method, label, _ in METHODS]
    checking = {"method": "reach", "label": "Checking which ones answer", "state": "running",
                "found": 0, "looked": [], "seconds": None}
    steps.append(checking)
    if on_step:
        on_step(steps)

    def run_one(index: int) -> list[dict[str, Any]]:
        step = steps[index]
        started = time.time()
        try:
            items = METHODS[index][2](lambda where: step["looked"].append(where))
            step["state"] = "done"
        except Exception:
            logger.debug("Scan method %s failed", step["method"], exc_info=True)
            items, step["state"] = [], "failed"
        for item in items:
            item["method"] = step["method"]
        step["found"] = len(items)
        step["seconds"] = round(time.time() - started, 1)
        if on_step:
            on_step(steps)
        return items

    started = time.time()
    with ThreadPoolExecutor(max_workers=len(METHODS)) as pool:
        results = list(pool.map(run_one, range(len(METHODS))))
    merged = merge([item for items in results for item in items])
    reached = time.time()
    found = rank(check_reach(merged))
    checking.update(state="done", found=sum(1 for item in found
                                            if item.get("reach") == "answering"),
                    seconds=round(time.time() - reached, 1))
    if on_step:
        on_step(steps)
    return {"steps": steps, "candidates": found, "seconds": round(time.time() - started, 1)}


RUNNING = re.compile(r"^(Docker container|PostgreSQL answering|MySQL answering)")


def rank(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best evidence first: a database that's running and that RDST can add by itself, then
    the rest. Ties keep the order the methods found them in, most recent project first."""
    def score(item: dict[str, Any]) -> int:
        running = any(RUNNING.match(line) for line in item["found_by"])
        secret = bool(item.get("env_file") or item.get("password_file") or
                      item.get("url_file"))
        named = bool(item.get("database"))
        found = bool(item.get("host"))
        answers = item.get("reach") == "answering"
        return (16 if answers else 0) + (8 if found else 0) + (4 if running else 0) + \
            (2 if secret else 0) + (1 if named else 0)

    return sorted(items, key=score, reverse=True)


SIGN_IN_PROVIDERS = ("supabase", "neon", "digitalocean", "aws")
SUPABASE_REF = re.compile(r"^(?:db\.)?([a-z0-9]{20})\.supabase\.co$")


def connection_args(item: dict[str, Any]) -> dict[str, Any] | None:
    """What rdst_add_database needs to add a found database by itself: its URL or fields with
    the password, read now from where the scan saw it. None when RDST doesn't know where the
    password is. A Supabase URL on the transaction pooler (6543) moves to the session pooler
    on the same host, since monitoring needs prepared statements."""
    from features.agent_connect import tools

    args: dict[str, Any]
    url_value = None
    if item.get("env_file") and item.get("env_var"):
        text = _read(discover.rdst_path(str(item["env_file"]))) or ""
        url_value = parse_dotenv(text).get(str(item["env_var"]), "")
    if url_value and parse_url(url_value) is not None:
        args = {"env_file": item["env_file"], "env_var": item["env_var"]}
    elif item.get("password_file") and item.get("password_var") and item.get("host"):
        args = {key: item.get(key) for key in ("engine", "host", "port", "database", "user")
                if item.get(key) not in (None, "")}
        args["password"] = tools._read_env_file(item["password_file"], item["password_var"])
    elif item.get("url_file") and item.get("url_host"):
        text = traces.tail(discover.rdst_path(item["url_file"]), SESSION_READ_BYTES) \
            if str(item["url_file"]).endswith((".jsonl", "history", "_history.txt")) \
            else _read(discover.rdst_path(item["url_file"])) or ""
        url = traces.find_raw_url(text, item["url_host"])
        if url is None:
            return None
        args = {"url": url}
    else:
        return None
    provider = item.get("provider")
    if provider == "supabase" and item.get("port") == 6543:
        args["port"] = 5432
    host = str(item.get("host") or "")
    ref = SUPABASE_REF.match(host.lower())
    user_ref = re.match(r"^postgres\.([a-z0-9]{20})$", str(item.get("user") or ""))
    tags = [f"provider:{provider}"] if provider in SIGN_IN_PROVIDERS else []
    if provider == "supabase" and (ref or user_ref):
        tags.append(f"supabase-ref:{(ref or user_ref).group(1)}")
    if tags:
        args["tags"] = tags
    return args


CARRIED = ("env_file", "env_var", "password_file", "password_var", "url_file", "url_host",
           "secrets", "instance", "history", "reach", "project", "source", "saved_as", "wsl")


def settle(proposed: list[dict[str, Any]], found: list[dict[str, Any]],
           skipped: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """The agent's short list, filled in from what the scan saw (where each password is, what
    pointed at it, whether it answers) and checked again, so a dead one never shows. Then
    every line of the scan's list (numbered as the agent saw it) that the agent neither
    proposed nor left out with a reason, so what shows never depends on what the agent
    happened to mention."""
    def key(item: dict[str, Any]) -> tuple:
        host = traces.database_host(str(item.get("host") or "")).lower()
        if not host:
            return (None, item.get("project"), item.get("name"))
        port = item.get("port") or DEFAULT_PORTS.get(item.get("engine") or "", 5432)
        return ("localhost" if host in LOCAL_HOSTS else host, port)

    def names_a_database(item: dict[str, Any]) -> bool:
        # An agent can point at the wrong variable, like a Supabase project's https URL.
        if not (item.get("env_file") and item.get("env_var")):
            return True
        text = _read(discover.rdst_path(str(item["env_file"]))) or ""
        value = parse_dotenv(text).get(str(item["env_var"]), "")
        return parse_url(value) is not None

    by_key = {key(item): item for item in found}
    by_host = {key(item)[0]: item for item in found if item.get("host")}
    used: set[int] = set()
    settled = []
    for item in proposed:
        line = item.get("found")
        seen = found[line] if isinstance(line, int) and 0 <= line < len(found) else None
        # Otherwise the scan's database at the same place; one without a port is the scan's
        # database on that host.
        seen = seen or by_key.get(key(item)) or (
            by_host.get(key(item)[0]) if item.get("host") and not item.get("port") else None)
        merged = dict(item)
        if not names_a_database(merged):
            merged.pop("env_file", None)
            merged.pop("env_var", None)
        if seen is not None:
            used.add(id(seen))
            for field in CARRIED:
                if merged.get(field) in (None, "") and seen.get(field) not in (None, ""):
                    merged[field] = seen[field]
            merged["found_by"] = list(seen.get("found_by") or merged.get("found_by") or [])
        settled.append(merged)
    rest = [item for line, item in enumerate(found)
            if id(item) not in used and line not in (skipped or {})]
    unchecked = [item for item in settled if item.get("host") and not item.get("reach")]
    kept = check_reach(unchecked) if unchecked else []
    return [item for item in settled if item.get("reach") or not item.get("host")
            or item in kept] + rest
