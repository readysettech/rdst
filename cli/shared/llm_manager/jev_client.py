"""Bounded client for RDST's fixed Jev Keyservice endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import math
import time
from typing import Any, Literal, TypedDict

import requests

from shared import account_session
from shared.keyservice import keyservice_url

MAX_REQUEST_BYTES = 384 * 1024
REQUEST_TIMEOUT_SECONDS = 45
ATTENTION_RUBRIC_VERSION = "query-attention-v1"
ACTION_RUBRIC_VERSION = "action-priority-v1"
ATTENTION_KINDS = {
    "latency_regression", "plan_change", "new_heavy_query", "traffic_spike",
    "query_stopped_running", "table_slowdown", "stuck_query",
    "rollback_or_deadlock_spike", "cache_hit_ratio_drop", "slow_creep",
    "query_improved",
}


class AttentionQuery(TypedDict):
    query_key: str
    attention: Literal["ignore", "inbox", "notify"]
    kind: str
    severity: float


class AttentionItem(TypedDict):
    id: str
    status: Literal["still", "improving", "resolved"]


class AttentionResult(TypedDict):
    request_id: str
    model: str
    rubric_version: str
    event_scope: Literal["single", "database_wide", "separate"]
    queries: list[AttentionQuery]
    open_items: list[AttentionItem]
    usage: dict[str, int]


@dataclass
class JevClientError(RuntimeError):
    code: str
    status: int
    retry_after: float | None = None

    def __str__(self) -> str:
        return self.code


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


class JevClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    @staticmethod
    def _session_token(*, force_refresh: bool = False) -> str | None:
        try:
            return account_session.access_token(force_refresh=force_refresh)
        except account_session.AccountSessionError as exc:
            code = ("JEV_SERVICE_UNAVAILABLE" if isinstance(exc.__cause__, requests.RequestException)
                    else "LOGIN_REQUIRED")
            raise JevClientError(code, 503 if code == "JEV_SERVICE_UNAVAILABLE" else 401) from exc

    def assess(
        self,
        *,
        request_id: str,
        rubric_version: str,
        sql: str,
        dialect: str,
        evidence: dict[str, Any],
        input_fingerprint: str,
    ) -> dict[str, Any]:
        from shared.llm_manager.inference_attribution import monitor_database_headers

        operation = "monitor_classification" if monitor_database_headers() else "query_registry_analysis"
        payload = {
            "request_id": request_id,
            "rubric_version": rubric_version,
            "normalized_sql": sql,
            "dialect": dialect,
            "evidence": evidence,
            "input_fingerprint": input_fingerprint,
            "attribution": {"surface": "web", "operation": operation},
        }
        return self._post("/v1/jev/assess", payload)

    def attention(
        self,
        *,
        request_id: str,
        target_numbers: dict[str, Any],
        index_changes: list[dict[str, Any]],
        open_items: list[dict[str, Any]],
        queries: list[dict[str, Any]],
    ) -> AttentionResult:
        payload = {
            "request_id": request_id,
            "rubric_version": ATTENTION_RUBRIC_VERSION,
            "target_numbers": target_numbers,
            "index_changes": index_changes,
            "open_items": open_items,
            "queries": queries,
            "attribution": {"surface": "web", "operation": "monitor_attention"},
        }
        result = self._post("/v1/jev/attention", payload)
        if (
            result.get("request_id") != request_id
            or result.get("model") != "jev-1.13.0"
            or result.get("rubric_version") != ATTENTION_RUBRIC_VERSION
            or result.get("event_scope") not in {"single", "database_wide", "separate"}
            or not isinstance(result.get("queries"), list)
            or not isinstance(result.get("open_items"), list)
            or len(result["queries"]) != len(queries)
            or len(result["open_items"]) != len(open_items)
            or not isinstance(result.get("usage"), dict)
            or any(
                type(result["usage"].get(key)) is not int or result["usage"][key] < 0
                for key in ("input_tokens", "output_tokens")
            )
        ):
            raise JevClientError("JEV_INVALID_RESPONSE", 502)
        for supplied, answer in zip(queries, result["queries"]):
            if (
                not isinstance(answer, dict)
                or answer.get("query_key") != supplied.get("query_key")
                or answer.get("attention") not in {"ignore", "inbox", "notify"}
                or answer.get("kind") not in ATTENTION_KINDS
                or type(answer.get("severity")) not in (int, float)
                or not math.isfinite(answer["severity"])
                or not 0 <= answer["severity"] <= 1
            ):
                raise JevClientError("JEV_INVALID_RESPONSE", 502)
        for supplied, answer in zip(open_items, result["open_items"]):
            if (
                not isinstance(answer, dict)
                or answer.get("id") != supplied.get("id")
                or answer.get("status") not in {"still", "improving", "resolved"}
            ):
                raise JevClientError("JEV_INVALID_RESPONSE", 502)
        return result

    def action_priority(
        self,
        *,
        request_id: str,
        target_numbers: dict[str, Any],
        actions: list[dict[str, Any]],
        recent_changes: list[str],
    ) -> dict[str, Any]:
        """How urgent each open action is now, and whether its evidence went stale."""
        payload = {
            "request_id": request_id,
            "rubric_version": ACTION_RUBRIC_VERSION,
            "target_numbers": target_numbers,
            "actions": actions,
            "recent_changes": recent_changes,
            "attribution": {"surface": "web", "operation": "monitor_actions"},
        }
        result = self._post("/v1/jev/actions", payload)
        answers = result.get("actions")
        if (
            result.get("request_id") != request_id
            or result.get("rubric_version") != ACTION_RUBRIC_VERSION
            or not isinstance(answers, list)
            or len(answers) != len(actions)
        ):
            raise JevClientError("JEV_INVALID_RESPONSE", 502)
        for supplied, answer in zip(actions, answers):
            if (
                not isinstance(answer, dict)
                or answer.get("id") != supplied.get("id")
                or answer.get("relevance") not in {"relevant", "stale"}
                or type(answer.get("priority")) not in (int, float)
                or not 0 <= answer["priority"] <= 1
                or type(answer.get("stale_probability")) not in (int, float)
            ):
                raise JevClientError("JEV_INVALID_RESPONSE", 502)
        return result

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        from shared.agent_activity import record_jev_call

        started = time.monotonic()
        try:
            body = self._send(path, payload)
        except JevClientError as exc:
            record_jev_call(path, payload, None, started, error=exc.code)
            raise
        record_jev_call(path, payload, body, started)
        return body

    def _send(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._session_token()
        if not token:
            raise JevClientError("LOGIN_REQUIRED", 401)
        try:
            encoded = json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise JevClientError("INVALID_REQUEST", 422) from exc
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise JevClientError("JEV_CONTEXT_TOO_LARGE", 413)
        from shared.llm_manager.inference_attribution import monitor_database_headers

        response = None
        for attempt in range(2):
            try:
                response = self._session.post(
                    keyservice_url(path),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        **monitor_database_headers(),
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise JevClientError("JEV_SERVICE_UNAVAILABLE", 503) from exc
            if response.status_code != 401 or attempt:
                break
            try:
                if response.json().get("code") != "UNAUTHORIZED":
                    break
            except (ValueError, AttributeError):
                break
            refreshed = self._session_token(force_refresh=True)
            if not refreshed:
                break
            token = refreshed

        assert response is not None
        if response.status_code == 200:
            from shared.llm_manager.inference_attribution import record_billable_call

            record_billable_call()
        try:
            body = response.json()
        except ValueError as exc:
            if response.status_code == 401:
                raise JevClientError("LOGIN_REQUIRED", 401) from exc
            raise JevClientError("JEV_INVALID_RESPONSE", response.status_code) from exc
        if response.status_code != 200:
            code = str(body.get("code") or "JEV_SERVICE_UNAVAILABLE") if isinstance(body, dict) else "JEV_SERVICE_UNAVAILABLE"
            if response.status_code == 401:
                code = "LOGIN_REQUIRED"
            from shared import product_telemetry

            product_telemetry.refusal(code, feature="jev")
            raise JevClientError(
                code,
                response.status_code,
                _retry_after(response.headers.get("Retry-After")),
            )
        if not isinstance(body, dict):
            raise JevClientError("JEV_INVALID_RESPONSE", 502)
        return body
