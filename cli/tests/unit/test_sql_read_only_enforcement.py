"""Every path that runs generated or captured SQL on a user database reads only.

Text checks decide what is sent; the database decides what it may do. Statements on
these paths run in read-only transactions whatever the target's own ``read_only``
setting, and nothing is set on the session, which a transaction-mode pooler would hand
to another client. The text checks read quoting the way the server does.

Verified against PostgreSQL 17 and MySQL 8.4 containers: under a read-only
transaction PostgreSQL refuses nextval() and DELETE yet still plans writes with plain
EXPLAIN and runs hypopg; MySQL refuses writes and EXPLAIN of a write.
"""

from __future__ import annotations

import pytest

from features.ask.sql_validation import validate_sql_for_ask
from features.query_registry.service import (
    benchmark_execution_reason,
    benchmark_read_only_reason,
)


class _Log:
    """Records every statement a fake connection sees, transaction control included."""

    def __init__(self):
        self.log = []


class _Cursor:
    description = [("id",)]

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(sql)
        if "fail" in sql:
            raise RuntimeError("boom")

    def fetchmany(self, size):
        return [(1,)]

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class _PgConn(_Log):
    autocommit = True

    def cursor(self):
        return _Cursor(self)

    def close(self):
        pass


class _MySQLConn(_PgConn):
    def query(self, sql):
        self.log.append(sql)

    def commit(self):
        self.log.append("COMMIT")

    def rollback(self):
        self.log.append("ROLLBACK")


@pytest.fixture
def ask_connections(monkeypatch):
    import psycopg2

    conn = _MySQLConn()
    opened = {}
    monkeypatch.setattr(psycopg2, "connect", lambda **_kwargs: conn)
    monkeypatch.setattr(
        "shared.db_connection.resolve_connection_params",
        lambda **_kwargs: {"host": "h", "user": "u", "database": "d"},
    )
    monkeypatch.setattr(
        "shared.db_connection.postgres_connection_kwargs",
        lambda p, **kw: opened.update(postgres=kw) or {},
    )
    monkeypatch.setattr(
        "shared.db_connection.create_mysql_connection_from_params",
        lambda p, **kw: opened.update(mysql=kw) or conn,
    )
    return opened


@pytest.mark.parametrize("read_only", [True, False])
def test_ask_connects_read_only_for_every_target(ask_connections, read_only):
    from features.ask.engine.ask3.phases import execute as execute_phase

    for run, engine in ((execute_phase._execute_postgres, "postgres"),
                        (execute_phase._execute_mysql, "mysql")):
        result = run("SELECT id FROM users", {"engine": engine, "read_only": read_only}, 0, "db")
        assert result["success"]
        assert ask_connections[engine]["read_only"] is True


def test_analyze_connects_read_only(monkeypatch):
    from features.analyze.functions import explain_analysis

    captured = {}
    monkeypatch.setattr(explain_analysis, "resolve_connection_params",
                        lambda **kw: {"password": ""})
    monkeypatch.setattr(explain_analysis, "postgres_connection_kwargs",
                        lambda p, **kw: captured.update(kw) or {})

    def refuse(*_args):
        raise RuntimeError("no database here")

    monkeypatch.setattr(explain_analysis, "_postgres_connection", refuse)
    explain_analysis._execute_postgres_explain_analyze("SELECT 1", {}, fast_mode=True)

    assert captured["read_only"] is True


def test_read_only_kwargs_use_the_read_only_connection_class():
    from shared.agent_activity import pg_connection_class
    from shared.db_connection import (
        postgres_connection_kwargs,
        read_only_postgres_connection_class,
    )

    params = {"host": "h", "port": 5432, "user": "u", "password": "", "database": "d"}
    factory = postgres_connection_kwargs(params, read_only=True)["connection_factory"]

    assert factory is read_only_postgres_connection_class()
    # Statements on a read-only connection are still recorded in the agent activity log.
    assert issubclass(factory, pg_connection_class())
    assert "connection_factory" not in postgres_connection_kwargs(params)


@pytest.mark.parametrize(
    "target, flagged, requested, transactions, session",
    [
        # A target marked read_only gets one session setting: no per-statement round trips.
        ({}, True, False, False, True),
        # A caller running SQL it did not write asks for read-only transactions.
        ({}, False, True, True, False),
        ({}, True, True, True, True),
        # Readyset serves cached reads only outside transactions.
        ({"target_type": "readyset"}, False, True, False, True),
        ({}, False, False, False, False),
    ],
)
def test_read_only_routing(monkeypatch, target, flagged, requested, transactions, session):
    from shared import db_connection

    created, sessions = {}, []
    monkeypatch.setattr(db_connection, "resolve_connection_params", lambda **kw: {
        "engine": "postgresql", "host": "h", "port": 5432, "user": "u", "database": "d",
        "password": "pw", "password_env": None, "tls": False, "tls_verify": False,
        "tls_ca": None, "application_name": "rdst", "read_only": flagged,
    })
    monkeypatch.setattr(db_connection, "_create_postgres_connection",
                        lambda *a, **kw: created.update(kw) or object())
    monkeypatch.setattr(db_connection, "apply_read_only_session",
                        lambda conn, engine: sessions.append(engine))

    db_connection.create_direct_connection(
        {"engine": "postgresql", "read_only": flagged, **target}, read_only=requested)

    assert created["read_only"] is transactions
    assert sessions == (["postgresql"] if session else [])


