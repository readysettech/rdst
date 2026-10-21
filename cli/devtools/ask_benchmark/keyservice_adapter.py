"""Benchmark transport that runs Ask's model calls through the hosted-inference path.

The product's own `LLMManager` and `HostedProvider` build every request exactly as they do
for a signed-in RDST user. An in-process stand-in for Keyservice answers those requests
with Keyservice's own code: `prompt_policy.admit` renders the registered system prompt,
`hosted_inference.openrouter_payload` builds the OpenRouter request for the route, the
upstream loop follows Keyservice's retry, downgrade and widening rules, and
`parse_openrouter_response` reads the reply. Ask then parses the returned text with its
own tolerant parser, as it does in production.

The benchmark spec selects the route: its model must be one of Keyservice's routes, and
every request is served on that route. Call records note the route Keyservice would
choose for the request so long-prompt routing differences stay visible.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from features.ask import execution_error_repair, result_check
from shared.llm_manager import hosted_provider
from shared.llm_manager.key_resolution import KeyResolution
from shared.llm_manager.llm_manager import LLMManager

from .models import ModelCallRecord, ModelSpec

KEYSERVICE_TRANSPORT = "keyservice"
KEYSERVICE_SRC = Path(__file__).resolve().parents[2] / "keyservice" / "src"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Keyservice derives the OpenRouter user and session IDs from these; any fixed values work.
_BENCHMARK_USER_ID = "rdst-ask-benchmark"
_BENCHMARK_IDENTITY_SECRET = "rdst-ask-benchmark"
# OpenRouter reports provider display names; Keyservice routes pin provider slugs.
_PROVIDER_SLUGS = {"google": "google-vertex", "gmi-cloud": "gmicloud"}


def load_keyservice_module(name: str) -> ModuleType:
    """Import a Keyservice source module under the bare name the Worker gives it."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, KEYSERVICE_SRC / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Keyservice module {name} is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def _call_stage(operation: str, turns: list[dict[str, Any]]) -> str:
    """The stage a call record names: the hosted operation, or one of Ask's repairs.

    Ask sends its database-error and result-check repairs as validation repairs, so their
    feedback tells them apart.
    """
    if operation != "sql_validation_repair":
        return operation
    feedback = " ".join(
        str(turn.get("content")) for turn in turns if turn.get("role") == "user"
    )
    if execution_error_repair.FEEDBACK_PREFIX in feedback:
        return "sql_execution_repair"
    if result_check.FEEDBACK_PREFIX in feedback:
        return "sql_result_check_repair"
    return operation


def keyservice_route(spec: ModelSpec) -> Any:
    """The Keyservice route a benchmark spec names, checked against its pinned settings."""
    hosted_inference = load_keyservice_module("hosted_inference")
    route = next(
        (route for route in hosted_inference.ROUTES if route.model == spec.model), None
    )
    if route is None:
        raise ValueError(f"{spec.name} names no Keyservice route: {spec.model}")
    if spec.reasoning_effort != route.reasoning_effort:
        raise ValueError(
            f"{spec.name} must use the route's {route.reasoning_effort} reasoning effort"
        )
    if spec.provider_order:
        raise ValueError(f"{spec.name} takes its providers from the Keyservice route")
    return route


