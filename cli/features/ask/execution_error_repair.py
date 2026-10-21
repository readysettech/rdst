"""Decide which database errors Ask may send back to the model for one repair.

A repair qualifies only when the database rejected the statement itself: its syntax,
the names it references, its grouping, its types or its data handling. Errors that
describe the session rather than the statement never qualify, because a rewritten
query fails the same way or the retry would hide the real problem from the user:
connection loss, missing privileges (including MySQL's EXECUTE denial for an unknown
routine), statement timeouts and cancels, locks and deadlocks, server resources and
shutdown, Ask's own refusals, and any error without a recognised code.
"""

from __future__ import annotations

import re

VERSION = "database-error-model-repair-v1"
# Longest database error text the repair prompt carries.
MAX_ERROR_CHARS = 2000
# Opens the repair feedback, which sets the repair apart from validation repairs.
FEEDBACK_PREFIX = "The database rejected this SQL when it ran it: "

# MySQL error numbers for statements the server parsed or planned and rejected.
_MYSQL_STATEMENT_ERRORS = frozenset(
    {
        1052,  # ambiguous column
        1054,  # unknown column
        1055,  # column not in GROUP BY (ONLY_FULL_GROUP_BY)
        1056,  # cannot group on a column
        1060,  # duplicate column name in a derived table
        1064,  # syntax error
        1066,  # table or alias not unique
        1111,  # invalid use of a group function
        1140,  # aggregate mixed with plain columns without GROUP BY
        1146,  # table does not exist
        1149,  # syntax error
        1221,  # incorrect usage of UNION, ORDER BY or LIMIT
        1222,  # UNION branches with different column counts
        1235,  # unsupported construct such as LIMIT in an IN subquery
        1241,  # operand has the wrong number of columns
        1242,  # scalar subquery returned more than one row
        1247,  # reference to a group function
        1248,  # derived table without an alias
        1250,  # table not allowed in a global ORDER clause
        1267,  # illegal mix of collations
        1270,  # illegal mix of collations
        1271,  # illegal mix of collations
        1292,  # truncated incorrect value
        1305,  # function does not exist
        1318,  # wrong number of arguments for a function
        1582,  # wrong parameter count for a native function
        1583,  # wrong parameters for a native function
        1630,  # function does not exist
        1690,  # value out of range
        3065,  # ORDER BY column not in the DISTINCT select list
        3593,  # window function used in an invalid context
    }
)
# PostgreSQL SQLSTATEs: data exceptions, cardinality violations, unsupported features,
# and syntax or rule violations other than missing privileges.
_POSTGRES_STATEMENT_CLASSES = ("22", "42")
_POSTGRES_STATEMENT_STATES = frozenset({"21000", "0A000"})
_POSTGRES_PRIVILEGE_STATE = "42501"
# Kinds an executor gives errors that may come from the statement; others never qualify.
_STATEMENT_ERROR_KINDS = frozenset({None, "query_execution"})
_MYSQL_ERROR_PREFIX = re.compile(r"^\((\d{4,5}),")


def qualifies(
    error: str | None,
    error_kind: str | None,
    error_code: str | None,
    db_type: str | None,
) -> bool:
    """Whether the database rejected the statement for a reason a rewrite can fix."""
    if not error or error_kind not in _STATEMENT_ERROR_KINDS:
        return False
    engine = (db_type or "").lower()
    if "mysql" in engine:
        match = _MYSQL_ERROR_PREFIX.match(error)
        code = error_code or (match.group(1) if match else None)
        return (
            code is not None and code.isdigit() and int(code) in _MYSQL_STATEMENT_ERRORS
        )
    if "postgres" in engine:
        if not error_code or error_code == _POSTGRES_PRIVILEGE_STATE:
            return False
        return (
            error_code[:2] in _POSTGRES_STATEMENT_CLASSES
            or error_code in _POSTGRES_STATEMENT_STATES
        )
    return False


def repair_feedback(error: str) -> str:
    """The database error as the repair prompt states it."""
    text = error.strip()
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + "..."
    return FEEDBACK_PREFIX + text
