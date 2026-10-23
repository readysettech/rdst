"""RDST's own scan for databases: what each method reads, how findings combine, and how a
scanned database goes to an agent to set up."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import threading

import pytest

from features.agent_connect import discover, scan, traces
from tests.unit.test_agent_connect import _request, app, data_dir, run  # noqa: F401

CLAUDE_WSL = discover.Agent("claude", "wsl", "/home/me/.local/bin/claude", "Ubuntu",
                            "/home/me/.local/bin:/usr/bin")


@pytest.fixture(autouse=True)
def _no_runs(monkeypatch):
    monkeypatch.setattr(discover, "_runs", {})
    monkeypatch.setattr(discover, "_handoffs", None)


def test_a_dotenv_names_urls_variable_groups_and_supabase_without_passwords():
    values = scan.parse_dotenv(
        "# local\n"
        "export DATABASE_URL='postgresql+psycopg://app:hunter2@localhost:25440/shop'\n"  # trufflehog:ignore
        "REPLICA_URL=mysql://ro@10.0.0.4/orders # read only\n"
        "DB_CONNECTION=mysql\nDB_HOST=127.0.0.1\nDB_PORT=3306\nDB_DATABASE=blog\n"
        "DB_USERNAME=root\nDB_PASSWORD=secret\n"
        "SUPABASE_URL=https://abcdefghijabcdefghij.supabase.co\n"
        "SUPABASE_DB_PASSWORD=s3cret\n"
        "TEMPLATE_URL=postgresql://${USER}@localhost/x\n")
    found = scan.from_dotenv("/w/shop/.env.production", values, "/w/shop")
    assert "hunter2" not in json.dumps(found) and "secret" not in json.dumps(found)
    url, replica, group, supabase = found
    assert (url["engine"], url["host"], url["port"], url["database"], url["user"]) == \
        ("postgresql", "localhost", 25440, "shop", "app")
    assert (url["env_file"], url["env_var"]) == ("/w/shop/.env.production", "DATABASE_URL")
    assert url["environment"] == "production"
    assert "env_file" not in replica
    assert (group["engine"], group["port"], group["database"], group["user"]) == \
        ("mysql", 3306, "blog", "root")
    assert (group["password_file"], group["password_var"]) == \
        ("/w/shop/.env.production", "DB_PASSWORD")
    assert supabase["host"] == "db.abcdefghijabcdefghij.supabase.co"
    assert supabase["provider"] == "supabase"
    assert supabase["password_var"] == "SUPABASE_DB_PASSWORD"


def test_compose_publishes_a_database_with_its_names():
    text = """
services:
  db:
    image: postgres:16
    ports: ["${PG_PORT:-25440}:5432"]
    environment:
      POSTGRES_DB: shop
      POSTGRES_USER: app
      POSTGRES_PASSWORD: hunter2
  cache:
    image: redis:7
    ports: ["6379:6379"]
  mysql:
    image: mysql:8
    ports: [{target: 3306, published: 13306}]
    environment: [MYSQL_DATABASE=orders]
  hidden:
    image: postgres:16
"""
    found = scan.from_compose("/w/shop/docker-compose.yml", text, "/w/shop")
    assert [(f["engine"], f["port"], f["database"], f["user"]) for f in found] == [
        ("postgresql", 25440, "shop", "app"), ("mysql", 13306, "orders", "root")]
    assert "hunter2" not in json.dumps(found)


def test_docker_ps_finds_database_containers_and_their_projects():
    rows = [
        {"ID": "a1", "Image": "postgres:16", "Names": "shop-db",
         "Ports": "0.0.0.0:25440->5432/tcp, :::25440->5432/tcp",
         "Labels": "com.docker.compose.project.working_dir=/w/shop,x=y"},
        {"ID": "b2", "Image": "postgres:16", "Names": "traffic", "Ports": "5432/tcp",
         "Labels": ""},
        {"ID": "c3", "Image": "nginx", "Names": "web", "Ports": "0.0.0.0:80->80/tcp"},
    ]
    found = scan.from_docker_ps("\n".join(json.dumps(r) for r in rows), "here")
    assert len(found) == 1
    assert (found[0]["port"], found[0]["project"], found[0]["note"]) == \
        (25440, "/w/shop", "Docker container shop-db")


def _server(answer):
    """A one-connection server on a free port that answers like ``answer``."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            answer(conn)
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1]


def test_a_probe_tells_postgres_from_mysql_without_logging_in():
    def postgres(conn):
        assert conn.recv(8) == b"\x00\x00\x00\x08\x04\xd2\x16\x2f"
        conn.sendall(b"N")

    def mysql(conn):
        conn.sendall(b"\x4a\x00\x00\x00\x0a8.0.36\x00" + b"\x00" * 20)

    def other(conn):
        conn.sendall(b"SSH-2.0-OpenSSH\r\n")

    assert scan.probe(_server(postgres)) == "postgresql"
    assert scan.probe(_server(mysql)) == "mysql"
    assert scan.probe(_server(other)) is None


def test_saved_connections_and_pgpass():
    dbeaver = json.dumps({"connections": {
        "pg-1": {"provider": "postgresql", "name": "Billing prod", "configuration": {
            "host": "billing.abc.us-east-2.rds.amazonaws.com", "port": "5432",
            "database": "billing", "type": "prod"}},
        "lite": {"provider": "sqlite", "configuration": {"database": "x.db"}}}})
    found = scan.from_dbeaver(dbeaver)
    assert [(f["provider"], f["environment"], f["database"]) for f in found] == [
        ("aws", "production", "billing")]
    pgpass = scan.from_pgpass("# comment\nlocalhost:5432:shop:app:pw\n*:*:*:me:pw\n")
    assert [(f["host"], f["database"], f["user"]) for f in pgpass] == \
        [("localhost", "shop", "app")]
    assert "pw" not in json.dumps(pgpass)


def test_findings_for_one_database_combine_and_a_bare_port_joins_them():
    merged = scan.merge([
        {"host": "localhost", "port": 25440, "database": "shop", "provider": "local",
         "env_file": "/w/.env", "env_var": "DATABASE_URL", "found_by": "DATABASE_URL in .env"},
        {"host": "127.0.0.1", "port": 25440, "provider": "docker", "project": "/w",
         "found_by": "Docker container shop-db"},
        {"host": "localhost", "port": 25440, "provider": "local",
         "found_by": "PostgreSQL answering on port 25440"},
        {"host": "localhost", "port": 25440, "database": "blog", "provider": "local",
         "found_by": "BLOG_URL in .env"},
    ])
    assert len(merged) == 2
    shop = merged[0]
    assert shop["found_by"] == ["DATABASE_URL in .env", "Docker container shop-db",
                                "PostgreSQL answering on port 25440"]
    assert (shop["provider"], shop["project"], shop["env_var"]) == \
        ("docker", "/w", "DATABASE_URL")


