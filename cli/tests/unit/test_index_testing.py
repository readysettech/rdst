"""Unit tests for hypopg planner verification of index recommendations."""

import json
from unittest import mock

import pytest

from features.analyze.functions import index_testing
from features.analyze.functions.index_testing import (
    SKIP_HYPOPG_NOT_INSTALLED,
    SKIP_NO_RECOMMENDATIONS,
    SKIP_PARAMETERIZED_QUERY,
    SKIP_UNSUPPORTED_ENGINE,
    _scan_using_index,
)
from features.analyze.functions.index_testing import test_index_recommendations as verify_indexes

PG_TARGET = {"engine": "postgresql", "host": "h", "port": 5432, "user": "u", "database": "d"}
RECS = [
    {"sql": "CREATE INDEX idx ON orders(customer_id)", "table": "orders", "columns": ["customer_id"]},
    {"sql": "CREATE INDEX CONCURRENTLY idx2 ON orders(channel)", "table": "orders", "columns": ["channel"]},
]


class FakeCursor:
    """Scripted psycopg2 cursor: answers hypopg and EXPLAIN calls deterministically."""

    def __init__(self, installed=True, available=True, used_by=("<1>btree_orders_customer_id",)):
        self.installed = installed
        self.available = available
        self.used_by = set(used_by)
        self.hypo = None
        self.executed = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "pg_extension" in sql:
            self._rows = [(1,)] if self.installed else []
        elif "pg_available_extensions" in sql:
            self._rows = [(1,)] if self.available else []
        elif "hypopg_reset" in sql:
            self.hypo = None
            self._rows = [(None,)]
        elif "hypopg_create_index" in sql:
            stmt = params[0]
            if "nope" in stmt:
                raise RuntimeError('hypopg: column "nope" does not exist')
            col = stmt.split("(")[-1].rstrip(")").strip()
            self.hypo = f"<1>btree_orders_{col}"
            self._rows = [(self.hypo,)]
        elif sql.startswith(index_testing._SELF_MARKER + "EXPLAIN"):
            if self.hypo and self.hypo in self.used_by:
                plan = {
                    "Node Type": "Bitmap Heap Scan",
                    "Total Cost": 35.8,
                    "Plans": [{"Node Type": "Bitmap Index Scan", "Index Name": self.hypo, "Total Cost": 4.1}],
                }
            else:
                plan = {"Node Type": "Seq Scan", "Total Cost": 49586.0}
            self._rows = [([{"Plan": plan}],)]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture
def connect(monkeypatch):
    holder = {}

    def _install(cursor):
        conn = FakeConn(cursor)
        holder["conn"] = conn

        def _resolve(**kw):
            conn.resolve_kwargs = kw
            return {**PG_TARGET, "password": "pw"}

        monkeypatch.setattr(index_testing, "psycopg2", mock.Mock(connect=mock.Mock(return_value=conn)))
        monkeypatch.setattr(index_testing, "resolve_connection_params", _resolve)
        monkeypatch.setattr(index_testing, "postgres_connection_kwargs", lambda p, **kw: dict(p))
        return conn

    return _install


def test_skips_without_recommendations():
    result = verify_indexes("SELECT 1", [], "t", target_config=PG_TARGET)
    assert result["tested"] is False
    assert result["skipped_reason"] == SKIP_NO_RECOMMENDATIONS


def test_skips_mysql():
    result = verify_indexes("SELECT 1", RECS, "t", target_config={"engine": "mysql"})
    assert result["skipped_reason"] == SKIP_UNSUPPORTED_ENGINE


def test_skips_parameterized_query():
    result = verify_indexes(
        "SELECT * FROM orders WHERE customer_id = $1", RECS, "t", target_config=PG_TARGET
    )
    assert result["skipped_reason"] == SKIP_PARAMETERIZED_QUERY


def test_reports_install_hint_when_hypopg_available_but_not_enabled(connect):
    conn = connect(FakeCursor(installed=False, available=True))
    result = verify_indexes("SELECT 1 FROM orders", RECS, "t", target_config=PG_TARGET)
    assert result["tested"] is False
    assert result["skipped_reason"] == SKIP_HYPOPG_NOT_INSTALLED
    assert "CREATE EXTENSION" in result["install_sql"]
    assert conn.closed


def test_reports_planner_verdicts(connect):
    connect(FakeCursor())
    result = verify_indexes(
        "SELECT * FROM orders WHERE customer_id = 42",
        json.dumps(RECS + [{"sql": "CREATE INDEX ON orders(nope)", "table": "orders", "columns": ["nope"]}]),
        "t",
        target_config=json.dumps(PG_TARGET),
    )
    assert result["tested"] is True
    used, unused, broken = result["results"]
    assert used["planner_used_index"] is True
    assert used["scan_type"] == "Bitmap Index Scan"
    assert used["cost_before"] == 49586.0 and used["cost_after"] == 35.8
    assert used["cost_reduction_pct"] == 99.9
    # CONCURRENTLY is stripped before handing the statement to hypopg.
    assert unused["index_sql"] == "CREATE INDEX idx2 ON orders(channel)"
    assert unused["planner_used_index"] is False and unused["cost_reduction_pct"] == 0.0
    assert broken["error"] == 'column "nope" does not exist'
    assert "1 of 3" in result["summary"]


def test_resets_hypothetical_indexes_after_each_recommendation(connect):
    conn = connect(FakeCursor())
    verify_indexes("SELECT * FROM orders", RECS, "t", target_config=PG_TARGET)
    resets = [sql for sql, _ in conn.cursor().executed if "hypopg_reset" in sql]
    assert len(resets) == 1 + len(RECS)


def test_explain_statements_carry_self_marker(connect):
    conn = connect(FakeCursor())
    verify_indexes("SELECT * FROM orders", RECS, "t", target_config=PG_TARGET)
    explains = [sql for sql, _ in conn.cursor().executed if "EXPLAIN" in sql]
    assert explains
    assert all(sql.startswith("/*rdst:analyze*/ EXPLAIN") for sql in explains)


def test_connection_carries_analyze_lane(connect):
    conn = connect(FakeCursor())
    verify_indexes("SELECT 1 FROM orders", RECS, "t", target_config=PG_TARGET)
    assert conn.resolve_kwargs["lane"] == "rdst/analyze"


def test_scan_using_index_walks_nested_plans():
    plan = {
        "Node Type": "Nested Loop",
        "Plans": [
            {"Node Type": "Seq Scan"},
            {"Node Type": "Index Scan", "Index Name": "<7>btree_x", "Plans": []},
        ],
    }
    assert _scan_using_index(plan, "<7>btree_x") == "Index Scan"
    assert _scan_using_index(plan, "<8>btree_y") is None
