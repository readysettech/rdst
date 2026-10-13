"""MySQL Deep Analyze executes only supported, bounded SELECTs."""

from features.analyze.functions.explain_analysis import (
    _extract_mysql_actual_time, _extract_mysql_rows_returned, _mysql_analyze_sql,
)

# MySQL 8.0 EXPLAIN ANALYZE output for a primary-key lookup.
_PK_LOOKUP_PLAN = (
    "-> Limit: 100 row(s)  (cost=0.35 rows=1) (actual time=0.015..0.020 rows=1 loops=1)\n"
    "    -> Rows fetched before execution  (cost=0..0 rows=1) "
    "(actual time=0.002..0.003 rows=1 loops=1)\n"
)


def test_mysql_analyze_requires_supported_version_and_select():
    assert _mysql_analyze_sql("SELECT id FROM orders", "8.0.17", 1000) is None
    assert _mysql_analyze_sql("SELECT id FROM orders", "10.6.1-MariaDB", 1000) is None
    assert _mysql_analyze_sql("SELECT id FROM orders", "8.0.18", 1000) == (
        "SELECT /*+ MAX_EXECUTION_TIME(1000) */ id FROM orders")
    assert _mysql_analyze_sql("SELECT id FROM orders", "8.4.11", 30000)
    assert _mysql_analyze_sql("UPDATE orders SET total=0", "8.4.11", 1000) is None
    assert _mysql_analyze_sql("SELECT 1; DELETE FROM orders", "8.4.11", 1000) is None
    # CTEs and set operations run too (the wait loop enforces their timeout), like PostgreSQL.
    assert _mysql_analyze_sql("WITH q AS (SELECT 1) SELECT * FROM q", "8.4.11", 1000) == (
        "WITH q AS (SELECT 1) SELECT * FROM q")
    assert _mysql_analyze_sql("SELECT a FROM t UNION SELECT a FROM u", "8.4.11", 1000)
    # Locking reads take row locks when run.
    assert _mysql_analyze_sql("SELECT id FROM orders FOR UPDATE", "8.4.11", 1000) is None
    assert _mysql_analyze_sql(
        "SELECT id FROM `orders` LOCK IN SHARE MODE", "8.4.11", 1000) is None


def test_mysql_analysis_restores_keyword_table_case():
    from features.analyze.functions.explain_analysis import _mysql_restore_table_case

    class Cursor:
        def execute(self, sql):
            assert "information_schema.TABLES" in sql

        def fetchall(self):
            return [{"TABLE_NAME": "events"}, {"TABLE_NAME": "orders"}]

    assert _mysql_restore_table_case(Cursor(), None, "SELECT COUNT(*) FROM EVENTS") == (
        "SELECT COUNT(*) FROM events")


def test_mysql_rows_returned_are_the_actual_rows_not_the_estimate():
    plan = ("-> Filter: (customers.email = 'c5@shop.test')  (cost=2444 rows=2404) "
            "(actual time=0.0747..4.77 rows=1 loops=1)\n"
            "    -> Table scan on customers  (cost=2444 rows=24036) "
            "(actual time=0.0587..3.64 rows=24000 loops=1)\n")
    assert _extract_mysql_rows_returned([{"EXPLAIN": plan}]) == 1
    limited = ("-> Limit: 20 row(s)  (cost=1867 rows=20) "
               "(actual time=3.67..3.68 rows=20 loops=1)\n")
    assert _extract_mysql_rows_returned([{"EXPLAIN": limited}]) == 20
    estimate_only = "-> Filter: (orders.status = 'new')  (cost=12 rows=180)\n"
    assert _extract_mysql_rows_returned([{"EXPLAIN": estimate_only}]) == 180


