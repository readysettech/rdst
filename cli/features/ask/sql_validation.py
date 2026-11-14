"""
SQL Validation and Safety for RDST Ask Command

Multi-layer validation to ensure queries are safe for execution:
1. Read-only enforcement (block write operations)
2. LIMIT injection (prevent unbounded result sets)
3. Query timeout preparation
4. Dangerous pattern detection
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import sqlglot
import sqlparse
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema
from sqlparse.sql import Parenthesis, Statement, TokenList
from sqlparse.tokens import DML, Keyword

# Shared with the analyze validator so the two cannot drift apart. A code view blanks
# literals, quoted names and comments as the server reads them: a keyword inside one is
# text, not an operation.
from shared.query_safety import GENERATED_SQL_DENIED_FUNCTIONS, code_views, statement_count


# Dangerous keywords that indicate write operations
WRITE_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "REPLACE",
    "MERGE",
    "GRANT",
    "REVOKE",
    "SET",
    "RESET",
    "COPY",
    "CALL",
    "EXECUTE",
    "DO",
    "VACUUM",
    "LOAD",
    "IMPORT",
    "ATTACH",
}

# Safe read-only keywords
READ_KEYWORDS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"}

# System catalogs a question about the database itself reads (its tables, row counts,
# sizes, indexes). They are absent from the loaded schema, so the server checks their
# columns when the query runs. MySQL's `mysql` schema holds account credentials.
CATALOG_SCHEMAS = {
    "postgresql": frozenset({"information_schema", "pg_catalog"}),
    "mysql": frozenset({"information_schema", "performance_schema", "sys"}),
}
CREDENTIAL_RELATIONS = frozenset({
    "pg_authid", "pg_shadow", "pg_user_mapping", "pg_user_mappings", "pg_subscription",
})


def describe_validation_issues(issues: Any) -> str:
    """One sentence for a validation refusal, built from each issue's message.

    Issues may be strings, dicts, or objects with a ``message``; their repr never reaches
    the user.
    """
    messages: List[str] = []
    for issue in issues or ():
        message = issue.get("message") if isinstance(issue, dict) else getattr(
            issue, "message", issue
        )
        text = message.strip().rstrip(".") if isinstance(message, str) else ""
        if text and text not in messages:
            messages.append(text)
    if not messages:
        return "The generated SQL failed validation."
    return f"The generated SQL failed validation: {'; '.join(messages)}."


def validate_sql_for_ask(
    sql: str,
    max_limit: int = 1000,
    default_limit: int = 100,
    enforce_result_limit: bool = True,
    dialect: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Comprehensive SQL validation for ask command.

    Performs multiple safety checks:
    1. Read-only validation (no writes)
    2. LIMIT injection/enforcement
    3. Dangerous pattern detection
    4. Syntax validation

    Args:
        sql: SQL query to validate
        max_limit: Maximum allowed LIMIT value
        default_limit: Default LIMIT to inject if missing
        enforce_result_limit: Add or reduce LIMIT clauses when true
        dialect: Target database type; selects the string and comment syntax used to find
            the outermost LIMIT
        **kwargs: Additional parameters

    Returns:
        Dict containing:
        - is_valid: bool
        - is_safe: bool
        - validated_sql: Potentially modified SQL with LIMIT injected
        - issues: List of validation issues
        - warnings: List of warnings
        - has_limit: bool
        - limit_value: int or None
    """
    issues = []
    warnings = []

    # Step 1: Basic validation
    if not sql or not sql.strip():
        return {
            "is_valid": False,
            "is_safe": False,
            "validated_sql": sql,
            "issues": ["Empty or whitespace-only query"],
            "warnings": [],
            "has_limit": False,
            "limit_value": None,
        }

    # Step 2: Check for dangerous keywords
    read_only_check = check_read_only(sql, dialect)
    if not read_only_check["is_read_only"]:
        issues.extend(read_only_check["issues"])
        return {
            "is_valid": False,
            "is_safe": False,
            "validated_sql": sql,
            "issues": issues,
            "warnings": warnings,
            "has_limit": False,
            "limit_value": None,
            "dangerous_keywords": read_only_check.get("dangerous_keywords", []),
        }

    # Step 3: Parse SQL and check structure
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            issues.append("Failed to parse SQL statement")
            return {
                "is_valid": False,
                "is_safe": False,
                "validated_sql": sql,
                "issues": issues,
                "warnings": warnings,
                "has_limit": False,
                "limit_value": None,
            }

        # Only the leading statement is inspected below, so anything trailing a
        # semicolon would reach the driver unexamined.
        if any(statement_count(view) > 1 for view in code_views(sql, dialect)):
            issues.append("Only a single statement is allowed")
            return {
                "is_valid": False,
                "is_safe": False,
                "validated_sql": sql,
                "issues": issues,
                "warnings": warnings,
                "has_limit": False,
                "limit_value": None,
            }

        statement = parsed[0]

        # Check if it's a SELECT statement
        if not _is_select_statement(statement):
            issues.append("Only SELECT statements are allowed")
            return {
                "is_valid": False,
                "is_safe": False,
                "validated_sql": sql,
                "issues": issues,
                "warnings": warnings,
                "has_limit": False,
                "limit_value": None,
            }

    except Exception as e:
        issues.append(f"SQL parsing error: {str(e)}")
        return {
            "is_valid": False,
            "is_safe": False,
            "validated_sql": sql,
            "issues": issues,
            "warnings": warnings,
            "has_limit": False,
            "limit_value": None,
        }

    # Step 3b: Constructs that stay inside a SELECT but reach past the queried
    # tables. These block rather than warn -- they return their payload to the
    # caller before any rollback, so an advisory verdict buys nothing.
    forbidden = _check_forbidden_constructs(sql, dialect)
    if forbidden:
        issues.extend(forbidden)
        return {
            "is_valid": False,
            "is_safe": False,
            "validated_sql": sql,
            "issues": issues,
            "warnings": warnings,
            "has_limit": False,
            "limit_value": None,
        }

    # Step 4: Check for existing LIMIT clause
    limit_info = _extract_limit_clause(sql, dialect)
    has_limit = limit_info["has_limit"]
    limit_value = limit_info["limit_value"]

    # Step 5: Inject or validate LIMIT
    validated_sql = sql
    limit_added = False
    limit_reduced = False
    if enforce_result_limit:
        if not has_limit:
            validated_sql = _inject_limit(sql, default_limit)
            warnings.append(f"Added LIMIT {default_limit} to prevent unbounded results")
            has_limit = True
            limit_value = default_limit
            limit_added = True
        elif limit_value and limit_value > max_limit:
            validated_sql = _replace_limit(sql, max_limit, dialect)
            warnings.append(f"Reduced LIMIT from {limit_value} to maximum {max_limit}")
            limit_value = max_limit
            limit_reduced = True

    # Step 6: Additional safety checks
    safety_warnings = _check_dangerous_patterns(validated_sql)
    warnings.extend(safety_warnings)

    return {
        "is_valid": True,
        "is_safe": True,
        "validated_sql": validated_sql,
        "issues": issues,
        "warnings": warnings,
        "has_limit": has_limit,
        "limit_value": limit_value,
        "limit_added": limit_added,
        "limit_reduced": limit_reduced,
    }


