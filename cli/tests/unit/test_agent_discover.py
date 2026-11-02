"""Agent discovery: RDST runs the user's coding agent to find databases in their recent
projects, the agent proposes them over /mcp, and the user adds the ones they pick."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from features.agent_connect import discover, session, tools
from features.agent_connect.prompt import render_discover_prompt
from tests.unit.test_agent_connect import _request, app, data_dir, run  # noqa: F401

ACCEPT = "application/json, text/event-stream"
CLAUDE = discover.Agent("claude", "local", "/usr/bin/claude")
CODEX_WSL = discover.Agent("codex", "wsl", "/home/me/.nvm/bin/codex", "Ubuntu",
                           "/home/me/.nvm/bin:/mnt/c/Program Files/x")


@pytest.fixture(autouse=True)
def _no_runs(monkeypatch):
    monkeypatch.setattr(discover, "_runs", {})
    monkeypatch.setattr(discover, "_handoffs", None)


def _fake_run(agent=CLAUDE, candidates=None) -> discover.Run:
    item = discover.Run(id="r1", agent=agent, candidates=candidates)
    discover._runs[item.id] = item
    return item


def _propose(app, run_id, databases):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "rdst_propose_databases", "arguments": {"databases": databases}}}
    path = f"/mcp?d={run_id}" if run_id else "/mcp"
    return run(_request(app, "POST", path, json=body, headers={"Accept": ACCEPT}))


def test_proposals_keep_no_secrets_and_only_postgres_or_mysql():
    found = discover.sanitize_candidates([
        {"engine": "postgres", "host": "db.x.supabase.co", "port": "5432", "database": "app",
         "provider": "supabase", "environment": "production",
         "note": "postgresql://admin:hunter2@db.x.supabase.co/app", "password": "hunter2"},  # trufflehog:ignore
        {"engine": "sqlite", "database": "app.db"},
        {"engine": "mysql", "host": "localhost", "port": "nope", "provider": "mainframe",
         "environment": "qa"},
        {"engine": "postgresql", "host": "db.x.supabase.co", "port": 5432, "database": "app"},
    ])
    assert [item["engine"] for item in found] == ["postgresql", "mysql"]
    assert "hunter2" not in json.dumps(found)
    assert found[0]["note"] == "postgresql://admin@db.x.supabase.co/app"
    assert found[0]["port"] == 5432 and found[1]["port"] is None
    assert found[1]["provider"] == "other" and found[1]["environment"] == "unknown"
    assert [item["id"] for item in found] == ["0", "1"]
    with pytest.raises(ValueError):
        discover.sanitize_candidates("not a list")


def test_a_search_proposes_over_mcp_without_ticking_the_paste_flow(app):
    item = _fake_run()
    run(_request(app, "POST", "/api/agent-connect/prompt"))
    seen, _ = None, None

    async def watch():
        subscriber, _backlog = session.events.subscribe()
        try:
            response = await _request(app, "POST", "/mcp?d=r1", json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "rdst_propose_databases", "arguments": {"databases": [
                        {"engine": "postgresql", "host": "localhost", "database": "shop"}]}}},
                headers={"Accept": ACCEPT})
            return response, [subscriber.queue.get_nowait()
                              for _ in range(subscriber.queue.qsize())]
        finally:
            session.events.unsubscribe(subscriber)

    response, seen = run(watch())
    result = response.json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["received"] == 1
    assert item.candidates[0]["database"] == "shop"
    assert all(event["step"] != "connected" for event in seen)


def test_proposed_projects_read_from_the_home_folder(monkeypatch):
    monkeypatch.setattr(discover.Path, "home", lambda: Path("/srv/people/me"))
    _fake_run()
    found = discover.propose("r1", [
        {"engine": "postgresql", "host": "a", "project": "/srv/people/me/work/shop"},
        {"engine": "mysql", "host": "b", "project": "/opt/legacy"}])
    assert [item["project"] for item in found] == ["~/work/shop", "/opt/legacy"]


def test_a_url_in_an_env_file_shows_where_it_points_without_its_password(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://app:hunter2@db.x.supabase.co:6543/shop\n",  # trufflehog:ignore
                   encoding="utf-8")
    _fake_run()
    found = discover.propose("r1", [{"engine": "postgresql", "provider": "supabase",
                                     "env_file": str(env), "env_var": "DATABASE_URL"}])
    assert (found[0]["host"], found[0]["port"], found[0]["database"], found[0]["user"]) == \
        ("db.x.supabase.co", 6543, "shop", "app")
    assert "hunter2" not in json.dumps(found)


def test_proposing_with_no_search_asks_for_one(app):
    result = _propose(app, None, [{"engine": "postgresql"}]).json()["result"]
    assert result["isError"] is True
    assert "Tell the user what you found" in result["content"][0]["text"]


def test_a_continued_session_adds_to_the_latest_search(app):
    item = _fake_run()
    discover._runs["s1"] = discover.Run(id="s1", agent=CLAUDE, kind="setup")
    result = _propose(app, None, [{"engine": "postgresql", "host": "localhost",
                                   "database": "shop"}]).json()["result"]
    assert result["isError"] is False
    assert item.candidates[0]["database"] == "shop"


def test_claude_runs_headless_with_only_read_tools(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("find them", encoding="utf-8")
    command = discover._command(CLAUDE, prompt, "http://127.0.0.1:9/mcp?d=r1")
    assert command[:3] == ["/usr/bin/claude", "-p", "find them"]
    assert "--strict-mcp-config" in command
    server = discover.server_name()
    assert command[command.index("--allowedTools") + 1:] == [
        "Read", "Grep", "Glob", f"mcp__{server}__rdst_propose_databases",
        f"mcp__{server}__rdst_status"]
    config = json.loads((tmp_path / "mcp.json").read_text())
    assert config["mcpServers"][server]["url"] == "http://127.0.0.1:9/mcp?d=r1"


def test_codex_in_wsl_reads_its_prompt_from_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "_wsl_path", lambda path: f"/mnt/c/rdst/{Path(path).name}")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("find 'them' \"now\"", encoding="utf-8")
    command = discover._command(CODEX_WSL, prompt, "http://172.20.0.1:9/mcp?d=r1")
    assert command[:6] == ["wsl.exe", "-d", "Ubuntu", "-e", "sh", "-c"]
    script = command[6]
    assert script.startswith("PATH='/home/me/.nvm/bin:/mnt/c/Program Files/x'; export PATH; "
                             "DISABLE_AUTOUPDATER=1; export DISABLE_AUTOUPDATER; "
                             "cd ~ && exec /home/me/.nvm/bin/codex exec ")
    assert script.endswith('"$(cat /mnt/c/rdst/prompt.txt)"')
    assert "find" not in script
    assert "default_tools_approval_mode=\"approve\"" in script
    assert "sandbox_mode=\"read-only\"" in script


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX login shells")
def test_the_login_shell_path_finds_an_agent_behind_a_function(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "claude").chmod(0o755)
    shell = tmp_path / "shell"
    # Like a profile that adds ~/.local/bin and wraps claude in a function.
    shell.write_text(f'#!/bin/sh\nPATH="{bin_dir}:$PATH"\nclaude() {{ :; }}\n'
                     'shift; eval "$1"\n', encoding="utf-8")
    shell.chmod(0o755)
    monkeypatch.setenv("SHELL", str(shell))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    path = discover._login_shell_path()
    assert path.startswith(f"{bin_dir}:")
    assert discover._login_shell_paths(("claude", "codex"), path) == {
        "claude": str(bin_dir / "claude")}


def test_wsl_detection_reads_only_its_own_lines(monkeypatch):
    noisy = ("Welcome! token eyJhbGciOi...\nRDST_DISTRO=Ubuntu\nRDST_HOME=/home/me\n"
             "RDST_PATH=/home/me/.local/bin:/usr/bin\n/home/me/.local/bin/claude\n"
             "RDST_SIGNED_IN=claude:1\n")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["stdin"] = command, kwargs.get("stdin")
        return subprocess.CompletedProcess(command, 0, noisy.encode(), b"")

    monkeypatch.setattr(discover.os, "name", "nt")
    monkeypatch.setattr(discover.shutil, "which", lambda name: "wsl.exe")
    monkeypatch.setattr(discover.subprocess, "run", fake_run)
    monkeypatch.setattr(discover, "wsl_script_command",
                        lambda script, name, distro=None: ["wsl.exe", "-e", "sh", name])
    agents = discover._detect_wsl()
    assert seen["command"] == ["wsl.exe", "-e", "sh", "detect.sh"]
    assert seen["stdin"] is subprocess.DEVNULL
    assert agents == [discover.Agent("claude", "wsl", "/home/me/.local/bin/claude", "Ubuntu",
                                     "/home/me/.local/bin:/usr/bin", signed_in=True)]
    assert discover.wsl_home == ("Ubuntu", "/home/me")


def test_a_wsl_script_runs_from_a_file_since_wsl_never_closes_a_piped_stdin(data_dir,
                                                                             monkeypatch):
    monkeypatch.setattr(discover, "_wsl_path", lambda path: f"/mnt/c/rdst/{Path(path).name}")
    command = discover.wsl_script_command("echo hi\n", "detect.sh", "Ubuntu")
    assert command == ["wsl.exe", "-d", "Ubuntu", "-e", "sh", "/mnt/c/rdst/detect.sh"]
    assert (data_dir / "wsl" / "detect.sh").read_bytes() == b"echo hi\n"
    assert "-lic" in discover.WSL_DETECT


def test_a_local_agent_runs_with_the_login_shells_path(tmp_path, monkeypatch):
    seen = {}

    async def fake_exec(*command, **kwargs):
        seen.update(kwargs)
        raise OSError("stop here")

    monkeypatch.setattr(discover.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("PATH", "/usr/bin")
    agent = discover.Agent("claude", "local", "/opt/homebrew/bin/claude",
                           env_path="/opt/homebrew/bin:/Users/me/.nvm/bin")
    item = discover.Run(id="p1", agent=agent)
    run(discover._drive(item, ["claude"], None, tmp_path / "log"))
    assert seen["env"]["PATH"] == f"/opt/homebrew/bin:/Users/me/.nvm/bin{os.pathsep}/usr/bin"
    assert item.state == "failed"


def test_wsl_files_open_through_the_distro_share():
    assert discover.windows_path_for(CODEX_WSL, "/home/me/app/.env") == \
        "\\\\wsl.localhost\\Ubuntu\\home\\me\\app\\.env"
    assert discover.windows_path_for(CODEX_WSL, "/mnt/d/work/.env") == "D:\\work\\.env"
    assert discover.windows_path_for(CLAUDE, "/home/me/app/.env") == "/home/me/app/.env"


def test_the_prompt_names_the_other_sides_sessions_and_never_asks_for_passwords():
    text = render_discover_prompt(["/mnt/c/Users/me/.claude", "/mnt/c/Users/me/.codex"])
    assert "/mnt/c/Users/me/.claude and /mnt/c/Users/me/.codex" in text
    assert "rdst_propose_databases" in text and "Never pass passwords" in text
    assert "other side" not in render_discover_prompt()
    assert all(ord(char) < 128 for char in text)


def test_adding_a_found_database_reads_its_file(app, tmp_path, monkeypatch):
    from features.agent_connect import tools

    async def reached(app, args, via="mcp", announce=True):
        return {"target": args["name"], "ok": True}

    monkeypatch.setattr(tools, "test_database", reached)
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://app:pw@db.local:5432/shop\n", encoding="utf-8")  # trufflehog:ignore
    secrets = tmp_path / ".env.local"
    secrets.write_text("PGPASS=pw2\n", encoding="utf-8")
    _fake_run(candidates=discover.sanitize_candidates([
        {"engine": "postgresql", "name": "shop", "env_file": str(env),
         "env_var": "DATABASE_URL"},
        {"engine": "postgresql", "name": "shop", "host": "db.local", "port": 5433,
         "database": "orders", "user": "app", "password_file": str(secrets),
         "password_var": "PGPASS"},
        {"engine": "mysql", "name": "legacy", "host": "db.local", "database": "old"},
    ]))
    first = run(_request(app, "POST", "/api/agent-connect/discover/r1/add",
                         json={"candidate": "0"}))
    assert first.status_code == 200, first.text
    assert first.json()["target"] == "shop"
    second = run(_request(app, "POST", "/api/agent-connect/discover/r1/add",
                          json={"candidate": "1"}))
    assert second.status_code == 200, second.text
    assert second.json()["target"] == "shop-2"
    missing = run(_request(app, "POST", "/api/agent-connect/discover/r1/add",
                           json={"candidate": "2"}))
    assert missing.status_code == 409
    assert "Fill in the form" in missing.json()["detail"]
    unknown = run(_request(app, "POST", "/api/agent-connect/discover/r1/add",
                           json={"candidate": "9"}))
    assert unknown.status_code == 404


def test_starting_a_search_is_for_this_computer_only(app, monkeypatch):
    monkeypatch.setattr(discover, "detect", lambda refresh=False: [CLAUDE])
    unknown = run(_request(app, "POST", "/api/agent-connect/discover",
                           json={"agent": "codex:local"}))
    assert unknown.status_code == 404
    cross_site = run(_request(app, "POST", "/api/agent-connect/discover",
                              json={"agent": "claude:local"},
                              headers={"Origin": "https://evil.example"}))
    assert cross_site.status_code == 403
    remote = run(_request(app, "POST", "/api/agent-connect/discover",
                          json={"agent": "claude:local"}, client=("10.0.0.5", 5000),
                          headers={"Origin": "http://127.0.0.1:8787"}))
    assert remote.status_code == 403
    agents = run(_request(app, "GET", "/api/agent-connect/agents")).json()
    assert agents["agents"] == [{"key": "claude:local", "id": "claude", "side": "local",
                                 "label": "Claude Code", "name": "Claude", "signed_in": None,
                                 "via": "cli"}]


def test_a_search_follows_the_agent_and_ends_with_its_reply(tmp_path, monkeypatch, data_dir):
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls ~/.claude/projects"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__rdst__rdst_propose_databases", "input": {}}]}},
        {"type": "result", "result": "Found one database."},
    ]
    script = "import json\n" + "".join(
        f"print({json.dumps(json.dumps(line))}, flush=True)\n" for line in lines)
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "print('bashrc noise')\n" + script])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        discover.propose(item.id, [{"engine": "mysql", "host": "h"}])
        await item.task
        return item

    item = run(go())
    assert item.state == "done"
    assert item.message == "Found one database."
    assert "Reading your recent sessions" in item.progress
    assert "Sending what it found to RDST" in item.progress


def test_a_search_that_proposes_nothing_says_so(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c",
        "import sys; print('bashrc noise', flush=True); print('Out of disk', file=sys.stderr); sys.exit(3)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await item.task
        return item

    item = run(go())
    assert item.state == "failed"
    assert item.message == \
        "Claude Code stopped without proposing any databases (exit 3): Out of disk"
    assert item.as_dict()["signed_out"] is False


def test_an_agent_signed_out_on_this_computer_says_so_and_offers_its_sign_in(
        monkeypatch, data_dir):
    """Claude Code reports an expired sign-in as an API error event, then exits cleanly."""
    events = [{"type": "system", "subtype": "init", "session_id": "s-1234567890"},
              {"type": "assistant", "error": "authentication_failed",
               "message": {"content": [{"type": "text", "text": "Failed to authenticate: "
                                        "OAuth session expired and could not be refreshed"}]}},
              {"type": "result", "is_error": True, "session_id": "s-1234567890",
               "result": "Failed to authenticate: OAuth session expired and could not be "
                         "refreshed"}]
    script = "import json; [print(json.dumps(e), flush=True) for e in %r]" % events
    monkeypatch.setattr(discover, "_command",
                        lambda agent, prompt, url, **kw: [sys.executable, "-c", script])
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])

    async def go(kind):
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp", kind=kind)
        await item.task
        return item

    for kind in ("search", "setup"):
        item = run(go(kind))
        shown = item.as_dict()
        assert item.state == "failed" and shown["signed_out"] is True
        assert item.message == ("Claude Code is signed out on this computer. Sign in to it, "
                                "then try again.")
        # The Claude app has a sign-in of its own: carrying on there isn't the way out.
        assert shown["continue_in"] is None
        assert discover._failure_class(item) == "agent_signed_out"


def test_a_run_that_lost_a_sign_in_refresh_runs_once_more(monkeypatch, data_dir, tmp_path):
    """Another Claude Code refreshed the sign-in first: this one reads as signed out, but
    the sign-in is fine, so RDST runs it again instead of asking the user to sign in."""
    marker = tmp_path / "ran-once"
    failed = {"type": "assistant", "error": "authentication_failed",
              "message": {"content": [{"type": "text", "text": "Failed to authenticate"}]}}
    done = {"type": "result", "session_id": "s-0987654321", "result": "Found nothing."}
    script = ("import json, os, sys\n"
              f"m = {str(marker)!r}\n"
              "first = not os.path.exists(m)\n"
              "open(m, 'w').close()\n"
              f"print(json.dumps({failed!r} if first else {done!r}), flush=True)\n")
    monkeypatch.setattr(discover, "_command",
                        lambda agent, prompt, url, **kw: [sys.executable, "-c", script])
    monkeypatch.setattr(discover, "_signed_in", lambda agent: True)
    monkeypatch.setattr(discover, "RETRY_AFTER_SIGN_IN_SECONDS", 0)

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await item.task
        return item

    item = run(go())
    assert item.as_dict()["signed_out"] is False
    assert item.session == "s-0987654321"
    # Signed out for real: no second run, and the user is asked to sign in.
    marker.unlink()
    monkeypatch.setattr(discover, "_signed_in", lambda agent: False)
    script_once = script.replace("first = not os.path.exists(m)", "first = True")
    monkeypatch.setattr(discover, "_command",
                        lambda agent, prompt, url, **kw: [sys.executable, "-c", script_once])
    assert run(go()).as_dict()["signed_out"] is True


def test_rdst_runs_the_signed_in_newest_copy_of_an_agent(monkeypatch, tmp_path):
    """An old copy (npm, ~/.claude/local) beside the one the user runs keeps a stale sign-in
    of its own: RDST runs the copy that's signed in, newest first."""
    old, new = tmp_path / "old" / "claude", tmp_path / "new" / "claude"
    for path in (old, new):
        path.parent.mkdir()
        path.write_text("")
        path.chmod(0o755)
    path = os.pathsep.join([str(old.parent), str(new.parent)])
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(discover, "work_folder", lambda agent: str(tmp_path))
    versions = {str(old): (2, 0, 1), str(new): (2, 1, 312)}
    monkeypatch.setattr(discover, "_version", lambda agent: versions[agent.path])
    monkeypatch.setattr(discover, "_signed_in", lambda agent: agent.path == str(new))
    picked = discover._best_install("claude", str(old), path)
    assert (picked.path, picked.signed_in) == (str(new), True)
    # Neither can say: the newest one.
    monkeypatch.setattr(discover, "_signed_in", lambda agent: None)
    assert discover._best_install("claude", str(old), path).path == str(new)
    # Only the old one is signed in: that one.
    monkeypatch.setattr(discover, "_signed_in", lambda agent: agent.path == str(old))
    assert discover._best_install("claude", str(old), path).path == str(old)


