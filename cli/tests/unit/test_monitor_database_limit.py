"""The account-wide monitored-database limit: fingerprint, claims, local-only, release, pause."""

from __future__ import annotations

import selectors
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from features.monitor import database_limit
from features.monitor.analyze_queue import AnalyzeQueue
from features.monitor.api import routes
from shared.llm_manager import inference_attribution
from shared.llm_manager.jev_client import JevClientError
from shared.monitor.monitor_store import MonitorStore
from tests.unit.monitor_replay_fixtures import NOW, series
from tests.unit.test_monitor_analyze_queue import FakeService, add_query
from tests.unit.test_monitor_replay import Clock, ScriptedJev, loop_for

TARGETS = {name: {"engine": "postgresql", "host": f"{name}.example.com", "port": 5432,
                  "database": "app"} for name in ("a", "b", "c", "d")}


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    """Signed in unless a test says otherwise: a slot held while signed out shows pending."""
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(database_limit, "own_key", lambda: False)


@pytest.fixture
def targets(monkeypatch):
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get",
                        lambda self, name: TARGETS.get(name))
    monkeypatch.setattr("shared.config.targets.TargetsConfig.list_targets",
                        lambda self: list(TARGETS))
    monkeypatch.setattr(database_limit, "_account_salt", lambda: "user-1")


def test_fingerprint_is_stable_salted_and_hides_the_database():
    config = {"engine": "PostgreSQL", "host": "DB.Example.com", "database": "app"}
    first = database_limit.fingerprint(config, salt="user-1")
    assert first == database_limit.fingerprint(
        {"engine": "postgresql", "host": "db.example.com", "port": 5432, "database": "app"},
        salt="user-1")
    assert len(first) == 64 and "example" not in first
    assert first != database_limit.fingerprint(config, salt="user-2")
    assert first != database_limit.fingerprint({**config, "database": "other"}, salt="user-1")
    assert database_limit.fingerprint(None, salt="user-1") is None


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "[::1]", "LOCALHOST", "",
                                  "127.0.1.1", "0.0.0.0"])
def test_loopback_hosts_are_one_database(host):
    config = {"engine": "mysql", "host": host, "port": 3306, "database": "shop"}
    assert database_limit.fingerprint(config, salt="u") == database_limit.fingerprint(
        {**config, "host": "localhost"}, salt="u")
    assert database_limit.fingerprint(config, salt="u") != database_limit.fingerprint(
        {**config, "host": "db.internal"}, salt="u")


def test_monitoring_calls_carry_the_fingerprint_header():
    assert inference_attribution.monitor_database_headers() == {}
    with inference_attribution.monitoring_database("f" * 64):
        assert inference_attribution.monitor_database_headers() == {
            "X-RDST-Monitor-Database": "f" * 64}
    assert inference_attribution.monitor_database_headers() == {}


def test_jev_client_sends_the_header(monkeypatch):
    from shared.llm_manager.jev_client import JevClient

    sent = {}

    class Session:
        def post(self, url, json, headers, timeout):
            sent.update(headers)
            return SimpleNamespace(status_code=403, headers={},
                                   json=lambda: {"code": "MONITOR_DATABASE_LIMIT"})

    monkeypatch.setattr(JevClient, "_session_token", staticmethod(lambda **kw: "token"))
    monkeypatch.setattr("shared.agent_activity.record_jev_call", lambda *a, **k: None)
    with inference_attribution.monitoring_database("a" * 64), pytest.raises(JevClientError) as err:
        JevClient(Session())._post("/v1/jev/attention", {"request_id": "x"})
    assert err.value.code == "MONITOR_DATABASE_LIMIT" and database_limit.is_limit_error(err.value)
    assert sent["X-RDST-Monitor-Database"] == "a" * 64


