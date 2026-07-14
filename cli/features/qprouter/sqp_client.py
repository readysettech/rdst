"""HTTP client for the SQP admin API (:9091).

The demo keeps SQP's embedded QueryPilot plugin disabled. The standalone cron
binary only mutates core SQP resources, so this client intentionally uses the
stable `/api/stats/digests` and `/api/pattern-rules` surfaces.
"""

from __future__ import annotations

from typing import Any

import requests

# Some SQP builds accept this header on mutating endpoints; it is harmless for
# core routes and keeps compatibility with older demo images.
API_KEY_HEADER = "X-QueryPilot-Api-Key"


class SqpError(RuntimeError):
    """An SQP admin API call failed."""


def to_decimal(fingerprint: int | str) -> int:
    """Normalize a fingerprint (decimal int, decimal str, or ``0x`` hex str) to int."""
    if isinstance(fingerprint, int):
        return fingerprint
    text = fingerprint.strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def to_hex(fingerprint: int | str) -> str:
    """Normalize a fingerprint to the ``0x``-prefixed, upper-case, 16-digit hex form."""
    return f"0x{to_decimal(fingerprint):016X}"


class SqpAdminClient:
    """Thin, typed wrapper over the SQP admin + QueryPilot REST API."""

    def __init__(self, host: str, admin_port: int, api_key: str | None = None,
                 timeout: float = 5.0):
        self.base = f"http://{host}:{admin_port}"
        self.api_key = api_key
        self.timeout = timeout

    # ------------------------------------------------------------------ helpers
    def _headers(self, mutating: bool) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.api_key:
            # Sent on reads too so QueryPilot un-redacts candidate digest_text.
            h[API_KEY_HEADER] = self.api_key
        if mutating:
            h["Content-Type"] = "application/json"
        return h

    def _get(self, path: str) -> Any:
        try:
            r = requests.get(self.base + path, headers=self._headers(False), timeout=self.timeout)
        except requests.RequestException as e:
            raise SqpError(f"GET {path} failed: {e}") from e
        if r.status_code >= 400:
            raise SqpError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def _post(self, path: str, body: dict | None = None) -> Any:
        try:
            r = requests.post(self.base + path, json=body or {},
                              headers=self._headers(True), timeout=self.timeout)
        except requests.RequestException as e:
            raise SqpError(f"POST {path} failed: {e}") from e
        if r.status_code >= 400:
            raise SqpError(f"POST {path} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def _delete(self, path: str) -> None:
        try:
            r = requests.delete(self.base + path, headers=self._headers(True), timeout=self.timeout)
        except requests.RequestException as e:
            raise SqpError(f"DELETE {path} failed: {e}") from e
        if r.status_code >= 400:
            raise SqpError(f"DELETE {path} -> {r.status_code}: {r.text[:200]}")

    # -------------------------------------------------------------------- reads
    def pools(self) -> Any:
        return self._get("/api/pools")

    def digests(self, limit: int = 100, order_by: str = "sum_time",
                min_count: int | None = None) -> list[dict]:
        """Per-fingerprint traffic stats (fingerprint_hash is DECIMAL here)."""
        path = f"/api/stats/digests?limit={limit}&order_by={order_by}"
        if min_count is not None:
            path += f"&min_count={min_count}"
        data = self._get(path)
        return data.get("digests", []) if isinstance(data, dict) else []

    def pattern_rules(self) -> list[dict]:
        """All routing rules (fingerprint_hash DECIMAL; ``comment`` carries RuleMeta)."""
        data = self._get("/api/pattern-rules")
        return data.get("rules", []) if isinstance(data, dict) else []

    # --------------------------------------------------------------- mutations
    def add_manual_rule(self, fingerprint: int | str, target_pool: str = "readyset",
                        comment: str = '{"owner":"rdst-manual"}') -> Any:
        """Add an RDST-owned routing rule — the 'cached by you' path."""
        return self._post("/api/pattern-rules", {
            "fingerprint_hash": to_decimal(fingerprint),
            "target_pool": target_pool,
            "comment": comment,
        })

    def drop_pattern_rule(self, fingerprint: int | str) -> None:
        self._delete(f"/api/pattern-rules/{to_decimal(fingerprint)}")

    def drop_manual_rule(self, fingerprint: int | str) -> None:
        self.drop_pattern_rule(fingerprint)