def test_windows_drives_its_own_agent_and_a_signed_in_one_comes_first(monkeypatch):
    """Windows runs the Windows agent, signed in or not (it reaches into WSL itself); the
    dialog offers one that can run first."""
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover.os, "name", "nt")
    monkeypatch.setattr(discover.sys, "platform", "win32")
    monkeypatch.setattr(discover, "_which_local", lambda name: f"C:/{name}.exe")
    monkeypatch.setattr(discover, "app_agent_path", lambda name: None)
    monkeypatch.setattr(discover, "_best_install", lambda name, first, path: discover.Agent(
        name, "local", first, env_path=path, signed_in=name == "codex"))
    wsl = discover.Agent("claude", "wsl", "/home/u/.local/bin/claude", "Ubuntu", "/usr/bin",
                         True)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [wsl])
    agents = discover.detect(refresh=True)
    assert [(agent.key, agent.signed_in) for agent in agents] == \
        [("codex:local", True), ("claude:local", False)]



def test_cancelling_stops_the_agent(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await asyncio.sleep(0.3)
        await discover.cancel(item.id)
        return item

    item = run(go())
    assert item.state == "cancelled"
    assert item.process.returncode is not None


def test_a_setup_resumes_the_search_session_with_a_shell_and_every_rdst_tool(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("set it up", encoding="utf-8")
    command = discover._command(CLAUDE, prompt, "http://127.0.0.1:9/mcp?d=r2", setup=True,
                                resume="5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    assert command[command.index("--resume") + 1] == "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    assert command[command.index("--allowedTools") + 1:] == [
        "Read", "Grep", "Glob", "Bash", f"mcp__{discover.server_name()}"]
    codex = discover._command(discover.Agent("codex", "local", "/usr/bin/codex"), prompt,
                              "http://127.0.0.1:9/mcp?d=r2", setup=True, resume="t-123456789")
    assert codex[:3] == ["/usr/bin/codex", "exec", "resume"]
    assert codex[-2:] == ["t-123456789", "set it up"]
    assert 'sandbox_mode="workspace-write"' in codex


def test_the_activity_shows_what_the_agent_says_and_runs_with_secrets_masked():
    claude = discover._activity({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Checking the shop project."},
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "PGPASSWORD=hunter2 psql postgresql://app:pw@db/shop"}},  # trufflehog:ignore
        {"type": "tool_use", "name": "ToolSearch", "input": {}},
        {"type": "tool_use", "name": "mcp__rdst__rdst_add_database", "input": {}}]}})
    assert claude == [("say", "Checking the shop project."),
                      ("tool", "$ PGPASSWORD=*** psql postgresql://app:***@db/shop"),  # trufflehog:ignore
                      ("tool", "rdst add_database")]
    codex = discover._activity({"type": "item.completed",
                                "item": {"type": "reasoning", "text": "**Looking** at Docker"}})
    assert codex == [("say", "Looking at Docker")]
    assert discover._session_of({"type": "thread.started", "thread_id": "t1"}) == "t1"
    assert discover._session_of({"type": "system", "session_id": "s1"}) == "s1"