def test_a_project_is_read_two_levels_deep_skipping_examples_and_dependencies(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://a@localhost/a\n")
    (tmp_path / ".env.example").write_text("DATABASE_URL=postgresql://x@localhost/x\n")
    (tmp_path / "apps" / "api").mkdir(parents=True)
    (tmp_path / "apps" / "api" / ".env.local").write_text("X=1\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / ".env").write_text("X=1\n")
    (tmp_path / "docker-compose.bench.yml").write_text("services: {}\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "database.yml").write_text("development: {}\n")
    found = sorted(path[len(str(tmp_path)):] for path in scan._project_files(str(tmp_path)))
    assert [p.replace("\\", "/") for p in found] == [
        "/.env", "/apps/api/.env.local", "/config/database.yml"]


def test_the_scan_reports_each_method_and_survives_one_failing(monkeypatch):
    def broken(report):
        raise RuntimeError("docker hung")

    monkeypatch.setattr(scan, "METHODS", (
        ("projects", "Your recent projects", lambda report: report("/w/shop") or [
            {"engine": "postgresql", "host": "localhost", "port": 5432, "database": "shop",
             "provider": "local", "found_by": "DATABASE_URL in .env"}]),
        ("docker", "Docker containers", broken)))
    monkeypatch.setattr(scan, "knock", lambda host, port: "answering")
    result = scan.scan()
    assert [(s["method"], s["state"], s["found"], s["looked"]) for s in result["steps"]] == [
        ("projects", "done", 1, ["/w/shop"]), ("docker", "failed", 0, []),
        ("reach", "done", 1, [])]
    assert len(result["candidates"]) == 1


def test_the_scan_route_keeps_its_findings_for_add_and_setup(app, monkeypatch):
    monkeypatch.setattr(scan, "scan", lambda on_step=None: {"seconds": 0.1, "steps": [
        {"method": "projects", "label": "Your recent projects", "state": "done", "found": 1,
         "looked": ["/w/shop"], "seconds": 0.1}], "candidates": [
        {"name": "shop", "engine": "postgresql", "host": "localhost", "port": 25440,
         "database": "shop", "provider": "docker", "project": "/w/shop",
         "found_by": ["DATABASE_URL in .env"]}]})
    monkeypatch.setattr(discover, "detect", lambda refresh=False: [])

    async def scan_and_wait():
        started = (await _request(app, "POST", "/api/agent-connect/scan")).json()
        assert started["state"] == "running"
        assert [m["method"] for m in started["methods"]] == [m[0] for m in scan.METHODS]
        await discover.get_run(started["id"]).task
        return (await _request(app, "GET", f"/api/agent-connect/discover/{started['id']}")
                ).json()

    body = run(scan_and_wait())
    assert body["kind"] == "scan" and body["agent"] is None and body["state"] == "done"
    assert body["candidates"][0]["found_by"] == ["DATABASE_URL in .env"]
    assert body["methods"][0]["looked"] == ["/w/shop"]
    assert discover.get_run(body["id"]).candidates[0]["port"] == 25440


def test_a_scanned_database_gets_a_fresh_conversation_in_the_agents_own_paths(app,
                                                                              monkeypatch):
    started = {}

    async def fake_start(agent, prompt, mcp_url, kind="search", resume=None, folder=None,
                         **_):
        started.update(agent=agent, prompt=prompt, kind=kind, resume=resume, folder=folder)
        item = discover.Run(id="s1", agent=agent, kind=kind)
        discover.keep(item)
        return item

    monkeypatch.setattr(discover, "start", fake_start)
    monkeypatch.setattr(discover, "find_agent", lambda key: CLAUDE_WSL)
    scanned = discover.Run(id="scan-1", agent=None, kind="scan", state="done",
                           candidates=discover.sanitize_candidates([{
                               "name": "shop", "engine": "postgresql", "host": "localhost",
                               "port": 25440, "database": "shop", "provider": "docker",
                               "project": "\\\\wsl.localhost\\Ubuntu\\home\\me\\shop",
                               "env_file": "\\\\wsl.localhost\\Ubuntu\\home\\me\\shop\\.env",
                               "env_var": "DATABASE_URL"}]))
    discover.keep(scanned)
    app.state.agent_connect = {"url": "http://127.0.0.1:9", "wsl_url": "http://172.1.1.1:9"}
    response = run(_request(app, "POST", "/api/agent-connect/discover/scan-1/setup",
                            json={"candidates": ["0"], "agent": "claude:wsl"}))
    assert response.status_code == 200, response.text
    assert started["kind"] == "setup" and started["resume"] is None
    # The agent works in the project the database came from.
    assert started["folder"] == "/home/me/shop"
    assert "project /home/me/shop" in started["prompt"]
    assert "env_file /home/me/shop/.env" in started["prompt"]


def test_help_later_picks_up_the_conversation_that_set_the_database_up(data_dir):
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    item = discover.Run(id="s2", agent=claude, kind="setup", session="sess-1")
    discover.keep(item)
    token = discover.current_run.set("s2")
    try:
        discover.saw("database_added", "shop")
    finally:
        discover.current_run.reset(token)
    assert discover.session_for("shop", claude) == "sess-1"
    assert discover.session_for("shop", CLAUDE_WSL) is None
    assert discover.session_for("other", claude) is None


def test_the_claude_app_gets_a_new_conversation_with_the_prompt_typed_in(app, monkeypatch):
    opened = []
    monkeypatch.setattr(discover, "open_url", opened.append)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])
    attempt = run(_request(app, "POST", "/api/agent-connect/prompt")).json()["attempt"]
    response = run(_request(app, "POST", "/api/agent-connect/open-app",
                            json={"app": "claude", "attempt": attempt}))
    assert response.status_code == 200, response.text
    assert opened[0].startswith("claude://code/new?q=RDST%20is%20a%20database")
    assert f"a%3D{attempt}" in opened[0]
    missing = run(_request(app, "POST", "/api/agent-connect/open-app",
                           json={"app": "codex", "attempt": attempt}))
    assert missing.status_code == 404
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude", "codex"])
    run(_request(app, "POST", "/api/agent-connect/open-app",
                 json={"app": "codex", "attempt": attempt}))
    assert opened[-1].startswith("codex://threads/new?prompt=RDST%20is%20a%20database")
    stale = run(_request(app, "POST", "/api/agent-connect/open-app",
                         json={"app": "claude", "attempt": "nope"}))
    assert stale.status_code == 404


def test_running_databases_rdst_can_add_come_first():
    ranked = scan.rank([
        {"name": "compose-only", "database": "x", "found_by": ["service db in compose.yml"]},
        {"name": "bare-port", "found_by": ["PostgreSQL answering on port 5433"]},
        {"name": "shop", "database": "shop", "env_file": "/w/.env",
         "found_by": ["DATABASE_URL in .env", "Docker container shop-db (postgres:16)"]},
    ])
    assert [item["name"] for item in ranked] == ["shop", "bare-port", "compose-only"]


def test_rdst_lists_projects_without_an_agent_from_either_kind_of_path(tmp_path,
                                                                       monkeypatch):
    from features.agent_connect import projects

    home = tmp_path / "me"
    (home / "work" / "shop" / ".git").mkdir(parents=True)
    now = time.time()
    for name, cwd in (("a", "C:\\Users\\me\\shop"), ("b", str(home / "work" / "shop"))):
        session = home / ".claude" / "projects" / name / "1.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text(json.dumps({"cwd": cwd}) + "\n", encoding="utf-8")
        os.utime(session, (now, now))
    monkeypatch.setattr(projects, "_homes", lambda: [(home, None)])
    monkeypatch.setattr(projects, "SCRATCH", re.compile(r"^/no-scratch/"))
    monkeypatch.setattr(projects.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    assert projects.recent_projects(None) == [str(home / "work" / "shop")]


def test_rdst_on_windows_asks_wsl_for_its_projects_and_files(monkeypatch):
    from features.agent_connect import projects

    monkeypatch.setattr(discover, "wsl_home", ("Ubuntu", "/home/me"))
    monkeypatch.setattr(discover, "wsl_script_command",
                        lambda script, name, distro=None: ["wsl.exe", name])
    out = ("RDST_PROJECT\t1793046488\t/home/me/projects/shop\n"
           "noise from a profile\nRDST_PROJECT\tbad\t/x\n")
    monkeypatch.setattr(subprocess, "run", lambda command, **_: subprocess.CompletedProcess(
        command, 0, out.encode(), b""))
    assert projects._wsl_projects(60, 12) == [(1793046488.0, "/home/me/projects/shop")]

    share = "\\\\wsl.localhost\\Ubuntu"
    folder = share + "\\home\\me\\projects\\shop"
    asked = {}

    def fake_python(request, name, timeout):
        asked.update(request)
        return [{"folder": "/home/me/projects/shop",
                 "structured": ["/home/me/projects/shop/.env"],
                 "files": [{"path": "/home/me/projects/shop/src/db.ts", "rel": "src/db.ts",
                            "kind": "source", "traces": []}],
                 "uses": None, "engines": [], "managers": [], "infra": []}]

    monkeypatch.setattr(scan, "_wsl_python", fake_python)
    reads = scan._wsl_read_projects([folder])
    assert asked == {"mode": "projects", "folders": ["/home/me/projects/shop"]}
    assert reads[folder]["structured"] == [share + "\\home\\me\\projects\\shop\\.env"]
    assert reads[folder]["files"][0]["path"] == \
        share + "\\home\\me\\projects\\shop\\src\\db.ts"


def test_a_cli_only_user_continues_the_conversation_in_a_terminal(app, monkeypatch):
    opened = []
    monkeypatch.setattr(discover.subprocess, "Popen", lambda command, **kw: opened.append(
        (command, kw)))
    monkeypatch.setattr(discover.sys, "platform", "linux")
    monkeypatch.setattr(discover.shutil, "which", lambda name: "/usr/bin/x-terminal-emulator"
                        if name == "x-terminal-emulator" else None)
    item = discover.Run(id="t1", agent=discover.Agent("claude", "local", "/usr/bin/claude"),
                        kind="setup", session="5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    discover.keep(item)
    response = run(_request(app, "POST", "/api/agent-connect/discover/t1/terminal"))
    assert response.status_code == 200, response.text
    assert opened[0][0] == ["/usr/bin/x-terminal-emulator", "--", "/usr/bin/claude",
                            "--resume", "5f1c2d3e-aaaa-bbbb-cccc-111122223333"]
    item.session = None
    assert run(_request(app, "POST", "/api/agent-connect/discover/t1/terminal")
               ).status_code == 409


def test_an_agent_on_windows_with_wsl_is_told_how_to_reach_it(monkeypatch, tmp_path):
    monkeypatch.setattr(discover, "wsl_home", ("Ubuntu", "/home/me"))
    monkeypatch.setattr(discover.os, "name", "nt")
    windows_codex = discover.Agent("codex", "local", "C:/codex.exe")
    assert discover._reaches_wsl(windows_codex)
    assert not discover._reaches_wsl(discover.Agent("codex", "wsl", "/x", "Ubuntu"))
    monkeypatch.setattr(discover.os, "name", "posix")
    assert not discover._reaches_wsl(windows_codex)
    note = discover.WSL_NOTE.format(distro="Ubuntu", home="/home/me")
    assert 'wsl.exe -d Ubuntu -e sh -c "grep -rIl' in note


def test_codex_drops_its_sandbox_only_to_reach_wsl(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("set it up", encoding="utf-8")
    codex = discover.Agent("codex", "local", "C:/codex.exe")
    plain = discover._command(codex, prompt, "http://127.0.0.1:9/mcp", setup=True)
    assert 'sandbox_mode="workspace-write"' in plain
    monkeypatch.setattr(discover, "_reaches_wsl", lambda agent: True)
    reaching = discover._command(codex, prompt, "http://127.0.0.1:9/mcp", setup=True)
    assert 'sandbox_mode="danger-full-access"' in reaching
    assert 'sandbox_mode="workspace-write"' not in reaching


def test_the_users_answer_continues_the_same_conversation(app, monkeypatch):
    started = {}

    async def fake_start(agent, prompt, mcp_url, kind="search", resume=None, folder=None,
                         **_):
        started.update(prompt=prompt, kind=kind, resume=resume)
        item = discover.Run(id="r2", agent=agent, kind=kind, session=resume)
        discover.keep(item)
        return item

    monkeypatch.setattr(discover, "start", fake_start)
    app.state.agent_connect = {"url": "http://127.0.0.1:9"}
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    asked = discover.Run(id="r1", agent=claude, kind="setup", state="done",
                         session="5f1c2d3e-aaaa-bbbb-cccc-111122223333", target="shop")
    discover.keep(asked)
    body = run(_request(app, "POST", "/api/agent-connect/discover/r1/reply",
                        json={"text": "  The jump host is bastion-prod in ~/.ssh/config  "})).json()
    assert started == {"prompt": "The jump host is bastion-prod in ~/.ssh/config",
                       "kind": "setup", "resume": "5f1c2d3e-aaaa-bbbb-cccc-111122223333"}
    assert body["target"] == "shop"
    assert body["activity"][0] == {"kind": "you",
                                   "text": "The jump host is bastion-prod in ~/.ssh/config"}
    empty = run(_request(app, "POST", "/api/agent-connect/discover/r1/reply", json={"text": " "}))
    assert empty.status_code == 400
    asked.session = None
    assert run(_request(app, "POST", "/api/agent-connect/discover/r1/reply",
                        json={"text": "hi"})).status_code == 409


def _ask(app, run_id, question, options):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "rdst_ask_user", "arguments": {"question": question, "options": options}}}
    return run(_request(app, "POST", f"/mcp?d={run_id}", json=body,
                        headers={"Accept": "application/json, text/event-stream"})
               ).json()["result"]


def test_a_setup_asks_the_user_with_choices_and_finishes_waiting_for_one(app):
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    setup = discover.Run(id="q1", agent=claude, kind="setup")
    discover.keep(setup)
    result = _ask(app, "q1", "How should RDST reach orders-db?",
                  ["Through bastion-prod", "Through my VPN", ""])
    assert result["isError"] is False
    assert setup.question == {"question": "How should RDST reach orders-db?",
                              "options": ["Through bastion-prod", "Through my VPN"]}
    discover._report_finished(setup, None)
    assert setup.state == "done" and setup.target is None

    assert _ask(app, "q1", "Which?", ["only one"])["isError"] is True
    search = discover.Run(id="q2", agent=claude, kind="search")
    discover.keep(search)
    refused = _ask(app, "q2", "Which?", ["a", "b"])
    assert refused["isError"] is True and "setup" in refused["content"][0]["text"]


def test_found_databases_rdst_already_has_say_so(monkeypatch):
    from shared.config import targets

    class Saved:
        def load(self):
            pass

        def list_targets(self):
            return ["shop"]

        def get(self, name):
            return {"host": "127.0.0.1", "port": 25440, "database": "shop"}

    monkeypatch.setattr(targets, "TargetsConfig", Saved)
    items = [{"host": "localhost", "port": 25440, "database": "shop"},
             {"host": "localhost", "port": 25440, "database": "blog"},
             {"host": "localhost", "port": 25440, "database": None},
             {"host": "localhost", "port": 5432, "database": "shop"}]
    discover.mark_saved(items)
    assert [item["saved_as"] for item in items] == ["shop", None, "shop", None]


def test_the_agent_asks_before_a_restart_and_never_touches_production():
    from features.agent_connect.prompt import render_setup_prompt

    prompt = render_setup_prompt([("shop", {"engine": "postgresql", "host": "localhost"})])
    assert "Never change a production database" in prompt
    assert "ask me first with rdst_ask_user" in prompt
    assert "Yes, apply and restart" in prompt
    assert "don't ask which one" in prompt


def test_a_database_in_rdst_gets_its_own_fix_prompt_to_copy_or_stage(app, monkeypatch):
    from features.agent_connect import tools

    monkeypatch.setattr(tools, "_configured", lambda name: {"engine": "postgresql"}
                        if name == "shopdev" else None)
    body = run(_request(app, "POST", "/api/agent-connect/fix-prompt",
                        json={"target": "shopdev"})).json()
    assert "The database shopdev is already saved in RDST" in body["prompt"]
    assert "rdst_check_prerequisites" in body["prompt"]
    assert "Never change a production database" in body["prompt"]
    assert f"a={body['attempt']}" in body["prompt"]
    missing = run(_request(app, "POST", "/api/agent-connect/fix-prompt",
                           json={"target": "nope"}))
    assert missing.status_code == 404
    opened = []
    monkeypatch.setattr(discover, "open_url", opened.append)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])
    run(_request(app, "POST", "/api/agent-connect/open-app",
                 json={"app": "claude", "attempt": body["attempt"], "target": "shopdev"}))
    assert "shopdev%20is%20already%20saved%20in%20RDST" in opened[0]


