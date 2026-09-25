"""MySQL Deep Analyze executes only supported, bounded SELECTs."""

from features.analyze.functions.explain_analysis import (
    _extract_mysql_rows_returned, _mysql_analyze_sql,
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
