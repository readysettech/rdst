"""Find the user's databases with their own coding agent.

RDST runs Claude Code or Codex headless, here or inside WSL, with RDST's MCP server attached:
the user's own CLI, or with none, the copy the Claude or Codex desktop app ships.
The agent reads its recent sessions and the projects they ran in, then proposes the databases
it found through rdst_propose_databases. The user picks which ones RDST adds; the agent never
adds anything itself.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from shared.ai_data_consent import ai_data_sharing_allowed
from shared.constants import rdst_data_dir, rdst_instance_suffix

logger = logging.getLogger(__name__)

AGENTS = {"claude": "Claude Code", "codex": "Codex"}
# What the dialog calls the agent in a sentence: "Claude found 2 databases".
AGENT_NAMES = {"claude": "Claude", "codex": "Codex"}
PROVIDERS = ("local", "docker", "kubernetes", "supabase", "neon", "digitalocean", "aws", "gcp", "azure",
             "planetscale", "railway", "render", "heroku", "aiven", "timescale", "crunchy",
             "cockroach", "xata", "tembo", "nile", "prisma", "tidb", "singlestore", "fly",
             "vultr", "linode", "scaleway", "ovh", "other")
ENVIRONMENTS = ("development", "staging", "production", "unknown")
# How long a detection result stands before the next request looks again.
DETECT_TTL_SECONDS = 60
DETECT_TIMEOUT_SECONDS = 20
RUN_TIMEOUT_SECONDS = 8 * 60
# Setting a database up can wait up to 10 minutes on the user's Turn on monitoring click.
SETUP_TIMEOUT_SECONDS = 15 * 60
MAX_CANDIDATES = 60
# Finished runs remembered, so a dialog reopened after a search still shows its results.
RUN_LIMIT = 10
PROGRESS_LIMIT = 6
ACTIVITY_LIMIT = 200
CLAUDE_TOOLS = ("Read", "Grep", "Glob", "mcp__{server}__rdst_propose_databases",
                "mcp__{server}__rdst_status")
# Setting up reads ports and passwords (docker port, a container's env) and can run the
# statements RDST asks for, so it also gets a shell and every RDST tool.
CLAUDE_SETUP_TOOLS = ("Read", "Grep", "Glob", "Bash", "mcp__{server}")
REGISTER_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Agent:
    """One runnable coding agent: which one, and where it's installed."""
    id: str
    side: str  # "local", or "wsl" for an agent inside WSL while RDST runs on Windows
    path: str
    distro: str | None = None
    # The user's login shell's PATH, which the agent runs with: an app started from the
    # desktop lacks nvm, Homebrew and ~/.local/bin, and the agent may need node from them.
    env_path: str | None = None
    # Whether the agent has a sign-in it can run with; None when it can't say.
    signed_in: bool | None = None
    # "app" for the copy a desktop app ships (the Claude app's Claude Code, the Codex app's
    # codex), which RDST runs when the user has no CLI of their own.
    via: str = "cli"

    @property
    def key(self) -> str:
        return f"{self.id}:{self.side}"

    @property
    def label(self) -> str:
        return AGENTS[self.id] + (" in WSL" if self.side == "wsl" else "")

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "id": self.id, "side": self.side, "label": self.label,
                "name": AGENT_NAMES[self.id], "signed_in": self.signed_in, "via": self.via}