def validate_filter_literal_provenance(
    sql: str,
    *,
    question: str,
    schema_formatted: str,
    provided_context: str = "",
    matched_database_values: str = "",
    clarifications: Optional[Dict[str, str]] = None,
    dialect: str = "",
    enum_overlap_advisory: bool = False,
) -> Dict[str, Any]:
    """Report literal provenance without treating enum overlap as a schema error."""
    try:
        tree = sqlglot.parse_one(
            sql, dialect="postgres" if dialect == "postgresql" else dialect or None
        )
    except Exception:
        return {"is_valid": True, "issues": [], "warnings": []}

    column_types, enum_columns = _parse_formatted_schema_metadata(schema_formatted)
    aliases = {
        table.alias_or_name.lower(): table.name.lower()
        for table in tree.find_all(exp.Table)
        if table.name
    }
    physical_tables = set(aliases.values())
    source_text = "\n".join(
        part
        for part in (
            question,
            provided_context,
            matched_database_values,
            "\n".join(str(value) for value in (clarifications or {}).values()),
        )
        if part
    )
    issues = []
    warnings = []
    seen = set()
    for column, literal in _filter_column_literal_pairs(tree):
        value = literal.this
        if not isinstance(value, str) or not value.strip():
            continue
        normalized_value = _normalize_filter_value(value)
        if not normalized_value:
            continue

        table_name = aliases.get(column.table.lower(), column.table.lower())
        if not table_name and len(physical_tables) == 1:
            table_name = next(iter(physical_tables))
        column_name = column.name.lower()
        qualified_column = f"{table_name}.{column_name}" if table_name else column_name
        enum_matches = sorted(
            qualified
            for qualified, payload in enum_columns.items()
            if _normalized_text_contains(payload, normalized_value)
        )
        source_supports_value = _source_supports_filter_value(
            source_text,
            value,
            normalized_value,
        )
        schema_supports_value = bool(enum_matches)

        issue_key = (qualified_column, value)
        if not source_supports_value and not schema_supports_value:
            if issue_key in seen:
                continue
            seen.add(issue_key)
            warnings.append(
                {
                    "column": column.name,
                    "table_alias": column.table or None,
                    "literal": value,
                    "kind": "unsupported_literal",
                    "message": (
                        f"Filter literal {value!r} has no source in the user question, "
                        "caller context, clarification answers, or listed enum values. "
                        "Remove it or replace it only with a supported value."
                    ),
                    "suggestions": [],
                }
            )
            continue

        target_type = column_types.get(qualified_column, "")
        target_is_enum = "enum" in target_type
        enum_matches_elsewhere = [
            match for match in enum_matches if match != qualified_column
        ]
        if (
            source_supports_value
            and enum_matches_elsewhere
            and target_type
            and not target_is_enum
            and (
                enum_overlap_advisory
                or not _explicit_free_text_reference(question, column.name, value)
            )
        ):
            if issue_key in seen:
                continue
            seen.add(issue_key)
            suggestions = enum_matches_elsewhere[:3]
            (warnings if enum_overlap_advisory else issues).append(
                {
                    "column": column.name,
                    "table_alias": column.table or None,
                    "literal": value,
                    "kind": "enum_shadowed_free_text",
                    "message": (
                        f"Filter literal {value!r} is applied to free-text column "
                        f"{qualified_column!r}; related text also occurs in listed enum "
                        "values. This overlap does not prove that another column "
                        "represents the requested concept or justify changing the filter."
                    ),
                    "suggestions": suggestions,
                }
            )

    return {"is_valid": not issues, "issues": issues, "warnings": warnings}


_TEXT_TYPE = re.compile(r"char|text|string")
_NUMBER_TYPE = re.compile(
    r"\b(?:tiny|small|medium|big)?int(?:eger)?\b|decimal|numeric|float|double|real|serial"
)


