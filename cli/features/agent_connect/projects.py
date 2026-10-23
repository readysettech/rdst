"""The project folders the user's recent coding agent sessions ran in.

RDST reads them from the session files itself, so a search starts from a short list of
projects instead of an agent crawling the disk. On Windows, WSL's sessions count too, and
each folder is named the way the searching agent opens it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path, PureWindowsPath

from features.agent_connect import discover

PROJECT_LIMIT = 20
RECENT_DAYS = 60
# A session names its folder in its first lines; the rest is conversation.
HEAD_LINES = 40
WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
# What a folder an app runs from has at its top. Sessions also run in data and scratch
# folders, which are no place to look for a database.
PROJECT_MARKERS = (".git", ".env", "package.json", "pyproject.toml", "requirements.txt",
                   "setup.py", "manage.py", "Pipfile", "go.mod", "Cargo.toml", "Gemfile",
                   "composer.json", "pom.xml", "build.gradle", "docker-compose.yml",
                   "docker-compose.yaml", "compose.yaml", "compose.yml", "Dockerfile",
                   "prisma", "mix.exs")


# Scratch folders on either side of the computer, as session files name them.
SCRATCH = re.compile(r"^/(tmp|var/tmp)(/|$)")


# Folders macOS asks the user about before an app reads them, each its own prompt. RDST
# reads nothing in them until the user picks a project there.
PROTECTED = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Movies",
             "Library/Mobile Documents", "Library/CloudStorage")


def protected(folder: str) -> str | None:
    """The macOS folder ``folder`` is in that asks before RDST reads it, like "Documents"."""
    if sys.platform != "darwin":
        return None
    if folder.startswith("/Volumes/"):
        return "an external drive"
    home = str(Path.home())
    for name in PROTECTED:
        root = f"{home}/{name}"
        if folder == root or folder.startswith(root + "/"):
            return "iCloud Drive" if name.startswith("Library/") else name
    return None


def _is_project(folder: str) -> bool:
    if Path(folder).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        return False
    return any(os.path.exists(os.path.join(folder, marker)) for marker in PROJECT_MARKERS)


def _newest(folder: Path) -> tuple[float, Path] | None:
    best: tuple[float, Path] | None = None
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.endswith(".jsonl") and entry.is_file():
                    mtime = entry.stat().st_mtime
                    if best is None or mtime > best[0]:
                        best = (mtime, Path(entry.path))
    except OSError:
        return None
    return best


def _cwd(session: Path) -> str | None:
    try:
        with session.open(encoding="utf-8", errors="replace") as lines:
            for _, line in zip(range(HEAD_LINES), lines):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload")
                cwd = event.get("cwd") or (payload.get("cwd") if isinstance(payload, dict)
                                           else None)
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


def _sessions(home: Path, since: float) -> Iterator[tuple[float, Path]]:
    """Each Claude Code project's newest session, and each recent Codex session."""
    claude = home / ".claude" / "projects"
    try:
        folders = [Path(entry.path) for entry in os.scandir(claude) if entry.is_dir()]
    except OSError:
        folders = []
    for folder in folders:
        newest = _newest(folder)
        if newest and newest[0] >= since:
            yield newest
    codex = home / ".codex" / "sessions"
    day = date.fromtimestamp(since)
    while day <= date.today():
        folder = codex / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        if folder.is_dir():
            for session in folder.glob("*.jsonl"):
                try:
                    yield session.stat().st_mtime, session
                except OSError:
                    continue
        day += timedelta(days=1)


