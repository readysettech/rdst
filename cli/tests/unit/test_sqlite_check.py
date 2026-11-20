import sqlite3

from shared.query_registry import observation_store
from shared.query_registry.observation_store import ObservationStore
from shared.sqlite_check import quick_check


class _Reports:
    """A connection whose quick_check returns canned problems; everything else is real."""

    def __init__(self, conn, problems):
        self._conn, self._problems = conn, problems

    def execute(self, sql, *args):
        if sql.strip().upper() == "PRAGMA QUICK_CHECK":
            return _Rows([(p,) for p in self._problems])
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _db(tmp_path, null=False):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE identity_alias (target_id TEXT, normalized_hash TEXT)")
    conn.execute("INSERT INTO identity_alias VALUES ('a', ?)", (None if null else "h",))
    return conn


def test_a_false_not_null_report_is_healthy(tmp_path):
    report = ["NULL value in identity_alias.normalized_hash"] * 3
    assert quick_check(_Reports(_db(tmp_path), report)) == "ok"


def test_a_real_null_is_still_reported(tmp_path):
    report = ["NULL value in identity_alias.normalized_hash"]
    assert quick_check(_Reports(_db(tmp_path, null=True), report)) == report[0]


def test_a_real_null_in_a_not_null_column_is_reported(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (a INTEGER PRIMARY KEY, b TEXT)")
    conn.execute("INSERT INTO t VALUES (1, NULL)")
    conn.commit()
    # Corrupt it the way a damaged file would: the schema now says NOT NULL.
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute("UPDATE sqlite_master SET sql = 'CREATE TABLE t (a INTEGER PRIMARY KEY, "
                 "b TEXT NOT NULL)' WHERE name = 't'")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(tmp_path / "t.db")
    assert "NULL value in t.b" in [row[0] for row in conn.execute("PRAGMA quick_check")]
    assert quick_check(conn) == "NULL value in t.b"


def test_other_problems_are_reported(tmp_path):
    conn = _db(tmp_path)
    assert quick_check(_Reports(conn, ["row 4 missing from index x"])) == "row 4 missing from index x"
    unknown = "NULL value in identity_alias.nope"
    assert quick_check(_Reports(conn, [unknown])) == unknown
    assert quick_check(conn) == "ok"


def test_cache_db_keeps_its_data_over_a_false_not_null_report(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    ObservationStore(path).close()
    connect = ObservationStore._connect
    monkeypatch.setattr(ObservationStore, "_connect", lambda self: _Reports(
        connect(self), ["NULL value in identity_alias.normalized_hash"]))
    ObservationStore(path).close()
    assert not list(tmp_path.glob("cache.db.corrupt-*"))
    assert observation_store.SCHEMA_VERSION == sqlite3.connect(path).execute(
        "PRAGMA user_version").fetchone()[0]
