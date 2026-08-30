from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from shared.llm_manager.base import LLMError

from .models import ModelCallRecord, ModelSpec
from .pydantic_adapter import (
    BenchmarkModelOutputError,
    BenchmarkTransportError,
    RouteMismatchError,
)

CLAUDE_SUBSCRIPTION_TRANSPORT = "claude-subscription"
CLAUDE_SUBSCRIPTION_MODEL = "claude-sonnet-4-6"
CLAUDE_SUBSCRIPTION_EFFORT = "medium"
PINNED_CLAUDE_CODE_VERSION = "2.1.250"

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

_CONTEXT_WINDOW_ERROR_PATTERNS = (
    re.compile(r"\bprompt is too long\b", re.IGNORECASE),
    re.compile(r"\bcontext window\b.*\bexceed", re.IGNORECASE),
    re.compile(r"\bexceed.*\bcontext window\b", re.IGNORECASE),
    re.compile(r"\binput tokens?\b.*\b(?:exceed|maximum|limit)\b", re.IGNORECASE),
)


class ClaudeSubscriptionAdapter:
    """Benchmark adapter for a Claude subscription through the Claude Code CLI."""

    propagate_query_errors = True

    def __init__(
        self,
        default_spec: ModelSpec,
        *,
        model_aliases: dict[str, ModelSpec] | None = None,
        oauth_token: str | None = None,
        claude_path: str | None = None,
        command_runner: CommandRunner = subprocess.run,
        cli_version: str | None = None,
        call_receipt_sink: Callable[[ModelCallRecord], None] | None = None,
        working_directory: Path | None = None,
    ):
        if default_spec.transport != CLAUDE_SUBSCRIPTION_TRANSPORT:
            raise ValueError(
                "ClaudeSubscriptionAdapter requires a claude-subscription model spec"
            )
        if default_spec.model != CLAUDE_SUBSCRIPTION_MODEL:
            raise ValueError(
                "Claude subscription benchmarks are pinned to claude-sonnet-4-6"
            )
        if default_spec.reasoning_effort != CLAUDE_SUBSCRIPTION_EFFORT:
            raise ValueError(
                "Claude subscription benchmarks require explicit medium effort"
            )
        self.default_spec = default_spec
        self.model_aliases = model_aliases or {}
        self.oauth_token = oauth_token or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
        if not self.oauth_token:
            raise ValueError(
                "CLAUDE_CODE_OAUTH_TOKEN is required for subscription benchmark calls"
            )
        self.claude_path = claude_path or shutil.which("claude")
        if not self.claude_path:
            raise ValueError("Claude Code CLI is required for subscription benchmarks")
        self.command_runner = command_runner
        self.cli_version = cli_version or self._load_cli_version()
        if self.cli_version != PINNED_CLAUDE_CODE_VERSION:
            raise ValueError(
                "Claude Code CLI version mismatch: "
                f"expected {PINNED_CLAUDE_CODE_VERSION}, got {self.cli_version}"
            )
        self.call_receipt_sink = call_receipt_sink
        self.call_records: list[ModelCallRecord] = []
        self._attempt_id: str | None = None
        self.working_directory = working_directory or Path(tempfile.gettempdir())

    def set_attempt_id(self, attempt_id: str | None) -> None:
        self._attempt_id = attempt_id

    def set_call_receipt_sink(
        self, sink: Callable[[ModelCallRecord], None] | None
    ) -> None:
        self.call_receipt_sink = sink

    def query(
        self,
        *,
        system_message: str,
        user_query: str,
        context: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        stop_sequences: Sequence[str] | None = None,
        provider: str | None = None,
        model: str | None = None,
        debug: bool | None = None,
        api_key: str | None = None,
        purpose: str | None = None,
        extra: dict[str, Any] | None = None,
        history: Sequence[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        del provider, debug, api_key
        spec = self._resolve_spec(model)
        stage = purpose or "general"
        effective_max_tokens = max_tokens or spec.max_tokens or 800
        requested_settings = {
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stop_sequences": list(stop_sequences or ()),
        }
        effective_settings = {
            "schema_version": 1,
            "stage": stage,
            "transport": CLAUDE_SUBSCRIPTION_TRANSPORT,
            "model": spec.model,
            "claude_code_version": self.cli_version,
            "max_tokens": effective_max_tokens,
            "max_turns": 1,
            "reasoning_effort": CLAUDE_SUBSCRIPTION_EFFORT,
            "reasoning_effort_source": "cli_flag_and_environment",
            "extended_thinking_enabled": True,
            "cli_retries": 0,
            "nonessential_traffic_disabled": True,
            "safe_mode": True,
            "tools": [],
            "session_persistence": False,
            "structured_output": _requests_json(extra),
            "billing": "claude_subscription_credit",
            "request_id_kind": "claude_code_session_id",
            "omitted_requested_settings": _omitted_settings(requested_settings),
        }
        effective_system = _system_prompt(system_message, extra)
        effective_prompt = _user_prompt(user_query, context, history)
        configuration_fingerprint = _hash(effective_settings)
        prompt_sha256 = _hash(
            {
                "system_message": effective_system,
                "user_prompt": effective_prompt,
                "settings": effective_settings,
            }
        )
        started_at = _now_iso()
        started = perf_counter()
        response: dict[str, Any] | None = None
        error: Exception | None = None
        try:
            completed = self.command_runner(
                self._command(spec, effective_system),
                input=effective_prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=spec.timeout_seconds,
                cwd=self.working_directory,
                env=self._environment(effective_max_tokens),
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip()
                if _is_context_window_error(message):
                    raise LLMError(
                        f"Claude Code could not fit the request in the model context "
                        f"window: {message}",
                        code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                        status=400,
                    )
                raise BenchmarkTransportError(
                    f"Claude Code exited {completed.returncode}: {message or 'no output'}"
                )
            payload = _load_payload(completed.stdout)
            response = self._response_from_payload(payload, spec)
            return response
        except subprocess.TimeoutExpired as exc:
            error = BenchmarkTransportError(
                f"Claude Code exceeded the {spec.timeout_seconds:g}s timeout"
            )
            raise error from exc
        except OSError as exc:
            error = BenchmarkTransportError(f"Unable to execute Claude Code: {exc}")
            raise error from exc
        except Exception as exc:
            error = exc
            raise
        finally:
            record = self._record(
                spec=spec,
                stage=stage,
                prompt_sha256=prompt_sha256,
                requested_settings=requested_settings,
                effective_settings=effective_settings,
                configuration_fingerprint=configuration_fingerprint,
                started_at=started_at,
                latency_ms=(perf_counter() - started) * 1000,
                response=response,
                error=error,
            )
            self._capture(record)

    def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        callback = kwargs.pop("callback", None)
        result = self.query(
            system_message=kwargs.pop("system_message", "You are a helpful assistant."),
            user_query=prompt,
            model=model,
            **kwargs,
        )
        response = {
            "response": result["text"],
            "tokens_used": result["usage"]["total_tokens"],
            "usage": result["usage"],
            "model": result["model"],
        }
        if callback:
            callback(
                prompt=prompt,
                response=response["response"],
                tokens=response["tokens_used"],
                model=response["model"],
            )
        return response

    def drain_call_records(self) -> list[ModelCallRecord]:
        records = self.call_records
        self.call_records = []
        return records

    def _load_cli_version(self) -> str:
        try:
            completed = self.command_runner(
                [self.claude_path, "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
                env=self._environment(128),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Unable to inspect Claude Code CLI: {exc}") from exc
        if completed.returncode != 0:
            raise ValueError(
                "Unable to inspect Claude Code CLI: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        version = completed.stdout.strip().split(" ", 1)[0]
        if not version:
            raise ValueError("Claude Code CLI returned an empty version")
        return version

    def _resolve_spec(self, requested_model: str | None) -> ModelSpec:
        if requested_model is None or requested_model == self.default_spec.model:
            return self.default_spec
        try:
            spec = self.model_aliases[requested_model]
        except KeyError as exc:
            raise ValueError(
                f"Model override {requested_model!r} has no pinned evaluation mapping"
            ) from exc
        if (
            spec.transport != CLAUDE_SUBSCRIPTION_TRANSPORT
            or spec.model != CLAUDE_SUBSCRIPTION_MODEL
            or spec.reasoning_effort != CLAUDE_SUBSCRIPTION_EFFORT
        ):
            raise RouteMismatchError(
                f"Model override {requested_model!r} is not pinned to "
                f"{CLAUDE_SUBSCRIPTION_MODEL} through the subscription transport"
            )
        return spec

    def _command(self, spec: ModelSpec, system_prompt: str) -> list[str]:
        return [
            self.claude_path,
            "-p",
            "--safe-mode",
            "--strict-mcp-config",
            "--tools",
            "",
            "--disable-slash-commands",
            "--prompt-suggestions",
            "false",
            "--no-chrome",
            "--model",
            spec.model,
            "--effort",
            CLAUDE_SUBSCRIPTION_EFFORT,
            "--max-turns",
            "1",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--system-prompt",
            system_prompt,
        ]

    def _environment(self, max_tokens: int) -> dict[str, str]:
        env = os.environ.copy()
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_FOUNDRY",
            "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING",
            "CLAUDE_CODE_DISABLE_THINKING",
            "CLAUDE_CODE_EFFORT_LEVEL",
            "MAX_THINKING_TOKENS",
        ):
            env.pop(name, None)
        env.update(
            {
                "CLAUDE_CODE_OAUTH_TOKEN": self.oauth_token,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP": "1",
                "CLAUDE_CODE_EFFORT_LEVEL": CLAUDE_SUBSCRIPTION_EFFORT,
                "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_tokens),
                "CLAUDE_CODE_MAX_RETRIES": "0",
                "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
                "DISABLE_AUTOUPDATER": "1",
                "DISABLE_TELEMETRY": "1",
            }
        )
        return env

    def _response_from_payload(
        self, payload: dict[str, Any], spec: ModelSpec
    ) -> dict[str, Any]:
        if payload.get("is_error") is True or payload.get("subtype") != "success":
            detail = str(
                payload.get("result") or payload.get("api_error_status") or payload
            )
            if _is_context_window_error(detail):
                session_id = payload.get("session_id")
                request_id = (
                    f"claude-code-session:{session_id}"
                    if isinstance(session_id, str) and session_id
                    else None
                )
                raise LLMError(
                    "Claude Code could not fit the request in the model context "
                    f"window: {detail}",
                    code="ANTHROPIC_CONTEXT_WINDOW_EXCEEDED",
                    status=400,
                    request_id=request_id,
                )
            raise BenchmarkTransportError(detail)
        if payload.get("num_turns") != 1:
            raise RouteMismatchError(
                f"Claude Code used {payload.get('num_turns')!r} turns; expected exactly 1"
            )
        if payload.get("terminal_reason") != "completed":
            raise BenchmarkTransportError(
                f"Claude Code terminal reason was {payload.get('terminal_reason')!r}"
            )
        model_usage = payload.get("modelUsage")
        if not isinstance(model_usage, dict) or not model_usage:
            raise RouteMismatchError("Claude Code response omitted modelUsage")
        observed_models = set(model_usage)
        if observed_models != {spec.model}:
            raise RouteMismatchError(
                "Claude Code used models "
                f"{sorted(observed_models)!r}; expected only {spec.model!r}"
            )
        usage = model_usage.get(spec.model)
        if not isinstance(usage, dict):
            raise RouteMismatchError(
                f"Claude Code response omitted usage for {spec.model!r}"
            )
        canonical_model = usage.get("canonicalModel")
        provider = usage.get("provider")
        if canonical_model != spec.model or provider != "firstParty":
            raise RouteMismatchError(
                "Claude Code returned route "
                f"model={canonical_model!r}, provider={provider!r}; expected "
                f"model={spec.model!r}, provider='firstParty'"
            )
        session_id = payload.get("session_id")
        result_uuid = payload.get("uuid")
        if not isinstance(session_id, str) or not session_id:
            raise BenchmarkModelOutputError("Claude Code response omitted session_id")
        if not isinstance(result_uuid, str) or not result_uuid:
            raise BenchmarkModelOutputError("Claude Code response omitted result uuid")
        top_level_usage = payload.get("usage") or {}
        output_details = top_level_usage.get("output_tokens_details") or {}
        if not isinstance(output_details, dict):
            raise BenchmarkModelOutputError(
                "Claude Code returned invalid output_tokens_details"
            )
        thinking_tokens = _integer(output_details, "thinking_tokens")
        text = payload.get("result")
        if not isinstance(text, str) or not text.strip():
            raise BenchmarkModelOutputError("Claude Code returned an empty result")
        input_tokens = _integer(usage, "inputTokens")
        output_tokens = _integer(usage, "outputTokens")
        cached_input_tokens = _integer(usage, "cacheReadInputTokens")
        return {
            "text": text,
            "model": spec.model,
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "cache_read_input_tokens": cached_input_tokens,
                "reasoning_tokens": thinking_tokens,
            },
            "raw": {
                "model": spec.model,
                "provider": provider,
                "claude_code_session_id": session_id,
                "claude_code_result_uuid": result_uuid,
                "claude_code_version": self.cli_version,
                "service_tier": payload.get("service_tier"),
                "stop_reason": payload.get("stop_reason"),
                "terminal_reason": payload.get("terminal_reason"),
                "model_usage": model_usage,
                "normalized_cli_cost_usd": payload.get("total_cost_usd"),
            },
        }

    def _record(
        self,
        *,
        spec: ModelSpec,
        stage: str,
        prompt_sha256: str,
        requested_settings: dict[str, Any],
        effective_settings: dict[str, Any],
        configuration_fingerprint: str,
        started_at: str,
        latency_ms: float,
        response: dict[str, Any] | None,
        error: Exception | None,
    ) -> ModelCallRecord:
        usage = (response or {}).get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        cached_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
        reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
        raw = (response or {}).get("raw") or {}
        response_text = (response or {}).get("text")
        session_id = raw.get("claude_code_session_id")
        request_id = f"claude-code-session:{session_id}" if session_id else None
        if request_id is None and isinstance(error, LLMError):
            request_id = error.request_id
        return ModelCallRecord(
            stage=stage,
            transport=CLAUDE_SUBSCRIPTION_TRANSPORT,
            requested_model=spec.model,
            returned_model=(response or {}).get("model"),
            upstream_provider="anthropic-claude-code",
            request_id=request_id,
            prompt_sha256=prompt_sha256,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=cached_input_tokens,
            latency_ms=latency_ms,
            transport_attempts=1,
            actual_cost_usd=None,
            normalized_cold_cost_usd=spec.pricing.cold_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
            expected_billed_cost_usd=Decimal(0),
            response_sha256=(
                hashlib.sha256(response_text.encode("utf-8")).hexdigest()
                if isinstance(response_text, str)
                else None
            ),
            model_name=spec.name,
            benchmark_model_name=self.default_spec.name,
            attempt_id=self._attempt_id,
            requested_settings=requested_settings,
            effective_settings={
                **effective_settings,
                "claude_code_receipt": {
                    "result_uuid": raw.get("claude_code_result_uuid"),
                    "service_tier": raw.get("service_tier"),
                    "stop_reason": raw.get("stop_reason"),
                    "terminal_reason": raw.get("terminal_reason"),
                    "model_usage": raw.get("model_usage"),
                    "normalized_cli_cost_usd": raw.get("normalized_cli_cost_usd"),
                },
            },
            configuration_fingerprint=configuration_fingerprint,
            started_at=started_at,
            completed_at=_now_iso(),
            error=str(error) if error else None,
            error_kind=_error_kind(error),
            route_error=str(error) if isinstance(error, RouteMismatchError) else None,
        )

    def _capture(self, record: ModelCallRecord) -> None:
        self.call_records.append(record)
        if self.call_receipt_sink:
            self.call_receipt_sink(record)


def _load_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkModelOutputError(
            f"Claude Code returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BenchmarkModelOutputError("Claude Code result wrapper must be an object")
    return payload


def _requests_json(extra: dict[str, Any] | None) -> bool:
    response_format = (extra or {}).get("response_format")
    return isinstance(response_format, dict) and response_format.get("type") in {
        "json_object",
        "json_schema",
    }


def _system_prompt(system_message: str, extra: dict[str, Any] | None) -> str:
    response_format = (extra or {}).get("response_format")
    if not isinstance(response_format, dict):
        return system_message
    if response_format.get("type") == "json_object":
        return f"{system_message}\n\nReturn exactly one valid JSON object and no other text."
    schema_config = response_format.get("json_schema")
    schema = schema_config.get("schema") if isinstance(schema_config, dict) else None
    if not isinstance(schema, dict):
        raise TypeError("json_schema response format requires an object schema")
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return (
        f"{system_message}\n\nReturn exactly one JSON object matching this schema "
        f"and no other text:\n{encoded}"
    )


def _user_prompt(
    user_query: str,
    context: str | None,
    history: Sequence[dict[str, str]] | None,
) -> str:
    parts: list[str] = []
    if context:
        parts.append(f"[CONTEXT]\n{context}\n[/CONTEXT]")
    if history:
        encoded_history = json.dumps(
            list(history), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        parts.append(
            f"[CONVERSATION_HISTORY]\n{encoded_history}\n[/CONVERSATION_HISTORY]"
        )
    parts.append(user_query)
    return "\n\n".join(parts)


def _omitted_settings(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "value": value,
            "reason": "unsupported_by_claude_code_subscription_transport",
        }
        for name, value in settings.items()
        if name in {"temperature", "top_p", "stop_sequences"}
        and value not in (None, [])
    }


def _integer(value: dict[str, Any], key: str) -> int:
    result = value.get(key, 0)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise BenchmarkModelOutputError(
            f"Claude Code returned invalid {key}: {result!r}"
        )
    return result


def _error_kind(error: Exception | None) -> str | None:
    if error is None:
        return None
    if isinstance(error, RouteMismatchError):
        return "route_mismatch"
    if isinstance(error, BenchmarkTransportError):
        return "transport"
    if isinstance(error, BenchmarkModelOutputError):
        return "model_output"
    if isinstance(error, LLMError):
        return error.code.lower()
    return "internal"


def _is_context_window_error(message: str) -> bool:
    return any(pattern.search(message) for pattern in _CONTEXT_WINDOW_ERROR_PATTERNS)


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