def test_analysis_sees_the_schemas_an_app_names_without_qualifying():
    from features.analyze.functions import explain_analysis

    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *args):
            executed.append(sql)

        def fetchone(self):
            return ('"$user", public', ["auth", "public", "storage"])

    class Conn:
        committed = False

        def cursor(self):
            return Cursor()

        def commit(self):
            Conn.committed = True

        def rollback(self):
            pass

    explain_analysis._widen_search_path(Conn())
    assert executed[-1].endswith('SET search_path TO "$user", public, auth, storage')
    assert Conn.committed


def test_monitoring_stores_its_captured_queries_without_the_truncation_guess(monkeypatch):
    from features.analyze.functions import workflow_integration

    seen = {}

    class Registry:
        def add_query(self, **kwargs):
            seen.update(kwargs)
            return "h1", True

    monkeypatch.setattr(workflow_integration, "QueryRegistry", Registry)
    monkeypatch.setattr(workflow_integration, "_persist_analysis_record", lambda *a: "a1")
    result = workflow_integration.store_analysis_results(query="select 1", target="t",
                                                         source="monitor")
    assert result["success"] and seen["known_complete"] is True
    workflow_integration.store_analysis_results(query="select 1", target="t", source="analyze")
    assert seen["known_complete"] is False


def test_ask_about_a_database_connects_the_agent_and_resumes_its_conversation(app, monkeypatch,
                                                                             data_dir):
    from features.agent_connect import tools

    monkeypatch.setattr(tools, "_configured", lambda name: {"engine": "postgresql"}
                        if name == "shop" else None)
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    monkeypatch.setattr(discover, "find_agent", lambda key: claude
                        if key == "claude:local" else None)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["claude"])
    registered, opened = [], []
    monkeypatch.setattr(discover, "register", lambda agent, url: registered.append(url))
    monkeypatch.setattr(discover, "open_url", opened.append)

    body = run(_request(app, "POST", "/api/agent-connect/ask-prompt",
                        json={"target": "shop"})).json()
    assert "I want to ask you about shop using what RDST found" in body["prompt"]
    assert "Don't change the database unless I ask you to" in body["prompt"]

    connected = run(_request(app, "POST", "/api/agent-connect/connect",
                             json={"agent": "claude:local"}))
    assert connected.status_code == 200 and registered[-1].endswith("/mcp")

    fresh = run(_request(app, "POST", "/api/agent-connect/ask-open",
                         json={"app": "claude", "target": "shop"})).json()
    assert fresh == {"opened": True, "resumed": False}
    assert opened[-1].startswith("claude://code/new?q=RDST%20is%20a%20database")

    discover.remember_session("shop", claude, "5f1c2d3e-aaaa-bbbb-cccc-111122223333")
    again = run(_request(app, "POST", "/api/agent-connect/ask-open",
                         json={"app": "claude", "target": "shop"})).json()
    assert again == {"opened": True, "resumed": True}
    assert opened[-1] == "claude://resume?session=5f1c2d3e-aaaa-bbbb-cccc-111122223333"