# WSL's own files are quick to read inside WSL and slow through its share, so RDST on
# Windows lists WSL's recent project folders with one script run there: each Claude Code
# project's newest session and the recent Codex sessions, the folder each ran in, and only
# the folders that look like code. Lines are "RDST_PROJECT<tab>mtime<tab>folder".
WSL_PROJECTS = r"""
cd "$HOME" 2>/dev/null || exit 0
{
  for d in .claude/projects/*/; do
    f=$(ls -t "$d" 2>/dev/null | grep '\.jsonl$' | head -n 1)
    [ -n "$f" ] && stat -c '%Y %n' "$d$f" 2>/dev/null
  done
  find .codex/sessions -name '*.jsonl' -mtime -DAYS -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -n 300
} | sort -rn | head -n 400 | while read -r t f; do
  c=$(head -c 65536 "$f" 2>/dev/null | grep -o '"cwd":"[^"]*"' | head -n 1 | cut -d'"' -f4)
  [ -n "$c" ] && printf '%s\t%s\n' "${t%.*}" "$c"
done | awk -F '\t' '!seen[$2]++' | head -n LIMIT | while IFS="$(printf '\t')" read -r t c; do
  case "$c" in /tmp|/tmp/*|/var/tmp|/var/tmp/*|/|"$HOME") continue ;; esac
  [ -d "$c" ] || continue
  for m in MARKERS; do
    if [ -e "$c/$m" ]; then printf 'RDST_PROJECT\t%s\t%s\n' "$t" "$c"; break; fi
  done
done
"""


