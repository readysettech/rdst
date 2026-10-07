"""Unit tests for sampled parameter value suggestions (pure parts)."""

from unittest.mock import MagicMock

import pytest

import features.analyze.parameter_suggestions as mod
from features.analyze.parameter_suggestions import (
    SOURCE_PG_STAT_ACTIVITY,
    SOURCE_MYSQL_HISTORY,
    align_sample_values,
    bind_placeholders_to_columns,
    enumerate_placeholders,
    suggest_parameter_values,
)

REGISTRY_HASH = "aabbccdd1122"

LIVE_PG_CASES = [
    (
        "fc254baa1a61", -3036557254965183398,
        "SELECT c.country, count(DISTINCT o.id) orders, round(sum(oi.quantity * oi.unit_price), $1) revenue "
        "FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.placed_at > now() - interval $2 GROUP BY c.country ORDER BY revenue DESC",
        "SELECT c.country, count(DISTINCT o.id) orders, round(sum(oi.quantity * oi.unit_price), 2) revenue "
        "FROM customers c JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.placed_at > now() - interval '30 days' GROUP BY c.country ORDER BY revenue DESC",
        ["2", "30 days"],
    ),
    (
        "3b5e93f8e3f9", -4438643196628005202,
        "SELECT p.category, count(*) items, round(sum(oi.quantity * oi.unit_price), $1) revenue "
        "FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.category ORDER BY revenue DESC",
        "SELECT p.category, count(*) items, round(sum(oi.quantity * oi.unit_price), 2) revenue "
        "FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.category ORDER BY revenue DESC",
        ["2"],
    ),
    (
        "c802d84b564c", 4482622752852780022,
        "SELECT count(*) FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.total > $1 AND oi.quantity > $2",
        "SELECT count(*) FROM orders o JOIN order_items oi ON oi.order_id = o.id "
        "WHERE o.total > 100 AND oi.quantity > 2",
        ["100", "2"],
    ),
]


@pytest.mark.parametrize("key,query_id,template,instance,values", LIVE_PG_CASES)
def test_live_pg_instance_survives_after_activity_ends(
    tmp_path, monkeypatch, key, query_id, template, instance, values
):
    """Real shopdb texts and signed IDs: a burst sample must serve later jobs."""
    from features.monitor.analyze_queue import apply_suggestions
    from shared.monitor.monitor_store import MonitorStore

    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    MonitorStore(tmp_path / "monitor.db").ensure_open()
    _use_store(monkeypatch, _FakeStore("shopdb-epoch", {
        f"16477:16384:{query_id}:t": key,
    }))
    assert mod._pg_query_ids(key, "localdev") == [query_id]

    conn = MagicMock()
    monkeypatch.setattr(mod, "_connect", lambda *args: conn)
    monkeypatch.setattr(mod.analysis_lane, "register", lambda *args: object())
    monkeypatch.setattr(mod.analysis_lane, "StatementConnection", lambda raw, run: raw)
    monkeypatch.setattr(mod.analysis_lane, "unregister", lambda *args: None)
    monkeypatch.setattr(mod, "_sample_column_values", lambda *args: [])
    samples = iter([{"sql": instance, "source": SOURCE_PG_STAT_ACTIVITY}, None])
    monkeypatch.setattr(mod, "_fetch_sample", lambda *args: None)
    monkeypatch.setattr(mod, "_burst_pg_sample", lambda *args: next(samples))

    first = suggest_parameter_values(template, "localdev", {}, key)
    second = suggest_parameter_values(template, "localdev", {}, key)
    for result in (first, second):
        assert [p["suggestions"][0]["value"] for p in result["placeholders"]] == values
        assert apply_suggestions(template, result) == (instance, "sampled")
    assert second["sample"]["source"] == mod.SOURCE_STORED_SAMPLE


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


def test_alignment_checks_full_template_and_preserves_repeated_slots():
    sql = "SELECT * FROM t WHERE status = $1 AND n > $2 OR status = $1 LIMIT 10"
    slots = enumerate_placeholders(sql)
    sample = "SELECT * FROM t WHERE status = 'O''Brien' AND n > -2 OR status = 'O''Brien' LIMIT 10"
    assert align_sample_values(sample, slots, sql) == {"$1": "O'Brien", "$2": "-2"}
    assert align_sample_values(sample.replace("LIMIT 10", "LIMIT 11"), slots, sql) is None
    assert align_sample_values(sample.replace("OR status", "AND status"), slots, sql) is None
    assert align_sample_values(sample.replace("'O''Brien' LIMIT", "'Other' LIMIT"), slots, sql) is None