def validate_join_column_types(
    sql: str, *, schema_formatted: str, dialect: str = ""
) -> Dict[str, Any]:
    """Flag equality conditions between a text column and a numeric column.

    A text code such as 'JFK' never equals an integer id, so such a join silently returns
    no rows (or, after MySQL's implicit cast, the wrong ones).
    """
    read = "postgres" if dialect == "postgresql" else dialect or None
    try:
        tree = sqlglot.parse_one(sql, dialect=read)
    except Exception:
        return {"is_valid": True, "issues": []}

    column_types, _ = _parse_formatted_schema_metadata(schema_formatted)

    def source_table(scope: Any, qualifier: str) -> str:
        """The base table a column's qualifier names, looking outward for a correlated
        reference; empty for a subquery, a CTE or an unknown name."""
        while scope is not None:
            sources = scope.sources
            if qualifier in sources:
                source = sources[qualifier]
                return source.name.lower() if isinstance(source, exp.Table) else ""
            if not qualifier:
                tables = [source for source in sources.values()]
                if len(tables) == 1 and isinstance(tables[0], exp.Table):
                    return tables[0].name.lower()
                return ""
            scope = scope.parent
        return ""

    def kind(scope: Any, column: exp.Column) -> tuple[str, str]:
        table = source_table(scope, column.table)
        qualified = f"{table}.{column.name.lower()}"
        data_type = column_types.get(qualified, "") if table else ""
        if _TEXT_TYPE.search(data_type):
            return qualified, "text"
        if _NUMBER_TYPE.search(data_type):
            return qualified, "numeric"
        return qualified, ""

    try:
        scopes = traverse_scope(tree)
    except Exception:
        return {"is_valid": True, "issues": []}

    issues = []
    for scope in scopes:
        for node in scope.expression.find_all(exp.EQ):
            if node.find_ancestor(exp.Select, exp.Union) is not scope.expression:
                continue
            left, right = node.this, node.expression
            if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                continue
            issues.extend(_text_number_issue(node, scope, kind, read))
    return {"is_valid": not issues, "issues": issues}


def _text_number_issue(node: exp.EQ, scope: Any, kind: Any, read: Any) -> list:
    """The issue for one equality of a text column and a numeric column, if it is one."""
    left, right = node.this, node.expression
    (left_name, left_kind), (right_name, right_kind) = kind(scope, left), kind(scope, right)
    if {left_kind, right_kind} != {"text", "numeric"}:
        return []
    text_name, number_name = (
        (left_name, right_name) if left_kind == "text" else (right_name, left_name)
    )
    return [
        {
            "column": right.name if left_kind == "text" else left.name,
            "table_alias": None,
            "message": (
                f"{node.sql(dialect=read)} compares text column "
                f"{text_name!r} with numeric column {number_name!r}, so their values "
                "cannot match. Join on columns that hold the same values."
            ),
            "suggestions": [],
        }
    ]


def _filter_column_literal_pairs(tree: exp.Expression):
    comparison_types = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Like,
        exp.ILike,
    )
    for node in tree.walk():
        if isinstance(node, comparison_types):
            left = node.this
            right = node.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                if right.is_string:
                    yield left, right
            elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                if left.is_string:
                    yield right, left
        elif isinstance(node, exp.In) and isinstance(node.this, exp.Column):
            for candidate in node.expressions:
                if isinstance(candidate, exp.Literal) and candidate.is_string:
                    yield node.this, candidate
        elif isinstance(node, exp.Between) and isinstance(node.this, exp.Column):
            for key in ("low", "high"):
                candidate = node.args.get(key)
                if isinstance(candidate, exp.Literal) and candidate.is_string:
                    yield node.this, candidate


def _parse_formatted_schema_metadata(
    schema_formatted: str,
) -> tuple[Dict[str, str], Dict[str, str]]:
    column_types = {}
    enum_columns = {}
    current_table = ""
    for raw_line in schema_formatted.splitlines():
        table_match = re.match(r"^Table:\s+`?([^`\s]+)`?", raw_line.strip())
        if table_match:
            current_table = table_match.group(1).lower()
            continue
        if not current_table or not raw_line.startswith("  "):
            continue
        content = raw_line.strip()
        if content.startswith(("Business context:", "Relationships:")):
            continue
        structural = content.split(" --", 1)[0].split(" [", 1)[0]
        if " (" not in structural or not structural.endswith(")"):
            continue
        column_name, data_type = structural.rsplit(" (", 1)
        qualified = f"{current_table}.{column_name.lower()}"
        column_types[qualified] = data_type[:-1].lower()
        enum_match = re.search(r"\[enum:\s*(.*?)\](?:\s|$)", content)
        if enum_match:
            enum_columns[qualified] = _normalize_text(enum_match.group(1))
    return column_types, enum_columns


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalize_filter_value(value: str) -> str:
    return _normalize_text(value.strip("%_^$"))


def _normalized_text_contains(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


_COMPLETE_NUMERIC_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"
)


def _source_supports_filter_value(
    source_text: str,
    raw_value: str,
    normalized_value: str,
) -> bool:
    if _normalized_text_contains(_normalize_text(source_text), normalized_value):
        return True
    literal_date = _canonical_complete_date(raw_value)
    if literal_date is None:
        return False
    return literal_date in _complete_dates_in_text(source_text)


def _canonical_complete_date(value: str) -> str | None:
    match = _COMPLETE_NUMERIC_DATE.fullmatch(value.strip("%_^$ "))
    if match is None:
        return None
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        ).isoformat()
    except ValueError:
        return None


def _complete_dates_in_text(value: str) -> set[str]:
    dates = set()
    for match in _COMPLETE_NUMERIC_DATE.finditer(value):
        try:
            dates.add(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ).isoformat()
            )
        except ValueError:
            continue
    return dates


def _explicit_free_text_reference(
    question: str, column_name: str, literal: str
) -> bool:
    question_normalized = _normalize_text(question)
    literal_normalized = _normalize_filter_value(literal)
    column_normalized = _normalize_text(column_name)
    if not literal_normalized:
        return False
    if re.search(
        rf"\b(?:named|called|titled|known\s+as)\s+"
        rf"{re.escape(literal_normalized)}\b",
        question_normalized,
    ):
        return True
    return bool(
        column_normalized
        and re.search(
            rf"\b{re.escape(column_normalized)}\s+(?:is|equals?|=)\s+"
            rf"{re.escape(literal_normalized)}\b",
            question_normalized,
        )
    )