def test_claim_maps_the_key_service_answer_to_an_ai_state(targets, monkeypatch):
    sent = []

    def answer(status, body):
        def request(method, path, **kwargs):
            sent.append((method, path, kwargs.get("json")))
            return (status, body) if status else None
        return request

    summary = {"limit": 3, "own_key_limit": 10, "active": 3, "hosted_active": 3, "databases": []}
    monkeypatch.setattr(database_limit, "_request", answer(200, {**summary, "claimed": True}))
    assert database_limit.claim(TARGETS["a"], with_own_key=False)["state"] == "on"
    assert sent[-1] == ("POST", "/v1/monitor/databases/claim",
                        {"fingerprint": database_limit.fingerprint(TARGETS["a"]), "own_key": False})
    monkeypatch.setattr(database_limit, "_request", answer(403, {
        **summary, "claimed": False, "code": "MONITOR_DATABASE_LIMIT"}))
    assert database_limit.claim(TARGETS["d"], with_own_key=True)["state"] == "local"
    assert sent[-1][2]["own_key"] is True
    monkeypatch.setattr(database_limit, "_request", answer(None, {}))
    assert database_limit.claim(TARGETS["d"], with_own_key=False)["state"] == "pending"
    monkeypatch.setattr(database_limit, "_request", answer(503, {"code": "ACCOUNTING_UNAVAILABLE"}))
    assert database_limit.claim(TARGETS["d"], with_own_key=False)["state"] == "pending"


def test_claim_says_why_ai_monitoring_is_not_on(targets, monkeypatch):
    def gives(result):
        monkeypatch.setattr(database_limit, "_request", lambda *a, **kw: result)
        return database_limit.claim(TARGETS["a"], with_own_key=False)

    assert gives(database_limit._NoAnswer("signed_out"))["reason"] == "signed_out"
    assert gives(database_limit._NoAnswer("unreachable"))["reason"] == "unreachable"
    assert gives((500, {"error": "boom"}))["reason"] == "service_error"
    assert gives((401, {}))["reason"] == "signed_out"
    assert gives((403, {"code": "MONITOR_DATABASE_LIMIT", "limit": 3, "active": 3}))[
        "reason"] == "limit"
    assert "reason" not in gives((200, {"limit": 3, "active": 1}))


