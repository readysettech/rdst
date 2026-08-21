"""Lane self-identification on RDST-initiated database connections.

Every connection RDST opens against a target carries an application name
(PostgreSQL application_name, MySQL program_name connection attribute) so
activity views can tell RDST's own sessions apart from production traffic.
"""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from features.top.command_sets import TOP_COMMAND_SETS
from shared.db_connection import (
    DEFAULT_LANE,
    create_direct_connection,
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    probe_readyset_status,
    resolve_application_name,
    resolve_connection_params,
)


PG_CONFIG = {
    "engine": "postgresql",
    "host": "db.example.com",
    "port": 5432,
    "user": "app",
    "password": "password",
    "database": "app",
}

MYSQL_CONFIG = {
    "engine": "mysql",
    "host": "db.example.com",
    "port": 3306,
    "user": "app",
    "password": "password",
    "database": "app",
}


def test_resolved_params_default_to_generic_rdst_lane():
    params = resolve_connection_params(target_config=dict(PG_CONFIG))
    assert params["application_name"] == DEFAULT_LANE
    assert DEFAULT_LANE.startswith("rdst/")


def test_resolved_params_carry_explicit_lane():
    params = resolve_connection_params(
        target_config=dict(PG_CONFIG), lane="rdst/ask"
    )
    assert params["application_name"] == "rdst/ask"


def test_user_configured_application_name_is_not_overridden():
    config = dict(PG_CONFIG, application_name="customer-tag")
    params = resolve_connection_params(target_config=config, lane="rdst/ask")
    assert params["application_name"] == "customer-tag"


def test_postgres_connection_kwargs_include_application_name():
    params = resolve_connection_params(
        target_config=dict(PG_CONFIG), lane="rdst/analyze"
    )
    kwargs = postgres_connection_kwargs(params)
    assert kwargs["application_name"] == "rdst/analyze"


def test_postgres_connection_kwargs_default_when_params_lack_tag():
    kwargs = postgres_connection_kwargs(
        {
            "host": "h",
            "port": 5432,
            "user": "u",
            "password": "p",
            "database": "d",
        }
    )
    assert kwargs["application_name"] == DEFAULT_LANE


def test_create_direct_connection_tags_postgres_with_lane():
    with patch("psycopg2.connect") as connect:
        create_direct_connection(dict(PG_CONFIG), lane="rdst/loadtest")
    assert connect.call_args.kwargs["application_name"] == "rdst/loadtest"


def test_create_direct_connection_bounds_postgres_queries():
    with patch("psycopg2.connect") as connect:
        create_direct_connection(
            dict(PG_CONFIG), query_timeout_seconds=3
        )
    assert connect.call_args.kwargs["options"] == "-c statement_timeout=3000"


def test_create_direct_connection_tags_mysql_program_name():
    with patch("pymysql.connect", return_value=MagicMock()) as connect:
        create_direct_connection(dict(MYSQL_CONFIG), lane="rdst/audit")
    assert connect.call_args.kwargs["program_name"] == "rdst/audit"


def test_create_direct_connection_bounds_mysql_reads_and_writes():
    with patch("pymysql.connect", return_value=MagicMock()) as connect:
        create_direct_connection(
            dict(MYSQL_CONFIG), query_timeout_seconds=3
        )
    assert connect.call_args.kwargs["read_timeout"] == 3
    assert connect.call_args.kwargs["write_timeout"] == 3


def test_postgres_readiness_probe_has_client_side_io_deadline():
    from psycopg2 import extensions

    connection = MagicMock()
    connection.poll.return_value = extensions.POLL_READ
    connection.fileno.return_value = 17
    with (
        patch("psycopg2.connect", return_value=connection) as connect,
        patch("shared.db_connection.select.select", return_value=([], [], [])),
        pytest.raises(TimeoutError, match="readiness probe timed out"),
    ):
        probe_readyset_status(dict(PG_CONFIG), timeout_seconds=0.01)

    assert connect.call_args.kwargs["async_"] is True
    connection.close.assert_called_once_with()


def test_postgres_readiness_probe_times_out_against_silent_real_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    release = threading.Event()

    def silent_server():
        connection, _address = listener.accept()
        try:
            release.wait(timeout=2)
        finally:
            connection.close()

    server = threading.Thread(target=silent_server, daemon=True)
    server.start()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="readiness probe timed out"):
            probe_readyset_status(
                dict(PG_CONFIG, host="127.0.0.1", port=listener.getsockname()[1]),
                timeout_seconds=0.05,
            )
        assert time.monotonic() - started < 1
    finally:
        release.set()
        listener.close()
        server.join(timeout=2)
    assert server.is_alive() is False


def test_mysql_readiness_probe_uses_bounded_connection_and_query():
    connection = MagicMock()
    with patch(
        "shared.db_connection.create_direct_connection",
        return_value=connection,
    ) as create:
        probe_readyset_status(dict(MYSQL_CONFIG), timeout_seconds=2)

    assert create.call_args.kwargs["connect_timeout"] == 2
    assert create.call_args.kwargs["query_timeout_seconds"] == 2
    cursor = connection.cursor.return_value
    cursor.execute.assert_called_once_with("SHOW READYSET STATUS")
    cursor.fetchall.assert_called_once_with()
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_mysql_readiness_probe_preserves_subsecond_deadline():
    connection = MagicMock()
    with patch(
        "shared.db_connection.create_direct_connection",
        return_value=connection,
    ) as create:
        probe_readyset_status(dict(MYSQL_CONFIG), timeout_seconds=0.05)

    assert create.call_args.kwargs["connect_timeout"] == 0.05
    assert create.call_args.kwargs["query_timeout_seconds"] == 0.05


