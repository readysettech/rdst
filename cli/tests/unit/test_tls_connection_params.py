import ssl
import sys
import types
from unittest.mock import MagicMock, patch

try:
    import pymysql
except ModuleNotFoundError:  # driver-free CI environments
    pymysql = types.ModuleType("pymysql")
    pymysql.err = types.ModuleType("pymysql.err")

    class _OperationalError(Exception):
        def __init__(self, *args):
            super().__init__(*args)
            self.args = args

    pymysql.err.OperationalError = _OperationalError
    pymysql.connect = MagicMock(name="pymysql.connect")
    pymysql.cursors = types.ModuleType("pymysql.cursors")
    pymysql.cursors.DictCursor = type("DictCursor", (), {})
    sys.modules["pymysql"] = pymysql
    sys.modules["pymysql.err"] = pymysql.err
    sys.modules["pymysql.cursors"] = pymysql.cursors

try:
    import psycopg2
except ModuleNotFoundError:  # driver-free CI environments
    psycopg2 = types.ModuleType("psycopg2")

    class _PgOperationalError(Exception):
        pass

    psycopg2.OperationalError = _PgOperationalError
    psycopg2.connect = MagicMock(name="psycopg2.connect")
    psycopg2.extras = types.ModuleType("psycopg2.extras")
    psycopg2.extras.RealDictCursor = type("RealDictCursor", (), {})
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = psycopg2.extras

from shared.api.ssh_errors import connectivity_error_payload
from shared.db_connection import (
    create_direct_connection,
    mysql_ssl_kwargs,
    postgres_ssl_kwargs,
)


def test_postgres_verified_tls_passes_root_certificate():
    assert postgres_ssl_kwargs(
        {
            "sslmode": "verify-full",
            "tls_verify": True,
            "tls_ca": "/certs/root.pem",
        }
    ) == {"sslmode": "verify-full", "sslrootcert": "/certs/root.pem"}


def test_mysql_verified_tls_builds_hostname_checking_context():
    context = MagicMock()
    context.verify_flags = ssl.VERIFY_X509_STRICT
    with patch("ssl.create_default_context", return_value=context) as create_context:
        result = mysql_ssl_kwargs(
            {"tls": True, "tls_verify": True, "tls_ca": "/certs/root.pem"}
        )

    create_context.assert_called_once_with(cafile="/certs/root.pem")
    assert result == {"ssl": context}
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_create_direct_connection_plumbs_tls_verification():
    with patch("shared.db_connection._create_mysql_connection") as connect:
        create_direct_connection(
            {
                "engine": "mysql",
                "host": "db.example.com",
                "port": 3306,
                "user": "app",
                "password": "password",
                "database": "app",
                "tls": True,
                "tls_verify": True,
                "tls_ca": "/certs/root.pem",
            }
        )

    assert connect.call_args.kwargs["tls_verify"] is True
    assert connect.call_args.kwargs["tls_ca"] == "/certs/root.pem"


def test_postgres_ssh_verify_splits_tls_host_from_socket_endpoint():
    config = {
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5432,
        "user": "app",
        "password": "password",
        "database": "app",
        "tls_verify": True,
        "tls_ca": "/certs/root.pem",
        "ssh": {"host": "jump.example.com"},
    }

    with (
        patch("shared.ssh_tunnel.get_tunnel_manager") as manager,
        patch("psycopg2.connect") as connect,
    ):
        manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49152)
        create_direct_connection(config, target="private-db")

    assert connect.call_args.kwargs == {
        "host": "database.internal",
        "hostaddr": "127.0.0.1",
        "port": 49152,
        "user": "app",
        "password": "password",
        "database": "app",
        "connect_timeout": 10,
        "sslmode": "verify-full",
        "sslrootcert": "/certs/root.pem",
    }


def test_postgres_non_ssh_verify_keeps_existing_endpoint_kwargs():
    config = {
        "engine": "postgresql",
        "host": "database.internal",
        "port": 5432,
        "user": "app",
        "password": "password",
        "database": "app",
        "tls_verify": True,
        "tls_ca": "/certs/root.pem",
    }

    with patch("psycopg2.connect") as connect:
        create_direct_connection(config)

    assert connect.call_args.kwargs["host"] == "database.internal"
    assert "hostaddr" not in connect.call_args.kwargs
    assert connect.call_args.kwargs["sslmode"] == "verify-full"
    assert connect.call_args.kwargs["sslrootcert"] == "/certs/root.pem"


def test_mysql_ssh_verify_preconnects_tunnel_socket_with_real_tls_hostname():
    config = {
        "engine": "mysql",
        "host": "database.internal",
        "port": 3306,
        "user": "app",
        "password": "password",
        "database": "app",
        "tls_verify": True,
        "tls_ca": "/certs/root.pem",
        "ssh": {"host": "jump.example.com"},
    }
    raw_socket = MagicMock()
    connection = MagicMock()
    context = MagicMock()
    context.verify_flags = ssl.VERIFY_X509_STRICT

    with (
        patch("shared.ssh_tunnel.get_tunnel_manager") as manager,
        patch(
            "shared.db_connection.socket.create_connection",
            return_value=raw_socket,
        ) as create_socket,
        patch("ssl.create_default_context", return_value=context),
        patch("pymysql.connect", return_value=connection) as connect,
    ):
        manager.return_value.ensure_tunnel.return_value = ("127.0.0.1", 49153)
        create_direct_connection(config, target="private-db")

    create_socket.assert_called_once_with(("127.0.0.1", 49153), 10)
    assert connect.call_args.kwargs["host"] == "database.internal"
    assert connect.call_args.kwargs["port"] == 49153
    assert connect.call_args.kwargs["defer_connect"] is True
    assert connect.call_args.kwargs["ssl"] is context
    connection.connect.assert_called_once_with(sock=raw_socket)


def test_mysql_non_ssh_verify_connects_normally_to_real_host():
    config = {
        "engine": "mysql",
        "host": "database.internal",
        "port": 3306,
        "user": "app",
        "password": "password",
        "database": "app",
        "tls_verify": True,
    }
    context = MagicMock()
    context.verify_flags = ssl.VERIFY_X509_STRICT

    with (
        patch("ssl.create_default_context", return_value=context),
        patch("pymysql.connect") as connect,
    ):
        create_direct_connection(config)

    assert connect.call_args.kwargs["host"] == "database.internal"
    assert "defer_connect" not in connect.call_args.kwargs
    assert connect.call_args.kwargs["ssl"] is context


def test_psycopg_certificate_failure_maps_to_actionable_category():
    import psycopg2

    error = psycopg2.OperationalError(
        'server certificate for "db.example.com" does not match host name "wrong.example.com"'
    )
    payload = connectivity_error_payload(error, "prod", {"engine": "postgresql"})

    assert payload["category"] == "tls_verification_failed"
    assert "could not be verified" in payload["message"]
    assert "CA bundle" in payload["message"]
    assert "tls_ca" in payload["message"]
    assert "Cloud providers publish" in payload["message"]


def test_pymysql_wrapped_certificate_failure_maps_to_actionable_category():
    error = pymysql.err.OperationalError(2003, "Can't connect to MySQL server")
    error.original_exception = ssl.SSLCertVerificationError(
        "certificate verify failed: unable to get local issuer certificate"
    )

    payload = connectivity_error_payload(error, "prod", {"engine": "mysql"})

    assert payload["category"] == "tls_verification_failed"
    assert "could not be verified" in payload["message"]
    assert "tls_ca" in payload["message"]