def test_a_conversation_opened_in_the_app_carries_on_there(app, monkeypatch, data_dir):
    session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    monkeypatch.setattr(discover.subprocess, "run", lambda command, **_:
                        subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(discover, "open_url", lambda url: None)
    monkeypatch.setattr(discover, "desktop_apps_cached", lambda: ["codex"])
    app.state.agent_connect = {"url": "http://127.0.0.1:9"}
    codex = discover.Agent("codex", "local", "/usr/bin/codex")
    asked = discover.Run(id="r1", agent=codex, kind="setup", state="done", session=session,
                         question={"question": "Restart it?", "options": ["Yes", "No"]})
    discover.keep(asked)
    discover.remember_session("shop", codex, session)
    assert run(_request(app, "POST", "/api/agent-connect/discover/r1/continue",
                        json={"app": "codex"})).status_code == 200
    assert discover.in_app(session) == "codex"
    assert asked.as_dict()["in_app"] == "codex"

    reply = run(_request(app, "POST", "/api/agent-connect/discover/r1/reply",
                         json={"text": "Yes"}))
    assert reply.status_code == 409
    assert "open in the Codex app" in reply.json()["detail"]
    # RDST's own help starts a fresh conversation; the app still resumes this one.
    assert discover.session_for("shop", codex) is None
    assert discover.session_for("shop", codex, in_background=False) == session
    monkeypatch.setattr(discover, "_handoffs", None)
    assert discover.in_app(session) == "codex"


def test_an_agent_runs_in_the_project_and_resumes_there(tmp_path, data_dir, monkeypatch):
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    project = tmp_path / "shop"
    project.mkdir()
    assert discover.run_folder(claude, str(project)) == str(project)
    assert discover.run_folder(claude, str(tmp_path / "gone")) is None
    assert discover.run_folder(claude, "\\\\wsl.localhost\\Ubuntu\\home\\me") is None
    assert discover.run_folder(CLAUDE_WSL, "/home/me/shop") == "/home/me/shop"

    item = discover.Run(id="s3", agent=claude, kind="setup", session="sess-9",
                        folder=str(project))
    discover.keep(item)
    token = discover.current_run.set("s3")
    try:
        discover.saw("database_added", "shop")
    finally:
        discover.current_run.reset(token)
    assert discover.folder_for("shop", claude) == str(project)
    assert discover.resume_command(item).startswith("cd ")
    assert discover.resume_command(item).endswith("claude --resume sess-9")

    command = discover._command(CLAUDE_WSL, tmp_path / "prompt.txt", "http://x/mcp",
                                setup=True, folder="/home/me/shop")
    assert "{ cd /home/me/shop 2>/dev/null || cd ~; }" in command[-1]


def test_rdst_follows_a_setup_the_user_continued_in_the_app(data_dir, monkeypatch):
    codex = discover.Agent("codex", "local", "/usr/bin/codex")
    item = discover.Run(id="s4", agent=codex, kind="setup", state="done", session="th-12345678",
                        question={"question": "Restart it?", "options": ["Yes", "No"]})
    discover.keep(item)
    discover.hand_off("th-12345678", "codex")
    # A tool call from the app's session carries no run id.
    discover.saw("database_added", "shop")
    assert item.target == "shop" and item.question is None
    discover.saw("monitoring_enabled", "shop")
    assert item.monitoring
    # A tool call naming another database doesn't land on it.
    discover.saw("database_added", "other")
    assert item.target == "shop"


def test_a_mac_terminal_opens_from_a_command_file_in_the_project(tmp_path, data_dir,
                                                                monkeypatch):
    opened = []
    monkeypatch.setattr(discover.os, "name", "posix")
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    monkeypatch.setattr(discover.subprocess, "run",
                        lambda command, **_: opened.append(command))
    project = tmp_path / "shop"
    project.mkdir()
    claude = discover.Agent("claude", "local", "/usr/local/bin/claude", env_path="/opt/bin")
    item = discover.Run(id="r9", agent=claude, kind="setup", session="sess-12345678",
                        folder=str(project))
    discover.open_terminal(item)
    script = data_dir / "terminal" / "r9.command"
    # Terminal opens the file itself: no Apple Events, so no automation prompt.
    assert opened == [["open", "-a", "Terminal", str(script)]]
    text = script.read_text()
    assert f"cd {project} || cd ~" in text
    assert "PATH=/opt/bin; export PATH" in text
    assert text.rstrip().endswith("exec /usr/local/bin/claude --resume sess-12345678")
    assert script.stat().st_mode & 0o777 == 0o700


def test_traces_find_databases_in_any_language_without_placeholders():
    from features.agent_connect import traces

    text = """
    url = "jdbc:postgresql://orders.abc.us-east-1.rds.amazonaws.com:5432/orders"  # trufflehog:ignore
    conn = "Host=pg.internal.example;Database=x"
    dotnet = "Server=sales.postgres.database.azure.com;Database=sales;Username=app;Password=pw;"  # trufflehog:ignore
    DATABASE_URL=postgresql+asyncpg://app:s3cret@db.abcdefghijklmnopqrst.supabase.co:6543/app  # trufflehog:ignore
    docs: postgresql://user:password@host:5432/database and db.x.supabase.co  # trufflehog:ignore
    psql -h ep-cool-1.us-east-2.aws.neon.tech -U me -d shop
    see https://zyxwvutsrqponmlkjihg.supabase.co for the API
    INSTANCE_CONNECTION_NAME=my-project:us-central1:orders-db
    """
    found = {(t["host"] or t.get("instance")): t for t in traces.find_all(text)}
    assert found["orders.abc.us-east-1.rds.amazonaws.com"]["database"] == "orders"
    assert found["sales.postgres.database.azure.com"]["has_password"] is True
    supabase = found["db.abcdefghijklmnopqrst.supabase.co"]
    assert (supabase["port"], supabase["has_password"], supabase["via"]) == (6543, True, "url")
    assert found["ep-cool-1.us-east-2.aws.neon.tech"]["database"] == "shop"
    assert "db.zyxwvutsrqponmlkjihg.supabase.co" in found
    assert found["my-project:us-central1:orders-db"]["provider"] == "gcp"
    assert not {"host", "db.x.supabase.co", "pg.internal.example"} & set(found)
    assert traces.provider_of("x.rlwy.net") == "railway"
    assert traces.provider_of("dpg-abc.oregon-postgres.render.com") == "render"


def test_a_project_using_a_database_it_doesnt_name_still_gets_an_entry(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"pg": "8.11.0"}}')
    (tmp_path / ".infisical.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "db.ts").write_text(
        "const url = `postgres://${user}:${pass}@${host}/${db}`\n")  # trufflehog:ignore
    items = scan._project_items(str(tmp_path), traces_read(tmp_path))
    assert len(items) == 1
    entry = items[0]
    assert (entry["incomplete"], entry["engine"], entry["secrets"]) == \
        (True, "postgresql", "Infisical")
    assert entry["found_by"] == "A database library in package.json, secrets in Infisical"


