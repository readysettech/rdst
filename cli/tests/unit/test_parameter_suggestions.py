"""Unit tests for sampled parameter value suggestions (pure parts)."""

from unittest.mock import MagicMock

import features.analyze.parameter_suggestions as mod
from features.analyze.parameter_suggestions import (
    SOURCE_PG_STAT_ACTIVITY,
    align_sample_values,
    bind_placeholders_to_columns,
    enumerate_placeholders,
    suggest_parameter_values,
)

REGISTRY_HASH = "aabbccdd1122"


def test_enumerate_placeholders_uses_desktop_keys():
    keys = [p["key"] for p in enumerate_placeholders("SELECT * FROM t WHERE a = $1 AND b = ? AND c = :p1 AND d = $1")]
    assert keys == ["$1", "?1", ":p1"]


def test_question_marks_inside_strings_are_not_placeholders():
    keys = [p["key"] for p in enumerate_placeholders("SELECT * FROM t WHERE a = 'why?' AND b = ?")]
    assert keys == ["?1"]


def test_bind_placeholders_to_columns_resolves_aliases_and_shape():
    sql = (
        "SELECT o.* FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE c.email = $1 AND o.total_cents BETWEEN $2 AND $3 AND o.id IN ($4) LIMIT $5 OFFSET $6"
    )
    bindings = bind_placeholders_to_columns(sql, "postgresql", enumerate_placeholders(sql))
    assert bindings["$1"]["column"] == "customers.email"
    assert bindings["$2"]["column"] == "orders.total_cents"
    assert bindings["$3"]["column"] == "orders.total_cents"
    assert bindings["$4"]["column"] == "orders.id"
    assert bindings["$5"]["kind"] == "limit"
    assert bindings["$6"]["kind"] == "offset"


def test_mysql_question_marks_bind_in_textual_order():
    sql = "SELECT * FROM `place` WHERE NAME ->> ? = ? LIMIT ?"
    bindings = bind_placeholders_to_columns(sql, "mysql", enumerate_placeholders(sql))
    # The first `?` is the JSON path operand, never a column value to sample.
    assert bindings["?1"]["kind"] == "json_path"
    assert bindings["?3"]["kind"] == "limit"


def test_align_sample_values_by_literal_position():
    template = enumerate_placeholders("SELECT * FROM `place` WHERE NAME ->> ? = ? LIMIT ?")
    values = align_sample_values("SELECT * FROM place WHERE name->>'$.address.city' = 'Boston' LIMIT 5", template)
    assert values == {"?1": "$.address.city", "?2": "Boston", "?3": "5"}


def test_align_sample_values_rejects_mismatched_counts_and_bind_params():
    template = enumerate_placeholders("SELECT * FROM t WHERE a = $1 AND b IN ($2, $3)")
    assert align_sample_values("SELECT * FROM t WHERE a = 1 AND b IN (2, 3, 4)", template) is None
    assert align_sample_values("SELECT * FROM t WHERE a = $1 AND b IN ($2, $3)", template) is None


def test_column_sample_reads_carry_self_marker():
    from unittest.mock import MagicMock

    from features.analyze.parameter_suggestions import _sample_column_values

    binding = {"table": "orders", "column_name": "status"}

    pg_cur = MagicMock()
    pg_cur.fetchone.return_value = None  # no pg_stats row; fall to DISTINCT read
    pg_cur.fetchall.return_value = [("shipped",)]
    pg_conn = MagicMock()
    pg_conn.cursor.return_value = pg_cur
    _sample_column_values(pg_conn, "postgresql", binding)
    distinct = [c.args[0] for c in pg_cur.execute.call_args_list if "DISTINCT" in c.args[0]]
    assert distinct and distinct[0].startswith('/*rdst:analyze*/ SELECT DISTINCT "status"')

    my_cur = MagicMock()
    my_cur.fetchall.return_value = [("shipped",)]
    my_conn = MagicMock()
    my_conn.cursor.return_value = my_cur
    _sample_column_values(my_conn, "mysql", binding)
    sql = my_cur.execute.call_args.args[0]
    assert sql.startswith("/*rdst:analyze*/ SELECT /*+ MAX_EXECUTION_TIME(")


