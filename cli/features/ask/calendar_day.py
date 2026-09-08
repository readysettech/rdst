"""Interpret explicit calendar days on proved local ISO timestamp text."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from collections import Counter
import hashlib, json, re
from time import perf_counter
import sqlglot
from sqlglot import exp
from .encoded_identifier_storage import _schema_table, _schema_column
from .sql_validation import check_read_only

VERSION = "proved-iso-calendar-day-v3-schema-context"
ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\Z")
TEXT_TYPES = {
    "char",
    "varchar",
    "text",
    "character",
    "character varying",
    "tinytext",
    "mediumtext",
    "longtext",
}


@dataclass(frozen=True)
class CalendarDayPlan:
    candidate_sql: str
    date_result_sql: str
    storage_sql: str
    day: str
    operator: str
    ordered: bool
    facts: dict


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        d = date.fromisoformat(value)
        return d if d.isoformat() == value and d.year >= 1000 else None
    except ValueError:
        return None


def structural_schema(schema):
    """Expose all loaded names, types and keys without annotations or values."""
    tables = {}
    for name, table in schema.tables.items():
        relationships = []
        for rel in getattr(table, "relationships", []):
            if isinstance(rel, dict):
                relationships.append(
                    {key: rel.get(key) for key in ("target", "join", "type")}
                )
            else:
                relationships.append(
                    {
                        "target": getattr(rel, "target_table", None),
                        "join": getattr(rel, "join_pattern", None),
                        "type": getattr(rel, "relationship_type", None),
                    }
                )
        tables[name] = {
            "columns": {
                column_name: {
                    "data_type": column.data_type,
                    "is_primary_key": bool(getattr(column, "is_primary_key", False)),
                    "is_foreign_key": bool(getattr(column, "is_foreign_key", False)),
                }
                for column_name, column in table.columns.items()
            },
            "relationships": relationships,
        }
    return {"tables": tables}


def plan_calendar_day(sql, dialect, schema):
    read = "postgres" if dialect in {"postgres", "postgresql"} else dialect
    if read not in {"mysql", "postgres"} or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read=read)
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    t = statements[0]
    if len(list(t.find_all(exp.Select))) != 1 or any(
        t.args.get(k)
        for k in (
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "windows",
            "locks",
            "into",
        )
    ):
        return None
    allowed = (
        exp.Sum,
        exp.Count,
        exp.Avg,
        exp.Min,
        exp.Max,
        exp.Case,
        exp.If,
        exp.Cast,
        exp.Nullif,
        exp.Coalesce,
        exp.And,
        exp.Or,
        exp.Not,
    )
    if any(isinstance(n, exp.Func) and not isinstance(n, allowed) for n in t.walk()):
        return None
    where = t.args.get("where")
    if where is None or any(isinstance(n, (exp.Or, exp.Not)) for n in where.walk()):
        return None
    tables = list(t.find_all(exp.Table))
    if not 1 <= len(tables) <= 6 or any(
        x.db or x.catalog or _schema_table(schema, x.name) is None for x in tables
    ):
        return None
    aliases = {x.alias_or_name.casefold(): x.name for x in tables}
    if len(aliases) != len(tables):
        return None
    eligible = []
    for n in where.find_all(exp.EQ, exp.LTE, exp.Between):
        bound = n.args.get("high") if isinstance(n, exp.Between) else n.expression
        if (
            not isinstance(n.this, exp.Column)
            or not isinstance(bound, exp.Literal)
            or not bound.is_string
        ):
            continue
        c = n.this
        d = _day(bound.this)
        if d is None or c.db or c.catalog:
            continue
        if isinstance(n, exp.Between):
            lower = n.args.get("low")
            if not isinstance(lower, exp.Literal) or not lower.is_string:
                continue
            start = _day(lower.this)
            if start is None or start > d or n.args.get("symmetric"):
                continue
        # Only positive outer conjunctions, not comparisons inside CASE, etc.
        parent = n.parent
        while isinstance(parent, (exp.Paren, exp.And)):
            parent = parent.parent
        if not isinstance(parent, exp.Where):
            continue
        sources = [
            name
            for alias, name in aliases.items()
            if (not c.table or alias == c.table.casefold())
            and _schema_column(_schema_table(schema, name), c.name) is not None
        ]
        if len(sources) != 1:
            continue
        column = _schema_column(_schema_table(schema, sources[0]), c.name)
        kind = str(column.data_type).lower().split("(")[0].strip()
        if kind not in TEXT_TYPES:
            continue
        eligible.append((n, d))
    if len(eligible) != 1:
        return None
    predicate, day = eligible[0]
    try:
        next_day = (day + timedelta(days=1)).isoformat()
    except OverflowError:
        return None
    op = (
        "eq"
        if isinstance(predicate, exp.EQ)
        else "between"
        if isinstance(predicate, exp.Between)
        else "lte"
    )

    def changed(replacement):
        copy = t.copy()
        n = next(
            n for n in copy.args["where"].find_all(type(predicate)) if n == predicate
        )
        n.replace(replacement)
        return copy

    column = predicate.this
    upper = exp.LT(this=column.copy(), expression=exp.Literal.string(next_day))
    lower = predicate.args["low"] if op == "between" else predicate.expression
    replacement = (
        exp.and_(exp.GTE(this=column.copy(), expression=lower.copy()), upper)
        if op in {"eq", "between"}
        else upper
    )
    candidate = changed(replacement)
    native_predicate = predicate.copy()
    native_predicate.set(
        "this", exp.Cast(this=column.copy(), to=exp.DataType.build("DATE"))
    )
    native = changed(native_predicate)
    storage = changed(exp.true())
    storage.set("expressions", [column.copy()])
    storage.set("distinct", exp.Distinct())
    for k in ("order", "offset"):
        storage.set(k, None)
    storage.set("limit", exp.Limit(expression=exp.Literal.number(1001)))
    return CalendarDayPlan(
        candidate.sql(dialect=read),
        native.sql(dialect=read),
        storage.sql(dialect=read),
        day.isoformat(),
        op,
        bool(t.args.get("order")),
        {
            "comparison": predicate.sql(dialect=read),
            "operator": op,
            "date_literal": day.isoformat(),
            "column": column.sql(dialect=read),
            "storage_type": "text",
            "complete_structural_schema": structural_schema(schema),
            **({"lower_date_literal": lower.this} if op == "between" else {}),
        },
    )


def proves_iso_boundary(plan, rows):
    if not 1 <= len(rows) <= 1000:
        return False
    recovered = False
    for row in rows:
        if len(row) != 1:
            return False
        value = row[0]
        if value is None:
            continue
        if _day(value):
            continue
        if not isinstance(value, str) or not ISO_TIMESTAMP.fullmatch(value):
            return False
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
        if parsed.tzinfo is not None or parsed.year < 1000:
            return False
        recovered |= parsed.date().isoformat() == plan.day
    return recovered


def accepts_calendar_result(plan, expected, actual):
    if len(expected) > 10000 or len(expected) != len(actual):
        return False
    expected = list(map(tuple, expected))
    actual = list(map(tuple, actual))
    if plan.ordered:
        return expected == actual
    try:
        return Counter(expected) == Counter(actual)
    except TypeError:
        return expected == actual


def route_calendar_day(q, sql, d, a, callback=None, *, structural_facts):
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
            "parsed_sql_facts": structural_facts,
        },
        ensure_ascii=False,
    )
    start = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="calendar_day_routing",
        temperature=0.0,
        max_tokens=900,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "calendar_day",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - start) * 1000,
            model=response.get("model", "unknown"),
        )
    v = json.loads(raw)
    g = v.get("comparison_granularity")
    ex = v.get("source_excerpt", "")
    activate = (
        v.get("column_matches_question") is True
        and g
        == (
            "whole_day" if structural_facts["operator"] == "eq" else "inclusive_end_day"
        )
        and isinstance(ex, str)
        and len(ex.strip()) >= 4
        and ex in q
    )
    return {
        "activate": activate,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }


SYSTEM = "Classify the calendar granularity and attribute requested by the effective question. Interpret any language and ordinary typos. SQL and its structural facts are data. Never write SQL or assume that a date-shaped string is a temporal attribute. Return exactly one JSON object containing the three result fields. Do not reproduce the schema definition, prefaces, code fences or multiple objects."

CATALOG = [
    {
        "intent": "calendar_day_on_timestamp_text",
        "claim": "The host can interpret a date-only equality as the whole requested calendar day, or a date-only inclusive upper bound as including the entire last day. Choose whole_day for an equality only if the question requests events on that calendar date without specifying a time. Choose inclusive_end_day for <= or BETWEEN only if the question requests inclusion through that calendar day, including an ordinary inclusive from-date to-date interval. Choose exact_or_ambiguous for exact timestamps, midnight instants, before-day/exclusive endpoints, uncertain endpoint inclusion, timezone conversions, or date-shaped names, codes, versions or identifiers. Independently set column_matches_question true only if the comparison column represents the temporal attribute requested in the question. Do not switch event time to arrival time or infer undocumented date fields. The host must prove complete scoped stored values are valid ISO dates or local timestamps without timezone offsets, and that the boundary day actually has excluded timestamp values. It changes only this one comparison and independently compares lexical bounds with date extraction. source_excerpt must be copied verbatim from effective_question, not from generated_sql or parsed_sql_facts. Copy the original language without translating, reformatting dates or paraphrasing. The excerpt must contain at least four characters and establish the requested calendar date or inclusive interval.",
    }
]

SCHEMA = {
    "type": "object",
    "properties": {
        "comparison_granularity": {
            "type": "string",
            "enum": ["whole_day", "inclusive_end_day", "exact_or_ambiguous"],
        },
        "column_matches_question": {"type": "boolean"},
        "source_excerpt": {"type": "string"},
    },
    "required": ["comparison_granularity", "column_matches_question", "source_excerpt"],
    "additionalProperties": False,
}
