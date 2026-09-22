"""Bounded client for RDST's fixed Jev Keyservice endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import time
from typing import Any

import requests

from shared import account_session
from shared.keyservice import keyservice_url

MAX_REQUEST_BYTES = 384 * 1024
REQUEST_TIMEOUT_SECONDS = 45


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
        token = account_session.access_token()
        if not token:
            raise JevClientError("LOGIN_REQUIRED", 401)
        payload = {
            "request_id": request_id,
            "rubric_version": rubric_version,
            "normalized_sql": sql,
            "dialect": dialect,
            "evidence": evidence,
            "input_fingerprint": input_fingerprint,
            "attribution": {"surface": "web", "operation": "query_registry_analysis"},
        }
        response = None
        for attempt in range(2):
            try:
                response = self._session.post(
                    keyservice_url("/v1/jev/assess"),
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
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
            refreshed = account_session.access_token(force_refresh=True)
            if not refreshed:
                break
            token = refreshed

        assert response is not None
        try:
            body = response.json()
        except ValueError as exc:
            raise JevClientError("JEV_INVALID_RESPONSE", response.status_code) from exc
        if response.status_code != 200:
            code = str(body.get("code") or "JEV_SERVICE_UNAVAILABLE")
            raise JevClientError(
                code,
                response.status_code,
                _retry_after(response.headers.get("Retry-After")),
            )
        if not isinstance(body, dict):
            raise JevClientError("JEV_INVALID_RESPONSE", 502)
        return body