def test_proposals_add_up_as_the_agent_sends_them():
    _fake_run()
    discover.propose("r1", [{"engine": "postgresql", "host": "a", "database": "shop"}])
    added = discover.propose("r1", [{"engine": "postgresql", "host": "a", "database": "shop"},
                                    {"engine": "mysql", "host": "b", "database": "legacy"}])
    assert [item["database"] for item in added] == ["legacy"]
    assert [item["id"] for item in discover._runs["r1"].candidates] == ["0", "1"]


def test_a_setup_follows_the_rdst_tools_its_agent_calls():
    item = discover.Run(id="s1", agent=CLAUDE, kind="setup")
    discover._runs["s1"] = item
    token = discover.current_run.set("s1")
    try:
        discover.saw("database_added", "shop")
        discover.saw("awaiting_user", "shop")
        assert (item.target, item.waiting, item.monitoring) == ("shop", True, False)
        discover.saw("monitoring_enabled", "shop")
        assert (item.waiting, item.monitoring) == (False, True)
    finally:
        discover.current_run.reset(token)
    assert [entry["text"] for entry in item.activity][-1] == "Monitoring is on for shop"


def test_a_setup_continued_in_the_app_shows_each_rdst_call_it_makes(app, monkeypatch):
    item = discover.Run(id="s9", agent=CLAUDE, kind="setup", state="failed", session="sess-9",
                        question={"question": "Restart it?", "options": ["Yes", "No"]})
    discover._runs["s9"] = item
    monkeypatch.setattr(discover, "in_app", lambda session: "claude" if session else None)

    async def refused(_app, _args):
        raise tools.ToolError("Connection refused on port 5432")

    monkeypatch.setitem(tools.TOOLS, "rdst_test_database",
                        (*tools.TOOLS["rdst_test_database"][:2], refused))
    token = discover.current_run.set(None)
    try:
        asyncio.run(tools.call_tool(app, "rdst_test_database", {"name": "shop"}))
    finally:
        discover.current_run.reset(token)
    assert item.question is None
    assert [entry["text"] for entry in item.activity][-2:] == [
        "Testing the connection to shop", "Couldn't: Connection refused on port 5432"]