def test_alignment_does_not_consume_fixed_literals_or_identifiers():
    sql = "SELECT col2 FROM t WHERE id=$1 AND fixed=42"
    slots = enumerate_placeholders(sql)
    assert align_sample_values("SELECT col2 FROM t WHERE id=7 AND fixed=42", slots, sql) == {"$1": "7"}
    assert align_sample_values("SELECT col3 FROM t WHERE id=7 AND fixed=42", slots, sql) is None
    assert align_sample_values("SELECT col2 FROM t WHERE id=7 AND fixed=43", slots, sql) is None
    quoted = "SELECT * FROM `t` WHERE `id`=?"
    assert align_sample_values("SELECT * FROM t WHERE id=7", enumerate_placeholders(quoted),
                               quoted) == {"?1": "7"}


def test_aligned_sample_keeps_literal_type_for_substitution():
    from features.monitor.analyze_queue import apply_suggestions

    sql = "SELECT * FROM t WHERE code=$1 AND flag=$2"
    slots = enumerate_placeholders(sql)
    choices = {slot["key"]: [] for slot in slots}
    sample = mod._use_aligned_sample(
        [{"sql": "SELECT * FROM t WHERE code='123' AND flag=TRUE",
          "source": SOURCE_PG_STAT_ACTIVITY}], sql, slots, choices)
    assert sample["aligned"]
    rendered, source = apply_suggestions(sql, {"placeholders": [
        {"placeholder": slot["placeholder"], "suggestions": choices[slot["key"]]}
        for slot in slots]})
    assert (rendered, source) == ("SELECT * FROM t WHERE code='123' AND flag=TRUE", "sampled")


@pytest.mark.parametrize(("column_type", "expected"), [
    ("text", "'04711'"),
    ("integer", "48181"),
])
def test_column_sample_uses_database_type_for_substitution(monkeypatch, column_type, expected):
    from features.monitor.analyze_queue import apply_suggestions

    conn = MagicMock()
    cur = conn.cursor.return_value
    cur.fetchone.side_effect = [(column_type,), (["04711" if column_type == "text" else "48181"],)]
    monkeypatch.setattr(mod, "_connect", lambda *args: conn)
    monkeypatch.setattr(mod.analysis_lane, "register", lambda *args: object())
    monkeypatch.setattr(mod.analysis_lane, "StatementConnection", lambda raw, run: raw)
    monkeypatch.setattr(mod.analysis_lane, "unregister", lambda *args: None)
    monkeypatch.setattr(mod, "_fetch_sample", lambda *args: None)

    sql = ("SELECT customer_id, city FROM addresses WHERE postal_code = $1"
           if column_type == "text" else "SELECT id FROM addresses WHERE house_number = $1")
    result = suggest_parameter_values(sql, "db", {"engine": "postgresql"})
    rendered, source = apply_suggestions(sql, result)
    assert rendered == sql.replace("$1", expected)
    assert source == "table_sampled"


def test_monitor_sample_history_is_bounded_and_latest_first(tmp_path):
    from shared.monitor.monitor_store import MonitorStore

    store = MonitorStore(tmp_path / "monitor.db")
    for value in range(8):
        store.record("db", 100 + value, [], {}, [], samples=[
            {"query_key": "q", "text": f"SELECT {value}"}])
    rows = store.read("SELECT text FROM query_sample WHERE target='db' AND query_key='q' "
                      "ORDER BY id DESC")
    assert [row["text"] for row in rows] == [f"SELECT {n}" for n in range(7, 2, -1)]
    store.record("db", 109, [], {}, [], samples=[{"query_key": "q", "text": "SELECT 5"}])
    rows = store.read("SELECT text FROM query_sample WHERE target='db' AND query_key='q' "
                      "ORDER BY id DESC")
    assert rows[0]["text"] == "SELECT 5" and len(rows) == 5


