from __future__ import annotations

from features.configure.privileges import detect_write_privileges


class FakeCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.current = None

    def execute(self, query):
        self.current = next(self.rows)

    def fetchone(self):
        return self.current[0]

    def fetchall(self):
        return self.current

    def close(self):
        pass


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)


def test_postgres_superuser_is_writable():
    result = detect_write_privileges(FakeConnection([[(True,)]]), "postgresql")

    assert result == {
        "writable": True,
        "evidence": "PostgreSQL role is a superuser.",
    }


def test_postgres_read_only_role_is_not_writable():
    result = detect_write_privileges(
        FakeConnection([[(False,)], [(False,)], [(False,)]]), "postgresql"
    )

    assert result["writable"] is False
    assert "bounded PostgreSQL" in result["evidence"]


def test_mysql_write_grants_are_detected():
    result = detect_write_privileges(
        FakeConnection(
            [[("GRANT SELECT, INSERT, UPDATE ON `app`.* TO `rdst`@`%`",)]]
        ),
        "mysql",
    )

    assert result["writable"] is True
    assert "INSERT" in result["evidence"]
    assert "UPDATE" in result["evidence"]


def test_mysql_select_only_is_not_writable():
    result = detect_write_privileges(
        FakeConnection([[('GRANT SELECT ON `app`.* TO `rdst`@`%`',)]]),
        "mysql",
    )

    assert result["writable"] is False


def test_mysql_database_named_all_does_not_look_like_all_privileges():
    result = detect_write_privileges(
        FakeConnection([[('GRANT SELECT ON `all`.* TO `rdst`@`%`',)]]),
        "mysql",
    )

    assert result["writable"] is False
