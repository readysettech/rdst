"""Index reads for the timeline. Index changes are facts on a chart, never evidence."""

from __future__ import annotations

import re
from typing import Any


PG_INDEX_SQL = ("/*rdst:observe*/ SELECT schemaname,tablename,indexname,indexdef "
                "FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema')")
MYSQL_INDEX_SQL = ("/*rdst:observe*/ SELECT TABLE_SCHEMA,TABLE_NAME,INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX "
                   "FROM information_schema.statistics WHERE TABLE_SCHEMA=DATABASE() "
                   "ORDER BY TABLE_SCHEMA,TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX")


class IndexesBusy(Exception):
    """A lock on a table held up the index read; the next pass tries again."""


def read_indexes(connection: Any, engine: str) -> list[dict[str, str]]:
    cursor = connection.cursor()
    try:
        if engine == "postgresql":
            # pg_indexes waits behind an ACCESS EXCLUSIVE lock (a migration, a LOCK TABLE).
            # A transaction-scoped lock_timeout gives up instead of stalling monitoring.
            cursor.execute("BEGIN")
            try:
                cursor.execute("SET LOCAL lock_timeout = '1s'")
                cursor.execute(PG_INDEX_SQL)
                rows = cursor.fetchall()
                cursor.execute("COMMIT")
            except Exception as exc:
                cursor.execute("ROLLBACK")
                if getattr(exc, "pgcode", None) == "55P03":
                    raise IndexesBusy(str(exc)) from exc
                raise
        else:
            cursor.execute(MYSQL_INDEX_SQL)
            rows = cursor.fetchall()
    finally:
        cursor.close()
    if engine == "postgresql":
        return [{"schema": row[0], "table": row[1], "index_name": row[2],
                 "definition": row[3]} for row in rows]
    grouped: dict[tuple[str, str, str], list[tuple[int, str]]] = {}
    for schema, table, name, column, ordinal in rows:
        grouped.setdefault((schema, table, name), []).append((int(ordinal), column or ""))
    return [{"schema": key[0], "table": key[1], "index_name": key[2],
             "definition": "(" + ", ".join(column for _, column in sorted(columns)) + ")"}
            for key, columns in grouped.items()]


def index_columns(definition: str) -> list[str]:
    # pg_indexes supplies CREATE INDEX ... USING btree (columns); MySQL uses
    # the same parenthesized canonical column list assembled above.
    match = re.search(r"\(([^)]*)\)", definition)
    if not match:
        return []
    return [part.strip().split()[0].strip('`"').lower() for part in match.group(1).split(",")]


def index_shape(definition: str) -> dict[str, Any]:
    """Key columns, whether the index is partial, and its INCLUDE columns, from a definition.

    Parentheses are balanced, so an expression such as lower((email)::text) stays whole.
    """
    text = definition or ""
    using = re.search(r"\bUSING\s+\w+\s*\(", text, re.I)
    start = using.end() - 1 if using else text.find("(")
    if start < 0:
        return {"columns": [], "partial": False, "include": []}

    def group(at: int) -> tuple[list[str], int]:
        depth, parts, current = 0, [], ""
        for position in range(at, len(text)):
            char = text[position]
            if char == "(":
                depth += 1
                if depth == 1:
                    continue
            elif char == ")":
                depth -= 1
                if depth == 0:
                    parts.append(current)
                    return parts, position
            elif char == "," and depth == 1:
                parts.append(current)
                current = ""
                continue
            current += char
        return parts, len(text)

    def column(part: str) -> str:
        part = part.strip()
        if "(" in part:
            return re.sub(r"\s+(ASC|DESC)(\s+NULLS\s+(FIRST|LAST))?$", "", part, flags=re.I).lower()
        return part.split()[0].strip('`"').lower()

    keys, end = group(start)
    rest = text[end + 1:]
    include = []
    included = re.match(r"\s*INCLUDE\s*\(([^)]*)\)", rest, re.I)
    if included:
        include = [part.strip().strip('"').lower() for part in included.group(1).split(",")]
    return {"columns": [column(part) for part in keys if part.strip()],
            "partial": bool(re.search(r"\bWHERE\b", rest, re.I)), "include": include}