class KeyserviceAdapter:
    """Benchmark adapter whose calls take the production hosted-inference path."""

    def __init__(
        self,
        default_spec: ModelSpec,
        *,
        upstream: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        api_key: str | None = None,
    ):
        # Benchmark calls are not product usage; keep them out of RDST telemetry.
        os.environ["RDST_TELEMETRY"] = "false"
        self.default_spec = default_spec
        self.route = keyservice_route(default_spec)
        self.call_records: list[ModelCallRecord] = []
        self.call_receipt_sink: Callable[[ModelCallRecord], None] | None = None
        self._attempt_id: str | None = None
        self._base_url = f"http://keyservice-{uuid.uuid4().hex}.benchmark.invalid"
        stand_in = _KeyserviceStandIn(
            self,
            upstream=upstream or requests.Session(),
            sleep=sleep,
            api_key=api_key,
        )
        # HostedProvider posts through its module session; the mount covers only this
        # adapter's unresolvable host, so every other request is untouched.
        hosted_provider._HTTP_SESSION.mount(self._base_url + "/", stand_in)
        self._manager = _StandInLLMManager(self._base_url)

    def set_attempt_id(self, attempt_id: str | None) -> None:
        self._attempt_id = attempt_id

    def set_call_receipt_sink(
        self, sink: Callable[[ModelCallRecord], None] | None
    ) -> None:
        self.call_receipt_sink = sink

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return self._manager.query(**kwargs)

    def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        return self._manager.generate_response(prompt, model, **kwargs)

    def drain_call_records(self) -> list[ModelCallRecord]:
        records = self.call_records
        self.call_records = []
        return records

    def _capture(self, record: ModelCallRecord) -> None:
        self.call_records.append(record)
        if self.call_receipt_sink:
            self.call_receipt_sink(record)


class _StandInLLMManager(LLMManager):
    """The product LLM manager, resolved to Readyset-hosted inference at the stand-in."""

    def __init__(self, base_url: str):
        super().__init__()
        self._stand_in_url = base_url

    def _safe_load_key_for_query(self, provider: str) -> KeyResolution:
        del provider
        return KeyResolution(
            api_key="rdst-ask-benchmark",
            is_trial=False,
            provider="readyset",
            proxy_url=self._stand_in_url,
        )