def test_status_carries_each_targets_reason_and_since(tmp_path, targets, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("a", {"enabled": True})
    monkeypatch.setattr(database_limit, "account_slots", lambda: None)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    monkeypatch.setattr(database_limit.time, "time", lambda: 1_000)
    database_limit.set_ai_state(store, "a", "pending", "unreachable")
    monkeypatch.setattr(database_limit.time, "time", lambda: 1_500)
    # The same state and reason again keeps the original "since".
    database_limit.set_ai_state(store, "a", "pending", "unreachable")
    row = database_limit.status(store, refresh=False)["targets"][0]
    assert (row["ai"], row["reason"], row["since"]) == ("pending", "unreachable", 1_000)
    database_limit.set_ai_state(store, "a", "on")
    row = database_limit.status(store, refresh=False)["targets"][0]
    assert (row["ai"], row["reason"], row["since"]) == ("on", None, 1_500)
    # A slot held while signed out can't run AI: it shows as pending, signed out.
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    assert database_limit.display_state(store, "a") == "pending"
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    # Signed out overrides whatever the last claim said.
    database_limit.set_ai_state(store, "a", "pending", "service_error")
    monkeypatch.setattr(database_limit, "_signed_in", lambda: False)
    assert database_limit.ai_reason(store, "a")["reason"] == "signed_out"


def test_status_reads_the_limit_from_the_key_service():
    summary = {"limit": 3, "own_key_limit": 10, "active": 5, "hosted_active": 2}
    assert database_limit.status_from(summary, with_own_key=False) == {
        "in_use": 2, "limit": 3, "own_key": False, "known": True}
    assert database_limit.status_from(summary, with_own_key=True) == {
        "in_use": 5, "limit": 10, "own_key": True, "known": True}
    # Offline and never answered: the count is this machine's, the limit unknown.
    assert database_limit.status_from(None, with_own_key=False, local_in_use=2) == {
        "in_use": 2, "limit": None, "own_key": False, "known": False}
    # Raising the server limit raises the app's.
    assert database_limit.status_from({**summary, "limit": 5}, with_own_key=False)["limit"] == 5


@pytest.fixture
def client(monkeypatch, tmp_path, targets):
    original_select = selectors.EpollSelector.select
    monkeypatch.setattr(selectors.EpollSelector, "select",
                        lambda self, timeout=None: original_select(
                            self, 0.01 if timeout is None else min(timeout, 0.01)))
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_now", lambda: 10_000)
    monkeypatch.setattr(database_limit, "account_slots", lambda: None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/monitor")
    return TestClient(app)


class FakeKeyService:
    """Holds slots per fingerprint the way the Key Service does, for one account."""

    def __init__(self, limit=3):
        self.limit = limit
        self.slots: list[str] = []
        self.online = True

    def summary(self):
        return {"limit": self.limit, "own_key_limit": 10, "active": len(self.slots),
                "hosted_active": len(self.slots),
                "databases": [{"fingerprint": fp, "tier": "hosted"} for fp in self.slots]}

    def claim(self, config, with_own_key=None, label=None):
        if not self.online:
            return {"state": "pending"}
        fp = database_limit.fingerprint(config)
        if fp not in self.slots and len(self.slots) >= self.limit:
            return {**self.summary(), "state": "local", "code": "MONITOR_DATABASE_LIMIT"}
        if fp not in self.slots:
            self.slots.append(fp)
        return {**self.summary(), "state": "on"}

    def release(self, config):
        fp = database_limit.fingerprint(config)
        if fp in self.slots:
            self.slots.remove(fp)
        return True


@pytest.fixture
def keyservice(monkeypatch):
    service = FakeKeyService()
    monkeypatch.setattr(database_limit, "claim", service.claim)
    monkeypatch.setattr(database_limit, "release", service.release)
    monkeypatch.setattr(database_limit, "account_slots",
                        lambda: service.summary() if service.online else None)
    monkeypatch.setattr(database_limit, "own_key", lambda: False)
    monkeypatch.setattr(database_limit, "_last_summary", None)
    monkeypatch.setattr(database_limit, "_last_refresh", {})
    return service


def test_a_full_account_turns_on_local_monitoring_and_turning_off_frees_a_slot(
        client, keyservice, monkeypatch):
    reached = []
    monkeypatch.setattr(database_limit, "limit_reached",
                        lambda target, **kw: reached.append((target, kw)))
    for name in ("a", "b", "c"):
        response = client.put(f"/api/monitor/targets/{name}/settings", json={"enabled": True})
        assert response.status_code == 200 and response.json()["ai_monitoring"] == "on"
    limit = client.get("/api/monitor/limit", params={"target": "d"}).json()
    assert (limit["in_use"], limit["limit"], limit["full"], limit["target_fits"]) == (3, 3, True, False)
    assert client.get("/api/monitor/limit", params={"target": "a"}).json()["target_fits"] is True

    response = client.put("/api/monitor/targets/d/settings", json={"enabled": True})
    assert response.status_code == 200 and response.json()["ai_monitoring"] == "local"
    assert MonitorStore().monitored_targets() == {"a", "b", "c", "d"}
    assert reached == [("d", {"where": "enable", "active": 3, "limit": 3})]
    assert {row["target"]: row["ai"] for row in client.get("/api/monitor/limit").json()["targets"]} == {
        "a": "on", "b": "on", "c": "on", "d": "local"}
    # Other settings on a monitored database still save.
    assert client.put("/api/monitor/targets/a/settings", json={"execute": False}).status_code == 200

    store = MonitorStore()
    database_limit.file_refusal(store, "a", 9_000)
    response = client.put("/api/monitor/targets/a/settings", json={"enabled": False})
    assert response.status_code == 200 and response.json()["ai_monitoring"] == "off"
    assert len(keyservice.slots) == 2
    assert store.inbox(target="a")[0]["state"] == "resolved"
    # A slot is free: the local-only database asks again and gets AI monitoring.
    response = client.post("/api/monitor/targets/d/ai-monitoring")
    assert response.status_code == 200 and response.json()["ai_monitoring"] == "on"
    assert client.post("/api/monitor/targets/a/ai-monitoring").status_code == 409


def test_offline_at_enable_is_pending_and_the_claim_is_retried(client, keyservice):
    keyservice.online = False
    response = client.put("/api/monitor/targets/a/settings", json={"enabled": True})
    assert response.json()["ai_monitoring"] == "pending"
    limit = client.get("/api/monitor/limit").json()
    assert (limit["limit"], limit["known"], limit["in_use"]) == (None, False, 0)
    store = MonitorStore()
    database_limit.refresh_claims(store, {"a"})
    assert database_limit.ai_state(store, "a") == "pending"  # Retried too soon: skipped.
    keyservice.online = True
    database_limit.refresh_claims(store, {"a"}, force=True)
    assert database_limit.ai_state(store, "a") == "on"


def test_refresh_keeps_slots_fresh_and_retries_local_databases(tmp_path, keyservice, targets,
                                                               monkeypatch):
    keyservice.limit = 1
    store = MonitorStore(tmp_path / "monitor.db")
    calls = []
    real = keyservice.claim
    monkeypatch.setattr(database_limit, "claim",
                        lambda config, **kw: calls.append(config["host"][0]) or real(config))
    for name in ("a", "d"):
        store.set_settings(name, {"enabled": True})
    database_limit.set_ai_state(store, "a", "on")
    database_limit.set_ai_state(store, "d", "local")
    database_limit.refresh_claims(store, {"a", "d"})
    assert calls == ["a", "d"] and database_limit.ai_state(store, "d") == "local"
    database_limit.refresh_claims(store, {"a", "d"})
    assert calls == ["a", "d"]  # Nothing is due yet.
    start = database_limit.time.monotonic()
    clock = [start + database_limit.LOCAL_RETRY_S + 1]
    monkeypatch.setattr(database_limit.time, "monotonic", lambda: clock[0])
    # An admin raised the limit: the local-only database gets AI on its next retry.
    keyservice.limit = 2
    database_limit.refresh_claims(store, {"a", "d"})
    assert calls == ["a", "d", "d"] and database_limit.ai_state(store, "d") == "on"
    clock[0] = start + database_limit.REFRESH_S + 1
    database_limit.refresh_claims(store, {"a", "d"})
    assert calls == ["a", "d", "d", "a"]


def test_local_only_database_records_readings_without_ai_checks(monkeypatch, tmp_path, targets):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "local")
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)
    client = ScriptedJev({"q": ("notify", "latency_regression", 0.8)})
    loop = loop_for(monkeypatch, tmp_path, store, Clock(), client)
    loop.cycle("db")
    assert store.read("SELECT outcome FROM judge_run") == [{"outcome": "local_only"}]
    assert database_limit.paused(store, "db", NOW)