def traces_read(folder):
    from features.agent_connect import traces

    return traces.read_project(str(folder))


def test_scripts_and_history_count_only_when_something_backs_them_up():
    merged = scan.merge([
        {"host": "localhost", "port": 5432, "database": "bench", "provider": "local",
         "project": "/w", "weak": True, "method": "projects",
         "found_by": "A connection URL in scripts/bench.sh"},
        {"host": "localhost", "port": 5999, "database": "old", "provider": "local",
         "project": "/w", "weak": True, "method": "projects",
         "found_by": "A connection URL in scripts/old.sh"},
        {"host": "localhost", "port": 5432, "provider": "local", "method": "ports",
         "found_by": "PostgreSQL answering on port 5432"},
        {"host": "ep-x.neon.tech", "database": "app", "provider": "neon", "history": True,
         "method": "history", "found_by": "In your shell history"},
        {"host": "10.0.0.5", "provider": "other", "history": True, "method": "history",
         "found_by": "Used in a Codex session"},
    ])
    assert [(m["host"], m.get("port")) for m in merged] == [("localhost", 5432),
                                                             ("ep-x.neon.tech", None)]
    assert merged[0]["found_by"] == ["PostgreSQL answering on port 5432",
                                     "A connection URL in scripts/bench.sh"]
    assert merged[1]["history"] is True


def test_a_mac_project_in_documents_waits_until_the_user_picks_it(monkeypatch):
    from features.agent_connect import projects

    monkeypatch.setattr(projects.sys, "platform", "darwin")
    home = str(projects.Path.home())
    assert projects.protected(f"{home}/Documents/shop") == "Documents"
    assert projects.protected(f"{home}/Library/Mobile Documents/app") == "iCloud Drive"
    assert projects.protected(f"{home}/code/shop") is None
    monkeypatch.setattr(projects, "recent_projects",
                        lambda agent: [f"{home}/Documents/shop", f"{home}/code/api"])
    read = []
    monkeypatch.setattr(scan.traces, "read_project", lambda folder: read.append(folder) or {
        "folder": folder, "structured": [], "files": [], "uses": None, "engines": [],
        "managers": [], "infra": []})
    items = scan.scan_projects(lambda where: None)
    assert read == [f"{home}/code/api"]
    assert items == [{"name": "shop", "engine": None, "host": None, "provider": "other",
                      "project": f"{home}/Documents/shop", "environment": "unknown",
                      "deferred": True, "found_by": "A recent project in Documents"}]


def test_a_found_database_with_its_password_on_disk_connects_without_an_agent(app,
                                                                              monkeypatch,
                                                                              tmp_path):
    from features.agent_connect import tools

    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://app:pw@db.abcdefghijklmnopqrst.supabase.co:6543/app\n")  # trufflehog:ignore
    scanned = discover.Run(id="scan-1", agent=None, kind="scan", state="done",
                           candidates=discover.sanitize_candidates([{
                               "name": "shop", "engine": "postgresql",
                               "host": "db.abcdefghijklmnopqrst.supabase.co", "port": 6543,
                               "database": "app", "provider": "supabase",
                               "env_file": str(env), "env_var": "DATABASE_URL"}, {
                               "name": "api", "engine": "postgresql", "host": "localhost",
                               "port": 5432, "provider": "local"}]))
    discover.keep(scanned)
    added = {}

    async def fake_add(app, args, announce=True):
        added.update(args)
        return {"target": "shop", "added": True}

    async def fake_test(app, args, via="mcp", announce=True):
        return {"target": "shop", "ok": False, "message": "no pg_hba.conf entry",
                "category": "network", "ready_to_monitor": False}

    monkeypatch.setattr(tools, "add_database", fake_add)
    monkeypatch.setattr(tools, "test_database", fake_test)
    from features.agent_connect.api import routes
    forgotten = []

    async def forget(name):
        forgotten.append(name)

    monkeypatch.setattr(routes, "_forget_target", forget)
    body = run(_request(app, "POST", "/api/agent-connect/discover/scan-1/add",
                        json={"candidate": "0"})).json()
    assert added == {"name": "shop", "env_file": str(env), "env_var": "DATABASE_URL",
                     "port": 5432, "tags": ["provider:supabase",
                                            "supabase-ref:abcdefghijklmnopqrst"]}
    # It didn't connect, so RDST doesn't keep it: the list offers the fixes.
    assert body == {"target": None, "added": False, "already_saved": False, "ok": False,
                    "message": "no pg_hba.conf entry", "category": "network"}
    assert forgotten == ["shop"]
    missing = run(_request(app, "POST", "/api/agent-connect/discover/scan-1/add",
                           json={"candidate": "1"}))
    assert missing.status_code == 409

    # Already saved and still failing: left as the user had it, and nothing recorded.
    async def already(app, args, announce=True):
        return {"target": "shop", "added": False, "already_saved": True}

    monkeypatch.setattr(tools, "add_database", already)
    remembered = []
    monkeypatch.setattr(discover, "remember_session", lambda *a, **k: remembered.append(a))
    body = run(_request(app, "POST", "/api/agent-connect/discover/scan-1/add",
                        json={"candidate": "0"})).json()
    assert body["ok"] is False and body["already_saved"] is True
    assert forgotten == ["shop"] and remembered == []
    assert discover.get_run("scan-1").candidates[0].get("saved_as") is None


