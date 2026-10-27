"""
Query Safety Validation for RDST Analyze

Validates that SQL queries are safe to analyze and won't perform destructive operations.
Critical security function to prevent DROP, DELETE, UPDATE, and other dangerous operations.
"""

import re
from typing import Any, Dict, Iterable, List, NamedTuple, Optional


# Dangerous SQL keywords that should not be allowed in analyze operations
DANGEROUS_KEYWORDS = {
    # Data modification
    "DELETE",
    "UPDATE",
    "INSERT",
    "REPLACE",
    "MERGE",
    "TRUNCATE",
    # Schema modification
    "DROP",
    "CREATE",
    "ALTER",
    "RENAME",
    # Administrative operations
    "GRANT",
    "REVOKE",
    "SET",
    "RESET",
    "FLUSH",
    "PURGE",
    "OPTIMIZE",
    # Transaction control (could interfere with analysis)
    "COMMIT",
    "ROLLBACK",
    "START TRANSACTION",
    "BEGIN",
    # Stored procedures/functions
    "CALL",
    "EXECUTE",
    "EXEC",
    # System operations
    "SHUTDOWN",
    "RESTART",
    "KILL",
    "LOAD DATA",
    "IMPORT",
    # User management
    "CREATE USER",
    "DROP USER",
    "ALTER USER",
    # Database management
    "USE",
    "CONNECT",
}

# Functions that reach outside the queried tables -- server filesystem, outbound
# connections, or a parked session. They sit inside an ordinary SELECT, so
# neither the keyword scan nor the leading-keyword check ever sees them, and
# they return their result before any rollback, so the transaction the caller
# happens to be in does not undo them.
SIDE_EFFECT_FUNCTIONS = {
    "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_STAT_FILE",
    "LO_IMPORT", "LO_EXPORT", "DBLINK", "DBLINK_EXEC", "QUERY_TO_XML",
    "PG_SLEEP", "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND",
    "LOAD_FILE", "SLEEP", "BENCHMARK", "SYS_EXEC", "SYS_EVAL",
    "NEXTVAL", "SETVAL", "PG_NOTIFY", "PG_ADVISORY_LOCK", "PG_ADVISORY_XACT_LOCK",
    "GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "SOURCE_POS_WAIT", "MASTER_POS_WAIT",
    "WAIT_FOR_EXECUTED_GTID_SET",
    # Session and server state that a read-only transaction still lets change.
    "PG_SLEEP_FOR", "PG_SLEEP_UNTIL", "PG_RELOAD_CONF", "PG_ROTATE_LOGFILE",
    "PG_ADVISORY_LOCK_SHARED", "PG_ADVISORY_XACT_LOCK_SHARED", "PG_TRY_ADVISORY_LOCK",
    "PG_TRY_ADVISORY_LOCK_SHARED", "PG_TRY_ADVISORY_XACT_LOCK",
    "PG_TRY_ADVISORY_XACT_LOCK_SHARED", "PG_ADVISORY_UNLOCK", "PG_ADVISORY_UNLOCK_SHARED",
    "PG_ADVISORY_UNLOCK_ALL", "PG_STAT_RESET", "PG_STAT_RESET_SHARED",
    "PG_STAT_RESET_SINGLE_TABLE_COUNTERS", "PG_STAT_STATEMENTS_RESET",
    "PG_LOGICAL_EMIT_MESSAGE", "PG_LOGICAL_SLOT_GET_CHANGES",
    "PG_LOGICAL_SLOT_GET_BINARY_CHANGES", "PG_REPLICATION_SLOT_ADVANCE",
    "PG_CREATE_LOGICAL_REPLICATION_SLOT", "PG_CREATE_PHYSICAL_REPLICATION_SLOT",
    "PG_DROP_REPLICATION_SLOT", "PG_SWITCH_WAL", "PG_CREATE_RESTORE_POINT", "PG_PROMOTE",
    "PG_FILE_WRITE", "PG_FILE_RENAME", "PG_FILE_UNLINK",
    # Large objects, outbound connections, and functions that run a query passed as text.
    "LO_UNLINK", "LO_CREATE", "LO_CREAT", "LO_FROM_BYTEA", "LO_PUT", "LOWRITE", "LO_TRUNCATE",
    "LO_TRUNCATE64", "DBLINK_CONNECT", "DBLINK_CONNECT_U", "DBLINK_SEND_QUERY", "DBLINK_OPEN",
    "QUERY_TO_XMLSCHEMA", "QUERY_TO_XML_AND_XMLSCHEMA", "TS_STAT",
    "WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS",
}

