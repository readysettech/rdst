"""Repair a model-routed full-month query using a proven YYYYMM axis."""

from __future__ import annotations

import calendar
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlglot
from sqlglot import exp

from shared.db_connection import quote_identifier

MONTH_AXIS_STORAGE_VERSION = "model-routed-month-axis-storage-v1"
MAX_PROBES = 4

_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9]+")
_NATIVE_DATE_TYPES = frozenset(
    {
        "date",
        "datetime",
        "datetime2",
        "smalldatetime",
        "timestamp",
        "timestamp with time zone",
        "timestamp without time zone",
        "timestamptz",
    }
)
_TEXT_TYPES = frozenset(
    {
        "char",
        "character",
        "character varying",
        "nchar",
        "nvarchar",
        "text",
        "varchar",
    }
)
_TEMPORAL_COLUMN_TERMS = frozenset(
    {"date", "month", "period", "time", "timestamp", "yearmonth", "ym"}
)
_TEMPORAL_TABLE_CORE_TERMS = frozenset(
    {
        "calendar",
        "date",
        "month",
        "monthly",
        "period",
        "time",
        "year",
        "yearmonth",
        "ym",
    }
)
_TEMPORAL_TABLE_AUXILIARY_TERMS = frozenset(
    {"dim", "dimension", "lookup", "map", "mapping", "table"}
)
_KEY_TERMS = frozenset({"code", "id", "key"})


@dataclass(frozen=True)
class _MonthRange:
    year: int
    month: int
    column: exp.Column
    members: tuple[exp.Expression, ...]
    storage_kind: str


@dataclass(frozen=True)
class _Route:
    source_table: str
    source_alias: str
    source_date_column: str
    alternate_table: str
    alternate_date_column: str
    shared_key: str


def _read_dialect(dialect: str) -> str:
    return "postgres" if dialect.casefold() in {"postgres", "postgresql"} else "mysql"


def _engine(dialect: str) -> str:
    return "postgresql" if dialect.casefold() in {"postgres", "postgresql"} else "mysql"


def _literal_date(value: exp.Expression | None) -> date | None:
    if not isinstance(value, exp.Literal) or not value.is_string:
        return None
    try:
        return date.fromisoformat(str(value.this))
    except ValueError:
        return None