def test_mysql_missing_values_produce_plain_explain(monkeypatch):
    from unittest.mock import MagicMock

    from features.analyze.functions import explain_analysis
    from features.analyze.parameter_suggestions import plan_only_sql

    sql = ("SELECT id, status, total, created_at FROM orders WHERE customer_id = ? "
           "ORDER BY created_at DESC LIMIT ?")
    filled = plan_only_sql(sql, "mysql")
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"EXPLAIN": '{"query_block": {"select_id": 1}}'}
    monkeypatch.setattr(explain_analysis, "resolve_connection_params", lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "_mysql_connection", lambda *args: conn)
    monkeypatch.setattr(explain_analysis, "_mysql_restore_table_case", lambda cur, run, sql: sql)
    result = explain_analysis._execute_mysql_explain_analyze(
        filled, {}, fast_mode=True, statement_timeout_ms=1000)
    assert result["success"] and result["explain_analyze_skipped"]
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert len(statements) == 1
    assert "EXPLAIN FORMAT=JSON" in statements[0]
    assert "customer_id = 1" in statements[0] and statements[0].endswith("LIMIT 10")
    assert "ANALYZE" not in statements[0]


def test_mysql_actual_time_is_the_root_last_row_time_times_loops():
    assert _extract_mysql_actual_time([{"EXPLAIN": _PK_LOOKUP_PLAN}]) == 0.020
    # A nested node's per-loop time is not the statement's time; only the root counts.
    join = ("-> Nested loop inner join  (cost=4.1 rows=3) "
            "(actual time=0.0747..4.77 rows=3 loops=1)\n"
            "    -> Index lookup on o using fk (customer_id=c.id)  (cost=0.3 rows=1) "
            "(actual time=0.5..1.5 rows=1 loops=3)\n")
    assert _extract_mysql_actual_time([{"EXPLAIN": join}]) == 4.77
    looped = "-> Filter: (t.a > 1)  (cost=1 rows=1) (actual time=0.01..0.25 rows=2 loops=4)\n"
    assert _extract_mysql_actual_time([{"EXPLAIN": looped}]) == 1.0
    # Newer servers print small numbers in exponent form.
    tiny = ("-> Rows fetched before execution  (cost=0..0 rows=1) "
            "(actual time=83e-6..83e-6 rows=1 loops=1)\n")
    assert abs(_extract_mysql_actual_time([{"EXPLAIN": tiny}]) - 83e-6) < 1e-12
    estimate_only = "-> Table scan on t  (cost=1 rows=5)\n"
    assert _extract_mysql_actual_time([{"EXPLAIN": estimate_only}]) is None
    assert _extract_mysql_actual_time([]) is None


def test_mysql_analyze_reports_the_plan_time_not_the_wall_clock(monkeypatch):
    from unittest.mock import MagicMock

    from features.analyze.functions import explain_analysis

    conn = MagicMock()
    conn.__enter__.return_value = conn
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [
        {"EXPLAIN": '{"query_block": {"select_id": 1}}'}, {"VERSION()": "8.0.36"},
        {"CONNECTION_ID()": 7}]
    worker_conn = MagicMock()
    worker_cursor = worker_conn.cursor.return_value.__enter__.return_value
    worker_cursor.fetchone.return_value = {"CONNECTION_ID()": 8}
    worker_cursor.fetchall.return_value = [{"EXPLAIN": _PK_LOOKUP_PLAN}]
    monkeypatch.setattr(explain_analysis, "resolve_connection_params",
                        lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "_mysql_connection", lambda *args: conn)
    monkeypatch.setattr(explain_analysis, "_mysql_restore_table_case", lambda cur, run, sql: sql)
    monkeypatch.setattr(explain_analysis, "create_mysql_connection_from_params",
                        lambda *args, **kwargs: worker_conn)

    result = explain_analysis._execute_mysql_explain_analyze(
        "SELECT id FROM orders WHERE id = 1 LIMIT 100", {}, non_interactive=True,
        statement_timeout_ms=5000)

    assert result["success"] and result["plan_format"] == "analyze"
    assert result["execution_time_ms"] == 0.020
    # The wait loop polls every 100 ms, so the wall clock is far above the plan's time.
    assert result["actual_elapsed_time_ms"] > result["execution_time_ms"]
    assert result["rows_returned"] == 1