# Change the session's settings from inside a SELECT, read-only mode included: a session
# that runs statements repeatedly under a session-level read-only setting must refuse them.
# MySQL has no such function; its SET_VAR hint lasts one statement.
SESSION_SETTING_FUNCTIONS = {"SET_CONFIG"}

# Generated SQL (Ask, agents) has no reason to change its session's settings. Captured
# traffic does (driver setup), and analyzing it only changes the analysis's own session.
GENERATED_SQL_DENIED_FUNCTIONS = SIDE_EFFECT_FUNCTIONS | SESSION_SETTING_FUNCTIONS

# Allowed keywords for read-only operations
ALLOWED_KEYWORDS = {
    "SELECT",
    "WITH",
    "SHOW",
    "DESCRIBE",
    "DESC",
    "EXPLAIN",
    "ANALYZE",
    "CHECK",
    "CHECKSUM",
}


class CodeView(NamedTuple):
    """One reading of a statement's text, offsets preserved.

    ``keywords`` blanks literals, quoted names and comments, so what remains is what the
    server executes. ``names`` keeps the text of quoted names, so a function called by its
    quoted name still reads as that function. ``escaped_name`` reports a PostgreSQL
    ``U&"..."`` name written with escapes, whose real spelling neither view shows.
    """

    keywords: str
    names: str
    escaped_name: bool


_DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_\x80-\U0010ffff][A-Za-z0-9_\x80-\U0010ffff]*)?\$")
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def _dialect_family(dialect: str) -> str:
    name = str(dialect or "").casefold()
    if "mysql" in name or "maria" in name:
        return "mysql"
    if "postgres" in name or name == "pg":
        return "postgres"
    return ""


def code_views(sql: str, dialect: str = "") -> List[CodeView]:
    """The statement as the server may read it, one view per quoting rule it may apply.

    Whether a backslash escapes a quote depends on server settings
    (``standard_conforming_strings``, ``NO_BACKSLASH_ESCAPES``), so a check must hold
    under each reading. A reading that leaves a quote or comment open is omitted: under
    it the server rejects the text without running any of it. An unknown dialect gets
    both engines' readings, and its PostgreSQL readings take backticks as quoted names:
    PostgreSQL reads a backtick as an operator, which no database defines unless someone
    with CREATE rights has run CREATE OPERATOR with one, so the statement holding the
    first backtick fails before it runs, along with every statement after it, while
    everything before it stays visible here. On a database that does define such an
    operator, text between backticks can hide a statement from these readings; callers
    pass the engine whenever they know it so this reading is only used when they don't.
    """
    family = _dialect_family(dialect)
    engines = ["postgres", "mysql"] if not family else [family]
    views = []
    for engine in engines:
        for backslash in (False, True):
            view = _lex(sql, engine == "mysql", backslash, backtick_names=not family)
            if view is not None and view not in views:
                views.append(view)
    return views


def statement_count(view: CodeView) -> int:
    """How many statements a view holds; empty ones between semicolons do not count."""
    return sum(1 for part in view.keywords.split(";") if part.strip())


def _continues_name(sql: str, i: int) -> bool:
    return i > 0 and (sql[i - 1].isalnum() or sql[i - 1] in "_$")


def _quoted_end(sql: str, start: int, backslash: bool) -> int:
    """Index just past the quoted span opening at ``start``, or -1 when it never closes."""
    quote, i, n = sql[start], start + 1, len(sql)
    while i < n:
        if backslash and sql[i] == "\\":
            i += 2
        elif sql[i] == quote:
            if i + 1 < n and sql[i + 1] == quote:
                i += 2
            else:
                return i + 1
        else:
            i += 1
    return -1


def _block_comment_end(sql: str, start: int, nested: bool) -> int:
    depth, i, n = 1, start + 2, len(sql)
    while i < n:
        if nested and sql.startswith("/*", i):
            depth, i = depth + 1, i + 2
        elif sql.startswith("*/", i):
            depth, i = depth - 1, i + 2
            if not depth:
                return i
        else:
            i += 1
    return -1


