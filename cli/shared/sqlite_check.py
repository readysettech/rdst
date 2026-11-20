"""PRAGMA quick_check, without SQLite 3.40's false NOT NULL reports.

SQLite 3.40.x (Debian 12, and the Python images built on it) reports
"NULL value in <table>.<column>" for STRICT WITHOUT ROWID tables whose rows hold
no NULL at all; 3.46 reports "ok" on the same file. Those reports are confirmed
with a direct read, so a healthy store is never rebuilt or refused over them.
"""

import logging
import re
import sqlite3

logger = logging.getLogger(__name__)

_NULL_REPORT = re.compile(r"NULL value in ([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")


def quick_check(conn: sqlite3.Connection) -> str:
    """'ok', or the first problem quick_check found that a direct read confirms."""
    problems = [row[0] for row in conn.execute("PRAGMA quick_check").fetchall()]
    if problems == ["ok"]:
        return "ok"
    for problem in problems:
        match = _NULL_REPORT.fullmatch(problem)
        if match is None or _has_null(conn, *match.groups()):
            return problem
    logger.info("quick_check reported NULLs that a direct read doesn't find (SQLite %s); "
                "treating the file as healthy", sqlite3.sqlite_version)
    return "ok"


def _has_null(conn: sqlite3.Connection, table: str, column: str) -> bool:
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if column not in columns:
        return True
    # typeof(), because SQLite folds "IS NULL" on a NOT NULL column to false.
    return conn.execute(
        f'SELECT 1 FROM "{table}" WHERE typeof("{column}") = \'null\' LIMIT 1'
    ).fetchone() is not None
