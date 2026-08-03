"""Bounded write-privilege detection for connection-test trust messaging."""

from __future__ import annotations

import re
from typing import Any


_MYSQL_WRITE_PRIVILEGE = re.compile(
    r"\b(ALL(?:\s+PRIVILEGES)?|INSERT|UPDATE|DELETE|DROP|ALTER)\b",
    re.IGNORECASE,
)
_MYSQL_GRANT_CLAUSE = re.compile(r"^\s*GRANT\s+(.+?)\s+ON\s", re.IGNORECASE)


def _first_value(row: Any) -> Any:
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    if isinstance(row, (tuple, list)):
        return row[0] if row else None
    return row


def _postgres_privileges(connection: Any) -> dict[str, Any]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user"
        )
        if bool(_first_value(cursor.fetchone())):
            return {
                "writable": True,
                "evidence": "PostgreSQL role is a superuser.",
            }

        cursor.execute(
            """
            WITH sample AS (
                SELECT
                    format('%I.%I', namespace.nspname, relation.relname) AS table_name,
                    relation.relowner
                FROM pg_catalog.pg_class AS relation
                JOIN pg_catalog.pg_namespace AS namespace
                    ON namespace.oid = relation.relnamespace
                WHERE relation.relkind IN ('r', 'p')
                  AND namespace.nspname NOT IN ('pg_catalog', 'information_schema')
                  AND namespace.nspname !~ '^pg_toast'
                ORDER BY namespace.nspname, relation.relname
                LIMIT 20
            )
            SELECT EXISTS (
                SELECT 1
                FROM sample
                WHERE has_table_privilege(table_name, 'INSERT')
                   OR has_table_privilege(table_name, 'UPDATE')
                   OR has_table_privilege(table_name, 'DELETE')
                   OR has_table_privilege(table_name, 'TRUNCATE')
                   OR has_table_privilege(table_name, 'REFERENCES')
                   OR has_table_privilege(table_name, 'TRIGGER')
                   OR pg_has_role(relowner, 'USAGE')
            )
            """
        )
        if bool(_first_value(cursor.fetchone())):
            return {
                "writable": True,
                "evidence": "Write or table-owner privileges found on sampled user tables.",
            }

        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_namespace
                WHERE nspname NOT IN ('pg_catalog', 'information_schema')
                  AND nspname !~ '^pg_toast'
                  AND has_schema_privilege(oid, 'CREATE')
                LIMIT 1
            )
            """
        )
        if bool(_first_value(cursor.fetchone())):
            return {
                "writable": True,
                "evidence": "CREATE privilege found on a user schema.",
            }
        return {
            "writable": False,
            "evidence": "No write privileges found in a bounded PostgreSQL check.",
        }
    finally:
        cursor.close()


def _mysql_privileges(connection: Any) -> dict[str, Any]:
    cursor = connection.cursor()
    try:
        cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
        grants = [str(_first_value(row) or "") for row in cursor.fetchall()]
    finally:
        cursor.close()

    privilege_clauses = [
        match.group(1)
        for grant in grants
        if (match := _MYSQL_GRANT_CLAUSE.search(grant))
    ]
    verbs = sorted(
        {
            match.group(1).upper().replace(" PRIVILEGES", "")
            for clause in privilege_clauses
            for match in _MYSQL_WRITE_PRIVILEGE.finditer(clause)
        }
    )
    if verbs:
        return {
            "writable": True,
            "evidence": f"MySQL grants include: {', '.join(verbs)}.",
        }
    return {
        "writable": False,
        "evidence": "No write privileges found in SHOW GRANTS.",
    }


def detect_write_privileges(connection: Any, engine: str) -> dict[str, Any]:
    """Return a conservative, short write-privilege verdict for a live connection."""
    try:
        if (engine or "").lower() in {"postgres", "postgresql", "psql"}:
            return _postgres_privileges(connection)
        if (engine or "").lower() in {"mysql", "mariadb"}:
            return _mysql_privileges(connection)
        return {
            "writable": False,
            "evidence": f"Privilege detection is unavailable for engine '{engine}'.",
        }
    except Exception as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        detail = str(exc).splitlines()[0][:100]
        return {
            "writable": False,
            "evidence": f"Privilege check unavailable: {detail}",
        }


__all__ = ["detect_write_privileges"]
