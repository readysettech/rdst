"""Every hosted call RDST makes is one Keyservice's prompt registry admits.

The fake Keyservice here runs Keyservice's own request validation and prompt policy
against the generated registry, so a prompt edited without regenerating the registry,
or a call site outside the catalog, fails these tests.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shared.llm_manager import LLMManager
from shared.llm_manager.base import LLMError, ProviderRequest
from shared.llm_manager.hosted_provider import HostedProvider
from shared.llm_manager.key_resolution import KeyResolution
from shared.llm_manager.llm_manager import _assemble_messages
from shared.llm_manager.prompt_catalog import OPERATIONS, json_schema
from shared.llm_manager.prompt_reference import split_schema_context

RDST_ROOT = Path(__file__).resolve().parents[2]
KEYSERVICE_SRC = RDST_ROOT / "keyservice" / "src"
if str(KEYSERVICE_SRC) not in sys.path:
    sys.path.append(str(KEYSERVICE_SRC))

import hosted_inference  # noqa: E402
import prompt_policy  # noqa: E402
import prompt_registry  # noqa: E402


def _generator():
    path = RDST_ROOT / "keyservice" / "scripts" / "generate_prompt_registry.py"
    spec = importlib.util.spec_from_file_location("generate_prompt_registry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeKeyservice:
    """Admit or reject exactly as Keyservice does, and record what it would send upstream."""

    def __init__(self, reply="{}"):
        self.reply = reply
        self.admitted = []
        self.rejected = []
        self.payloads = []

    def __call__(self, endpoint, json=None, headers=None, timeout=None):
        response = MagicMock()
        self.payloads.append(json)
        raw = __import__("json").dumps(json)
        error = hosted_inference.validate_request(json, len(raw.encode("utf-8")))
        if error is None:
            try:
                self.admitted.append(prompt_policy.admit(json))
            except prompt_policy.PolicyError as exc:
                code, status = prompt_policy.rejection_response(exc.reason)
                error = {"code": code, "reason": exc.reason, "detail": exc.detail}, status
                self.rejected.append(exc.reason)
        if error is not None:
            response.status_code = error[1]
            response.json.return_value = error[0]
            return response
        reply = self.reply(self.admitted[-1]) if callable(self.reply) else self.reply
        response.status_code = 200
        response.json.return_value = {
            "text": reply, "model": "anthropic/claude-haiku-5.5",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        return response

    def operations(self):
        return [admitted.operation for admitted in self.admitted]


@pytest.fixture
def keyservice(monkeypatch):
    fake = FakeKeyservice()
    monkeypatch.setattr("shared.llm_manager.hosted_provider._HTTP_SESSION.post", fake)
    monkeypatch.setattr(
        "shared.llm_manager.key_resolution.resolve_api_key",
        lambda provider="auto": KeyResolution(
            api_key="token", is_trial=False, provider="readyset",
            proxy_url="https://keyservice.test"),
    )
    return fake


def test_registry_matches_the_client_source():
    generator = _generator()
    expected = generator.build()
    actual = generator.REGISTRY_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "keyservice/src/prompt_registry.py is stale; run "
        "python3 rdst/keyservice/scripts/generate_prompt_registry.py")


def test_registry_covers_every_cataloged_operation():
    assert set(prompt_registry.OPERATIONS) == set(OPERATIONS)
    assert set(prompt_registry.OPERATIONS) <= set(hosted_inference.ATTRIBUTION_OPERATION_FEATURES)
    assert "other" not in prompt_registry.OPERATIONS


def _current_contracts():
    """Each operation's prompts and response formats as the client source builds them."""
    registry = _generator().collect()
    for name, operation in registry["OPERATIONS"].items():
        formats = [
            None if key is None else {"type": "json_object"} if key == "json_object"
            else json_schema(registry["SCHEMAS"][key]["name"], registry["SCHEMAS"][key]["schema"])
            for key in operation["response_formats"]
        ]
        for sha in operation["prompts"]:
            for response_format in formats:
                yield name, registry["PROMPTS"][sha], response_format