def _lex(sql: str, mysql: bool, backslash: bool,
         backtick_names: bool = False) -> Optional[CodeView]:
    """Blank every literal, quoted name and comment in ``sql`` under one quoting rule.

    PostgreSQL: backslash escapes apply to ``E''`` strings, and to every string when
    ``backslash`` (``standard_conforming_strings = off``); dollar quotes; nested block
    comments. MySQL: backslash escapes apply to quoted strings unless ``backslash`` is
    off (``NO_BACKSLASH_ESCAPES``); ``#`` and ``-- `` comments; ``/*! */`` bodies run as
    SQL. ``backtick_names`` reads backticks as quoted names under either engine.
    """
    keywords, names = list(sql), list(sql)
    escaped_name = False
    executable = False
    i, n = 0, len(sql)

    def blank(start: int, end: int, keep_name: bool = False) -> None:
        keywords[start:end] = " " * (end - start)
        if not keep_name:
            names[start:end] = " " * (end - start)

    while i < n:
        ch = sql[i]
        if mysql and (sql.startswith("/*!", i) or sql.startswith("/*M!", i)):
            end = sql.index("!", i) + 1
            while end < n and sql[end].isdigit():
                end += 1
            blank(i, end)
            i, executable = end, True
            continue
        if executable and sql.startswith("*/", i):
            blank(i, i + 2)
            i, executable = i + 2, False
            continue
        if ch in "'\"`" and (ch != "`" or mysql or backtick_names):
            if mysql:
                escapes = backslash and ch != "`"
            else:
                e_string = (ch == "'" and i > 0 and sql[i - 1] in "eE"
                            and not _continues_name(sql, i - 1))
                escapes = ch == "'" and (backslash or e_string)
            end = _quoted_end(sql, i, escapes)
            if end < 0:
                return None
            if ch == "'" or (mysql and ch == '"'):
                blank(i, end)
            else:
                body = sql[i + 1:end - 1]
                unicode_name = not mysql and i >= 2 and sql[i - 2:i] in ("U&", "u&")
                if unicode_name and ("\\" in body or re.match(r"\s*UESCAPE\b", sql[end:], re.I)):
                    escaped_name = True
                blank(i, end, keep_name=bool(_PLAIN_NAME.fullmatch(body)))
                names[i] = names[end - 1] = " "
            i = end
            continue
        if not mysql and ch == "$" and not _continues_name(sql, i):
            dollar = _DOLLAR_QUOTE.match(sql, i)
            if dollar:
                close = sql.find(dollar.group(), dollar.end())
                if close < 0:
                    return None
                end = close + len(dollar.group())
                blank(i, end)
                i = end
                continue
        line_comment = (mysql and ch == "#") or (
            sql.startswith("--", i)
            and (not mysql or i + 2 >= n or sql[i + 2] <= " " or sql[i + 2].isspace()))
        if line_comment:
            end = sql.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
            continue
        if sql.startswith("/*", i):
            end = _block_comment_end(sql, i, nested=not mysql)
            if end < 0:
                return None
            blank(i, end)
            i = end
            continue
        i += 1
    if executable:
        return None
    return CodeView("".join(keywords), "".join(names), escaped_name)


def side_effect_calls(
    sql: str, dialect: str = "", functions: Iterable[str] = SIDE_EFFECT_FUNCTIONS
) -> List[str]:
    """``functions`` that ``sql`` calls under any reading of its quoting, by plain, quoted
    or schema-qualified name."""
    found: List[str] = []
    for view in code_views(sql, dialect):
        names = view.names.upper()
        found.extend(func for func in sorted(functions)
                     if func not in found and re.search(r"\b" + func + r"\s*\(", names))
    return found


def is_plain_explainable_write(sql: str, dialect: str = "") -> bool:
    """Allow a single DML statement to be planned without executing it."""
    views = code_views(sql, dialect)
    if not views or any(statement_count(view) != 1 for view in views):
        return False
    try:
        from sqlglot import exp, parse

        statements = [statement for statement in parse(sql) if statement is not None]
        return len(statements) == 1 and isinstance(statements[0],
                                                     (exp.Insert, exp.Update, exp.Delete))
    except Exception:
        return False