_detect_lock = threading.Lock()
_detected: tuple[float, list[Agent]] | None = None
# A background detection is under way, so a stale answer doesn't start another.
_refreshing = False
# The default WSL distro and its user's home, from the last detection on Windows.
wsl_home: tuple[str, str] | None = None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _which_local(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    if os.name == "nt":
        extra = [home / ".local" / "bin" / f"{name}.exe",
                 Path(os.environ.get("APPDATA", home)) / "npm" / f"{name}.cmd"]
    else:
        extra = [home / ".local" / "bin" / name, home / ".claude" / "local" / name,
                 Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]
    return next((str(path) for path in extra if path.is_file()), None)


def _version_in(path: Path) -> tuple[int, ...]:
    """The version a folder on ``path`` is named for, like claude-code/2.1.309/."""
    found = next((re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", part) for part in path.parts
                  if re.fullmatch(r"\d+\.\d+\.\d+", part)), None)
    return tuple(int(group) for group in found.groups()) if found else ()


def _hidden() -> set[str]:
    """Installs a test run treats as missing, from RDST_TEST_HIDE_AGENTS: any of claude,
    codex (their CLIs), claude-app and codex-app. A test machine keeps everything installed."""
    return {part.strip() for part in os.environ.get("RDST_TEST_HIDE_AGENTS", "").split(",")
            if part.strip()}


def app_agent_path(name: str) -> str | None:
    """The copy of agent ``name`` its desktop app put on this computer, newest first: the
    Claude app keeps Claude Code for its Code tab, one folder per version, and the Codex app
    keeps codex. None when there's no app, or it hasn't unpacked one yet."""
    if f"{name}-app" in _hidden():
        return None
    home = Path.home()
    if os.name == "nt":
        if name == "claude":
            root = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / \
                "Claude" / "claude-code"
            found = [*root.glob("*/claude.exe"), *root.glob("*/*/claude.exe")]
        else:
            root = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / \
                "OpenAI" / "Codex" / "bin"
            found = list(root.glob("*/codex.exe"))
    elif sys.platform == "darwin":
        if name == "claude":
            root = home / "Library" / "Application Support" / "Claude" / "claude-code"
            found = [*root.glob("*/claude"), *root.glob("*/*/claude"),
                     *root.glob("*/claude.app/Contents/MacOS/claude"),
                     *root.glob("*/*/claude.app/Contents/MacOS/claude")]
        else:
            found = [folder / "Codex.app" / "Contents" / "Resources" / "codex"
                     for folder in (Path("/Applications"), home / "Applications")]
    else:
        return None
    found = [path for path in found if path.is_file()]
    return str(max(found, key=lambda path: (_version_in(path), _mtime(path)))) \
        if found else None


def _app_agent(name: str, path: str | None) -> Agent | None:
    """The agent ``name``'s desktop app ships, with whether it's signed in. The Claude app
    signs its own copy in for each conversation, so on its own that copy needs a sign-in of
    its own; the Codex app's copy shares the user's Codex sign-in."""
    found = app_agent_path(name)
    if not found:
        return None
    agent = Agent(name, "local", found, env_path=path, via="app")
    return replace(agent, signed_in=_signed_in(agent))


def _login_shell_path() -> str | None:
    """The PATH the user's own shell sets up. Its startup files may print anything, so only
    the marked line counts."""
    shell = os.environ.get("SHELL") or "/bin/bash"
    try:
        out = subprocess.run([shell, "-ilc", 'printf "\\nRDST_PATH=%s\\n" "$PATH"'],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=DETECT_TIMEOUT_SECONDS,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return next((line[len("RDST_PATH="):] for line in reversed(out.splitlines())
                 if line.startswith("RDST_PATH=")), None) or None


# The folders macOS asks about before an app reads them.
MAC_PROTECTED = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Movies",
                 "Library/Mobile Documents", "Library/CloudStorage")


def protected_folder(path: str | None) -> str | None:
    """On a Mac, which guarded folder ``path`` is in (like "Downloads"), or None."""
    if sys.platform != "darwin" or not path:
        return None
    home = str(Path.home())
    for name in MAC_PROTECTED:
        guarded = f"{home}/{name}"
        if path == guarded or path.startswith(guarded + "/"):
            return "iCloud Drive" if name.startswith("Library/") else name
    return None


def _without_protected(path: str | None) -> str | None:
    """``path`` without the folders macOS guards, so looking a program up never opens one."""
    if not path:
        return path
    home = str(Path.home())
    guarded = tuple(f"{home}/{name}" for name in MAC_PROTECTED)
    kept = [entry for entry in path.split(os.pathsep) if entry and not entry.startswith(
        "/Volumes/") and not any(entry == g or entry.startswith(g + "/") for g in guarded)]
    return os.pathsep.join(dict.fromkeys(kept)) or None


def _mac_path() -> str:
    """A Mac's PATH without running the user's shell: the system's, Homebrew's and the
    folders Claude Code, Codex and Node version managers install to."""
    home = Path.home()
    try:
        system = subprocess.run(["/usr/libexec/path_helper", "-s"], capture_output=True,
                                text=True, timeout=5, stdin=subprocess.DEVNULL).stdout
        base = next((part.split('"')[1] for part in system.split(";")
                     if part.strip().startswith("PATH=")), "")
    except (OSError, subprocess.SubprocessError, IndexError):
        base = ""
    nvm = sorted((home / ".nvm" / "versions" / "node").glob("*/bin"), key=_mtime,
                 reverse=True)[:1]
    folders = [home / ".local" / "bin", home / ".claude" / "local", *nvm,
               home / ".volta" / "bin", home / ".bun" / "bin", home / "Library" / "pnpm",
               home / ".npm-global" / "bin", home / ".asdf" / "shims",
               home / ".local" / "share" / "mise" / "shims", Path("/opt/homebrew/bin"),
               Path("/opt/homebrew/sbin"), Path("/usr/local/bin")]
    parts = [str(folder) for folder in folders if folder.is_dir()]
    parts += [entry for entry in (base or "/usr/bin:/bin:/usr/sbin:/sbin").split(os.pathsep)
              if entry]
    return _without_protected(os.pathsep.join(parts)) or "/usr/bin:/bin"


def _login_shell_paths(names: tuple[str, ...], path: str | None) -> dict[str, str]:
    """Where the user's own shell finds ``names``: its PATH rather than `command -v`, which
    names a function or alias instead."""
    if not path:
        return {}
    return {name: found for name in names if (found := shutil.which(name, path=path))}


def _paths_in(text: str, names: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("/") and PurePosixPath(line).name in names:
            found.setdefault(PurePosixPath(line).name, line)
    return found


# Run by sh from a file, so nothing passes through Windows' command-line quoting. The user's
# login shell runs once, only to learn its PATH (nvm, ~/.local/bin); a plain sh then finds
# each agent on it, past any function or alias of the same name. The login shell gets its
# own session: an interactive shell started from wsl.exe otherwise stops on the console it
# inherits. An interactive bash ignores SIGTERM, so a hung one is killed. The usual install
# folders count too, for a login shell this can't drive.
WSL_DETECT = r"""
echo "RDST_DISTRO=$WSL_DISTRO_NAME"
echo "RDST_HOME=$HOME"
login=$(getent passwd "$(id -un)" | cut -d: -f7)
case "$login" in */bash|*/zsh|*/ksh|*/sh|*/dash) ;; *) login=/bin/bash ;; esac
limit=""; command -v timeout >/dev/null 2>&1 && limit="timeout -s KILL 15"
command -v setsid >/dev/null 2>&1 && limit="$limit setsid -w"
found=$($limit "$login" -lic 'printf "\nRDST_PATH=%s\n" "$PATH"' </dev/null 2>/dev/null \
  | sed -n 's/^RDST_PATH=//p' | tail -n 1)
nvm=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | tail -n 1)
PATH="${found:-$PATH}:$HOME/.local/bin:$HOME/.claude/local:$HOME/.npm-global/bin${nvm:+:$nvm}"
echo "RDST_PATH=$PATH"
for name in claude codex; do command -v "$name" 2>/dev/null; done
wait=""; command -v timeout >/dev/null 2>&1 && wait="timeout -s KILL 10"
if p=$(command -v claude); then
  out=$($wait "$p" auth status --json </dev/null 2>/dev/null | tr -d ' ')
  case "$out" in *'"loggedIn":true'*) echo "RDST_SIGNED_IN=claude:1" ;;
    *'"loggedIn":false'*) echo "RDST_SIGNED_IN=claude:0" ;; esac
fi
if p=$(command -v codex); then
  case "$($wait "$p" login status </dev/null 2>&1)" in
    *'Not logged in'*) echo "RDST_SIGNED_IN=codex:0" ;;
    *'Logged in'*) echo "RDST_SIGNED_IN=codex:1" ;; esac
fi
"""


def wsl_script_command(script: str, name: str, distro: str | None = None) -> list[str]:
    """The wsl.exe command that runs ``script`` with sh. The script goes in a file WSL reads
    under /mnt: wsl.exe never closes a piped stdin, so sh reading one would wait forever."""
    folder = rdst_data_dir() / "wsl"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(script, encoding="utf-8", newline="\n")
    return ["wsl.exe", *(["-d", distro] if distro else []), "-e", "sh", _wsl_path(path)]


def _detect_wsl() -> list[Agent]:
    """Agents inside the default WSL distro, with the PATH they run with."""
    if os.name != "nt" or not shutil.which("wsl.exe"):
        return []
    try:
        result = subprocess.run(wsl_script_command(WSL_DETECT, "detect.sh"),
                                capture_output=True, timeout=DETECT_TIMEOUT_SECONDS + 10,
                                stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    out = result.stdout.decode("utf-8", errors="replace")
    distro = next((line.split("=", 1)[1].strip() for line in out.splitlines()
                   if line.startswith("RDST_DISTRO=")), "") or None
    if not distro:
        return []
    global wsl_home
    home = next((line.split("=", 1)[1].strip() for line in out.splitlines()
                 if line.startswith("RDST_HOME=")), "")
    wsl_home = (distro, home) if home.startswith("/") else None
    path = next((line.split("=", 1)[1].strip() for line in reversed(out.splitlines())
                 if line.startswith("RDST_PATH=")), "") or None
    signed = dict(line.split("=", 1)[1].strip().split(":", 1) for line in out.splitlines()
                  if re.fullmatch(r"RDST_SIGNED_IN=(claude|codex):[01]\s*", line))
    return [Agent(name, "wsl", found, distro, path,
                  None if name not in signed else signed[name] == "1")
            for name, found in _paths_in(out, tuple(AGENTS)).items()]


SIGN_IN_TIMEOUT_SECONDS = 10
RETRY_AFTER_SIGN_IN_SECONDS = 2


def _signed_in(agent: Agent) -> bool | None:
    """Whether ``agent`` on this side has a sign-in, from its own status command; None when
    it can't say, like a version without one."""
    command = ([agent.path, "auth", "status", "--json"] if agent.id == "claude"
               else [agent.path, "login", "status"])
    where = work_folder(agent)
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=SIGN_IN_TIMEOUT_SECONDS,
                                stdin=subprocess.DEVNULL, cwd=where,
                                env=agent_env(agent, where),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    if agent.id == "claude":
        try:
            status = json.loads(result.stdout)
        except ValueError:
            return None
        logged_in = status.get("loggedIn") if isinstance(status, dict) else None
        return logged_in if isinstance(logged_in, bool) else None
    said = result.stdout + result.stderr
    return False if "Not logged in" in said else True if "Logged in" in said else None


def _installs(name: str, first: str, path: str | None) -> list[str]:
    """Every copy of ``name`` on ``path`` and in the usual install folders, ``first`` first: a
    computer often has an old one (npm, ~/.claude/local) beside the one the user runs."""
    folders = [*(path or os.environ.get("PATH", "")).split(os.pathsep),
               str(Path.home() / ".local" / "bin"), str(Path.home() / ".claude" / "local"),
               "/opt/homebrew/bin", "/usr/local/bin"]
    names = [f"{name}.exe", f"{name}.cmd"] if os.name == "nt" else [name]
    seen: dict[str, str] = {os.path.realpath(first): first}
    for folder in folders:
        for candidate in names:
            full = os.path.join(folder, candidate) if folder else ""
            if full and os.path.isfile(full) and os.access(full, os.X_OK):
                seen.setdefault(os.path.realpath(full), full)
    return list(seen.values())


def _version(agent: Agent) -> tuple[int, ...]:
    try:
        out = subprocess.run([agent.path, "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=SIGN_IN_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
                             cwd=work_folder(agent), env=agent_env(agent, work_folder(agent)),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(part) for part in found.groups()) if found else ()


def _best_install(name: str, first: str, path: str | None) -> Agent:
    """The copy of ``name`` RDST runs: the one with a sign-in, newest first. A copy whose
    stored sign-in went stale fails every run while the user's own copy works."""
    agents = [Agent(name, "local", where, env_path=path) for where in _installs(name, first, path)]
    with ThreadPoolExecutor(max_workers=min(len(agents), 4)) as pool:
        signed = list(pool.map(_signed_in, agents))
        versions = list(pool.map(_version, agents)) if len(agents) > 1 else [()]
    ranked = sorted(zip(agents, signed, versions), key=lambda one: (
        one[1] is True, one[1] is not False, one[2]), reverse=True)
    agent, signed_in, _ = ranked[0]
    if len(agents) > 1:
        logger.info("%s installs: %s; running %s", name, [
            (str(one[0].path), one[1], ".".join(map(str, one[2]))) for one in ranked], agent.path)
    return replace(agent, signed_in=signed_in)


def forget_detected() -> None:
    """The next detection looks again, like after the user signs an agent in or out."""
    global _detected
    with _detect_lock:
        _detected = None


def detect(refresh: bool = False) -> list[Agent]:
    """The coding agents RDST can run, here and (on Windows) inside WSL. Asking each one
    whether it's signed in takes seconds, so a stale answer comes back at once while a fresh
    one is worked out in the background; ``refresh`` waits for a fresh one."""
    global _refreshing
    with _detect_lock:
        if not refresh and _detected:
            if time.time() - _detected[0] >= DETECT_TTL_SECONDS and not _refreshing:
                _refreshing = True
                threading.Thread(target=_detect_now, name="rdst-agent-detect",
                                 daemon=True).start()
            return list(_detected[1])
    return _detect_now()


def _detect_now() -> list[Agent]:
    global _detected, _refreshing
    try:
        agents = _find_agents()
    finally:
        with _detect_lock:
            _refreshing = False
    with _detect_lock:
        _detected = (time.time(), agents)
    return list(agents)


def _find_agents() -> list[Agent]:
    hidden = _hidden()
    if sys.platform == "darwin":
        # A login shell runs the user's startup files as RDST, and anything they touch in
        # Downloads or Documents makes macOS ask about RDST. The usual install folders come
        # first, then a desktop app's own copy; the shell only when there's neither.
        shell_path = _mac_path()
        found = {name: path for name in AGENTS
                 if name not in hidden and (path := shutil.which(name, path=shell_path))}
        if not found and not any(app_agent_path(name) for name in AGENTS):
            shell_path = _without_protected(_login_shell_path()) or shell_path
            found = _login_shell_paths(tuple(AGENTS), shell_path)
        found = {name: path for name, path in found.items() if name not in hidden}
    else:
        found = {name: path for name in AGENTS if (path := _which_local(name))}
        shell_path = _login_shell_path() if os.name != "nt" else None
        found = {**_login_shell_paths(tuple(name for name in AGENTS if name not in found),
                                      shell_path), **found}
        found = {name: path for name, path in found.items() if name not in hidden}
    agents = [_best_install(name, found[name], shell_path) for name in AGENTS if name in found]
    # No CLI, but the agent's desktop app ships one: RDST runs that copy the same way.
    apps = [agent for name in AGENTS if name not in found
            if (agent := _app_agent(name, shell_path))]
    agents += apps
    # On Windows the agent runs on Windows and reaches into WSL itself (see WSL_NOTE), so
    # its conversation can continue in the Windows app or terminal. An agent only WSL has
    # is still offered, and so is one beside an app's copy that isn't signed in.
    ready = {*found, *(agent.id for agent in apps if agent.signed_in is not False)}
    agents += [agent for agent in _detect_wsl() if agent.id not in ready | hidden]
    # One that can run comes first: it's the one the dialog offers.
    agents.sort(key=lambda agent: agent.signed_in is False)
    return agents


_apps: tuple[float, list[str]] | None = None


def desktop_apps_cached() -> list[str]:
    global _apps
    if _apps is None or time.time() - _apps[0] > DETECT_TTL_SECONDS:
        _apps = (time.time(), desktop_apps())
    return _apps[1]


def desktop_apps() -> list[str]:
    """Which agents' desktop apps are installed here: their sessions can open in them."""
    if os.name == "nt":
        import winreg

        apps = []
        for app in AGENTS:
            if f"{app}-app" in _hidden():
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, app) as key:
                    winreg.QueryValueEx(key, "URL Protocol")
                apps.append(app)
            except OSError:
                continue
        return apps
    if sys.platform == "darwin":
        bundles = {"claude": "Claude.app", "codex": "Codex.app"}
        return [app for app, bundle in bundles.items() if f"{app}-app" not in _hidden()
                and any((root / bundle).exists()
                       for root in (Path("/Applications"), Path.home() / "Applications"))]
    return []


def continue_url(run: "Run", app: str) -> str | None:
    """The deep link that opens ``run``'s session in ``app``: the Claude app imports a Claude
    Code session, and the Codex app opens a thread. Only a session on this side of the computer
    is in the app's reach."""
    if app != run.agent.id or run.agent.side != "local" or not run.session:
        return None
    if not re.fullmatch(r"[0-9A-Za-z-]{8,64}", run.session):
        return None
    return (f"claude://resume?session={run.session}" if app == "claude"
            else f"codex://threads/{run.session}")


def register(agent: Agent, url: str) -> None:
    """Add RDST's MCP server at ``url`` to ``agent``'s own settings, for every session the
    user opens, the way `rdst claude add` does. Raises OSError when the agent refuses."""
    name = server_name()
    if agent.id == "claude":
        add = [agent.path, "mcp", "add", "--scope", "user", "--transport", "http", name, url]
        remove = [agent.path, "mcp", "remove", "--scope", "user", name]
    else:
        add = [agent.path, "mcp", "add", name, "--url", url]
        remove = [agent.path, "mcp", "remove", name]
    # The address can change between runs, so the entry is replaced each time.
    # Run from RDST's own folder: an agent started in the home folder looks around it.
    where = work_folder(agent) if agent.side != "wsl" else str(rdst_data_dir())
    env = agent_env(agent, where) if agent.side != "wsl" else None
    subprocess.run(remove, capture_output=True, timeout=REGISTER_TIMEOUT_SECONDS,
                   stdin=subprocess.DEVNULL, cwd=where, env=env)
    result = subprocess.run(add, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=REGISTER_TIMEOUT_SECONDS,
                            stdin=subprocess.DEVNULL, cwd=where, env=env)
    if result.returncode != 0:
        raise OSError((result.stderr or result.stdout).strip()[:300]
                      or f"{AGENT_NAMES[agent.id]} couldn't add RDST")


# Longer prompts don't survive every OS's URL handling.
NEW_CONVERSATION_LIMIT = 6000


def new_conversation_url(app: str, prompt: str) -> str | None:
    """The deep link that opens a new conversation in ``app`` with ``prompt`` typed in,
    ready for the user to send: a Claude Code conversation, or a Codex thread."""
    if not prompt or len(prompt) > NEW_CONVERSATION_LIMIT:
        return None
    from urllib.parse import quote

    if app == "claude":
        return f"claude://code/new?q={quote(prompt, safe='')}"
    if app == "codex":
        return f"codex://threads/new?prompt={quote(prompt, safe='')}"
    return None


def _cd(folder: str | None) -> str:
    """The sh that moves into ``folder`` (made when it's RDST's own), falling back to the
    home folder."""
    if not folder:
        return "cd ~"
    make = f"mkdir -p {shlex.quote(folder)} 2>/dev/null; " if folder.endswith("/.rdst-agent") \
        else ""
    return f"{{ {make}cd {shlex.quote(folder)} 2>/dev/null || cd ~; }}"


def work_folder(agent: Agent) -> str:
    """Where an agent runs when it isn't in a project: an empty folder of RDST's own. Never
    the home folder: an agent starting there looks around it, and on a Mac that reads as
    RDST asking for Downloads, Documents, Photos and other apps' data."""
    if agent.side == "wsl" and wsl_home:
        return f"{wsl_home[1].rstrip('/')}/.rdst-agent"
    folder = rdst_data_dir() / "agent-work"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _claude_project_dir(folder: str) -> str:
    """The folder name Claude Code keeps a working folder's sessions under."""
    return re.sub(r"[^A-Za-z0-9]", "-", folder)


def move_session(agent: Agent, session: str, source: str, target: str) -> bool:
    """Copy a Claude Code conversation from the folder it ran in to ``target``, so it
    carries on there: Claude resumes a session only in its own folder. Codex resumes by id
    anywhere."""
    if agent.id != "claude" or source == target:
        return True
    if not re.fullmatch(r"[0-9A-Za-z-]{8,64}", session):
        return False
    name = f"{session}.jsonl"
    if agent.side == "wsl":
        script = (f'p="$HOME/.claude/projects"; mkdir -p "$p/{_claude_project_dir(target)}" && '
                  f'cp "$p/{_claude_project_dir(source)}/{name}" '
                  f'"$p/{_claude_project_dir(target)}/{name}"')
        try:
            return subprocess.run(wsl_script_command(script, "move-session.sh", agent.distro),
                                  capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                  ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    projects = Path.home() / ".claude" / "projects"
    origin = projects / _claude_project_dir(source) / name
    if not origin.is_file():
        return False
    try:
        destination = projects / _claude_project_dir(target)
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination / name)
    except OSError:
        return False
    return True


# What an agent RDST starts runs with.
AGENT_ENV = {"DISABLE_AUTOUPDATER": "1"}
# What RDST inherited from where it was started and an agent mustn't: the folder it was
# started in, and the session of an agent that started it.
_INHERITED = re.compile(r"^(OLDPWD|CLAUDECODE|CLAUDE_CODE_(SESSION_ID|CHILD_SESSION|"
                        r"MESSAGING_\w+|EXECPATH|ENTRYPOINT|SESSION_\w+|SSE_PORT)|"
                        r"CODEX_(SANDBOX\w*|THREAD_ID|SESSION\w*))$")


def agent_env(agent: Agent, cwd: str | None) -> dict[str, str]:
    """The environment for an agent RDST starts in ``cwd``. An agent goes by PWD for its
    folder, so one inherited from a terminal in Downloads reads as RDST opening Downloads."""
    env = {key: value for key, value in os.environ.items() if not _INHERITED.match(key)}
    env.pop("PWD", None)
    if cwd:
        env["PWD"] = cwd
    env.update(AGENT_ENV)
    if agent.env_path:
        env["PATH"] = os.pathsep.join([agent.env_path, os.environ.get("PATH", "")])
    return env


def run_folder(agent: Agent, folder: str | None) -> str | None:
    """The project folder ``agent`` can run in, as it opens it; None for the home folder.
    A Windows agent stays out of WSL's share, which isn't a folder Windows programs run in."""
    if not folder:
        return None
    if agent.side == "wsl":
        return folder if folder.startswith("/") else None
    # An agent names a folder with ~ as often as without.
    folder = os.path.expanduser(folder)
    if folder.startswith("\\\\") or not os.path.isdir(folder):
        return None
    return folder


def resume_command(run: "Run") -> str | None:
    """What a terminal runs to pick the session up, from the folder it ran in."""
    if not run.session:
        return None
    program = run.agent.id
    if run.agent.via == "app":
        # The app's copy isn't on the user's PATH.
        path = run.agent.path
        program = (f'"{path}"' if " " in path else path) if os.name == "nt" \
            else shlex.quote(path)
    command = (f"{program} --resume {run.session}" if run.agent.id == "claude"
               else f"{program} resume {run.session}")
    # Claude Code keeps a session with the folder it ran in, and resumes it only there.
    return f"cd {shlex.quote(run.folder)} && {command}" if run.folder else command


def open_terminal(run: "Run") -> None:
    """Open a terminal window that picks ``run``'s conversation up, for a user with only the
    agent's CLI. Raises OSError when there's no terminal to open."""
    if not run.session or not re.fullmatch(r"[0-9A-Za-z-]{8,64}", run.session):
        raise OSError("This conversation can't be picked up yet.")
    agent = run.agent
    args = (["--resume", run.session] if agent.id == "claude" else ["resume", run.session])
    _in_terminal(agent, args, run.folder, run.id)


def sign_in(agent: Agent) -> None:
    """Open a terminal window where the user signs ``agent`` in. Raises OSError when there's
    no terminal to open."""
    forget_detected()
    _in_terminal(agent, ["auth", "login"] if agent.id == "claude" else ["login"],
                 work_folder(agent), f"sign-in-{agent.id}")


def _in_terminal(agent: Agent, args: list[str], folder: str | None, name: str) -> None:
    """Run ``agent`` with ``args`` in a terminal window of its own, in ``folder``."""
    if agent.side == "wsl":
        script = f"{_cd(folder)} && exec {shlex.quote(agent.path)} {' '.join(args)}"
        if agent.env_path:
            script = f"PATH={shlex.quote(agent.env_path)}; export PATH; {script}"
        command = ["wsl.exe", "-d", str(agent.distro), "-e", "sh", "-c", script]
    else:
        command = [agent.path, *args]
    home = (folder if agent.side == "local" and folder else str(Path.home()))
    if os.name == "nt":
        # A new console window of its own, in the folder the conversation ran in.
        subprocess.Popen(command, cwd=None if agent.side == "wsl" else home,
                         env=agent_env(agent, home) if agent.side == "local" else None,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    elif sys.platform == "darwin":
        # A .command file opens in Terminal without Apple Events, so macOS doesn't ask the
        # user to let RDST control Terminal.
        script = rdst_data_dir() / "terminal" / f"{name}.command"
        script.parent.mkdir(parents=True, exist_ok=True)
        path = (f"PATH={shlex.quote(agent.env_path)}; export PATH\n" if agent.env_path
                else "")
        script.write_text(f"#!/bin/sh\n{path}cd {shlex.quote(home)} || cd ~\n"
                          f"exec {shlex.join(command)}\n", encoding="utf-8")
        script.chmod(0o700)
        subprocess.run(["open", "-a", "Terminal", str(script)], check=True, timeout=10,
                       stdin=subprocess.DEVNULL)
    else:
        terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
        if not terminal:
            raise OSError("RDST couldn't find a terminal to open.")
        subprocess.Popen([terminal, "--", *command], cwd=home, env=agent_env(agent, home))


def open_url(url: str) -> None:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", url], check=True, timeout=10)
    else:
        subprocess.run(["xdg-open", url], check=True, timeout=10)


def find_agent(key: str) -> Agent | None:
    return next((agent for agent in detect() if agent.key == key), None)


def _clean(value: Any, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    # A URL with credentials in it keeps its shape, never its password.
    text = re.sub(r"(://[^:/@\s]+):[^@\s]+@", r"\1@", text)
    return text[:limit] or None


def sanitize_candidates(items: Any) -> list[dict[str, Any]]:
    """The agent's proposals, cut to the fields RDST shows, with no secrets."""
    if not isinstance(items, list):
        raise ValueError("databases must be a list")
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for item in items[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        engine = str(item.get("engine") or "").lower()
        engine = {"postgres": "postgresql", "pg": "postgresql", "mariadb": "mysql"}.get(
            engine, engine)
        # A project RDST hasn't read yet, or one whose database it couldn't pin down, may
        # not know its engine.
        partial = bool(item.get("deferred") or item.get("incomplete"))
        if engine not in ("postgresql", "mysql"):
            if not partial:
                continue
            engine = None
        try:
            port = int(item["port"]) if item.get("port") not in (None, "") else None
        except (TypeError, ValueError):
            port = None
        provider = str(item.get("provider") or "other").lower()
        environment = str(item.get("environment") or "unknown").lower()
        candidate = {
            "engine": engine,
            "name": _clean(item.get("name"), 60),
            "host": _clean(item.get("host"), 255),
            "port": port,
            "database": _clean(item.get("database"), 120),
            "user": _clean(item.get("user"), 120),
            "provider": provider if provider in PROVIDERS else "other",
            "environment": environment if environment in ENVIRONMENTS else "unknown",
            "project": _clean(item.get("project")),
            "source": _clean(item.get("source")),
            "env_file": _clean(item.get("env_file")),
            "env_var": _clean(item.get("env_var"), 120),
            "password_file": _clean(item.get("password_file")),
            "password_var": _clean(item.get("password_var"), 120),
            "note": _clean(item.get("note"), 200),
            "url_file": _clean(item.get("url_file")),
            "url_host": _clean(item.get("url_host"), 255),
            "instance": _clean(item.get("instance"), 200),
            "secrets": _clean(item.get("secrets"), 120),
            # How to read an operator-managed password, as a command; RDST never reads it.
            "password_hint": _clean(item.get("password_hint"), 240),
            "incomplete": bool(item.get("incomplete")) or None,
            # Which line of RDST's own list the agent means, when it's going over one.
            "found": item["found"] if isinstance(item.get("found"), int) and
            not isinstance(item.get("found"), bool) else None,
            "deferred": bool(item.get("deferred")) or None,
            "history": bool(item.get("history")) or None,
            # Runs inside WSL rather than on this side of the computer.
            "wsl": bool(item.get("wsl")) or None,
            "reach": item.get("reach") if item.get("reach") in ("answering", "unreachable")
            else None,
            "saved_as": _clean(item.get("saved_as"), 120),
            "found_by": [text for text in (_clean(entry, 160) for entry in
                         (item.get("found_by") or [])[:4] if isinstance(entry, str)) if text]
            if isinstance(item.get("found_by"), list) else [],
        }
        key = (candidate["host"], candidate["port"], candidate["database"],
               candidate["env_file"], candidate["env_var"],
               None if candidate["host"] else (candidate["project"], candidate["name"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    for index, candidate in enumerate(out):
        candidate["id"] = str(index)
    return out


@dataclass
class Run:
    """One search or setup: the agent process, what it's doing, and what it proposed. A
    scan has no agent: RDST looked itself, and ``methods`` says where."""
    id: str
    agent: Agent | None
    started: float = field(default_factory=time.time)
    state: str = "running"  # running, done, failed, cancelled
    message: str | None = None
    steps: int = 0
    progress: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] | None = None
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    last_error: str | None = None  # the agent's last line that wasn't an event
    failure: str | None = None  # why RDST stopped it, for telemetry
    # The agent reported an error of its own, like a sign-in or API failure.
    errored: bool = False
    # The agent handed RDST databases with rdst_propose_databases.
    proposed: bool = False
    # The lines of RDST's own list the agent left out, and why, by line number.
    skipped: dict[int, str] = field(default_factory=dict)
    # The version the agent reported as it started, for telemetry.
    agent_version: str | None = None
    # How a search went over RDST's own list, for telemetry: proposed, skipped, unmentioned.
    counts: dict[str, int] | None = None
    # RDST stopped a search at its time limit, keeping what it had proposed.
    timed_out: bool = False
    kind: str = "search"  # search, or setup: connecting one database the user picked
    session: str | None = None  # the agent's own session id, to resume for a setup
    activity: list[dict[str, str]] = field(default_factory=list)
    # A setup's milestones, from the RDST tools the agent calls.
    target: str | None = None
    waiting: bool = False
    monitoring: bool = False
    methods: list[dict[str, Any]] | None = None
    # A decision the agent asked the user for, as a question and its choices.
    question: dict[str, Any] | None = None
    # The project folder the agent runs in, as the agent opens it; None for the home folder.
    folder: str | None = None
    # Settles what a search found (checked against the scan) before it reads as done.
    finish: Callable[["Run"], None] | None = field(default=None, repr=False)

    @property
    def signed_out(self) -> bool:
        return self.failure == "agent_signed_out"

    def note(self, text: str) -> None:
        self.steps += 1
        self.progress = [*self.progress, text][-PROGRESS_LIMIT:]

    def log(self, kind: str, text: str) -> None:
        entry = {"kind": kind, "text": text}
        # A waiting loop repeats itself; the activity shows each line once while it does.
        if entry in self.activity[-6:]:
            return
        self.activity = [*self.activity, entry][-ACTIVITY_LIMIT:]

    def as_dict(self) -> dict[str, Any]:
        agent = self.agent
        return {"id": self.id, "agent": agent.as_dict() if agent else None, "state": self.state,
                "message": self.message, "steps": self.steps, "progress": self.progress,
                "elapsed": round(time.time() - self.started),
                "candidates": self.candidates or [], "kind": self.kind,
                "activity": self.activity, "target": self.target, "waiting": self.waiting,
                "monitoring": self.monitoring, "session": self.session,
                "methods": self.methods, "question": self.question,
                "resume_command": resume_command(self) if agent else None,
                "continue_in": agent.id if agent and continue_url(self, agent.id) and
                agent.id in desktop_apps_cached() and not self.signed_out else None,
                "signed_out": self.signed_out,
                "in_app": in_app(self.session)}


_runs: dict[str, Run] = {}
# The search an /mcp request comes from, named by the d= parameter of the URL RDST gave it.
current_run: ContextVar[str | None] = ContextVar("agent_discovery_run", default=None)


def latest_session(agent: Agent) -> str | None:
    """The newest session ``agent`` ran for RDST, to pick up where it left off."""
    return next((run.session for run in reversed(list(_runs.values()))
                 if run.agent and run.agent.key == agent.key and run.session), None)


def get_run(run_id: str) -> Run | None:
    return _runs.get(run_id)


def propose(run_id: str | None, items: Any, skip: Any = None) -> list[dict[str, Any]]:
    """Record a search's proposals; ``run_id`` comes from the agent's MCP URL. A session the
    user continued in their own app has none, and adds to RDST's latest search."""
    run = _runs.get(run_id or "") or next(
        (run for run in reversed(list(_runs.values())) if run.kind == "search"), None)
    if run is None:
        raise LookupError("RDST isn't running a database search right now, so it can't show "
                          "these. Tell the user what you found instead.")
    if not isinstance(items, list):
        raise ValueError("databases must be a list")
    for entry in skip if isinstance(skip, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("found"), int):
            run.skipped[entry["found"]] = _clean(entry.get("reason"), 200) or "Left out"
    # The agent sends databases as it finds them; each call adds to what's there.
    fresh = sanitize_candidates([*(run.candidates or []), *items])
    added = fresh[len(run.candidates or []):]
    run.candidates = fresh
    run.proposed = True
    home = str(Path.home())
    for item in added:
        if run.agent.side == "local" and (item["project"] or "").startswith(home):
            item["project"] = "~" + item["project"][len(home):]
        if item["env_file"] and item["env_var"] and not item["host"]:
            _fill_from_url(run.agent, item)
    count = len(run.candidates)
    run.note(f"Found {count} database{'s' if count != 1 else ''}")
    return added


LOCAL = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def mark_saved(items: list[dict[str, Any]]) -> None:
    """Name the database RDST already has for each found one: the same server and port,
    and the same database when the finding names one."""
    from shared.config.targets import TargetsConfig

    try:
        config = TargetsConfig()
        config.load()
        saved = [(name, config.get(name) or {}) for name in config.list_targets()]
    except Exception:
        return

    def server(host: Any, port: Any) -> tuple:
        host = str(host or "").strip().lower()
        return ("localhost" if host in LOCAL else host, str(port or ""))

    for item in items:
        item["saved_as"] = next(
            (name for name, target in saved
             if server(target.get("host"), target.get("port")) ==
             server(item.get("host"), item.get("port"))
             and (not item.get("database") or target.get("database") == item["database"])),
            None)


def _fill_from_url(agent: Agent, item: dict[str, Any]) -> None:
    """Where a database whose URL is in an env file lives, read here; never its password."""
    from features.agent_connect import tools

    try:
        url = urlsplit(tools._read_env_file(windows_path_for(agent, item["env_file"]),
                                            item["env_var"]))
        port = url.port
    except (tools.ToolError, ValueError):
        return
    item["host"] = url.hostname or None
    item["port"] = item["port"] or port
    item["database"] = item["database"] or url.path.lstrip("/") or None
    item["user"] = item["user"] or (unquote(url.username) if url.username else None)


QUESTION_CHOICES = 4


def ask(question: Any, options: Any) -> dict[str, Any]:
    """Record the decision the agent asks the user for on the run it's in; the user's pick
    comes back to it as its next message."""
    run = _runs.get(current_run.get() or "")
    if run is None or run.kind != "setup":
        raise LookupError("Only a setup RDST started can ask the user through RDST. Say "
                          "what you need in your reply instead.")
    text = _clean(question, 300)
    choices = [choice for choice in (_clean(option, 120) for option in
                                     (options if isinstance(options, list) else [])) if choice]
    if not text or not 2 <= len(choices) <= QUESTION_CHOICES:
        raise ValueError(f"Ask one question with 2 to {QUESTION_CHOICES} short choices.")
    run.question = {"question": text, "options": choices}
    run.log("rdst", f"Asked you: {text}")
    return run.question


def _in_app_setup(target: str) -> Run | None:
    """The setup the user took to the agent's app, for a tool call from that app: its
    session has RDST registered without the run's d=."""
    taken = [run for run in _runs.values() if run.kind == "setup" and in_app(run.session)
             and run.target in (None, target)]
    return max(taken, key=lambda run: run.started, default=None)


def app_call(target: str | None, text: str) -> None:
    """What the agent in the app the user continued a setup in just asked RDST to do, so the
    setup RDST shows follows it. A call RDST's own run makes shows in that run already."""
    if current_run.get() is not None or not target:
        return
    run = _in_app_setup(target)
    if run is None:
        return
    # Whatever RDST showed before the hand-off was answered in the app.
    run.question = None
    run.log("rdst", text)


def saw(step: str, target: str) -> None:
    """A setup's milestone, from the RDST tool the agent just called, in RDST's own run or
    in the app the user continued it in."""
    run = _runs.get(current_run.get() or "")
    if run is None and current_run.get() is None:
        run = _in_app_setup(target)
        if run is not None:
            # The question RDST showed was answered in the app.
            run.question = None
    if run is None or run.kind != "setup":
        return
    run.target = target
    if step == "database_added" and run.agent and run.session:
        remember_session(target, run.agent, run.session, run.folder)
    if step == "awaiting_user":
        run.waiting = True
    elif step == "monitoring_enabled":
        run.waiting, run.monitoring = False, True
    run.log("rdst", {"database_added": f"Added {target} to RDST",
                     "awaiting_user": f"Waiting for you to turn on monitoring for {target}",
                     "monitoring_enabled": f"Monitoring is on for {target}"}.get(step, step))


def windows_path_for(agent: Agent, path: str) -> str:
    """A file the agent named, as RDST opens it: WSL paths go through the distro's share."""
    if agent.side != "wsl" or not path.startswith("/"):
        return path
    mounted = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if mounted:
        return f"{mounted.group(1).upper()}:{(mounted.group(2) or '/').replace('/', chr(92))}"
    return f"\\\\wsl.localhost\\{agent.distro}{path.replace('/', chr(92))}"


def agent_path(agent: Agent, path: str | None) -> str | None:
    """A file RDST knows by its own path, as ``agent`` opens it."""
    if not path:
        return path
    if agent.side == "wsl":
        share = re.match(r"^\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)$", path, re.IGNORECASE)
        if share:
            return share.group(1).replace("\\", "/")
        if re.match(r"^[A-Za-z]:[\\/]", path):
            return _wsl_path(PureWindowsPath(path))
        return path
    return rdst_path(path)


def _sessions_file() -> Path:
    return rdst_data_dir() / "agent-sessions.json"


def _sessions() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(_sessions_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_session(target: str, agent: Agent, session: str,
                     folder: str | None = None) -> None:
    """Note which agent conversation set ``target`` up, and the folder it ran in, so later
    help picks it up again there."""
    data = _sessions()
    data[target] = {"agent": agent.key, "session": session, "folder": folder,
                    "at": int(time.time())}
    try:
        _sessions_file().parent.mkdir(parents=True, exist_ok=True)
        _sessions_file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("RDST couldn't save the agent session for %s", target, exc_info=True)


def session_for(target: str, agent: Agent, *, in_background: bool = True) -> str | None:
    """The conversation ``agent`` had about ``target``, when it had one. One the user opened
    in the agent's app carries on there, so it isn't picked up in the background."""
    entry = _sessions().get(target) or {}
    session = entry.get("session") if entry.get("agent") == agent.key else None
    return None if in_background and in_app(session) else session


def conversation_of(target: str) -> dict[str, Any] | None:
    """The conversation that set ``target`` up, whichever agent had it."""
    entry = _sessions().get(target)
    return entry if isinstance(entry, dict) and entry.get("session") else None


def folder_for(target: str, agent: Agent) -> str | None:
    """The folder the conversation that set ``target`` up ran in."""
    entry = _sessions().get(target) or {}
    return entry.get("folder") if entry.get("agent") == agent.key else None


def _handoffs_file() -> Path:
    return rdst_data_dir() / "agent-handoffs.json"


_handoffs: dict[str, str] | None = None


def in_app(session: str | None) -> str | None:
    """The desktop app the user opened ``session`` in. The app owns the conversation from
    then on, so RDST doesn't resume it in the background."""
    global _handoffs
    if not session:
        return None
    if _handoffs is None:
        try:
            data = json.loads(_handoffs_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _handoffs = data if isinstance(data, dict) else {}
    return _handoffs.get(session)


def hand_off(session: str, app: str) -> None:
    """Note that the user opened ``session`` in ``app``."""
    in_app(session)
    assert _handoffs is not None
    _handoffs[session] = app
    try:
        _handoffs_file().parent.mkdir(parents=True, exist_ok=True)
        _handoffs_file().write_text(json.dumps(_handoffs, indent=2), encoding="utf-8")
    except OSError:
        logger.debug("RDST couldn't save that %s moved to the %s app", session, app,
                     exc_info=True)


def rdst_path(path: str) -> str:
    """A file any agent named, as RDST opens it. On Windows, a Linux path is WSL's: an agent
    there names its own files that way, whichever session it came from."""
    if os.name != "nt" or not path.startswith("/") or not wsl_home:
        return path
    return windows_path_for(Agent("codex", "wsl", "", wsl_home[0]), path)


def _wsl_path(path: Path) -> str:
    """A Windows path as WSL sees it under /mnt."""
    drive, rest = os.path.splitdrive(str(path))
    return f"/mnt/{drive.rstrip(':').lower()}{rest.replace(chr(92), '/')}"


# Stands in for the prompt argument until the command is built for where it runs.
_PROMPT = object()


def server_name() -> str:
    """RDST's MCP server name in the user's agents, the same one `rdst claude add` uses, so a
    session RDST ran keeps its tools when the user picks it up."""
    return f"rdst{rdst_instance_suffix()}"


def _command(agent: Agent, prompt_file: Path, mcp_url: str, setup: bool = False,
             resume: str | None = None, folder: str | None = None,
             title: str | None = None, model: str | None = None) -> list[str]:
    """The headless agent command, run in ``folder`` or the user's home folder. A search
    only reads; a setup also gets a shell, and ``resume`` continues the search's own
    session."""
    server = server_name()
    if agent.id == "claude":
        config = prompt_file.with_name("mcp.json")
        config.write_text(json.dumps({"mcpServers": {server: {"type": "http", "url": mcp_url}}}),
                          encoding="utf-8")
        config_arg = _wsl_path(config) if agent.side == "wsl" else str(config)
        args: list[Any] = ["-p", _PROMPT, "--output-format", "stream-json", "--verbose",
                "--mcp-config", config_arg, "--strict-mcp-config",
                "--max-turns", "100" if setup else "60",
                *(["--resume", resume] if resume else []),
                *(["--name", title] if title else []),
                *(["--model", model] if model else []),
                "--allowedTools", *(tool.format(server=server)
                                    for tool in (CLAUDE_SETUP_TOOLS if setup else CLAUDE_TOOLS))]
    else:
        sandbox = (['-c', 'sandbox_mode="workspace-write"',
                    "-c", "sandbox_workspace_write.network_access=true"] if setup
                   else ["-c", 'sandbox_mode="read-only"'])
        if _reaches_wsl(agent):
            # Codex's Windows sandbox refuses to start wsl.exe and to read WSL's share, so
            # an agent that has to reach into WSL runs without it; its prompt still keeps
            # it to reading, or to the steps RDST asks for.
            sandbox = ["-c", 'sandbox_mode="danger-full-access"']
        args = ["exec", *(["resume"] if resume else []), "--skip-git-repo-check", "--json",
                *sandbox, "-c", f'mcp_servers.{server}.url="{mcp_url}"',
                "-c", f'mcp_servers.{server}.default_tools_approval_mode="approve"',
                *([resume] if resume else []), _PROMPT]
    if agent.side == "wsl":
        # A plain sh with the PATH detection found: no startup files run, so nothing they
        # print or wait for gets in the way. The prompt comes from its file.
        read_prompt = f'"$(cat {shlex.quote(_wsl_path(prompt_file))})"'
        words = [read_prompt if a is _PROMPT else shlex.quote(a) for a in args]
        path = f"PATH={shlex.quote(agent.env_path)}; export PATH; " if agent.env_path else ""
        path += "".join(f"{key}={shlex.quote(value)}; export {key}; "
                        for key, value in AGENT_ENV.items())
        script = f"{path}{_cd(folder)} && exec {shlex.quote(agent.path)} {' '.join(words)}"
        return ["wsl.exe", "-d", str(agent.distro), "-e", "sh", "-c", script]
    prompt = prompt_file.read_text(encoding="utf-8")
    if os.name == "nt" and agent.path.lower().endswith((".cmd", ".bat")):
        # cmd.exe can't carry a multi-line argument; the agent reads the prompt instead.
        prompt = f"Read {prompt_file} and do what it asks."
    return [agent.path, *(prompt if a is _PROMPT else a for a in args)]


def _describe(event: dict[str, Any]) -> str | None:
    """One line of progress from a Claude stream-json or Codex --json event."""
    kind = event.get("type")
    if kind == "assistant":
        for part in (event.get("message") or {}).get("content") or []:
            if part.get("type") == "tool_use":
                return _tool_line(part.get("name", ""), part.get("input") or {})
    item = event.get("item") or {}
    if kind == "item.started":
        if item.get("type") == "command_execution":
            return _tool_line("Bash", {"command": item.get("command", "")})
        if item.get("type") == "mcp_tool_call":
            return _tool_line(f"mcp__rdst__{item.get('tool', '')}", {})
    return None


def _tool_line(name: str, args: dict[str, Any]) -> str:
    if name.endswith("rdst_propose_databases"):
        return "Sending what it found to RDST"
    if name.startswith("mcp__"):
        return "Checking RDST"
    target = args.get("file_path") or args.get("path") or args.get("pattern") or ""
    if name == "Read" and target:
        return f"Reading {_short(target)}"
    if name in ("Grep", "Glob"):
        return "Searching your projects"
    command = str(args.get("command") or "")
    if ".claude" in command or ".codex" in command:
        return "Reading your recent sessions"
    return "Looking through your projects" if command else "Working"


# Values that look secret are masked in what the activity shows: PGPASSWORD=..., -p..., a
# URL's password.
_SECRET = re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*)"
                     r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")


def _redact(text: str) -> str:
    text = re.sub(r"(://[^:/@\s]+):[^@\s]+@", r"\1:***@", text)
    text = _SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
    # mysql takes its password glued to -p.
    return re.sub(r"(\s-p)(?!\s)\S+", r"\1***", text) if "mysql" in text else text


def _one_line(text: Any, limit: int = 240) -> str:
    flat = " ".join(str(text or "").replace("**", "").split())
    return _redact(flat if len(flat) <= limit else flat[:limit - 1] + "...")


def _tool_detail(name: str, args: dict[str, Any]) -> str | None:
    """A tool call as a terminal-style line: the tool and what it's pointed at."""
    if name == "ToolSearch" or name == "TodoWrite":
        return None
    if name.startswith("mcp__rdst__"):
        return f"rdst {name[len('mcp__rdst__'):].removeprefix('rdst_')}"
    if name == "Bash":
        return f"$ {_one_line(args.get('command'))}"
    if name == "Read":
        return f"read {_short(args.get('file_path') or '')}"
    if name in ("Grep", "Glob"):
        where = args.get("path")
        return (f"{name.lower()} {_one_line(args.get('pattern'), 120)}"
                f"{f'  in {_short(where)}' if where else ''}")
    return name


def _activity(event: dict[str, Any]) -> list[tuple[str, str]]:
    """What the dialog's activity shows for one event: ("say", text) for the agent's own words,
    ("tool", line) for each tool it runs."""
    kind = event.get("type")
    lines: list[tuple[str, str]] = []
    if kind == "assistant":
        for part in (event.get("message") or {}).get("content") or []:
            if part.get("type") == "text" and str(part.get("text") or "").strip():
                lines.append(("say", _one_line(part["text"], 400)))
            elif part.get("type") == "tool_use":
                detail = _tool_detail(part.get("name", ""), part.get("input") or {})
                if detail:
                    lines.append(("tool", detail))
        return lines
    item = event.get("item") or {}
    if kind == "item.started" and item.get("type") == "command_execution":
        lines.append(("tool", f"$ {_one_line(item.get('command'))}"))
    elif kind == "item.started" and item.get("type") == "mcp_tool_call":
        lines.append(("tool", f"rdst {str(item.get('tool', '')).removeprefix('rdst_')}"))
    elif kind == "item.completed" and item.get("type") in ("agent_message", "reasoning"):
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(("say", _one_line(text, 400)))
    return lines


def _session_of(event: dict[str, Any]) -> str | None:
    if event.get("type") == "thread.started":
        return event.get("thread_id")
    return event.get("session_id") if event.get("type") in ("system", "result") else None


def _short(path: str) -> str:
    home = str(Path.home())
    path = str(path)
    if path.startswith(home):
        return "~" + path[len(home):]
    return re.sub(r"^/home/[^/]+", "~", path)


def _final_text(event: dict[str, Any]) -> str | None:
    if event.get("type") == "result":
        return event.get("result")
    item = event.get("item") or {}
    if event.get("type") == "item.completed" and item.get("type") == "agent_message":
        return item.get("text")
    return None


async def _follow(run: Run, sink: Any) -> str | None:
    """Read the agent's event stream into ``run`` until it exits; returns its last reply."""
    final: str | None = None
    assert run.process is not None and run.process.stdout is not None
    async for raw in run.process.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        sink.write(line + "\n")
        if not line.startswith("{"):
            run.last_error = line or run.last_error
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        text = _describe(event)
        if text and (not run.progress or run.progress[-1] != text):
            run.note(text)
        for kind, entry in _activity(event):
            run.log(kind, entry)
        run.session = run.session or _session_of(event)
        if event.get("type") == "system" and event.get("subtype") == "init":
            run.agent_version = str(event.get("claude_code_version") or "") or None
        if event.get("error") == "authentication_failed":
            run.failure = "agent_signed_out"
        if event.get("type") == "result" and event.get("is_error") or \
                event.get("type") in ("error", "turn.failed"):
            run.errored = True
            run.last_error = _final_text(event) or str(event.get("message") or "") or \
                run.last_error
        final = _final_text(event) or final
    await run.process.wait()
    return final


async def _drive(run: Run, command: list[str], cwd: str | None, log: Path) -> None:
    limit = SETUP_TIMEOUT_SECONDS if run.kind == "setup" else RUN_TIMEOUT_SECONDS
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if run.agent.side == "local":
        # The agent doesn't update itself mid-run: RDST runs the user's agent, it doesn't
        # upgrade it, and on a Mac the updater reads as RDST opening Downloads.
        kwargs["env"] = agent_env(run.agent, cwd)
    final: str | None = None
    resumed = run.session
    try:
        for attempt in range(2):
            # A run asked for just as AI was turned off would miss stop_all's sweep: it doesn't
            # start, and a retry doesn't either.
            if not ai_data_sharing_allowed():
                run.state, run.message = "cancelled", None
                break
            run.process = await asyncio.create_subprocess_exec(
                *command, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                limit=8 * 1024 * 1024, **kwargs)
            if attempt == 0:
                run.note(f"Started {run.agent.label}")
            with log.open("a" if attempt else "w", encoding="utf-8") as sink:
                final = await asyncio.wait_for(_follow(run, sink), limit)
            # Two Claude Code processes refreshing one sign-in at once: the one that loses
            # reads as signed out while the sign-in is fine, so it runs once more.
            if attempt or run.failure != "agent_signed_out" or run.agent.side != "local" or \
                    not await asyncio.to_thread(_signed_in, run.agent):
                break
            logger.info("Run %s lost a sign-in refresh; running it again", run.id)
            from shared import product_telemetry

            product_telemetry.agent_connect_step(
                "signin_retried", attempt_id=run.id, tool=f"rdst_{run.kind}_{run.agent.id}",
                agent_version=run.agent_version)
            run.failure, run.session, final = None, resumed, None
            await asyncio.sleep(RETRY_AFTER_SIGN_IN_SECONDS)
    except asyncio.TimeoutError:
        # What it found so far still counts; RDST only stops it.
        if run.candidates is None or run.kind == "setup":
            run.state, run.message = "failed", f"{run.agent.label} took too long."
            run.failure = "agent_timeout"
        else:
            run.timed_out = True
            final = (f"{AGENT_NAMES[run.agent.id]} was still looking after "
                     f"{limit // 60} minutes, so RDST stopped it here.")
    except OSError:
        run.state, run.message = "failed", f"RDST couldn't start {run.agent.label}."
        run.failure = "agent_start_failed"
    except asyncio.CancelledError:
        run.state, run.message = "cancelled", None
        raise
    finally:
        if run.process and run.process.returncode is None:
            run.process.kill()
            try:
                await asyncio.wait_for(run.process.wait(), 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug("Agent run %s didn't exit after kill", run.id)
    if run.finish is not None and run.state == "running":
        try:
            await asyncio.to_thread(run.finish, run)
        except Exception:
            logger.exception("RDST couldn't check what run %s found", run.id)
    _report_finished(run, final)


def _monitored(target: str) -> bool:
    from features.monitor.api import routes as monitor

    try:
        return target in monitor._store().monitored_targets()
    except Exception:
        logger.debug("Couldn't read whether %s is monitored", target, exc_info=True)
        return False


_AGENT_FAILURES = (
    ("agent_signed_out", ("not logged in", "please log in", "/login", "invalid api key",
                          "authentication_error", "failed to authenticate", "unauthorized",
                          "401")),
    ("agent_rate_limited", ("rate limit", "usage limit", "quota", "429", "overloaded")),
)


def _failure_class(run: Run) -> str:
    """Why a search or setup ended without its result, as a class; never the agent's text."""
    if run.failure:
        return run.failure
    said = (run.last_error or "").lower()
    for label, needles in _AGENT_FAILURES:
        if any(needle in said for needle in needles):
            return label
    if run.process is not None and run.process.returncode:
        return "agent_exited"
    return "stopped_before_adding" if run.kind == "setup" else "nothing_proposed"


def _report_finished(run: Run, final: str | None) -> None:
    from shared import product_telemetry

    step = f"{run.kind}_finished"
    tool = f"rdst_{run.kind}_{run.agent.id}"
    if run.state == "running" and _failure_class(run) == "agent_signed_out":
        run.state, run.failure = "failed", "agent_signed_out"
        run.message = (f"{run.agent.label} is signed out on this computer. Sign in to it, "
                       "then try again.")
        forget_detected()
    if run.state != "running":
        product_telemetry.agent_connect_step(
            step, attempt_id=run.id, tool=tool, ok=False,
            error_class="cancelled" if run.state == "cancelled" else _failure_class(run),
            agent_version=run.agent_version, agent_via=run.agent.via)
        return
    said_last = next((line["text"] for line in reversed(run.activity)
                      if line["kind"] == "say"), None)
    if final and _one_line(final, 400) != said_last:
        run.log("say", _one_line(final, 400))
    if run.kind == "setup":
        # Asking the user is a way to stop, as much as adding the database is.
        run.state = "done" if (run.target or run.question) and not run.errored else "failed"
        # The user may have turned monitoring on themselves while the agent waited.
        run.monitoring = run.monitoring or (run.target is not None and _monitored(run.target))
        code = run.process.returncode if run.process else None
        said = _clean(run.last_error, 200)
        run.message = (_clean(final.replace("**", "") if final else None, 400) or
                       f"{run.agent.label} stopped before adding the database"
                       f"{f' (exit {code})' if code else ''}{f': {said}' if said else '.'}")
        product_telemetry.agent_connect_step(
            step, attempt_id=run.id, tool=tool, ok=run.state == "done",
            error_class=None if run.state == "done" else _failure_class(run),
            agent_version=run.agent_version, agent_via=run.agent.via)
        return
    code = run.process.returncode if run.process else None
    if run.candidates is not None and run.proposed and not run.errored and \
            (not code or run.timed_out):
        run.state = "done"
        run.message = _clean(final.replace("**", "") if final else None, 400)
    elif run.candidates is not None:
        # What RDST found by itself still shows, but as RDST's list: the agent didn't make
        # sense of it, and the user is told why.
        run.state = "failed"
        said = _clean(run.last_error or final, 200)
        run.message = (f"{run.agent.label} couldn't go through what RDST found"
                       f"{f': {said}' if said else '.'}")
    else:
        run.state = "failed"
        said = _clean(run.last_error, 200)
        run.message = (_clean(final.replace("**", "") if final else None, 400) or
                       f"{run.agent.label} stopped without proposing any databases"
                       f"{f' (exit {code})' if code else ''}{f': {said}' if said else '.'}")
    product_telemetry.agent_connect_step(
        step, attempt_id=run.id, tool=tool, ok=run.state == "done",
        error_class=None if run.state == "done" else _failure_class(run),
        agent_version=run.agent_version, counts=run.counts, agent_via=run.agent.via)


WSL_NOTE = """

Some of my files live inside WSL (the {distro} distribution, home folder {home}), while you \
run on Windows. Read a single file through \\\\wsl.localhost\\{distro}\\ paths, but search \
and list inside WSL, which is much faster than going through that share from Windows: \
wsl.exe -d {distro} -e sh -c "grep -rIl --exclude-dir=node_modules <pattern> <folder>". Use the \
same wsl.exe form for docker port or a psql check. RDST runs on Windows: a database WSL \
publishes on localhost is reachable on Windows' localhost too.
"""


def _reaches_wsl(agent: Agent) -> bool:
    """Whether ``agent`` runs on Windows on a computer that also has WSL."""
    return os.name == "nt" and agent.side == "local" and wsl_home is not None


async def start(agent: Agent, prompt: str, mcp_url: str, kind: str = "search",
                resume: str | None = None, folder: str | None = None,
                title: str | None = None, model: str | None = None,
                finish: Callable[["Run"], None] | None = None) -> Run:
    """Run ``agent`` on ``prompt`` with RDST's MCP server at ``mcp_url`` (run id appended).
    A setup run can ``resume`` an earlier session, and runs in the project ``folder`` it's
    about (as the agent opens it) when there is one. ``title`` names the conversation in
    the agent's app, ``model`` picks a faster one where it helps, and ``finish`` settles what
    the run found before it reads as done."""
    run_id = secrets.token_hex(8)
    folder = run_folder(agent, folder) or work_folder(agent)
    run = Run(id=run_id, agent=agent, kind=kind, session=resume, folder=folder, finish=finish)
    if _reaches_wsl(agent) and wsl_home:
        prompt += WSL_NOTE.format(distro=wsl_home[0], home=wsl_home[1])
    work = rdst_data_dir() / "agent-discovery" / run_id
    work.mkdir(parents=True, exist_ok=True)
    prompt_file = work / "prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8", newline="\n")
    separator = "&" if "?" in mcp_url else "?"
    command = _command(agent, prompt_file, f"{mcp_url}{separator}d={run_id}",
                       setup=kind == "setup", resume=resume, folder=folder, title=title,
                       model=model)
    cwd = None if agent.side == "wsl" else folder or str(Path.home())
    keep(run)
    run.task = asyncio.create_task(_drive(run, command, cwd, work / "agent.log"))
    return run


def keep(run: Run) -> None:
    """Remember ``run``, forgetting the oldest finished ones past RUN_LIMIT."""
    _runs[run.id] = run
    while len(_runs) > RUN_LIMIT:
        oldest = next(iter(_runs))
        if _runs[oldest].state == "running":
            break
        del _runs[oldest]


def _stop_in_wsl(run: Run) -> None:
    try:
        subprocess.run(["wsl.exe", "-d", str(run.agent.distro), "-e", "pkill", "-f", run.id],
                       capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        logger.debug("Couldn't stop run %s inside WSL", run.id, exc_info=True)


async def stop_all() -> None:
    """RDST is shutting down: no agent it started keeps running without it."""
    for run_id in [run.id for run in _runs.values() if run.state == "running"]:
        await cancel(run_id)


async def cancel(run_id: str) -> Run | None:
    run = _runs.get(run_id)
    if run is None:
        return None
    if run.state == "running":
        run.state, run.message = "cancelled", None
        if run.agent.side == "wsl":
            # Ending wsl.exe can leave the agent running inside WSL; its command line names
            # the run (the MCP config path or URL).
            await asyncio.to_thread(_stop_in_wsl, run)
        if run.process and run.process.returncode is None:
            run.process.kill()
            try:
                await asyncio.wait_for(run.process.wait(), 5)
            except asyncio.TimeoutError:
                logger.warning("Agent search %s didn't stop after kill", run.id)
        if run.task:
            run.task.cancel()
    return run