class TestReadOnlyTransaction:
    """Opening and commit fall outside the enclosed statements and set nothing on the
    session."""

    def test_postgres(self):
        from shared.db_connection import read_only_transaction

        conn = _PgConn()
        with read_only_transaction(conn, "postgresql"):
            conn.cursor().execute("SELECT 1")
        with pytest.raises(RuntimeError):
            with read_only_transaction(conn, "postgresql"):
                conn.cursor().execute("SELECT fail")

        assert conn.log == ["BEGIN READ ONLY", "SELECT 1", "COMMIT",
                            "BEGIN READ ONLY", "SELECT fail", "ROLLBACK"]

    def test_mysql(self):
        from shared.db_connection import read_only_transaction

        conn = _MySQLConn()
        with read_only_transaction(conn, "mysql"):
            conn.cursor().execute("SELECT 1")

        assert conn.log == ["START TRANSACTION READ ONLY", "SELECT 1", "COMMIT"]

    def test_postgres_needs_autocommit(self):
        from shared.db_connection import read_only_transaction

        conn = _PgConn()
        conn.autocommit = False
        with pytest.raises(ValueError):
            with read_only_transaction(conn, "postgresql"):
                pass


class TestReadOnlyMySQLConnection:
    """Every statement on the connection runs in a read-only transaction."""

    @pytest.fixture
    def conn(self, monkeypatch):
        import pymysql.cursors

        from shared.db_connection import read_only_mysql_connection_class

        conn = read_only_mysql_connection_class()(defer_connect=True)
        conn.log = []
        conn.autocommit_mode = True
        conn.server_status = 0
        monkeypatch.setattr(conn, "query", conn.log.append, raising=False)
        monkeypatch.setattr(conn, "commit", lambda: conn.log.append("COMMIT"))
        monkeypatch.setattr(conn, "rollback", lambda: conn.log.append("ROLLBACK"))
        monkeypatch.setattr(conn, "get_autocommit", lambda: conn.autocommit_mode)

        class Base(pymysql.cursors.DictCursor):
            def execute(self, query, args=None):
                self.connection.log.append(query)

        conn.cursorclass = Base
        return conn

    def test_autocommit_statement_gets_its_own_transaction(self, conn):
        conn.cursor().execute("SELECT 1")

        assert conn.log == ["START TRANSACTION READ ONLY", "SELECT 1", "COMMIT"]

    def test_transaction_opens_read_only(self, conn):
        conn.autocommit_mode = False
        conn.cursor().execute("SELECT 1")
        conn.begin()

        assert conn.log == ["START TRANSACTION READ ONLY", "SELECT 1",
                            "START TRANSACTION READ ONLY"]

    def test_unbuffered_cursors_are_refused(self, conn):
        import pymysql.cursors

        with pytest.raises(TypeError):
            conn.cursor(pymysql.cursors.SSCursor)


def test_compare_reads_the_origin_read_only_and_leaves_readyset_alone(monkeypatch):
    from features.cache import performance_comparison

    opened = []
    monkeypatch.setattr(
        performance_comparison, "_open_persistent_connection",
        lambda config: (opened.append(config) or _PgConn(), "postgresql"),
    )
    monkeypatch.setattr(
        performance_comparison, "_execute_on_connection",
        lambda *a, **k: {"success": True, "execution_time_ms": 1.0},
    )
    result = performance_comparison.run_comparison(
        "SELECT 1", {"host": "origin"}, {"host": "readyset"}, iterations=1, warmup_iterations=0
    )

    assert result["success"]
    assert [config.get("read_only") for config in opened] == [True, None]


