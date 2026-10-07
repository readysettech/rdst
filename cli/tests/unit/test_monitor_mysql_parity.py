"""MySQL gets the same daily-snapshot health checks and database actions as PostgreSQL, in
MySQL's own terms and SQL."""

from __future__ import annotations

import dataclasses

from features.audit.health.config_audit import collect_config_audit
from features.audit.health.connection_analysis import collect_connection_analysis
from features.audit.health.index_health import collect_index_health
from features.audit.health.vacuum_bloat import collect_vacuum_bloat
from features.monitor import db_actions
from features.monitor.snapshot import health_section

MB = 1024 * 1024


class Cursor:
    """Positional rows by the first matching SQL fragment; a missing rule raises."""

    def __init__(self, rules):
        self.rules, self.rows, self.executed = rules, [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for fragment, rows in self.rules:
            if fragment in sql:
                if isinstance(rows, Exception):
                    raise rows
                self.rows = list(rows)
                return
        raise RuntimeError(f"unexpected SQL: {sql[:80]}")

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, rules):
        self.rules = rules

    def cursor(self):
        return Cursor(self.rules)


def _dict(section):
    return dataclasses.asdict(section)


def test_vacuum_reports_free_space_stale_statistics_and_purge_backlog():
    connection = Connection([
        ("information_schema.TABLES", [("shop", "events", 300_000, 900 * MB, 600 * MB),
                                       ("shop", "customers", 20_000, 4 * MB, 3 * MB),
                                       ("shop", "orders", 200_000, 0, 30 * MB)]),
        ("INNODB_TABLESTATS", [("shop/events", 300_000, 0), ("shop/customers", 20_000, 0),
                               ("shop/orders", 200_000, 90_000)]),
        ("innodb_table_stats", []),
        ("trx_rseg_history_len", [(2_500_000,)]),
    ])
    section = _dict(collect_vacuum_bloat(connection, "mysql"))
    tables = {table["table"]: table for table in section["tables"]}
    # Free space is what a rebuild returns; small tables' extent slack is not bloat.
    assert tables["events"]["free_bytes"] == 900 * MB and tables["events"]["dead_tuples"] == 0
    assert tables["events"]["status"] == "warn"
    assert tables["customers"]["status"] == "healthy"
    assert tables["orders"]["n_mod_since_analyze"] == 90_000
    assert section["history_list_length"] == 2_500_000

    actions = {action["id"]: action for action in db_actions.database_actions(
        {"engine": "mysql", "vacuum_bloat": section}, {})}
    assert actions["vacuum"]["sql"] == "OPTIMIZE TABLE `shop`.`events`;"
    assert "free space" in actions["vacuum"]["finding"]
    assert actions["analyze"]["sql"] == "ANALYZE TABLE `shop`.`orders`;"
    assert actions["purge_lag"]["severity"] == "warn"

    health = health_section({"engine": "mysql", "vacuum_bloat": section})
    problems = {problem["check"]: problem for problem in health["problems"]}
    assert problems["vacuum_bloat"]["sql"] == "OPTIMIZE TABLE `shop`.`events`;"
    assert problems["vacuum_bloat"]["rows"][0]["value"] == "60.0% free"
    assert "purge_lag" in problems


def test_unused_indexes_have_sizes_and_mysql_drop_sql():
    connection = Connection([
        ("information_schema.STATISTICS", [
            ("shop", "orders", "PRIMARY", 1, 1, "id"),
            ("shop", "orders", "idx_orders_status", 0, 0, "status"),
            ("shop", "orders", "idx_orders_status_created", 0, 0, "status,created_at")]),
        ("table_io_waits_summary_by_index_usage", [
            ("shop", "orders", "PRIMARY", 50, 50, 50),
            ("shop", "orders", "idx_orders_status_created", 9, 9, 9)]),
        ("innodb_index_stats", [("shop", "orders", "idx_orders_status", 12 * MB)]),
    ])
    section = _dict(collect_index_health(connection, "mysql"))
    assert [row["index"] for row in section["unused_indexes"]] == ["idx_orders_status"]
    assert section["unused_indexes"][0]["size_bytes"] == 12 * MB
    assert section["duplicates"][0]["covered_by"] == "idx_orders_status_created"
    actions = {action["id"]: action for action in db_actions.database_actions(
        {"engine": "mysql", "index_health": section}, {})}
    assert actions["unused_indexes"]["sql"] == (
        "ALTER TABLE `shop`.`orders` DROP INDEX `idx_orders_status`;")
    assert actions["unused_indexes"]["evidence"][1]["value"] == "12.0 MB"
    health = health_section({"engine": "mysql", "index_health": section})
    problem = next(p for p in health["problems"] if p["check"] == "unused_indexes")
    assert problem["sql"] == "ALTER TABLE `shop`.`orders` DROP INDEX `idx_orders_status`;"


def test_unreadable_index_usage_is_not_checked_instead_of_all_unused():
    connection = Connection([
        ("information_schema.STATISTICS", [("shop", "orders", "idx_a", 0, 0, "a")]),
        ("table_io_waits_summary_by_index_usage", RuntimeError("SELECT command denied")),
    ])
    try:
        collect_index_health(connection, "mysql")
    except RuntimeError:
        pass
    else:
        raise AssertionError("an unreadable usage table must fail the section")