def test_refused_attention_files_one_item_and_pauses_ai_checks(monkeypatch, tmp_path, targets):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)
    clock = Clock()
    seen = []

    class Refusing(ScriptedJev):
        def attention(self, **request):
            seen.append(inference_attribution.monitor_database_headers())
            return super().attention(**request)

    client = Refusing({"q": ("notify", "latency_regression", 0.8)},
                      failures=[JevClientError("MONITOR_DATABASE_LIMIT", 403), None])
    monkeypatch.setattr(database_limit, "target_fingerprint", lambda target: "e" * 64)
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox(target="db")[0]
    assert (item["subject"], item["title"]) == ("database_limit", "Monitoring limit reached")
    assert seen == [{"X-RDST-Monitor-Database": "e" * 64}]
    assert database_limit.paused(store, "db", clock.now)

    def next_cycle(step):
        clock.now += step
        series(store, "q", now=clock.now, before_calls=100, after_calls=100,
               before_ms=10, after_ms=14)
        loop.cycle("db")

    next_cycle(300)
    assert len(seen) == 1  # Held for an hour; readings keep recording.
    assert store.read("SELECT outcome FROM judge_run ORDER BY id") == [
        {"outcome": "error"}, {"outcome": "backoff"}]
    next_cycle(database_limit.RETRY_S)
    assert len(seen) == 2
    assert store.inbox(target="db", state="resolved")[0]["subject"] == "database_limit"