def validate_query_safety(sql: str, dialect: str = "", **kwargs) -> Dict[str, Any]:
    """
    Validate that a SQL query is safe for analysis operations.

    This function ensures queries are read-only and won't perform any
    destructive or administrative operations.

    Args:
        sql: The SQL query to validate
        dialect: Target engine; an unknown one is checked under every engine's quoting rules
        **kwargs: Additional workflow parameters (ignored)

    Returns:
        Dict containing:
        - safe: boolean indicating if query is safe
        - normalized_sql: cleaned query text
        - issues: list of safety issues found
        - error: error message if validation failed
    """
    try:
        normalized = _normalize_query_for_safety(sql)

        if not normalized.strip():
            return {
                "safe": False,
                "normalized_sql": normalized,
                "issues": ["Empty query"],
                "error": "Query is empty or contains only whitespace",
            }

        views = code_views(sql, dialect)
        if not views:
            return {
                "safe": False,
                "normalized_sql": normalized,
                "issues": ["Query has an unterminated string, quoted name or comment"],
                "keywords_found": [],
                "dangerous_keywords": [],
                "unterminated": True,
            }

        issues: List[str] = []
        dangerous_found: List[str] = []
        keywords: List[str] = []
        for view in views:
            view_keywords = _extract_sql_keywords(view.keywords)
            keywords = keywords or view_keywords
            view_issues = []
            for keyword in view_keywords:
                if keyword in DANGEROUS_KEYWORDS:
                    if keyword not in dangerous_found:
                        dangerous_found.append(keyword)
                    view_issues.append(f"Dangerous keyword found: {keyword}")

            first_keyword = view_keywords[0] if view_keywords else ""
            if first_keyword not in ALLOWED_KEYWORDS:
                # Often a typo rather than an attack, so name what was expected
                # instead of only reporting that the query was rejected.
                allowed = ", ".join(sorted(ALLOWED_KEYWORDS))
                view_issues.append(
                    f"query must begin with one of {allowed} (found '{first_keyword}')"
                )

            view_issues.extend(_validate_query_patterns(view.keywords, view.names))
            if view.escaped_name:
                view_issues.append("Unicode-escaped quoted names are not allowed")
            issues.extend(issue for issue in view_issues if issue not in issues)

        return {
            "safe": len(issues) == 0,
            "normalized_sql": normalized,
            "issues": issues,
            "keywords_found": keywords[:10],
            "dangerous_keywords": dangerous_found,
        }
    except Exception as exc:
        return {
            "safe": False,
            "normalized_sql": sql,
            "issues": [f"Validation error: {exc}"],
            "error": f"Failed to validate query safety: {exc}",
        }


def _normalize_query_for_safety(sql: str) -> str:
    """Normalize query for safety analysis."""
    sql = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"\s+", " ", sql)
    return sql.strip()


# Row-locking clauses read rows and take locks; they modify nothing, but they
# contain UPDATE and SHARE, so they have to come out before keyword scanning.
_ROW_LOCK_CLAUSE = re.compile(
    r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b|\bFOR\s+(?:KEY\s+)?SHARE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b",
    re.IGNORECASE,
)

def _extract_sql_keywords(sql: str) -> List[str]:
    """Extract SQL keywords from a query whose literals and quoted names are blanked."""
    sql = _ROW_LOCK_CLAUSE.sub(" ", sql)
    keyword_pattern = r"\b([A-Za-z_][A-Za-z0-9_]*)\b"
    matches = re.findall(keyword_pattern, sql)

    keywords = []
    for match in matches:
        upper_match = match.upper()
        if (
            upper_match in DANGEROUS_KEYWORDS
            or upper_match in ALLOWED_KEYWORDS
            or len(upper_match) <= 20
        ):
            keywords.append(upper_match)

    return keywords


def _validate_query_patterns(sql: str, names: Optional[str] = None) -> List[str]:
    """Validate a code view (see ``CodeView``) using pattern-based rules.

    Every pattern describes an executable construct, so it runs on text whose literals
    are blanked: a semicolon in a string value is not a second statement.
    """
    issues = []
    sql_upper = sql.upper()
    names_upper = sql_upper if names is None else names.upper()

    for func in sorted(SIDE_EFFECT_FUNCTIONS):
        if re.search(r"\b" + func + r"\s*\(", names_upper):
            issues.append(f"{func}() reaches outside the queried tables")

    dangerous_patterns = [
        (r"\bINTO\b", "SELECT INTO may write or assign values", sql_upper),
        (r"\bINTO\s+OUTFILE\b", "INTO OUTFILE operations not allowed", sql_upper),
        (r"\bLOAD_FILE\s*\(", "LOAD_FILE function not allowed", names_upper),
        (r"\bSYSTEM\s*\(", "SYSTEM function calls not allowed", names_upper),
        (r"\b@@\w+", "System variables access may not be safe", sql_upper),
        (r"\bSLEEP\s*\(", "SLEEP function not allowed for analysis", names_upper),
        (r";[\s;]*[^\s;]", "Multiple statements not allowed", sql_upper),
    ]

    for pattern, message, text in dangerous_patterns:
        if re.search(pattern, text):
            issues.append(message)

    if len(sql) > 50000:
        issues.append("Query exceeds maximum length for safety analysis")

    paren_depth = 0
    max_depth = 0
    for char in sql:
        if char == "(":
            paren_depth += 1
            max_depth = max(max_depth, paren_depth)
        elif char == ")":
            paren_depth -= 1

    if max_depth > 50:
        issues.append("Query has excessive nesting depth")

    return issues