def check_read_only(sql: str, dialect: str = "") -> Dict[str, Any]:
    """
    Check if SQL is read-only (no write operations).

    Uses multiple approaches:
    1. Keyword pattern matching
    2. SQL parsing to check statement type
    """
    views = code_views(sql, dialect)
    if not views:
        return {
            "is_read_only": False,
            "issues": [
                "Error tokenizing the SQL: a string, quoted name or comment is never closed "
                "(write an apostrophe inside a string as two)"
            ],
            "dangerous_keywords": [],
        }
    dangerous_found = []
    for view in views:
        # MySQL uses REPLACE both for a write statement (``REPLACE INTO ...``) and
        # for a read-only scalar function (``REPLACE(value, from, to)``).  Remove
        # only function-call spellings before the keyword scan; a statement-level
        # REPLACE, including one following a CTE or another statement, remains.
        keyword_scan_sql = re.sub(r"\bREPLACE\s*(?=\()", "", view.keywords.upper())

        # Check for write keywords
        for keyword in WRITE_KEYWORDS:
            # Use word boundaries to avoid false positives such as DESCRIPTION/DESC.
            pattern = r"\b" + keyword + r"\b"
            if keyword not in dangerous_found and re.search(pattern, keyword_scan_sql):
                dangerous_found.append(keyword)

    if dangerous_found:
        return {
            "is_read_only": False,
            "issues": [f"Write operation detected: {', '.join(dangerous_found)}"],
            "dangerous_keywords": dangerous_found,
        }

    # Additional check: ensure it starts with SELECT or WITH
    first_keyword = _get_first_keyword(sql)
    if first_keyword and first_keyword not in READ_KEYWORDS:
        return {
            "is_read_only": False,
            "issues": [f"Query must start with SELECT or WITH, found: {first_keyword}"],
            "dangerous_keywords": [first_keyword],
        }

    return {"is_read_only": True, "issues": [], "dangerous_keywords": []}


def _is_select_statement(statement: Statement) -> bool:
    """Check if parsed statement is a SELECT query."""
    for token in statement.tokens:
        if isinstance(token, Parenthesis):
            nested = sqlparse.parse(token.value[1:-1])
            if nested and _is_select_statement(nested[0]):
                return True
        if token.ttype in DML and token.value.upper() == "SELECT":
            return True
        # Check for WITH clause (CTEs)
        if token.ttype in Keyword and token.value.upper() == "WITH":
            return True
    return False