@pytest.mark.asyncio
async def test_paused_target_runs_only_analyses_the_user_asked_for(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    add_query(store, "one", 100, now=NOW)
    database_limit.file_refusal(store, "db", NOW - 60)
    service = FakeService()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    assert service.calls == []
    store.enqueue_analysis("db", "one", "user", 1, NOW)
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_refused_analysis_files_the_limit_item(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    add_query(store, "one", 100, now=NOW)
    await AnalyzeQueue(store, service=FakeService(llm_error="MONITOR_DATABASE_LIMIT"),
                       clock=lambda: NOW).tick()
    job = store.analyze_jobs("db")[0]
    assert job["state"] == "failed" and job["error"].startswith("MONITOR_DATABASE_LIMIT")
    assert store.inbox(target="db")[0]["subject"] == "database_limit"


def test_a_used_up_jev_allowance_holds_checks_and_resumes_without_a_restart(
        monkeypatch, tmp_path, targets):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "on")
    series(store, "q", before_calls=100, after_calls=100, before_ms=10, after_ms=14)
    clock = Clock()
    seen = []
    reached = []
    monkeypatch.setattr(database_limit, "limit_reached",
                        lambda target, **kw: reached.append((target, kw["kind"])))

    class Exhausted(ScriptedJev):
        def attention(self, **request):
            seen.append(1)
            return super().attention(**request)

    error = JevClientError("JEV_BUDGET_EXHAUSTED", 429)
    error.retry_after = 3600
    client = Exhausted({"q": ("notify", "latency_regression", 0.8)}, failures=[error, None])
    loop = loop_for(monkeypatch, tmp_path, store, clock, client)
    loop.cycle("db")
    item = store.inbox(target="db")[0]
    assert (item["subject"], item["title"]) == ("jev_allowance", "AI limit reached")
    assert reached == [("db", "jev")]
    assert database_limit.allowance_reached(store) == ["jev"]
    assert database_limit.display_state(store, "db") == "paused"

    def next_cycle(step):
        clock.now += step
        series(store, "q", now=clock.now, before_calls=100, after_calls=100,
               before_ms=10, after_ms=14)
        loop.cycle("db")

    next_cycle(300)
    assert len(seen) == 1  # Held: no retry storm, readings keep recording.
    assert store.read("SELECT outcome FROM judge_run ORDER BY id")[-1] == {"outcome": "allowance"}
    # An admin raised the limit; the next check after the hold goes through on its own.
    next_cycle(database_limit.ALLOWANCE_RETRY_S)
    assert len(seen) == 2
    assert database_limit.allowance_reached(store) == []
    assert store.inbox(target="db", state="resolved")[0]["subject"] == "jev_allowance"
    assert database_limit.display_state(store, "db") == "on"


@pytest.mark.asyncio
async def test_a_used_up_hosted_allowance_pauses_the_analysis_queue(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    # One analysis at a time, so exactly one meets the used-up allowance.
    monkeypatch.setenv("RDST_MONITOR_ANALYSIS_LIMIT", "1")
    monkeypatch.setattr(database_limit, "limit_reached", lambda target, **kw: None)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    add_query(store, "one", 100, now=NOW)
    add_query(store, "two", 90, now=NOW)
    await AnalyzeQueue(store, service=FakeService(llm_error="HOSTED_CAP_REACHED"),
                       clock=lambda: NOW).tick()
    assert [job["state"] for job in store.analyze_jobs("db")].count("failed") == 1
    assert database_limit.allowance_reached(store) == ["hosted"]
    assert database_limit.paused(store, "db", NOW)
    service = FakeService()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW + 60).tick()
    assert service.calls == []  # The queue holds; nothing retries.
    later = NOW + database_limit.ALLOWANCE_RETRY_S + 1
    await AnalyzeQueue(store, service=service, clock=lambda: later).tick()
    assert len(service.calls) == 1
    assert database_limit.allowance_reached(store) == []


def test_signing_in_claims_at_once_instead_of_saying_signed_out(tmp_path, keyservice, targets,
                                                                monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("a", {"enabled": True})
    database_limit.set_ai_state(store, "a", "pending", "signed_out")
    # Signed in now (the autouse fixture): the next read claims the slot.
    assert database_limit.ai_reason(store, "a")["reason"] is None
    assert database_limit.ai_state(store, "a") == "on"


@pytest.mark.asyncio
async def test_global_hosted_cap_backs_off_and_recovers(tmp_path, monkeypatch, targets):
    monkeypatch.setenv("RDST_MONITOR_ANALYZE_DAILY_CAP", "100")
    # One analysis at a time, so the second job can't start before the first files the cap.
    monkeypatch.setenv("RDST_MONITOR_ANALYSIS_LIMIT", "1")
    monkeypatch.setattr(database_limit, "own_key", lambda: False)
    monkeypatch.setattr(database_limit, "limit_reached", lambda *a, **kw: None)
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "execute": True})
    database_limit.set_ai_state(store, "db", "on")
    add_query(store, "one", 100, now=NOW)
    add_query(store, "two", 90, now=NOW)
    service = FakeService(llm_error="HOSTED_GLOBAL_CAP_REACHED")
    clock = [NOW]
    queue = AnalyzeQueue(store, service=service, clock=lambda: clock[0])
    await queue.tick()
    assert database_limit.display_state(store, "db") == "paused"
    assert database_limit.ai_reason(store, "db")["reason"] == "quota"
    item = store.inbox(target="db")[0]
    assert (item["subject"], item["title"]) == ("hosted_allowance", "AI limit reached")
    assert store.analyze_jobs("db")[0]["billable_calls"] == 0
    clock[0] += 60
    await queue.tick()
    assert len(service.calls) == 1
    service.llm_error = None
    clock[0] = NOW + database_limit.ALLOWANCE_RETRY_S + 1
    await queue.tick()
    assert len(service.calls) == 2
    assert database_limit.allowance_reached(store) == []
    assert database_limit.display_state(store, "db") == "on"
    assert store.inbox(target="db", state="resolved")[0]["subject"] == "hosted_allowance"


