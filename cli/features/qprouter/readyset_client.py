"""Client for the ReadySet cache engine behind SQP.

Two jobs: create/drop shallow caches (used by the manual-caching path, which runs
``CREATE CACHE`` directly against ReadySet and then routes the fingerprint at SQP),
and scrape ReadySet's Prometheus metrics for the aggregate cache hit rate — SQP
does not expose per-query hit/miss, so the hit-rate number comes from here.
"""

from __future__ import annotations

import re
from typing import Any

import requests


class ReadysetClient:
    def __init__(self, host: str, port: int, user: str = "sqp_user",
                 password: str = "sqp_pass", database: str = "sqp_test",
                 metrics_port: int | None = None, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.metrics_port = metrics_port
        self.timeout = timeout

    def _connect(self):
        import psycopg2

        conn = psycopg2.connect(
            host=self.host, port=self.port, user=self.user,
            password=self.password, dbname=self.database,
            connect_timeout=int(self.timeout),
        )
        conn.autocommit = True
        return conn

    def create_cache(self, sql: str, name: str | None = None) -> None:
        stmt = f"CREATE CACHE {name} FROM {sql}" if name else f"CREATE CACHE FROM {sql}"
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
        finally:
            conn.close()

    def drop_cache(self, name: str) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP CACHE {name}")
        finally:
            conn.close()

    def show_caches(self) -> list[tuple]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW CACHES")
                return cur.fetchall()
        finally:
            conn.close()

    def status(self) -> dict[str, str]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW READYSET STATUS")
                return {row[0].strip(): row[1].strip() for row in cur.fetchall()}
        finally:
            conn.close()

    # -------- metrics: aggregate cache hit rate from readyset_query_log_* --------
    def cache_metrics(self) -> dict[str, Any]:
        """Return {'keys_read', 'cache_misses', 'hit_rate'} scraped from ReadySet.

        hit_rate = 1 - cache_misses / keys_read. Requires ReadySet query logging +
        a reachable metrics port; returns an empty dict if unavailable.
        """
        if not self.metrics_port:
            return {}
        try:
            r = requests.get(f"http://{self.host}:{self.metrics_port}/metrics",
                            timeout=self.timeout)
            r.raise_for_status()
        except requests.RequestException:
            return {}
        keys_read = _sum_metric(r.text, "readyset_query_log_total_keys_read")
        misses = _sum_metric(r.text, "readyset_query_log_total_cache_misses")
        out: dict[str, Any] = {"keys_read": keys_read, "cache_misses": misses}
        if keys_read > 0:
            out["hit_rate"] = max(0.0, 1.0 - misses / keys_read)
        return out


def _sum_metric(prom_text: str, name: str) -> float:
    """Sum all samples of a Prometheus metric across label sets."""
    total = 0.0
    pat = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9eE.+-]+)$", re.M)
    for m in pat.finditer(prom_text):
        try:
            total += float(m.group(1))
        except ValueError:
            continue
    return total