class _KeyserviceStandIn(BaseAdapter):
    """Answer `/v1/inference` with Keyservice's admission, routing and upstream rules."""

    def __init__(
        self,
        adapter: KeyserviceAdapter,
        *,
        upstream: requests.Session,
        sleep: Callable[[float], None],
        api_key: str | None,
    ):
        super().__init__()
        self._adapter = adapter
        self._upstream = upstream
        self._sleep = sleep
        self._api_key = api_key
        self._hosted = load_keyservice_module("hosted_inference")
        # prompt_policy imports the registry by its bare module name.
        load_keyservice_module("prompt_registry")
        self._policy = load_keyservice_module("prompt_policy")

    def send(self, request, **kwargs):
        del kwargs
        raw = request.body or b""
        raw = raw.encode("utf-8") if isinstance(raw, str) else raw
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            status, payload = 400, {"code": "INVALID_REQUEST", "detail": "Invalid JSON body"}
        else:
            status, payload = self._handle(body, len(raw))
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode("utf-8")
        response.headers = CaseInsensitiveDict({"Content-Type": "application/json"})
        response.encoding = "utf-8"
        response.url = request.url
        response.request = request
        return response

    def close(self) -> None:
        self._upstream.close()

    def _handle(self, body: Any, raw_size: int) -> tuple[int, dict[str, Any]]:
        hosted = self._hosted
        rejected = hosted.validate_request(body, raw_size)
        if rejected:
            payload, status = rejected
            return status, payload
        try:
            admitted = self._policy.admit(body)
        except self._policy.PolicyError as exc:
            # Production would refuse this prompt; the benchmark must not measure past it.
            self._record(
                stage=str((body.get("attribution") or {}).get("operation") or "other"),
                payload=None,
                attempts=[],
                started_at=_now_iso(),
                started=time.perf_counter(),
                error=f"Keyservice prompt policy refused the request: {exc.reason}",
                error_kind="internal",
            )
            code, status = self._policy.rejection_response(exc.reason)
            return status, {"code": code, "reason": exc.reason, "detail": exc.detail}
        stage = _call_stage(admitted.operation, body.get("messages") or [])
        body["messages"] = admitted.messages
        body.pop("prompt", None)
        production_route = hosted.select_route(body)
        payload = hosted.openrouter_payload(
            body, _BENCHMARK_USER_ID, _BENCHMARK_IDENTITY_SECRET, self._adapter.route
        )
        return self._send_upstream(
            payload,
            stage=stage,
            request_id=str(body["request_id"]),
            max_tokens=int(body.get("max_tokens") or 800),
            feature=self._policy.prompt_registry.OPERATIONS[admitted.operation][
                "feature"
            ],
            production_route_model=production_route.model,
        )

    def _send_upstream(
        self,
        payload: dict[str, Any],
        *,
        stage: str,
        request_id: str,
        max_tokens: int,
        feature: str,
        production_route_model: str,
    ) -> tuple[int, dict[str, Any]]:
        hosted = self._hosted
        started_at = _now_iso()
        started = time.perf_counter()
        deadline = (
            time.monotonic()
            + hosted.request_budget_seconds(max_tokens, feature)
            - hosted.DEADLINE_FINALIZATION_SECONDS
        )
        record = {
            "stage": stage,
            "payload": payload,
            "started_at": started_at,
            "started": started,
            "production_route_model": production_route_model,
        }
        api_key = self._api_key or os.environ["OPENROUTER_API_KEY"]
        attempts: list[dict[str, Any]] = []
        response = None
        data = None
        for attempt in range(hosted.UPSTREAM_MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining < hosted.UPSTREAM_MIN_ATTEMPT_SECONDS:
                if response is not None:
                    break
                self._record(**record, attempts=attempts, error="deadline before request",
                             error_kind="timeout")
                return 504, {"code": "HOSTED_DEADLINE_EXCEEDED",
                             "detail": "Hosted inference ran out of time"}
            attempt_started = time.monotonic()
            try:
                response = self._upstream.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "X-Title": "RDST Ask Benchmark",
                    },
                    data=hosted.encode_json(payload).encode("utf-8"),
                    timeout=remaining,
                )
            except requests.RequestException as exc:
                timed_out = isinstance(exc, requests.Timeout)
                attempts.append({
                    "category": "deadline_exceeded" if timed_out else "transport_error",
                    "latency_ms": _elapsed_ms(attempt_started),
                })
                self._record(**record, attempts=attempts, error=str(exc),
                             error_kind="timeout" if timed_out else "transport")
                code = "HOSTED_DEADLINE_EXCEEDED" if timed_out else "UPSTREAM_ERROR"
                return (504 if timed_out else 502), {
                    "code": code, "detail": "Hosted inference returned an uncertain result",
                }
            try:
                data = response.json()
            except ValueError:
                data = None
            diagnostics = hosted.upstream_attempt_diagnostics(
                response.status_code, data, response.headers
            )
            diagnostics["latency_ms"] = _elapsed_ms(attempt_started)
            attempts.append(diagnostics)
            if hosted.downgrade_structured_output(payload, response.status_code, data):
                diagnostics["category"] = "structured_output_downgrade"
                continue
            if hosted.widen_structured_output(payload, response.status_code):
                diagnostics["widened_to"] = "json_object"
                continue
            if not hosted.should_retry_upstream(response.status_code, attempt):
                break
            delay = hosted.upstream_retry_delay(
                attempt, response.headers.get("retry-after"), request_id
            )
            if delay + hosted.UPSTREAM_MIN_ATTEMPT_SECONDS >= deadline - time.monotonic():
                break
            self._sleep(delay)

        status = response.status_code
        if not 200 <= status < 300:
            self._record(**record, attempts=attempts, data=data,
                         error=f"OpenRouter returned HTTP {status}",
                         error_kind=_upstream_error_kind(status))
            if status == 429:
                return 429, {"code": "HOSTED_UPSTREAM_BUSY",
                             "detail": "The included AI is temporarily busy. Try again."}
            return (502 if status >= 500 or status in (401, 403) else status), {
                "code": "UPSTREAM_ERROR",
                "detail": "The included AI could not process the request",
            }
        try:
            result = hosted.parse_openrouter_response(data, payload["model"])
        except ValueError as exc:
            self._record(**record, attempts=attempts, data=data, error=str(exc),
                         error_kind="model_output")
            return 502, {"code": "UPSTREAM_ERROR",
                         "detail": "OpenRouter returned an unusable response"}
        self._record(**record, attempts=attempts, data=data, result=result)
        return 200, {
            "id": result["openrouter_id"] or request_id,
            "model": payload["model"],
            "text": result["text"],
            "provider": result["upstream_provider"],
            "usage": {
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "reasoning_tokens": result["reasoning_tokens"],
                "cost_microusd": result["cost_microusd"],
                "prompt_tokens_details": result["prompt_tokens_details"],
            },
        }

    def _record(
        self,
        *,
        stage: str,
        payload: dict[str, Any] | None,
        attempts: list[dict[str, Any]],
        started_at: str,
        started: float,
        production_route_model: str | None = None,
        data: Any = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        adapter = self._adapter
        spec = adapter.default_spec
        route = adapter.route
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("completion_tokens_details")
        reasoning_tokens = int(
            (details.get("reasoning_tokens") if isinstance(details, dict) else 0) or 0
        )
        prompt_details = usage.get("prompt_tokens_details")
        cached_tokens = int(
            (prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else 0)
            or 0
        )
        route_error = None
        if result is not None:
            provider = _provider_slug(result["upstream_provider"])
            if result["model"] != route.model:
                route_error = f"Keyservice route returned model {result['model']!r}"
            elif provider not in route.providers:
                route_error = (
                    f"Keyservice route returned provider {result['upstream_provider']!r}; "
                    f"expected one of {', '.join(route.providers)}"
                )
        settings = {
            "schema_version": 1,
            "configuration_name": spec.name,
            "stage": stage,
            "transport": KEYSERVICE_TRANSPORT,
            "route_model": route.model,
            "route_providers": list(route.providers),
            "reasoning_effort": route.reasoning_effort,
            "production_route_model": production_route_model,
            "upstream_max_tokens": (payload or {}).get("max_tokens"),
            "temperature": (payload or {}).get("temperature"),
            "response_format": ((payload or {}).get("response_format") or {}).get("type"),
            "upstream_attempts": attempts,
        }
        fingerprint_settings = {
            key: value for key, value in settings.items() if key != "upstream_attempts"
        }
        record = ModelCallRecord(
            stage=stage,
            transport=KEYSERVICE_TRANSPORT,
            requested_model=route.model,
            returned_model=result["model"] if result else None,
            upstream_provider=_provider_slug(result["upstream_provider"]) if result else None,
            request_id=(result or {}).get("openrouter_id") or None,
            prompt_sha256=_sha256(
                {
                    "model": (payload or {}).get("model"),
                    "messages": (payload or {}).get("messages"),
                    "response_format": (payload or {}).get("response_format"),
                }
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cached_input_tokens=cached_tokens,
            latency_ms=_elapsed_ms(started, clock=time.perf_counter),
            transport_attempts=len(attempts),
            actual_cost_usd=(
                Decimal(str(result["cost_usd"])) if result is not None else None
            ),
            normalized_cold_cost_usd=spec.pricing.cold_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
            ),
            expected_billed_cost_usd=spec.pricing.expected_billed_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                cached_input_tokens=cached_tokens,
            ),
            response_sha256=_sha256(result["text"]) if result else None,
            model_name=spec.name,
            benchmark_model_name=spec.name,
            attempt_id=adapter._attempt_id,
            effective_settings=settings,
            configuration_fingerprint=_sha256(fingerprint_settings),
            started_at=started_at,
            completed_at=_now_iso(),
            error=route_error or error,
            error_kind="route" if route_error else error_kind,
            route_error=route_error,
        )
        adapter._capture(record)


def _upstream_error_kind(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status in {408, 504}:
        return "timeout"
    if status >= 500:
        return "provider_5xx"
    if status in {401, 402, 403}:
        # Account, credit and key failures say nothing about the product.
        return "transport"
    return "route_policy"


def _provider_slug(value: Any) -> str:
    slug = str(value).strip().lower().replace(" ", "-")
    return _PROVIDER_SLUGS.get(slug, slug)


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _elapsed_ms(started: float, *, clock: Callable[[], float] = time.monotonic) -> float:
    return round((clock() - started) * 1000, 1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
