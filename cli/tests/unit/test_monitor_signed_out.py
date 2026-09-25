"""Signed-out automatic AI stays pending while local monitoring keeps recording."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from features.monitor import database_limit, report, snapshot
from features.monitor.analyze_queue import AnalyzeQueue, queue_plan, waiting_reason
from shared import account_session
from shared.llm_manager import LLMManager
from shared.llm_manager.base import LLMError
from shared.llm_manager.inference_attribution import inference_workflow
from shared.llm_manager.jev_client import JevClient, JevClientError
from shared.llm_manager.key_resolution import resolve_api_key
from shared.monitor.monitor_store import MonitorStore
from tests.unit.test_monitor_analyze_queue import FakeService, NOW, add_query
from tests.unit.test_monitor_database_limit import series
from tests.unit.test_monitor_replay import Clock, ScriptedJev, loop_for


@pytest.fixture(params=["none", "env", "keyring", "saved_claude", "saved_readyset"])
def authorization(request, monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, name: {})
    preference = {"saved_claude": "claude", "saved_readyset": "readyset"}.get(request.param)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get_llm_provider", lambda self: preference)
    monkeypatch.setattr("shared.secret_store_service.SecretStoreService.get_secret",
                        lambda self, name: "unit-own-key" if request.param == "keyring" and
                        name == "ANTHROPIC_API_KEY" else None)
    if request.param in {"env", "saved_claude", "saved_readyset"}:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-own-key")
    signed_in = [False]
    monkeypatch.setattr(account_session, "access_token", lambda **kw: "unit-session" if signed_in[0] else None)
    monkeypatch.setattr(account_session, "is_signed_in_locally", lambda *a: signed_in[0])
    monkeypatch.setattr(database_limit, "_signed_in", lambda: bool(account_session.access_token()))
    return request.param, signed_in


@pytest.mark.asyncio
async def test_automatic_lanes_hold_and_resume(authorization, monkeypatch, tmp_path):
    _, signed_in = authorization
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "nightly_report": True,
                              "nightly_hour": 0})
    store.start_first_hour("db", NOW - 30)
    database_limit.set_ai_state(store, "db", "on")
    # Yesterday's local day has traffic, so the nightly report is due in any process zone.
    _, yesterday_end, _ = snapshot.report_period("nightly", NOW)
    store._write(lambda conn: conn.execute(
        "INSERT INTO stmt_series (target,query_key,res,bucket_start,calls,total_ms,rows,blks_hit,blks_read) VALUES (?,?,?,?,?,?,?,?,?)",
        ("db", "q", 60, yesterday_end - 60, 100, 1000, 0, 0, 0)))
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    jev = ScriptedJev({"q": ("notify", "latency_regression", .8)})
    attention = Mock(wraps=jev.attention)
    jev.attention = attention
    clock = Clock(NOW)
    loop = loop_for(monkeypatch, tmp_path, store, clock, jev)
    narrative = Mock(side_effect=AssertionError("automatic narrative called signed out"))
    monkeypatch.setattr(report, "_narrate", narrative)
    scheduler = report.ReportScheduler(store, clock=lambda: NOW)
    for tick in range(3):
        add_query(store, "one", 100, now=NOW + tick * 60)
        series(store, "q", now=clock.now, before_calls=100, after_calls=100,
               before_ms=10, after_ms=14)
        loop.cycle("db")
        await queue.tick()
        assert scheduler._plan(NOW + tick * 60) == []
        clock.now += 60
    assert service.calls == []
    attention.assert_not_called()
    narrative.assert_not_called()
    assert len(store.read("SELECT * FROM monitor_pulse WHERE kind='collect'")) >= 3
    assert database_limit.ai_reason(store, "db")["reason"] == "signed_out"
    assert store.read("SELECT detail FROM monitor_pulse WHERE kind='attention' ORDER BY id DESC LIMIT 1")[0][
        "detail"] == "Sign in to turn on AI monitoring"
    jobs = store.analyze_jobs("db")
    assert jobs and all(job["state"] == "queued" for job in jobs)
    plan = queue_plan(store, "db", NOW)
    assert waiting_reason(plan, jobs[0]["id"]) == "Sign in to turn on AI monitoring"
    # A restart keeps the durable jobs held.
    store = MonitorStore(store.path, clock=lambda: NOW)
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    await queue.tick()
    assert service.calls == []
    assert queue._retry_at <= NOW + 60
    # One ordinary queue tick resumes; no auth failure retry backoff was spent.
    signed_in[0] = True
    await queue.tick()
    assert service.calls
    assert queue._retry_at is None or queue._retry_at <= NOW + 60
    loop.cycle("db")
    attention.assert_called_once()
    assert scheduler._plan(NOW)


@pytest.mark.parametrize("feature", ["monitor", "analyze"])
def test_provider_resolution_guard_and_manual_policy(authorization, feature, monkeypatch):
    variant, signed_in = authorization
    from shared.llm_manager.base import ProviderResponse
    from shared.llm_manager.claude_provider import ClaudeProvider
    from shared.llm_manager.hosted_glm_provider import HostedGLMProvider

    transport = Mock(return_value=ProviderResponse(text="unit", usage={"total_tokens": 1}))
    hosted_transport = Mock(return_value=ProviderResponse(text="unit", usage={"total_tokens": 1}))
    monkeypatch.setattr(ClaudeProvider, "complete", transport)
    monkeypatch.setattr(HostedGLMProvider, "complete", hosted_transport)
    with inference_workflow(feature, "web"):
        if feature == "monitor" or variant in {"none", "saved_readyset"}:
            with pytest.raises(LLMError):
                LLMManager().generate_response("unit prompt")
            transport.assert_not_called()
            hosted_transport.assert_not_called()
        else:
            assert resolve_api_key().provider == "claude"
            LLMManager().generate_response("manual unit prompt")
            transport.assert_called_once()
    signed_in[0] = True
    with inference_workflow("monitor", "web"):
        assert resolve_api_key().provider in {"claude", "readyset"}
        LLMManager().generate_response("automatic unit prompt")
    calls_before_logout = transport.call_count + hosted_transport.call_count
    signed_in[0] = False
    with inference_workflow("monitor", "web"), pytest.raises(LLMError, match="signed out"):
        LLMManager().generate_response("automatic unit prompt after logout")
    assert transport.call_count + hosted_transport.call_count == calls_before_logout


def test_jev_transport_never_sends_signed_out(authorization):
    session = SimpleNamespace(post=Mock())
    client = JevClient(session)
    with pytest.raises(JevClientError, match="LOGIN_REQUIRED"):
        client.assess(request_id="unit", rubric_version="unit", sql="SELECT * FROM orders",
                      dialect="postgresql", evidence={}, input_fingerprint="unit")
    with pytest.raises(JevClientError, match="LOGIN_REQUIRED"):
        client.attention(request_id="unit", target_numbers={}, index_changes=[],
                         open_items=[], queries=[])
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_manual_analysis_with_own_key_remains_allowed(authorization, tmp_path):
    variant, _ = authorization
    if variant not in {"env", "keyring", "saved_claude"}:
        return
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    add_query(store, "one", 100)
    store.enqueue_analysis("db", "one", "user", 1, NOW)
    service = FakeService()
    await AnalyzeQueue(store, service=service, clock=lambda: NOW).tick()
    assert len(service.calls) == 1
    assert store.analyze_jobs("db")[0]["state"] == "done"


@pytest.mark.asyncio
async def test_logout_after_claim_holds_without_retry_backoff(authorization, tmp_path):
    _, signed_in = authorization
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    add_query(store, "one", 100)
    store.enqueue_analysis("db", "one", "new", 1, NOW)
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    signed_in[0] = True
    job = queue._claim("db")
    assert job is not None
    signed_in[0] = False
    await queue._process(job)
    assert service.calls == []
    held = store.analyze_jobs("db")[0]
    assert (held["state"], held["error"], held["started_at"]) == ("queued", "signed out", None)
    signed_in[0] = True
    await queue.tick()
    assert len(service.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("visible", [False, True])
async def test_classification_worker_waits_then_resumes(authorization, monkeypatch, visible):
    from features.query_registry import assessment

    _, signed_in = authorization
    monkeypatch.setattr(assessment, "is_signed_in_locally", lambda: signed_in[0])
    library = Mock()
    library.assessments_for_target.return_value = {}
    library.claim_assessment.return_value = {
        "id": 1, "generation": 1, "hash": "one", "sql": "SELECT * FROM orders",
        "request_id": "unit",
    }
    library.complete_assessment.return_value = False
    monkeypatch.setattr(assessment, "QueryRegistry", lambda: SimpleNamespace(library_store=library))
    monkeypatch.setattr(assessment.MonitorStore, "full_detail_keys", lambda *a: {"one"})
    monkeypatch.setattr(assessment.MonitorStore, "sync_query_ui", lambda *a, **kw: None)
    monkeypatch.setattr(assessment, "collect_assessment_context", lambda *a, **kw: SimpleNamespace(
        payload={}, fingerprint="unit", collected_at="unit", coverage="unit", summary=lambda: "unit"))
    monkeypatch.setattr(assessment, "validate_response", lambda value: {
        "priority_score": 20, "band": "Low", "confidence": .8, "findings": []})
    worker = assessment.AssessmentWorker()
    monkeypatch.setattr(worker, "_current_config", lambda target: {"engine": "postgresql"})
    monkeypatch.setattr(worker, "_publish", Mock(side_effect=_publish_nothing))
    provider = Mock(return_value={})
    worker._client = SimpleNamespace(assess=provider)
    if visible:
        worker.select_target("db")
    else:
        monitor = MonitorStore()
        monitor.set_settings("db", {"enabled": True})
        database_limit.set_ai_state(monitor, "db", "on")
        worker.set_background_targets({"db"})
    assert not await worker._process_one("db")
    provider.assert_not_called()
    library.claim_assessment.assert_not_called()
    assert library.set_target_assessment_state.call_args.args[2] == "waiting_auth"
    signed_in[0] = True
    assert await worker._process_one("db")
    provider.assert_called_once()
    assert assessment.IDLE_SECONDS <= 60


async def _publish_nothing(*args):
    pass


@pytest.mark.asyncio
async def test_background_narrative_retry_is_held(authorization, monkeypatch, tmp_path):
    import json

    _, signed_in = authorization
    store = MonitorStore(tmp_path / "monitor.db")
    run, _ = store.claim_report("db", "nightly", NOW)
    narrative = {"narrative_error": "service unavailable", "narrative_retry_at": NOW,
                 "narrative_retry_count": 0, "computed": {}, "provisional_query_keys": []}
    store.update_report(run["id"], state="done", report=narrative)
    provider = Mock(return_value={"summary": "unit", "fix_first": [], "one_fix_several_queries": [],
                                  "observations": []})
    monkeypatch.setattr(report, "_narrate", provider)
    monkeypatch.setattr(report, "validate_narrative", lambda value: value)
    monkeypatch.setattr(report, "_bound_narrative", lambda value, context: value)
    scheduler = report.ReportScheduler(store, clock=lambda: NOW)
    assert scheduler._plan(NOW) == []
    await scheduler._retry_narrative(run["id"], "db", narrative)
    provider.assert_not_called()
    assert json.loads(store.report(run["id"])["report_json"])["narrative_retry_at"] == NOW
    signed_in[0] = True
    assert scheduler._plan(NOW)[0][0] == "retry"
    await scheduler._retry_narrative(run["id"], "db", narrative)
    provider.assert_called_once()


def test_sign_out_preserves_local_only_slot_policy(authorization, tmp_path):
    _, signed_in = authorization
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "local", "limit", NOW)
    assert database_limit.paused(store, "db", NOW)
    assert database_limit.ai_reason(store, "db")["reason"] == "signed_out"
    signed_in[0] = True
    assert database_limit.ai_state(store, "db") == "local"
    assert database_limit.paused(store, "db", NOW + 60)


@pytest.mark.asyncio
async def test_logout_during_parameter_collection_holds_analysis(authorization, tmp_path):
    _, signed_in = authorization
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True})
    add_query(store, "one", 100)

    class SigningOutService(FakeService):
        async def suggest_parameter_values(self, *args):
            signed_in[0] = False
            return {"placeholders": []}

    service = SigningOutService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    signed_in[0] = True
    await queue.tick()
    assert service.calls == []
    assert store.analyze_jobs("db")[0]["state"] == "queued"
    assert store.analyze_jobs("db")[0]["error"] == "signed out"