def test_suggestions_use_stored_values_before_table_fallback(monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(mod, "_connect", lambda *args: conn)
    monkeypatch.setattr(mod.analysis_lane, "register", lambda *args: object())
    monkeypatch.setattr(mod.analysis_lane, "StatementConnection", lambda raw, run: raw)
    monkeypatch.setattr(mod.analysis_lane, "unregister", lambda *args: None)
    monkeypatch.setattr(mod, "_stored_samples", lambda *args: [
        {"sql": "SELECT * FROM t WHERE id=7", "source": mod.SOURCE_STORED_SAMPLE}])
    monkeypatch.setattr(mod, "_fetch_sample", lambda *args: None)
    monkeypatch.setattr(mod, "_burst_pg_sample", lambda *args: (_ for _ in ()).throw(
        AssertionError("burst should not run")))
    monkeypatch.setattr(mod, "_sample_column_values", lambda *args: (_ for _ in ()).throw(
        AssertionError("table fallback should not run")))
    result = suggest_parameter_values("SELECT * FROM t WHERE id=$1", "db", {}, REGISTRY_HASH)
    assert [item["value"] for item in result["placeholders"][0]["suggestions"]] == ["7"]
    assert result["placeholders"][0]["suggestions"][0]["literal_sql"] == "7"


def test_suggestions_burst_then_table_when_history_does_not_align(monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(mod, "_connect", lambda *args: conn)
    monkeypatch.setattr(mod.analysis_lane, "register", lambda *args: object())
    monkeypatch.setattr(mod.analysis_lane, "StatementConnection", lambda raw, run: raw)
    monkeypatch.setattr(mod.analysis_lane, "unregister", lambda *args: None)
    monkeypatch.setattr(mod, "_stored_samples", lambda *args: [
        {"sql": "SELECT * FROM other WHERE id=7", "source": mod.SOURCE_STORED_SAMPLE}])
    monkeypatch.setattr(mod, "_fetch_sample", lambda *args: None)
    monkeypatch.setattr(mod, "_burst_pg_sample", lambda *args: None)
    monkeypatch.setattr(mod, "_sample_column_values", lambda *args: [
        {"value": "8", "provenance": "Common value in t.id (pg_stats)"}])
    result = suggest_parameter_values("SELECT * FROM t WHERE id=$1", "db", {}, REGISTRY_HASH)
    assert result["sample"] is None
    assert result["placeholders"][0]["suggestions"][0]["value"] == "8"


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


def test_burst_sampler_stops_at_first_matching_activity(monkeypatch):
    monkeypatch.setattr(mod, "_pg_query_ids", lambda query_hash, target: [123])
    class Cursor:
        def __init__(self):
            self.polls = 0
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            if "pg_stat_activity" in sql:
                self.polls += 1
                assert params == ([123],)
                assert "state='active'" in sql

        def fetchone(self):
            if "server_version_num" in self.sql:
                return (160000,)
            if "pg_stat_activity" in self.sql and self.polls == 2:
                return ("SELECT * FROM t WHERE id=7", "now")
            return None

        def close(self):
            pass

    cur = Cursor()
    conn = MagicMock()
    conn.cursor.return_value = cur
    current = [0.0]

    def sleep(seconds):
        current[0] += seconds

    sample = mod._burst_pg_sample(conn, REGISTRY_HASH, "db",
                                  clock=lambda: current[0], sleep=sleep)
    assert sample["sql"] == "SELECT * FROM t WHERE id=7"
    assert cur.polls == 2 and current[0] == mod.BURST_INTERVAL_SECONDS


def test_mysql_history_sample_precedes_digest_summary(monkeypatch):
    monkeypatch.setattr(mod, "_mysql_digests", lambda query_hash, target: ["digest-a"])
    cur = MagicMock()
    cur.fetchone.return_value = ("SELECT * FROM t WHERE id=7", 123)
    conn = MagicMock()
    conn.cursor.return_value = cur
    sample = mod._fetch_sample(conn, "mysql", "SELECT * FROM t WHERE id=?", REGISTRY_HASH, "db")
    assert sample["source"] == SOURCE_MYSQL_HISTORY
    assert "events_statements_history_long" in cur.execute.call_args.args[0]
    assert cur.execute.call_args.args[1] == ("digest-a",)


def test_mysql_history_uses_digest_text_when_alias_is_not_yet_stored(monkeypatch):
    monkeypatch.setattr(mod, "_mysql_digests", lambda query_hash, target: [])
    cur = MagicMock()
    cur.fetchone.side_effect = [("digest-b",), ("SELECT * FROM t WHERE id=7", 123)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    sample = mod._fetch_sample(conn, "mysql", "SELECT * FROM t WHERE id=?", REGISTRY_HASH, "db")
    assert sample["source"] == SOURCE_MYSQL_HISTORY
    assert "DIGEST_TEXT" in cur.execute.call_args_list[0].args[0]
    assert "events_statements_history_long" in cur.execute.call_args_list[1].args[0]


def test_mysql_history_falls_back_to_short_history(monkeypatch):
    monkeypatch.setattr(mod, "_mysql_digests", lambda query_hash, target: ["digest-a"])
    cur = MagicMock()
    def execute(sql, _params):
        if "events_statements_history_long" in sql:
            raise RuntimeError("history long unavailable")
    cur.execute.side_effect = execute
    cur.fetchone.return_value = ("SELECT * FROM t WHERE id=8", 456)
    conn = MagicMock()
    conn.cursor.return_value = cur
    sample = mod._fetch_sample(conn, "mysql", "SELECT * FROM t WHERE id=?", REGISTRY_HASH, "db")
    assert sample["source"] == SOURCE_MYSQL_HISTORY
    assert "events_statements_history " in cur.execute.call_args.args[0]
    conn.rollback.assert_called_once()


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


def test_unsampled_placeholders_stay_placeholders(monkeypatch):
    """Shape hints are never substituted: an unfilled placeholder stays, for a generic plan."""
    from features.monitor.analyze_queue import apply_suggestions

    monkeypatch.setattr(mod, "_connect", lambda *args: None)
    cases = [
        ("SELECT now() - interval $1", "SELECT now() - $1::interval"),
        ("SELECT date $1", "SELECT $1::date"),
        ("SELECT timestamp $1", "SELECT $1::timestamp"),
        ("SELECT 1 LIMIT $1", "SELECT 1 LIMIT $1"),
        ("SELECT 1 OFFSET $1", "SELECT 1 OFFSET $1"),
        ("SELECT * FROM t WHERE name LIKE $1", "SELECT * FROM t WHERE name LIKE $1"),
        ("SELECT * FROM t WHERE id IN ($1, $2)", "SELECT * FROM t WHERE id IN ($1, $2)"),
        ("SELECT $1::uuid", "SELECT $1::uuid"),
        ("SELECT CAST($1 AS date)", "SELECT CAST($1 AS date)"),
    ]
    for sql, expected in cases:
        result = suggest_parameter_values(sql, "t", {"engine": "postgresql"})
        filled, source = apply_suggestions(sql, result)
        assert filled == expected, sql
        assert source == "none"


def test_exact_live_interval_query_without_database(monkeypatch):
    from features.monitor.analyze_queue import apply_suggestions

    monkeypatch.setattr(mod, "_connect", lambda *args: None)
    sql = ("SELECT c.country, count(DISTINCT o.id) orders, "
           "round(sum(oi.quantity * oi.unit_price), $1) revenue "
           "FROM customers c JOIN orders o ON o.customer_id = c.id "
           "JOIN order_items oi ON oi.order_id = o.id "
           "WHERE o.placed_at > now() - interval $2 "
           "GROUP BY c.country ORDER BY revenue DESC")
    filled, source = apply_suggestions(sql, suggest_parameter_values(sql, "t", {"engine": "postgresql"}))
    assert "round(sum(oi.quantity * oi.unit_price), $1)" in filled
    assert "now() - $2::interval" in filled
    assert "NULL" not in filled and "7 days" not in filled
    assert source == "none"


def test_a_placeholder_compared_to_a_function_of_a_column_binds_to_that_column():
    from features.analyze.parameter_suggestions import (bind_placeholders_to_columns,
                                                        enumerate_placeholders)

    sql = ("SELECT readings.site_id FROM readings WHERE date_trunc('minute', "
           "readings.recorded_at) >= $1 AND lower(readings.metric) = $2")
    bound = bind_placeholders_to_columns(sql, "postgresql", enumerate_placeholders(sql))
    assert bound["$1"]["column"] == "readings.recorded_at"
    assert bound["$2"]["column"] == "readings.metric"


def test_a_newer_than_comparison_on_a_time_column_is_marked_after():
    from features.analyze.parameter_suggestions import (bind_placeholders_to_columns,
                                                        enumerate_placeholders)

    sql = ("SELECT id FROM readings WHERE recorded_at >= $1 AND $2 < received_at "
           "AND device_id = $3")
    bound = bind_placeholders_to_columns(sql, "postgresql", enumerate_placeholders(sql))
    assert [bound[key]["after"] for key in ("$1", "$2", "$3")] == [True, True, False]


def test_a_date_trunc_unit_placeholder_is_a_time_unit():
    from features.analyze.parameter_suggestions import (bind_placeholders_to_columns,
                                                        enumerate_placeholders)

    sql = "SELECT 1 FROM readings WHERE date_trunc($1, readings.recorded_at) >= $2::timestamp"
    bound = bind_placeholders_to_columns(sql, "postgresql", enumerate_placeholders(sql))
    assert bound["$1"] == {"kind": "time_unit"}
    assert bound["$2"]["column"] == "readings.recorded_at" and bound["$2"]["after"]