def _flatten_and(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return [*_flatten_and(expression.this), *_flatten_and(expression.expression)]
    return [expression]


def _same_column(left: exp.Expression, right: exp.Expression) -> bool:
    return (
        isinstance(left, exp.Column)
        and isinstance(right, exp.Column)
        and left.name.casefold() == right.name.casefold()
        and left.table.casefold() == right.table.casefold()
    )


def _calendar_month(
    start: date, end: date, *, inclusive: bool
) -> tuple[int, int] | None:
    if start.day != 1:
        return None
    if inclusive:
        if end.year != start.year or end.month != start.month:
            return None
        if end.day != calendar.monthrange(start.year, start.month)[1]:
            return None
    else:
        expected = (
            date(start.year + 1, 1, 1)
            if start.month == 12
            else date(start.year, start.month + 1, 1)
        )
        if end != expected:
            return None
    return start.year, start.month


def _month_range(tree: exp.Select) -> _MonthRange | None:
    where = tree.args.get("where")
    if not isinstance(where, exp.Where):
        return None
    conjuncts = _flatten_and(where.this)
    candidates: list[_MonthRange] = []
    for predicate in conjuncts:
        if not isinstance(predicate, exp.Between) or not isinstance(
            predicate.this, exp.Column
        ):
            continue
        low = _literal_date(predicate.args.get("low"))
        high = _literal_date(predicate.args.get("high"))
        month = _calendar_month(low, high, inclusive=True) if low and high else None
        if month:
            candidates.append(
                _MonthRange(*month, predicate.this, (predicate,), "inclusive-date")
            )

    lower = [
        predicate
        for predicate in conjuncts
        if isinstance(predicate, exp.GTE)
        and isinstance(predicate.this, exp.Column)
        and _literal_date(predicate.expression) is not None
    ]
    upper = [
        predicate
        for predicate in conjuncts
        if isinstance(predicate, exp.LT)
        and isinstance(predicate.this, exp.Column)
        and _literal_date(predicate.expression) is not None
    ]
    for low in lower:
        for high in upper:
            if not _same_column(low.this, high.this):
                continue
            start = _literal_date(low.expression)
            end = _literal_date(high.expression)
            month = (
                _calendar_month(start, end, inclusive=False) if start and end else None
            )
            if month:
                candidates.append(
                    _MonthRange(*month, low.this, (low, high), "half-open-date")
                )
    return candidates[0] if len(candidates) == 1 else None


def month_axis_storage_shape(sql: str, dialect: str) -> tuple[str, ...]:
    """Return SQL syntax facts only; the model supplies the user's intent."""
    if dialect.casefold() not in {"mysql", "postgres", "postgresql"}:
        return ()
    try:
        statements = sqlglot.parse(sql, read=_read_dialect(dialect))
    except (sqlglot.errors.SqlglotError, ValueError):
        return ()
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return ()
    month_range = _month_range(statements[0])
    if month_range is None or not month_range.column.table:
        return ()
    return (
        "one-qualified-date-column-is-filtered-over-one-exact-calendar-month",
        "the-month-is-derived-from-sql-boundaries-not-question-wording",
        "candidate-requires-an-empty-primary-result-and-database-support",
    )


def _identifier_tokens(value: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", separated)
    separated = separated.replace("_", " ").replace("-", " ")
    return tuple(token.casefold() for token in _IDENTIFIER_TOKEN.findall(separated))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _identifier_terms(value: str) -> tuple[str, ...]:
    return tuple(_singular(token) for token in _identifier_tokens(value))


def _axis_signature(value: str) -> str:
    return "".join(_identifier_terms(value))


def _base_type(column: Any) -> str:
    return (
        " ".join(str(getattr(column, "data_type", "")).casefold().split())
        .split("(", 1)[0]
        .strip()
    )


def _schema_table(schema_info: Any, name: str) -> Any | None:
    tables = getattr(schema_info, "tables", None)
    if not isinstance(tables, dict):
        return None
    matches = [
        table
        for key, table in tables.items()
        if name.casefold()
        in {str(key).casefold(), str(getattr(table, "name", "") or key).casefold()}
    ]
    return matches[0] if len(matches) == 1 else None


def _schema_column(table: Any, name: str) -> Any | None:
    columns = getattr(table, "columns", None)
    if not isinstance(columns, dict):
        return None
    matches = [
        column
        for key, column in columns.items()
        if name.casefold()
        in {str(key).casefold(), str(getattr(column, "name", "") or key).casefold()}
    ]
    return matches[0] if len(matches) == 1 else None


def _column_name(key: Any, column: Any) -> str:
    return str(getattr(column, "name", "") or key)


def _table_name(key: Any, table: Any) -> str:
    return str(getattr(table, "name", "") or key)


def _aliases(tree: exp.Select) -> dict[str, exp.Table] | None:
    aliases: dict[str, exp.Table] = {}
    for table in tree.find_all(exp.Table):
        if table.args.get("db") is not None or table.args.get("catalog") is not None:
            return None
        alias = table.alias_or_name.casefold()
        if not alias or alias in aliases:
            return None
        aliases[alias] = table
    return aliases


def _is_descendant(node: exp.Expression, ancestor: exp.Expression) -> bool:
    current: exp.Expression | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = current.parent
    return False


def _source_axis(
    *,
    tree: exp.Select,
    month_range: _MonthRange,
    aliases: dict[str, exp.Table],
    schema_info: Any,
) -> tuple[Any, exp.Table, str] | tuple[None, None, str]:
    source_date = month_range.column
    source_expression = aliases.get(source_date.table.casefold())
    if source_expression is None:
        return None, None, "source-table-unresolved"
    source_table = _schema_table(schema_info, source_expression.name)
    if source_table is None:
        return None, None, "source-table-not-in-schema"
    schema_date = _schema_column(source_table, source_date.name)
    if schema_date is None or _base_type(schema_date) not in _NATIVE_DATE_TYPES:
        return None, None, "source-axis-not-native-date"
    if (
        month_range.storage_kind == "inclusive-date"
        and _base_type(schema_date) != "date"
    ):
        return None, None, "inclusive-range-requires-date-column"
    temporal_axes = [
        column
        for column in source_table.columns.values()
        if _base_type(column) in _NATIVE_DATE_TYPES
    ]
    if len(temporal_axes) != 1:
        return None, None, "ambiguous-source-temporal-axis"
    if _column_name("", temporal_axes[0]).casefold() != source_date.name.casefold():
        return None, None, "filtered-column-not-unique-source-axis"
    for column in tree.find_all(exp.Column):
        if _same_column(column, source_date) and not any(
            _is_descendant(column, member) for member in month_range.members
        ):
            return None, None, "source-axis-used-outside-month-filter"
    return source_table, source_expression, ""


def _shared_keys(source_table: Any, alternate_table: Any) -> tuple[str, ...]:
    matches: list[str] = []
    for source_key, source_column in source_table.columns.items():
        source_name = _column_name(source_key, source_column)
        source_terms = _identifier_terms(source_name)
        if len(source_terms) < 2 or source_terms[-1] not in _KEY_TERMS:
            continue
        for alternate_key, alternate_column in alternate_table.columns.items():
            alternate_name = _column_name(alternate_key, alternate_column)
            if (
                _axis_signature(source_name) == _axis_signature(alternate_name)
                and _base_type(source_column)
                and _base_type(source_column) == _base_type(alternate_column)
            ):
                matches.append(source_name)
    return tuple(sorted(set(matches), key=str.casefold))


def _routes(
    *,
    schema_info: Any,
    used_table_names: set[str],
    source_table: Any,
    source_expression: exp.Table,
    source_alias: str,
    source_date_column: str,
) -> tuple[list[_Route] | None, str | None]:
    tables = getattr(schema_info, "tables", None)
    if not isinstance(tables, dict):
        return None, "schema-unavailable"
    routes: list[_Route] = []
    ambiguous = False
    source_signature = _axis_signature(source_date_column)
    for key, table in tables.items():
        table_name = _table_name(key, table)
        if table_name.casefold() in used_table_names or "." in table_name:
            continue
        shared_keys = _shared_keys(source_table, table)
        if not shared_keys:
            continue
        if len(shared_keys) != 1:
            ambiguous = True
            continue
        temporal_columns = [
            column
            for column_key, column in table.columns.items()
            if _base_type(column) in _TEXT_TYPES
            and set(_identifier_terms(_column_name(column_key, column)))
            & _TEMPORAL_COLUMN_TERMS
        ]
        if len(temporal_columns) != 1:
            ambiguous = ambiguous or len(temporal_columns) > 1
            continue
        alternate_column = _column_name("", temporal_columns[0])
        table_terms = set(_identifier_terms(table_name))
        allowed_terms = _TEMPORAL_TABLE_CORE_TERMS | _TEMPORAL_TABLE_AUXILIARY_TERMS
        if _axis_signature(alternate_column) != source_signature:
            continue
        if (
            not (table_terms & _TEMPORAL_TABLE_CORE_TERMS)
            or not table_terms <= allowed_terms
        ):
            ambiguous = True
            continue
        routes.append(
            _Route(
                source_expression.name,
                source_alias,
                source_date_column,
                table_name,
                alternate_column,
                shared_keys[0],
            )
        )
    if ambiguous:
        return None, "ambiguous-alternate-temporal-axis"
    routes.sort(
        key=lambda route: (
            route.alternate_table.casefold(),
            route.alternate_date_column.casefold(),
        )
    )
    if len(routes) > MAX_PROBES:
        return None, "too-many-alternate-period-routes"
    return routes, None


def _candidate_alias(tree: exp.Select) -> str:
    used = {
        table.alias_or_name.casefold()
        for table in tree.find_all(exp.Table)
        if table.alias_or_name
    }
    for index in range(1, 10):
        alias = f"_rdst_period_{index}"
        if alias.casefold() not in used:
            return alias
    raise ValueError("unable to allocate bounded period alias")


def _combine_and(expressions: list[exp.Expression]) -> exp.Expression:
    combined = expressions[0]
    for expression in expressions[1:]:
        combined = exp.and_(combined, expression)
    return combined


def _rewrite(tree: exp.Select, month_range: _MonthRange, route: _Route) -> exp.Select:
    rewritten = tree.copy()
    copied_range = _month_range(rewritten)
    where = rewritten.args.get("where")
    if copied_range is None or not isinstance(where, exp.Where):
        raise ValueError("month predicate identity became ambiguous")
    target_ids = {id(member) for member in copied_range.members}
    residual = [
        predicate
        for predicate in _flatten_and(where.this)
        if id(predicate) not in target_ids
    ]
    alias = _candidate_alias(rewritten)
    period = f"{month_range.year:04d}{month_range.month:02d}"
    residual.append(
        exp.EQ(
            this=exp.column(route.alternate_date_column, table=alias, quoted=True),
            expression=exp.Literal.string(period),
        )
    )
    rewritten.set("where", exp.Where(this=_combine_and(residual)))
    rewritten.set(
        "joins",
        [
            *(rewritten.args.get("joins") or ()),
            exp.Join(
                this=exp.Table(
                    this=exp.to_identifier(route.alternate_table, quoted=True),
                    alias=exp.TableAlias(this=exp.to_identifier(alias, quoted=True)),
                ),
                kind="INNER",
                on=exp.EQ(
                    this=exp.column(
                        route.shared_key, table=route.source_alias, quoted=True
                    ),
                    expression=exp.column(route.shared_key, table=alias, quoted=True),
                ),
            ),
        ],
    )
    return rewritten


def _valid_yyyymm(column_sql: str, dialect: str) -> str:
    literal = exp.Literal.string("^(19|20)[0-9]{2}(0[1-9]|1[0-2])$").sql(
        dialect=_read_dialect(dialect)
    )
    return (
        f"{column_sql} ~ {literal}"
        if dialect.casefold() in {"postgres", "postgresql"}
        else f"{column_sql} REGEXP {literal}"
    )


def _support_query(
    route: _Route, candidate: exp.Select, month_range: _MonthRange, dialect: str
) -> str:
    engine = _engine(dialect)
    read_dialect = _read_dialect(dialect)
    source_table = quote_identifier(route.source_table, engine)
    source_date = quote_identifier(route.source_date_column, engine)
    alternate_table = quote_identifier(route.alternate_table, engine)
    alternate_date = quote_identifier(route.alternate_date_column, engine)
    shared_key = quote_identifier(route.shared_key, engine)
    period_sql = exp.Literal.string(
        f"{month_range.year:04d}{month_range.month:02d}"
    ).sql(dialect=read_dialect)
    start = date(month_range.year, month_range.month, 1)
    end = (
        date(month_range.year + 1, 1, 1)
        if month_range.month == 12
        else date(month_range.year, month_range.month + 1, 1)
    )
    start_sql = exp.Literal.string(start.isoformat()).sql(dialect=read_dialect)
    end_sql = exp.Literal.string(end.isoformat()).sql(dialect=read_dialect)
    source_range = (
        f"_rdst_source.{source_date} >= {start_sql} "
        f"AND _rdst_source.{source_date} < {end_sql}"
    )
    candidate_date = f"_rdst_period.{alternate_date}"
    valid_period = _valid_yyyymm(candidate_date, dialect)
    source_from = f"{source_table} AS _rdst_source"
    alternate_from = f"{alternate_table} AS _rdst_period"
    overlap = candidate.copy()
    overlap.set("expressions", [exp.Literal.number(1)])
    overlap.set("distinct", None)
    overlap_sql = overlap.sql(dialect=read_dialect)
    return (
        "SELECT "
        f"EXISTS(SELECT 1 FROM {source_from} "
        f"WHERE {source_range} LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {source_from} "
        f"WHERE _rdst_source.{source_date} IS NOT NULL "
        f"AND NOT ({source_range}) LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} = {period_sql} LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} <> {period_sql} "
        f"AND {valid_period} LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} IS NOT NULL "
        f"AND NOT ({valid_period}) LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} = {period_sql} "
        f"GROUP BY _rdst_period.{shared_key} "
        "HAVING COUNT(*) > 1 LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} = {period_sql} "
        f"AND _rdst_period.{shared_key} IS NULL LIMIT 1), "
        f"EXISTS(SELECT 1 FROM {alternate_from} "
        f"WHERE {candidate_date} IS NOT NULL "
        f"GROUP BY _rdst_period.{shared_key}, {candidate_date} "
        "HAVING COUNT(*) > 1 LIMIT 1), "
        f"EXISTS({overlap_sql})"
    )


def _supported_query_shape(tree: exp.Select) -> bool:
    if (
        tree.args.get("group") is not None
        or tree.args.get("having") is not None
        or tree.args.get("qualify") is not None
        or tree.args.get("order") is not None
        or tree.args.get("limit") is not None
        or tree.args.get("offset") is not None
        or tree.args.get("locks")
        or tree.find(exp.AggFunc) is not None
        or tree.find(exp.Star) is not None
        or tree.find(exp.Window) is not None
        or tree.find(exp.Or) is not None
        or len(list(tree.find_all(exp.Select))) != 1
        or any(
            isinstance(node, (exp.Union, exp.Intersect, exp.Except))
            for node in tree.walk()
        )
    ):
        return False
    return all(
        join.args.get("side") is None
        and str(join.args.get("kind") or "").casefold() in {"", "inner"}
        and join.args.get("method") is None
        and join.args.get("on") is not None
        for join in tree.args.get("joins") or ()
    )


def normalize_month_axis_storage_sql(
    *,
    sql: str,
    dialect: str,
    schema_info: Any,
    db_executor: Callable[..., Any] | None,
    target_config: dict[str, Any] | None,
    intent_activated: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Move one full-month predicate to one uniquely proven YYYYMM axis."""
    diagnostics: dict[str, Any] = {
        "version": MONTH_AXIS_STORAGE_VERSION,
        "status": "unchanged",
        "probe_count": 0,
        "max_probe_count": MAX_PROBES,
        "candidate_execution_count": 0,
        "execution_feedback": True,
        "database_value_feedback": True,
        "intent_source": "model-router",
    }
    if not intent_activated:
        diagnostics["reason"] = "intent-not-activated"
        return sql, diagnostics
    if dialect.casefold() not in {"mysql", "postgres", "postgresql"}:
        diagnostics["reason"] = "unsupported-dialect"
        return sql, diagnostics
    if db_executor is None or target_config is None or schema_info is None:
        diagnostics["reason"] = "database-probe-unavailable"
        return sql, diagnostics
    try:
        statements = sqlglot.parse(sql, read=_read_dialect(dialect))
    except sqlglot.errors.SqlglotError as exc:
        diagnostics.update({"status": "parse-error", "error_kind": type(exc).__name__})
        return sql, diagnostics
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        diagnostics["reason"] = "not-one-select"
        return sql, diagnostics
    tree = statements[0]
    if not _supported_query_shape(tree):
        diagnostics["reason"] = "unsupported-result-shape"
        return sql, diagnostics
    month_range = _month_range(tree)
    if month_range is None:
        diagnostics["reason"] = "no-single-full-month-range"
        return sql, diagnostics
    aliases = _aliases(tree)
    if aliases is None:
        diagnostics["reason"] = "qualified-or-ambiguous-source"
        return sql, diagnostics
    source_table, source_expression, reason = _source_axis(
        tree=tree, month_range=month_range, aliases=aliases, schema_info=schema_info
    )
    if source_table is None or source_expression is None:
        diagnostics["reason"] = reason
        return sql, diagnostics
    routes, reason = _routes(
        schema_info=schema_info,
        used_table_names={table.name.casefold() for table in aliases.values()},
        source_table=source_table,
        source_expression=source_expression,
        source_alias=month_range.column.table,
        source_date_column=month_range.column.name,
    )
    if routes is None:
        diagnostics["reason"] = reason
        return sql, diagnostics
    if not routes:
        diagnostics["reason"] = "no-alternate-period-route"
        return sql, diagnostics

    supported: list[tuple[_Route, exp.Select]] = []
    hashes: list[str] = []
    probe_failed = False
    expected_proofs = (False, True, True, True, False, False, False, False, True)
    for route in routes:
        candidate = _rewrite(tree, month_range, route)
        query = _support_query(route, candidate, month_range, dialect)
        hashes.append(hashlib.sha256(query.encode()).hexdigest())
        try:
            result = db_executor(query, target_config)
            rows = result.get("rows") if isinstance(result, dict) else None
            if (
                not result.get("success")
                or not isinstance(rows, list)
                or len(rows) != 1
                or len(rows[0]) != 9
            ):
                probe_failed = True
                continue
            proofs = tuple(bool(int(value)) for value in rows[0])
        except Exception:
            probe_failed = True
            continue
        if proofs == expected_proofs:
            supported.append((route, candidate))
    period = f"{month_range.year:04d}{month_range.month:02d}"
    diagnostics.update(
        {
            "probe_count": len(hashes),
            "probe_sha256": hashes,
            "requested_month_sha256": hashlib.sha256(period.encode()).hexdigest(),
        }
    )
    if probe_failed:
        diagnostics["reason"] = "database-probe-failed"
        return sql, diagnostics
    if len(supported) != 1:
        diagnostics["reason"] = (
            "no-supported-period-route"
            if not supported
            else "ambiguous-supported-period-routes"
        )
        return sql, diagnostics
    route, candidate = supported[0]
    normalized = candidate.sql(dialect=_read_dialect(dialect))
    diagnostics.update(
        {
            "status": "normalized",
            "selection_policy": "model-intent-unique-semantic-yyyy-mm-axis-v1",
            "source_axis": f"{route.source_table}.{route.source_date_column}",
            "alternate_axis": f"{route.alternate_table}.{route.alternate_date_column}",
            "shared_key": route.shared_key,
            "input_sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "output_sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        }
    )
    return normalized, diagnostics


def normalize_context(ctx: Any, db_executor: Callable[..., Any] | None) -> Any:
    selected = set(
        getattr(ctx, "correction_intent_routing", {}).get("selected_intents", [])
    )
    normalized, diagnostics = normalize_month_axis_storage_sql(
        sql=ctx.sql or "",
        dialect=ctx.db_type,
        schema_info=ctx.schema_info,
        db_executor=db_executor,
        target_config=ctx.target_config,
        intent_activated="month_axis_storage" in selected,
    )
    ctx.month_axis_storage = diagnostics
    if normalized != (ctx.sql or ""):
        ctx.sql = normalized
        ctx.generated_sql = normalized
    return ctx