def test_rdsts_own_setup_run_doesnt_take_app_lines():
    item = discover.Run(id="s8", agent=CLAUDE, kind="setup", session="sess-8")
    discover._runs["s8"] = item
    token = discover.current_run.set("s8")
    try:
        discover.app_call("shop", "Testing the connection to shop")
    finally:
        discover.current_run.reset(token)
    assert item.activity == []


def test_a_setup_starts_from_the_picked_results(app, monkeypatch):
    started = {}

    async def fake_start(agent, prompt, url, kind="search", resume=None, folder=None, **_):
        started.update(prompt=prompt, kind=kind, resume=resume)
        run_ = discover.Run(id="s9", agent=agent, kind=kind)
        discover._runs["s9"] = run_
        return run_

    monkeypatch.setattr(discover, "start", fake_start)
    search = _fake_run(candidates=discover.sanitize_candidates([
        {"engine": "postgresql", "name": "shop", "host": "localhost", "provider": "docker",
         "note": "container shop-db"}]))
    search.state, search.session = "done", "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    reply = run(_request(app, "POST", "/api/agent-connect/discover/r1/setup",
                         json={"candidates": ["0"]}))
    assert reply.status_code == 200, reply.text
    assert reply.json()["kind"] == "setup"
    assert started["kind"] == "setup" and started["resume"] == search.session
    assert "Save as shop:" in started["prompt"] and "container shop-db" in started["prompt"]
    missing = run(_request(app, "POST", "/api/agent-connect/discover/r1/setup",
                           json={"candidates": ["7"]}))
    assert missing.status_code == 404


def test_a_local_session_opens_in_its_desktop_app():
    item = discover.Run(id="x", agent=CLAUDE, session="5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    assert discover.continue_url(item, "claude") == \
        "claude://resume?session=5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    assert discover.continue_url(item, "codex") is None
    assert discover.resume_command(item) == "claude --resume 5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    codex = discover.Run(id="y", agent=discover.Agent("codex", "local", "c"), session="t-abcdef12")
    assert discover.continue_url(codex, "codex") == "codex://threads/t-abcdef12"
    in_wsl = discover.Run(id="z", agent=CODEX_WSL, session="t-abcdef12")
    assert discover.continue_url(in_wsl, "codex") is None


def test_waiting_for_monitoring_returns_once_the_user_turns_it_on(monkeypatch):
    from features.agent_connect import tools
    from features.monitor.api import routes as monitor

    monitored: set[str] = set()

    class Store:
        def monitored_targets(self):
            return monitored

    monkeypatch.setattr(monitor, "_store", lambda: Store())
    monkeypatch.setattr(tools, "_require_target", lambda args: args["name"])
    monkeypatch.setattr(tools, "WAIT_LIMIT_SECONDS", 0)
    assert run(tools.wait_for_monitoring(None, {"name": "shop"}))["monitoring"] is False
    monitored.add("shop")
    assert run(tools.wait_for_monitoring(None, {"name": "shop"})) == {"target": "shop",
                                                                      "monitoring": True}


def test_setup_statements_are_only_the_ones_rdst_lists(monkeypatch):
    from features.agent_connect import tools

    async def listed(name):
        return {"sql": ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements;",
                        "ALTER SYSTEM SET track_activity_query_size = 4096;"],
                "ready_to_monitor": False}

    monkeypatch.setattr(tools, "prerequisites", listed)
    monkeypatch.setattr(tools, "_require_target", lambda args: args["name"])
    for statement in ("DROP TABLE orders", "ALTER SYSTEM SET track_activity_query_size = 4096"):
        with pytest.raises(tools.ToolError, match="CREATE EXTENSION IF NOT EXISTS"):
            run(tools.run_setup_statement(None, {"name": "shop", "statement": statement}))


def test_a_waiting_loop_shows_each_line_once():
    item = discover.Run(id="w", agent=CLAUDE)
    for _ in range(5):
        item.log("tool", "rdst wait_for_monitoring")
        item.log("say", "Still waiting.")
    assert [entry["text"] for entry in item.activity] == ["rdst wait_for_monitoring",
                                                          "Still waiting."]


def test_shutting_down_stops_every_running_agent(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await asyncio.sleep(0.3)
        await discover.stop_all()
        return item

    item = run(go())
    assert item.state == "cancelled" and item.process.returncode is not None


def test_a_search_that_runs_long_keeps_what_it_found(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "RUN_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        discover.propose(item.id, [{"engine": "mysql", "host": "h"}])
        await item.task
        return item

    item = run(go())
    assert item.state == "done"
    assert "RDST stopped it here" in item.message


def _session(path: Path, cwd: str, mtime: float, codex: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = ({"type": "session_meta", "payload": {"cwd": cwd}} if codex
            else {"type": "user", "cwd": cwd})
    path.write_text(json.dumps({"type": "summary"}) + "\n" + json.dumps(line) + "\n",
                    encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_rdst_lists_the_projects_recent_sessions_ran_in(tmp_path, monkeypatch):
    from datetime import date

    from features.agent_connect import projects

    home = tmp_path / "me"
    work = {name: home / "work" / name for name in ("shop", "billing", "old")}
    for folder in work.values():
        (folder / ".git").mkdir(parents=True)
    (home / "work" / "data").mkdir()
    now = time.time()
    _session(home / ".claude/projects/a/1.jsonl", str(work["billing"]), now - 300)
    _session(home / ".claude/projects/a/2.jsonl", str(work["shop"]), now - 100)
    _session(home / ".claude/projects/b/1.jsonl", str(work["old"]), now - 90 * 86400)
    _session(home / ".claude/projects/c/1.jsonl", str(home), now)
    _session(home / ".claude/projects/d/1.jsonl", str(home / "gone"), now)
    _session(home / ".claude/projects/e/1.jsonl", str(home / "work" / "data"), now)
    today = date.today()
    _session(home / f".codex/sessions/{today:%Y}/{today:%m}/{today:%d}/x.jsonl",
             str(work["billing"]), now - 50, codex=True)
    monkeypatch.setattr(projects.Path, "home", lambda: home)
    monkeypatch.setattr(projects.os, "name", "posix")
    monkeypatch.setattr(projects.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    # pytest's own folders are scratch ones.
    monkeypatch.setattr(projects, "SCRATCH", re.compile(r"^/no-scratch/"))
    assert projects.recent_projects(CLAUDE) == [str(work["billing"]), str(work["shop"])]


def test_the_search_starts_from_rdsts_projects_and_the_users_hint():
    prompt = render_discover_prompt(projects=["/home/me/work/shop"], hint='the "billing"\napp')
    assert "   - /home/me/work/shop" in prompt
    assert "don't read the sessions again" in prompt
    assert "never search a whole drive" in prompt
    assert 'I told RDST where to look: "the \'billing\' app"' in prompt
    assert prompt.index("I told RDST") < prompt.index("1. RDST already read")
    assert "~/.claude/projects/<folder>" in render_discover_prompt()
    assert "Shells can't open" not in prompt
    share = render_discover_prompt(projects=["\\\\wsl.localhost\\Ubuntu\\home\\me\\shop"])
    assert "Shells can't open the \\\\wsl.localhost folders" in share


def test_scratch_folders_are_no_project():
    from features.agent_connect import projects

    assert projects.SCRATCH.match("/tmp/claude-1000/shop")
    assert projects.SCRATCH.match("/var/tmp")
    assert not projects.SCRATCH.match("/home/me/tmpwork")


def test_continuing_in_the_app_gives_it_rdsts_tools(app, monkeypatch):
    calls, opened = [], []
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_: calls.append(
        command) or subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(discover, "open_url", opened.append)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])
    _fake_run().session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    response = run(_request(app, "POST", "/api/agent-connect/discover/r1/continue",
                            json={"app": "claude"}))
    assert response.status_code == 200
    server = discover.server_name()
    assert calls[0] == ["/usr/bin/claude", "mcp", "remove", "--scope", "user", server]
    assert calls[1][:7] == ["/usr/bin/claude", "mcp", "add", "--scope", "user", "--transport",
                            "http"]
    assert calls[1][7] == server and calls[1][8].endswith("/mcp")
    assert opened == ["claude://resume?session=5f1c2d3e-aaaa-bbbb-cccc-111122223333"]


def test_codex_registers_rdst_by_url(monkeypatch):
    calls = []
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_: calls.append(
        command) or subprocess.CompletedProcess(command, 0, "", ""))
    codex = discover.Agent("codex", "local", "C:/codex.exe")
    discover.register(codex, "http://127.0.0.1:9/mcp")
    assert calls[1] == ["C:/codex.exe", "mcp", "add", discover.server_name(), "--url",
                        "http://127.0.0.1:9/mcp"]
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_:
                        subprocess.CompletedProcess(command, 1, "", "no config"))
    with pytest.raises(OSError, match="no config"):
        discover.register(codex, "http://127.0.0.1:9/mcp")


def test_the_agent_adds_a_database_behind_a_jump_host():
    from features.agent_connect import tools

    fields = tools._connection_fields({"host": "db.abc.us-east-2.rds.amazonaws.com",
                                       "database": "app", "user": "app",
                                       "ssh": {"profile": "bastion"}})
    assert fields["ssh"] == {"profile": "bastion"}
    schema = tools.TOOLS["rdst_add_database"][1]["properties"]["ssh"]
    assert set(schema["properties"]) == {"profile", "host", "user", "port", "key_path"}


def test_rdst_on_windows_opens_a_wsl_agents_file_through_the_share(monkeypatch):
    monkeypatch.setattr(discover.os, "name", "nt")
    monkeypatch.setattr(discover, "wsl_home", ("Ubuntu", "/home/me"))
    assert discover.rdst_path("/home/me/coins/.secrets/supabase.env") == \
        "\\\\wsl.localhost\\Ubuntu\\home\\me\\coins\\.secrets\\supabase.env"
    assert discover.rdst_path("C:\\work\\.env") == "C:\\work\\.env"
    monkeypatch.setattr(discover.os, "name", "posix")
    assert discover.rdst_path("/home/me/.env") == "/home/me/.env"


def test_on_a_mac_rdst_finds_agents_without_running_the_users_shell(monkeypatch, tmp_path):
    """A login shell runs the user's startup files as RDST, and macOS asks about RDST for
    any guarded folder they touch; a PATH entry in one would make every lookup ask too."""
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [])
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    claude = tmp_path / ".local" / "bin" / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    monkeypatch.setattr(discover, "app_agent_path", lambda name: None)
    ran = []
    monkeypatch.setattr(discover, "_login_shell_path", lambda: ran.append("shell"))
    agents = discover.detect(refresh=True)
    assert [a.path for a in agents] == [str(claude)]
    assert ran == []
    assert f"{tmp_path}/Downloads" not in (agents[0].env_path or "")
    path = discover._without_protected(
        f"{tmp_path}/Downloads/tools/bin:{tmp_path}/.local/bin:/Volumes/x/bin:/usr/bin")
    assert path == f"{tmp_path}/.local/bin:/usr/bin"