def _wsl_projects(days: int, limit: int) -> list[tuple[float, str]]:
    """WSL's recent project folders, as WSL names them, from one script run there."""
    import shlex
    import subprocess

    script = (WSL_PROJECTS.replace("DAYS", str(days)).replace("LIMIT", str(limit * 5))
              .replace("MARKERS", " ".join(shlex.quote(m) for m in PROJECT_MARKERS)))
    try:
        command = discover.wsl_script_command(script, "projects.sh",
                                              discover.wsl_home[0] if discover.wsl_home
                                              else None)
        out = subprocess.run(command, capture_output=True, timeout=30,
                             stdin=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                             ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "RDST_PROJECT" and parts[1].isdigit():
            found.append((float(parts[1]), parts[2]))
    return found


def _homes() -> list[tuple[Path, str | None]]:
    """The home folders RDST reads sessions from, each with the WSL share its paths live
    under (None for this side's own home)."""
    homes: list[tuple[Path, str | None]] = [(Path.home(), None)]
    if os.name == "nt" and discover.wsl_home:
        distro, home = discover.wsl_home
        share = f"\\\\wsl.localhost\\{distro}"
        homes.append((Path(share + home.replace("/", "\\")), share))
    return homes


def recent_projects(agent: discover.Agent | None, limit: int = PROJECT_LIMIT,
                    days: int = RECENT_DAYS) -> list[str]:
    """The folders the user worked in lately, newest first, as ``agent`` opens them, or as
    RDST does without one."""
    since = time.time() - days * 86400
    newest: dict[str, tuple[float, str]] = {}
    for home, share in _homes():
        if share:
            in_wsl = agent is not None and agent.side == "wsl"
            for mtime, cwd in _wsl_projects(days, limit):
                if mtime < since or SCRATCH.match(cwd):
                    continue
                theirs = cwd if in_wsl else share + cwd.replace("/", "\\")
                if theirs not in newest or newest[theirs][0] < mtime:
                    newest[theirs] = (mtime, theirs)
            continue
        skip = {str(home).rstrip("\\/").lower(), "/", "c:\\"}
        if share and discover.wsl_home:
            skip.add(discover.wsl_home[1].rstrip("/").lower())
        sessions = sorted(_sessions(home, since), reverse=True)
        seen: set[str] = set()
        for mtime, session in sessions:
            if len(seen) >= limit * 5:
                break
            cwd = _cwd(session)
            if not cwd or cwd.rstrip("\\/").lower() in skip or SCRATCH.match(cwd):
                continue
            seen.add(cwd)
            # Where RDST checks it's still there, and how the agent (or RDST) opens it.
            in_wsl = agent is not None and agent.side == "wsl"
            if WINDOWS_PATH.match(cwd):
                here = cwd if os.name == "nt" else None
                theirs = discover._wsl_path(PureWindowsPath(cwd)) if in_wsl else cwd
            elif share or (os.name == "nt" and discover.wsl_home):
                # A Linux path is WSL's, whichever side's session named it.
                here = discover.rdst_path(cwd)
                theirs = cwd if in_wsl else here
            else:
                here = theirs = cwd
            # A protected folder can't be checked without macOS asking; its sessions say
            # it's a project.
            if here is None or not protected(here) and (
                    not os.path.isdir(here) or not _is_project(here)):
                continue
            if theirs in newest and newest[theirs][0] >= mtime:
                continue
            newest[theirs] = (mtime, theirs)
    ordered = sorted(newest.values(), reverse=True)
    return [path for _, path in ordered[:limit]]


def here_path(cwd: str) -> str | None:
    """A session's folder as RDST opens it: on Windows, a Linux path is WSL's."""
    if WINDOWS_PATH.match(cwd):
        return cwd if os.name == "nt" else None
    if os.name == "nt" and discover.wsl_home:
        return discover.rdst_path(cwd)
    return cwd


def recent_sessions(per_project: int, days: int = RECENT_DAYS,
                    limit: int = 400) -> list[tuple[Path, str | None, str]]:
    """This side's newest agent sessions, a few per project folder: each session file, the
    folder it ran in as RDST opens it, and which agent's it is."""
    since = time.time() - days * 86400
    home = Path.home()
    picked: list[tuple[float, Path, str]] = []
    claude = home / ".claude" / "projects"
    try:
        folders = [Path(entry.path) for entry in os.scandir(claude) if entry.is_dir()]
    except OSError:
        folders = []
    for folder in folders:
        try:
            files = [(entry.stat().st_mtime, Path(entry.path)) for entry in os.scandir(folder)
                     if entry.name.endswith(".jsonl") and entry.is_file()]
        except OSError:
            continue
        for mtime, path in sorted(files, reverse=True)[:per_project]:
            if mtime >= since:
                picked.append((mtime, path, "Claude Code"))
    codex = sorted(((mtime, path) for mtime, path in _sessions(home, since)
                    if ".codex" in path.parts), reverse=True)[:limit]
    out = []
    per_folder: dict[str | None, int] = {}
    for _, path, agent in sorted([*picked, *((m, p, "Codex") for m, p in codex)],
                                 reverse=True):
        cwd = _cwd(path)
        if agent == "Codex":
            per_folder[cwd] = per_folder.get(cwd, 0) + 1
            if per_folder[cwd] > per_project:
                continue
        out.append((path, here_path(cwd) if cwd else None, agent))
    return out[:limit]


REQUEST_LINES = 200
REQUEST_LIMIT = 160


def _request_text(event: dict) -> str | None:
    """The user's own words in one session line, from Claude Code or Codex."""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if payload.get("type") == "user_message":
        return str(payload.get("message") or "")
    message = event.get("message") if event.get("type") == "user" else \
        payload if payload.get("type") == "message" and payload.get("role") == "user" else None
    if not isinstance(message, dict) or event.get("isMeta"):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "") for part in content
                        if isinstance(part, dict) and part.get("type") in ("text", "input_text"))
    return None


def first_request(session: Path) -> str | None:
    """What the user first asked in a session, in a line: what they were doing in its folder.
    Context an agent adds itself (its environment, instructions, command output) is skipped."""
    try:
        with session.open(encoding="utf-8", errors="replace") as lines:
            for _, line in zip(range(REQUEST_LINES), lines):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                text = _request_text(event) if isinstance(event, dict) else None
                text = " ".join((text or "").split())
                if text and not text.startswith(("<", "Caveat:", "# AGENTS.md", "[Request")):
                    return text[:REQUEST_LIMIT]
    except OSError:
        return None
    return None


def recent_requests(days: int = RECENT_DAYS) -> dict[str, str]:
    """For each folder this side's agent sessions ran in, what the newest one was about."""
    requests: dict[str, str] = {}
    for session, folder, _agent in recent_sessions(per_project=3, days=days):
        if folder and folder not in requests:
            asked = first_request(session)
            # RDST's own runs start with its prompt; they say nothing about the folder.
            if asked and not asked.startswith(("RDST", "I want RDST")):
                requests[folder] = asked
    return requests
