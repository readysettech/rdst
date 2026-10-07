from features.monitor import table_stats
from shared.monitor.monitor_store import MonitorStore


def _table(name, **counters):
    row = {"schema": "public", "table": name, "ins": 0, "upd": 0, "del": 0, "hot_upd": 0,
           "seq_scan": 0, "idx_scan": 0, "scan_rows": 0, "live_rows": 100, "dead_rows": 0,
           "size_bytes": 8192}
    return {**row, **counters}


def _index(name, scans, **fields):
    return {"schema": "public", "table": "events", "index_name": name, "scans": scans,
            "size_bytes": 4096, "unique": False, "primary": False, "last_scan_at": None, **fields}


def _reading(ts, tables, indexes=(), settings=None, since=1_000, engine="postgresql"):
    return {"engine": engine, "version": 160000, "counter_since": since, "taken_at": ts,
            "tables": list(tables), "indexes": list(indexes), "settings": settings or {}}


def test_activity_is_the_difference_between_readings(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record_stats("db", _reading(36_000, [_table("events", ins=100)], [_index("e_kind", 5)]))
    assert store.read("SELECT * FROM table_activity") == []
    store.record_stats("db", _reading(36_300, [_table("events", ins=160, upd=4)],
                                      [_index("e_kind", 5)]))
    store.record_stats("db", _reading(36_600, [_table("events", ins=200, upd=4)],
                                      [_index("e_kind", 9)]))
    row = store.read("SELECT * FROM table_activity")[0]
    assert (row["ins"], row["upd"], row["seconds"], row["bucket_start"]) == (100, 4, 600, 36_000)
    assert store.read("SELECT scans FROM index_activity")[0]["scans"] == 4
    state = store.read("SELECT * FROM index_state")[0]
    assert (state["scans"], state["first_seen"]) == (9, 36_000)


def test_a_counter_going_backwards_counts_from_the_reset(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record_stats("db", _reading(36_000, [_table("events", ins=500)]))
    store.record_stats("db", _reading(36_300, [_table("events", ins=30)]))
    assert store.read("SELECT ins FROM table_activity")[0]["ins"] == 30
    assert store.read("SELECT counter_since FROM stats_meta")[0]["counter_since"] == 36_000


def test_a_newer_stats_reset_restarts_every_counter(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record_stats("db", _reading(36_000, [_table("events", ins=500)], [_index("e_kind", 50)]))
    store.record_stats("db", _reading(36_300, [_table("events", ins=900)], [_index("e_kind", 70)],
                                      since=36_200))
    # After a reset the counters hold the activity since it.
    assert store.read("SELECT ins FROM table_activity")[0]["ins"] == 900
    assert store.read("SELECT scans FROM index_activity")[0]["scans"] == 70
    assert store.read("SELECT counter_since FROM stats_meta")[0]["counter_since"] == 36_200


def test_mysql_uptime_drift_is_not_a_reset(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record_stats("db", _reading(36_000, [_table("events", ins=10)], since=1_000, engine="mysql"))
    store.record_stats("db", _reading(36_300, [_table("events", ins=40)], since=1_001, engine="mysql"))
    assert store.read("SELECT ins FROM table_activity")[0]["ins"] == 30
    assert store.read("SELECT counter_since FROM stats_meta")[0]["counter_since"] == 1_000


def test_setting_changes_are_recorded_once(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    work_mem = {"value": "4096", "unit": "kB", "pending_restart": False}
    assert store.record_stats("db", _reading(36_000, [], settings={"work_mem": work_mem})) == []
    raised = {**work_mem, "value": "32768"}
    changes = store.record_stats("db", _reading(36_300, [], settings={"work_mem": raised}))
    assert changes == [{"name": "work_mem", "old": "4096", "new": "32768", "ts": 36_300}]
    assert store.record_stats("db", _reading(36_600, [], settings={"work_mem": raised})) == []
    assert store.read("SELECT changed_at FROM setting_state")[0]["changed_at"] == 36_300


def test_dropped_tables_and_indexes_leave_the_current_state(tmp_path):
    store = MonitorStore(tmp_path / "monitor.db")
    store.record_stats("db", _reading(36_000, [_table("events"), _table("old")],
                                      [_index("e_kind", 1), _index("e_old", 1)]))
    store.record_stats("db", _reading(36_300, [_table("events")], [_index("e_kind", 1)]))
    assert [row["table"] for row in store.read('SELECT "table" FROM table_state')] == ["events"]
    assert [row["index_name"] for row in store.read("SELECT index_name FROM index_state")] == ["e_kind"]


class _Cursor:
    def __init__(self, answers):
        self.answers, self.rows, self.executed = answers, [], []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self.rows = next((rows for marker, rows in self.answers if marker in sql), [])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Connection:
    def __init__(self, answers):
        self.cursor_ = _Cursor(answers)

    def cursor(self):
        return self.cursor_


def test_mysql_reading_attributes_full_scan_rows_and_index_accesses():
    connection = _Connection([
        ("global_status", [("7200",)]),
        ("table_io_waits_summary_by_table", [("shop", "events", 900, 3, 1, 5000, 4096)]),
        ("information_schema.STATISTICS", [("shop", "events", "PRIMARY", 0),
                                           ("shop", "events", "e_kind", 1)]),
        ("innodb_index_stats", [("events", "e_kind", 16384)]),
        ("by_index_usage", [("shop", "events", None, 120000), ("shop", "events", "PRIMARY", 40),
                            ("shop", "events", "e_kind", 0)]),
        ("global_variables", [("TMP_TABLE_SIZE", "16777216")]),
    ])
    reading = table_stats.read_stats(connection, "mysql", now=10_000)
    assert reading["counter_since"] == 10_000 - 7200
    events = reading["tables"][0]
    assert (events["ins"], events["scan_rows"], events["idx_scan"]) == (900, 120000, 40)
    by_name = {index["index_name"]: index for index in reading["indexes"]}
    assert by_name["PRIMARY"]["primary"] and by_name["PRIMARY"]["unique"]
    assert (by_name["e_kind"]["scans"], by_name["e_kind"]["size_bytes"]) == (0, 16384)
    assert reading["settings"] == {"tmp_table_size": {"value": "16777216", "unit": None,
                                                      "pending_restart": False}}


def test_postgres_reading_uses_last_idx_scan_only_on_16_and_later():
    answers = [("server_version_num", [(150004, 1_000, None)]), ("pg_stat_user_tables", []),
               ("pg_stat_user_indexes", []), ("pg_settings", [])]
    connection = _Connection(answers)
    reading = table_stats.read_stats(connection, "postgresql", now=10_000)
    assert reading["counter_since"] == 1_000
    assert not any("last_idx_scan" in sql for sql in connection.cursor_.executed)


def test_live_rows_survive_a_statistics_reset():
    """n_live_tup restarts near zero at a reset while dead rows keep counting; the planner's
    estimate keeps the dead-row share honest."""
    from features.audit.health import vacuum_bloat

    for sql in (table_stats.PG_TABLES_SQL, vacuum_bloat._PG_TABLE_SQL):
        assert "GREATEST(" in sql and "reltuples" in sql