def test_on_a_mac_an_apps_own_agent_skips_the_users_shell(monkeypatch, tmp_path):
    """With only the Claude app, RDST runs its copy of Claude Code and never the login shell,
    whose startup files can make macOS ask about Downloads or Documents."""
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [])
    monkeypatch.setattr(discover, "_mac_path", lambda: str(tmp_path / "empty"))
    bundled = tmp_path / "Library" / "Application Support" / "Claude" / "claude-code" / \
        "2.1.309" / "5af865b9901c" / "claude.app" / "Contents" / "MacOS" / "claude"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setattr(discover, "_signed_in", lambda agent: True)
    ran = []
    monkeypatch.setattr(discover, "_login_shell_path", lambda: ran.append("shell"))
    agents = discover.detect(refresh=True)
    assert [(a.path, a.via) for a in agents] == [(str(bundled), "app")]
    assert ran == []


def test_a_test_run_hides_installs_without_touching_them(monkeypatch, tmp_path):
    """RDST_TEST_HIDE_AGENTS lets a Mac test treat a CLI or an app as missing while the user's
    own installs stay put; hiding the CLI leaves the app's copy, and hiding that too leaves
    nothing and no shell run."""
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [])
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    cli = tmp_path / ".local" / "bin" / "claude"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    bundled = tmp_path / "Library" / "Application Support" / "Claude" / "claude-code" / \
        "2.1.309" / "claude"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setattr(discover, "_signed_in", lambda agent: True)
    monkeypatch.setattr(discover, "_login_shell_path", lambda: None)
    monkeypatch.setenv("RDST_TEST_HIDE_AGENTS", "claude")
    assert [(a.path, a.via) for a in discover.detect(refresh=True)] == [(str(bundled), "app")]
    monkeypatch.setenv("RDST_TEST_HIDE_AGENTS", "claude, claude-app")
    assert discover.detect(refresh=True) == []
    assert "claude" not in discover.desktop_apps()


def test_only_the_users_own_conversation_offers_a_workload_for_a_quiet_database():
    from features.agent_connect import prompt

    pasted = prompt.render_prompt("http://127.0.0.1:8000")
    fix = prompt.render_fix_prompt("http://127.0.0.1:8000", "shop")
    for text in (pasted, fix):
        assert "rdst_top_queries" in text and "read-only SELECTs" in text
        assert "Unless it's production" in text
    # The setup RDST runs in the background only connects the database.
    setup = prompt.render_setup_prompt([("shop", {"engine": "postgresql", "host": "localhost",
                                                  "database": "shop"})])
    assert "read-only SELECTs" not in setup


def test_failed_runs_report_why_as_a_class(monkeypatch):
    from types import SimpleNamespace

    from shared import product_telemetry

    sent = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture", lambda event, props: sent.append(props))
    timed_out = discover.Run(id="f1", agent=CLAUDE, kind="setup", state="failed",
                             failure="agent_timeout")
    signed_out = discover.Run(id="f2", agent=CLAUDE, last_error="Not logged in at db.host:5432")
    signed_out.process = SimpleNamespace(returncode=1)
    crashed = discover.Run(id="f3", agent=CLAUDE, kind="setup")
    crashed.process = SimpleNamespace(returncode=2)
    for item in (timed_out, signed_out, crashed, discover.Run(id="f4", agent=CLAUDE)):
        discover._report_finished(item, None)
    assert [(p["step"], p["ok"], p["error_class"]) for p in sent] == [
        ("setup_finished", False, "agent_timeout"),
        ("search_finished", False, "agent_signed_out"),
        ("setup_finished", False, "agent_exited"),
        ("search_finished", False, "nothing_proposed")]
    assert "db.host" not in json.dumps(sent)
def test_cancelling_stops_the_agent(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await asyncio.sleep(0.3)
        await discover.cancel(item.id)
        return item

    item = run(go())
    assert item.state == "cancelled"
    assert item.process.returncode is not None


def test_a_setup_resumes_the_search_session_with_a_shell_and_every_rdst_tool(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("set it up", encoding="utf-8")
    command = discover._command(CLAUDE, prompt, "http://127.0.0.1:9/mcp?d=r2", setup=True,
                                resume="5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    assert command[command.index("--resume") + 1] == "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    assert command[command.index("--allowedTools") + 1:] == [
        "Read", "Grep", "Glob", "Bash", f"mcp__{discover.server_name()}"]
    codex = discover._command(discover.Agent("codex", "local", "/usr/bin/codex"), prompt,
                              "http://127.0.0.1:9/mcp?d=r2", setup=True, resume="t-123456789")
    assert codex[:3] == ["/usr/bin/codex", "exec", "resume"]
    assert codex[-2:] == ["t-123456789", "set it up"]
    assert 'sandbox_mode="workspace-write"' in codex


def test_the_activity_shows_what_the_agent_says_and_runs_with_secrets_masked():
    claude = discover._activity({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Checking the shop project."},
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "PGPASSWORD=hunter2 psql postgresql://app:pw@db/shop"}},  # trufflehog:ignore
        {"type": "tool_use", "name": "ToolSearch", "input": {}},
        {"type": "tool_use", "name": "mcp__rdst__rdst_add_database", "input": {}}]}})
    assert claude == [("say", "Checking the shop project."),
                      ("tool", "$ PGPASSWORD=*** psql postgresql://app:***@db/shop"),  # trufflehog:ignore
                      ("tool", "rdst add_database")]
    codex = discover._activity({"type": "item.completed",
                                "item": {"type": "reasoning", "text": "**Looking** at Docker"}})
    assert codex == [("say", "Looking at Docker")]
    assert discover._session_of({"type": "thread.started", "thread_id": "t1"}) == "t1"
    assert discover._session_of({"type": "system", "session_id": "s1"}) == "s1"