def test_own_key_work_ignores_hosted_cap_and_preserves_hosted_accounting(
        tmp_path, monkeypatch):
    monkeypatch.setattr(database_limit, "limit_reached", lambda *a, **kw: None)
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "on")
    monkeypatch.setattr(database_limit, "own_key", lambda: False)
    database_limit.file_allowance(store, "db", "hosted", NOW)
    assert database_limit.paused(store, "db", NOW + 60)
    monkeypatch.setattr(database_limit, "own_key", lambda: True)
    assert not database_limit.paused(store, "db", NOW + 60)
    assert database_limit.display_state(store, "db") == "on"
    assert database_limit.status(store, refresh=False)["allowance"] == []
    database_limit.clear_allowance(store, "hosted", NOW + 60)
    assert database_limit.allowance_reached(store) == ["hosted"]
    monkeypatch.setattr(database_limit, "own_key", lambda: False)
    assert database_limit.paused(store, "db", NOW + 60)
    assert database_limit.display_state(store, "db") == "paused"
    assert database_limit.status(store, refresh=False)["allowance"] == ["hosted"]
    monkeypatch.setattr(database_limit, "own_key", lambda: True)
    database_limit.file_allowance(store, "db", "jev", NOW)
    assert database_limit.display_state(store, "db") == "paused"
    assert database_limit.allowance_paused(store, "jev", NOW + 60)
    database_limit.set_ai_state(store, "db", "local", "limit")
    assert database_limit.paused(store, "db", NOW + 60)