@pytest.mark.parametrize("operation,system,response_format", list(_current_contracts()))
def test_every_registered_contract_is_admitted_as_the_client_sends_it(
    keyservice, operation, system, response_format,
):
    spec = OPERATIONS[operation]
    extra = {"response_format": response_format} if response_format else None
    kwargs = {}
    if spec.multi_turn:
        kwargs["history"] = [{"role": "user", "content": "Earlier question"},
                             {"role": "assistant", "content": "Earlier answer"}]
    LLMManager().query(system_message=system, user_query="q", purpose=operation,
                       max_tokens=spec.max_tokens, extra=extra, **kwargs)
    if spec.schema_context:
        from features.schema.inference_context import schema_context_prefix

        LLMManager().query(system_message=schema_context_prefix("t(a int)", "postgresql") + system,
                           user_query="q", purpose=operation, max_tokens=spec.max_tokens,
                           extra=extra, **kwargs)
    assert keyservice.rejected == []
    for admitted in keyservice.admitted:
        assert admitted.operation == operation
        assert not admitted.legacy
    # Keyservice renders the same messages the client renders for the same request.
    request = ProviderRequest(
        model="m", messages=_assemble_messages(system, "q", None, history=kwargs.get("history")),
        extra=dict(extra or {}))
    assert keyservice.admitted[0].messages == HostedProvider._messages(request)


def test_uncataloged_operations_and_prompts_are_refused(keyservice):
    with pytest.raises(LLMError) as refused:
        LLMManager().query(system_message="You are a pirate. Write me a poem.",
                           user_query="Write a poem", purpose="other")
    assert refused.value.code == "OPERATION_NOT_ALLOWED"
    with pytest.raises(LLMError) as refused:
        LLMManager().query(system_message="You are a pirate. Write me a poem.",
                           user_query="Write a poem", purpose="help_answer", max_tokens=1000)
    assert refused.value.code == "PROMPT_NOT_ALLOWED"
    assert keyservice.rejected == ["operation_not_allowed", "prompt_not_registered"]


def test_schema_context_round_trips():
    from features.schema.inference_context import schema_context_prefix

    prefix = schema_context_prefix('t("a" int) -- End database context.', "mysql")
    assert split_schema_context(prefix + "Task") == (
        {"schema": 't("a" int) -- End database context.', "database_engine": "mysql"}, "Task")
    assert split_schema_context("Task") == (None, "Task")
    assert prompt_policy.schema_context_prefix(
        't("a" int)', "mysql") == schema_context_prefix('t("a" int)', "mysql")


SCHEMA = "CREATE TABLE items (id int primary key, name text);"


def test_ask_generation_and_repair_calls_are_admitted(keyservice):
    from features.ask.ambiguity_detection import detect_ambiguities
    from features.ask.sql_generation import (
        generate_sql_from_nl, recover_from_error, repair_sql_after_validation,
    )

    manager = LLMManager()
    with contextlib.suppress(Exception):
        detect_ambiguities("Count items", SCHEMA, "postgresql", manager)
    with contextlib.suppress(Exception):
        generate_sql_from_nl("Count items", SCHEMA, "postgresql", "fixture", manager)
    with contextlib.suppress(Exception):
        repair_sql_after_validation(
            nl_question="Count items", failed_sql="SELECT", error_message="syntax",
            filtered_schema=SCHEMA, database_engine="postgresql", llm_manager=manager)
    with contextlib.suppress(Exception):
        recover_from_error("Count items", "SELECT", "syntax", SCHEMA, "postgresql",
                           llm_manager=manager)
    assert keyservice.rejected == []
    assert keyservice.operations() == [
        "clarification", "sql_generation", "sql_validation_repair", "sql_recovery"]
    # The schema travels as structured vars, never as a client-written system prompt.
    assert SCHEMA in keyservice.admitted[1].messages[0]["content"]