def test_proposals_add_up_as_the_agent_sends_them():
    _fake_run()
    discover.propose("r1", [{"engine": "postgresql", "host": "a", "database": "shop"}])
    added = discover.propose("r1", [{"engine": "postgresql", "host": "a", "database": "shop"},
                                    {"engine": "mysql", "host": "b", "database": "legacy"}])
    assert [item["database"] for item in added] == ["legacy"]
    assert [item["id"] for item in discover._runs["r1"].candidates] == ["0", "1"]


def test_a_setup_follows_the_rdst_tools_its_agent_calls():
    item = discover.Run(id="s1", agent=CLAUDE, kind="setup")
    discover._runs["s1"] = item
    token = discover.current_run.set("s1")
    try:
        discover.saw("database_added", "shop")
        discover.saw("awaiting_user", "shop")
        assert (item.target, item.waiting, item.monitoring) == ("shop", True, False)
        discover.saw("monitoring_enabled", "shop")
        assert (item.waiting, item.monitoring) == (False, True)
    finally:
        discover.current_run.reset(token)
    assert [entry["text"] for entry in item.activity][-1] == "Monitoring is on for shop"


def test_a_setup_continued_in_the_app_shows_each_rdst_call_it_makes(app, monkeypatch):
    item = discover.Run(id="s9", agent=CLAUDE, kind="setup", state="failed", session="sess-9",
                        question={"question": "Restart it?", "options": ["Yes", "No"]})
    discover._runs["s9"] = item
    monkeypatch.setattr(discover, "in_app", lambda session: "claude" if session else None)

    async def refused(_app, _args):
        raise tools.ToolError("Connection refused on port 5432")

    monkeypatch.setitem(tools.TOOLS, "rdst_test_database",
                        (*tools.TOOLS["rdst_test_database"][:2], refused))
    token = discover.current_run.set(None)
    try:
        asyncio.run(tools.call_tool(app, "rdst_test_database", {"name": "shop"}))
    finally:
        discover.current_run.reset(token)
    assert item.question is None
    assert [entry["text"] for entry in item.activity][-2:] == [
        "Testing the connection to shop", "Couldn't: Connection refused on port 5432"]


def test_rdsts_own_setup_run_doesnt_take_app_lines():
    item = discover.Run(id="s8", agent=CLAUDE, kind="setup", session="sess-8")
    discover._runs["s8"] = item
    token = discover.current_run.set("s8")
    try:
        discover.app_call("shop", "Testing the connection to shop")
    finally:
        discover.current_run.reset(token)
    assert item.activity == []


def test_a_setup_starts_from_the_picked_results(app, monkeypatch):
    started = {}

    async def fake_start(agent, prompt, url, kind="search", resume=None, folder=None, **_):
        started.update(prompt=prompt, kind=kind, resume=resume)
        run_ = discover.Run(id="s9", agent=agent, kind=kind)
        discover._runs["s9"] = run_
        return run_

    monkeypatch.setattr(discover, "start", fake_start)
    search = _fake_run(candidates=discover.sanitize_candidates([
        {"engine": "postgresql", "name": "shop", "host": "localhost", "provider": "docker",
         "note": "container shop-db"}]))
    search.state, search.session = "done", "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    reply = run(_request(app, "POST", "/api/agent-connect/discover/r1/setup",
                         json={"candidates": ["0"]}))
    assert reply.status_code == 200, reply.text
    assert reply.json()["kind"] == "setup"
    assert started["kind"] == "setup" and started["resume"] == search.session
    assert "Save as shop:" in started["prompt"] and "container shop-db" in started["prompt"]
    missing = run(_request(app, "POST", "/api/agent-connect/discover/r1/setup",
                           json={"candidates": ["7"]}))
    assert missing.status_code == 404


def test_a_local_session_opens_in_its_desktop_app():
    item = discover.Run(id="x", agent=CLAUDE, session="5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    assert discover.continue_url(item, "claude") == \
        "claude://resume?session=5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    assert discover.continue_url(item, "codex") is None
    assert discover.resume_command(item) == "claude --resume 5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    codex = discover.Run(id="y", agent=discover.Agent("codex", "local", "c"), session="t-abcdef12")
    assert discover.continue_url(codex, "codex") == "codex://threads/t-abcdef12"
    in_wsl = discover.Run(id="z", agent=CODEX_WSL, session="t-abcdef12")
    assert discover.continue_url(in_wsl, "codex") is None


def test_waiting_for_monitoring_returns_once_the_user_turns_it_on(monkeypatch):
    from features.agent_connect import tools
    from features.monitor.api import routes as monitor

    monitored: set[str] = set()

    class Store:
        def monitored_targets(self):
            return monitored

    monkeypatch.setattr(monitor, "_store", lambda: Store())
    monkeypatch.setattr(tools, "_require_target", lambda args: args["name"])
    monkeypatch.setattr(tools, "WAIT_LIMIT_SECONDS", 0)
    assert run(tools.wait_for_monitoring(None, {"name": "shop"}))["monitoring"] is False
    monitored.add("shop")
    assert run(tools.wait_for_monitoring(None, {"name": "shop"})) == {"target": "shop",
                                                                      "monitoring": True}


def test_setup_statements_are_only_the_ones_rdst_lists(monkeypatch):
    from features.agent_connect import tools

    async def listed(name):
        return {"sql": ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements;",
                        "ALTER SYSTEM SET track_activity_query_size = 4096;"],
                "ready_to_monitor": False}

    monkeypatch.setattr(tools, "prerequisites", listed)
    monkeypatch.setattr(tools, "_require_target", lambda args: args["name"])
    for statement in ("DROP TABLE orders", "ALTER SYSTEM SET track_activity_query_size = 4096"):
        with pytest.raises(tools.ToolError, match="CREATE EXTENSION IF NOT EXISTS"):
            run(tools.run_setup_statement(None, {"name": "shop", "statement": statement}))


def test_a_waiting_loop_shows_each_line_once():
    item = discover.Run(id="w", agent=CLAUDE)
    for _ in range(5):
        item.log("tool", "rdst wait_for_monitoring")
        item.log("say", "Still waiting.")
    assert [entry["text"] for entry in item.activity] == ["rdst wait_for_monitoring",
                                                          "Still waiting."]


def test_shutting_down_stops_every_running_agent(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        await asyncio.sleep(0.3)
        await discover.stop_all()
        return item

    item = run(go())
    assert item.state == "cancelled" and item.process.returncode is not None


def test_a_search_that_runs_long_keeps_what_it_found(monkeypatch, data_dir):
    monkeypatch.setattr(discover, "RUN_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(discover, "_command", lambda agent, prompt, url, **kw: [
        sys.executable, "-c", "import time; time.sleep(30)"])

    async def go():
        item = await discover.start(CLAUDE, "find them", "http://127.0.0.1:9/mcp")
        discover.propose(item.id, [{"engine": "mysql", "host": "h"}])
        await item.task
        return item

    item = run(go())
    assert item.state == "done"
    assert "RDST stopped it here" in item.message


