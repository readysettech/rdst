from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "keyservice" / ".buildkite" / "cleanup_ci_envs.py"

# The public mirror publishes tests/unit without the keyservice or its CI scripts.
if not SCRIPT.is_file():
    pytest.skip("keyservice CI scripts are not part of this checkout", allow_module_level=True)

spec = importlib.util.spec_from_file_location("cleanup_ci_envs", SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cleanup
spec.loader.exec_module(cleanup)

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
MONTH = timedelta(days=30)


def at(days_ago: int, fraction: bool = True) -> str:
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.123456Z" if fraction else f"{stamp}Z"


def worker(name: str, days_ago: int) -> dict:
    return {"id": name, "created_on": at(400), "modified_on": at(days_ago)}


def database(name: str, days_ago: int) -> dict:
    return {"name": name, "uuid": f"uuid-{name}", "created_at": at(days_ago, fraction=False)}


def domain(env: str) -> dict:
    return {"id": f"id-{env}", "hostname": f"keyservice-{env}.readyset.io", "service": f"rdst-keyservice-{env}"}


def plan(workers=(), domains=(), databases=(), bound=None):
    """bound maps a Worker to the database names it binds; absent Workers bind nothing known."""
    bound = bound or {}

    def bound_databases(worker):
        names = bound.get(worker)
        return None if names is None else {f"uuid-{name}" for name in names}

    return cleanup.plan_cleanup(
        list(workers), list(domains), list(databases), NOW, MONTH, bound_databases
    )


def test_nothing_stale_plans_nothing():
    result = plan(
        [worker("rdst-keyservice-cl100", 3), worker("rdst-keyservice-pr-100-7", 29)],
        [domain("cl100"), domain("pr-100-7")],
        [database("rdst-keyservice-db-cl100", 90), database("rdst-keyservice-db-pr-100-7", 29)],
    )
    assert not result


def test_a_stale_env_goes_whole():
    result = plan(
        [worker("rdst-keyservice-cl100", 31), worker("rdst-keyservice-cl200", 1)],
        [domain("cl100"), domain("cl200")],
        [
            database("rdst-keyservice-db-cl100", 200),
            database("rdst-keyservice-db-cl100-b12", 5),
            database("rdst-keyservice-db-cl200", 200),
        ],
    )
    assert result.workers == ["rdst-keyservice-cl100"]
    assert [d["hostname"] for d in result.domains] == ["keyservice-cl100.readyset.io"]
    assert [d["name"] for d in result.databases] == [
        "rdst-keyservice-db-cl100",
        "rdst-keyservice-db-cl100-b12",
    ]


def test_a_live_env_keeps_the_database_its_worker_binds():
    databases = [
        database("rdst-keyservice-db-cl100", 90),
        database("rdst-keyservice-db-cl100-b12", 60),
        database("rdst-keyservice-db-cl100-b51", 2),
    ]
    # A failed rebuild leaves the Worker on its old database and a newer unused one.
    result = plan(
        [worker("rdst-keyservice-cl100", 1)],
        [domain("cl100")],
        databases,
        bound={"rdst-keyservice-cl100": ["rdst-keyservice-db-cl100"]},
    )
    assert not result.workers and not result.domains
    assert [d["name"] for d in result.databases] == ["rdst-keyservice-db-cl100-b12"]
    # Without its bindings, a live env keeps every database.
    unknown = plan([worker("rdst-keyservice-cl100", 1)], [], databases)
    assert not unknown


def test_a_live_env_needs_no_lookup_without_an_old_database():
    def unexpected(worker):
        raise AssertionError(f"looked up {worker}")

    result = cleanup.plan_cleanup(
        [worker("rdst-keyservice-cl100", 1)], [], [database("rdst-keyservice-db-cl100", 3)],
        NOW, MONTH, unexpected,
    )
    assert not result


def test_leftovers_without_a_worker_go_by_their_own_age():
    result = plan(
        [],
        [domain("pr-100-7")],
        [database("rdst-keyservice-db-pr-100-7", 31), database("rdst-keyservice-db-pr-100-8", 2)],
    )
    # A domain without its Worker routes nowhere, whatever its age.
    assert [d["hostname"] for d in result.domains] == ["keyservice-pr-100-7.readyset.io"]
    assert [d["name"] for d in result.databases] == ["rdst-keyservice-db-pr-100-7"]


@pytest.mark.parametrize(
    "name",
    [
        "rdst-keyservice",
        "rdst-keyservice-staging",
        "rdst-keyservice-faruk",
        "rdst-keyservice-cl100x",
        "rdst-keyservice-pr-abc",
    ],
)
def test_never_touches_anything_but_ci_environments(name):
    db_name = name.replace("rdst-keyservice", "rdst-keyservice-db", 1)
    host = name.replace("rdst-keyservice", "keyservice", 1)
    result = plan(
        [worker(name, 400)],
        [{"id": "x", "hostname": f"{host}.readyset.io", "service": name}],
        [database(db_name, 400)],
    )
    assert not result


class FakeCloudflare:
    def __init__(self, workers, domains, databases, fail=(), relisted_workers=None):
        """relisted_workers is the Worker listing from the second listing on."""
        self.workers = workers
        self.relisted_workers = workers if relisted_workers is None else relisted_workers
        self.worker_listings = 0
        self.domains = domains
        self.databases = databases
        self.fail = set(fail)
        self.deleted = []
        self.requests = 0

    def list(self, path):
        self.requests += 1
        if path == "/workers/scripts":
            self.worker_listings += 1
            return self.workers if self.worker_listings == 1 else self.relisted_workers
        assert path == "/workers/domains"
        return self.domains

    def list_pages(self, path):
        self.requests += 1
        # Unfiltered: the pattern alone picks the CI databases.
        assert path == "/d1/database"
        return self.databases

    def bound_databases(self, worker):
        self.requests += 1
        return None

    def request(self, method, path):
        assert method == "DELETE"
        if path in self.fail:
            raise RuntimeError("boom")
        self.deleted.append(path)


def run(monkeypatch, capsys, fake, *args):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account")
    monkeypatch.setattr(cleanup, "Cloudflare", lambda token, account: fake)
    monkeypatch.setattr(cleanup, "utc_now", lambda: NOW)
    code = cleanup.main(list(args))
    return code, capsys.readouterr()


def test_stops_after_the_listings_when_nothing_is_stale(monkeypatch, capsys):
    fake = FakeCloudflare([worker("rdst-keyservice-cl100", 1)], [domain("cl100")], [])
    code, output = run(monkeypatch, capsys, fake)
    assert code == 0
    assert fake.requests == 3 and fake.deleted == []
    assert "No keyservice CI environment is older than 30 days" in output.out


def test_deletes_domains_then_workers_then_databases(monkeypatch, capsys):
    fake = FakeCloudflare(
        [worker("rdst-keyservice-cl100", 40)],
        [domain("cl100")],
        [database("rdst-keyservice-db-cl100", 40)],
        fail={"/d1/database/uuid-rdst-keyservice-db-cl100"},
    )
    code, output = run(monkeypatch, capsys, fake)
    assert fake.deleted == ["/workers/domains/id-cl100", "/workers/scripts/rdst-keyservice-cl100?force=true"]
    # A failed deletion fails the run once everything else has been tried.
    assert code == 1
    assert "delete failed for database rdst-keyservice-db-cl100" in output.err

    dry = FakeCloudflare([worker("rdst-keyservice-cl100", 40)], [], [])
    code, output = run(monkeypatch, capsys, dry, "--dry-run")
    assert code == 0 and dry.deleted == []
    assert "would delete worker rdst-keyservice-cl100" in output.out


def test_listing_errors_fail_before_any_deletion(monkeypatch):
    serve(monkeypatch, json.dumps({"success": False, "errors": [{"message": "denied"}]}).encode())
    with pytest.raises(RuntimeError, match="denied"):
        cleanup.Cloudflare("token", "account").list("/workers/scripts")


class Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def serve(monkeypatch, *bodies):
    responses = iter(bodies)
    monkeypatch.setattr(
        cleanup.urllib.request, "urlopen", lambda request, timeout: Response(next(responses))
    )


def test_an_empty_successful_deletion_counts(monkeypatch):
    serve(monkeypatch, b"")
    assert cleanup.Cloudflare("token", "account").request("DELETE", "/workers/domains/x") == {"success": True}
    serve(monkeypatch, b"")
    with pytest.raises(json.JSONDecodeError):
        cleanup.Cloudflare("token", "account").list("/workers/scripts")


@pytest.mark.parametrize(
    ("bindings", "expected"),
    [
        ([{"type": "d1", "name": "DB", "id": "uuid-a"}, {"type": "plain_text", "name": "X"}], {"uuid-a"}),
        ([{"type": "plain_text", "name": "X"}], None),
        ([{"type": "d1", "name": "DB"}], None),
    ],
)
def test_bound_databases_fail_closed(monkeypatch, bindings, expected):
    serve(monkeypatch, json.dumps({"success": True, "result": {"bindings": bindings}}).encode())
    assert cleanup.Cloudflare("token", "account").bound_databases("rdst-keyservice-cl1") == expected


def test_an_env_redeployed_during_the_run_is_kept(monkeypatch, capsys):
    # Stale in the first listing, redeployed by a new patchset before deletion.
    stale = worker("rdst-keyservice-cl100", 40)
    fake = FakeCloudflare(
        [stale, worker("rdst-keyservice-cl200", 40)],
        [domain("cl100"), domain("cl200")],
        [database("rdst-keyservice-db-cl100", 40), database("rdst-keyservice-db-cl200", 40)],
        relisted_workers=[worker("rdst-keyservice-cl100", 0), worker("rdst-keyservice-cl200", 40)],
    )
    code, _ = run(monkeypatch, capsys, fake)
    assert code == 0
    assert fake.deleted == [
        "/workers/domains/id-cl200",
        "/workers/scripts/rdst-keyservice-cl200?force=true",
        "/d1/database/uuid-rdst-keyservice-db-cl200",
    ]