def test_a_password_in_a_session_log_is_read_only_when_the_user_connects(tmp_path):
    log = tmp_path / "s.jsonl"
    log.write_text('{"text":"psql postgresql://me:s3cret@ep-a.neon.tech/app\\n"}\n')  # trufflehog:ignore
    args = scan.connection_args({"url_file": str(log), "url_host": "ep-a.neon.tech",
                                 "provider": "neon", "host": "ep-a.neon.tech"})
    assert args == {"url": "postgresql://me:s3cret@ep-a.neon.tech/app",  # trufflehog:ignore
                    "tags": ["provider:neon"]}


def test_picking_a_project_the_scan_only_listed_reads_it_then(app, monkeypatch):
    scanned = discover.Run(id="scan-2", agent=None, kind="scan", state="done",
                           candidates=discover.sanitize_candidates([{
                               "name": "shop", "engine": None, "project": "/Users/me/Documents/shop",
                               "deferred": True, "found_by": ["A recent project in Documents"]}]))
    discover.keep(scanned)
    monkeypatch.setattr(scan, "scan_folder", lambda folder: [{
        "name": "shop", "engine": "postgresql", "host": "localhost", "port": 5432,
        "database": "shop", "provider": "local", "project": folder,
        "env_file": f"{folder}/.env", "env_var": "DATABASE_URL",
        "found_by": "DATABASE_URL in .env"}])
    body = run(_request(app, "POST", "/api/agent-connect/discover/scan-2/folder",
                        json={"project": "/Users/me/Documents/shop"})).json()
    assert [(c["host"], c["deferred"]) for c in body["candidates"]] == [("localhost", None)]
    assert run(_request(app, "POST", "/api/agent-connect/discover/scan-2/folder",
                        json={"project": "/etc"})).status_code == 404


def test_the_user_can_describe_a_database_for_the_agent_to_find(app, monkeypatch):
    started = {}

    async def fake_start(agent, prompt, mcp_url, kind="search", resume=None, folder=None,
                         **_):
        started.update(prompt=prompt, kind=kind)
        item = discover.Run(id="d1", agent=agent, kind=kind)
        discover.keep(item)
        return item

    monkeypatch.setattr(discover, "start", fake_start)
    monkeypatch.setattr(discover, "find_agent", lambda key: CLAUDE_WSL)
    app.state.agent_connect = {"url": "http://127.0.0.1:9", "wsl_url": "http://172.1.1.1:9"}
    body = run(_request(app, "POST", "/api/agent-connect/describe",
                        json={"agent": "claude:wsl",
                              "text": 'the "analytics" replica behind our bastion'})).json()
    assert started["kind"] == "setup"
    assert "I described it as: \"the 'analytics' replica behind our bastion\"" in \
        started["prompt"]
    assert body["activity"][-1] == {"kind": "you",
                                    "text": 'the "analytics" replica behind our bastion'}
    assert run(_request(app, "POST", "/api/agent-connect/describe",
                        json={"agent": "claude:wsl", "text": " "})).status_code == 400


def test_claude_turns_what_rdst_found_into_a_short_list_that_answers(app, monkeypatch):
    scanned = discover.Run(id="scan-3", agent=None, kind="scan", state="done",
                           candidates=discover.sanitize_candidates([{
                               "name": "shop", "engine": "postgresql", "host": "localhost",
                               "port": 5432, "database": "shop", "provider": "local",
                               "project": "/w/shop", "env_file": "/w/shop/.env",
                               "env_var": "DATABASE_URL", "reach": "answering",
                               "found_by": ["DATABASE_URL in .env"]}, {
                               "name": "bench", "engine": "postgresql", "host": "localhost",
                               "port": 5999, "provider": "local", "reach": "answering",
                               "found_by": ["PostgreSQL answering on port 5999"]}]))
    discover.keep(scanned)
    started = {}

    async def fake_start(agent, prompt, mcp_url, kind="search", resume=None, folder=None,
                         title=None, model=None, finish=None):
        started.update(prompt=prompt, title=title, model=model)
        item = discover.Run(id="m1", agent=agent, kind=kind, candidates=[
            {"name": "shop", "engine": "postgresql", "host": "localhost", "port": 5432,
             "database": "shop", "note": "shop's local database, from its .env", "found": 0},
            {"name": "old", "engine": "postgresql", "host": "localhost", "port": 6111,
             "note": "an old copy"}], skipped={1: "a benchmark database"})
        finish(item)
        discover.keep(item)
        return item

    monkeypatch.setattr(discover, "start", fake_start)
    monkeypatch.setattr(discover, "find_agent",
                        lambda key: discover.Agent("claude", "local", "/usr/bin/claude"))
    monkeypatch.setattr(scan, "knock", lambda host, port: "refused")
    from features.agent_connect import projects
    monkeypatch.setattr(projects, "recent_projects", lambda agent: [])
    monkeypatch.setattr(projects, "recent_requests", lambda: {"/w/shop": "add a checkout page"})
    app.state.agent_connect = {"url": "http://127.0.0.1:9"}
    body = run(_request(app, "POST", "/api/agent-connect/discover",
                        json={"agent": "claude:local", "scan": "scan-3"})).json()
    assert started["title"] == "RDST: find your database" and started["model"] == "sonnet"
    assert started["prompt"].startswith("RDST: find your database")
    assert ('0. shop; postgresql; localhost:5432/shop; answers; /w/shop; DATABASE_URL in .env; '
            'my latest session there: "add a checkout page"') in started["prompt"]
    assert "1. bench; postgresql; localhost:5999; answers" in started["prompt"]
    picked = discover.get_run(body["id"]).candidates
    # Telemetry gets how Claude went over the list, as counts only.
    assert discover.get_run(body["id"]).counts == {"proposed": 2, "skipped": 1,
                                                  "unmentioned": 0}
    # Filled in from the scan, the dead one and the one Claude left out with a reason gone.
    assert [(c["name"], c["env_var"], c["reach"]) for c in picked] == \
        [("shop", "DATABASE_URL", "answering")]
    assert picked[0]["note"] == "shop's local database, from its .env"


def test_a_database_seen_in_history_is_named_after_itself():
    assert scan._trace_name({"database": "orders", "host": "x.neon.tech"}) == "orders"
    assert scan._trace_name({"database": "postgres",
                             "host": "db.abcdefghijklmnopqrst.supabase.co"}) == \
        "abcdefghijklmnopqrst"
    assert scan._trace_name({"database": None,
                             "host": "private-shop-do-user-1-0.b.db.ondigitalocean.com"}) == \
        "shop"


def test_a_conversation_continued_in_a_terminal_carries_on_there(app, monkeypatch):
    session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    opened = []
    monkeypatch.setattr(discover, "open_terminal", opened.append)
    codex = discover.Agent("codex", "local", "/usr/bin/codex")
    asked = discover.Run(id="t1", agent=codex, kind="setup", state="done", session=session,
                         question={"question": "Restart it?", "options": ["Yes", "No"]})
    discover.keep(asked)
    assert run(_request(app, "POST", "/api/agent-connect/discover/t1/terminal")).json() == \
        {"opened": True}
    assert asked.as_dict()["in_app"] == "terminal"
    reply = run(_request(app, "POST", "/api/agent-connect/discover/t1/reply",
                         json={"text": "Yes"}))
    assert reply.status_code == 409
    assert "open in a terminal now" in reply.json()["detail"]