def _session(path: Path, cwd: str, mtime: float, codex: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = ({"type": "session_meta", "payload": {"cwd": cwd}} if codex
            else {"type": "user", "cwd": cwd})
    path.write_text(json.dumps({"type": "summary"}) + "\n" + json.dumps(line) + "\n",
                    encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_rdst_lists_the_projects_recent_sessions_ran_in(tmp_path, monkeypatch):
    from datetime import date

    from features.agent_connect import projects

    home = tmp_path / "me"
    work = {name: home / "work" / name for name in ("shop", "billing", "old")}
    for folder in work.values():
        (folder / ".git").mkdir(parents=True)
    (home / "work" / "data").mkdir()
    now = time.time()
    _session(home / ".claude/projects/a/1.jsonl", str(work["billing"]), now - 300)
    _session(home / ".claude/projects/a/2.jsonl", str(work["shop"]), now - 100)
    _session(home / ".claude/projects/b/1.jsonl", str(work["old"]), now - 90 * 86400)
    _session(home / ".claude/projects/c/1.jsonl", str(home), now)
    _session(home / ".claude/projects/d/1.jsonl", str(home / "gone"), now)
    _session(home / ".claude/projects/e/1.jsonl", str(home / "work" / "data"), now)
    today = date.today()
    _session(home / f".codex/sessions/{today:%Y}/{today:%m}/{today:%d}/x.jsonl",
             str(work["billing"]), now - 50, codex=True)
    monkeypatch.setattr(projects.Path, "home", lambda: home)
    monkeypatch.setattr(projects.os, "name", "posix")
    monkeypatch.setattr(projects.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    # pytest's own folders are scratch ones.
    monkeypatch.setattr(projects, "SCRATCH", re.compile(r"^/no-scratch/"))
    assert projects.recent_projects(CLAUDE) == [str(work["billing"]), str(work["shop"])]


def test_the_search_starts_from_rdsts_projects_and_the_users_hint():
    prompt = render_discover_prompt(projects=["/home/me/work/shop"], hint='the "billing"\napp')
    assert "   - /home/me/work/shop" in prompt
    assert "don't read the sessions again" in prompt
    assert "never search a whole drive" in prompt
    assert 'I told RDST where to look: "the \'billing\' app"' in prompt
    assert prompt.index("I told RDST") < prompt.index("1. RDST already read")
    assert "~/.claude/projects/<folder>" in render_discover_prompt()
    assert "Shells can't open" not in prompt
    share = render_discover_prompt(projects=["\\\\wsl.localhost\\Ubuntu\\home\\me\\shop"])
    assert "Shells can't open the \\\\wsl.localhost folders" in share


def test_scratch_folders_are_no_project():
    from features.agent_connect import projects

    assert projects.SCRATCH.match("/tmp/claude-1000/shop")
    assert projects.SCRATCH.match("/var/tmp")
    assert not projects.SCRATCH.match("/home/me/tmpwork")


def test_continuing_in_the_app_gives_it_rdsts_tools(app, monkeypatch):
    calls, opened = [], []
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_: calls.append(
        command) or subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(discover, "open_url", opened.append)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])
    _fake_run().session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    response = run(_request(app, "POST", "/api/agent-connect/discover/r1/continue",
                            json={"app": "claude"}))
    assert response.status_code == 200
    server = discover.server_name()
    assert calls[0] == ["/usr/bin/claude", "mcp", "remove", "--scope", "user", server]
    assert calls[1][:7] == ["/usr/bin/claude", "mcp", "add", "--scope", "user", "--transport",
                            "http"]
    assert calls[1][7] == server and calls[1][8].endswith("/mcp")
    assert opened == ["claude://resume?session=5f1c2d3e-aaaa-bbbb-cccc-111122223333"]


def test_codex_registers_rdst_by_url(monkeypatch):
    calls = []
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_: calls.append(
        command) or subprocess.CompletedProcess(command, 0, "", ""))
    codex = discover.Agent("codex", "local", "C:/codex.exe")
    discover.register(codex, "http://127.0.0.1:9/mcp")
    assert calls[1] == ["C:/codex.exe", "mcp", "add", discover.server_name(), "--url",
                        "http://127.0.0.1:9/mcp"]
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_:
                        subprocess.CompletedProcess(command, 1, "", "no config"))
    with pytest.raises(OSError, match="no config"):
        discover.register(codex, "http://127.0.0.1:9/mcp")


def test_the_agent_adds_a_database_behind_a_jump_host():
    from features.agent_connect import tools

    fields = tools._connection_fields({"host": "db.abc.us-east-2.rds.amazonaws.com",
                                       "database": "app", "user": "app",
                                       "ssh": {"profile": "bastion"}})
    assert fields["ssh"] == {"profile": "bastion"}
    schema = tools.TOOLS["rdst_add_database"][1]["properties"]["ssh"]
    assert set(schema["properties"]) == {"profile", "host", "user", "port", "key_path"}


def test_rdst_on_windows_opens_a_wsl_agents_file_through_the_share(monkeypatch):
    monkeypatch.setattr(discover.os, "name", "nt")
    monkeypatch.setattr(discover, "wsl_home", ("Ubuntu", "/home/me"))
    assert discover.rdst_path("/home/me/coins/.secrets/supabase.env") == \
        "\\\\wsl.localhost\\Ubuntu\\home\\me\\coins\\.secrets\\supabase.env"
    assert discover.rdst_path("C:\\work\\.env") == "C:\\work\\.env"
    monkeypatch.setattr(discover.os, "name", "posix")
    assert discover.rdst_path("/home/me/.env") == "/home/me/.env"


def test_on_a_mac_rdst_finds_agents_without_running_the_users_shell(monkeypatch, tmp_path):
    """A login shell runs the user's startup files as RDST, and macOS asks about RDST for
    any guarded folder they touch; a PATH entry in one would make every lookup ask too."""
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [])
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    claude = tmp_path / ".local" / "bin" / "claude"
    claude.write_text("#!/bin/sh\n")
    claude.chmod(0o755)
    monkeypatch.setattr(discover, "app_agent_path", lambda name: None)
    ran = []
    monkeypatch.setattr(discover, "_login_shell_path", lambda: ran.append("shell"))
    agents = discover.detect(refresh=True)
    assert [a.path for a in agents] == [str(claude)]
    assert ran == []
    assert f"{tmp_path}/Downloads" not in (agents[0].env_path or "")
    path = discover._without_protected(
        f"{tmp_path}/Downloads/tools/bin:{tmp_path}/.local/bin:/Volumes/x/bin:/usr/bin")
    assert path == f"{tmp_path}/.local/bin:/usr/bin"


def test_only_the_users_own_conversation_offers_a_workload_for_a_quiet_database():
    from features.agent_connect import prompt

    pasted = prompt.render_prompt("http://127.0.0.1:8000")
    fix = prompt.render_fix_prompt("http://127.0.0.1:8000", "shop")
    for text in (pasted, fix):
        assert "rdst_top_queries" in text and "read-only SELECTs" in text
        assert "Unless it's production" in text
    # The setup RDST runs in the background only connects the database.
    setup = prompt.render_setup_prompt([("shop", {"engine": "postgresql", "host": "localhost",
                                                  "database": "shop"})])
    assert "read-only SELECTs" not in setup


def test_failed_runs_report_why_as_a_class(monkeypatch):
    from types import SimpleNamespace

    from shared import product_telemetry

    sent = []
    monkeypatch.setattr(product_telemetry, "enabled", lambda: True)
    monkeypatch.setattr(product_telemetry, "capture", lambda event, props: sent.append(props))
    timed_out = discover.Run(id="f1", agent=CLAUDE, kind="setup", state="failed",
                             failure="agent_timeout")
    signed_out = discover.Run(id="f2", agent=CLAUDE, last_error="Not logged in at db.host:5432")
    signed_out.process = SimpleNamespace(returncode=1)
    crashed = discover.Run(id="f3", agent=CLAUDE, kind="setup")
    crashed.process = SimpleNamespace(returncode=2)
    for item in (timed_out, signed_out, crashed, discover.Run(id="f4", agent=CLAUDE)):
        discover._report_finished(item, None)
    assert [(p["step"], p["ok"], p["error_class"]) for p in sent] == [
        ("setup_finished", False, "agent_timeout"),
        ("search_finished", False, "agent_signed_out"),
        ("setup_finished", False, "agent_exited"),
        ("search_finished", False, "nothing_proposed")]
    assert "db.host" not in json.dumps(sent)