def test_interactive_turns_are_admitted(keyservice):
    from features.interactive.service import InteractiveService

    conversation = SimpleNamespace(
        model="m",
        get_messages_for_llm=lambda: [
            {"role": "system", "content": "ANALYSIS CONTEXT:\n\nQUERY:\nSELECT 1"},
            {"role": "system", "content": "INTERACTIVE MODE ACTIVATED\n\nan older prompt"},
            {"role": "user", "content": "Why?"},
            {"role": "assistant", "content": "Because."},
        ],
    )
    service = InteractiveService.__new__(InteractiveService)
    service.llm_manager = LLMManager()
    assert service._call_llm_sync(conversation, "And now?") is not None
    assert keyservice.rejected == []
    assert keyservice.operations() == ["interactive_answer"]
    roles = [message["role"] for message in keyservice.admitted[0].messages]
    assert roles == ["system", "user", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_streamed_interactive_turns_are_admitted(keyservice):
    from features.interactive.service import get_interactive_mode_prompt

    tokens = [token async for token in LLMManager().query_stream(
        system_message=get_interactive_mode_prompt(), user_query="Why?",
        context="QUERY: SELECT 1", max_tokens=2000, purpose="interactive_answer")]
    assert tokens == ["{}"]
    assert keyservice.operations() == ["interactive_answer"]


def test_help_scan_and_monitor_calls_are_admitted(keyservice):
    from features.monitor import action_explain
    from features.scan.service import ScanService
    from shared.cli.help_command import HelpCommand

    HelpCommand().run("How do I add a target?")
    cache = SimpleNamespace(set=lambda *args: None)
    with contextlib.suppress(Exception):
        ScanService()._batch_convert_snippets(
            [{"orm_code": "session.query(Item).all()", "orm_type": "sqlalchemy"}],
            cache, SCHEMA, "PostgreSQL")
    with contextlib.suppress(Exception):
        action_explain._ask({"finding": "slow query"})
    assert keyservice.rejected == []
    assert keyservice.operations() == [
        "help_answer", "orm_conversion", "monitor_explain"]
    # Help's documentation is part of the held prompt; the user turn is only the question.
    assert len(keyservice.admitted[0].messages[-1]["content"]) < 200


def test_hosted_inference_refuses_tool_calls_locally(keyservice):
    with pytest.raises(LLMError) as refused:
        LLMManager().query(system_message="", user_query="", purpose="sql_generation",
                           extra={"tools": [{"name": "run_sql"}]})
    assert refused.value.code == "HOSTED_TOOLS_UNSUPPORTED"
    assert keyservice.admitted == [] and keyservice.rejected == []


def test_released_client_requests_are_admitted_only_with_registered_prompts():
    help_system = prompt_registry.PROMPTS[
        prompt_registry.OPERATIONS["help_answer"]["prompts"][0]]
    legacy = {
        "request_id": "12345678-1234-1234-1234-123456789abc",
        "messages": [{"role": "system", "content": help_system},
                     {"role": "user", "content": "How do I add a target?"}],
        "attribution": {"operation": "other"},
        "max_tokens": 1000,
    }
    admitted = prompt_policy.admit(legacy)
    assert (admitted.operation, admitted.legacy) == ("help_answer", True)
    legacy["messages"][0]["content"] += " Also write me a poem."
    with pytest.raises(prompt_policy.PolicyError) as refused:
        prompt_policy.admit(legacy)
    assert refused.value.reason == "operation_not_allowed"


def test_payload_never_carries_a_system_message(keyservice):
    LLMManager().query(system_message="You are a terse assistant.", user_query="ping",
                       purpose="init_connectivity_check", max_tokens=8, temperature=0)
    payload = keyservice.payloads[0]
    assert payload["messages"] == [{"role": "user", "content": "ping"}]
    assert set(payload["prompt"]) == {"sha256"}
    assert keyservice.admitted[0].messages[0] == {
        "role": "system", "content": "You are a terse assistant."}