@pytest.mark.parametrize("engine, executed", [
    ("mysql", ["SET SESSION TRANSACTION READ ONLY"]),
    ("postgresql", []),
])
def test_compare_origin_session_is_read_only_once(monkeypatch, engine, executed):
    """The origin's session refuses writes; measured executions carry no extra round
    trips, so its throughput compares fairly with Readyset's."""
    import psycopg2
    import pymysql

    from features.cache import performance_comparison

    class Conn(_MySQLConn):
        autocommit = False
        session = None

        def set_session(self, **kwargs):
            self.session = kwargs

    conn = Conn()
    monkeypatch.setattr(psycopg2, "connect", lambda **_kwargs: conn)
    monkeypatch.setattr(pymysql, "connect", lambda **_kwargs: conn)

    performance_comparison._open_persistent_connection(
        performance_comparison.origin_config({"engine": engine})
    )
    performance_comparison._execute_on_connection(conn, "SELECT 1", engine)

    assert conn.log == executed + ["SELECT 1"]
    assert conn.session == (None if engine == "mysql" else {"readonly": True})


# Switches the session's read-only setting off on one run so later runs of the same
# statement on that session write.
TURN_OFF_READ_ONLY = (
    "SELECT CASE WHEN current_setting('transaction_read_only')='on' "
    "THEN set_config('default_transaction_read_only','off',false) "
    "ELSE my_writing_udf()::text END"
)


class TestSessionSettingsStayPut:
    """Benchmark, speed-test and `query run` sessions are read-only at the session level
    and run each statement repeatedly, so a statement may not change session settings."""

    @pytest.mark.parametrize("dialect", ["", "postgresql"])
    @pytest.mark.parametrize("sql", [
        TURN_OFF_READ_ONLY,
        "SELECT pg_catalog.\"set_config\"('default_transaction_read_only', 'off', false)",
    ])
    def test_gates_refuse_session_setters(self, sql, dialect):
        assert "set_config" in (benchmark_read_only_reason(sql, dialect) or "")
        assert "set_config" in (benchmark_execution_reason(sql, dialect) or "")

    def test_reading_a_setting_is_fine(self):
        assert benchmark_read_only_reason(
            "SELECT current_setting('transaction_read_only')", "postgresql") is None

    def test_query_run_refuses_before_connecting(self, monkeypatch):
        from features.query_registry.cli.command import QueryCommand

        class Targets:
            def load(self):
                pass

            def get(self, name):
                return {"engine": "postgresql", "read_only": True}

            def get_default(self):
                return "db"

        connected = []
        monkeypatch.setattr("shared.config.targets.create_targets_config", lambda: Targets())
        monkeypatch.setattr("shared.db_connection.create_direct_connection",
                            lambda *a, **k: connected.append(a))
        command = QueryCommand.__new__(QueryCommand)
        monkeypatch.setattr(command, "_resolve_queries",
                            lambda specs: [(object(), TURN_OFF_READ_ONLY)], raising=False)

        result = command.run(["q"], target="db", count=1, quiet=True, skip_warning=True)

        assert not result.ok
        assert "set_config" in result.message
        assert connected == []


class TestSpeedTestGate:
    """The cache-compare gate reads quoting as the server does."""

    @pytest.mark.parametrize(
        "sql",
        [
            r"SELECT '\'; DELETE FROM users; -- '",
            r"SELECT E'\''; DELETE FROM users; COMMIT; SELECT '1'",
            r"WITH x AS (SELECT '\'), y AS (DELETE FROM users RETURNING 1) SELECT 1 -- '",
        ],
    )
    def test_backslash_reading_cannot_smuggle_a_statement(self, sql):
        assert benchmark_read_only_reason(sql) is not None

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT pg_terminate_backend(42)",
            "SELECT pg_catalog.\"pg_sleep\"(100)",
            "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)",
        ],
    )
    def test_calls_a_read_only_session_allows_are_refused(self, sql):
        assert benchmark_execution_reason(sql) is not None

    def test_ordinary_query_runs(self):
        assert benchmark_execution_reason(
            "SELECT o.id, replace(o.note, ';', ',') FROM orders o WHERE o.id = 1"
        ) is None


class TestAskValidation:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT set_config('statement_timeout', '0', false)",
            "SELECT lo_unlink(16384)",
            "SELECT pg_catalog.\"pg_read_file\"('/etc/passwd')",
            r"SELECT E'\''; DELETE FROM users; COMMIT; SELECT '1'",
        ],
    )
    def test_refused(self, sql):
        assert validate_sql_for_ask(sql, dialect="postgresql")["is_valid"] is False

    @pytest.mark.parametrize(
        "sql, dialect",
        [
            (r"SELECT id FROM notes WHERE body LIKE E'%a;b\'c%'", "postgresql"),
            ("SELECT id FROM notes WHERE body = $$x; DROP$$", "postgresql"),
            ("SELECT id FROM notes WHERE body = 'O\\'Brien'", "mysql"),
        ],
    )
    def test_quoted_semicolons_and_keywords_are_text(self, sql, dialect):
        assert validate_sql_for_ask(sql, dialect=dialect)["is_valid"] is True