def test_rdst_reads_what_a_projects_latest_session_was_about(tmp_path, monkeypatch):
    from features.agent_connect import projects

    claude = tmp_path / "claude.jsonl"
    claude.write_text("\n".join(json.dumps(line) for line in [
        {"type": "user", "isMeta": True, "message": {"content": "<local-command-caveat>"}},
        {"type": "user", "message": {"content": [{"type": "text",
                                                  "text": "<command-name>/init"}]}},
        {"type": "user", "message": {"content": "Add  a checkout\npage to the shop"}}]))
    codex = tmp_path / "codex.jsonl"
    codex.write_text("\n".join(json.dumps(line) for line in [
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "clone github.com/x/ca and build it"}}]))
    assert projects.first_request(claude) == "Add a checkout page to the shop"
    assert projects.first_request(codex) == "clone github.com/x/ca and build it"
    rdst = tmp_path / "rdst.jsonl"
    rdst.write_text(json.dumps({"type": "user", "message": {"content": "RDST: set up shop"}}))
    monkeypatch.setattr(projects, "recent_sessions", lambda per_project, days: [
        (rdst, "/w/shop", "Claude Code"), (claude, "/w/shop", "Claude Code"),
        (codex, "/Users/me/Documents/Codex/2026-09-27/ca", "Codex")])
    assert projects.recent_requests() == {
        "/w/shop": "Add a checkout page to the shop",
        "/Users/me/Documents/Codex/2026-09-27/ca": "clone github.com/x/ca and build it"}


def test_a_search_whose_agent_failed_says_so_and_shows_what_rdst_found(monkeypatch,
                                                                         data_dir):
    """The agent never went over RDST's list: the list still shows, as RDST's, and the
    dialog says the agent couldn't, instead of reading as the agent's picks."""
    events = [{"type": "result", "is_error": True, "result": "API Error: 529 overloaded"}]
    script = "import json; [print(json.dumps(e), flush=True) for e in %r]" % events
    monkeypatch.setattr(discover, "_command",
                        lambda agent, prompt, url, **kw: [sys.executable, "-c", script])

    def finish(item):
        item.candidates = discover.sanitize_candidates([{"engine": "mysql", "host": "h"}])

    async def go():
        item = await discover.start(CLAUDE, "go over these", "http://127.0.0.1:9/mcp",
                                    finish=finish)
        await item.task
        return item

    item = run(go())
    assert item.state == "failed" and len(item.candidates) == 1
    assert item.message == "Claude Code couldn't go through what RDST found: API Error: 529 " \
                           "overloaded"


def test_a_stale_agent_list_answers_at_once_and_refreshes_behind(monkeypatch):
    """Asking each agent whether it's signed in takes seconds on Windows; the add dialog gets
    the last answer straight away while a fresh one is worked out."""
    import threading

    calls = []
    release = threading.Event()

    def slow():
        calls.append(1)
        release.wait(5)
        return [CLAUDE]

    monkeypatch.setattr(discover, "_find_agents", slow)
    monkeypatch.setattr(discover, "_detected", (0.0, [discover.Agent("codex", "local", "x")]))
    monkeypatch.setattr(discover, "_refreshing", False)
    stale = discover.detect()
    assert [agent.id for agent in stale] == ["codex"]
    assert [agent.id for agent in discover.detect()] == ["codex"]
    release.set()
    for _ in range(50):
        if discover._detected and discover._detected[1] == [CLAUDE]:
            break
        time.sleep(0.05)
    assert discover.detect() == [CLAUDE] and len(calls) == 1


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    path.chmod(0o755)
    return path


def test_rdst_finds_the_agent_a_desktop_app_ships_on_windows(monkeypatch, tmp_path):
    """The Claude app keeps Claude Code for its Code tab, one folder per version, and the
    Codex app keeps codex: RDST finds the newest of each."""
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    code = roaming / "Claude" / "claude-code"
    _exe(code / "2.1.99" / "aaa111" / "claude.exe")
    newest = _exe(code / "2.1.309" / "9078b9c1d466" / "claude.exe")
    _exe(code / "2.1.308" / "c17a819afb40" / "claude.exe")
    codex = _exe(local / "OpenAI" / "Codex" / "bin" / "a96ca4c1eee0cf35" / "codex.exe")
    _exe(local / "OpenAI" / "Codex" / "bin" / "90ee5f622914d088" / "rg.exe")
    monkeypatch.setattr(discover, "os", SimpleNamespace(
        name="nt", environ={"APPDATA": str(roaming), "LOCALAPPDATA": str(local)}))
    assert discover.app_agent_path("claude") == str(newest)
    assert discover.app_agent_path("codex") == str(codex)
    monkeypatch.setattr(discover, "os", SimpleNamespace(
        name="nt", environ={"APPDATA": str(tmp_path / "a"), "LOCALAPPDATA": str(tmp_path / "b")}))
    assert discover.app_agent_path("claude") is None
    assert discover.app_agent_path("codex") is None


def test_rdst_finds_the_agent_a_desktop_app_ships_on_a_mac(monkeypatch, tmp_path):
    """The Claude app keeps each version's Claude Code in its own claude.app, under a folder
    per build."""
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    code = tmp_path / "Library" / "Application Support" / "Claude" / "claude-code"
    bundle = Path("claude.app") / "Contents" / "MacOS" / "claude"
    _exe(code / "2.1.308" / "a93ded4b1b47" / bundle)
    newest = _exe(code / "2.1.309" / "5af865b9901c" / bundle)
    _exe(tmp_path / "Applications" / "Codex.app" / "Contents" / "Resources" / "codex")
    assert discover.app_agent_path("claude") == str(newest)
    assert discover.app_agent_path("codex").endswith("Codex.app/Contents/Resources/codex")


def test_with_no_cli_rdst_searches_with_the_copy_the_app_ships(monkeypatch):
    """No claude on PATH, but the Claude app is installed: its Claude Code is the agent the
    dialog offers. A WSL agent stays on offer only while the app's copy isn't signed in."""
    monkeypatch.setattr(discover, "_detected", None)
    monkeypatch.setattr(discover.sys, "platform", "linux")
    monkeypatch.setattr(discover, "_which_local", lambda name: None)
    monkeypatch.setattr(discover, "_login_shell_path", lambda: None)
    bundled = "C:/Users/me/AppData/Roaming/Claude/claude-code/2.1.309/x/claude.exe"
    monkeypatch.setattr(discover, "app_agent_path",
                        lambda name: bundled if name == "claude" else None)
    wsl = discover.Agent("claude", "wsl", "/home/u/.local/bin/claude", "Ubuntu", "/usr/bin",
                         True)
    monkeypatch.setattr(discover, "_detect_wsl", lambda: [wsl])
    signed = {"value": True}
    monkeypatch.setattr(discover, "_signed_in", lambda agent: signed["value"])
    agents = discover.detect(refresh=True)
    assert [(a.key, a.path, a.via, a.signed_in) for a in agents] == [
        ("claude:local", bundled, "app", True)]
    assert agents[0].as_dict() == {"key": "claude:local", "id": "claude", "side": "local",
                                   "label": "Claude Code", "name": "Claude",
                                   "signed_in": True, "via": "app"}
    # The app's copy needs its own sign-in: the signed-in WSL agent comes first.
    signed["value"] = False
    agents = discover.detect(refresh=True)
    assert [(a.key, a.via, a.signed_in) for a in agents] == [
        ("claude:wsl", "cli", True), ("claude:local", "app", False)]
    # A CLI of the user's own wins over the app's copy.
    monkeypatch.setattr(discover, "_which_local",
                        lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(discover, "_best_install", lambda name, first, path: discover.Agent(
        name, "local", first, env_path=path, signed_in=True))
    agents = discover.detect(refresh=True)
    assert [(a.path, a.via) for a in agents] == [("/usr/bin/claude", "cli")]


def test_the_apps_copy_runs_headless_like_the_cli(tmp_path):
    """The same headless command, stream-json events and MCP config as a CLI search; only
    the program differs. Picking it up later names the app's copy, which isn't on PATH."""
    bundled = discover.Agent("claude", "local", "/apps/claude-code/2.1.309/claude", via="app")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("find them", encoding="utf-8")
    command = discover._command(bundled, prompt, "http://127.0.0.1:9/mcp?d=r1")
    assert command[0] == bundled.path
    assert command[1:] == discover._command(CLAUDE, prompt, "http://127.0.0.1:9/mcp?d=r1")[1:]
    run = discover.Run(id="r1", agent=bundled, session="0123456789abcdef")
    assert discover.resume_command(run) == \
        "/apps/claude-code/2.1.309/claude --resume 0123456789abcdef"
    assert discover.continue_url(run, "claude") == "claude://resume?session=0123456789abcdef"