def test_idle_in_transaction_sessions_come_from_innodb_trx():
    connection = Connection([
        ("max_connections", [("max_connections", "151")]),
        ("GROUP BY COALESCE(PROCESSLIST_COMMAND", [("Sleep", 8), ("Query", 2)]),
        ("INNODB_TRX", [(41, 900, "app", "10.0.0.5", "checkout-svc", ""),
                        (42, 20, "app", "10.0.0.6", None, "")]),
        ("session_connect_attrs", [("proxysql",)]),
    ])
    section = _dict(collect_connection_analysis(connection, "mysql"))
    assert section["idle_in_transaction_count"] == 2
    assert [row["pid"] for row in section["long_running_idle_in_tx"]] == [41]
    assert section["pooler_detected"] and section["pooler_type"] == "proxysql"
    action = next(a for a in db_actions.database_actions(
        {"engine": "mysql", "connections": section}, {}) if a["id"] == "idle_in_transaction")
    assert action["rows"][0]["name"] == "checkout-svc" and action["sql"] is None


def test_settings_default_buffer_pool_merges_with_a_low_cache_hit():
    connection = Connection([("SHOW GLOBAL VARIABLES", [
        ("innodb_buffer_pool_size", str(128 * MB)), ("max_connections", "151"),
        ("sort_buffer_size", "262144"), ("tmp_table_size", str(16 * MB)),
        ("max_heap_table_size", str(16 * MB)), ("innodb_redo_log_capacity", str(100 * MB)),
        ("innodb_io_capacity", "200"), ("slow_query_log", "OFF"), ("long_query_time", "10"),
        ("innodb_stats_persistent", "ON"), ("innodb_stats_auto_recalc", "OFF")])])
    config = _dict(collect_config_audit(connection, "mysql"))
    status = {row["parameter"]: row["status"] for row in config["settings"]}
    assert status["innodb_redo_log_capacity"] == "warn"
    assert status["innodb_io_capacity"] == "warn"
    assert status["long_query_time"] == "warn"
    assert status["innodb_stats_auto_recalc"] == "crit"
    actions = {action["id"]: action for action in db_actions.database_actions(
        {"engine": "mysql", "config_audit": config},
        {"cache_hit_ratio_min": 0.82, "cache_hit_weighted": 0.9, "disk_reads_per_s": 400,
         "deadlocks": 3})}
    buffers = actions["setting_innodb_buffer_pool_size"]
    # Like shared_buffers at its default: one story with the low cache hit.
    assert "cache_hit" not in actions and buffers["severity"] == "warn"
    assert "MySQL's out-of-the-box default" in buffers["finding"]
    assert buffers["finding"].startswith("Only 90.0% of reads found their data in memory.")
    # The buffer pool resizes online, so no restart and SET PERSIST.
    assert buffers["restart"] is False
    assert actions["setting_innodb_io_capacity"]["sql"] == (
        "SET PERSIST innodb_io_capacity = 1000;")
    assert actions["setting_long_query_time"]["sql"] == "SET PERSIST long_query_time = 0.2;"
    assert actions["deadlocks"]["sql"] == "SET PERSIST innodb_print_all_deadlocks = ON;"
    assert db_actions.setting_sql("innodb_log_file_size", "1 GB", "mysql") == (
        "SET PERSIST_ONLY innodb_log_file_size = 1073741824;")


def test_connection_pressure_names_a_mysql_pooler():
    action = next(a for a in db_actions.database_actions(
        {"engine": "mysql", "connections": {"total_connections": 140, "max_connections": 151}},
        {}) if a["id"] == "connections")
    assert "ProxySQL" in action["steps"]


def test_plan_only_fallback_reads_mysql_tree_plan(monkeypatch):
    from features.monitor import report as reports

    calls = []

    class PlanCursor:
        def execute(self, sql):
            calls.append(sql)

        def fetchall(self):
            # The target connection's dict rows; one EXPLAIN FORMAT=TREE cell, many lines.
            return [{"EXPLAIN": "-> Filter: (orders.status = 'paid')\n"
                                "    -> Table scan on orders"}]

        def close(self):
            pass

    class PlanConnection:
        def cursor(self):
            return PlanCursor()

        def rollback(self):
            pass

        def close(self):
            pass

    class Config:
        def load(self):
            pass

        def get(self, _target):
            return {"engine": "mysql"}

    monkeypatch.setattr("shared.config.targets.TargetsConfig", Config)
    monkeypatch.setattr("shared.db_connection.create_direct_connection",
                        lambda _config, **kwargs: PlanConnection())
    assert reports.plain_explain("shop", "SELECT * FROM orders WHERE status = 'paid'") == {
        "plan": ["-> Filter: (orders.status = 'paid')", "    -> Table scan on orders"]}
    assert calls == ["/*rdst:analyze*/ EXPLAIN FORMAT=TREE "
                     "SELECT * FROM orders WHERE status = 'paid'"]


def test_deep_analysis_reads_mysql_global_status_from_performance_schema():
    from features.analyze.functions.query_metrics import _get_mysql_global_status

    class StatusCursor:
        def execute(self, sql):
            assert "performance_schema.global_status" in sql
            assert "information_schema.GLOBAL_STATUS" not in sql

        def fetchall(self):
            return [{"VARIABLE_NAME": "Threads_running", "VARIABLE_VALUE": "3"}]

    assert _get_mysql_global_status(StatusCursor()) == {"mysql_status_threads_running": 3}


def test_health_overview_speaks_mysql():
    collectors = {"engine": "mysql", "vacuum_bloat": {"tables": []},
                  "replication": {"is_replica": False}}
    overview = db_actions.health_overview(collectors, {}, [])
    checks = {check["check"]: check for check in overview["checks"]}
    assert checks["vacuum"]["label"] == "Free space and purge"
    assert checks["vacuum"]["value"] == "Nothing to reclaim, purge keeping up"
    assert checks["replication"]["value"] == "No replica lag"