def test_on_a_mac_docker_is_skipped_when_its_socket_is_in_another_apps_data(monkeypatch,
                                                                          tmp_path):
    monkeypatch.setattr(scan.sys, "platform", "darwin")
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(scan.Path, "home", lambda: tmp_path)
    run_dir = tmp_path / ".docker" / "run"
    run_dir.mkdir(parents=True)
    container = tmp_path / "Library" / "Containers" / "com.docker.docker" / "Data"
    container.mkdir(parents=True)
    (container / "docker.sock").write_text("")
    (run_dir / "docker.sock").symlink_to(container / "docker.sock")
    assert scan._docker_reachable_quietly() is False
    (run_dir / "docker.sock").unlink()
    (run_dir / "docker.sock").write_text("")
    assert scan._docker_reachable_quietly() is True


def test_agents_start_in_rdsts_own_folder_never_the_home_folder(data_dir):
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    work = discover.work_folder(claude)
    assert work.startswith(str(data_dir)) and os.path.isdir(work)
    assert discover.run_folder(claude, None) is None


def test_a_conversation_moves_into_the_picked_project(monkeypatch, tmp_path):
    monkeypatch.setattr(discover.Path, "home", lambda: tmp_path)
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    source = tmp_path / ".claude" / "projects" / "-rdst-agent-work"
    source.mkdir(parents=True)
    (source / f"{session}.jsonl").write_text("{}\n")
    assert discover.move_session(claude, session, "/rdst/agent-work", "/w/shop.app")
    assert (tmp_path / ".claude" / "projects" / "-w-shop-app" / f"{session}.jsonl").is_file()
    assert not discover.move_session(claude, "nope", "/rdst/agent-work", "/w/shop")
    codex = discover.Agent("codex", "local", "/usr/bin/codex")
    assert discover.move_session(codex, session, "/a", "/b")


def test_rdst_reads_a_password_file_and_wont_save_without_a_password(tmp_path):
    from features.agent_connect import tools

    secrets = tmp_path / "auctori_supabase.env"
    secrets.write_text("SUPABASE_DB_PASSWORD=s3cret\n")
    fields = tools._connection_fields({
        "host": "db.x.example.com", "database": "postgres", "user": "postgres",
        "fields": {"password_file": str(secrets), "password_var": "SUPABASE_DB_PASSWORD"}})
    assert fields["password"] == "s3cret" and "password_file" not in fields
    with pytest.raises(tools.ToolError, match="needs this database's password"):
        run(tools.add_database(None, {"name": "shop", "host": "db.x.example.com",
                                      "database": "postgres", "user": "postgres"}))


def test_rdsts_own_connect_posts_nothing_to_the_agent_progress_feed(app, monkeypatch, tmp_path):
    """The dialog's pasted-prompt panel follows that feed and opens the database it hears
    about; a Connect that failed must leave the user on the list."""
    from features.agent_connect import session, tools

    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://rdst:old@localhost:5432/imdb\n")  # trufflehog:ignore
    scanned = discover.Run(id="scan-9", agent=None, kind="scan", state="done",
                           candidates=discover.sanitize_candidates([{
                               "name": "readyset", "engine": "postgresql",
                               "host": "localhost", "port": 5432, "database": "imdb",
                               "provider": "docker", "env_file": str(env),
                               "env_var": "DATABASE_URL"}]))
    discover.keep(scanned)
    posted = []
    monkeypatch.setattr(session.events, "publish", lambda step, **kw: posted.append(step))
    seen = {}

    async def fake_add(app, args, announce=True):
        seen["add"] = announce
        return {"target": "readyset-postgres", "added": False, "already_saved": True}

    async def fake_test(app, args, via="mcp", announce=True):
        seen["test"] = announce
        return {"target": "readyset-postgres", "ok": False,
                "message": "password authentication failed"}

    monkeypatch.setattr(tools, "add_database", fake_add)
    monkeypatch.setattr(tools, "test_database", fake_test)
    body = run(_request(app, "POST", "/api/agent-connect/discover/scan-9/add",
                        json={"candidate": "0"})).json()
    assert body["ok"] is False
    assert seen == {"add": False, "test": False} and posted == []


def test_checking_a_saved_database_says_why_it_cant_connect_and_posts_nothing(app,
                                                                            monkeypatch):
    from features.agent_connect import tools

    monkeypatch.setattr(tools, "_configured", lambda name: {"host": "localhost"}
                        if name == "readyset-postgres" else None)
    seen = {}

    async def fake_test(app, args, via="mcp", announce=True):
        seen.update(announce=announce)
        return {"ok": False, "message": "password authentication failed"}

    monkeypatch.setattr(tools, "test_database", fake_test)
    body = run(_request(app, "POST", "/api/agent-connect/check-target",
                        json={"target": "readyset-postgres"})).json()
    assert body == {"target": "readyset-postgres", "ok": False,
                    "message": "password authentication failed", "category": None}
    assert seen == {"announce": False}
    assert run(_request(app, "POST", "/api/agent-connect/check-target",
                        json={"target": "nope"})).status_code == 404


def test_a_variable_that_isnt_a_database_url_is_never_used_to_connect(tmp_path):
    """Claude pointed RDST at SUPABASE_URL, the project's https address."""
    env = tmp_path / "auctori_supabase.env"
    env.write_text("SUPABASE_URL=https://abcdefghijklmnopqrst.supabase.co\n"
                   "SUPABASE_DB_PASSWORD=s3cret\n")
    found = [{"name": "auctori", "engine": "postgresql",
              "host": "db.abcdefghijklmnopqrst.supabase.co", "port": 5432,
              "database": "postgres", "user": "postgres", "provider": "supabase",
              "password_file": str(env), "password_var": "SUPABASE_DB_PASSWORD",
              "reach": "answering", "found_by": "SUPABASE_URL in auctori_supabase.env"}]
    proposed = [{**found[0], "env_file": str(env), "env_var": "SUPABASE_URL",
                 "password_file": None, "password_var": None}]
    settled = scan.settle(proposed, found)
    assert settled[0].get("env_var") is None
    assert settled[0]["password_var"] == "SUPABASE_DB_PASSWORD"
    args = scan.connection_args({**found[0], "env_file": str(env), "env_var": "SUPABASE_URL"})
    assert args["password"] == "s3cret" and "env_var" not in args


def test_agents_rdst_starts_dont_update_themselves_and_find_a_tilde_folder(tmp_path,
                                                                         monkeypatch):
    assert discover.AGENT_ENV["DISABLE_AUTOUPDATER"] == "1"
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Projects" / "shop").mkdir(parents=True)
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    assert discover.run_folder(claude, "~/Projects/shop") == str(tmp_path / "Projects" / "shop")
    wsl = discover.Agent("claude", "wsl", "/home/me/.local/bin/claude", "Ubuntu")
    command = discover._command(wsl, tmp_path / "prompt.txt", "http://x/mcp")
    assert "DISABLE_AUTOUPDATER=1; export DISABLE_AUTOUPDATER;" in command[-1]


