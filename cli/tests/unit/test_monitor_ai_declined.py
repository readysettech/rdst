"""With AI turned off in a build that asks first, nothing goes to AI and nothing retries,
while local monitoring keeps recording."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from features.monitor import database_limit, report
from features.monitor.analyze_queue import AnalyzeQueue, queue_plan, waiting_reason
from features.query_registry import assessment
from shared import account_session, ai_data_consent
from shared.monitor.monitor_store import MonitorStore
from tests.unit.test_monitor_analyze_queue import FakeService, NOW, add_query
from tests.unit.test_monitor_database_limit import series
from tests.unit.test_monitor_replay import Clock, ScriptedJev, loop_for
from tests.unit.test_monitor_v4 import NOW as REPORT_NOW, _seed


@pytest.fixture
def consent(monkeypatch, tmp_path):
    """A signed-in Store build whose AI answer the test sets; starts declined."""
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RDST_REQUIRE_AI_DATA_CONSENT", "1")
    answer = ["denied"]
    monkeypatch.setattr(ai_data_consent, "ai_data_consent", lambda: answer[0])
    monkeypatch.setattr(account_session, "access_token", lambda **kw: "unit-session")
    monkeypatch.setattr(account_session, "is_signed_in_locally", lambda *a: True)
    monkeypatch.setattr(database_limit, "_signed_in", lambda: True)
    return answer


def test_declined_holds_no_slot_and_says_why(consent, monkeypatch, tmp_path):
    claim = Mock(side_effect=AssertionError("claimed a slot with AI off"))
    monkeypatch.setattr(database_limit, "claim", claim)
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    assert database_limit.claim_target(store, "db") == "local"
    assert database_limit.display_state(store, "db") == "local"
    assert database_limit.ai_reason(store, "db")["reason"] == "declined"
    assert database_limit.paused(store, "db", NOW)
    claim.assert_not_called()


def test_the_decision_applies_to_every_monitored_database_at_once(consent, monkeypatch,
                                                                  tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "on")
    monkeypatch.setattr(database_limit, "_target_config", lambda target: {"host": "unit"})
    monkeypatch.setattr(database_limit, "fingerprint", lambda config: "fp")
    # The Key Service doesn't answer: the database waits to retry the release.
    answers = [database_limit._NoAnswer("unreachable"), (200, {"released": True})]
    request = Mock(side_effect=lambda *a, **kw: answers.pop(0))
    monkeypatch.setattr(database_limit, "_request", request)
    database_limit.apply_ai_consent(store)
    assert database_limit.ai_state(store, "db") == "pending"
    assert database_limit.ai_reason(store, "db")["reason"] == "declined"
    database_limit.refresh_claims(store, {"db"}, force=True)
    assert database_limit.ai_state(store, "db") == "local"
    assert request.call_count == 2
    # Released once, it isn't asked again.
    database_limit.refresh_claims(store, {"db"}, force=True)
    assert request.call_count == 2
    consent[0] = "granted"
    monkeypatch.setattr(database_limit, "claim", lambda config, label: {"state": "on"})
    database_limit.apply_ai_consent(store)
    assert database_limit.ai_state(store, "db") == "on"
    assert database_limit.ai_reason(store, "db")["reason"] is None


@pytest.mark.asyncio
async def test_monitoring_records_but_never_calls_ai(consent, monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: NOW)
    store.set_settings("db", {"enabled": True, "deep_analysis": True, "nightly_report": True,
                              "nightly_hour": 0})
    store.start_first_hour("db", NOW - 30)
    database_limit.set_ai_state(store, "db", "local", "declined")
    service = FakeService()
    queue = AnalyzeQueue(store, service=service, clock=lambda: NOW)
    jev = ScriptedJev({"q": ("notify", "latency_regression", .8)})
    attention = Mock(wraps=jev.attention)
    jev.attention = attention
    clock = Clock(NOW)
    loop = loop_for(monkeypatch, tmp_path, store, clock, jev)
    for tick in range(3):
        add_query(store, "one", 100, now=NOW + tick * 60)
        series(store, "q", now=clock.now, before_calls=100, after_calls=100,
               before_ms=10, after_ms=14)
        loop.cycle("db")
        await queue.tick()
        clock.now += 60
    assert service.calls == []
    attention.assert_not_called()
    assert len(store.read("SELECT * FROM monitor_pulse WHERE kind='collect'")) >= 3
    outcomes = {row["outcome"] for row in store.read("SELECT outcome FROM judge_run")}
    assert "error" not in outcomes
    jobs = store.analyze_jobs("db")
    plan = queue_plan(store, "db", NOW)
    assert jobs and all(plan["jobs"][job["id"]]["held_reason"] == "ai_off" for job in jobs)
    assert waiting_reason(plan, jobs[0]["id"]) == (
        "AI is off. Turn on AI in Settings to run deep analysis.")


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["nightly", "user"])
async def test_reports_are_written_without_ai(consent, monkeypatch, tmp_path, trigger):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: REPORT_NOW)
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "local", "declined")
    _seed(store)
    monkeypatch.setattr(report, "USER_WAIT_SECONDS", 0)
    narrate = Mock(side_effect=AssertionError("report narrative called with AI off"))
    result = await report.run_report(
        "db", trigger, store=store, clock=lambda: REPORT_NOW,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}), narrate=narrate)
    narrate.assert_not_called()
    row = store.report(result["id"])
    assert "AI_OFF" in str(row)
    # No deep analysis is queued or waited for, and the report doesn't wait on one either.
    assert store.analyze_jobs("db") == []
    assert not row["provisional"]


async def _publish_nothing(*args):
    pass


@pytest.mark.asyncio
async def test_query_classification_waits_untouched_and_resumes(consent, monkeypatch, tmp_path):
    monkeypatch.setattr(assessment, "is_signed_in_locally", lambda: True)
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
    worker.select_target("db")
    assert not await worker._process_one("db")
    provider.assert_not_called()
    library.claim_assessment.assert_not_called()
    library.set_target_assessment_state.assert_not_called()
    consent[0] = "granted"
    assert await worker._process_one("db")
    provider.assert_called_once()


def test_first_pass_and_activity_say_ai_is_off(consent, monkeypatch, tmp_path):
    from features.monitor import actions
    from shared.agent_activity.recorder import judge_summary

    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(report, "first_pass", lambda *a: {
        "due": False, "analyzed": 0, "total": 3, "due_by": NOW + 3600})
    state = actions._checkup_state(store, "db", NOW)
    assert state["held"] == "ai_paused" and state["ai_reason"] == "declined"
    assert state["held_text"].startswith("AI is off")
    assert judge_summary("ai_off", 0, False) == ("skipped", "AI is off, nothing checked")


def test_consent_is_read_once_until_it_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ai_data_consent, "_cached", None)
    loads = Mock()
    original = ai_data_consent.TargetsConfig.load
    monkeypatch.setattr(ai_data_consent.TargetsConfig, "load",
                        lambda self: (loads(), original(self))[1])
    assert ai_data_consent.ai_data_consent() == "pending"
    assert ai_data_consent.ai_data_consent() == "pending"
    assert loads.call_count == 1
    ai_data_consent.set_ai_data_consent("granted")
    assert ai_data_consent.ai_data_consent() == "granted"


def test_turning_ai_off_settles_its_inbox_items_and_waits_to_release(consent, monkeypatch,
                                                                     tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "pending", "signed_out")
    store.upsert_item("db", "monitoring_issue", database_limit.SUBJECT, 1.0,
                      database_limit.TITLE, {"target": "db"}, NOW)
    monkeypatch.setattr(database_limit, "_target_config", lambda target: {"host": "unit"})
    monkeypatch.setattr(database_limit, "fingerprint", lambda config: "fp")
    request = Mock(return_value=(200, {"released": True}))
    monkeypatch.setattr(database_limit, "_request", request)
    signed_in = [False]
    monkeypatch.setattr(database_limit, "_signed_in", lambda: signed_in[0])
    # Signed out, the slot can't be released yet; nothing is sent until a sign-in.
    assert database_limit.claim_target(store, "db") == "pending"
    request.assert_not_called()
    open_items = store.read("SELECT * FROM inbox_item WHERE state!='resolved'")
    assert open_items == []
    signed_in[0] = True
    assert database_limit.claim_target(store, "db") == "local"
    request.assert_called_once()


def test_the_last_claim_records_the_latest_choice(consent, monkeypatch, tmp_path):
    import threading

    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    consent[0] = "granted"
    claiming = threading.Event()
    finish = threading.Event()

    def slow_claim(config, label):
        claiming.set()
        finish.wait(5)
        return {"state": "on"}

    monkeypatch.setattr(database_limit, "claim", slow_claim)
    monkeypatch.setattr(database_limit, "_request", lambda *a, **kw: (200, {}))
    monkeypatch.setattr(database_limit, "_target_config", lambda target: {"host": "unit"})
    monkeypatch.setattr(database_limit, "fingerprint", lambda config: "fp")
    refresh = threading.Thread(target=database_limit.claim_target, args=(store, "db"))
    refresh.start()
    claiming.wait(5)
    # AI is turned off while the refresh's claim is in flight.
    consent[0] = "denied"
    applied = threading.Thread(target=database_limit.apply_ai_consent, args=(store,))
    applied.start()
    finish.set()
    refresh.join(5)
    applied.join(5)
    assert database_limit.ai_state(store, "db") == "local"
    assert database_limit.ai_reason(store, "db")["reason"] == "declined"


def test_a_hosted_request_waiting_its_turn_stops_once_ai_is_off(consent):
    from shared.llm_manager.base import LLMError, ProviderRequest
    from shared.llm_manager.hosted_provider import HostedProvider

    request = ProviderRequest(model="unit", messages=[{"role": "user", "content": "SELECT 1"}])
    with pytest.raises(LLMError) as error:
        HostedProvider().complete(request, api_key="unit")
    assert error.value.code == "AI_DATA_CONSENT_REQUIRED"


def test_withdrawn_diagnostics_drop_what_posthog_queued():
    from queue import Queue

    from shared.telemetry_manager import _discard_queued_posthog

    lane = SimpleNamespace(queue=Queue())
    for event in ("a", "b"):
        lane.queue.put(event)
    _discard_queued_posthog(SimpleNamespace(_lanes=[lane]))
    assert lane.queue.empty()
    legacy = SimpleNamespace(queue=Queue())
    legacy.queue.put("c")
    _discard_queued_posthog(legacy)
    assert legacy.queue.empty()


def test_turned_back_on_reads_as_starting_until_claimed(consent, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.set_settings("db", {"enabled": True})
    database_limit.set_ai_state(store, "db", "local", "declined")
    consent[0] = "granted"
    assert database_limit.display_state(store, "db") == "pending"
    assert database_limit.ai_reason(store, "db")["reason"] is None


def test_coding_agents_get_nothing_while_ai_is_off(consent):
    import asyncio

    from fastapi import FastAPI

    from features.agent_connect import tools

    result = asyncio.run(tools.call_tool(FastAPI(), next(iter(tools.TOOLS)), {}))
    assert result["isError"] and result["content"][0]["text"] == tools.AGENTS_NEED_AI


def test_retries_after_a_token_refresh_check_ai_again(consent, monkeypatch):
    from shared.llm_manager import jev_client
    from shared.llm_manager.jev_client import JevClient, JevClientError

    consent[0] = "granted"
    posts = []

    class Response:
        status_code = 401

        def json(self):
            return {"code": "UNAUTHORIZED"}

    def post(*args, **kwargs):
        posts.append(1)
        consent[0] = "denied"
        return Response()

    client = JevClient()
    monkeypatch.setattr(client, "_session", SimpleNamespace(post=post))
    monkeypatch.setattr(client, "_session_token", lambda force_refresh=False: "token")
    monkeypatch.setattr(jev_client, "keyservice_url", lambda path: "https://unit" + path)
    with pytest.raises(JevClientError) as error:
        client._send("/v1/jev/assess", {"q": 1})
    assert error.value.code == "AI_DATA_CONSENT_REQUIRED"
    assert posts == [1]


def test_another_process_withdrawing_diagnostics_stops_this_one(monkeypatch, tmp_path):
    from shared.telemetry_manager import TelemetryManager

    monkeypatch.setenv("RDST_CONSENT", "opt_in")
    monkeypatch.delenv("RDST_TESTING", raising=False)
    monkeypatch.delenv("RDST_TELEMETRY", raising=False)
    manager = TelemetryManager()
    manager._rdst_dir = tmp_path
    manager.set_telemetry_consent("granted")
    assert manager.is_enabled()
    drained = Mock()
    monkeypatch.setattr(manager, "_disable_remote_clients", drained)
    other = TelemetryManager()
    other._rdst_dir = tmp_path
    other.set_telemetry_consent("denied")
    assert not manager.is_enabled()
    drained.assert_called_once()


def test_no_key_check_goes_to_anthropic_with_ai_off(consent, monkeypatch):
    from shared import anthropic_env
    import shared.llm_manager as llm

    anthropic_env.clear_anthropic_validity_cache()
    monkeypatch.setattr(llm, "LLMManager", Mock(side_effect=AssertionError("pinged Anthropic")))
    assert anthropic_env.check_anthropic_key("sk-ant-api03-" + "a" * 40) == {
        "valid": False, "reason": "provider_error", "model": None}


@pytest.mark.asyncio
async def test_schema_descriptions_wait_for_ai(consent, monkeypatch):
    from features.schema import annotate_service
    from tests.unit.test_bootstrap_service import FakeAnnotate, _opts, _service, _stages

    validator = Mock(side_effect=AssertionError("checked the key with AI off"))
    monkeypatch.setattr(annotate_service, "validate_anthropic_key", validator)
    events = [event async for event in annotate_service.AnnotateService().annotate("db", {})]
    assert [event.type for event in events] == ["annotate_error"]
    assert "AI is off" in events[0].message

    annotate = FakeAnnotate()
    service = _service(annotate=annotate, validator=validator)
    events = [event async for event in service.run("db", {"engine": "postgresql"}, _opts())]
    assert _stages(events, "annotate")[-1] == ("skipped", "AI is off")
    assert annotate.calls == 0


@pytest.mark.asyncio
async def test_an_ai_off_report_promises_no_retry(consent, monkeypatch, tmp_path):
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: REPORT_NOW)
    store.set_settings("db", {"enabled": True})
    _seed(store)
    monkeypatch.setattr(report, "USER_WAIT_SECONDS", 0)
    result = await report.run_report(
        "db", "user", store=store, clock=lambda: REPORT_NOW,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}), narrate=Mock())
    body = json.loads(store.report(result["id"])["report_json"])
    assert body["narrative_error"].startswith("AI_OFF")
    assert body["narrative_retry_at"] is None
    assert "Turn on AI" in body["summary"]


@pytest.mark.asyncio
async def test_a_key_saved_with_ai_off_is_not_checked(consent, monkeypatch):
    from shared.api.routes import env

    saved = {}

    class Service:
        secret_store = SimpleNamespace(
            set_secret=lambda **kw: saved.update(kw) or {"persisted": True, "session_only": False})

        def get_allowed_secret_names(self):
            return ["ANTHROPIC_API_KEY"]

        def bind_missing_target_password(self, name):
            pass

        def set_ai_provider(self, provider):
            pass

    monkeypatch.setattr(env, "EnvRequirementsService", Service)
    monkeypatch.setattr(env, "require_local_request", lambda request: None)
    monkeypatch.setattr(env, "check_anthropic_key", Mock(side_effect=AssertionError("checked")))
    key = "sk-ant-api03-" + "a" * 40
    response = await env.set_env_secret(
        None, env.EnvSetRequest(name="ANTHROPIC_API_KEY", value=key))
    assert response.success and saved["value"] == key
    assert response.message == env.KEY_SAVED_AI_OFF_MESSAGE


def test_the_setup_check_sends_nothing_with_ai_off(consent, monkeypatch):
    from features.init import service

    monkeypatch.setattr(service, "create_llm_manager", Mock(side_effect=AssertionError("pinged")))
    monkeypatch.setattr(service, "get_anthropic_source", lambda *a, **kw: "secure_store")
    monkeypatch.setattr(service, "is_signed_in_locally", lambda *a: False)
    monkeypatch.setattr(service.InitService, "_ensure_llm_provider_for_anthropic",
                        lambda self, cfg: None)
    cfg = SimpleNamespace(get_llm_config=lambda: {"provider": "claude"})
    result = service.InitService().check_llm(cfg)
    assert result["success"] is False and "AI is off" in result["error"]


@pytest.mark.asyncio
async def test_ai_turned_off_mid_report_waits_for_ai(consent, monkeypatch, tmp_path):
    from shared.llm_manager import LLMError

    consent[0] = "granted"
    store = MonitorStore(tmp_path / "monitor.db", clock=lambda: REPORT_NOW)
    store.set_settings("db", {"enabled": True})
    _seed(store)
    monkeypatch.setattr(report, "USER_WAIT_SECONDS", 0)

    def withdrawn(context):
        consent[0] = "denied"
        raise LLMError("AI data sharing requires explicit permission in RDST Settings.",
                       code="AI_DATA_CONSENT_REQUIRED")

    result = await report.run_report(
        "db", "user", store=store, clock=lambda: REPORT_NOW,
        explain=lambda target, sql: {"plan": ["Seq Scan"]},
        collect=lambda target: ({"section_errors": {}}, {}), narrate=withdrawn)
    body = json.loads(store.report(result["id"])["report_json"])
    assert body["narrative_error"].startswith("AI_OFF")
    assert body["narrative_retry_at"] is None
    assert "Turn on AI" in body["summary"]