def test_mysql_readiness_probe_times_out_against_silent_real_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    release = threading.Event()

    def silent_server():
        connection, _address = listener.accept()
        try:
            release.wait(timeout=2)
        finally:
            connection.close()

    server = threading.Thread(target=silent_server, daemon=True)
    server.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="Failed to connect to MySQL"):
            probe_readyset_status(
                dict(
                    MYSQL_CONFIG,
                    host="127.0.0.1",
                    port=listener.getsockname()[1],
                ),
                timeout_seconds=0.05,
            )
        assert time.monotonic() - started < 1
    finally:
        release.set()
        listener.close()
        server.join(timeout=2)
    assert server.is_alive() is False


def test_create_direct_connection_defaults_untagged_callers():
    with patch("psycopg2.connect") as connect:
        create_direct_connection(dict(PG_CONFIG))
    assert connect.call_args.kwargs["application_name"] == DEFAULT_LANE


def test_mysql_from_params_uses_resolved_application_name():
    params = resolve_connection_params(
        target_config=dict(MYSQL_CONFIG), lane="rdst/observe"
    )
    with patch("pymysql.connect", return_value=MagicMock()) as connect:
        create_mysql_connection_from_params(params)
    assert connect.call_args.kwargs["program_name"] == "rdst/observe"


def test_mysql_from_params_keeps_explicit_program_name_override():
    params = resolve_connection_params(
        target_config=dict(MYSQL_CONFIG), lane="rdst/observe"
    )
    with patch("pymysql.connect", return_value=MagicMock()) as connect:
        create_mysql_connection_from_params(params, program_name="custom")
    assert connect.call_args.kwargs["program_name"] == "custom"


def test_resolve_application_name_precedence():
    assert resolve_application_name("customer-tag", "rdst/ask") == "customer-tag"
    assert resolve_application_name(None, "rdst/ask") == "rdst/ask"
    assert resolve_application_name(None, None) == DEFAULT_LANE


def _connect_data_manager(tmp_path, db_type, application_name):
    from shared.data_manager import ConnectionConfig, DataManager
    from shared.data_manager_service import DataManagerQueryType

    connection_config = ConnectionConfig(
        host="db.example.com",
        port=5432,
        database="app",
        username="app",
        password="password",
        db_type=db_type,
        application_name=application_name,
    )
    DataManager(
        connection_config={DataManagerQueryType.UPSTREAM: connection_config},
        global_logger=MagicMock(),
        command_sets=[],
        available_commands={},
        data_directory=str(tmp_path),
        cli_mode=True,
    )


def test_data_manager_pg_keeps_user_configured_application_name(tmp_path):
    from shared.data_manager_service import DMSDbType

    with patch("psycopg2.connect") as connect:
        _connect_data_manager(tmp_path, DMSDbType.PostgreSQL, "customer-tag")
    assert connect.call_args.kwargs["application_name"] == "customer-tag"


def test_data_manager_mysql_keeps_user_configured_application_name(tmp_path):
    from shared.data_manager_service import DMSDbType

    with patch("pymysql.connect", return_value=MagicMock()) as connect:
        _connect_data_manager(tmp_path, DMSDbType.MySql, "customer-tag")
    assert connect.call_args.kwargs["program_name"] == "customer-tag"


def test_data_manager_carries_lane_when_no_custom_name(tmp_path):
    from shared.data_manager_service import DMSDbType

    params = resolve_connection_params(
        target_config=dict(PG_CONFIG), lane="rdst/observe"
    )
    with patch("psycopg2.connect") as connect:
        _connect_data_manager(
            tmp_path, DMSDbType.PostgreSQL, params["application_name"]
        )
    assert connect.call_args.kwargs["application_name"] == "rdst/observe"


def test_data_manager_defaults_untagged_configs(tmp_path):
    from shared.data_manager_service import DMSDbType

    with patch("psycopg2.connect") as connect:
        _connect_data_manager(tmp_path, DMSDbType.PostgreSQL, None)
    assert connect.call_args.kwargs["application_name"] == DEFAULT_LANE


def test_data_manager_source_has_no_hardcoded_observe_lane():
    import inspect

    import shared.data_manager.data_manager as data_manager_module

    assert "rdst/observe" not in inspect.getsource(data_manager_module)


def test_pg_activity_query_excludes_rdst_sessions():
    sql = TOP_COMMAND_SETS["rdst_top_pg_activity"]["commands"][
        "pg_activity_queries"
    ]["query"]
    assert "COALESCE(application_name, '') NOT LIKE 'rdst/%'" in sql
    assert "pid != pg_backend_pid()" in sql


def test_mysql_activity_query_excludes_rdst_sessions():
    sql = TOP_COMMAND_SETS["rdst_top_mysql_activity"]["commands"][
        "mysql_activity_queries"
    ]["query"]
    assert "performance_schema.session_account_connect_attrs" in sql
    assert "ATTR_VALUE LIKE 'rdst/%'" in sql
    assert "ID != CONNECTION_ID()" in sql


def test_historical_statement_queries_are_unchanged():
    pg_stat_sql = TOP_COMMAND_SETS["rdst_top_pg_stat"]["commands"][
        "pg_stat_queries"
    ]["query"]
    digest_sql = TOP_COMMAND_SETS["rdst_top_mysql_digest"]["commands"][
        "mysql_digest_queries"
    ]["query"]
    assert "application_name" not in pg_stat_sql
    assert "connect_attrs" not in digest_sql
