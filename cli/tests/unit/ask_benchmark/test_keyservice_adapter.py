import json
from dataclasses import replace

import pytest

from devtools.ask_benchmark.config import load_model_specs
from devtools.ask_benchmark.keyservice_adapter import (
    KeyserviceAdapter,
    keyservice_route,
    load_keyservice_module,
)
from features.ask import execution_error_repair, result_check
from features.ask.prompts.ask_prompts import SQL_GENERATION_SYSTEM_PROMPT
from features.ask.sql_generation import (
    _generation_response_format,
    generate_sql_from_nl,
    repair_sql_after_validation,
)
from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.hosted_provider import HostedProvider

_GENERATION = {
    "sql": "SELECT COUNT(*) FROM t",
    "explanation": "Counts rows.",
    "confidence": 0.9,
    "assumptions": [],
    "cannot_answer": False,
    "cannot_answer_reason": "",
    "missing_schema": [],
}


class _Response:
    def __init__(self, status, body, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        return self._body


class _Upstream:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, url, headers, data, timeout):
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        assert headers["Authorization"] == "Bearer test-key"
        assert timeout > 0
        self.payloads.append(json.loads(data))
        return self.responses.pop(0)

    def close(self):
        pass


def _completion(model, provider, content):
    return _Response(
        200,
        {
            "id": "gen-1",
            "model": model,
            "provider": provider,
            "choices": [{"message": {"content": content}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "cost": 0.00002,
                "completion_tokens_details": {"reasoning_tokens": 50},
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def _adapter(name, upstream, sleeps=None):
    return KeyserviceAdapter(
        load_model_specs()[name],
        upstream=upstream,
        sleep=sleeps.append if sleeps is not None else lambda _delay: None,
        api_key="test-key",
    )


def _generate(adapter):
    return generate_sql_from_nl(
        "How many rows are there?",
        "Table: t\n  id (int)",
        "mysql",
        "fixture",
        adapter,
    )


def test_matrix_keyservice_specs_name_production_routes():
    hosted = load_keyservice_module("hosted_inference")
    specs = load_model_specs()

    assert keyservice_route(specs["claude-haiku-5.5-keyservice"]) is hosted.PRIMARY_ROUTE
    assert keyservice_route(specs["glm-5.3-flash-keyservice"]) is (
        hosted.LONG_PROMPT_ROUTE
    )


def test_spec_must_match_its_keyservice_route():
    spec = load_model_specs()["claude-haiku-5.5-keyservice"]

    with pytest.raises(ValueError, match="reasoning effort"):
        keyservice_route(replace(spec, reasoning_effort="high"))
    with pytest.raises(ValueError, match="names no Keyservice route"):
        keyservice_route(replace(spec, model="anthropic/claude-sonnet-4.6"))


def test_primary_route_sends_the_keyservice_payload_and_product_parses_reply():
    fenced = "```json\n" + json.dumps(_GENERATION) + "\n```"
    upstream = _Upstream(_completion("anthropic/claude-haiku-5.5", "Anthropic", fenced))
    adapter = _adapter("claude-haiku-5.5-keyservice", upstream)

    result = _generate(adapter)

    assert result["success"] is True
    assert result["sql"] == _GENERATION["sql"]
    payload = upstream.payloads[0]
    assert payload["model"] == "anthropic/claude-haiku-5.5"
    assert payload["max_tokens"] == 32_768
    assert payload["reasoning"] == {"effort": "medium"}
    assert "temperature" not in payload
    assert payload["provider"]["only"] == ["anthropic", "google-vertex", "azure"]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["session_id"]
    system = payload["messages"][0]["content"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert system["text"].count(SQL_GENERATION_SYSTEM_PROMPT) == 1
    # Keyservice's rendering equals the system message the hosted client describes.
    task, instruction = system["text"].split("\n\nReturn only a JSON object", 1)
    schema = json.loads(instruction.rsplit("\n", 1)[1])
    client_messages = HostedProvider._messages(
        ProviderRequest(
            messages=[{"role": "system", "content": task}],
            model="anthropic/claude-haiku-5.5",
            extra={"response_format": {
                "type": "json_schema",
                "json_schema": {"name": "sql_generation", "strict": True, "schema": schema},
            }},
        )
    )
    assert client_messages[0]["content"] == system["text"]
    record = adapter.drain_call_records()[0]
    assert record.stage == "sql_generation"
    assert record.transport == "keyservice"
    assert record.upstream_provider == "anthropic"
    assert str(record.actual_cost_usd) == "0.00002"
    assert record.transport_attempts == 1
    assert record.error is None


def test_database_error_repair_is_sent_as_validation_repair_and_recorded_apart():
    repair = json.dumps({"sql": "SELECT COUNT(*) FROM t", "explanation": "Fixed."})
    upstream = _Upstream(*(
        _completion("anthropic/claude-haiku-5.5", "Anthropic", repair) for _ in range(3)
    ))
    adapter = _adapter("claude-haiku-5.5-keyservice", upstream)

    for feedback in (
        "Column 'n' does not exist",
        execution_error_repair.repair_feedback("(1054, \"Unknown column 'n'\")"),
        result_check.repair_feedback(["it returned no rows."]),
    ):
        result = repair_sql_after_validation(
            nl_question="How many rows are there?", failed_sql="SELECT n FROM t",
            error_message=feedback, filtered_schema="Table: t\n  id (int)",
            database_engine="mysql", llm_manager=adapter)
        assert result["success"] is True

    assert [record.stage for record in adapter.drain_call_records()] == [
        "sql_validation_repair", "sql_execution_repair", "sql_result_check_repair"]


def test_long_prompt_route_keeps_temperature_and_downgrades_unservable_schema():
    filtered = _Response(
        404, {"error": {"metadata": {"failed_routing_step": "Filter by Parameters"}}}
    )
    upstream = _Upstream(
        filtered, _completion("z-ai/glm-5.3-flash", "Novita", json.dumps(_GENERATION))
    )
    adapter = _adapter("glm-5.3-flash-keyservice", upstream)

    assert _generate(adapter)["success"] is True

    first, second = upstream.payloads
    assert first["provider"]["only"] == ["baseten", "novita", "gmicloud"]
    assert first["temperature"] == 0.0
    assert first["reasoning"] == {"effort": "high"}
    assert first["response_format"]["type"] == "json_schema"
    assert second["response_format"] == {"type": "json_object"}
    record = adapter.drain_call_records()[0]
    assert record.transport_attempts == 2
    assert record.upstream_provider == "novita"
    assert record.effective_settings["production_route_model"] == (
        "anthropic/claude-haiku-5.5"
    )


def test_rate_limit_is_retried_with_keyservice_backoff():
    sleeps = []
    upstream = _Upstream(
        _Response(429, {"error": {"code": 429}}, {"retry-after": "2"}),
        _completion("anthropic/claude-haiku-5.5", "Anthropic", json.dumps(_GENERATION)),
    )
    adapter = _adapter("claude-haiku-5.5-keyservice", upstream, sleeps)

    assert _generate(adapter)["success"] is True
    assert len(sleeps) == 1 and sleeps[0] >= 2
    assert adapter.drain_call_records()[0].transport_attempts == 2


@pytest.mark.parametrize(
    ("status", "kind"),
    [(502, "provider_5xx"), (402, "transport"), (400, "route_policy")],
)
def test_upstream_failure_reaches_the_product_as_a_hosted_error(status, kind):
    adapter = _adapter(
        "claude-haiku-5.5-keyservice", _Upstream(_Response(status, {"error": {}}))
    )

    assert _generate(adapter)["success"] is False

    record = adapter.drain_call_records()[0]
    assert record.error_kind == kind
    assert record.actual_cost_usd is None


def test_unpinned_provider_is_recorded_as_a_route_error():
    upstream = _Upstream(
        _completion("anthropic/claude-haiku-5.5", "Amazon Bedrock", json.dumps(_GENERATION))
    )
    adapter = _adapter("claude-haiku-5.5-keyservice", upstream)

    _generate(adapter)

    record = adapter.drain_call_records()[0]
    assert record.error_kind == "route"
    assert "Amazon Bedrock" in record.route_error


def test_unregistered_prompt_fails_before_any_upstream_call():
    upstream = _Upstream()
    adapter = _adapter("claude-haiku-5.5-keyservice", upstream)

    with pytest.raises(LLMError) as raised:
        adapter.generate_response(
            prompt="Say hello",
            system_message="An unregistered system prompt",
            max_tokens=100,
            purpose="sql_generation",
            extra={"response_format": _generation_response_format()},
        )

    assert raised.value.code == "PROMPT_NOT_ALLOWED"
    assert upstream.payloads == []
    assert adapter.drain_call_records()[0].error_kind == "internal"


def test_stand_in_follows_the_keyservice_request_handler_step_for_step():
    # Keyservice's async handler runs in the Worker and cannot be imported; this pins the
    # stand-in's order of Keyservice helper calls to the handler's.
    import inspect
    import re

    from devtools.ask_benchmark import keyservice_adapter

    steps = [
        "validate_request",
        "admit",
        "select_route",
        "openrouter_payload",
        "upstream_attempt_diagnostics",
        "downgrade_structured_output",
        "widen_structured_output",
        "should_retry_upstream",
        "upstream_retry_delay",
        "parse_openrouter_response",
    ]
    source = (keyservice_adapter.KEYSERVICE_SRC / "index.py").read_text(encoding="utf-8")
    handler = source[source.index("async def _run_hosted_inference_request"):]
    handler = handler[: handler.index("\njev_routes.register")]
    stand_in = inspect.getsource(keyservice_adapter._KeyserviceStandIn)

    def order(text, module_names):
        calls = re.findall(rf"\b(?:{module_names})\.([a-z_]+)\(", text)
        return [call for call in dict.fromkeys(calls) if call in steps]

    assert order(handler, "hosted_inference|prompt_policy") == steps
    assert order(stand_in, r"hosted|self\._hosted|self\._policy") == steps