def test_connect_carries_analyze_lane(monkeypatch):
    import features.analyze.parameter_suggestions as mod

    captured = {}

    def fake_resolve(**kwargs):
        captured.update(kwargs)
        return {"resolved": True}

    monkeypatch.setattr(mod, "resolve_connection_params", fake_resolve)
    monkeypatch.setattr(mod, "create_mysql_connection_from_params", lambda resolved: "conn")
    assert mod._connect("mysql", "t", {"engine": "mysql"}) == "conn"
    assert captured["lane"] == "rdst/analyze"


class _FakeStore:
    """Minimal stand-in for the collector's observation store."""

    def __init__(self, epoch_id, aliases):
        self._epoch_id = epoch_id
        self._aliases = aliases

    def get_collector_state(self, target):
        return {"epoch_id": self._epoch_id} if self._epoch_id else {}

    def get_identity_aliases(self, target, epoch_id):
        return dict(self._aliases) if epoch_id == self._epoch_id else {}


def _use_store(monkeypatch, store):
    from features.query_registry.discovery import query_discovery

    monkeypatch.setattr(query_discovery, "existing_store", lambda: store)


def _pg_cursor(rows):
    cur = MagicMock()
    cur.fetchone.side_effect = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_numeric_hash_matches_both_signs_of_the_query_id():
    assert mod._pg_query_ids("-123", "demo") == [123, -123]
    assert mod._pg_query_ids("123", "demo") == [123, -123]


def test_unrecognized_hash_resolves_to_nothing():
    assert mod._pg_query_ids(None, "demo") == []
    assert mod._pg_query_ids("not-a-hash", "demo") == []


def test_registry_hash_resolves_through_the_collector_aliases(monkeypatch):
    _use_store(
        monkeypatch,
        _FakeStore(
            "epoch-1",
            {"10:20:987:t": REGISTRY_HASH, "10:20:5:t": "0011223344ff"},
        ),
    )

    assert mod._pg_query_ids(REGISTRY_HASH, "demo") == [987]


def test_registry_hash_without_a_collector_epoch_resolves_to_nothing(monkeypatch):
    _use_store(monkeypatch, _FakeStore("", {}))

    assert mod._pg_query_ids(REGISTRY_HASH, "demo") == []


def test_registry_hash_without_a_store_resolves_to_nothing(monkeypatch):
    _use_store(monkeypatch, None)

    assert mod._pg_query_ids(REGISTRY_HASH, "demo") == []


def test_fetch_sample_reads_activity_for_a_resolved_registry_hash(monkeypatch):
    _use_store(monkeypatch, _FakeStore("epoch-1", {"10:20:987:t": REGISTRY_HASH}))
    conn, cur = _pg_cursor(
        [(160000,), ("SELECT * FROM orders WHERE id = 7", "2026-08-20 10:00:00")]
    )

    sample = mod._fetch_sample(
        conn,
        "postgresql",
        "SELECT * FROM orders WHERE id = $1",
        REGISTRY_HASH,
        "demo",
    )

    assert sample["source"] == SOURCE_PG_STAT_ACTIVITY
    activity = [c for c in cur.execute.call_args_list if "pg_stat_activity" in c.args[0]]
    assert len(activity) == 1
    assert "query_id = ANY(%s)" in activity[0].args[0]
    assert activity[0].args[1] == ([987],)


def test_fetch_sample_skips_the_activity_lane_when_unresolvable(monkeypatch):
    _use_store(monkeypatch, _FakeStore("epoch-1", {}))
    conn, cur = _pg_cursor([])

    assert (
        mod._fetch_sample(
            conn,
            "postgresql",
            "SELECT * FROM orders WHERE id = $1",
            REGISTRY_HASH,
            "demo",
        )
        is None
    )
    assert cur.execute.call_args_list == []


def test_suggest_parameter_values_without_connection_still_gives_shape_hints(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("no db")

    monkeypatch.setattr(mod, "_connect", boom)
    result = suggest_parameter_values(
        "SELECT * FROM orders WHERE status = $1 LIMIT $2", "t", {"engine": "postgresql"}
    )
    by_key = {p["placeholder"]: p for p in result["placeholders"]}
    assert by_key["$1"]["column"] == "orders.status" and by_key["$1"]["suggestions"] == []
    assert by_key["$2"]["suggestions"] == [{"value": "10", "provenance": "Query shape (LIMIT)"}]
    assert result["sample"] is None