def test_refused_database_collects_then_release_and_raise_resume_in_place(
        client, keyservice, monkeypatch):
    from features.query_registry.discovery import QueryDiscoveryCollector
    from shared.query_registry.delta_processor import COMPLETE, CounterRow, DeltaResult

    for name in ("a", "b", "c", "d"):
        response = client.put(f"/api/monitor/targets/{name}/settings", json={"enabled": True})
        assert response.status_code == 200
    assert response.json()["ai_monitoring"] == "local"
    store = MonitorStore()
    collector = QueryDiscoveryCollector("d", two_phase=True)
    collector._monitor = store
    collector._engine = "mysql"
    collector._alias_map = {"app:digest": "query"}
    collector._monitor_db_sample = (0, 0, 100, 1, 0, 2, 100, "epoch")
    collector._baseline = {"app:digest": CounterRow(
        "app:digest", 60, 10, 600, 10, 10, 10, 120, "epoch")}
    registry = SimpleNamespace(get_query=lambda _: SimpleNamespace(original_sql="SELECT id FROM orders"))
    collector._record_monitor_cycle(120, [DeltaResult(
        "app:digest", 60, 120, COMPLETE, 60, 600.0, 1.0, 60)], registry, [])
    assert store.read("SELECT calls FROM stmt_series WHERE target='d' AND res=60") == [{"calls": 60}]
    assert store.read("SELECT calls FROM db_series WHERE target='d' AND res=60") == [{"calls": 60}]
    assert database_limit.ai_reason(store, "d")["reason"] == "limit"
    assert database_limit.paused(store, "d", NOW)
    assert client.put("/api/monitor/targets/a/settings", json={"enabled": False}).status_code == 200
    assert client.post("/api/monitor/targets/d/ai-monitoring").json()["ai_monitoring"] == "on"
    assert not database_limit.paused(store, "d", NOW)
    assert client.put("/api/monitor/targets/a/settings", json={"enabled": True}).json()["ai_monitoring"] == "local"
    keyservice.limit = 4
    # The ordinary worker retries in the same process after an admin raises the limit.
    database_limit.refresh_claims(store, {"a", "b", "c", "d"}, force=True)
    assert database_limit.ai_state(store, "a") == "on"
    assert client.get("/api/monitor/limit").json()["limit"] == 4
    collector._record_monitor_cycle(180, [DeltaResult(
        "app:digest", 120, 180, COMPLETE, 30, 300.0, 1.0, 60)], registry, [])
    assert store.read("SELECT SUM(calls) AS calls FROM stmt_series WHERE target='d' AND res=60") == [{"calls": 90}]


def test_status_lists_slots_held_for_databases_not_on_this_computer(tmp_path, targets, monkeypatch):
    store = MonitorStore(tmp_path / "monitor.db")
    here = database_limit.target_fingerprint("a")
    other = "f" * 64
    summary = {"limit": 3, "active": 2, "hosted_active": 2,
               "databases": [{"fingerprint": here, "tier": "hosted", "last_seen_at": 900},
                             {"fingerprint": other, "tier": "hosted", "last_seen_at": 800,
                              "label": "orders"}]}
    monkeypatch.setattr(database_limit, "account_slots", lambda: summary)
    assert database_limit.status(store)["elsewhere"] == [
        {"fingerprint": other, "tier": "hosted", "last_seen_at": 800, "label": "orders"}]
    released = []
    monkeypatch.setattr(database_limit, "_request", lambda method, path, **kwargs: (
        released.append((path, kwargs["json"])) or (200, {"released": True})))
    assert database_limit.release_fingerprint(other)
    assert released == [("/v1/monitor/databases/release", {"fingerprint": other})]


def test_reenable_preserves_analysis_preferences(client, keyservice):
    with client:
        url = "/api/monitor/targets/a/settings"
        response = client.put(url, json={"enabled": True, "deep_analysis": False,
                                         "analysis_mode": "plans_only"})
        assert response.status_code == 200
        assert client.put(url, json={"enabled": False}).status_code == 200
        response = client.put(url, json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["deep_analysis"] is False
        assert response.json()["execute"] is False
        assert response.json()["analysis_mode"] == "plans_only"


@pytest.mark.parametrize("signed_in", [True, False])
def test_unknown_limit_counts_only_ai_on(tmp_path, monkeypatch, signed_in):
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(database_limit, "_signed_in", lambda: signed_in)
    monkeypatch.setattr(database_limit, "_last_summary",
                        {"limit": 3, "active": 3, "hosted_active": 3} if not signed_in else None)
    for target, state in (("a", "on"), ("b", "pending"), ("c", "local")):
        store.set_settings(target, {"enabled": True})
        database_limit.set_ai_state(store, target, state)
    result = database_limit.status(store, refresh=False)
    assert result["in_use"] == (1 if signed_in else 0)


def test_settings_identify_saved_preferences_before_first_enable(client):
    url = "/api/monitor/targets/a/settings"
    assert client.get(url).json()["configured"] is False
    response = client.put(url, json={"deep_analysis": False, "execute": False})
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["enabled_at"] is None
    assert client.get(url).json()["configured"] is True