def test_a_database_that_doesnt_connect_is_never_saved_and_supabase_finds_its_pooler(
        app, monkeypatch, tmp_path):
    from features.agent_connect import resolve, tools
    from shared.config.targets import TargetsConfig

    env = tmp_path / "auctori_supabase.env"
    env.write_text("SUPABASE_DB_PASSWORD=s3cret\nSUPABASE_ACCESS_TOKEN=sbp_x\n")
    tried = []

    async def connects_only_through_the_pooler(name, fields):
        tried.append(fields["host"])
        if fields["host"].endswith("pooler.supabase.com"):
            return True, None, None
        return False, "No route to host", "network"

    monkeypatch.setattr(tools, "_try_connection", connects_only_through_the_pooler)
    from features.providers import supabase
    monkeypatch.setattr(supabase, "_pooler_config", lambda token, ref: {
        "db_host": "aws-0-us-east-1.pooler.supabase.com"} if token == "sbp_x" else None)
    added = run(tools.add_database(app, {
        "name": "auctori", "host": "db.abcdefghijklmnopqrst.supabase.co", "port": 5432,
        "database": "postgres", "user": "postgres", "password_file": str(env),
        "password_var": "SUPABASE_DB_PASSWORD"}, announce=False))
    assert added["added"] is True
    assert tried == ["db.abcdefghijklmnopqrst.supabase.co",
                     "aws-0-us-east-1.pooler.supabase.com"]
    config = TargetsConfig()
    config.load()
    saved = config.get("auctori")
    assert (saved["host"], saved["port"], saved["user"]) == \
        ("aws-0-us-east-1.pooler.supabase.com", 5432, "postgres.abcdefghijklmnopqrst")
    assert "supabase-ref:abcdefghijklmnopqrst" in saved.get("tags", [])

    async def never(name, fields):
        return False, "password authentication failed.", "auth"

    monkeypatch.setattr(tools, "_try_connection", never)
    with pytest.raises(tools.ConnectionFailed, match="saved nothing: password authentication "
                       "failed. Fix"):
        run(tools.add_database(app, {"name": "shop", "url": "postgresql://a:b@localhost/shop"},  # trufflehog:ignore
                               announce=False))
    config.load()
    assert config.get("shop") is None
    assert resolve.supabase_ref("db.abcdefghijklmnopqrst.supabase.co") == \
        "abcdefghijklmnopqrst"


def test_a_conversation_in_the_app_is_where_a_database_gets_fixed(app, data_dir):
    claude = discover.Agent("claude", "local", "/usr/bin/claude")
    session = "5f1c2d3e-aaaa-bbbb-cccc-111122223333"
    discover.remember_session("shop", claude, session)
    assert run(_request(app, "GET", "/api/agent-connect/conversation/shop")).json() == \
        {"agent": "claude:local", "in_app": None}
    discover.hand_off(session, "claude")
    assert run(_request(app, "GET", "/api/agent-connect/conversation/shop")).json() == \
        {"agent": "claude:local", "in_app": "claude"}
    assert run(_request(app, "GET", "/api/agent-connect/conversation/none")).json() == \
        {"agent": None, "in_app": None}


def test_a_project_in_a_guarded_mac_folder_always_stays_in_claudes_short_list():
    waiting = {"name": "shop", "engine": None, "host": None, "provider": "other",
               "project": "/Users/me/Documents/shop", "deferred": True,
               "found_by": ["A recent project in Documents"]}
    running = {"name": "api", "engine": "postgresql", "host": "localhost", "port": 5432,
               "provider": "local", "reach": "answering", "found_by": ["port 5432"]}
    settled = scan.settle([{"name": "api", "engine": "postgresql", "host": "localhost",
                            "port": 5432}], [running, waiting])
    assert [item["name"] for item in settled] == ["api", "shop"]
    assert settled[1]["deferred"] is True
    assert [item["name"] for item in scan.settle([], [running, waiting])] == ["api", "shop"]


def test_every_line_rdst_found_shows_unless_the_agent_left_it_out_with_a_reason():
    """Which of the scan's databases show never depends on what the agent happened to
    mention: a line it neither proposed nor skipped still shows, after its picks."""
    secret = {"name": "billing", "engine": "postgresql", "host": None, "provider": "other",
              "project": "/w/billing", "incomplete": True, "secrets": "Infisical",
              "found_by": ["A database library in package.json, secrets in Infisical"]}
    running = {"name": "api", "engine": "postgresql", "host": "localhost", "port": 5432,
               "provider": "local", "reach": "answering", "found_by": ["port 5432"]}
    clone = {"name": "ca", "project": "/Users/me/Documents/Codex/2026-09-27/ca",
             "deferred": True, "found_by": ["A recent project in Documents"]}
    found = [running, secret, clone]
    # The agent named the billing project by its line, so it isn't listed twice.
    proposed = [{"name": "billing db", "engine": "postgresql", "host": None,
                 "project": "/w/billing", "found": 1}]
    assert [item["name"] for item in scan.settle(proposed, found)] == ["billing db", "api", "ca"]
    assert [item["name"] for item in scan.settle(proposed, found, {2: "a repo I only built"})] \
        == ["billing db", "api"]
    # The agent didn't run: everything RDST found.
    assert [item["name"] for item in scan.settle([], found)] == ["api", "billing", "ca"]


def test_an_agent_rdst_starts_doesnt_inherit_where_rdst_was_started(monkeypatch):
    monkeypatch.setenv("PWD", "/Users/me/Downloads")
    monkeypatch.setenv("OLDPWD", "/Users/me/Documents")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDE_CODE_CHILD_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/sock")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep-me")  # trufflehog:ignore
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    claude = discover.Agent("claude", "local", "/usr/local/bin/claude")
    env = discover.agent_env(claude, "/Users/me/Library/Application Support/rdst/agent-work")
    assert env["PWD"] == "/Users/me/Library/Application Support/rdst/agent-work"
    for gone in ("OLDPWD", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                 "CLAUDE_CODE_MESSAGING_SOCKET"):
        assert gone not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me"
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_the_mac_search_has_no_rules_naming_folders_macos_guards(monkeypatch, tmp_path):
    # Claude Code opens each folder a permission rule names when it starts, and on a Mac
    # that asks the user about Desktop, Documents and Downloads before the search does anything.
    monkeypatch.setattr(discover.sys, "platform", "darwin")
    claude = discover.Agent("claude", "local", "/usr/local/bin/claude")
    (tmp_path / "prompt.txt").write_text("find")
    search = discover._command(claude, tmp_path / "prompt.txt", "http://x/mcp")
    assert "--disallowedTools" not in search
    assert not any("Downloads" in part or "Documents" in part for part in search)


def test_the_search_prompt_never_names_a_folder_macos_guards(monkeypatch):
    from features.agent_connect import prompt

    monkeypatch.setattr(discover.sys, "platform", "darwin")
    downloads = str(discover.Path.home() / "Downloads" / "shop")
    found = [{"name": "shop-db", "engine": "postgresql", "host": "localhost", "port": 15433,
              "reach": "answering", "project": downloads,
              "found_by": [f"docker-compose.yml in {downloads}", "Docker container shop-db"]},
             {"name": "shop", "project": downloads, "deferred": True}]
    text = prompt.render_triage_prompt(found)
    assert "Downloads/shop" not in text and downloads not in text
    assert "a project in Downloads (shop), not read yet" in text
    assert "Docker container shop-db" in text
    assert "don't open it or look for it" in text


def test_a_password_a_history_command_carries_is_used_to_connect(tmp_path):
    """PGPASSWORD=... psql -h ... and mysql -p...: the password is on disk, so Connect uses
    it instead of asking."""
    from urllib.parse import unquote, urlsplit

    lines = {
        "pg": "PGPASSWORD='p@ss w0rd' psql -h ep-cool-1.us-east-2.aws.neon.tech -U owner "
              "-d neondb",
        "my": "mysql -h shopdb.c9x1.us-east-1.rds.amazonaws.com -P 3307 -u app -pS3cret shop",
        "none": "psql -h ep-cool-2.us-east-2.aws.neon.tech -U owner -d neondb",
    }
    for name, line in lines.items():
        path = tmp_path / f"{name}_history"
        path.write_text(f": 1761600000:0;ls\n: 1761600001:0;{line}\n")
        traced = traces.find_all(traces.snippets(traces.tail(str(path), 1 << 20)))
        (item,) = scan._history_items(traced, None, "shell", str(path))
        args = scan.connection_args(item)
        if name == "none":
            assert args is None and not item.get("url_file")
            continue
        url = urlsplit(args["url"])
        assert (url.username, unquote(url.password)) == \
            (("owner", "p@ss w0rd") if name == "pg" else ("app", "S3cret"))
        assert url.path == ("/neondb" if name == "pg" else "/shop")