def _get_first_keyword(sql: str) -> Optional[str]:
    """Extract the first SQL keyword from query."""
    # Remove leading whitespace and comments
    sql = sql.strip()
    if not sql:
        return None

    # Parse and get first keyword token
    try:
        parsed = sqlparse.parse(sql)
        if parsed:
            statement = parsed[0]
            for token in statement.tokens:
                if isinstance(token, Parenthesis):
                    nested_keyword = _get_first_keyword(token.value[1:-1])
                    if nested_keyword:
                        return nested_keyword
                if token.ttype in Keyword or token.ttype in DML:
                    return token.value.upper()
    except Exception:
        pass

    # Fallback: regex
    match = re.match(r"\s*\(*\s*(\w+)", sql, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None


_MYSQL_EXECUTABLE_COMMENT = re.compile(r"(/\*!\d*)(.*?)\*/", re.DOTALL)


@dataclass(frozen=True)
class _RowLimit:
    """The outermost query's LIMIT or FETCH FIRST clause, as offsets into the SQL."""

    fetch: bool
    with_ties: bool
    count: int | None  # None for "FETCH FIRST ROW ONLY", which means one row
    skips: bool  # MySQL's "LIMIT offset, count" or "LIMIT n OFFSET m"
    count_span: tuple[int, int] | None
    start: int
    end: int


def _is_mysql(dialect: str) -> bool:
    return "mysql" in (dialect or "").casefold()


def is_catalog_relation(table: exp.Table, schema_names: set[str], dialect: str) -> bool:
    """True when ``table`` names a readable system catalog rather than a user table."""
    name = table.name.casefold()
    if not name or name in CREDENTIAL_RELATIONS:
        return False
    mysql = _is_mysql(dialect)
    schema = table.db.casefold()
    if schema:
        return schema in CATALOG_SCHEMAS["mysql" if mysql else "postgresql"]
    # PostgreSQL searches pg_catalog before the user's schemas.
    return not mysql and name.startswith("pg_") and name not in schema_names


def _outer_limit(sql: str, dialect: str = "") -> _RowLimit | None:
    """The outermost query's LIMIT or FETCH FIRST clause, or None.

    The tokenizer skips strings, quoted names, comments and dollar-quoted bodies in the
    dialect's own rules; a clause inside parentheses belongs to a subquery.
    """
    text = sql
    if _is_mysql(dialect):
        # MySQL runs the SQL inside /*! ... */ (optionally /*!NNNNN, a minimum version), so
        # its markers become spaces and its body is read as SQL; offsets stay the same.
        text = _MYSQL_EXECUTABLE_COMMENT.sub(
            lambda m: " " * len(m.group(1)) + m.group(2) + "  ", sql
        )
    try:
        tokens = sqlglot.tokenize(text, read="mysql" if _is_mysql(dialect) else "postgres")
    except sqlglot.errors.TokenError:
        return None
    words = [token.text.upper() for token in tokens]
    depth = 0
    for i, token in enumerate(tokens):
        if token.token_type in (sqlglot.TokenType.L_PAREN, sqlglot.TokenType.L_BRACKET):
            depth += 1
        elif token.token_type in (sqlglot.TokenType.R_PAREN, sqlglot.TokenType.R_BRACKET):
            depth = max(0, depth - 1)
        elif depth == 0 and token.token_type == sqlglot.TokenType.LIMIT:
            found = _limit_clause(tokens, words, i)
            if found is not None:
                return found
        elif depth == 0 and token.token_type == sqlglot.TokenType.FETCH:
            found = _fetch_clause(tokens, words, i)
            if found is not None:
                return found
    return None


_MAX_ROW_COUNT = Decimal(2**63 - 1)


def _row_count(text: str) -> int | None:
    """A row count written as a number ("100", "100.0", "1e2"), rounded as PostgreSQL
    rounds it; None when it is not a finite number."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    # A count past any table's size ("1e999999999") reads as the largest; turning it into
    # an exact integer would take time and memory that grow with its exponent.
    if value.copy_abs() >= _MAX_ROW_COUNT:
        return int(_MAX_ROW_COUNT) if value > 0 else -int(_MAX_ROW_COUNT)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _limit_clause(tokens, words, i) -> _RowLimit | None:
    """``LIMIT n``, ``LIMIT n OFFSET m`` or MySQL's ``LIMIT offset, count``."""
    number = sqlglot.TokenType.NUMBER
    if i + 1 >= len(tokens) or tokens[i + 1].token_type != number:
        return None
    count, last, skips = tokens[i + 1], i + 1, False
    if i + 3 < len(tokens) and words[i + 2] == "," and tokens[i + 3].token_type == number:
        count, last, skips = tokens[i + 3], i + 3, True
    elif i + 3 < len(tokens) and words[i + 2] == "OFFSET" and tokens[i + 3].token_type == number:
        last, skips = i + 3, True
    value = _row_count(count.text)
    if value is None:
        return None
    return _RowLimit(
        fetch=False,
        with_ties=False,
        count=value,
        skips=skips,
        count_span=(count.start, count.end + 1),
        start=tokens[i].start,
        end=tokens[last].end + 1,
    )


def _fetch_clause(tokens, words, i) -> _RowLimit | None:
    """``FETCH FIRST|NEXT [n] ROW|ROWS ONLY|WITH TIES``."""
    j = i + 1
    if j >= len(tokens) or words[j] not in ("FIRST", "NEXT"):
        return None
    j += 1
    count = None
    if j < len(tokens) and tokens[j].token_type == sqlglot.TokenType.NUMBER:
        count, j = tokens[j], j + 1
    if j >= len(tokens) or words[j] not in ("ROW", "ROWS"):
        return None
    j += 1
    if j < len(tokens) and words[j] == "ONLY":
        with_ties = False
    elif j + 1 < len(tokens) and words[j] == "WITH" and words[j + 1] == "TIES":
        with_ties, j = True, j + 1
    else:
        return None
    value = _row_count(count.text) if count is not None else None
    if count is not None and value is None:
        return None
    return _RowLimit(
        fetch=True,
        with_ties=with_ties,
        count=value,
        skips=False,
        count_span=(count.start, count.end + 1) if count is not None else None,
        start=tokens[i].start,
        end=tokens[j].end + 1,
    )


def _extract_limit_clause(sql: str, dialect: str = "") -> Dict[str, Any]:
    """Whether the outermost query has a row limit (LIMIT or FETCH FIRST), and its count."""
    limit = _outer_limit(sql, dialect)
    if limit is None:
        return {"has_limit": False, "limit_value": None}
    return {"has_limit": True, "limit_value": 1 if limit.count is None else limit.count}


def uncapped_sql(
    capped_sql: str, original_sql: str, dialect: str = "", fallback: str | None = None
) -> str | None:
    """``capped_sql`` with the row cap validation imposed undone and every other rewrite kept.

    A limit validation appended is dropped, and a limit it lowered gets the count it had in
    ``original_sql`` back. When the cap cannot be found where validation put it, the result
    is ``fallback``, or ``original_sql`` when no fallback is given.
    """
    original = _extract_limit_clause(original_sql, dialect)
    if original["has_limit"]:
        return _replace_limit(capped_sql, original["limit_value"], dialect)
    limit = _outer_limit(capped_sql, dialect)
    if limit is None or limit.with_ties or capped_sql[limit.end :].strip(" ;\n\t"):
        return original_sql if fallback is None else fallback
    return capped_sql[: limit.start].rstrip()


def probe_outer_limit(sql: str, limit: int, dialect: str = "") -> str | None:
    """The SQL with its outermost row limit raised from ``limit`` to ``limit + 1``.

    The extra row tells a result the limit cut from one that fits exactly. None when the
    outermost limit is not ``limit``, or is WITH TIES, whose count does not bound its rows.
    """
    found = _outer_limit(sql, dialect)
    if found is None or found.with_ties or (found.count or 1) != limit:
        return None
    probe = _replace_limit(sql, limit + 1, dialect)
    return probe if probe != sql else None


def has_outer_offset(sql: str, dialect: str = "") -> bool:
    """Whether the outermost query skips rows (OFFSET, or MySQL's "LIMIT offset, count")."""
    limit = _outer_limit(sql, dialect)
    if limit is not None and limit.skips:
        return True
    try:
        tokens = sqlglot.tokenize(sql, read="mysql" if _is_mysql(dialect) else "postgres")
    except sqlglot.errors.TokenError:
        return False
    depth = 0
    for token in tokens:
        if token.token_type in (sqlglot.TokenType.L_PAREN, sqlglot.TokenType.L_BRACKET):
            depth += 1
        elif token.token_type in (sqlglot.TokenType.R_PAREN, sqlglot.TokenType.R_BRACKET):
            depth = max(0, depth - 1)
        elif depth == 0 and token.token_type == sqlglot.TokenType.OFFSET:
            return True
    return False


def _inject_limit(sql: str, limit: int) -> str:
    """Append a LIMIT to a query whose outermost level has none."""
    sql = sql.rstrip()
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return f"{sql} LIMIT {limit}"


def _replace_limit(sql: str, new_limit: int, dialect: str = "") -> str:
    """Lower the outermost query's row limit to ``new_limit``, keeping any offset and the
    FETCH FIRST form (WITH TIES may still return more rows than its count)."""
    limit = _outer_limit(sql, dialect)
    # "FETCH FIRST ROW ONLY" means one row, already under any cap.
    if limit is None or limit.count_span is None:
        return sql
    start, end = limit.count_span
    return sql[:start] + str(new_limit) + sql[end:]


# A SELECT reaching this check is single-statement and read-only, so every
# remaining INTO is a destination: a new table (PostgreSQL SELECT ... INTO, with
# or without TEMP/UNLOGGED/TABLE), a variable, or the OUTFILE/DUMPFILE forms
# reported separately.
_SELECT_INTO = re.compile(r"\bINTO\s+(?!OUTFILE\b|DUMPFILE\b)", re.IGNORECASE)


def _check_forbidden_constructs(sql: str, dialect: str = "") -> List[str]:
    """
    Check for constructs that must block execution rather than warn.

    Returns:
        List of issue messages; empty means nothing forbidden was found
    """
    issues = []
    for view in code_views(sql, dialect):
        sql_upper = view.keywords.upper()
        found = []
        if re.search(r"\bINTO\s+(?:OUTFILE|DUMPFILE)\b", sql_upper):
            found.append("INTO OUTFILE/DUMPFILE writes to the database server filesystem")

        if _SELECT_INTO.search(sql_upper):
            found.append(
                "SELECT ... INTO writes the result set outside the queried tables"
            )

        names_upper = view.names.upper()
        for func in sorted(GENERATED_SQL_DENIED_FUNCTIONS):
            if re.search(r"\b" + func + r"\s*\(", names_upper):
                found.append(
                    f"{func}() reaches outside the queried tables and is not permitted"
                )
        for relation in sorted(CREDENTIAL_RELATIONS):
            if re.search(r"\b" + relation.upper() + r"\b", names_upper):
                found.append(f"{relation} holds credentials and is not permitted")
        if _is_mysql(dialect) and re.search(r"(?<![\w$])MYSQL[`\"]?\s*\.", names_upper):
            found.append("The mysql schema holds account credentials and is not permitted")
        if view.escaped_name:
            found.append("Unicode-escaped quoted names are not permitted")
        issues.extend(issue for issue in found if issue not in issues)

    return issues


def _check_dangerous_patterns(sql: str) -> List[str]:
    """
    Check for patterns worth surfacing to the user without blocking.

    Returns:
        List of warning messages
    """
    warnings = []
    sql_upper = sql.upper()

    # Check for potentially expensive operations
    if "CROSS JOIN" in sql_upper:
        warnings.append("Query contains CROSS JOIN - may produce very large result set")

    # Check for user-defined functions that might have side effects
    if "PROCEDURE" in sql_upper or "FUNCTION" in sql_upper:
        warnings.append(
            "Query calls stored procedures/functions - verify they are read-only"
        )

    return warnings


def estimate_query_complexity(sql: str) -> str:
    """
    Estimate query complexity based on structure.

    Returns:
        'simple', 'moderate', 'complex', or 'very_complex'
    """
    sql_upper = sql.upper()

    # Count indicators of complexity
    join_count = sql_upper.count("JOIN")
    subquery_count = sql_upper.count("SELECT") - 1  # Subtract main SELECT
    aggregate_count = sum(
        sql_upper.count(agg) for agg in ["COUNT", "SUM", "AVG", "MAX", "MIN"]
    )
    has_group_by = "GROUP BY" in sql_upper
    has_having = "HAVING" in sql_upper
    has_window = "OVER(" in sql_upper or "OVER (" in sql_upper

    complexity_score = 0
    if join_count > 0:
        complexity_score += min(join_count * 2, 10)
    if subquery_count > 0:
        complexity_score += min(subquery_count * 3, 15)
    if aggregate_count > 0:
        complexity_score += 2
    if has_group_by:
        complexity_score += 3
    if has_having:
        complexity_score += 2
    if has_window:
        complexity_score += 5

    if complexity_score <= 5:
        return "simple"
    elif complexity_score <= 10:
        return "moderate"
    elif complexity_score <= 20:
        return "complex"
    else:
        return "very_complex"


def extract_table_names(sql: str) -> List[str]:
    """
    Extract table names from SQL query.

    Uses sqlparse to parse the SQL and extract table identifiers.

    Args:
        sql: SQL query string

    Returns:
        List of table names found in the query
    """
    try:
        parsed = sqlparse.parse(sql)
        if not parsed:
            return []

        tables = set()

        def extract_from_token(token):
            """Recursively extract table names from tokens."""
            if isinstance(token, TokenList):
                # Look for FROM or JOIN clauses
                from_seen = False
                for item in token.tokens:
                    # Check if this is a FROM or JOIN keyword
                    if item.ttype is Keyword and item.value.upper() in (
                        "FROM",
                        "JOIN",
                        "INNER",
                        "LEFT",
                        "RIGHT",
                        "OUTER",
                        "CROSS",
                    ):
                        from_seen = True
                        continue

                    # If we just saw FROM/JOIN, next identifier is likely a table
                    if from_seen and item.ttype is None:
                        # Clean up the identifier (remove schema prefix if present)
                        table_name = str(item).strip()
                        # Remove aliases (everything after AS or space)
                        table_name = re.split(
                            r"\s+(?:as\s+)?", table_name, flags=re.IGNORECASE
                        )[0]
                        # Remove schema prefix (schema.table -> table)
                        if "." in table_name:
                            table_name = table_name.split(".")[-1]
                        # Remove quotes
                        table_name = table_name.strip("\"'`")
                        if table_name and table_name.upper() not in (
                            "SELECT",
                            "WHERE",
                            "ORDER",
                            "GROUP",
                            "HAVING",
                            "LIMIT",
                        ):
                            tables.add(table_name)
                        from_seen = False

                    # Recursively process sublists
                    extract_from_token(item)

        for statement in parsed:
            extract_from_token(statement)

        # Fallback: simple regex extraction if sqlparse fails
        if not tables:
            # Look for patterns like "FROM table" or "JOIN table"
            from_pattern = r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
            matches = re.findall(from_pattern, sql, re.IGNORECASE)
            tables.update(matches)

        return sorted(list(tables))

    except Exception:
        # If parsing fails, try simple regex fallback
        from_pattern = r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        matches = re.findall(from_pattern, sql, re.IGNORECASE)
        return sorted(list(set(matches)))


def extract_column_references(sql: str) -> List[Dict[str, Any]]:
    """
    Extract column references from SQL query.

    Returns list of dicts with:
    - column: column name
    - table_alias: table alias or name if specified (e.g., 'c' from 'c.userid')
    - full_ref: the full reference as written (e.g., 'c.userid')

    Args:
        sql: SQL query string

    Returns:
        List of column reference dictionaries
    """
    columns = []

    # Pattern to match table.column or alias.column references
    # Matches: c.userid, posts.id, p.ownerdisplayname, etc.
    qualified_pattern = r"([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)"

    for match in re.finditer(qualified_pattern, sql):
        table_alias = match.group(1).lower()
        column = match.group(2).lower()
        full_ref = match.group(0)

        # Skip common SQL keywords that might match
        if table_alias in (
            "order",
            "group",
            "inner",
            "left",
            "right",
            "outer",
            "cross",
        ):
            continue

        columns.append(
            {"column": column, "table_alias": table_alias, "full_ref": full_ref}
        )

    return columns


def extract_table_aliases(sql: str) -> Dict[str, str]:
    """
    Extract table aliases from SQL query.

    Returns dict mapping alias -> table_name.

    Args:
        sql: SQL query string

    Returns:
        Dict of alias -> table_name
    """
    aliases = {}

    # Pattern for "table_name alias" or "table_name AS alias"
    # Matches: FROM comments c, FROM posts AS p, JOIN users u
    pattern = (
        r"(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:AS\s+)?([a-zA-Z_][a-zA-Z0-9_]*)"
    )

    for match in re.finditer(pattern, sql, re.IGNORECASE):
        table_name = match.group(1).lower()
        alias = match.group(2).lower()

        # Skip if "alias" is actually a keyword
        if alias in (
            "on",
            "where",
            "inner",
            "left",
            "right",
            "outer",
            "cross",
            "join",
            "and",
            "or",
        ):
            continue

        aliases[alias] = table_name

    # Also add tables without aliases (they reference themselves)
    tables = extract_table_names(sql)
    for table in tables:
        table_lower = table.lower()
        if table_lower not in aliases:
            aliases[table_lower] = table_lower

    return aliases


def validate_columns_against_schema(
    sql: str, schema: Dict[str, List[str]]
) -> Dict[str, Any]:
    """
    Validate that all column references in SQL exist in the schema.

    Args:
        sql: SQL query string
        schema: Dict mapping table_name -> list of column_names
            Example: {'posts': ['id', 'title', 'body'], 'comments': ['id', 'text']}

    Returns:
        Dict containing:
        - is_valid: bool - True if all columns exist
        - invalid_columns: List of invalid column references
        - suggestions: Dict mapping invalid column -> suggested alternatives
        - error_message: Human-readable error if invalid
    """
    # Normalize schema to lowercase
    schema_lower = {
        table.lower(): [col.lower() for col in cols] for table, cols in schema.items()
    }

    # Extract column references and aliases
    column_refs = extract_column_references(sql)
    aliases = extract_table_aliases(sql)

    invalid_columns = []
    suggestions = {}

    for ref in column_refs:
        alias = ref["table_alias"]
        column = ref["column"]
        full_ref = ref["full_ref"]

        # Resolve alias to table name
        table_name = aliases.get(alias, alias)

        # Check if table exists
        if table_name not in schema_lower:
            # Table doesn't exist - will be caught by database anyway
            continue

        # Check if column exists in table
        valid_columns = schema_lower[table_name]
        if column not in valid_columns:
            invalid_columns.append(
                {
                    "reference": full_ref,
                    "table": table_name,
                    "column": column,
                    "alias": alias,
                }
            )

            # Find similar column names for suggestions
            similar = _find_similar_columns(column, valid_columns)
            if similar:
                suggestions[full_ref] = similar

    if invalid_columns:
        # Build error message
        errors = []
        for inv in invalid_columns:
            msg = (
                f"Column '{inv['reference']}' does not exist in table '{inv['table']}'"
            )
            if inv["reference"] in suggestions:
                msg += f". Did you mean: {', '.join(suggestions[inv['reference']])}?"
            errors.append(msg)

        return {
            "is_valid": False,
            "invalid_columns": invalid_columns,
            "suggestions": suggestions,
            "error_message": "\n".join(errors),
        }

    return {
        "is_valid": True,
        "invalid_columns": [],
        "suggestions": {},
        "error_message": None,
    }


def validate_resolved_columns_against_schema(
    sql: str,
    schema: Dict[str, List[str]],
    dialect: str,
) -> Dict[str, Any]:
    """Resolve every candidate column against the loaded schema."""
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    mapping = {
        table: {column: "UNKNOWN" for column in columns}
        for table, columns in schema.items()
    }
    try:
        tree = sqlglot.parse_one(sql, read=read_dialect)
        schema_names = {name.casefold() for name in schema}
        if any(
            is_catalog_relation(table, schema_names, dialect)
            for table in tree.find_all(exp.Table)
        ):
            # Catalog columns aren't in the loaded schema; the server resolves them.
            return {"is_valid": True, "error_message": None}
        resolved_schema = MappingSchema(
            mapping,
            dialect=read_dialect,
            normalize=read_dialect != "postgres",
        )
        qualify(
            tree.copy(),
            dialect=read_dialect,
            schema=resolved_schema,
            allow_partial_qualification=False,
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except (
        sqlglot.errors.ParseError,
        sqlglot.errors.TokenError,
        sqlglot.errors.OptimizeError,
        ValueError,
    ) as exc:
        return {
            "is_valid": False,
            "error_message": f"Column resolution failed: {exc}",
        }
    return {"is_valid": True, "error_message": None}


def validate_tables_against_schema(
    sql: str,
    schema: Dict[str, List[str]],
    dialect: str,
) -> Dict[str, Any]:
    """Validate physical table references while excluding scoped CTE aliases."""
    schema_names = {name.casefold(): name for name in schema}
    try:
        tree = sqlglot.parse_one(
            sql,
            read="postgres" if dialect == "postgresql" else dialect,
        )
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
        return {
            "is_valid": False,
            "invalid_tables": [],
            "error_message": f"SQL parser rejected query: {exc}",
        }
    cte_names = {
        cte.alias_or_name.casefold()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }
    invalid_tables = sorted(
        {
            table.name
            for table in tree.find_all(exp.Table)
            # A table function (JSON_TABLE, generate_series) has no table name.
            if table.name
            and table.name.casefold() not in cte_names
            and table.name.casefold() not in schema_names
            and not is_catalog_relation(table, set(schema_names), dialect)
        },
        key=str.casefold,
    )
    if not invalid_tables:
        return {"is_valid": True, "invalid_tables": [], "error_message": None}
    return {
        "is_valid": False,
        "invalid_tables": invalid_tables,
        "error_message": "Tables do not exist in the loaded schema: "
        + ", ".join(invalid_tables),
    }


_UNQUOTED_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def quote_case_sensitive_identifiers(
    sql: str, schema: Dict[str, List[str]], dialect: str
) -> str:
    """Quote unquoted PostgreSQL names that can only mean a mixed-case table or column.

    PostgreSQL folds an unquoted name to lower case, so `FROM Equipment` misses a table
    created as "Equipment". A name is quoted when its lower-case form names nothing in the
    schema and exactly one mixed-case table or column matches it ignoring case. Only the
    names change; the rest of the text is kept as written.
    """
    if dialect != "postgresql" or not schema:
        return sql
    names = set(schema) | {column for columns in schema.values() for column in columns}
    lower = {name for name in names if name == name.lower()}
    candidates: Dict[str, set] = {}
    for name in names - lower:
        if name.lower() not in lower:
            candidates.setdefault(name.casefold(), set()).add(name)
    exact = {key: next(iter(found)) for key, found in candidates.items() if len(found) == 1}
    if not exact:
        return sql
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except sqlglot.errors.TokenError:
        return sql

    replacements = []
    for index, token in enumerate(tokens):
        name = exact.get(token.text.casefold())
        written = sql[token.start : token.end + 1]
        if name is None or written != token.text or not _UNQUOTED_WORD.fullmatch(written):
            continue
        following = tokens[index + 1].token_type if index + 1 < len(tokens) else None
        preceding = tokens[index - 1].token_type if index else None
        # A keyword-typed word is a name only when qualified, as in e.Name.
        if following == sqlglot.TokenType.L_PAREN or (
            token.token_type != sqlglot.TokenType.VAR and preceding != sqlglot.TokenType.DOT
        ):
            continue
        replacements.append((token.start, token.end + 1, f'"{name}"'))
    for start, end, quoted in reversed(replacements):
        sql = sql[:start] + quoted + sql[end:]
    return sql


def cast_rounded_values_to_numeric(sql: str, dialect: str) -> str:
    """Cast the value of each two-argument PostgreSQL ROUND to numeric.

    PostgreSQL defines ROUND with a precision only for numeric, so ROUND(x, 2) fails when x
    is double precision. A value already cast to numeric, or a nested two-argument ROUND,
    is kept as written, as is the rest of the text.
    """
    if dialect != "postgresql":
        return sql
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except sqlglot.errors.TokenError:
        return sql

    insertions = []
    for index, token in enumerate(tokens[:-1]):
        if (
            token.token_type != sqlglot.TokenType.VAR
            or token.text.upper() != "ROUND"
            or tokens[index + 1].token_type != sqlglot.TokenType.L_PAREN
            or (index and tokens[index - 1].token_type == sqlglot.TokenType.DOT)
        ):
            continue
        arguments, depth, start = [], 0, index + 2
        for position in range(index + 1, len(tokens)):
            kind = tokens[position].token_type
            if kind in (sqlglot.TokenType.L_PAREN, sqlglot.TokenType.L_BRACKET):
                depth += 1
            elif kind in (sqlglot.TokenType.R_PAREN, sqlglot.TokenType.R_BRACKET):
                depth -= 1
            if depth == 0 or (depth == 1 and kind == sqlglot.TokenType.COMMA):
                arguments.append((start, position - 1))
                start = position + 1
            if depth == 0:
                break
        if depth != 0 or len(arguments) != 2 or arguments[0][0] > arguments[0][1]:
            continue
        first, last = tokens[arguments[0][0]], tokens[arguments[0][1]]
        if _is_numeric_value(sql[first.start : last.end + 1]):
            continue
        insertions += [(first.start, "("), (last.end + 1, ")::numeric")]
    for position, text in sorted(insertions, reverse=True):
        sql = sql[:position] + text + sql[position:]
    return sql


def _is_numeric_value(text: str) -> bool:
    try:
        value = sqlglot.parse_one(text, read="postgres")
    except sqlglot.errors.SqlglotError:
        return False
    value = value.unnest()
    if isinstance(value, exp.Round):
        return value.args.get("decimals") is not None
    return isinstance(value, (exp.Cast, exp.TryCast)) and value.to.this in (
        exp.DataType.Type.DECIMAL,
        exp.DataType.Type.BIGDECIMAL,
    )


def _find_similar_columns(
    target: str, candidates: List[str], max_suggestions: int = 3
) -> List[str]:
    """
    Find column names similar to target using edit distance.

    Args:
        target: The column name to match
        candidates: List of valid column names
        max_suggestions: Maximum number of suggestions to return

    Returns:
        List of similar column names, sorted by similarity
    """

    def levenshtein_distance(s1: str, s2: str) -> int:
        """Simple Levenshtein distance implementation."""
        if len(s1) < len(s2):
            return levenshtein_distance(s2, s1)

        if len(s2) == 0:
            return len(s1)

        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row

        return prev_row[-1]

    # Calculate distances and filter
    scored = []
    for candidate in candidates:
        distance = levenshtein_distance(target.lower(), candidate.lower())
        # Only suggest if reasonably similar (within 50% of target length)
        max_distance = max(3, len(target) // 2)
        if distance <= max_distance:
            scored.append((candidate, distance))

    # Sort by distance (closest first)
    scored.sort(key=lambda x: x[1])

    return [col for col, _ in scored[:max_suggestions]]
